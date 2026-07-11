from __future__ import annotations

import asyncio
import html
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from io import BytesIO
from typing import TYPE_CHECKING, Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.pdfmetrics import registerFont
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

if TYPE_CHECKING:
    from app.schemas import (
        CreditBucketProgress,
        DiagnosisCareerResourceContext,
        DiagnosisResponse,
    )


FONT_NAME = "STSong-Light"
registerFont(UnicodeCIDFont(FONT_NAME))


@dataclass(frozen=True)
class DiagnosisPdfPayload:
    diagnosis: "DiagnosisResponse"
    watermark_text: str
    generated_at: datetime


def _percent(value: float) -> str:
    return f"{round(value * 100)}%"


def _decimal_text(value: Decimal | int | float | str | None) -> str:
    if value is None:
        return "-"
    decimal_value = Decimal(str(value))
    if decimal_value == decimal_value.to_integral():
        return str(decimal_value.quantize(Decimal("1")))
    return str(decimal_value.normalize())


def _paragraph(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(text, style)


def _escape_inline_markdown(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
    escaped = re.sub(r"`([^`]+?)`", r"<font color='#0f766e'>\1</font>", escaped)
    return escaped


def markdown_to_flowables(
    markdown: str,
    styles: dict[str, ParagraphStyle],
) -> list[Any]:
    flowables: list[Any] = []
    bullet_buffer: list[str] = []

    def flush_bullets() -> None:
        nonlocal bullet_buffer
        for bullet in bullet_buffer:
            flowables.append(
                _paragraph(
                    f"• {_escape_inline_markdown(bullet)}",
                    styles["Bullet"],
                )
            )
        bullet_buffer = []

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line:
            flush_bullets()
            flowables.append(Spacer(1, 0.16 * cm))
            continue

        heading_match = re.match(r"^(#{1,3})\s+(.+)$", line)
        if heading_match:
            flush_bullets()
            level = len(heading_match.group(1))
            style_name = "H1" if level == 1 else "H2" if level == 2 else "H3"
            flowables.append(
                _paragraph(
                    _escape_inline_markdown(heading_match.group(2)),
                    styles[style_name],
                )
            )
            continue

        bullet_match = re.match(r"^[-*]\s+(.+)$", line)
        if bullet_match:
            bullet_buffer.append(bullet_match.group(1))
            continue

        numbered_match = re.match(r"^(\d+)[.)]\s+(.+)$", line)
        if numbered_match:
            flush_bullets()
            flowables.append(
                _paragraph(
                    f"{numbered_match.group(1)}. "
                    f"{_escape_inline_markdown(numbered_match.group(2))}",
                    styles["Bullet"],
                )
            )
            continue

        flush_bullets()
        flowables.append(
            _paragraph(
                _escape_inline_markdown(line),
                styles["Body"],
            )
        )

    flush_bullets()
    return flowables


def build_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "CoverTitle": ParagraphStyle(
            "CoverTitle",
            parent=base["Title"],
            fontName=FONT_NAME,
            fontSize=28,
            leading=36,
            textColor=colors.HexColor("#0f766e"),
            alignment=TA_CENTER,
            spaceAfter=16,
        ),
        "CoverSubtitle": ParagraphStyle(
            "CoverSubtitle",
            parent=base["BodyText"],
            fontName=FONT_NAME,
            fontSize=13,
            leading=22,
            textColor=colors.HexColor("#334155"),
            alignment=TA_CENTER,
            spaceAfter=12,
        ),
        "H1": ParagraphStyle(
            "H1",
            parent=base["Heading1"],
            fontName=FONT_NAME,
            fontSize=18,
            leading=24,
            textColor=colors.HexColor("#0f766e"),
            spaceBefore=14,
            spaceAfter=8,
        ),
        "H2": ParagraphStyle(
            "H2",
            parent=base["Heading2"],
            fontName=FONT_NAME,
            fontSize=14,
            leading=20,
            textColor=colors.HexColor("#155e75"),
            spaceBefore=12,
            spaceAfter=6,
        ),
        "H3": ParagraphStyle(
            "H3",
            parent=base["Heading3"],
            fontName=FONT_NAME,
            fontSize=12,
            leading=18,
            textColor=colors.HexColor("#1e293b"),
            spaceBefore=8,
            spaceAfter=4,
        ),
        "Body": ParagraphStyle(
            "Body",
            parent=base["BodyText"],
            fontName=FONT_NAME,
            fontSize=10.5,
            leading=17,
            textColor=colors.HexColor("#1f2937"),
            alignment=TA_LEFT,
            spaceAfter=5,
        ),
        "Small": ParagraphStyle(
            "Small",
            parent=base["BodyText"],
            fontName=FONT_NAME,
            fontSize=8.5,
            leading=13,
            textColor=colors.HexColor("#64748b"),
        ),
        "Bullet": ParagraphStyle(
            "Bullet",
            parent=base["BodyText"],
            fontName=FONT_NAME,
            fontSize=10,
            leading=16,
            leftIndent=12,
            firstLineIndent=-8,
            textColor=colors.HexColor("#334155"),
            spaceAfter=3,
        ),
        "TableCell": ParagraphStyle(
            "TableCell",
            parent=base["BodyText"],
            fontName=FONT_NAME,
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#1f2937"),
        ),
        "TableHeader": ParagraphStyle(
            "TableHeader",
            parent=base["BodyText"],
            fontName=FONT_NAME,
            fontSize=9,
            leading=12,
            textColor=colors.white,
            alignment=TA_CENTER,
        ),
    }


def _draw_page_background(canvas: Any, doc: Any, watermark_text: str) -> None:
    width, height = A4
    canvas.saveState()
    try:
        canvas.setFillAlpha(0.08)
    except AttributeError:
        pass
    canvas.setFont(FONT_NAME, 18)
    canvas.setFillColor(colors.HexColor("#94a3b8"))
    canvas.translate(width / 2, height / 2)
    canvas.rotate(45)
    for y in range(-620, 700, 95):
        canvas.drawCentredString(0, y, watermark_text)
    canvas.restoreState()

    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#ccfbf1"))
    canvas.setLineWidth(1.2)
    canvas.line(1.6 * cm, height - 1.35 * cm, width - 1.6 * cm, height - 1.35 * cm)
    canvas.setFont(FONT_NAME, 8)
    canvas.setFillColor(colors.HexColor("#64748b"))
    canvas.drawString(1.6 * cm, 1.0 * cm, "NCKUall 成大資源整合平台")
    canvas.drawRightString(
        width - 1.6 * cm,
        1.0 * cm,
        f"Page {doc.page}",
    )
    canvas.restoreState()


def _cover_table(
    diagnosis: DiagnosisResponse,
    generated_at: datetime,
    styles: dict[str, ParagraphStyle],
) -> list[Any]:
    stats = diagnosis.credit_statistics
    return [
        Spacer(1, 2.1 * cm),
        _paragraph("NCKUall", styles["CoverTitle"]),
        _paragraph("成大生涯全方位診斷報告", styles["CoverTitle"]),
        _paragraph(
            "以畢業學分規則、課程資料與生涯資源生成的個人化摘要",
            styles["CoverSubtitle"],
        ),
        Spacer(1, 0.8 * cm),
        Table(
            [
                ["科系", stats.department_name],
                ["目前學期", stats.current_semester],
                ["總學分完成率", _percent(stats.overall_completion_rate)],
                ["已採計學分", _decimal_text(stats.total_earned_credits)],
                ["產生時間", generated_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")],
            ],
            colWidths=[4.2 * cm, 9.6 * cm],
            style=[
                ("FONTNAME", (0, 0), (-1, -1), FONT_NAME),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#0f766e")),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.white),
                ("BACKGROUND", (1, 0), (1, -1), colors.HexColor("#f8fafc")),
                ("TEXTCOLOR", (1, 0), (1, -1), colors.HexColor("#0f172a")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#99f6e4")),
                ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#d1fae5")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 9),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
            ],
        ),
        Spacer(1, 1.0 * cm),
        _paragraph(
            "本報告僅作為選課與生涯規劃輔助。正式畢業資格、推甄規定與交換申請門檻，仍應以成大及各系所公告為準。",
            styles["Small"],
        ),
        PageBreak(),
    ]


def _credit_table(
    buckets: list["CreditBucketProgress"],
    styles: dict[str, ParagraphStyle],
) -> Table:
    data: list[list[Any]] = [
        [
            _paragraph("項目", styles["TableHeader"]),
            _paragraph("需求", styles["TableHeader"]),
            _paragraph("已修", styles["TableHeader"]),
            _paragraph("尚缺", styles["TableHeader"]),
            _paragraph("完成率", styles["TableHeader"]),
        ]
    ]
    for bucket in buckets:
        data.append(
            [
                _paragraph(bucket.label, styles["TableCell"]),
                _paragraph(_decimal_text(bucket.required_credits), styles["TableCell"]),
                _paragraph(_decimal_text(bucket.earned_credits), styles["TableCell"]),
                _paragraph(_decimal_text(bucket.remaining_credits), styles["TableCell"]),
                _paragraph(_percent(bucket.completion_rate), styles["TableCell"]),
            ]
        )

    return Table(
        data,
        colWidths=[5.0 * cm, 2.3 * cm, 2.3 * cm, 2.3 * cm, 2.5 * cm],
        repeatRows=1,
        style=[
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f766e")),
            ("BACKGROUND", (0, 1), (-1, -1), colors.white),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#99f6e4")),
            ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#dbeafe")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ],
    )


def _career_context_cards(
    items: list["DiagnosisCareerResourceContext"],
    styles: dict[str, ParagraphStyle],
) -> list[Any]:
    flowables: list[Any] = []
    for item in items[:8]:
        subtitle = " / ".join(
            value
            for value in (
                item.resource_type,
                item.professor_name,
                item.organization_name,
            )
            if value
        )
        rows = [
            [_paragraph(html.escape(item.title), styles["H3"])],
            [_paragraph(html.escape(subtitle or "職涯資源"), styles["Small"])],
        ]
        if item.summary:
            rows.append([_paragraph(html.escape(item.summary[:260]), styles["Body"])])
        if item.official_url:
            rows.append([_paragraph(f"來源：{html.escape(item.official_url)}", styles["Small"])])
        flowables.append(
            KeepTogether(
                [
                    Table(
                        rows,
                        colWidths=[15.0 * cm],
                        style=[
                            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
                            ("BOX", (0, 0), (-1, -1), 0.45, colors.HexColor("#cbd5e1")),
                            ("LEFTPADDING", (0, 0), (-1, -1), 10),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                            ("TOPPADDING", (0, 0), (-1, -1), 6),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                        ],
                    ),
                    Spacer(1, 0.22 * cm),
                ]
            )
        )
    return flowables


def _build_story(payload: DiagnosisPdfPayload) -> list[Any]:
    styles = build_styles()
    diagnosis = payload.diagnosis
    stats = diagnosis.credit_statistics
    story: list[Any] = []
    story.extend(_cover_table(diagnosis, payload.generated_at, styles))

    story.append(_paragraph("學分達成率總覽", styles["H1"]))
    story.append(_credit_table(stats.buckets, styles))
    story.append(Spacer(1, 0.4 * cm))

    if stats.general_education_areas:
        story.append(_paragraph("通識向度檢查", styles["H2"]))
        ge_rows: list[list[Any]] = [
            [
                _paragraph("向度", styles["TableHeader"]),
                _paragraph("需求", styles["TableHeader"]),
                _paragraph("已修", styles["TableHeader"]),
                _paragraph("狀態", styles["TableHeader"]),
            ]
        ]
        for area in stats.general_education_areas:
            ge_rows.append(
                [
                    _paragraph(html.escape(area.area), styles["TableCell"]),
                    _paragraph(_decimal_text(area.required_credits), styles["TableCell"]),
                    _paragraph(_decimal_text(area.earned_credits), styles["TableCell"]),
                    _paragraph("已滿足" if area.is_satisfied else "待補齊", styles["TableCell"]),
                ]
            )
        story.append(
            Table(
                ge_rows,
                colWidths=[5.4 * cm, 3.0 * cm, 3.0 * cm, 3.0 * cm],
                repeatRows=1,
                style=[
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#155e75")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#bae6fd")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e0f2fe")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ],
            )
        )
        story.append(Spacer(1, 0.4 * cm))

    story.append(_paragraph("AI 建議報告", styles["H1"]))
    story.extend(markdown_to_flowables(diagnosis.report_markdown, styles))

    if diagnosis.career_context:
        story.append(PageBreak())
        story.append(_paragraph("引用的生涯資源摘要", styles["H1"]))
        story.extend(_career_context_cards(diagnosis.career_context, styles))

    story.append(Spacer(1, 0.4 * cm))
    story.append(
        _paragraph(
            "隱私聲明：本 PDF 於伺服器記憶體中即時生成，不會寫入任何暫存檔。",
            styles["Small"],
        )
    )
    return story


def _render_pdf_sync(payload: DiagnosisPdfPayload) -> bytes:
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=1.65 * cm,
        leftMargin=1.65 * cm,
        topMargin=1.75 * cm,
        bottomMargin=1.65 * cm,
        title="NCKUall Diagnosis Report",
        author="NCKUall",
        subject="NCKU graduation and career diagnosis",
    )
    story = _build_story(payload)

    def on_page(canvas: Any, doc: Any) -> None:
        _draw_page_background(canvas, doc, payload.watermark_text)

    document.build(story, onFirstPage=on_page, onLaterPages=on_page)
    buffer.seek(0)
    return buffer.getvalue()


async def render_diagnosis_pdf(payload: DiagnosisPdfPayload) -> bytes:
    """Render the PDF in a worker thread and return in-memory bytes."""

    return await asyncio.to_thread(_render_pdf_sync, payload)
