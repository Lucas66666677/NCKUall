#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

VENV_DIR="$ROOT_DIR/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3.11}"
CONTAINER_NAME="${TEST_POSTGRES_CONTAINER:-nckuall-test-postgres}"
POSTGRES_IMAGE="${TEST_POSTGRES_IMAGE:-pgvector/pgvector:pg16}"
POSTGRES_USER="${TEST_POSTGRES_USER:-nckuall}"
POSTGRES_PASSWORD="${TEST_POSTGRES_PASSWORD:-nckuall}"
POSTGRES_DB="${TEST_POSTGRES_DB:-nckuall_test}"
POSTGRES_PORT="${TEST_POSTGRES_PORT:-55432}"
REPORT_FILE="${TEST_REPORT_FILE:-test-report.txt}"

echo "==> NCKUall backend clean setup and test"
echo "==> Working directory: $ROOT_DIR"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "!! $PYTHON_BIN was not found. Falling back to python3."
  PYTHON_BIN="python3"
fi

PYTHON_VERSION="$("$PYTHON_BIN" - <<'PY'
import sys
print(f"{sys.version_info.major}.{sys.version_info.minor}")
PY
)"

if [[ "$PYTHON_VERSION" != "3.11" ]]; then
  echo "!! Python 3.11 is required. Current interpreter is Python $PYTHON_VERSION."
  exit 1
fi

if [[ -d "$VENV_DIR" ]]; then
  echo "==> Removing old virtual environment at $VENV_DIR"
  rm -rf "$VENV_DIR"
fi

echo "==> Creating clean Python 3.11 virtual environment at $VENV_DIR"
"$PYTHON_BIN" -m venv "$VENV_DIR"

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

echo "==> Upgrading pip tooling"
python -m pip install --upgrade pip setuptools wheel

echo "==> Installing runtime dependencies from requirements.txt"
python -m pip install -r requirements.txt

echo "==> Installing test dependencies from requirements-dev.txt"
python -m pip install -r requirements-dev.txt

DOCKER_AVAILABLE="false"
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  DOCKER_AVAILABLE="true"
fi

if [[ "$DOCKER_AVAILABLE" == "true" ]]; then
  echo "==> Docker is running. Preparing temporary pgvector PostgreSQL container."
  docker pull "$POSTGRES_IMAGE"

  if docker ps -a --format '{{.Names}}' | grep -Fxq "$CONTAINER_NAME"; then
    if ! docker ps --format '{{.Names}}' | grep -Fxq "$CONTAINER_NAME"; then
      echo "==> Starting existing container $CONTAINER_NAME"
      docker start "$CONTAINER_NAME" >/dev/null
    else
      echo "==> Container $CONTAINER_NAME is already running"
    fi
  else
    echo "==> Creating container $CONTAINER_NAME on localhost:$POSTGRES_PORT"
    docker run \
      --name "$CONTAINER_NAME" \
      -e POSTGRES_USER="$POSTGRES_USER" \
      -e POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
      -e POSTGRES_DB="$POSTGRES_DB" \
      -p "$POSTGRES_PORT:5432" \
      -d "$POSTGRES_IMAGE" >/dev/null
  fi

  echo "==> Waiting for PostgreSQL to accept connections"
  for attempt in {1..45}; do
    if docker exec "$CONTAINER_NAME" pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; then
      break
    fi
    if [[ "$attempt" -eq 45 ]]; then
      echo "!! PostgreSQL did not become ready in time."
      exit 1
    fi
    sleep 1
  done

  export TEST_DATABASE_URL="postgresql+psycopg://$POSTGRES_USER:$POSTGRES_PASSWORD@127.0.0.1:$POSTGRES_PORT/$POSTGRES_DB"
  export DATABASE_URL="$TEST_DATABASE_URL"
  export DATABASE_READ_URL="$TEST_DATABASE_URL"
  echo "==> TEST_DATABASE_URL=$TEST_DATABASE_URL"
else
  echo "!! Docker is not available or not running."
  if [[ -z "${TEST_DATABASE_URL:-}" ]]; then
    echo "!! TEST_DATABASE_URL is not set. PostgreSQL integration tests will be skipped by pytest fixtures."
  else
    export DATABASE_URL="$TEST_DATABASE_URL"
    export DATABASE_READ_URL="$TEST_DATABASE_URL"
    echo "==> Using existing TEST_DATABASE_URL from environment."
  fi
fi

export SUPABASE_JWT_SECRET="${SUPABASE_JWT_SECRET:-test-only-supabase-jwt-secret}"
export SUPABASE_JWT_AUDIENCE="${SUPABASE_JWT_AUDIENCE:-authenticated}"
export CHAT_MODERATION_ENABLED="${CHAT_MODERATION_ENABLED:-false}"
export RAG_RERANK_PRELOAD="${RAG_RERANK_PRELOAD:-false}"
export CHECK_VECTOR_INDEXES_ON_STARTUP="${CHECK_VECTOR_INDEXES_ON_STARTUP:-false}"
export REDIS_URL="${REDIS_URL:-}"

echo "==> Running pytest"
set +e
python -m pytest -v --durations=5 2>&1 | tee "$REPORT_FILE"
PYTEST_EXIT=${PIPESTATUS[0]}
set -e

echo "==> Text report: $ROOT_DIR/$REPORT_FILE"

if [[ "$PYTEST_EXIT" -ne 0 ]]; then
  echo "!! Tests failed with exit code $PYTEST_EXIT"
  exit "$PYTEST_EXIT"
fi

echo "==> All backend tests passed."
