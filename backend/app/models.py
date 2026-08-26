from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    event,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


EMBEDDING_DIMENSIONS = 1536


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for all PostgreSQL models."""


class TimestampMixin:
    """Shared audit columns."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class UUIDPrimaryKeyMixin:
    """Use UUID primary keys to keep records portable across services."""

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )


class DeveloperKey(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Hashed credential issued to an external NCKUall API consumer.

    Plaintext API keys are shown once by the provisioning command and are
    never persisted. Scopes use resource:action names such as courses:read.
    """

    __tablename__ = "developer_keys"
    __table_args__ = (
        CheckConstraint(
            "cardinality(scopes) > 0",
            name="ck_developer_keys_scopes_not_empty",
        ),
        Index(
            "ux_developer_keys_hashed_key",
            "hashed_key",
            unique=True,
        ),
        Index(
            "ix_developer_keys_active_expires",
            "is_active",
            "expires_at",
        ),
    )

    hashed_key: Mapped[str] = mapped_column(String(64), nullable=False)
    key_prefix: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    owner_name: Mapped[str] = mapped_column(String(160), nullable=False)
    owner_email: Mapped[Optional[str]] = mapped_column(String(254))
    scopes: Mapped[list[str]] = mapped_column(
        ARRAY(String(80)),
        nullable=False,
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        index=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=text("true"),
        nullable=False,
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    last_used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )


class User(TimestampMixin, Base):
    """
    Local privacy-preserving Supabase user profile for Karma scoring.

    The primary key is the Supabase `sub` claim when available. Only an email
    hash is retained; reviews remain publicly anonymous.
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    email_hash: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    karma_points: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )


class AuditLog(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Immutable security audit record for privileged or high-risk operations.

    Audit rows are append-only. Application-level event guards and database
    triggers both prevent updates/deletes so operators cannot rewrite history
    through normal ORM or SQL paths.
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_action_created", "action", "created_at"),
        Index("ix_audit_logs_operator_created", "operator_id", "created_at"),
        Index(
            "ix_audit_logs_target",
            "target_resource",
            "target_id",
            "created_at",
        ),
    )

    operator_id: Mapped[Optional[str]] = mapped_column(
        String(160),
        index=True,
    )
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    target_resource: Mapped[str] = mapped_column(String(120), nullable=False)
    target_id: Mapped[Optional[str]] = mapped_column(String(120))
    changes: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45))
    request_id: Mapped[Optional[str]] = mapped_column(String(80), index=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(500))


def _prevent_audit_log_mutation(
    _mapper: object,
    _connection: object,
    _target: AuditLog,
) -> None:
    raise RuntimeError("AuditLog records are immutable and cannot be modified.")


event.listen(AuditLog, "before_update", _prevent_audit_log_mutation)
event.listen(AuditLog, "before_delete", _prevent_audit_log_mutation)


class CourseDifficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    UNKNOWN = "unknown"


class CareerResourceType(str, Enum):
    EXCHANGE = "exchange"
    STUDY_ABROAD = "study_abroad"
    GRAD_SCHOOL = "grad_school"
    LAB_REVIEW = "lab_review"
    PRE_MASTER = "pre_master"
    TRANSFER_DEPARTMENT = "transfer_department"
    PROGRAM = "program"
    OTHER = "other"


class ActivityType(str, Enum):
    CLUB = "club"
    OFFICIAL_EVENT = "official_event"
    PARTY = "party"
    BIKE_FESTIVAL = "bike_festival"
    LECTURE = "lecture"
    COMPETITION = "competition"
    OTHER = "other"


class LifeResourceType(str, Enum):
    RENTAL = "rental"
    FOOD = "food"
    STUDY_SPACE = "study_space"
    TRANSPORTATION = "transportation"
    SERVICE = "service"
    OTHER = "other"


class LifeReviewType(str, Enum):
    RENTAL_WARNING = "rental_warning"
    RENTAL_RECOMMENDATION = "rental_recommendation"
    FOOD_RECOMMENDATION = "food_recommendation"
    PROTEIN_MEAL_PREP = "protein_meal_prep"
    OTHER = "other"


