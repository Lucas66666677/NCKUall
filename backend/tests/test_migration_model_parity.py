"""
Contract guard that the upgrade path arrives at the models it is supposed to.

`test_migration_gate_contract.py` proves the revision chain has one head, that
every revision file sits on the upgrade path, and that CI applies that chain to
a real pgvector Postgres. All three measure the chain's *shape* and its wiring.
None of them measures where the chain *ends up*.

That leaves one cheap, silent failure: a table added to `app/models.py` with no
revision to create it.

* The integration suite cannot see it. `conftest.test_database` builds every
  schema it hands out with `Base.metadata.create_all`, so the table is simply
  there for every test that asks for it.
* The CI migration step cannot see it either. `alembic upgrade head` runs
  against an empty database, applies whatever revisions exist, and succeeds --
  it never compares the schema it produced against the models.

Both signals stay green and the table first goes missing on staging or
production, the only databases where the models are not the thing that built
the schema, as `relation "..." does not exist` at request time.

`alembic check` reports this, but only against an already-migrated live
database, and preflight has none. So this reads the upgrade path as source
instead: walk the revisions in upgrade order, apply each table create, drop and
rename to a set, and compare that set with `Base.metadata`. Like the other
contract guards the checks are import-only -- no database, no Alembic run -- so
they run as a release-readiness step ahead of the integration suite.

Scope is deliberately tables and not columns. Column names in these revisions
come from shared helpers (`id_column()`, `timestamp_columns()`) and from loops
over a table-name variable, so no honest static reader can recover them. A
missing table is the failure that takes endpoints down, and it is the one that
reads cleanly off the source.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
import ast
import re

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.models import Base


BACKEND_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"

#: Operations that add, remove or rename a table. Everything else a revision
#: does -- columns, indexes, constraints, enums -- leaves this set alone.
_CREATE = "create_table"
_DROP = "drop_table"
_RENAME = "rename_table"
TABLE_OPERATIONS = frozenset({_CREATE, _DROP, _RENAME})

#: Table DDL written as raw SQL rather than as an `op.*` call. The revisions
#: use `op.execute` for extensions, concurrent indexes, functions and triggers,
#: none of which touch this set -- so a match means the reader is being asked
#: to follow SQL it does not parse, and it says so instead of guessing.
_RAW_TABLE_DDL = re.compile(
    r"\b(?:CREATE|DROP)\s+TABLE\b|\bRENAME\s+TO\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class UpgradePath:
    """The tables an upgrade leaves behind, and what could not be read."""

    tables: frozenset[str]

    #: Table operations the reader found but could not resolve to a name.
    #: Non-empty means `tables` is incomplete, so it is reported before any
    #: comparison is drawn from it -- an under-read chain must never pass as
    #: an agreeing one.
    unreadable: tuple[str, ...]


def _string_literals(node: ast.AST) -> Iterator[str]:
    """Every statically known string in `node`, f-string fragments included."""

    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            yield child.value
        elif isinstance(child, ast.JoinedStr):
            yield from (
                part.value
                for part in child.values
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
            )


def _literal_name(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _table_operations(node: ast.AST) -> Iterator[ast.Call]:
    for child in ast.walk(node):
        if (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr in TABLE_OPERATIONS
        ):
            yield child


def _function(tree: ast.Module, name: str) -> ast.FunctionDef | None:
    return next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == name
        ),
        None,
    )


def read_upgrade_path(sources: Iterable[tuple[str, str]]) -> UpgradePath:
    """Replay the table operations of each revision, in upgrade order.

    `sources` are `(origin, source)` pairs ordered base first, which is the
    order a deploy applies them. Only `upgrade()` is replayed: `downgrade()`
    drops the same tables again and is not on the path a release runs.
    """

    tables: set[str] = set()
    unreadable: list[str] = []

    for origin, source in sources:
        tree = ast.parse(source)
        upgrade = _function(tree, "upgrade")
        if upgrade is None:
            unreadable.append(f"{origin}: no module-level `upgrade()` to read")
            continue

        downgrade = _function(tree, "downgrade")
        replayed = {id(call) for call in _table_operations(upgrade)}
        ignored = (
            {id(call) for call in _table_operations(downgrade)}
            if downgrade is not None
            else set()
        )
        # A table operation reached through a helper is never replayed, so the
        # reader reports it rather than quietly losing the table.
        for call in _table_operations(tree):
            if id(call) not in replayed and id(call) not in ignored:
                unreadable.append(
                    f"{origin}: `op.{call.func.attr}` on line {call.lineno} is "
                    f"outside `upgrade()` and `downgrade()`"
                )

        for call in _table_operations(upgrade):
            operation = call.func.attr
            names = [_literal_name(argument) for argument in call.args[:2]]
            if not names or names[0] is None:
                unreadable.append(
                    f"{origin}: `op.{operation}` on line {call.lineno} names "
                    f"its table with an expression, not a literal"
                )
                continue

            if operation == _CREATE:
                tables.add(names[0])
            elif operation == _DROP:
                tables.discard(names[0])
            elif len(names) < 2 or names[1] is None:
                unreadable.append(
                    f"{origin}: `op.rename_table` on line {call.lineno} names "
                    f"its new table with an expression, not a literal"
                )
            else:
                tables.discard(names[0])
                tables.add(names[1])

        for call in ast.walk(upgrade):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "execute"
                and _RAW_TABLE_DDL.search("\n".join(_string_literals(call)))
            ):
                unreadable.append(
                    f"{origin}: `op.execute` on line {call.lineno} writes table "
                    f"DDL as raw SQL, which this reader does not follow"
                )

    return UpgradePath(tables=frozenset(tables), unreadable=tuple(unreadable))


def _chain_sources() -> list[tuple[str, str]]:
    """Every revision's source, base first, in the order a deploy applies it."""

    script = ScriptDirectory.from_config(Config(str(ALEMBIC_INI)))
    revisions = list(script.iterate_revisions("heads", "base"))[::-1]
    return [
        (Path(revision.path).name, Path(revision.path).read_text(encoding="utf-8"))
        for revision in revisions
    ]


