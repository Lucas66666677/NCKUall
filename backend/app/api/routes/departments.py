from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import (
    DEPARTMENTS_CACHE_KEY,
    AsyncCacheManager,
    get_cache_manager,
    low_churn_cache_ttl_seconds,
)
from app.database import get_async_db
from app.models import Department
from app.schemas import DepartmentResponse


router = APIRouter(prefix="/departments", tags=["departments"])


@router.get(
    "",
    response_model=list[DepartmentResponse],
    summary="List active departments",
)
async def get_departments(
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_async_db)],
) -> list[DepartmentResponse]:
    """Return active departments for global frontend filters."""

    cache_manager: AsyncCacheManager = get_cache_manager(request)
    cached = await cache_manager.get_models(
        DEPARTMENTS_CACHE_KEY,
        DepartmentResponse,
    )
    if cached.hit:
        response.headers["X-Cache"] = "HIT"
        return cached.value

    statement = (
        select(Department)
        .where(Department.is_active.is_(True))
        .order_by(Department.college, Department.name_zh)
    )
    departments = list((await db.scalars(statement)).all())
    department_responses = [
        DepartmentResponse.model_validate(department)
        for department in departments
    ]
    await cache_manager.set_models(
        DEPARTMENTS_CACHE_KEY,
        department_responses,
        model_type=DepartmentResponse,
        ttl_seconds=low_churn_cache_ttl_seconds(),
    )
    response.headers["X-Cache"] = "MISS" if cache_manager.enabled else "BYPASS"
    return department_responses