class ReviewModerationStatus(str, Enum):
    APPROVED = "APPROVED"
    HIDDEN = "HIDDEN"
    PENDING = "PENDING"


class Department(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    NCKU department/faculty master table.

    Course planning and career planning records must reference this table so
    department-specific differences can be filtered strictly and reliably.
    """

    __tablename__ = "departments"

    code: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    name_zh: Mapped[str] = mapped_column(String(120), nullable=False)
    name_en: Mapped[Optional[str]] = mapped_column(String(160))
    college: Mapped[Optional[str]] = mapped_column(String(120))
    website_url: Mapped[Optional[str]] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    courses: Mapped[list["Course"]] = relationship(
        back_populates="department",
        cascade="all, delete-orphan",
    )
    graduation_requirement: Mapped[Optional["GraduationRequirement"]] = relationship(
        back_populates="department",
        cascade="all, delete-orphan",
        uselist=False,
    )
    career_resources: Mapped[list["CareerResource"]] = relationship(
        back_populates="department",
        cascade="all, delete-orphan",
    )


class GraduationRequirement(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Department-specific graduation thresholds used by the diagnosis engine.

    Flexible JSON fields preserve each department's local rules, while the
    numeric columns keep common progress metrics queryable and auditable.
    """

    __tablename__ = "graduation_requirements"
    __table_args__ = (
        UniqueConstraint(
            "department_id",
            "curriculum_year",
            name="uq_graduation_requirements_department_year",
        ),
        Index(
            "ix_graduation_requirements_department_active",
            "department_id",
            "is_active",
        ),
    )

    department_id: Mapped[UUID] = mapped_column(
        ForeignKey("departments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    curriculum_year: Mapped[Optional[int]] = mapped_column(Integer)
    total_required_credits: Mapped[Decimal] = mapped_column(
        Numeric(5, 1),
        nullable=False,
    )
    major_required_credits: Mapped[Decimal] = mapped_column(
        Numeric(5, 1),
        default=Decimal("0"),
        nullable=False,
    )
    major_elective_credits: Mapped[Decimal] = mapped_column(
        Numeric(5, 1),
        default=Decimal("0"),
        nullable=False,
    )
    general_education_credits: Mapped[Decimal] = mapped_column(
        Numeric(5, 1),
        default=Decimal("0"),
        nullable=False,
    )
    general_education_areas: Mapped[list | dict] = mapped_column(
        JSONB,
        default=list,
        nullable=False,
        comment=(
            "Examples: ['人文', '社會', '自然', '跨域'] or "
            "{'人文': 2, '社會': 2, '自然': 2, '跨域': 2}"
        ),
    )
    rules_json: Mapped[dict] = mapped_column(
        JSONB,
        default=dict,
        nullable=False,
        comment=(
            "Optional mappings such as course_categories, "
            "required_course_codes, and general_education_courses."
        ),
    )
    source_url: Mapped[Optional[str]] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=text("true"),
        nullable=False,
    )

    # AI/RAG: embed department graduation-rule documents for future audits.
    embedding: Mapped[Optional[list[float]]] = mapped_column(
        Vector(EMBEDDING_DIMENSIONS)
    )

    department: Mapped["Department"] = relationship(
        back_populates="graduation_requirement"
    )


class Course(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Core course information for course planning.

    Every course belongs to one department, even if it is also available to
    students from other departments.
    """

    __tablename__ = "courses"
    __table_args__ = (
        UniqueConstraint("department_id", "course_code", name="uq_course_department_code"),
        Index("ix_courses_department_semester", "department_id", "academic_year", "semester"),
        Index(
            "ix_courses_title_zh_trgm",
            "title_zh",
            postgresql_using="gin",
            postgresql_ops={"title_zh": "gin_trgm_ops"},
        ),
        Index(
            "ix_courses_instructor_name_trgm",
            "instructor_name",
            postgresql_using="gin",
            postgresql_ops={"instructor_name": "gin_trgm_ops"},
        ),
    )

    department_id: Mapped[UUID] = mapped_column(
        ForeignKey("departments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    course_code: Mapped[str] = mapped_column(String(64), nullable=False)
    title_zh: Mapped[str] = mapped_column(String(200), nullable=False)
    title_en: Mapped[Optional[str]] = mapped_column(String(240))
    instructor_name: Mapped[Optional[str]] = mapped_column(String(120), index=True)
    academic_year: Mapped[Optional[int]] = mapped_column(Integer)
    semester: Mapped[Optional[int]] = mapped_column(Integer)
    credits: Mapped[Optional[Decimal]] = mapped_column(Numeric(3, 1))
    required_for_major: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        default=list,
        server_default=text("'{}'::varchar[]"),
        nullable=False,
    )
    syllabus_url: Mapped[Optional[str]] = mapped_column(String(500))
    description: Mapped[Optional[str]] = mapped_column(Text)
    difficulty: Mapped[CourseDifficulty] = mapped_column(
        SQLEnum(CourseDifficulty, name="course_difficulty"),
        default=CourseDifficulty.UNKNOWN,
        nullable=False,
    )

    # AI/RAG: embed searchable course description, syllabus summary, and tags.
    embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))

    department: Mapped["Department"] = relationship(back_populates="courses")
    grade_distributions: Mapped[list["CourseGradeDistribution"]] = relationship(
        back_populates="course",
        cascade="all, delete-orphan",
    )
    reviews: Mapped[list["CourseReview"]] = relationship(
        back_populates="course",
        cascade="all, delete-orphan",
    )


class CourseGradeDistribution(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Objective grade distribution data for a course offering."""

    __tablename__ = "course_grade_distributions"
    __table_args__ = (
        UniqueConstraint("course_id", "academic_year", "semester", name="uq_course_grade_term"),
    )

    course_id: Mapped[UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    academic_year: Mapped[int] = mapped_column(Integer, nullable=False)
    semester: Mapped[int] = mapped_column(Integer, nullable=False)
    enrollment_count: Mapped[Optional[int]] = mapped_column(Integer)
    avg_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    median_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    pass_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4))
    grade_buckets: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        comment='Example: {"A+": 12, "A": 18, "B+": 20, "F": 3}',
    )
    source_url: Mapped[Optional[str]] = mapped_column(String(500))

    course: Mapped["Course"] = relationship(back_populates="grade_distributions")


class CourseReview(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Student course review, separated from objective grade data."""

    __tablename__ = "course_reviews"
    __table_args__ = (
        Index("ix_course_reviews_course_rating", "course_id", "overall_rating"),
    )

    course_id: Mapped[UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    reviewer_department_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL"),
        index=True,
    )
    author_user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    overall_rating: Mapped[Optional[int]] = mapped_column(Integer)
    workload_rating: Mapped[Optional[int]] = mapped_column(Integer)
    difficulty_rating: Mapped[Optional[int]] = mapped_column(Integer)
    grading_fairness_rating: Mapped[Optional[int]] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_approved: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=text("true"),
        nullable=False,
        index=True,
    )
    score: Mapped[Decimal] = mapped_column(
        Numeric(10, 4),
        default=0,
        server_default="0",
        nullable=False,
    )
    ai_spam_confidence: Mapped[Decimal] = mapped_column(
        Numeric(5, 4),
        default=0,
        server_default="0",
        nullable=False,
    )

    # AI/RAG: embed review content for semantic search and answer grounding.
    embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))

    course: Mapped["Course"] = relationship(back_populates="reviews")
    reviewer_department: Mapped[Optional["Department"]] = relationship()
    author: Mapped[Optional["User"]] = relationship()


class CareerResource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Department-specific career planning resource.

    This table intentionally requires department_id because exchange, graduate
    admission, labs, pre-master options, transfer rules, and programs differ
    heavily across departments.
    """

    __tablename__ = "career_resources"
    __table_args__ = (
        Index("ix_career_resources_department_type", "department_id", "resource_type"),
    )

    department_id: Mapped[UUID] = mapped_column(
        ForeignKey("departments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    resource_type: Mapped[CareerResourceType] = mapped_column(
        SQLEnum(CareerResourceType, name="career_resource_type"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    organization_name: Mapped[Optional[str]] = mapped_column(String(180))
    professor_name: Mapped[Optional[str]] = mapped_column(String(120))
    location: Mapped[Optional[str]] = mapped_column(String(180))
    summary: Mapped[Optional[str]] = mapped_column(Text)
    requirements: Mapped[Optional[str]] = mapped_column(Text)
    application_timeline: Mapped[Optional[str]] = mapped_column(Text)
    official_url: Mapped[Optional[str]] = mapped_column(String(500))
    source_url: Mapped[Optional[str]] = mapped_column(String(500))
    source_updated_at: Mapped[Optional[date]] = mapped_column(Date)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    # AI/RAG: embed summaries, requirements, timelines, and verified notes.
    embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))

    department: Mapped["Department"] = relationship(back_populates="career_resources")
    reviews: Mapped[list["CareerResourceReview"]] = relationship(
        back_populates="career_resource",
        cascade="all, delete-orphan",
    )


class CareerResourceReview(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Experience sharing or evaluation for a department-specific career resource."""

    __tablename__ = "career_resource_reviews"

    career_resource_id: Mapped[UUID] = mapped_column(
        ForeignKey("career_resources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    reviewer_department_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL"),
        index=True,
    )
    rating: Mapped[Optional[int]] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # AI/RAG: embed student experience for semantic retrieval.
    embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))

    career_resource: Mapped["CareerResource"] = relationship(back_populates="reviews")
    reviewer_department: Mapped[Optional["Department"]] = relationship()


class CareerDocumentChunk(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Normalized RAG chunk for irregular career-planning sources.

    Examples include exchange PDFs, pre-master program documents, department
    lab pages, and professor research-area pages.
    """

    __tablename__ = "career_document_chunks"
    __table_args__ = (
        UniqueConstraint("source_type", "source_url", "chunk_index", name="uq_career_chunk_source_index"),
        Index("ix_career_chunks_department_category", "department_id", "category"),
        Index("ix_career_chunks_source", "source_type", "source_url"),
        Index(
            "ix_career_document_chunks_weighted_fts",
            text(
                """(
                    setweight(
                        to_tsvector(
                            'simple'::regconfig,
                            COALESCE(source_title, '')
                        ),
                        'A'
                    )
                    ||
                    setweight(
                        to_tsvector(
                            'simple'::regconfig,
                            COALESCE(content, '')
                        ),
                        'B'
                    )
                    ||
                    setweight(
                        to_tsvector(
                            'simple'::regconfig,
                            COALESCE(CAST(metadata_json AS TEXT), '')
                        ),
                        'C'
                    )
                )"""
            ),
            postgresql_using="gin",
        ),
        Index(
            "ix_career_document_chunks_source_title_trgm",
            "source_title",
            postgresql_using="gin",
            postgresql_ops={"source_title": "gin_trgm_ops"},
        ),
        Index(
            "ix_career_document_chunks_content_trgm",
            "content",
            postgresql_using="gin",
            postgresql_ops={"content": "gin_trgm_ops"},
        ),
    )

    department_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL"),
        index=True,
    )
    source_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(700))
    source_title: Mapped[Optional[str]] = mapped_column(String(240))
    category: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    # AI/RAG: vector embedding for semantic retrieval over career-planning chunks.
    embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))

    department: Mapped[Optional["Department"]] = relationship()


class ChatHistory(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Persistent chat memory keyed by frontend-generated session_id.

    Roles follow LangChain-style naming: "human" for user messages and "ai"
    for assistant responses.
    """

    __tablename__ = "chat_history"
    __table_args__ = (
        Index("ix_chat_history_session_created", "session_id", "created_at"),
    )

    session_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(24), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class DiagnosisResult(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Persisted diagnosis report snapshot for authenticated PDF exports.

    The report stores no raw transcript file, IP address, or plaintext email.
    `owner_user_id` is the Supabase subject when available; otherwise callers
    may store a stable server-side hash.
    """

    __tablename__ = "diagnosis_results"
    __table_args__ = (
        Index("ix_diagnosis_results_owner_created", "owner_user_id", "created_at"),
        Index("ix_diagnosis_results_session_created", "session_id", "created_at"),
        Index("ix_diagnosis_results_department_created", "department_id", "created_at"),
    )

    session_id: Mapped[Optional[str]] = mapped_column(String(120), index=True)
    owner_user_id: Mapped[Optional[str]] = mapped_column(String(160), index=True)
    owner_email_hash: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    department_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL"),
        index=True,
    )
    current_semester: Mapped[str] = mapped_column(String(40), nullable=False)
    result_json: Mapped[dict] = mapped_column(JSONB, nullable=False)

    department: Mapped[Optional["Department"]] = relationship()


