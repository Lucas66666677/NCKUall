from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.auth import (
    NCKU_EMAIL_DOMAINS,
    AuthUser,
    get_optional_user,
    verify_ncku_user,
)
from app.controllers.diagnostics import (
    DiagnosisGenerationError,
    diagnose_graduation_and_career,
    load_diagnosis_result_for_export,
    persist_diagnosis_result,
)
from app.controllers.analytics import get_trending_resources
from app.database import get_async_db, get_db
from app.schemas import DiagnosisRequest, DiagnosisResponse, TrendingResponse
from app.security.rate_limit import enforce_chat_rate_limit
from app.services.diagnosis_pdf import DiagnosisPdfPayload, render_diagnosis_pdf


router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get(
    "/trending",
    response_model=TrendingResponse,
    summary="Get anonymous campus resource trends",
    description=(
        "Return the five most-viewed courses, labs, and events from the "
        "rolling 74-hour anonymous analytics window."
    ),
)
def get_trending(
    db: Annotated[Session, Depends(get_db)],
) -> TrendingResponse:
    """Public aggregate endpoint; no visitor identifiers are stored."""

    return get_trending_resources(db)


@router.post(
    "/diagnose",
    response_model=DiagnosisResponse,
    summary="Diagnose graduation credit progress and career direction",
    description=(
        "Calculate hard graduation-credit gaps from department rules, then "
        "combine career-planning resources and RAG context to generate a "
        "Markdown diagnosis report."
    ),
)
async def diagnose_student_path(
    payload: DiagnosisRequest,
    _rate_limit: Annotated[None, Depends(enforce_chat_rate_limit)],
    db: Annotated[AsyncSession, Depends(get_async_db)],
    current_user: Annotated[AuthUser | None, Depends(get_optional_user)],
) -> DiagnosisResponse:
    """Generate a structured credit funnel plus an AI career diagnosis report."""

    try:
        response = await diagnose_graduation_and_career(db, payload)
        if (
            current_user is not None
            and current_user.email.endswith(NCKU_EMAIL_DOMAINS)
        ):
            result = await persist_diagnosis_result(
                db,
                payload=payload,
                response=response,
                user=current_user,
            )
            response = response.model_copy(
                update={
                    "diagnosis_id": result.id,
                    "session_id": result.session_id,
                }
            )
        return response
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="診斷資料庫查詢暫時失敗",
        ) from exc
    except DiagnosisGenerationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="AI 診斷報告生成暫時失敗",
        ) from exc


def _mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    if not domain:
        return "***"
    if len(local) <= 3:
        masked_local = f"{local[:1]}***"
    else:
        masked_local = f"{local[:3]}***{local[-1:]}"
    return f"{masked_local}@{domain}"


def _watermark_text(user: AuthUser) -> str:
    digest = sha256(
        f"{user.user_id or ''}:{user.email}".encode("utf-8"),
    ).hexdigest()[:8].upper()
    return f"NCKUall 認證報告 - {_mask_email(user.email)} - {digest}"


@router.get(
    "/diagnose/export",
    summary="Export the latest verified diagnosis report as PDF",
    description=(
        "Generate a polished in-memory PDF for the caller-owned diagnosis "
        "snapshot. No temporary file is written to disk."
    ),
    response_class=StreamingResponse,
)
async def export_diagnosis_pdf(
    db: Annotated[AsyncSession, Depends(get_async_db)],
    current_user: Annotated[AuthUser, Depends(verify_ncku_user)],
    session_id: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=120,
            description="Optional frontend session id. When omitted, the user's newest diagnosis is exported.",
        ),
    ] = None,
) -> StreamingResponse:
    """Return a private diagnosis PDF as an in-memory streaming response."""

    try:
        result = await load_diagnosis_result_for_export(
            db,
            user=current_user,
            session_id=session_id,
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="診斷報告查詢暫時失敗",
        ) from exc

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="找不到可匯出的診斷報告，請先重新產生診斷。",
        )

    diagnosis = DiagnosisResponse.model_validate(result.result_json).model_copy(
        update={
            "diagnosis_id": result.id,
            "session_id": result.session_id,
        }
    )
    pdf_bytes = await render_diagnosis_pdf(
        DiagnosisPdfPayload(
            diagnosis=diagnosis,
            watermark_text=_watermark_text(current_user),
            generated_at=datetime.now(timezone.utc),
        )
    )
    filename = f"nckuall-diagnosis-{result.id}.pdf"
    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store, private, max-age=0",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )
