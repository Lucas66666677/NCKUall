from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Course,
    CourseSubmissionStatus,
    CourseVisualSubmission,
    Department,
    LifeReview,
    LifeReviewType,
    ReviewModerationStatus,
)


pytestmark = pytest.mark.integration


class AccessTokenFactory(Protocol):
    def __call__(
        self,
        email: str,
        *,
        extra_claims: dict[str, Any] | None = None,
    ) -> str:
        ...


async def create_flagged_review(db_session: AsyncSession) -> LifeReview:
    review = LifeReview(
        review_type=LifeReviewType.RENTAL_WARNING,
        title="租屋隔音檢舉測試",
        content="此評論正在等待管理員確認。",
        area="大學路",
        author_alias="匿名同學",
        report_count=3,
        last_reported_at=datetime.now(UTC),
        moderation_status=ReviewModerationStatus.PENDING,
        tags=[],
        metadata_json={},
    )
    db_session.add(review)
    await db_session.commit()
    await db_session.refresh(review)
    return review


async def test_admin_routes_reject_guest_and_regular_user(
    client: AsyncClient,
    make_access_token: AccessTokenFactory,
) -> None:
    guest_response = await client.get("/api/admin/reviews/flagged")
    assert guest_response.status_code == 403
    assert guest_response.json()["detail"] == "僅限管理員存取"

    regular_token = make_access_token("student@gs.ncku.edu.tw")
    regular_response = await client.get(
        "/api/admin/reviews/flagged",
        headers={"Authorization": f"Bearer {regular_token}"},
    )
    assert regular_response.status_code == 403
    assert regular_response.json()["detail"] == "僅限管理員存取"


