"""
Contract guards for `backend/PRODUCTION_MIGRATION_CHECKLIST.md`.

The checklist is the procedure for applying migrations to production, and it is
load-bearing for a reason specific to this repository: the image does not
migrate. `backend/Dockerfile` ends at a bare `gunicorn`, there is no entrypoint
that upgrades and no `render.yaml` here that would, while the backend
auto-deploys from `main`. Merging a revision therefore ships the code that needs
the new tables without creating them, and a human closes that gap by hand.

A checklist that names the wrong revision, the wrong table or a route that has
since been renamed is worse than no checklist: it is followed, it reports
success, and the thing it was meant to verify was never checked. Nothing else
in the suite reads a Markdown file, so the doc can drift from the schema it
describes with every test green.

These checks tie the doc to the code it makes claims about -- the revision
chain, the objects those revisions create, and the routes that read them. They
are import-only: no database, no Alembic run, no container, like
`test_migration_gate_contract.py` and `test_health_gate_contract.py`.

What they deliberately do not check is whether production has actually been
migrated. That is not knowable from this repository, and step 1 of the
checklist is what determines it.
"""

from __future__ import annotations

import re
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.main import app
from app.models import Base


BACKEND_ROOT = Path(__file__).resolve().parents[1]
CHECKLIST = BACKEND_ROOT / "PRODUCTION_MIGRATION_CHECKLIST.md"
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"
VERSIONS_DIR = BACKEND_ROOT / "alembic" / "versions"
DOCKERFILE = BACKEND_ROOT / "Dockerfile"

#: The revisions the checklist walks the operator through. Every claim it makes
#: about tables, indexes and rollback is a claim about these two.
DOCUMENTED_REVISIONS = ("20260826_0014", "20260827_0015")

_REVISION_ID = re.compile(r"\b20\d{6}_\d{4}\b")
_INDEX_NAME = re.compile(r"\bix_[a-z0-9_]+\b")
_API_PATH = re.compile(r"/api/[A-Za-z0-9/_{}-]+")
_CREATE_INDEX = re.compile(r'op\.create_index\(\s*"([^"]+)"')
_TO_REGCLASS = re.compile(r"to_regclass\('public\.([a-z0-9_]+)'\)")


def _checklist() -> str:
    return CHECKLIST.read_text(encoding="utf-8")


def _script_directory() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(str(ALEMBIC_INI)))


def _revision_files() -> list[Path]:
    """The migration files for the revisions the checklist documents."""

    return [
        path
        for path in sorted(VERSIONS_DIR.glob("*.py"))
        if any(revision in path.name for revision in DOCUMENTED_REVISIONS)
    ]


def _mounted_paths() -> set[str]:
    """Every path the application publishes, prefixes resolved.

    Read out of the generated OpenAPI document rather than `app.routes`:
    routers included under a prefix stay nested in `app.routes` on this FastAPI
    version, so walking it directly sees only the two routes mounted on the app
    itself and would report every `/api/...` path as missing.
    """

    return set(app.openapi()["paths"])


def test_the_checklist_exists_and_is_not_a_stub() -> None:
    """Guards the guard: an empty file would satisfy every regex below."""

    assert CHECKLIST.is_file(), f"{CHECKLIST.name} is missing"
    assert len(_checklist()) > 2000


def test_every_revision_the_checklist_names_is_in_the_chain() -> None:
    """A renamed or squashed revision leaves the operator running nothing."""

    known = {revision.revision for revision in _script_directory().walk_revisions()}
    named = set(_REVISION_ID.findall(_checklist()))
    assert named, "the checklist names no revision at all"
    unknown = sorted(named - known)
    assert not unknown, f"the checklist names revisions that do not exist: {unknown}"


def test_the_checklist_documents_both_pending_revisions() -> None:
    """Guards the guard: proves the check above is reading a real list.

    Without this, deleting every revision id from the doc would still pass.
    """

    named = set(_REVISION_ID.findall(_checklist()))
    missing = sorted(set(DOCUMENTED_REVISIONS) - named)
    assert not missing, f"the checklist no longer covers {missing}"


def test_the_head_the_checklist_tells_the_operator_to_expect_is_the_head() -> None:
    """Steps 1, 2 and 4 all gate on this exact string.

    If it is stale, step 2's STOP fires on a correct chain and step 4's success
    condition can never be met -- a checklist that fails closed on a healthy
    database is still a checklist nobody can complete.
    """

    heads = _script_directory().get_heads()
    assert len(heads) == 1, f"the chain has {len(heads)} heads: {heads}"
    assert f"{heads[0]} (head)" in _checklist(), (
        f"the checklist does not tell the operator to expect '{heads[0]} (head)'"
    )


def test_every_index_the_checklist_lists_is_one_the_migrations_create() -> None:
    """Step 5 fails closed on a missing index, so the list has to be the truth.

    A name that no migration creates makes step 5 unpassable; an index the
    migrations create and the doc omits is one nobody checks for.
    """

    created: set[str] = set()
    for path in _revision_files():
        created.update(_CREATE_INDEX.findall(path.read_text(encoding="utf-8")))
    assert created, "no create_index call was found in the documented revisions"

    listed = set(_INDEX_NAME.findall(_checklist()))
    assert listed == created, (
        f"checklist lists {sorted(listed)}; the migrations create {sorted(created)}"
    )


def test_every_table_the_checklist_probes_is_a_table_the_models_declare() -> None:
    """Step 5's `to_regclass` names are pasted into psql verbatim.

    A misspelled or renamed name comes back null, which step 5 reads as "the
    version table and the schema disagree" -- sending the operator to
    investigate a database that was fine.
    """

    probed = set(_TO_REGCLASS.findall(_checklist()))
    assert probed, "step 5 no longer probes for any table"
    unknown = sorted(probed - set(Base.metadata.tables))
    assert not unknown, f"the checklist probes for tables no model declares: {unknown}"


def test_every_api_path_the_checklist_names_is_mounted() -> None:
    """Step 6 curls these. A renamed route turns its STOP into a 404."""

    mounted = _mounted_paths()
    named = set(_API_PATH.findall(_checklist()))
    assert named, "the checklist names no API path at all"
    unmounted = sorted(named - mounted)
    assert not unmounted, f"the checklist curls routes that are not mounted: {unmounted}"


def test_the_checklist_quotes_the_image_command_it_reasons_from() -> None:
    """Its whole premise is that the image does not migrate.

    The doc quotes the Dockerfile's `CMD` verbatim to establish that. Quoting a
    line the image no longer carries is how a premise goes stale unnoticed.
    """

    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    command = 'CMD ["gunicorn", "--config", "gunicorn.conf.py", "app.main:app"]'
    assert command in dockerfile, "the Dockerfile no longer ends at that CMD"
    assert command in _checklist(), "the checklist quotes a CMD the image does not use"


def test_the_image_still_does_not_migrate_on_its_own() -> None:
    """The condition that makes a manual checklist necessary at all.

    If an `alembic upgrade` is ever added to the image or an entrypoint, this
    document describes a procedure that is no longer the one being followed,
    and every step's expected output has to be re-derived. Failing here is the
    signal to rewrite the doc, not to delete this test.
    """

    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert not re.search(r"alembic\s+upgrade", dockerfile), (
        "the image now migrates; PRODUCTION_MIGRATION_CHECKLIST.md assumes it "
        "does not and must be rewritten"
    )
