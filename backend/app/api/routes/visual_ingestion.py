from __future__ import annotations

import logging
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    UploadFile,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    AuthUser,
    is_admin_user,
    verify_visual_ingestion_user,
)
from app.database import get_async_db
from app.models import Department
from app.security.visual_ingestion import (
    enforce_visual_ingestion_rate_limit,
)
from app.visual_ingestion.controller import (
    upsert_course_from_visual,
    upsert_event_from_visual,
)
from app.visual_ingestion.files import read_and_validate_upload
from app.visual_ingestion.schemas import (
    CourseVisualExtraction,
    EventVisualExtraction,
    VisualIngestResponse,
    VisualIngestType,
)
from app.visual_ingestion.service import (
    VisualParser,
    ensure_extraction_is_usable,
    get_visual_parser,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/ingest", tags=["admin-ingestion"])


async def active_department_catalog(
    db: AsyncSession,
) -> list[dict[str, str]]:
    rows = (
        await db.execute(
            select(Department.code, Department.name_zh)
            .where(Department.is_active.is_(True))
            .order_by(Department.code)
        )
    ).all()
    await db.rollback()
    return [
        {"code": str(row.code), "name": str(row.name_zh)}
        for row in rows
    ]


@router.post(
    "/visual",
    response_model=VisualIngestResponse,
    summary="Extract and upsert data from a trusted visual upload",
    description=(
        "Accepts PNG, JPEG, or PDF documents from an administrator or "
        "verified NCKU account. The source is parsed with multimodal "
        "Structured Outputs and persisted only after strict validation."
    ),
)
async def ingest_visual_document(
    file: Annotated[
        UploadFile,
        File(description="PNG, JPG, or PDF source document."),
    ],
    ingest_type: Annotated[
        VisualIngestType,
        Form(description="Target resource type: event or course."),
    ],
    db: Annotated[AsyncSession, Depends(get_async_db)],
    user: Annotated[
        AuthUser,
        Depends(verify_visual_ingestion_user),
    ],
    parser: Annotated[VisualParser, Depends(get_visual_parser)],
    _rate_limit: Annotated[
        None,
        Depends(enforce_visual_ingestion_rate_limit),
    ],
) -> VisualIngestResponse:
    upload = await read_and_validate_upload(file)
    departments = (
        await active_department_catalog(db)
        if ingest_type == VisualIngestType.COURSE
        else []
    )
    extraction = await parser.parse(
        upload=upload,
        ingest_type=ingest_type,
        departments=departments,
        user=user,
    )
    ensure_extraction_is_usable(extraction)

    if ingest_type == VisualIngestType.EVENT:
        if not isinstance(extraction, EventVisualExtraction):
            raise TypeError("Parser returned the wrong event schema.")
        persisted = await upsert_event_from_visual(
            db,
            extraction,
            is_admin=is_admin_user(user),
        )
    else:
        if not isinstance(extraction, CourseVisualExtraction):
            raise TypeError("Parser returned the wrong course schema.")
        persisted = await upsert_course_from_visual(db, extraction)

    logger.info(
        "visual_ingestion_completed",
        extra={
            "ingest_type": ingest_type.value,
            "action": persisted.action,
            "resource_id": str(persisted.resource_id),
            "upload_sha256": upload.sha256,
            "upload_bytes": upload.size_bytes,
            "upload_pages": upload.page_count,
            "actor_id": user.user_id,
        },
    )
    return VisualIngestResponse(
        ingest_type=ingest_type,
        action=persisted.action,
        resource_id=persisted.resource_id,
        title=persisted.title,
        confidence=extraction.confidence,
        tags=persisted.tags,
        created_at=persisted.created_at,
        updated_at=persisted.updated_at,
        extracted=extraction,
    )
