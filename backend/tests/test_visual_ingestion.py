from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace
from typing import Any, Protocol
from unittest.mock import AsyncMock

import fitz
import pytest
from fastapi import FastAPI, HTTPException, UploadFile
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import Response

from app.auth import (
    AuthUser,
    verify_visual_ingestion_user,
)
from app.main import app
from app.models import Activity, Course, Department
from app.security.rate_limit import RedisRateLimiter
from app.security.visual_ingestion import (
    enforce_visual_ingestion_rate_limit,
)
from app.visual_ingestion.files import (
    ValidatedVisualUpload,
    read_and_validate_upload,
)
from app.visual_ingestion.schemas import (
    CourseVisualExtraction,
    EventVisualExtraction,
    ExtractionErrorCode,
    VisualIngestType,
)
from app.visual_ingestion.service import (
    OpenAIVisualParser,
    ensure_extraction_is_usable,
)


class AccessTokenFactory(Protocol):
    def __call__(
        self,
        email: str,
        *,
        extra_claims: dict[str, Any] | None = None,
    ) -> str: ...


class FakeRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    async def eval(
        self,
        _script: str,
        _numkeys: int,
        key: str,
        window_seconds: int,
    ) -> list[int]:
        self.counts[key] = self.counts.get(key, 0) + 1
        return [self.counts[key], window_seconds]


class FakeParser:
    def __init__(
        self,
        extraction: EventVisualExtraction | CourseVisualExtraction,
    ) -> None:
        self.extraction = extraction
        self.calls: list[dict[str, Any]] = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        return self.extraction


def make_png() -> bytes:
    document = fitz.open()
    page = document.new_page(width=320, height=180)
    page.insert_text((24, 60), "NCKU visual ingestion test")
    content = page.get_pixmap().tobytes("png")
    document.close()
    return content


def make_pdf(page_count: int) -> bytes:
    document = fitz.open()
    for index in range(page_count):
        page = document.new_page()
        page.insert_text((72, 72), f"Page {index + 1}")
    content = document.tobytes()
    document.close()
    return content


def make_upload(
    content: bytes,
    *,
    filename: str,
    content_type: str,
) -> UploadFile:
    return UploadFile(
        file=BytesIO(content),
        filename=filename,
        headers=Headers({"content-type": content_type}),
    )


def make_request(test_app: FastAPI) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/admin/ingest/visual",
            "headers": [],
            "client": ("203.0.113.10", 12345),
            "server": ("testserver", 80),
            "scheme": "http",
            "app": test_app,
        }
    )


def event_extraction() -> EventVisualExtraction:
    return EventVisualExtraction(
        readable=True,
        confidence=0.96,
        error_code=ExtractionErrorCode.NONE,
        error_message=None,
        event_name="成大單車節",
        start_at=datetime.now(UTC) + timedelta(days=30),
        end_at=None,
        location="光復校區",
        organizer="國立成功大學",
        summary="年度校園大型活動。",
        registration_url="https://example.edu.tw/register",
    )


def course_extraction() -> CourseVisualExtraction:
    return CourseVisualExtraction(
        readable=True,
        confidence=0.94,
        error_code=ExtractionErrorCode.NONE,
        error_message=None,
        department_code="DPS",
        department_name="光電科學與工程學系",
        course_code="DPS1001",
        title_zh="光電導論",
        title_en="Introduction to Photonics",
        instructor_name="王教授",
        academic_year=115,
        semester=1,
        credits=3,
        required_for_major=True,
        description="光電科學基礎課程。",
        syllabus_url="https://example.edu.tw/syllabus",
    )


def test_visual_ingestion_rbac_accepts_admin_or_ncku() -> None:
    ncku_user = AuthUser(
        user_id="ncku-user",
        email="student@gs.ncku.edu.tw",
        claims={},
    )
    admin_user = AuthUser(
        user_id="admin-user",
        email="admin@gmail.com",
        claims={"app_metadata": {"is_admin": True}},
    )
    regular_user = AuthUser(
        user_id="regular-user",
        email="user@gmail.com",
        claims={},
    )

    assert verify_visual_ingestion_user(ncku_user) is ncku_user
    assert verify_visual_ingestion_user(admin_user) is admin_user
    with pytest.raises(HTTPException) as exc_info:
        verify_visual_ingestion_user(regular_user)
    assert exc_info.value.status_code == 403


async def test_upload_validation_checks_magic_size_and_pdf_pages() -> None:
    upload = await read_and_validate_upload(
        make_upload(
            make_png(),
            filename="poster.png",
            content_type="image/png",
        ),
        max_bytes=2 * 1024 * 1024,
        max_pdf_pages=2,
    )
    assert upload.media_type == "image/png"
    assert upload.page_count == 1
    assert upload.size_bytes > 0

    with pytest.raises(HTTPException) as mismatch:
        await read_and_validate_upload(
            make_upload(
                b"not-a-png",
                filename="poster.png",
                content_type="image/png",
            )
        )
    assert mismatch.value.status_code == 422

    with pytest.raises(HTTPException) as too_many_pages:
        await read_and_validate_upload(
            make_upload(
                make_pdf(2),
                filename="guide.pdf",
                content_type="application/pdf",
            ),
            max_pdf_pages=1,
        )
    assert too_many_pages.value.status_code == 422


