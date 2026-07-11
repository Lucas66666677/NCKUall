from __future__ import annotations

import pytest

from app import privacy


def test_redacts_ncku_student_ids_mobile_numbers_and_valid_national_ids() -> None:
    text = (
        "學號 E14012345，手機 0912-345-678 或 0987 654 321，"
        "國際格式 +886 912-345-678，身分證 A123456789，"
        "信箱 student@gs.ncku.edu.tw。"
    )

    sanitized = privacy.redact_direct_identifiers(text)

    assert "E14012345" not in sanitized
    assert "0912-345-678" not in sanitized
    assert "0987 654 321" not in sanitized
    assert "+886 912-345-678" not in sanitized
    assert "A123456789" not in sanitized
    assert "student@gs.ncku.edu.tw" not in sanitized
    assert sanitized.count(privacy.REDACTION) == 6


def test_invalid_id_like_code_and_campus_names_are_preserved() -> None:
    text = (
        "陳教授在光電系理學大樓授課，設備代碼 A123456788，"
        "課程名稱是工程數學。"
    )

    assert privacy.redact_direct_identifiers(text) == text


def test_nested_metadata_is_sanitized_without_changing_structure() -> None:
    value = {
        "contact": "0912345678",
        "tags": ["理學大樓", "F74123456"],
        "count": 3,
    }

    sanitized = privacy.sanitize_nested_text(value)

    assert sanitized == {
        "contact": privacy.REDACTION,
        "tags": ["理學大樓", privacy.REDACTION],
        "count": 3,
    }


async def test_long_text_uses_optional_llm_after_regex_redaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PII_LLM_REVIEW_ENABLED", "true")
    monkeypatch.setenv("PII_LLM_PROVIDER", "openai")
    original = (
        "王大明同學分享宿舍經驗，聯絡電話是 0912-345-678。"
        + "其餘住宿心得保持不變。" * 20
        + "陳教授與理學大樓都應保留。"
    )

    async def fake_review(text: str) -> str:
        assert "0912-345-678" not in text
        assert privacy.REDACTION in text
        return text.replace("王大明同學", "某同學")

    monkeypatch.setattr(
        privacy,
        "_review_student_names_with_openai",
        fake_review,
    )

    sanitized = await privacy.anonymize_review_text(original)

    assert "王大明" not in sanitized
    assert "某同學" in sanitized
    assert privacy.REDACTION in sanitized
    assert "陳教授" in sanitized
    assert "理學大樓" in sanitized


async def test_llm_failure_falls_back_to_regex_sanitized_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PII_LLM_REVIEW_ENABLED", "true")
    original = "心得內容。" * 50 + "電話 0912345678"

    async def failing_review(_text: str) -> str:
        raise TimeoutError

    monkeypatch.setattr(
        privacy,
        "_review_student_names_with_openai",
        failing_review,
    )

    sanitized = await privacy.anonymize_review_text(original)

    assert "0912345678" not in sanitized
    assert sanitized.endswith(f"電話 {privacy.REDACTION}")


async def test_llm_cannot_remove_existing_redaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PII_LLM_REVIEW_ENABLED", "true")
    original = "心得內容。" * 50 + "電話 0912345678"

    async def unsafe_review(text: str) -> str:
        return text.replace(privacy.REDACTION, "0912345678")

    monkeypatch.setattr(
        privacy,
        "_review_student_names_with_openai",
        unsafe_review,
    )

    sanitized = await privacy.anonymize_review_text(original)

    assert "0912345678" not in sanitized
    assert privacy.REDACTION in sanitized
