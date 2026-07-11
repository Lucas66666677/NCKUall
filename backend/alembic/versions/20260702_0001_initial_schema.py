"""Create the initial application schema.

Revision ID: 20260702_0001
Revises:
Create Date: 2026-07-02
"""

from collections.abc import Sequence

from alembic import op
import pgvector.sqlalchemy
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260702_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def timestamp_columns() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def id_column() -> sa.Column:
    return sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False)


def upgrade() -> None:
    """Create extensions, enums, tables, constraints, and indexes."""

    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    op.create_table(
        "activities",
        sa.Column(
            "activity_type",
            sa.Enum(
                "CLUB",
                "OFFICIAL_EVENT",
                "PARTY",
                "BIKE_FESTIVAL",
                "LECTURE",
                "COMPETITION",
                "OTHER",
                name="activity_type",
            ),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("organizer_name", sa.String(length=180), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("location", sa.String(length=240), nullable=True),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("registration_url", sa.String(length=500), nullable=True),
        sa.Column("official_url", sa.String(length=500), nullable=True),
        sa.Column("cover_image_url", sa.String(length=500), nullable=True),
        sa.Column("tags", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("is_official", sa.Boolean(), nullable=False),
        sa.Column(
            "embedding",
            pgvector.sqlalchemy.Vector(dim=1536),
            nullable=True,
        ),
        id_column(),
        *timestamp_columns(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_activities_activity_type",
        "activities",
        ["activity_type"],
        unique=False,
    )
    op.create_index(
        "ix_activities_organizer_name",
        "activities",
        ["organizer_name"],
        unique=False,
    )
    op.create_index(
        "ix_activities_type_start_at",
        "activities",
        ["activity_type", "start_at"],
        unique=False,
    )

    op.create_table(
        "chat_history",
        sa.Column("session_id", sa.String(length=120), nullable=False),
        sa.Column("role", sa.String(length=24), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        id_column(),
        *timestamp_columns(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_chat_history_session_created",
        "chat_history",
        ["session_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_chat_history_session_id",
        "chat_history",
        ["session_id"],
        unique=False,
    )

    op.create_table(
        "departments",
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name_zh", sa.String(length=120), nullable=False),
        sa.Column("name_en", sa.String(length=160), nullable=True),
        sa.Column("college", sa.String(length=120), nullable=True),
        sa.Column("website_url", sa.String(length=500), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        id_column(),
        *timestamp_columns(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_departments_code", "departments", ["code"], unique=True)

    op.create_table(
        "life_resources",
        sa.Column(
            "resource_type",
            sa.Enum(
                "RENTAL",
                "FOOD",
                "STUDY_SPACE",
                "TRANSPORTATION",
                "SERVICE",
                "OTHER",
                name="life_resource_type",
            ),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("area", sa.String(length=120), nullable=True),
        sa.Column("address", sa.String(length=300), nullable=True),
        sa.Column("latitude", sa.Numeric(precision=10, scale=7), nullable=True),
        sa.Column("longitude", sa.Numeric(precision=10, scale=7), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("price_min", sa.Integer(), nullable=True),
        sa.Column("price_max", sa.Integer(), nullable=True),
        sa.Column("rating", sa.Numeric(precision=3, scale=2), nullable=True),
        sa.Column("contact_info", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("external_url", sa.String(length=500), nullable=True),
        sa.Column("tags", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("is_verified", sa.Boolean(), nullable=False),
        sa.Column(
            "embedding",
            pgvector.sqlalchemy.Vector(dim=1536),
            nullable=True,
        ),
        id_column(),
        *timestamp_columns(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_life_resources_area",
        "life_resources",
        ["area"],
        unique=False,
    )
    op.create_index(
        "ix_life_resources_resource_type",
        "life_resources",
        ["resource_type"],
        unique=False,
    )
    op.create_index(
        "ix_life_resources_type_area",
        "life_resources",
        ["resource_type", "area"],
        unique=False,
    )

    op.create_table(
        "career_document_chunks",
        sa.Column("department_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("source_url", sa.String(length=700), nullable=True),
        sa.Column("source_title", sa.String(length=240), nullable=True),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "embedding",
            pgvector.sqlalchemy.Vector(dim=1536),
            nullable=True,
        ),
        id_column(),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_type",
            "source_url",
            "chunk_index",
            name="uq_career_chunk_source_index",
        ),
    )
    op.create_index(
        "ix_career_chunks_department_category",
        "career_document_chunks",
        ["department_id", "category"],
        unique=False,
    )
    op.create_index(
        "ix_career_chunks_source",
        "career_document_chunks",
        ["source_type", "source_url"],
        unique=False,
    )
    op.create_index(
        "ix_career_document_chunks_category",
        "career_document_chunks",
        ["category"],
        unique=False,
    )
    op.create_index(
        "ix_career_document_chunks_department_id",
        "career_document_chunks",
        ["department_id"],
        unique=False,
    )
    op.create_index(
        "ix_career_document_chunks_source_type",
        "career_document_chunks",
        ["source_type"],
        unique=False,
    )

    op.create_table(
        "career_resources",
        sa.Column("department_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "resource_type",
            sa.Enum(
                "EXCHANGE",
                "STUDY_ABROAD",
                "GRAD_SCHOOL",
                "LAB_REVIEW",
                "PRE_MASTER",
                "TRANSFER_DEPARTMENT",
                "PROGRAM",
                "OTHER",
                name="career_resource_type",
            ),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("organization_name", sa.String(length=180), nullable=True),
        sa.Column("professor_name", sa.String(length=120), nullable=True),
        sa.Column("location", sa.String(length=180), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("requirements", sa.Text(), nullable=True),
        sa.Column("application_timeline", sa.Text(), nullable=True),
        sa.Column("official_url", sa.String(length=500), nullable=True),
        sa.Column("source_url", sa.String(length=500), nullable=True),
        sa.Column("source_updated_at", sa.Date(), nullable=True),
        sa.Column("tags", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "embedding",
            pgvector.sqlalchemy.Vector(dim=1536),
            nullable=True,
        ),
        id_column(),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_career_resources_department_id",
        "career_resources",
        ["department_id"],
        unique=False,
    )
    op.create_index(
        "ix_career_resources_department_type",
        "career_resources",
        ["department_id", "resource_type"],
        unique=False,
    )
    op.create_index(
        "ix_career_resources_resource_type",
        "career_resources",
        ["resource_type"],
        unique=False,
    )

    op.create_table(
        "courses",
        sa.Column("department_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("course_code", sa.String(length=64), nullable=False),
        sa.Column("title_zh", sa.String(length=200), nullable=False),
        sa.Column("title_en", sa.String(length=240), nullable=True),
        sa.Column("instructor_name", sa.String(length=120), nullable=True),
        sa.Column("academic_year", sa.Integer(), nullable=True),
        sa.Column("semester", sa.Integer(), nullable=True),
        sa.Column("credits", sa.Numeric(precision=3, scale=1), nullable=True),
        sa.Column("required_for_major", sa.Boolean(), nullable=False),
        sa.Column("syllabus_url", sa.String(length=500), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "difficulty",
            sa.Enum("EASY", "MEDIUM", "HARD", "UNKNOWN", name="course_difficulty"),
            nullable=False,
        ),
        sa.Column(
            "embedding",
            pgvector.sqlalchemy.Vector(dim=1536),
            nullable=True,
        ),
        id_column(),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "department_id",
            "course_code",
            name="uq_course_department_code",
        ),
    )
    op.create_index(
        "ix_courses_department_id",
        "courses",
        ["department_id"],
        unique=False,
    )
    op.create_index(
        "ix_courses_department_semester",
        "courses",
        ["department_id", "academic_year", "semester"],
        unique=False,
    )
    op.create_index(
        "ix_courses_instructor_name",
        "courses",
        ["instructor_name"],
        unique=False,
    )

    op.create_table(
        "life_reviews",
        sa.Column("life_resource_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "review_type",
            sa.Enum(
                "RENTAL_WARNING",
                "RENTAL_RECOMMENDATION",
                "FOOD_RECOMMENDATION",
                "PROTEIN_MEAL_PREP",
                "OTHER",
                name="life_review_type",
            ),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("location_name", sa.String(length=180), nullable=True),
        sa.Column("area", sa.String(length=120), nullable=True),
        sa.Column("address", sa.String(length=300), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("price_level", sa.Integer(), nullable=True),
        sa.Column("author_alias", sa.String(length=80), nullable=True),
        sa.Column("tags", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("is_verified", sa.Boolean(), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "embedding",
            pgvector.sqlalchemy.Vector(dim=1536),
            nullable=True,
        ),
        id_column(),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(
            ["life_resource_id"],
            ["life_resources.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_life_reviews_area", "life_reviews", ["area"], unique=False)
    op.create_index(
        "ix_life_reviews_life_resource_id",
        "life_reviews",
        ["life_resource_id"],
        unique=False,
    )
    op.create_index(
        "ix_life_reviews_resource_created",
        "life_reviews",
        ["life_resource_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_life_reviews_review_type",
        "life_reviews",
        ["review_type"],
        unique=False,
    )
    op.create_index(
        "ix_life_reviews_type_area",
        "life_reviews",
        ["review_type", "area"],
        unique=False,
    )

    op.create_table(
        "career_resource_reviews",
        sa.Column("career_resource_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reviewer_department_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("is_verified", sa.Boolean(), nullable=False),
        sa.Column(
            "embedding",
            pgvector.sqlalchemy.Vector(dim=1536),
            nullable=True,
        ),
        id_column(),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(
            ["career_resource_id"],
            ["career_resources.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_department_id"],
            ["departments.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_career_resource_reviews_career_resource_id",
        "career_resource_reviews",
        ["career_resource_id"],
        unique=False,
    )
    op.create_index(
        "ix_career_resource_reviews_reviewer_department_id",
        "career_resource_reviews",
        ["reviewer_department_id"],
        unique=False,
    )

    op.create_table(
        "course_grade_distributions",
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("academic_year", sa.Integer(), nullable=False),
        sa.Column("semester", sa.Integer(), nullable=False),
        sa.Column("enrollment_count", sa.Integer(), nullable=True),
        sa.Column("avg_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("median_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("pass_rate", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column(
            "grade_buckets",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment='Example: {"A+": 12, "A": 18, "B+": 20, "F": 3}',
        ),
        sa.Column("source_url", sa.String(length=500), nullable=True),
        id_column(),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["courses.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "course_id",
            "academic_year",
            "semester",
            name="uq_course_grade_term",
        ),
    )
    op.create_index(
        "ix_course_grade_distributions_course_id",
        "course_grade_distributions",
        ["course_id"],
        unique=False,
    )

    op.create_table(
        "course_reviews",
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reviewer_department_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("overall_rating", sa.Integer(), nullable=True),
        sa.Column("workload_rating", sa.Integer(), nullable=True),
        sa.Column("difficulty_rating", sa.Integer(), nullable=True),
        sa.Column("grading_fairness_rating", sa.Integer(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tags", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("is_verified", sa.Boolean(), nullable=False),
        sa.Column(
            "embedding",
            pgvector.sqlalchemy.Vector(dim=1536),
            nullable=True,
        ),
        id_column(),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["courses.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_department_id"],
            ["departments.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_course_reviews_course_id",
        "course_reviews",
        ["course_id"],
        unique=False,
    )
    op.create_index(
        "ix_course_reviews_course_rating",
        "course_reviews",
        ["course_id", "overall_rating"],
        unique=False,
    )
    op.create_index(
        "ix_course_reviews_reviewer_department_id",
        "course_reviews",
        ["reviewer_department_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the application schema while leaving pgvector installed."""

    op.drop_index(
        "ix_course_reviews_reviewer_department_id",
        table_name="course_reviews",
    )
    op.drop_index("ix_course_reviews_course_rating", table_name="course_reviews")
    op.drop_index("ix_course_reviews_course_id", table_name="course_reviews")
    op.drop_table("course_reviews")

    op.drop_index(
        "ix_course_grade_distributions_course_id",
        table_name="course_grade_distributions",
    )
    op.drop_table("course_grade_distributions")

    op.drop_index(
        "ix_career_resource_reviews_reviewer_department_id",
        table_name="career_resource_reviews",
    )
    op.drop_index(
        "ix_career_resource_reviews_career_resource_id",
        table_name="career_resource_reviews",
    )
    op.drop_table("career_resource_reviews")

    op.drop_index("ix_life_reviews_type_area", table_name="life_reviews")
    op.drop_index("ix_life_reviews_review_type", table_name="life_reviews")
    op.drop_index("ix_life_reviews_resource_created", table_name="life_reviews")
    op.drop_index("ix_life_reviews_life_resource_id", table_name="life_reviews")
    op.drop_index("ix_life_reviews_area", table_name="life_reviews")
    op.drop_table("life_reviews")

    op.drop_index("ix_courses_instructor_name", table_name="courses")
    op.drop_index("ix_courses_department_semester", table_name="courses")
    op.drop_index("ix_courses_department_id", table_name="courses")
    op.drop_table("courses")

    op.drop_index(
        "ix_career_resources_resource_type",
        table_name="career_resources",
    )
    op.drop_index(
        "ix_career_resources_department_type",
        table_name="career_resources",
    )
    op.drop_index(
        "ix_career_resources_department_id",
        table_name="career_resources",
    )
    op.drop_table("career_resources")

    op.drop_index(
        "ix_career_document_chunks_source_type",
        table_name="career_document_chunks",
    )
    op.drop_index(
        "ix_career_document_chunks_department_id",
        table_name="career_document_chunks",
    )
    op.drop_index(
        "ix_career_document_chunks_category",
        table_name="career_document_chunks",
    )
    op.drop_index(
        "ix_career_chunks_source",
        table_name="career_document_chunks",
    )
    op.drop_index(
        "ix_career_chunks_department_category",
        table_name="career_document_chunks",
    )
    op.drop_table("career_document_chunks")

    op.drop_index("ix_life_resources_type_area", table_name="life_resources")
    op.drop_index("ix_life_resources_resource_type", table_name="life_resources")
    op.drop_index("ix_life_resources_area", table_name="life_resources")
    op.drop_table("life_resources")

    op.drop_index("ix_departments_code", table_name="departments")
    op.drop_table("departments")

    op.drop_index("ix_chat_history_session_id", table_name="chat_history")
    op.drop_index("ix_chat_history_session_created", table_name="chat_history")
    op.drop_table("chat_history")

    op.drop_index("ix_activities_type_start_at", table_name="activities")
    op.drop_index("ix_activities_organizer_name", table_name="activities")
    op.drop_index("ix_activities_activity_type", table_name="activities")
    op.drop_table("activities")

    bind = op.get_bind()
    for enum_name in (
        "life_review_type",
        "course_difficulty",
        "career_resource_type",
        "life_resource_type",
        "activity_type",
    ):
        postgresql.ENUM(name=enum_name).drop(bind, checkfirst=True)
