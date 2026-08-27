"""
Contract guards for the deployment health gate and the route it probes.

The backend container in `docker-compose.prod.yml` is gated on `/livez`, not
on `/health`. The distinction is the whole point: `/health` pings the writer
and the read replica, so a gate pointed there reports a database outage as a
dead process -- flipping the container unhealthy, and holding back anything
that reads that status, for a dependency the process itself survived.
`/health` is still the readiness signal `backend/HA_DR_RUNBOOK.md` reads for
its `read_only` and `degraded` states.

Nothing else in the suite reads the compose file, so the gate is the piece
most able to drift: renaming the route, or letting it grow a dependency,
leaves the probe on a 404 or back on the database with every other test green.

Like `test_public_api_contract.py` these checks are import-only -- no database,
no running container -- so they also run as a standalone release-readiness step
ahead of the integration suite.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit
import re

from fastapi.routing import APIRoute

from app import main
from app.main import app


#: The gate probes this. It consults nothing, so it reports the process and
#: only the process.
LIVENESS_PATH = "/livez"

#: Readiness. It reaches the database, which is exactly why a gate must not
#: be pointed at it.
READINESS_PATH = "/health"

COMPOSE_FILE = Path(__file__).resolve().parents[2] / "docker-compose.prod.yml"

_URL = re.compile(r"https?://[^\s\"']+")


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


def _gate_probe_paths(service: str = "backend") -> list[str]:
    """URL paths the compose health gate for `service` probes, in file order.

    Read out of the service's own `healthcheck:` block rather than the whole
    file, so a URL elsewhere in the compose document is never mistaken for a
    probe. An empty list means no probe could be read at all -- the callers
    below fail on that rather than treating an unreadable gate as a correct one.
    """

    lines = COMPOSE_FILE.read_text(encoding="utf-8").splitlines()
    healthcheck = _indented_block(_indented_block(lines, f"{service}:"), "healthcheck:")
    return [urlsplit(url).path for url in _URL.findall("\n".join(healthcheck))]


def _route(path: str) -> APIRoute | None:
    return next(
        (
            route
            for route in app.routes
            if isinstance(route, APIRoute) and route.path == path
        ),
        None,
    )


def test_the_liveness_route_is_still_mounted() -> None:
    """The gate probes it by path, so dropping it leaves the probe on a 404."""

    route = _route(LIVENESS_PATH)
    assert route is not None, f"{LIVENESS_PATH} is no longer mounted"
    assert "GET" in route.methods


def test_the_liveness_route_consults_nothing() -> None:
    """A liveness route that injects a dependency is a readiness route.

    `Depends(...)` anywhere in the signature is what would put the database, or
    an auth gate, back in front of the probe.
    """

    route = _route(LIVENESS_PATH)
    assert route is not None
    injected = [dependency.name for dependency in route.dependant.dependencies]
    assert injected == [], f"{LIVENESS_PATH} now injects {injected}"


async def test_the_liveness_route_answers_without_a_database() -> None:
    """Called directly: no client, no lifespan, no connection of any kind."""

    assert await main.liveness_probe() == {"status": "alive"}


def test_the_readiness_route_still_reaches_the_database() -> None:
    """Guards the guard: prove the two routes are actually different.

    If `/health` ever stopped touching the database, the checks above would
    still pass while saying nothing, because there would be no readiness route
    left for a gate to be wrongly pointed at.
    """

    assert _route(READINESS_PATH) is not None
    source = Path(main.__file__).read_text(encoding="utf-8")
    readiness = source.split(f'@app.get("{READINESS_PATH}"', 1)[1]
    assert "check_database_health" in readiness.split("@app.")[0]


def test_the_deployment_health_gate_probes_liveness() -> None:
    """The gate is configuration no other test reads, so it drifts silently."""

    probes = _gate_probe_paths()
    assert probes, (
        f"no probe URL could be read from the backend healthcheck in "
        f"{COMPOSE_FILE.name}; an unreadable gate is not a correct one"
    )
    off_target = sorted({path for path in probes if path != LIVENESS_PATH})
    assert not off_target, (
        f"the backend health gate probes {off_target}; a dependency-sensitive "
        f"route there fails the container on a database outage"
    )


def test_the_probe_reader_finds_nothing_in_a_gate_without_a_url() -> None:
    """Guards the guard: the empty result the check above fails on is reachable.

    Redis is gated on `redis-cli ping`, which names no URL -- so this is also
    proof the reader scopes itself to one service's block rather than sweeping
    the file.
    """

    assert _gate_probe_paths("redis") == []
