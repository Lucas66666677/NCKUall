from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.controllers.careers import list_career_resources
from app.database import get_db
from app.schemas import CareerResourceResponse


router = APIRouter(prefix="/careers", tags=["careers"])


@router.get("", response_model=list[CareerResourceResponse])
def get_career_resources(
    db: Annotated[Session, Depends(get_db)],
    department_id: Annotated[UUID | None, Query(description="Filter career resources by department UUID.")] = None,
    category: Annotated[
        str | None,
        Query(description="Filter by category, e.g. 留學, 實驗室, 推甄, exchange, lab_review."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[CareerResourceResponse]:
    """Get department-specific career and program resources."""

    try:
        return list_career_resources(
            db,
            department_id=department_id,
            category=category,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported career category: {category}",
        ) from exc

