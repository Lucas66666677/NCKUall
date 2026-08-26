from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models import (
    ActivityType,
    CareerResourceType,
    CourseDifficulty,
    CourseSubmissionStatus,
    LifeReviewType,
    LifeResourceType,
    ReviewModerationStatus,
)


class DepartmentSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name_zh: str
    name_en: str | None = None
    college: str | None = None


class DepartmentResponse(DepartmentSummary):
    is_active: bool


class CourseGradeDistributionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    academic_year: int
    semester: int
    enrollment_count: int | None = None
    avg_score: Decimal | None = None
    median_score: Decimal | None = None
    pass_rate: Decimal | None = None
    grade_buckets: dict = Field(default_factory=dict)
    source_url: str | None = None
    created_at: datetime
    updated_at: datetime


class CourseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    department_id: UUID
    department: DepartmentSummary | None = None
    course_code: str
    title_zh: str
    title_en: str | None = None
    instructor_name: str | None = None
    academic_year: int | None = None
    semester: int | None = None
    credits: Decimal | None = None
    required_for_major: bool
    tags: list[str] = Field(default_factory=list)
    syllabus_url: str | None = None
    description: str | None = None
    difficulty: CourseDifficulty
    grade_distributions: list[CourseGradeDistributionResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class CourseReviewCreate(BaseModel):
    reviewer_department_id: UUID | None = None
    overall_rating: int | None = Field(default=None, ge=1, le=5)
    workload_rating: int | None = Field(default=None, ge=1, le=5)
    difficulty_rating: int | None = Field(default=None, ge=1, le=5)
    grading_fairness_rating: int | None = Field(
        default=None,
        ge=1,
        le=5,
    )
    content: str = Field(min_length=5, max_length=4000)
    tags: list[str] = Field(default_factory=list, max_length=12)


class CourseReviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    course_id: UUID
    reviewer_department_id: UUID | None = None
    overall_rating: int | None = None
    workload_rating: int | None = None
    difficulty_rating: int | None = None
    grading_fairness_rating: int | None = None
    content: str
    tags: list[str] = Field(default_factory=list)
    is_verified: bool
    is_approved: bool
    score: Decimal
    ai_spam_confidence: Decimal
    created_at: datetime
    updated_at: datetime


class CompletedCourseInput(BaseModel):
    course_code: str = Field(
        min_length=1,
        max_length=64,
        description="Completed course code, e.g. DPS1001.",
    )
    grade: str | None = Field(
        default=None,
        max_length=16,
        description="Letter or numeric grade. F/W/NP and numeric grades below 60 are treated as not passed.",
    )
    credits: Decimal | None = Field(
        default=None,
        ge=0,
        le=20,
        description="Optional transcript credits; database course credits are used when omitted.",
    )
    title: str | None = Field(
        default=None,
        max_length=200,
        description="Optional transcript course title for display and fallback matching.",
    )
    category: Literal[
        "major_required",
        "major_elective",
        "general_education",
        "free_elective",
        "other",
    ] | None = Field(
        default=None,
        description="Optional frontend/user-provided category override.",
    )
    general_education_area: str | None = Field(
        default=None,
        max_length=80,
        description="Optional general education area/dimension, e.g. 人文, 社會, 自然, 跨域.",
    )


class DiagnosisRequest(BaseModel):
    session_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
        description="Optional frontend session id used to export the generated diagnosis report.",
    )
    department_id: UUID
    current_semester: str = Field(
        min_length=2,
        max_length=40,
        description="Current academic stage, e.g. 大三上.",
    )
    completed_courses: list[CompletedCourseInput] = Field(
        min_length=1,
        max_length=240,
        description="Transcript-like completed course list.",
    )


class CreditBucketProgress(BaseModel):
    bucket: Literal[
        "total",
        "major_required",
        "major_elective",
        "general_education",
        "free_elective",
    ]
    label: str
    required_credits: Decimal
    earned_credits: Decimal
    remaining_credits: Decimal
    completion_rate: float