async def test_openai_parser_uses_structured_image_input() -> None:
    extraction = event_extraction()
    parse = AsyncMock(
        return_value=SimpleNamespace(output_parsed=extraction)
    )
    parser = object.__new__(OpenAIVisualParser)
    parser.model = "gpt-4o-mini"
    parser.timeout_seconds = 10
    parser.client = SimpleNamespace(
        responses=SimpleNamespace(parse=parse)
    )
    content = make_png()
    upload = ValidatedVisualUpload(
        content=content,
        media_type="image/png",
        safe_filename="nckuall-visual-ingest.png",
        sha256="test-digest",
        size_bytes=len(content),
        page_count=1,
    )
    user = AuthUser(
        user_id="ncku-user",
        email="student@gs.ncku.edu.tw",
        claims={},
    )

    result = await parser.parse(
        upload=upload,
        ingest_type=VisualIngestType.EVENT,
        departments=[],
        user=user,
    )

    assert result is extraction
    kwargs = parse.await_args.kwargs
    assert kwargs["text_format"] is EventVisualExtraction
    document = kwargs["input"][0]["content"][1]
    assert document["type"] == "input_image"
    assert document["image_url"].startswith(
        "data:image/png;base64,"
    )
    assert kwargs["store"] is False


def test_unreadable_or_low_confidence_output_returns_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unreadable = EventVisualExtraction(
        readable=False,
        confidence=0.1,
        error_code=ExtractionErrorCode.UNREADABLE,
        error_message="文字模糊",
        event_name=None,
        start_at=None,
        end_at=None,
        location=None,
        organizer=None,
        summary=None,
        registration_url=None,
    )
    with pytest.raises(HTTPException) as unreadable_error:
        ensure_extraction_is_usable(unreadable)
    assert unreadable_error.value.status_code == 422

    monkeypatch.setenv("VISUAL_INGEST_MIN_CONFIDENCE", "0.99")
    with pytest.raises(HTTPException) as confidence_error:
        ensure_extraction_is_usable(event_extraction())
    assert confidence_error.value.status_code == 422


async def test_visual_ingestion_rate_limit_is_per_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VISUAL_INGEST_RATE_LIMIT_PER_HOUR", "2")
    test_app = FastAPI()
    test_app.state.visual_ingestion_rate_limiter = RedisRateLimiter(
        FakeRedis(),
        window_seconds=3600,
    )
    request = make_request(test_app)
    user = AuthUser(
        user_id="ncku-user",
        email="student@gs.ncku.edu.tw",
        claims={},
    )

    await enforce_visual_ingestion_rate_limit(
        request,
        Response(),
        user,
    )
    await enforce_visual_ingestion_rate_limit(
        request,
        Response(),
        user,
    )
    with pytest.raises(HTTPException) as exc_info:
        await enforce_visual_ingestion_rate_limit(
            request,
            Response(),
            user,
        )
    assert exc_info.value.status_code == 429
    assert exc_info.value.headers is not None
    assert exc_info.value.headers["Retry-After"] == "3600"


@pytest.mark.integration
async def test_visual_endpoint_upserts_event_and_course(
    client: AsyncClient,
    db_session: AsyncSession,
    make_access_token: AccessTokenFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    department = Department(
        code="DPS",
        name_zh="光電科學與工程學系",
        college="理學院",
        is_active=True,
    )
    db_session.add(department)
    await db_session.commit()

    limiter = RedisRateLimiter(
        FakeRedis(),
        window_seconds=3600,
    )
    monkeypatch.setattr(
        app.state,
        "visual_ingestion_rate_limiter",
        limiter,
        raising=False,
    )
    token = make_access_token("student@gs.ncku.edu.tw")
    headers = {"Authorization": f"Bearer {token}"}
    png = make_png()

    event_parser = FakeParser(event_extraction())
    monkeypatch.setattr(
        app.state,
        "visual_ingestion_parser",
        event_parser,
        raising=False,
    )
    event_response = await client.post(
        "/api/admin/ingest/visual",
        headers=headers,
        data={"ingest_type": "event"},
        files={"file": ("poster.png", png, "image/png")},
    )
    assert event_response.status_code == 200, event_response.text
    assert event_response.json()["action"] == "created"
    assert event_response.json()["title"] == "成大單車節"

    course_parser = FakeParser(course_extraction())
    app.state.visual_ingestion_parser = course_parser
    course_response = await client.post(
        "/api/admin/ingest/visual",
        headers=headers,
        data={"ingest_type": "course"},
        files={"file": ("course.png", png, "image/png")},
    )
    assert course_response.status_code == 200, course_response.text
    assert course_response.json()["action"] == "created"
    assert course_response.json()["title"] == "光電導論"
    assert course_parser.calls[0]["departments"] == [
        {"code": "DPS", "name": "光電科學與工程學系"}
    ]

    activities = list(
        (await db_session.scalars(select(Activity))).all()
    )
    courses = list((await db_session.scalars(select(Course))).all())
    assert len(activities) == 1
    assert activities[0].tags == ["AI 視覺匯入", "校園活動"]
    assert len(courses) == 1
    assert courses[0].tags == ["AI 視覺匯入", "課程簡章"]