class SearchLog(UUIDPrimaryKeyMixin, Base):
    """
    Anonymous resource interaction used only for aggregate popularity.

    Deliberately contains no user, session, email, query text, or IP fields.
    A null resource_id represents a general search that cannot be attributed
    to one concrete resource and is excluded from resource rankings.
    """

    __tablename__ = "search_logs"
    __table_args__ = (
        Index(
            "ix_search_logs_type_created_resource",
            "resource_type",
            "created_at",
            "resource_id",
        ),
    )

    resource_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    resource_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class UserViewLog(UUIDPrimaryKeyMixin, Base):
    """
    Authenticated resource view history for personalized recommendations.

    Unlike aggregate SearchLog, this table intentionally stores a user id so
    the recommendation engine can build a private per-user interest vector.
    It still avoids IP address, user-agent, query text, and raw content.
    """

    __tablename__ = "user_view_logs"
    __table_args__ = (
        CheckConstraint(
            "resource_type IN ('course', 'career', 'event')",
            name="ck_user_view_logs_resource_type",
        ),
        Index("ix_user_view_logs_user_created", "user_id", "created_at"),
        Index(
            "ix_user_view_logs_user_resource",
            "user_id",
            "resource_type",
            "resource_id",
        ),
        Index(
            "ix_user_view_logs_resource_created",
            "resource_type",
            "resource_id",
            "created_at",
        ),
    )

    user_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class Activity(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Campus-wide activity, club, and event information."""

    __tablename__ = "activities"
    __table_args__ = (
        Index("ix_activities_type_start_at", "activity_type", "start_at"),
        Index(
            "ix_activities_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),
    )

    activity_type: Mapped[ActivityType] = mapped_column(
        SQLEnum(ActivityType, name="activity_type"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    organizer_name: Mapped[Optional[str]] = mapped_column(String(180), index=True)
    description: Mapped[Optional[str]] = mapped_column(Text)
    location: Mapped[Optional[str]] = mapped_column(String(240))
    start_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    registration_url: Mapped[Optional[str]] = mapped_column(String(500))
    official_url: Mapped[Optional[str]] = mapped_column(String(500))
    cover_image_url: Mapped[Optional[str]] = mapped_column(String(500))
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)
    is_official: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # AI/RAG: embed event descriptions and organizer notes.
    embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))


class LifeResource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Life assistant resource such as rentals, restaurants, and campus services."""

    __tablename__ = "life_resources"
    __table_args__ = (
        Index("ix_life_resources_type_area", "resource_type", "area"),
    )

    resource_type: Mapped[LifeResourceType] = mapped_column(
        SQLEnum(LifeResourceType, name="life_resource_type"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    area: Mapped[Optional[str]] = mapped_column(String(120), index=True)
    address: Mapped[Optional[str]] = mapped_column(String(300))
    latitude: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 7))
    longitude: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 7))
    description: Mapped[Optional[str]] = mapped_column(Text)
    price_min: Mapped[Optional[int]] = mapped_column(Integer)
    price_max: Mapped[Optional[int]] = mapped_column(Integer)
    rating: Mapped[Optional[Decimal]] = mapped_column(Numeric(3, 2))
    contact_info: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    external_url: Mapped[Optional[str]] = mapped_column(String(500))
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # AI/RAG: embed descriptions, reviews imported from trusted sources, and tags.
    embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))

    reviews: Mapped[list["LifeReview"]] = relationship(
        back_populates="life_resource",
        cascade="all, delete-orphan",
    )


