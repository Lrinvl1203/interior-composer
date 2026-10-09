"""Generate a stable consultation PDF for an Interior Composer snapshot."""
from __future__ import annotations

from pathlib import Path
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo
from xml.sax.saxutils import escape

from PIL import Image as PILImage, ImageOps
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


INK = colors.HexColor("#2C2620")
MUSTARD = colors.HexColor("#B87922")
OLIVE = colors.HexColor("#74745E")
PAPER = colors.HexColor("#FAF7F2")
LINE = colors.HexColor("#D9D3CA")


def korean_report_date(value: Any) -> str:
    """Use the same Korean calendar date in metadata and PDF body."""
    try:
        timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            return timestamp.date().isoformat()
        return timestamp.astimezone(ZoneInfo("Asia/Seoul")).date().isoformat()
    except (TypeError, ValueError):
        return str(value or "")[:10]


def _font() -> str:
    candidates = (
        Path(r"C:\Windows\Fonts\malgun.ttf"),
        Path(r"C:\Windows\Fonts\NanumGothic.ttf"),
        Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
    )
    for path in candidates:
        if path.is_file():
            name = "InteriorReportKorean"
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, str(path)))
            return name
    return "Helvetica"


def _fitted_image(path: Path, max_width: float, max_height: float) -> Image:
    with PILImage.open(path) as source:
        width, height = source.size
    ratio = min(max_width / max(width, 1), max_height / max(height, 1))
    return Image(str(path), width=width * ratio, height=height * ratio)


def _pdf_preview(source: Path, target: Path, max_edge: int = 1800) -> Path:
    """Create a compact JPEG for PDF embedding without changing the web snapshot."""

    with PILImage.open(source) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
        image.thumbnail((max_edge, max_edge), PILImage.Resampling.LANCZOS)
        image.save(target, "JPEG", quality=84, optimize=True, progressive=True)
    return target


def _text(value: object) -> str:
    return escape(str(value or ""))