async def test_user_metadata_cannot_grant_admin_access(
    client: AsyncClient,
    make_access_token: AccessTokenFactory,
) -> None:
    token = make_access_token(
        "attacker@gmail.com",
        extra_claims={"user_metadata": {"is_admin": True}},
    )

    response = await client.get(
        "/api/admin/reviews/flagged",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "僅限管理員存取"


async def test_admin_can_paginate_and_moderate_flagged_reviews(
    client: AsyncClient,
    db_session: AsyncSession,
    make_access_token: AccessTokenFactory,
) -> None:
    review = await create_flagged_review(db_session)
    token = make_access_token(
        "admin@ncku.edu.tw",
        extra_claims={"app_metadata": {"is_admin": True}},
    )
    headers = {"Authorization": f"Bearer {token}"}

    queue_response = await client.get(
        "/api/admin/reviews/flagged?limit=1&offset=0",
        headers=headers,
    )
    assert queue_response.status_code == 200, queue_response.text
    queue = queue_response.json()
    assert queue["total"] == 1
    assert queue["limit"] == 1
    assert queue["offset"] == 0
    assert queue["items"][0]["id"] == str(review.id)
    assert queue["items"][0]["report_count"] == 3
    assert queue["items"][0]["moderation_status"] == "PENDING"

    update_response = await client.put(
        f"/api/admin/reviews/{review.id}/status",
        headers=headers,
        json={"status": "HIDDEN"},
    )
    assert update_response.status_code == 200, update_response.text
    assert update_response.json()["moderation_status"] == "HIDDEN"
    assert update_response.json()["moderated_by"]

    await db_session.refresh(review)
    assert review.moderation_status == ReviewModerationStatus.HIDDEN
    assert review.moderated_at is not None
    assert review.moderated_by is not None

    public_response = await client.get("/api/life/reviews")
    assert public_response.status_code == 200
    assert public_response.json() == []

    refreshed_queue = await client.get(
        "/api/admin/reviews/flagged?limit=10&offset=0",
        headers=headers,
    )
    assert refreshed_queue.status_code == 200
    assert refreshed_queue.json()["total"] == 0


async def test_admin_status_payload_is_strict_enum(
    client: AsyncClient,
    db_session: AsyncSession,
    make_access_token: AccessTokenFactory,
) -> None:
    review = await create_flagged_review(db_session)
    token = make_access_token(
        "admin@ncku.edu.tw",
        extra_claims={"app_metadata": {"is_admin": True}},
    )

    response = await client.put(
        f"/api/admin/reviews/{review.id}/status",
        headers={"Authorization": f"Bearer {token}"},
        json={"status": "DELETED"},
    )

    assert response.status_code == 422


async def test_approving_popular_review_publishes_notification(
    client: AsyncClient,
    db_session: AsyncSession,
    make_access_token: AccessTokenFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    review = await create_flagged_review(db_session)
    token = make_access_token(
        "admin@ncku.edu.tw",
        extra_claims={"app_metadata": {"is_admin": True}},
    )
    publish = AsyncMock()
    monkeypatch.setattr(
        "app.api.routes.admin.notification_broker.publish",
        publish,
    )

    response = await client.put(
        f"/api/admin/reviews/{review.id}/status",
        headers={"Authorization": f"Bearer {token}"},
        json={"status": "APPROVED"},
    )

    assert response.status_code == 200, response.text
    publish.assert_awaited_once()
    notification = publish.await_args.args[0]
    assert notification.kind == "review.approved"
    assert notification.topic == "all"
    assert notification.resource_id == str(review.id)
    assert notification.href == f"/life#review-{review.id}"


async def _seed_course_submission(
    db_session: AsyncSession,
) -> tuple[Course, CourseVisualSubmission]:
    department = Department(
        code="DPS",
        name_zh="光電科學與工程學系",
        is_active=True,
    )
    db_session.add(department)
    await db_session.flush()
    course = Course(
        department_id=department.id,
        course_code="DPS1001",
        title_zh="光電導論",
        instructor_name="王教授",
        credits=Decimal("3.0"),
    )
    db_session.add(course)
    await db_session.flush()
    submission = CourseVisualSubmission(
        course_id=course.id,
        status=CourseSubmissionStatus.PENDING,
        proposed={
            "title_zh": "光電導論（修正）",
            "instructor_name": "李教授",
            "credits": 2,
            # Fields the extraction did not read must not clear live data.
            "syllabus_url": None,
        },
        confidence=Decimal("0.900"),
    )
    db_session.add(submission)
    await db_session.commit()
    await db_session.refresh(course)
    await db_session.refresh(submission)
    return course, submission


async def test_admin_can_approve_queued_course_edit(
    client: AsyncClient,
    db_session: AsyncSession,
    make_access_token: AccessTokenFactory,
) -> None:
    course, submission = await _seed_course_submission(db_session)
    headers = {
        "Authorization": "Bearer "
        + make_access_token(
            "admin@ncku.edu.tw",
            extra_claims={"app_metadata": {"is_admin": True}},
        )
    }

    queue = await client.get("/api/admin/course-submissions", headers=headers)
    assert queue.status_code == 200, queue.text
    assert queue.json()["total"] == 1
    assert queue.json()["items"][0]["id"] == str(submission.id)

    approved = await client.post(
        f"/api/admin/course-submissions/{submission.id}/review",
        headers=headers,
        json={"approve": True},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "APPROVED"
    assert approved.json()["reviewed_by_user_id"]

    await db_session.refresh(course)
    assert course.title_zh == "光電導論（修正）"
    assert course.instructor_name == "李教授"
    assert course.credits == Decimal("2")
    # A null in the proposal means "not extracted", not "clear the field".
    assert course.syllabus_url is None

    # Queue drains, and the same submission cannot be reviewed twice.
    drained = await client.get("/api/admin/course-submissions", headers=headers)
    assert drained.json()["total"] == 0
    replay = await client.post(
        f"/api/admin/course-submissions/{submission.id}/review",
        headers=headers,
        json={"approve": True},
    )
    assert replay.status_code == 409


async def test_admin_reject_leaves_course_untouched(
    client: AsyncClient,
    db_session: AsyncSession,
    make_access_token: AccessTokenFactory,
) -> None:
    course, submission = await _seed_course_submission(db_session)
    headers = {
        "Authorization": "Bearer "
        + make_access_token(
            "admin@ncku.edu.tw",
            extra_claims={"app_metadata": {"is_admin": True}},
        )
    }

    rejected = await client.post(
        f"/api/admin/course-submissions/{submission.id}/review",
        headers=headers,
        json={"approve": False},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "REJECTED"

    await db_session.refresh(course)
    assert course.title_zh == "光電導論"
    assert course.instructor_name == "王教授"


async def test_course_submission_queue_rejects_non_admin(
    client: AsyncClient,
    db_session: AsyncSession,
    make_access_token: AccessTokenFactory,
) -> None:
    _course, submission = await _seed_course_submission(db_session)
    headers = {
        "Authorization": f"Bearer {make_access_token('student@gs.ncku.edu.tw')}"
    }

    assert (
        await client.get("/api/admin/course-submissions", headers=headers)
    ).status_code == 403
    assert (
        await client.post(
            f"/api/admin/course-submissions/{submission.id}/review",
            headers=headers,
            json={"approve": True},
        )
    ).status_code == 403
