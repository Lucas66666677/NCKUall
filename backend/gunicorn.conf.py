from __future__ import annotations

from os import getenv

from app.observability.logging import configure_logging


bind = f"0.0.0.0:{getenv('PORT', '8000')}"
workers = int(getenv("WEB_CONCURRENCY", "2"))
worker_class = "uvicorn.workers.UvicornWorker"
timeout = int(getenv("GUNICORN_TIMEOUT", "120"))
accesslog = None
errorlog = "-"
capture_output = True


def on_starting(_server) -> None:
    """Configure the Gunicorn master process before workers are forked."""

    configure_logging()


def post_fork(_server, _worker) -> None:
    """Reapply JSON logging inside each worker process."""

    configure_logging()
