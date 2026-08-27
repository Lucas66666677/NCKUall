"""
Contract guards for the release proof that database migrations are ready.

Nothing in the integration suite runs a migration. `conftest.test_database`
builds every schema it hands out with `Base.metadata.create_all`, so a green
`pytest` says the *models* are coherent and says nothing at all about whether
`alembic upgrade head` still reaches them. A release that reads a green suite
as "migrations ready" is reading a signal that was never measuring migrations.

The proof lives in two places, and neither one is covered by any other test:

* The revision chain itself. `alembic upgrade head` is what CI, and
  `backend/scripts/deploy_staging.py`, both run against production-shaped
  databases -- so a second head makes the deploy ambiguous, and a revision
  stranded off the upgrade path silently never runs.
* The CI step that applies that chain to a real pgvector Postgres. It is
  workflow configuration no other test reads, so deleting or narrowing it
  leaves every job green while the only migration proof in the pipeline is
  gone.

Like `test_health_gate_contract.py` and `test_public_api_contract.py` these
checks are import-only -- no database, no Alembic run -- so they also stand as
a standalone release-readiness step ahead of the integration suite.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit
import re

from alembic.config import Config
from alembic.script import ScriptDirectory


BACKEND_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"
VERSIONS_DIR = BACKEND_ROOT / "alembic" / "versions"
WORKFLOW = BACKEND_ROOT.parent / ".github" / "workflows" / "main.yml"

#: What CI and `deploy_staging.py` both run. Matched loosely, so rewording
#: the invocation still counts as a migration proof.
_UPGRADE_HEAD = re.compile(r"alembic\s+upgrade\s+heads?\b")
_POSTGRES_URL = re.compile(r"postgresql(?:\+\w+)?://\S+")
_TEST_DATABASE_URL = re.compile(r"TEST_DATABASE_URL\s*[:=]\s*(\S+)")


def _script_directory() -> ScriptDirectory:
    """Read the revision chain the way Alembic itself reads it.

    Loading alone rejects duplicate revision ids and cycles; the checks below
    cover the shapes that load cleanly and still break a deploy.
    """

    return ScriptDirectory.from_config(Config(str(ALEMBIC_INI)))


def _indented_block(lines: list[str], header: str) -> list[str]:
    """The lines indented under the first line whose stripped text is `header`."""

    for index, line in enumerate(lines):
        if line.strip() != header:
            continue
        indent = len(line) - len(line.lstrip())
        block: list[str] = []
        for candidate in lines[index + 1 :]:
            if candidate.strip() and len(candidate) - len(candidate.lstrip()) <= indent:
                break
            block.append(candidate)
        return block
    return []


def _job_block(job: str) -> str:
    """The workflow text belonging to one job, so a match elsewhere never counts.

    An empty string means the job could not be read at all -- the callers below
    fail on that rather than treating an unreadable workflow as a correct one.
    """

    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    return "\n".join(_indented_block(_indented_block(lines, "jobs:"), f"{job}:"))


def _database_names(text: str) -> set[str]:
    return {
        name
        for url in _POSTGRES_URL.findall(text)
        if (name := urlsplit(url.rstrip("\"'")).path.lstrip("/"))
    }


def _revision_id(path: Path) -> str:
    """The `revision = "..."` a version file declares, read as text."""

    match = re.search(
        r"^revision(?::\s*str)?\s*=\s*[\"']([^\"']+)[\"']",
        path.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert match is not None, f"{path.name} declares no revision id"
    return match.group(1)


def test_the_revision_chain_has_a_single_head() -> None:
    """Two heads make `upgrade head` ambiguous wherever a release runs it."""

    heads = _script_directory().get_heads()
    assert len(heads) == 1, (
        f"the revision chain has {len(heads)} heads ({', '.join(sorted(heads))}); "
        f"`alembic upgrade head` cannot resolve a branched chain"
    )


def test_every_revision_file_is_on_the_upgrade_path() -> None:
    """A revision file off the upgrade path ships and never runs.

    Walking the heads back to base is the traversal a deploy performs, and the
    version files are read straight off disk -- so this compares Alembic's own
    map against the filesystem rather than against itself. A `down_revision`
    naming a revision that does not exist fails here too: the traversal cannot
    resolve it, and an unresolvable chain is not a proven one.
    """

    script = _script_directory()
    on_path = {
        revision.revision
        for revision in script.iterate_revisions("heads", "base")
    }
    on_disk = {_revision_id(path) for path in VERSIONS_DIR.glob("*.py")}
    assert on_disk, f"no revision files were read from {VERSIONS_DIR}"
    stranded = sorted(on_disk - on_path)
    assert not stranded, (
        f"{stranded} are not on the upgrade path; a revision off the path "
        f"never reaches a production database"
    )


def test_ci_applies_the_chain_to_a_real_postgres() -> None:
    """The only migration proof in the pipeline, and only this test reads it."""

    job = _job_block("backend")
    assert job, f"the backend job could not be read from {WORKFLOW.name}"
    assert _UPGRADE_HEAD.search(job), (
        f"no `alembic upgrade head` runs in the backend job of "
        f"{WORKFLOW.name}; nothing else in CI executes a migration"
    )
    assert "pgvector/pgvector" in job, (
        "the backend job no longer provides a pgvector Postgres; the vector "
        "index revisions cannot be proved against a stock image"
    )


def test_ci_proves_the_upgrade_on_a_database_pytest_does_not_own() -> None:
    """Run it against the pytest database and the proof stops being one.

    `conftest` creates its schemas with `create_all`, so a migration applied to
    the same database is measured against a schema it did not build.
    """

    job = _job_block("backend")
    proof = {
        name
        for line in job.splitlines()
        if _UPGRADE_HEAD.search(line)
        for name in _database_names(line)
    }
    owned_by_pytest = _database_names(" ".join(_TEST_DATABASE_URL.findall(job)))

    assert proof, "the `alembic upgrade head` step names no database to prove"
    assert owned_by_pytest, "the backend job sets no TEST_DATABASE_URL to compare"
    assert proof.isdisjoint(owned_by_pytest), (
        f"the migration proof and pytest share {sorted(proof & owned_by_pytest)}; "
        f"`create_all` in conftest would mask a broken revision"
    )


def test_the_workflow_reader_is_scoped_to_one_job() -> None:
    """Guards the guard: prove the checks above read the backend job, not the file.

    The frontend job runs no Python at all, so a reader that swept the whole
    document would report Alembic here too.
    """

    frontend = _job_block("frontend")
    assert frontend, f"the frontend job could not be read from {WORKFLOW.name}"
    assert not _UPGRADE_HEAD.search(frontend)
