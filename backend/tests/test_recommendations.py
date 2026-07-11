from __future__ import annotations

from uuid import uuid4

from app.controllers.recommendations import (
    RecommendationCandidate,
    average_vectors,
    merge_candidates,
    score_candidate,
)


def test_average_vectors_ignores_malformed_dimensions() -> None:
    assert average_vectors([]) == []
    assert average_vectors([[1.0, 3.0], [3.0, 5.0], [10.0]]) == [2.0, 4.0]


def test_department_bonus_changes_adjusted_score() -> None:
    department_id = uuid4()

    same_department = score_candidate(
        similarity_score=0.8,
        department_id=department_id,
        preferred_department_id=department_id,
    )
    other_department = score_candidate(
        similarity_score=0.8,
        department_id=uuid4(),
        preferred_department_id=department_id,
    )

    assert same_department > other_department
    assert same_department == 0.92
    assert other_department == 0.8


def test_merge_candidates_excludes_viewed_and_ranks_mixed_resources() -> None:
    department_id = uuid4()
    viewed_course_id = uuid4()
    career_id = uuid4()
    course_id = uuid4()

    items = merge_candidates(
        [
            RecommendationCandidate(
                resource_type="course",
                resource_id=viewed_course_id,
                title="已看過的課",
                subtitle=None,
                department_id=department_id,
                department_name="光電科學與工程學系",
                href=f"/courses/{viewed_course_id}",
                similarity_score=0.99,
                adjusted_score=1.11,
                tags=[],
            ),
            RecommendationCandidate(
                resource_type="career",
                resource_id=career_id,
                title="半導體實驗室",
                subtitle="林教授",
                department_id=department_id,
                department_name="光電科學與工程學系",
                href="/careers?category=lab_project",
                similarity_score=0.86,
                adjusted_score=0.98,
                tags=["lab_project"],
            ),
            RecommendationCandidate(
                resource_type="course",
                resource_id=course_id,
                title="固態電子學",
                subtitle="王教授",
                department_id=uuid4(),
                department_name="電機工程學系",
                href=f"/courses/{course_id}",
                similarity_score=0.9,
                adjusted_score=0.9,
                tags=["半導體"],
            ),
        ],
        viewed_keys={("course", viewed_course_id)},
        preferred_department_id=department_id,
        limit=6,
    )

    assert [item.resource_id for item in items] == [career_id, course_id]
    assert items[0].resource_type == "career"
    assert "目前選取科系" in items[0].reason
