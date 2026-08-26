from os import getenv
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.auth import AuthUser, verify_admin_user
from app.controllers.admin import (
    get_admin_dashboard_stats,
    list_course_submissions,
    list_flagged_reviews,
    review_course_submission,
    update_review_status,
)
from app.database import get_db
from app.models import (
    CourseSubmissionStatus,
    LifeReview,
    ReviewModerationStatus,
)
from app.realtime.notifications import (
    NotificationPayload,
    notification_broker,
)
from app.schemas import (
    AdminCourseSubmissionDecision,
    AdminCourseSubmissionResponse,
    AdminCourseSubmissionsResponse,
    AdminDashboardStatsResponse,
    AdminFlaggedReviewsResponse,
    AdminReviewResponse,
    AdminReviewStatusUpdate,
)
from app.security.audit import audit_admin_action, set_audit_changes


router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(verify_admin_user)],
)


@router.get(
    "/reviews/flagged",
    response_model=AdminFlaggedReviewsResponse,
    summary="List flagged student reviews",
)
def get_flagged_reviews(
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AdminFlaggedReviewsResponse:
    """Return reported reviews for the administrator moderation queue."""

    items, total = list_flagged_reviews(
        db,
        limit=limit,
        offset=offset,
    )
    return AdminFlaggedReviewsResponse(
        items=[AdminReviewResponse.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/course-submissions",
    response_model=AdminCourseSubmissionsResponse,
    summary="List course edits awaiting review",
)
def get_course_submissions(
    db: Annotated[Session, Depends(get_db)],
    submission_status: Annotated[
        CourseSubmissionStatus | None, Query(alias="status")
    ] = CourseSubmissionStatus.PENDING,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AdminCourseSubmissionsResponse:
    """Return course edits proposed by non-admins, oldest first."""

    items, total = list_course_submissions(
        db,
        status=submission_status,
        limit=limit,
        offset=offset,
    )
    return AdminCourseSubmissionsResponse(
        items=[
            AdminCourseSubmissionResponse.model_validate(item)
            for item in items
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/course-submissions/{submission_id}/review",
    response_model=AdminCourseSubmissionResponse,
    summary="Approve or reject a proposed course edit",
)
@audit_admin_action(
    action=lambda values, _result: (
        "APPROVE_COURSE_SUBMISSION"
        if values["payload"].approve
        else "REJECT_COURSE_SUBMISSION"
    ),
    target_resource="course_visual_submissions",
    target_id_getter=lambda values, _result: values["submission_id"],
)
def post_course_submission_review(
    submission_id: UUID,
    payload: AdminCourseSubmissionDecision,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    admin_user: Annotated[AuthUser, Depends(verify_admin_user)],
) -> AdminCourseSubmissionResponse:
    """Apply a queued course edit to the live record, or discard it."""

    submission = review_course_submission(
        db,
        submission_id=submission_id,
        approve=payload.approve,
        admin_user_id=admin_user.user_id or admin_user.email,
    )
    if submission is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="找不到指定課程提交",
        )
    set_audit_changes(
        request,
        before={"status": CourseSubmissionStatus.PENDING.value},
        after={
            "status": submission.status.value,
            "reviewed_by": submission.reviewed_by_user_id,
            "reviewed_at": submission.reviewed_at,
        },
        metadata={
            "course_id": str(submission.course_id),
            "submitted_by": submission.submitted_by_user_id,
        },
    )
    return AdminCourseSubmissionResponse.model_validate(submission)


@router.put(
    "/reviews/{review_id}/status",
    response_model=AdminReviewResponse,
    summary="Update review moderation status",
)
@audit_admin_action(
    action=lambda values, _result: (
        "HIDE_REVIEW"
        if values["payload"].status == ReviewModerationStatus.HIDDEN
        else "APPROVE_REVIEW"
        if values["payload"].status == ReviewModerationStatus.APPROVED
        else "MARK_REVIEW_PENDING"
    ),
    target_resource="life_reviews",
    target_id_getter=lambda values, _result: values["review_id"],
)
def put_review_status(
    review_id: UUID,
    payload: AdminReviewStatusUpdate,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    admin_user: Annotated[AuthUser, Depends(verify_admin_user)],
) -> AdminReviewResponse:
    """Hide, approve, or return a reported review to pending state."""

    existing_review = db.get(LifeReview, review_id)
    previous_status = (
        existing_review.moderation_status
        if existing_review is not None
        else None
    )
    review = update_review_status(
        db,
        review_id=review_id,
        moderation_status=payload.status,
        admin_user_id=admin_user.user_id or admin_user.email,
    )
    if review is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="找不到指定評論",
        )
    set_audit_changes(
        request,
        before={
            "moderation_status": previous_status.value
            if previous_status
            else None,
        },
        after={
            "moderation_status": review.moderation_status.value,
            "moderated_by": review.moderated_by,
            "moderated_at": review.moderated_at,
            "report_count": review.report_count,
        },
        metadata={
            "review_title": review.title,
            "review_type": review.review_type.value,
        },
    )

    try:
        popular_threshold = max(
            0,
            int(getenv("NOTIFICATION_POPULAR_REVIEW_MIN_REPORTS", "1")),
        )
    except ValueError:
        popular_threshold = 1
    if (
        payload.status == ReviewModerationStatus.APPROVED
        and previous_status != ReviewModerationStatus.APPROVED
        and review.report_count >= popular_threshold
    ):
        background_tasks.add_task(
            notification_broker.publish,
            NotificationPayload(
                kind="review.approved",
                topic="all",
                title=f"熱門評論已通過審核：{review.title or '校園評論'}",
                summary=(
                    review.content.strip()[:500]
                    or "一筆熱門校園評論已通過審核。"
                ),
                href=f"/life#review-{review.id}",
                resource_id=str(review.id),
            ),
        )

    return AdminReviewResponse.model_validate(review)


@router.get(
    "/stats",
    response_model=AdminDashboardStatsResponse,
    summary="Get administrator dashboard metrics",
)
def get_dashboard_stats(
    db: Annotated[Session, Depends(get_db)],
) -> AdminDashboardStatsResponse:
    """Return today's review volume, queue size, and popular queries."""

    today_count, pending_count, popular_terms = get_admin_dashboard_stats(db)
    return AdminDashboardStatsResponse(
        today_new_reviews=today_count,
        pending_flagged_reviews=pending_count,
        popular_search_terms=popular_terms,
    )
