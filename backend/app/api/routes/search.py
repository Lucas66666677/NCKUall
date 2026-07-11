from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.controllers.search import get_search_suggestions
from app.database import get_db
from app.schemas import SearchSuggestionResponse


router = APIRouter(prefix="/search", tags=["search"])


@router.get(
    "/suggestions",
    response_model=list[SearchSuggestionResponse],
    summary="Get course, instructor, and event typeahead suggestions",
)
def get_suggestions(
    db: Annotated[Session, Depends(get_db)],
    keyword: Annotated[
        str,
        Query(min_length=1, max_length=80),
    ],
    department_id: Annotated[UUID | None, Query()] = None,
) -> list[SearchSuggestionResponse]:
    """Return at most eight lightweight suggestions for public search."""

    return get_search_suggestions(
        db,
        keyword=keyword,
        department_id=department_id,
    )
