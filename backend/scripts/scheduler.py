"""Central orchestrator for NCKUall scraper and data pipelines.

This script intentionally executes each scraper/importer as a subprocess so the
individual scripts remain independently runnable and fault-isolated.

Schedules, Asia/Taipei timezone:
    - Course catalog pipeline: monthly on day 1 at 01:15
    - Campus life / events / career info pipeline: daily at 02:00
    - PTT + Dcard review enrichment/import pipeline: Sunday at 03:00

Examples:
    python backend/scripts/scheduler.py --serve
    python backend/scripts/scheduler.py --run-once daily
    python backend/scripts/scheduler.py --run-once weekly_reviews
    python backend/scripts/scheduler.py --run-once courses --dry-run
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

try:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger
except ImportError:  # pragma: no cover - exercised only when deps are missing
    BlockingScheduler = None  # type: ignore[assignment]
    CronTrigger = None  # type: ignore[assignment]


LOGGER = logging.getLogger("nckuall_scheduler")
TAIPEI_TIMEZONE = "Asia/Taipei"
DEFAULT_REVIEW_KEYWORDS = "微積分,通識,教授,課程,甜,涼,硬,必修,選修"


@dataclass(frozen=True, slots=True)
class PipelineTask:
    name: str
    command: list[str]
    timeout_seconds: int = 7200
    required_outputs: tuple[str, ...] = ()


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def backend_root() -> Path:
    return Path(__file__).resolve().parents[1]


def logs_dir() -> Path:
    return project_root() / "logs"


def python_cmd(relative_script: str, *args: str) -> list[str]:
    return [sys.executable, str(project_root() / relative_script), *args]


def configure_logging(verbose: bool = False) -> None:
    logs_dir().mkdir(parents=True, exist_ok=True)
    log_path = logs_dir() / "scraper_cron.log"
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    LOGGER.info("Logging initialized: %s", log_path)


def task_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def format_command(command: list[str]) -> str:
    return " ".join(f'"{part}"' if " " in part else part for part in command)


def validate_outputs(task: PipelineTask) -> None:
    missing = [path for path in task.required_outputs if not (project_root() / path).exists()]
    if missing:
        raise FileNotFoundError(f"Task {task.name} did not produce required output(s): {missing}")


def run_task(task: PipelineTask, *, dry_run: bool = False) -> None:
    LOGGER.info("TASK START: %s", task.name)
    LOGGER.info("COMMAND: %s", format_command(task.command))
    if dry_run:
        LOGGER.info("DRY RUN: skipped execution for task=%s", task.name)
        return

    started = time.monotonic()
    try:
        result = subprocess.run(
            task.command,
            cwd=project_root(),
            env=task_env(),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=task.timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = time.monotonic() - started
        LOGGER.error("TASK TIMEOUT: %s after %.1fs", task.name, elapsed)
        if exc.stdout:
            LOGGER.error("STDOUT before timeout:\n%s", exc.stdout)
        if exc.stderr:
            LOGGER.error("STDERR before timeout:\n%s", exc.stderr)
        raise

    elapsed = time.monotonic() - started
    if result.stdout:
        LOGGER.info("STDOUT %s:\n%s", task.name, result.stdout.strip())
    if result.stderr:
        LOGGER.warning("STDERR %s:\n%s", task.name, result.stderr.strip())

    if result.returncode != 0:
        LOGGER.error("TASK FAILED: %s exit_code=%s elapsed=%.1fs", task.name, result.returncode, elapsed)
        raise RuntimeError(f"Task failed: {task.name} exit_code={result.returncode}")

    validate_outputs(task)
    LOGGER.info("TASK DONE: %s elapsed=%.1fs", task.name, elapsed)


def run_pipeline(name: str, tasks: list[PipelineTask], *, dry_run: bool = False) -> None:
    LOGGER.info("PIPELINE START: %s task_count=%s dry_run=%s", name, len(tasks), dry_run)
    started = time.monotonic()
    try:
        for task in tasks:
            run_task(task, dry_run=dry_run)
    except Exception:
        LOGGER.exception("PIPELINE FAILED: %s", name)
        raise
    LOGGER.info("PIPELINE DONE: %s elapsed=%.1fs", name, time.monotonic() - started)


def course_pipeline_tasks() -> list[PipelineTask]:
    return [
        PipelineTask(
            name="scrape_course_catalog",
            command=python_cmd(
                "backend/scrapers/ncku_course_catalog.py",
                "--output",
                "data/ncku_courses_f7.json",
            ),
            timeout_seconds=6 * 60 * 60,
            required_outputs=("data/ncku_courses_f7.json",),
        ),
        PipelineTask(
            name="scrape_course_syllabus",
            command=python_cmd(
                "backend/scrapers/ncku_syllabus.py",
                "--input",
                "data/ncku_courses_f7.json",
                "--output",
                "data/ncku_courses_detailed.json",
            ),
            timeout_seconds=6 * 60 * 60,
            required_outputs=("data/ncku_courses_detailed.json",),
        ),
        PipelineTask(
            name="import_courses_to_supabase",
            command=python_cmd(
                "backend/scripts/import_to_supabase.py",
                "--type",
                "courses",
                "--file",
                "data/ncku_courses_detailed.json",
                "--create-missing-departments",
            ),
            timeout_seconds=60 * 60,
        ),
    ]


def daily_pipeline_tasks() -> list[PipelineTask]:
    return [
        PipelineTask(
            name="scrape_campus_life",
            command=python_cmd(
                "backend/scrapers/campus_life.py",
                "--output",
                "data/campus_life_updates.json",
            ),
            timeout_seconds=60 * 60,
            required_outputs=("data/campus_life_updates.json",),
        ),
        PipelineTask(
            name="scrape_ncku_events",
            command=python_cmd(
                "backend/scrapers/ncku_events.py",
                "--output",
                "data/upcoming_events.json",
            ),
            timeout_seconds=90 * 60,
            required_outputs=("data/upcoming_events.json",),
        ),
        PipelineTask(
            name="scrape_career_fairs",
            command=python_cmd(
                "backend/scrapers/career_fairs.py",
                "--pages",
                "3",
                "--output",
                "data/career_events.json",
            ),
            timeout_seconds=90 * 60,
            required_outputs=("data/career_events.json",),
        ),
    ]


def weekly_review_pipeline_tasks() -> list[PipelineTask]:
    keywords = os.getenv("NCKUALL_REVIEW_KEYWORDS", DEFAULT_REVIEW_KEYWORDS)
    return [
        PipelineTask(
            name="scrape_ptt_reviews",
            command=python_cmd(
                "backend/scrapers/ptt_course_reviews.py",
                "--keywords",
                keywords,
                "--boards",
                "NCKU,Course",
                "--pages",
                "3",
                "--include-comments",
                "--output",
                "data/ptt_reviews.json",
            ),
            timeout_seconds=2 * 60 * 60,
            required_outputs=("data/ptt_reviews.json",),
        ),
        PipelineTask(
            name="scrape_dcard_reviews",
            command=python_cmd(
                "backend/scrapers/dcard_reviews.py",
                "--keywords",
                keywords,
                "--boards",
                "ncku,course",
                "--pages",
                "3",
                "--output",
                "data/dcard_reviews.json",
            ),
            timeout_seconds=2 * 60 * 60,
            required_outputs=("data/dcard_reviews.json",),
        ),
        PipelineTask(
            name="ai_enrich_reviews",
            command=python_cmd(
                "backend/scripts/ai_enrichment_pipeline.py",
                "--inputs",
                "data/ptt_reviews.json",
                "data/dcard_reviews.json",
                "--output",
                "data/unified_reviews_enriched.json",
                "--concurrency",
                os.getenv("NCKUALL_AI_ENRICH_CONCURRENCY", "3"),
            ),
            timeout_seconds=6 * 60 * 60,
            required_outputs=("data/unified_reviews_enriched.json",),
        ),
        PipelineTask(
            name="import_unified_reviews_to_supabase",
            command=python_cmd(
                "backend/scripts/import_to_supabase.py",
                "--type",
                "unified_reviews",
                "--file",
                "data/unified_reviews_enriched.json",
            ),
            timeout_seconds=60 * 60,
        ),
    ]


PIPELINE_BUILDERS: dict[str, Callable[[], list[PipelineTask]]] = {
    "courses": course_pipeline_tasks,
    "daily": daily_pipeline_tasks,
    "weekly_reviews": weekly_review_pipeline_tasks,
}


def run_once(pipeline_name: str, *, dry_run: bool = False) -> None:
    builder = PIPELINE_BUILDERS[pipeline_name]
    run_pipeline(pipeline_name, builder(), dry_run=dry_run)


def ensure_apscheduler_available() -> None:
    if BlockingScheduler is None or CronTrigger is None:
        raise RuntimeError(
            "APScheduler is not installed. Run: pip install APScheduler "
            "or install backend/requirements.txt."
        )


def serve(dry_run: bool = False) -> None:
    ensure_apscheduler_available()
    scheduler = BlockingScheduler(timezone=TAIPEI_TIMEZONE)
    scheduler.add_job(
        lambda: run_once("courses", dry_run=dry_run),
        CronTrigger(day=1, hour=1, minute=15, timezone=TAIPEI_TIMEZONE),
        id="monthly_course_catalog_pipeline",
        name="Monthly course catalog pipeline",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=2 * 60 * 60,
    )
    scheduler.add_job(
        lambda: run_once("daily", dry_run=dry_run),
        CronTrigger(hour=2, minute=0, timezone=TAIPEI_TIMEZONE),
        id="daily_campus_events_pipeline",
        name="Daily campus life/events/career pipeline",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=60 * 60,
    )
    scheduler.add_job(
        lambda: run_once("weekly_reviews", dry_run=dry_run),
        CronTrigger(day_of_week="sun", hour=3, minute=0, timezone=TAIPEI_TIMEZONE),
        id="weekly_review_enrichment_pipeline",
        name="Weekly review scraping/enrichment/import pipeline",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=2 * 60 * 60,
    )
    LOGGER.info("Scheduler starting in timezone=%s dry_run=%s", TAIPEI_TIMEZONE, dry_run)
    LOGGER.info("Registered jobs: %s", [job.id for job in scheduler.get_jobs()])
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        LOGGER.info("Scheduler stopped.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="NCKUall scraper/data pipeline scheduler.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--serve", action="store_true", help="Run APScheduler forever.")
    mode.add_argument(
        "--run-once",
        choices=sorted(PIPELINE_BUILDERS.keys()),
        help="Run one pipeline immediately and exit.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Log commands without executing them.")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.verbose)
    LOGGER.info("Project root: %s", project_root())
    LOGGER.info("Backend root: %s", backend_root())
    if args.run_once:
        run_once(args.run_once, dry_run=args.dry_run)
    else:
        serve(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