class GeneralEducationAreaProgress(BaseModel):
    area: str
    required_credits: Decimal
    earned_credits: Decimal
    is_satisfied: bool


class CreditDiagnosisStats(BaseModel):
    department_id: UUID
    department_name: str
    current_semester: str
    curriculum_year: int | None = None
    total_required_credits: Decimal
    total_earned_credits: Decimal
    overall_completion_rate: float
    passed_course_count: int
    ignored_course_count: int
    buckets: list[CreditBucketProgress]
    general_education_areas: list[GeneralEducationAreaProgress]
    missing_required_course_codes: list[str] = Field(default_factory=list)
    recommended_course_codes: list[str] = Field(default_factory=list)


class DiagnosisCareerResourceContext(BaseModel):
    title: str
    resource_type: str
    professor_name: str | None = None
    organization_name: str | None = None
    summary: str | None = None
    requirements: str | None = None
    official_url: str | None = None
    tags: list[str] = Field(default_factory=list)


class DiagnosisResponse(BaseModel):
    diagnosis_id: UUID | None = None
    session_id: str | None = None
    credit_statistics: CreditDiagnosisStats
    career_context: list[DiagnosisCareerResourceContext]
    report_markdown: str


class CareerResourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    department_id: UUID
    department: DepartmentSummary | None = None
    resource_type: CareerResourceType
    title: str
    organization_name: str | None = None
    professor_name: str | None = None
    location: str | None = None
    summary: str | None = None
    requirements: str | None = None
    application_timeline: str | None = None
    official_url: str | None = None
    source_url: str | None = None
    tags: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class ActivityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    activity_type: ActivityType
    title: str
    organizer_name: str | None = None
    description: str | None = None
    location: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    registration_url: str | None = None
    official_url: str | None = None
    cover_image_url: str | None = None
    tags: list[str] = Field(default_factory=list)
    is_official: bool
    created_at: datetime
    updated_at: datetime


class LifeResourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    resource_type: LifeResourceType
    name: str
    area: str | None = None
    address: str | None = None
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    description: str | None = None
    price_min: int | None = None
    price_max: int | None = None
    rating: Decimal | None = None
    contact_info: dict
    external_url: str | None = None
    tags: list[str] = Field(default_factory=list)
    is_verified: bool
    created_at: datetime
    updated_at: datetime


class LifeReviewCreate(BaseModel):
    life_resource_id: UUID | None = Field(
        default=None,
        description="Optional related life resource ID, if the review is tied to an existing place/listing.",
    )
    review_type: LifeReviewType = Field(
        description="Review category such as rental warning or protein meal-prep recommendation.",
    )
    title: str = Field(min_length=2, max_length=160, description="Short title displayed on review cards.")
    content: str = Field(min_length=5, max_length=4000, description="Detailed student review content.")
    location_name: str | None = Field(default=None, max_length=180, description="Place, landlord, store, or listing name.")
    area: str | None = Field(default=None, max_length=120, description="Area around NCKU, e.g. 東寧路, 勝利校區.")
    address: str | None = Field(default=None, max_length=300, description="Optional address or approximate location.")
    rating: int | None = Field(default=None, ge=1, le=5, description="1-5 student rating; warning posts may leave it empty.")
    price_level: int | None = Field(default=None, ge=1, le=5, description="1-5 relative price level.")
    author_alias: str | None = Field(default=None, max_length=80, description="Public display alias.")
    tags: list[str] = Field(default_factory=list, max_length=12, description="Short tags for frontend chips.")
    metadata: dict = Field(default_factory=dict, description="Optional structured metadata for moderation or source tracking.")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "review_type": "rental_warning",
                    "title": "勝利校區附近套房隔音偏差",
                    "content": "晚上機車聲很明顯，建議看屋時確認窗戶與牆面隔音。",
                    "location_name": "勝利路某套房",
                    "area": "勝利校區",
                    "rating": 2,
                    "author_alias": "NCKU student",
                    "tags": ["租屋", "避雷", "隔音"],
                },
                {
                    "review_type": "protein_meal_prep",
                    "title": "東寧路雞胸肉與豆腐補給",
                    "content": "價格穩定，適合一週備餐。尖峰時段人較多。",
                    "location_name": "東寧路超市",
                    "area": "東寧路",
                    "rating": 4,
                    "price_level": 2,
                    "tags": ["高蛋白", "備餐", "食材"],
                },
            ]
        }
    )


class LifeReviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    life_resource_id: UUID | None = None
    review_type: LifeReviewType
    title: str
    content: str
    location_name: str | None = None
    area: str | None = None
    address: str | None = None
    rating: int | None = None
    price_level: int | None = None
    author_alias: str | None = None
    tags: list[str] = Field(default_factory=list)
    is_verified: bool
    is_approved: bool
    score: Decimal
    ai_spam_confidence: Decimal
    metadata_json: dict
    moderation_status: ReviewModerationStatus
    report_count: int
    created_at: datetime
    updated_at: datetime


class AdminReviewResponse(LifeReviewResponse):
    last_reported_at: datetime | None = None
    moderated_at: datetime | None = None
    moderated_by: str | None = None


class AdminFlaggedReviewsResponse(BaseModel):
    items: list[AdminReviewResponse]
    total: int
    limit: int
    offset: int


class AdminReviewStatusUpdate(BaseModel):
    status: ReviewModerationStatus


class AdminCourseSubmissionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    course_id: UUID
    submitted_by_user_id: str | None = None
    status: CourseSubmissionStatus
    proposed: dict
    confidence: Decimal | None = None
    upload_sha256: str | None = None
    reviewed_by_user_id: str | None = None
    reviewed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AdminCourseSubmissionsResponse(BaseModel):
    items: list[AdminCourseSubmissionResponse]
    total: int
    limit: int
    offset: int


class AdminCourseSubmissionDecision(BaseModel):
    approve: bool


class AdminDashboardStatsResponse(BaseModel):
    today_new_reviews: int
    pending_flagged_reviews: int
    popular_search_terms: list[str] = Field(default_factory=list)


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=120)
    user_query: str = Field(min_length=1, max_length=2000)
    department_filter: str = Field(min_length=1, max_length=120)
    category_filter: str | None = Field(default=None, max_length=80)


class ChatCitation(BaseModel):
    resource_id: UUID | None = None
    source_title: str | None = None
    source_url: str | None = None
    source_type: str
    category: str
    department: str | None = None
    chunk_index: int
    similarity: float
    excerpt: str
    metadata: dict = Field(default_factory=dict)


class ChatResponse(BaseModel):
    answer: str
    citations: list[ChatCitation]
    retrieved_count: int
    intent: str | None = None
    used_tools: list[str] = Field(default_factory=list)


class TrendingResourceResponse(BaseModel):
    resource_type: str
    resource_id: UUID
    title: str
    subtitle: str | None = None
    interaction_count: int
    href: str


class TrendingResponse(BaseModel):
    window_hours: int
    courses: list[TrendingResourceResponse] = Field(default_factory=list)
    labs: list[TrendingResourceResponse] = Field(default_factory=list)
    events: list[TrendingResourceResponse] = Field(default_factory=list)


class UserViewLogCreate(BaseModel):
    resource_type: Literal["course", "career", "event"]
    resource_id: UUID


class UserViewLogResponse(BaseModel):
    resource_type: Literal["course", "career", "event"]
    resource_id: UUID
    recorded: bool = True


class RecommendationItemResponse(BaseModel):
    resource_type: Literal["course", "career"]
    resource_id: UUID
    title: str
    subtitle: str | None = None
    department_id: UUID | None = None
    department_name: str | None = None
    href: str
    reason: str
    similarity_score: float
    adjusted_score: float
    tags: list[str] = Field(default_factory=list)


class RecommendationResponse(BaseModel):
    items: list[RecommendationItemResponse] = Field(default_factory=list)
    based_on_count: int
    viewed_resource_count: int
    profile_ready: bool


class SearchSuggestionResponse(BaseModel):
    resource_type: Literal["course", "instructor", "event"]
    resource_id: UUID
    label: str
    secondary_text: str | None = None
    href: str
