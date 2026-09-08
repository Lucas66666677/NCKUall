"""
Contract guards for the deployed-revision route and what it is allowed to say.

`/version` exists because nothing else in this service answers "which commit
is running?". The answer had to be recovered behaviourally -- a changed error
message, a new field -- which works once per release and not at all for a
release that changes nothing observable.

Two properties are worth guarding, and neither is obvious from reading the
route:

1. **It publishes hexadecimal or nothing.** The route is unauthenticated, so
   its payload reaches any caller. `RENDER_GIT_COMMIT` is an environment
   variable like any other, and the failure mode for a variable is holding
   the wrong thing -- a database URL, a key, a pasted `.env` line. A check
   that only asked "is it non-empty?" would republish whatever that was. The
   parse is therefore a whitelist, and the checks below try to get real
   secrets through it.

2. **The health payloads did not change.** The reason this is a third route
   rather than a field on `/health` is that `/health` and `/livez` are already
   contracts: `test_health_gate_contract.py` pins the gate to `/livez`'s
   literal payload, and `HA_DR_RUNBOOK.md` reads `/health`'s states. The last
   check here fails if either grows a revision field.

Import-only -- no database, no running container -- so this runs as a
standalone release-readiness step ahead of the integration suite, like
`test_public_api_contract.py` and `test_health_gate_contract.py`.
"""

from __future__ import annotations

from pathlib import Path
import ast

from fastapi.routing import APIRoute
import pytest

from app import main, revision
from app.main import app
from app.revision import (
    REVISION_ENV_VAR,
    commit_sha_or_none,
    deployed_revision,
    log_revision_configuration,
)


VERSION_PATH = "/version"

#: A real 40-character SHA-1, in the shape Render injects.
A_COMMIT_SHA = "c34a9e5b1d4f7a20e8c96b3d5f1a7e2c9b804d61"

DEPLOYMENT_DOC = Path(__file__).resolve().parents[2] / "DEPLOYMENT.md"
OBSERVABILITY_DOC = Path(__file__).resolve().parents[1] / "OBSERVABILITY.md"

#: Things an environment variable holds when someone fills in the wrong box.
#: Every one of them is truthy, so a presence check would publish all of them.
NOT_A_COMMIT_SHA = [
    "postgresql+psycopg://postgres:hunter2@db.example.com:5432/postgres",
    "sk-proj-Ab3dEf9GhJkLmNpQrStUvWxYz0123456789",
    "rediss://default:s3cr3t@redis.example.com:6379",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.9f7Qn0",
    "RENDER_GIT_COMMIT=c34a9e5",
    "refs/heads/main",
    "main",
    "v0.1.0",
    "unknown",
    "latest",
    "$RENDER_GIT_COMMIT",
    "",
    "   ",
]


def _route(path: str) -> APIRoute | None:
    return next(
        (
            route
            for route in app.routes
            if isinstance(route, APIRoute) and route.path == path
        ),
        None,
    )


def _handler_body(path: str) -> str:
    """The source of the handler mounted at `path`, up to the next decorator."""

    source = Path(main.__file__).read_text(encoding="utf-8")
    return source.split(f'@app.get("{path}"', 1)[1].split("@app.")[0]


def _startup_warning_arguments() -> list[str]:
    """Arguments of the one `logger.warning` in `app/revision.py`, as source.

    Parsed rather than pattern-matched: the format string contains parentheses
    of its own, so any reader built on splitting the text stops in the wrong
    place and passes on a call it never actually read.
    """

    tree = ast.parse(Path(revision.__file__).read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "warning"
    ]
    assert len(calls) == 1, (
        f"expected exactly one warning in {Path(revision.__file__).name}, "
        f"found {len(calls)}; each one publishes to the log aggregator"
    )
    return [ast.unparse(argument) for argument in calls[0].args]


def test_the_version_route_is_mounted() -> None:
    """Operators and the launch ledger reach it by path; a rename is a 404."""

    route = _route(VERSION_PATH)
    assert route is not None, f"{VERSION_PATH} is no longer mounted"
    assert "GET" in route.methods