def render_interior_report_pdf(report: dict[str, Any], report_dir: Path, path: Path) -> Path:
    """Render the immutable report snapshot as a polished A4 PDF."""

    path.parent.mkdir(parents=True, exist_ok=True)
    font = _font()
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "ReportTitle",
        parent=styles["Title"],
        fontName=font,
        fontSize=29,
        leading=37,
        textColor=INK,
        spaceAfter=5 * mm,
    )
    kicker = ParagraphStyle(
        "ReportKicker",
        parent=styles["BodyText"],
        fontName=font,
        fontSize=8.5,
        leading=12,
        textColor=MUSTARD,
        spaceAfter=3 * mm,
    )
    heading = ParagraphStyle(
        "ReportHeading",
        parent=styles["Heading2"],
        fontName=font,
        fontSize=19,
        leading=25,
        textColor=INK,
        spaceAfter=5 * mm,
    )
    body = ParagraphStyle(
        "ReportBody",
        parent=styles["BodyText"],
        fontName=font,
        fontSize=9.5,
        leading=16,
        textColor=INK,
    )
    muted = ParagraphStyle(
        "ReportMuted",
        parent=body,
        fontSize=8.5,
        leading=14,
        textColor=OLIVE,
    )
    small_right = ParagraphStyle(
        "ReportSmallRight",
        parent=muted,
        alignment=TA_RIGHT,
    )

    def decorate(canvas: Any, document: Any) -> None:
        canvas.saveState()
        canvas.setStrokeColor(LINE)
        canvas.line(18 * mm, 13 * mm, A4[0] - 18 * mm, 13 * mm)
        canvas.setFont(font, 7.5)
        canvas.setFillColor(OLIVE)
        canvas.drawString(18 * mm, 8.5 * mm, "MORROW AI · INTERIOR CONSULTATION REPORT")
        canvas.drawRightString(A4[0] - 18 * mm, 8.5 * mm, f"{document.page}")
        canvas.restoreState()

    document = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=17 * mm,
        bottomMargin=19 * mm,
        title=str(report.get("title") or "Interior consultation report"),
        author="Morrow AI",
    )

    before_preview = _pdf_preview(report_dir / "before.png", report_dir / ".before-pdf-preview.jpg")
    after_preview = _pdf_preview(report_dir / "after.png", report_dir / ".after-pdf-preview.jpg")
    story: list[Any] = [
        Paragraph("BEFORE / AFTER CONSULTATION", kicker),
        Paragraph(_text(report.get("title") or "인테리어 상담 보고서"), title),
        Paragraph(
            f"{_text(report.get('space_type') or '공간 미지정')} · VERSION {int(report.get('version') or 1):02d}",
            body,
        ),
        Spacer(1, 3 * mm),
        Paragraph(
            f"발행일 {_text(korean_report_date(report.get('created_at')))} · 생성 모델 {_text(str(report.get('ai_mode') or '').title())}",
            muted,
        ),
        Spacer(1, 9 * mm),
    ]

    comparison = Table(
        [[_fitted_image(before_preview, 82 * mm, 86 * mm), _fitted_image(after_preview, 82 * mm, 86 * mm)],
         [Paragraph("BEFORE · 입력 구도", muted), Paragraph("AFTER · 부분 합성", small_right)]],
        colWidths=[85 * mm, 85 * mm],
    )
    comparison.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (0, 0), "LEFT"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("BOX", (0, 0), (-1, 0), 0.6, LINE),
        ("INNERGRID", (0, 0), (-1, 0), 0.6, LINE),
        ("BACKGROUND", (0, 0), (-1, 0), PAPER),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, 0), 4),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
    ]))
    story.extend([comparison, Spacer(1, 8 * mm)])

    summary = report.get("consultation") if isinstance(report.get("consultation"), dict) else {}
    story.extend([
        Paragraph("DESIGN SUMMARY", kicker),
        Paragraph(_text(summary.get("summary") or "선택된 항목을 원본 구도에 맞춰 적용했습니다."), body),
    ])
    request = str(report.get("request") or "").strip()
    if request:
        story.extend([Spacer(1, 4 * mm), Paragraph(f"고객 요청 · {_text(request)}", muted)])

    story.append(PageBreak())
    story.extend([Paragraph("01 · REQUESTED INVENTORY", kicker), Paragraph("요청 에셋과 마감재", heading)])

    table_style = TableStyle([
        ("FONT", (0, 0), (-1, -1), font, 8.5),
        ("BACKGROUND", (0, 0), (-1, 0), INK),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PAPER]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
    ])

    assets = report.get("assets") if isinstance(report.get("assets"), list) else []
    asset_rows: list[list[Any]] = [["에셋", "분류", "배치 방식", "요청 수량"]]
    for item in assets:
        if not isinstance(item, dict):
            continue
        asset_rows.append([
            _text(item.get("name") or item.get("asset_id") or "가구"),
            _text(item.get("category") or "furniture"),
            "사용자 지정" if item.get("placement") == "manual" else "AI 최적 배치",
            "1",
        ])
    if len(asset_rows) == 1:
        asset_rows.append(["선택 안 함", "-", "기존 가구 유지", "-"])
    asset_table = Table(asset_rows, colWidths=[66 * mm, 35 * mm, 45 * mm, 24 * mm], repeatRows=1)
    asset_table.setStyle(table_style)
    story.extend([asset_table, Spacer(1, 8 * mm)])

    finishes = report.get("finishes") if isinstance(report.get("finishes"), list) else []
    finish_rows: list[list[Any]] = [["마감재", "카테고리", "적용 면"]]
    for item in finishes:
        if not isinstance(item, dict):
            continue
        finish_rows.append([
            _text(item.get("name") or item.get("id") or "마감재"),
            _text(item.get("category_label") or item.get("category") or "마감"),
            _text(item.get("target_label") or item.get("target") or "AI 자동 판단"),
        ])
    if len(finish_rows) == 1:
        finish_rows.append(["선택 안 함", "-", "기존 마감 유지"])
    finish_table = Table(finish_rows, colWidths=[70 * mm, 45 * mm, 55 * mm], repeatRows=1)
    finish_table.setStyle(table_style)
    story.extend([finish_table, Spacer(1, 10 * mm)])

    story.extend([Paragraph("02 · CONSULTATION NOTES", kicker), Paragraph("배치와 적용 원칙", heading)])
    notes = summary.get("notes") if isinstance(summary.get("notes"), list) else []
    for index, note in enumerate(notes, 1):
        story.append(KeepTogether([
            Paragraph(f"{index:02d} · {_text(note)}", body),
            Spacer(1, 3.5 * mm),
        ]))

    story.extend([
        Spacer(1, 7 * mm),
        Paragraph("현장 적용 전 확인", kicker),
        Paragraph(
            "본 자료는 인테리어 상담을 위한 이미지 제안서입니다. 실제 색상과 재질은 샘플 확인이 필요하며, 치수·수량·시공 가능 여부는 현장 실측과 전문가 검토 후 확정해야 합니다.",
            muted,
        ),
    ])

    try:
        document.build(story, onFirstPage=decorate, onLaterPages=decorate)
    finally:
        before_preview.unlink(missing_ok=True)
        after_preview.unlink(missing_ok=True)
    return path
