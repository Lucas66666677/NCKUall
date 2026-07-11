from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import CareerResource, CareerResourceType


CATEGORY_ALIASES: dict[str, CareerResourceType] = {
    "exchange": CareerResourceType.EXCHANGE,
    "交換": CareerResourceType.EXCHANGE,
    "海外交換": CareerResourceType.EXCHANGE,
    "study_abroad": CareerResourceType.STUDY_ABROAD,
    "留學": CareerResourceType.STUDY_ABROAD,
    "雙聯學位": CareerResourceType.PROGRAM,
    "dual_degree": CareerResourceType.PROGRAM,
    "grad_school": CareerResourceType.GRAD_SCHOOL,
    "推甄": CareerResourceType.GRAD_SCHOOL,
    "研究所": CareerResourceType.GRAD_SCHOOL,
    "lab_review": CareerResourceType.LAB_REVIEW,
    "實驗室": CareerResourceType.LAB_REVIEW,
    "pre_master": CareerResourceType.PRE_MASTER,
    "預研": CareerResourceType.PRE_MASTER,
    "transfer_department": CareerResourceType.TRANSFER_DEPARTMENT,
    "轉系": CareerResourceType.TRANSFER_DEPARTMENT,
    "program": CareerResourceType.PROGRAM,
    "計畫": CareerResourceType.PROGRAM,
}


def normalize_category(category: str | None) -> CareerResourceType | None:
    """Map API category text to the internal enum used by PostgreSQL."""

    if category is None:
        return None

    normalized = category.strip().lower()
    return CATEGORY_ALIASES.get(normalized) or CareerResourceType(normalized)


def list_career_resources(
    db: Session,
    *,
    department_id: UUID | None = None,
    category: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[CareerResource]:
    """Return department-specific career resources with optional category filtering."""

    stmt = (
        select(CareerResource)
        .options(selectinload(CareerResource.department))
        .order_by(CareerResource.updated_at.desc(), CareerResource.title)
        .limit(limit)
        .offset(offset)
    )

    if department_id is not None:
        stmt = stmt.where(CareerResource.department_id == department_id)

    resource_type = normalize_category(category)
    if resource_type is not None:
        stmt = stmt.where(CareerResource.resource_type == resource_type)

    return list(db.scalars(stmt).all())
