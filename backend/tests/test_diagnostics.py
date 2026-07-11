from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from app.controllers.diagnostics import (
    calculate_credit_statistics,
    is_passing_grade,
)
from app.models import Course, Department, GraduationRequirement
from app.schemas import CompletedCourseInput, DiagnosisRequest


def test_grade_parser_treats_failed_and_withdrawn_courses_as_not_passed() -> None:
    assert is_passing_grade("A+") is True
    assert is_passing_grade("85") is True
    assert is_passing_grade("通過") is True
    assert is_passing_grade("59") is False
    assert is_passing_grade("F") is False
    assert is_passing_grade("W") is False
    assert is_passing_grade("不及格") is False


def test_credit_diagnosis_calculates_buckets_and_ge_area_gaps() -> None:
    department_id = uuid4()
    department = Department(
        id=department_id,
        code="DPS",
        name_zh="光電科學與工程學系",
    )
    requirement = GraduationRequirement(
        department_id=department_id,
        curriculum_year=115,
        total_required_credits=Decimal("128"),
        major_required_credits=Decimal("6"),
        major_elective_credits=Decimal("6"),
        general_education_credits=Decimal("4"),
        general_education_areas={
            "人文": 2,
            "社會": 2,
        },
        rules_json={
            "required_course_codes": ["DPS1001", "DPS1002"],
            "general_education_courses": {
                "人文": ["GE101"],
                "社會": ["GE201"],
            },
        },
    )
    courses = [
        Course(
            department_id=department_id,
            course_code="DPS1001",
            title_zh="光電導論",
            credits=Decimal("3"),
            required_for_major=True,
        ),
        Course(
            department_id=department_id,
            course_code="DPS1002",
            title_zh="電磁學",
            credits=Decimal("3"),
            required_for_major=True,
        ),
        Course(
            department_id=department_id,
            course_code="DPS2001",
            title_zh="光電實驗",
            credits=Decimal("3"),
            required_for_major=False,
        ),
    ]
    payload = DiagnosisRequest(
        department_id=department_id,
        current_semester="大三上",
        completed_courses=[
            CompletedCourseInput(course_code="DPS1001", grade="A"),
            CompletedCourseInput(course_code="DPS1001", grade="A"),
            CompletedCourseInput(course_code="DPS1002", grade="F"),
            CompletedCourseInput(course_code="DPS2001", grade="B+"),
            CompletedCourseInput(course_code="GE101", grade="85", credits=Decimal("2")),
            CompletedCourseInput(
                course_code="FREE001",
                grade="P",
                credits=Decimal("2"),
                category="free_elective",
            ),
        ],
    )

    stats = calculate_credit_statistics(
        payload=payload,
        department=department,
        requirement=requirement,
        department_courses=courses,
    )

    buckets = {bucket.bucket: bucket for bucket in stats.buckets}
    assert stats.total_earned_credits == Decimal("10")
    assert stats.passed_course_count == 4
    assert stats.ignored_course_count == 2
    assert buckets["major_required"].earned_credits == Decimal("3")
    assert buckets["major_required"].remaining_credits == Decimal("3")
    assert buckets["major_elective"].earned_credits == Decimal("3")
    assert buckets["general_education"].earned_credits == Decimal("2")
    assert stats.missing_required_course_codes == ["DPS1002"]
    assert stats.recommended_course_codes[0] == "DPS1002"

    ge_areas = {area.area: area for area in stats.general_education_areas}
    assert ge_areas["人文"].is_satisfied is True
    assert ge_areas["社會"].is_satisfied is False


@pytest.mark.parametrize(
    ("grade", "expected"),
    [
        ("A-", True),
        ("60", True),
        ("NP", False),
        ("停修", False),
    ],
)
def test_grade_parser_common_edge_cases(grade: str, expected: bool) -> None:
    assert is_passing_grade(grade) is expected
