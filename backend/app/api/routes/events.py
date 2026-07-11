from typing import Annotated
from urllib.parse import urlparse
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    status,
)
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session

from app.controllers.analytics import record_search_log
from app.controllers.events import get_event_by_id, list_recent_events
from app.database import get_analytics_session_factory, get_db
from app.schemas import ActivityResponse
from app.security.developer_api import (
    EVENTS_READ_SCOPE,
    DeveloperPrincipal,
    require_developer_scope,
)


router = APIRouter(prefix="/events", tags=["events"])


@router.get(
    "",
    response_model=list[ActivityResponse],
    openapi_extra={"security": [{}]},
)
def get_events(
    db: Annotated[Session, Depends(get_db)],
    _developer: Annotated[
        DeveloperPrincipal | None,
        Depends(require_developer_scope(EVENTS_READ_SCOPE)),
    ],
    upcoming_only: Annotated[bool, Query(description="When true, hide past events.")] = True,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ActivityResponse]:
    """Get campus activities ordered by event time."""

    return list_recent_events(
        db,
        upcoming_only=upcoming_only,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{event_id}/visit",
    response_class=RedirectResponse,
    status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    summary="Open an event source and record an anonymous click",
)
def visit_event_source(
    event_id: UUID,
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
    analytics_session_factory: Annotated[
        async_sessionmaker[AsyncSession],
        Depends(get_analytics_session_factory),
    ],
) -> RedirectResponse:
    """Count an event click, then redirect only to a stored HTTP(S) URL."""

    event = get_event_by_id(db, event_id=event_id)
    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="找不到指定活動",
        )

    destination = event.official_url or event.registration_url
    parsed_url = urlparse(destination or "")
    if not destination or parsed_url.scheme not in {"http", "https"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="活動官方連結無效或尚未設定",
        )

    background_tasks.add_task(
        record_search_log,
        "event",
        event.id,
        session_factory=analytics_session_factory,
    )
    return RedirectResponse(
        url=destination,
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    )
