"""
Contract guards for the reranker the backend image bakes at build time.

`backend/Dockerfile` downloads the FlashRank ONNX model during the build and
keeps it in the image, so a cold container answers its first chat request
without reaching the network. Three declarations have to agree for that to
hold, and they live in two files that are never edited together:

* `ARG RAG_RERANK_BUILD_MODEL` -- the model the build downloads.
* `ENV RAG_RERANK_MODEL` / `ENV RAG_RERANK_CACHE_DIR` -- what the process asks
  for, and where it looks for it.
* the `getenv` fallbacks in `app/retrieval/reranker.py` -- what it asks for when
  the environment says nothing at all.

The Dockerfile states the coupling in prose -- "Override the build ARG and
runtime env together when changing it" -- and nothing enforces it. A comment is
not a gate: changing the served model, or dropping an `ENV` line while tidying
the image, leaves a build that bakes one model and a process that loads another,
with every other test in this suite green.

What that costs is not a slow first request. `app/main.py` preloads the ranker
inside `lifespan`, and `RAG_RERANK_FAIL_OPEN` defaults to false, so a cache miss
is paid during startup: the container either downloads a model on every cold
start, or -- on a host with no egress to the model host -- raises and never
comes up at all. The image is the only place that download is meant to happen.

Like `test_health_gate_contract.py` these checks read files. They import neither
the application nor `flashrank`, so they need no database and no network.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
DOCKERFILE = BACKEND / "Dockerfile"
RERANKER = BACKEND / "app" / "retrieval" / "reranker.py"
MAIN = BACKEND / "app" / "main.py"

#: The build ARG, and the runtime variables it has to agree with.
BUILD_MODEL_ARG = "RAG_RERANK_BUILD_MODEL"
MODEL_SETTING = "RAG_RERANK_MODEL"
CACHE_SETTING = "RAG_RERANK_CACHE_DIR"

_ASSIGNMENT = re.compile(r"([A-Z_][A-Z0-9_]*)=(\"[^\"]*\"|'[^']*'|\S+)")
_RANKER_CALL = re.compile(r"Ranker\((?P<arguments>[^)]*)\)")
_KEYWORD = re.compile(r"(\w+)=('[^']*'|\"[^\"]*\")")


def _dockerfile_text() -> str:
    """The Dockerfile with its line continuations folded away."""

    return DOCKERFILE.read_text(encoding="utf-8").replace("\\\n", " ")


def _directive_values(directive: str) -> dict[str, str]:
    """Every `NAME=value` pair the given Dockerfile directive declares."""

    values: dict[str, str] = {}
    for line in _dockerfile_text().splitlines():
        stripped = line.strip()
        if not stripped.startswith(f"{directive} "):
            continue
        for name, value in _ASSIGNMENT.findall(stripped[len(directive):]):
            values[name] = value.strip("\"'")
    return values


def _bake_arguments() -> dict[str, str]:
    """The `Ranker(...)` keyword arguments the image builds its cache with."""

    for line in _dockerfile_text().splitlines():
        match = _RANKER_CALL.search(line)
        if match is None:
            continue
        return {
            name: value.strip("\"'")
            for name, value in _KEYWORD.findall(match.group("arguments"))
        }
    return {}


def _runtime_settings() -> dict[str, tuple[str, str]]:
    """`Ranker(...)` keyword -> (environment variable, fallback) from the code.

    Read out of the source rather than by importing it: `flashrank` is a
    dependency of the chat path, and a contract check should not need it.
    """

    tree = ast.parse(RERANKER.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "id", None) != "Ranker":
            continue
        settings: dict[str, tuple[str, str]] = {}
        for keyword in node.keywords:
            call = keyword.value
            if not isinstance(call, ast.Call):
                continue
            if getattr(call.func, "id", None) != "getenv" or len(call.args) != 2:
                continue
            name, fallback = (ast.literal_eval(argument) for argument in call.args)
            settings[str(keyword.arg)] = (str(name), str(fallback))
        return settings
    return {}


def test_the_image_bakes_the_model_the_process_asks_for() -> None:
    """The build ARG and the runtime ENV are the pair the comment couples."""

    bake = _bake_arguments()
    assert bake, f"no Ranker(...) build step could be read from {DOCKERFILE.name}"
    assert bake.get("model_name") == f"${{{BUILD_MODEL_ARG}}}", (
        f"the build step names its model directly rather than through "
        f"{BUILD_MODEL_ARG}, so the ARG no longer describes what is in the image"
    )

    build_model = _directive_values("ARG").get(BUILD_MODEL_ARG)
    runtime_model = _directive_values("ENV").get(MODEL_SETTING)
    assert build_model, f"{DOCKERFILE.name} declares no default for {BUILD_MODEL_ARG}"
    assert build_model == runtime_model, (
        f"{DOCKERFILE.name} bakes {build_model!r} and runs {runtime_model!r}; the "
        f"process would fetch its model during startup instead of reading the image's"
    )


def test_the_build_writes_into_the_cache_the_process_reads() -> None:
    """One variable used by both steps, not two paths that happen to match."""

    cache_directory = _directive_values("ENV").get(CACHE_SETTING)
    assert cache_directory, (
        f"{DOCKERFILE.name} no longer sets {CACHE_SETTING}, so the process falls back "
        f"to a path outside the image's baked cache (see the test below)"
    )
    assert _bake_arguments().get("cache_dir") == f"${{{CACHE_SETTING}}}", (
        f"the build writes its cache somewhere other than {CACHE_SETTING}, so the "
        f"process would look in an empty directory"
    )
    assert f'mkdir -p "${{{CACHE_SETTING}}}"' in _dockerfile_text(), (
        f"the build no longer creates {CACHE_SETTING} before writing into it"
    )


def test_the_code_defaults_agree_with_the_image() -> None:
    """The image's ENV is belt and braces only while the fallback matches it."""

    runtime = _runtime_settings()
    assert runtime, f"no Ranker(...) call could be read from {RERANKER.name}"
    assert runtime.get("model_name", ("", ""))[0] == MODEL_SETTING
    assert runtime.get("cache_dir", ("", ""))[0] == CACHE_SETTING

    baked = _directive_values("ARG").get(BUILD_MODEL_ARG)
    assert runtime["model_name"][1] == baked, (
        f"{RERANKER.name} falls back to {runtime['model_name'][1]!r} while the image "
        f"bakes {baked!r}; an environment that loses {MODEL_SETTING} downloads a model"
    )


def test_the_code_cache_fallback_really_is_outside_the_image() -> None:
    """Guards the guard: proves the `ENV` rule above is load-bearing.

    If the fallback ever pointed at the baked directory, dropping
    `ENV RAG_RERANK_CACHE_DIR` would be harmless and that rule would be
    asserting nothing.
    """

    fallback = _runtime_settings()["cache_dir"][1]
    assert fallback != _directive_values("ENV").get(CACHE_SETTING), (
        f"the code's fallback cache directory is now the image's own {fallback!r}"
    )


def test_a_cache_miss_is_paid_at_startup_and_fails_closed() -> None:
    """Guards the guard: this is what makes the pairing above a release contract.

    Preloading is what moves a missing model out of the first user's request and
    into the deploy; failing closed is what stops a container that has to reach
    the network on every cold start from reporting itself ready to serve.
    """

    source = MAIN.read_text(encoding="utf-8")
    assert 'getenv("RAG_RERANK_PRELOAD", "true")' in source, (
        "the reranker is no longer preloaded during startup by default"
    )
    assert 'getenv("RAG_RERANK_FAIL_OPEN", "false")' in source, (
        "a failed preload no longer fails closed by default"
    )
