FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000 \
    WEB_CONCURRENCY=2 \
    GUNICORN_TIMEOUT=120 \
    RAG_RERANK_MODEL=ms-marco-MultiBERT-L-12 \
    RAG_RERANK_CACHE_DIR=/app/.cache/flashrank \
    RAG_RERANK_PRELOAD=true

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

# Bake the reranker into the image so the first chat request never downloads
# a model. Override the build ARG and runtime env together when changing it.
ARG RAG_RERANK_BUILD_MODEL=ms-marco-MultiBERT-L-12
RUN mkdir -p "${RAG_RERANK_CACHE_DIR}" \
    && python -c "from flashrank import Ranker; Ranker(model_name='${RAG_RERANK_BUILD_MODEL}', cache_dir='${RAG_RERANK_CACHE_DIR}', max_length=256)"

COPY app ./app
COPY scripts ./scripts
COPY alembic ./alembic
COPY alembic.ini ./
COPY gunicorn.conf.py ./

RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

# Liveness, not readiness. `/health` pings the writer and the read replica, so
# an image gated there reports a database outage as a dead process and restarts
# a backend that was never the thing that broke. `/livez` reports the process
# and only the process. `docker-compose.prod.yml` overrides this healthcheck
# with the same route; a launch path that does not override it inherits
# whatever is written here, so the two have to agree.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/livez" || exit 1

CMD ["gunicorn", "--config", "gunicorn.conf.py", "app.main:app"]