def test_the_version_route_consults_nothing() -> None:
    """It reports the build, so it must answer when the database is down.

    `Depends(...)` in the signature is what would put the database, or an auth
    gate, in front of a route whose entire job is to be reachable.
    """

    route = _route(VERSION_PATH)
    assert route is not None
    injected = [dependency.name for dependency in route.dependant.dependencies]
    assert injected == [], f"{VERSION_PATH} now injects {injected}"


async def test_it_reports_the_commit_the_platform_injected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point, stated once: the real variable, the real payload."""

    monkeypatch.setenv(REVISION_ENV_VAR, A_COMMIT_SHA)
    assert await main.deployed_revision_probe() == {"revision": A_COMMIT_SHA}


async def test_it_reports_null_when_the_platform_injected_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Locally, and on any host that is not Render, the variable is absent.

    `null` rather than a 500 or an invented string: an unknown revision is a
    normal state, and the route still has to answer.
    """

    monkeypatch.delenv(REVISION_ENV_VAR, raising=False)
    assert await main.deployed_revision_probe() == {"revision": None}


async def test_the_payload_carries_the_revision_and_no_other_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A route that answers "what is deployed?" invites more fields.

    Environment, provider names and feature flags are all configuration, and
    this one is unauthenticated. Pinning the key set is what keeps the next
    useful-sounding addition from being published to anyone who asks.
    """

    monkeypatch.setenv(REVISION_ENV_VAR, A_COMMIT_SHA)
    assert set(await main.deployed_revision_probe()) == {"revision"}


@pytest.mark.parametrize("value", NOT_A_COMMIT_SHA)
async def test_a_value_that_is_not_a_commit_sha_is_never_published(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """The leak guard, driven through the route rather than the parser.

    Each of these is truthy, so a length or presence check -- the shape this
    codebase has had to correct elsewhere -- would return every one of them to
    an anonymous caller.
    """

    monkeypatch.setenv(REVISION_ENV_VAR, value)
    assert await main.deployed_revision_probe() == {"revision": None}


def test_the_parser_accepts_a_real_sha() -> None:
    """Guards the guard: prove the rejections above are a filter, not a wall.

    Without this, `commit_sha_or_none` could return `None` for every input and
    satisfy every rejection check in this file.
    """

    assert commit_sha_or_none(A_COMMIT_SHA) == A_COMMIT_SHA


def test_the_parser_normalizes_case_and_surrounding_whitespace() -> None:
    """A pasted value arrives with a newline; some tools print SHAs uppercase.

    Both are the same commit, so both are usable -- but the published form is
    one form, so two probes of one deployment cannot disagree.
    """

    assert commit_sha_or_none(f"  {A_COMMIT_SHA.upper()}\n") == A_COMMIT_SHA


@pytest.mark.parametrize(
    "value",
    [
        A_COMMIT_SHA[:6],
        A_COMMIT_SHA + "0",
        A_COMMIT_SHA[:-1] + "g",
        A_COMMIT_SHA[:20] + " " + A_COMMIT_SHA[21:],
    ],
    ids=["too-short", "too-long", "non-hex-character", "embedded-space"],
)
def test_the_parser_rejects_near_misses(value: str) -> None:
    """Anchored, not searched: a SHA inside a longer string is not a SHA.

    `A_COMMIT_SHA + "0"` is the case that matters -- an unanchored pattern
    would match the leading 40 characters and publish a string that is not
    what the platform set.
    """

    assert commit_sha_or_none(value) is None


def test_an_abbreviated_sha_is_accepted() -> None:
    """Render sends 40 characters; a hand-set value on another host may not.

    Seven is git's own abbreviation floor, and the whitelist property is
    unchanged by length: it is still hexadecimal or nothing.
    """

    assert commit_sha_or_none(A_COMMIT_SHA[:7]) == A_COMMIT_SHA[:7]


def test_the_environment_read_uses_the_variable_render_actually_sets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The constant and the read have to be the same variable.

    Nothing else would notice this being wrong: a route reading an unset
    variable returns `null`, which is also what a correct route returns on
    every host that is not Render.
    """

    assert REVISION_ENV_VAR == "RENDER_GIT_COMMIT"
    monkeypatch.setenv("RENDER_GIT_COMMIT", A_COMMIT_SHA)
    assert deployed_revision() == A_COMMIT_SHA


