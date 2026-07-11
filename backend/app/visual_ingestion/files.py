from __future__ import annotations

import hashlib
from dataclasses import dataclass
from os import getenv
from pathlib import Path

import fitz
from fastapi import HTTPException, UploadFile, status


ALLOWED_TYPES = {
    "image/png": {".png"},
    "image/jpeg": {".jpg", ".jpeg"},
    "application/pdf": {".pdf"},
}
READ_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class ValidatedVisualUpload:
    content: bytes
    media_type: str
    safe_filename: str
    sha256: str
    size_bytes: int
    page_count: int


def configured_max_bytes() -> int:
    try:
        value = int(getenv("VISUAL_INGEST_MAX_BYTES", "15728640"))
    except ValueError:
        value = 15 * 1024 * 1024
    return max(1024, min(value, 50 * 1024 * 1024))


def configured_max_pdf_pages() -> int:
    try:
        value = int(getenv("VISUAL_INGEST_MAX_PDF_PAGES", "10"))
    except ValueError:
        value = 10
    return max(1, min(value, 50))


def invalid_upload(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={
            "code": "invalid_visual_upload",
            "message": detail,
        },
    )


def validate_magic(content: bytes, media_type: str) -> None:
    if media_type == "image/png":
        valid = content.startswith(b"\x89PNG\r\n\x1a\n")
    elif media_type == "image/jpeg":
        valid = (
            content.startswith(b"\xff\xd8\xff")
            and content.endswith(b"\xff\xd9")
        )
    else:
        valid = content.startswith(b"%PDF-")
    if not valid:
        raise invalid_upload("檔案內容與宣告格式不符")


def inspect_document(
    content: bytes,
    media_type: str,
    *,
    max_pdf_pages: int,
) -> int:
    file_type = {
        "image/png": "png",
        "image/jpeg": "jpeg",
        "application/pdf": "pdf",
    }[media_type]
    try:
        with fitz.open(stream=content, filetype=file_type) as document:
            if document.needs_pass:
                raise invalid_upload("不支援加密或設有密碼的 PDF")
            page_count = document.page_count
            if page_count < 1:
                raise invalid_upload("檔案沒有可辨識的頁面")
            if (
                media_type == "application/pdf"
                and page_count > max_pdf_pages
            ):
                raise invalid_upload(
                    f"PDF 頁數不可超過 {max_pdf_pages} 頁"
                )
            return page_count
    except HTTPException:
        raise
    except Exception as exc:
        raise invalid_upload("圖片或 PDF 已損毀，無法解析") from exc


async def read_and_validate_upload(
    upload: UploadFile,
    *,
    max_bytes: int | None = None,
    max_pdf_pages: int | None = None,
) -> ValidatedVisualUpload:
    media_type = (upload.content_type or "").split(";", 1)[0].lower()
    if media_type not in ALLOWED_TYPES:
        raise invalid_upload("僅接受 PNG、JPG 或 PDF")

    suffix = Path(upload.filename or "").suffix.lower()
    if suffix and suffix not in ALLOWED_TYPES[media_type]:
        raise invalid_upload("副檔名與 Content-Type 不一致")

    byte_limit = max_bytes or configured_max_bytes()
    page_limit = max_pdf_pages or configured_max_pdf_pages()
    content = bytearray()
    try:
        while True:
            chunk = await upload.read(READ_CHUNK_SIZE)
            if not chunk:
                break
            content.extend(chunk)
            if len(content) > byte_limit:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail={
                        "code": "visual_upload_too_large",
                        "message": (
                            f"檔案不可超過 {byte_limit // 1024 // 1024} MB"
                        ),
                    },
                )
    finally:
        await upload.close()

    if not content:
        raise invalid_upload("上傳檔案不可為空")
    raw_content = bytes(content)
    validate_magic(raw_content, media_type)
    page_count = inspect_document(
        raw_content,
        media_type,
        max_pdf_pages=page_limit,
    )
    extension = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "application/pdf": ".pdf",
    }[media_type]
    return ValidatedVisualUpload(
        content=raw_content,
        media_type=media_type,
        safe_filename=f"nckuall-visual-ingest{extension}",
        sha256=hashlib.sha256(raw_content).hexdigest(),
        size_bytes=len(raw_content),
        page_count=page_count,
    )