def test_the_reader_can_follow_every_table_operation_on_the_upgrade_path() -> None:
    """Guards the guard: an under-read chain must fail here, not agree below."""

    path = read_upgrade_path(_chain_sources())
    assert not path.unreadable, (
        "the upgrade path contains table operations this reader cannot "
        "resolve, so the comparisons below would be drawn from an incomplete "
        "schema:\n  " + "\n  ".join(path.unreadable)
    )
    assert path.tables, "no revision on the upgrade path creates any table"


def test_the_upgrade_path_creates_every_table_the_models_declare() -> None:
    """A model without a revision is invisible until production asks for it."""

    created = read_upgrade_path(_chain_sources()).tables
    missing = sorted(set(Base.metadata.tables) - created)
    assert not missing, (
        f"{missing} are declared in app/models.py and created by no revision; "
        f"`create_all` hands them to the test suite, so only a migrated "
        f"database would ever report them missing"
    )


def test_the_models_declare_every_table_the_upgrade_path_creates() -> None:
    """And the other direction: a table the models stopped mapping.

    Retiring the model without a revision to drop the table leaves a migrated
    database carrying a table nothing reads, and leaves the next unrelated
    `alembic revision --autogenerate` offering to drop it.
    """

    created = read_upgrade_path(_chain_sources()).tables
    orphaned = sorted(created - set(Base.metadata.tables))
    assert not orphaned, (
        f"the upgrade path creates {orphaned}, which app/models.py no longer "
        f"maps; either drop them in a revision or restore the models"
    )


def test_the_reader_replays_operations_rather_than_collecting_names() -> None:
    """Guards the guard: prove the walk is ordered, not a scan for literals.

    A reader that swept every `op.create_table` in the tree would report a
    dropped table as present, a renamed one under both names, and would count
    the tables `downgrade()` recreates -- agreeing with the models by accident.
    This drives those shapes past it.
    """

    path = read_upgrade_path(
        [
            (
                "0001",
                "def upgrade():\n"
                "    op.create_table('kept')\n"
                "    op.create_table('renamed_away')\n"
                "    op.create_table('dropped_later')\n"
                "def downgrade():\n"
                "    op.drop_table('kept')\n",
            ),
            (
                "0002",
                "def upgrade():\n"
                "    op.rename_table('renamed_away', 'renamed_to')\n"
                "    op.drop_table('dropped_later')\n"
                "def downgrade():\n"
                "    op.create_table('dropped_later')\n",
            ),
        ]
    )

    assert path.unreadable == ()
    assert set(path.tables) == {"kept", "renamed_to"}


def test_the_reader_reports_table_names_it_cannot_resolve() -> None:
    """Guards the guard: an unreadable shape must fail, not read as no table."""

    computed, helper, raw_sql = (
        read_upgrade_path([("rev", source)]).unreadable
        for source in (
            "def upgrade():\n    op.create_table(name_from_a_variable)\n",
            "def _helper():\n    op.create_table('hidden')\n"
            "def upgrade():\n    _helper()\n",
            'def upgrade():\n    op.execute(f"CREATE TABLE {name} (id uuid)")\n',
        )
    )

    assert any("not a literal" in complaint for complaint in computed)
    assert any("outside `upgrade()`" in complaint for complaint in helper)
    assert any("raw SQL" in complaint for complaint in raw_sql)