def test_a_misconfigured_variable_is_reported_at_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`null` on its own sends an operator who did wire it up to the wrong box.

    The route stays silent about the reason on purpose, so startup is the only
    place the distinction can be drawn.
    """

    monkeypatch.setenv(REVISION_ENV_VAR, "refs/heads/main")
    assert log_revision_configuration() is True


def test_startup_actually_calls_the_configuration_check() -> None:
    """Guards the guard: the check above proves the function, not the wiring.

    Deleting the call from the lifespan would leave every other check in this
    file green while the warning it documents never fires again -- and a
    misconfigured variable would go back to being indistinguishable from an
    absent one.
    """

    tree = ast.parse(Path(main.__file__).read_text(encoding="utf-8"))
    lifespan = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "lifespan"
    )
    called = {
        node.func.id
        for node in ast.walk(lifespan)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "log_revision_configuration" in called


@pytest.mark.parametrize("value", [None, "", "   ", A_COMMIT_SHA])
def test_startup_stays_quiet_when_there_is_nothing_to_report(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    """Guards the guard: an unset variable is not a misconfiguration.

    Warning on absence would fire on every local run and every non-Render
    host, and a warning that always fires is one nobody reads.
    """

    if value is None:
        monkeypatch.delenv(REVISION_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(REVISION_ENV_VAR, value)
    assert log_revision_configuration() is False


def test_the_startup_warning_does_not_carry_the_value() -> None:
    """The reason to reject a value is that it might be a secret.

    Logging it would move the leak from the public payload into the log
    aggregator rather than prevent it. Read as a syntax tree rather than by
    string search, so the check is about what is actually passed: the name of
    the variable, and a length. Anything else -- the value, a slice of it, a
    prefix "just for debugging" -- fails here.
    """

    fmt, *arguments = _startup_warning_arguments()
    assert not fmt.startswith("f"), (
        "the warning interpolates its own format string, which is how the "
        "value gets in without appearing as an argument"
    )
    assert "%d" in fmt, "the warning no longer reports a length"
    for argument in arguments:
        assert argument == "REVISION_ENV_VAR" or argument.startswith("len("), (
            f"the startup warning passes {argument!r}; a variable holding the "
            f"wrong thing may be holding a secret, so only its length is "
            f"logged"
        )


def test_no_health_route_publishes_the_revision() -> None:
    """The reason this is a third route: neither payload was allowed to change.

    `/livez` is parsed by both health gates and its payload is a literal
    `test_health_gate_contract.py` pins; `/health`'s keys are what
    `HA_DR_RUNBOOK.md` reads. A revision field added to either would be a
    contract change disguised as an improvement -- so it is stated here as a
    failure rather than left to review.
    """

    for path in ("/livez", "/health"):
        assert "deployed_revision" not in _handler_body(path), (
            f"{path} now reports the revision; that payload is a contract, "
            f"and {VERSION_PATH} exists so it does not have to change"
        )


def test_the_deployment_doc_names_the_variable_the_code_reads() -> None:
    """An operator sets this in a dashboard, not in this repository.

    A variable no document names is one nobody configures, and the route then
    reports `null` forever on a correctly deployed service.
    """

    assert REVISION_ENV_VAR in DEPLOYMENT_DOC.read_text(encoding="utf-8")


def test_the_observability_doc_explains_how_to_read_the_route() -> None:
    """Three answers, each meaning something different; 404 is the subtle one.

    Without the doc, a 404 reads as "broken" rather than "the deployed build
    predates this route", which is the single most useful thing the route can
    tell an operator chasing a deployment that did not land.
    """

    doc = OBSERVABILITY_DOC.read_text(encoding="utf-8")
    assert VERSION_PATH in doc
    assert "404" in doc
