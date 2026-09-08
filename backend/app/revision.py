"""The commit this process was built from, read from the platform.

The service publishes no build identifier, so "which revision is actually
running?" can today only be answered by finding a behavioural difference
between two releases -- a changed error string, a new response field. That
works once per release, and not at all for a release that changes nothing
observable, which is exactly the case where the question matters most: a merge
CI passed and the platform may or may not have picked up.

Render injects the deployed commit as ``RENDER_GIT_COMMIT``. This module reads
that one variable and nothing else in the environment.

The route serving this is unauthenticated, like ``/livez``, so whatever it
returns is published to any caller that probes it. That makes the parse below
a **whitelist**: a value is returned only when it already is a commit SHA, and
it is returned normalized rather than as it was typed. The property this buys
is the reason the module exists -- whatever ends up in that variable, a
database URL, an API key, a whole ``.env`` line pasted into the wrong box, the
only characters this module is capable of publishing are hexadecimal digits.

Rejection is silent to the caller and logged for the operator, by length and
never by value: the reason to refuse a value here is that it might be a secret,
so writing it to the log instead would just move the leak.
"""

from __future__ import annotations

import logging
from os import getenv
import re


logger = logging.getLogger(__name__)

#: Render sets this on every deploy, to the full 40-character commit SHA.
#: It is the only environment variable this module reads.
REVISION_ENV_VAR = "RENDER_GIT_COMMIT"

#: A commit SHA, and nothing that is not one. The lower bound is git's own
#: abbreviation floor, so a short SHA set by hand on a host that is not Render
#: stays usable; the upper bound is a full SHA-1. A branch name, a URL, a key,
#: a version string and an empty value all fail to match, which is the point.
_COMMIT_SHA = re.compile(r"\A[0-9a-fA-F]{7,40}\Z")


def commit_sha_or_none(value: str | None) -> str | None:
    """`value` as a normalized commit SHA, or ``None`` when it is not one.

    Pure, and separate from the environment read below, so the decision about
    what may be published can be tested against inputs a process environment
    is awkward to hold.
    """

    if value is None:
        return None

    candidate = value.strip()
    if not _COMMIT_SHA.match(candidate):
        return None

    return candidate.lower()


def deployed_revision() -> str | None:
    """The commit this process was built from, or ``None`` if unknown.

    ``None`` covers two situations, and deliberately does not distinguish them
    to the caller: the variable is unset, or it holds something that is not a
    commit SHA. Only the second is a misconfiguration, and
    `log_revision_configuration` is what tells the operator about it.
    """

    return commit_sha_or_none(getenv(REVISION_ENV_VAR))


def log_revision_configuration() -> bool:
    """Warn once, at startup, when the revision variable is set but unusable.

    Called from the application lifespan rather than from the route: a warning
    emitted per request would repeat for every probe, and the condition it
    reports cannot change without a restart. Returns whether it warned, so the
    branch is assertable without reading log records.

    Without this, a variable set to the wrong thing is indistinguishable from
    one that was never set -- both report ``null`` -- and an operator who did
    wire it up would be sent looking in the wrong place.
    """

    raw = getenv(REVISION_ENV_VAR)
    if raw is None or not raw.strip():
        return False
    if commit_sha_or_none(raw) is not None:
        return False

    logger.warning(
        "%s is set but is not a commit SHA (length %d); the deployed "
        "revision will be reported as unknown. The value is not logged: a "
        "variable holding the wrong thing may be holding a secret.",
        REVISION_ENV_VAR,
        len(raw.strip()),
    )
    return True