class LifeReview(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Student-generated life assistant review.

    Used for rental warnings, food recommendations, and practical campus-life
    sharing such as protein meal-prep ingredient sources.
    """

    __tablename__ = "life_reviews"
    __table_args__ = (
        Index("ix_life_reviews_type_area", "review_type", "area"),
        Index("ix_life_reviews_resource_created", "life_resource_id", "created_at"),
        Index(
            "ix_life_reviews_moderation_reports",
            "moderation_status",
            "report_count",
        ),
    )

    life_resource_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("life_resources.id", ondelete="SET NULL"),
        index=True,
    )
    author_user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    review_type: Mapped[LifeReviewType] = mapped_column(
        SQLEnum(LifeReviewType, name="life_review_type"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    location_name: Mapped[Optional[str]] = mapped_column(String(180))
    area: Mapped[Optional[str]] = mapped_column(String(120), index=True)
    address: Mapped[Optional[str]] = mapped_column(String(300))
    rating: Mapped[Optional[int]] = mapped_column(Integer)
    price_level: Mapped[Optional[int]] = mapped_column(Integer)
    author_alias: Mapped[Optional[str]] = mapped_column(String(80))
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_approved: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=text("true"),
        nullable=False,
        index=True,
    )
    score: Mapped[Decimal] = mapped_column(
        Numeric(10, 4),
        default=0,
        server_default="0",
        nullable=False,
    )
    ai_spam_confidence: Mapped[Decimal] = mapped_column(
        Numeric(5, 4),
        default=0,
        server_default="0",
        nullable=False,
    )
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    moderation_status: Mapped[ReviewModerationStatus] = mapped_column(
        SQLEnum(ReviewModerationStatus, name="review_moderation_status"),
        default=ReviewModerationStatus.APPROVED,
        server_default=ReviewModerationStatus.APPROVED.value,
        nullable=False,
        index=True,
    )
    report_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )
    last_reported_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    moderated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    moderated_by: Mapped[Optional[str]] = mapped_column(String(120))

    # AI/RAG: embed student life reviews for future grounded recommendations.
    embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))

    life_resource: Mapped[Optional["LifeResource"]] = relationship(back_populates="reviews")
    author: Mapped[Optional["User"]] = relationship()


class LifeReviewVote(UUIDPrimaryKeyMixin, Base):
    """One verified NCKU upvote per life review."""

    __tablename__ = "life_review_votes"
    __table_args__ = (
        UniqueConstraint(
            "life_review_id",
            "voter_user_id",
            name="uq_life_review_votes_review_voter",
        ),
        Index("ix_life_review_votes_review_created", "life_review_id", "created_at"),
    )

    life_review_id: Mapped[UUID] = mapped_column(
        ForeignKey("life_reviews.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    voter_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    value: Mapped[int] = mapped_column(
        Integer,
        default=1,
        server_default="1",
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class LifeReviewFlag(UUIDPrimaryKeyMixin, Base):
    """One verified NCKU abuse report per life review.

    Tracked per-reporter (not just a bare counter) so that a single account
    cannot single-handedly reach the auto-hide threshold by calling the flag
    endpoint repeatedly.
    """

    __tablename__ = "life_review_flags"
    __table_args__ = (
        UniqueConstraint(
            "life_review_id",
            "reporter_user_id",
            name="uq_life_review_flags_review_reporter",
        ),
        Index("ix_life_review_flags_review_created", "life_review_id", "created_at"),
    )

    life_review_id: Mapped[UUID] = mapped_column(
        ForeignKey("life_reviews.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    reporter_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
