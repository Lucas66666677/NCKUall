from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal
from urllib.parse import urlparse
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class VisualIngestType(str, Enum):
    EVENT = "event"
    COURSE = "course"


class ExtractionErrorCode(str, Enum):
    NONE = "none"
    UNREADABLE = "unreadable"
    WRONG_DOCUMENT_TYPE = "wrong_document_type"
    INSUFFICIENT_INFORMATION = "insufficient_information"
    AMBIGUOUS = "ambiguous"


class VisualExtractionBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    readable: bool
    confidence: float = Field(ge=0, le=1)
    error_code: ExtractionErrorCode
    error_message: str | None = Field(max_length=300)

    @model_validator(mode="after")
    def validate_status(self):
        if self.readable:
            if self.error_code != ExtractionErrorCode.NONE:
                raise ValueError(
                    "Readable extraction must use error_code=none."
                )
            if self.error_message is not None:
                raise ValueError(
                    "Readable extraction cannot include an error message."
                )
        elif self.error_code == ExtractionErrorCode.NONE:
            raise ValueError(
                "Unreadable extraction must include an error code."
            )
        return self


class EventVisualExtraction(VisualExtractionBase):
    event_name: str | None = Field(max_length=240)
    start_at: datetime | None
    end_at: datetime | None
    location: str | None = Field(max_length=240)
    organizer: str | None = Field(max_length=180)
    summary: str | None = Field(max_length=500)
    registration_url: str | None = Field(max_length=500)

    @field_validator("registration_url")
    @classmethod
    def validate_registration_url(
        cls,
        value: str | None,
    ) -> str | None:
        return validate_optional_http_url(value)

    @model_validator(mode="after")
    def validate_event_payload(self):
        if not self.readable:
            return self
        required_values = (
            self.event_name,
            self.start_at,
            self.location,
            self.organizer,
            self.summary,
        )
        if any(value is None for value in required_values):
            raise ValueError(
                "Readable event extraction is incomplete."
            )
        if self.start_at is not None and self.start_at.tzinfo is None:
            raise ValueError("Event start_at must include a timezone.")
        if self.end_at is not None:
            if self.end_at.tzinfo is None:
                raise ValueError("Event end_at must include a timezone.")
            if (
                self.start_at is not None
                and self.end_at < self.start_at
            ):
                raise ValueError("Event end_at precedes start_at.")
        return self


class CourseVisualExtraction(VisualExtractionBase):
    department_code: str | None = Field(max_length=32)
    department_name: str | None = Field(max_length=120)
    course_code: str | None = Field(max_length=64)
    title_zh: str | None = Field(max_length=200)
    title_en: str | None = Field(max_length=240)
    instructor_name: str | None = Field(max_length=120)
    academic_year: int | None = Field(ge=1, le=3000)
    semester: int | None = Field(ge=1, le=3)
    credits: float | None = Field(ge=0, le=30)
    required_for_major: bool | None
    description: str | None = Field(max_length=5000)
    syllabus_url: str | None = Field(max_length=500)

    @field_validator("syllabus_url")
    @classmethod
    def validate_syllabus_url(
        cls,
        value: str | None,
    ) -> str | None:
        return validate_optional_http_url(value)

    @model_validator(mode="after")
    def validate_course_payload(self):
        if not self.readable:
            return self
        if not self.department_code and not self.department_name:
            raise ValueError(
                "Readable course extraction needs a department."
            )
        if not self.course_code or not self.title_zh:
            raise ValueError(
                "Readable course extraction needs code and title."
            )
        return self


class VisualIngestResponse(BaseModel):
    ingest_type: VisualIngestType
    # "pending_review": a non-admin proposed an edit to an existing course, so
    # resource_id is the queued submission's id, not the course's.
    action: Literal["created", "updated", "pending_review"]
    resource_id: UUID
    title: str
    confidence: float
    tags: list[str]
    created_at: datetime
    updated_at: datetime
    extracted: EventVisualExtraction | CourseVisualExtraction


def validate_optional_http_url(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("URL must use http or https.")
    return value
