from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Activity


def get_event_by_id(db: Session, *, event_id: UUID) -> Activity | None:
    """Return one activity for a tracked official-link redirect."""

    return db.get(Activity, event_id)


def list_recent_events(
    db: Session,
    *,
    upcoming_only: bool = True,
    limit: int = 50,
    offset: int = 0,
) -> list[Activity]:
    """Return recent or upcoming campus activities ordered by time."""

    stmt = (
        select(Activity)
        .order_by(Activity.start_at.asc().nullslast(), Activity.created_at.desc())
        .limit(limit)
        .offset(offset)
    )

    if upcoming_only:
        now = datetime.now(timezone.utc)
        stmt = stmt.where(or_(Activity.start_at.is_(None), Activity.start_at >= now))

    return list(db.scalars(stmt).all())
