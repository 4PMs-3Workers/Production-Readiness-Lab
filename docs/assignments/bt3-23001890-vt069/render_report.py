"""Render report.md to the assignment PDF. Run from any working directory."""

from __future__ import annotations

import re
from pathlib import Path

from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parents[3]
SOURCE = Path(__file__).with_name("report.md")
OUTPUT = ROOT / "output" / "pdf" / "BT3_23001890_VT-069.pdf"
FONT_DIR = Path(r"C:\Windows\Fonts")


def inline_markup(value: str) -> str:
    """Convert the small safe Markdown inline subset used in the source."""
    tokens: list[str] = []

    def hold(rendered: str) -> str:
        tokens.append(rendered)
        return f"\x00{len(tokens) - 1}\x00"

    pattern = re.compile(r"\[([^\]]+)\]\(([^)]+)\)|\*\*(.+?)\*\*|`([^`]+)`|\*(.+?)\*")

    def replace(match: re.Match[str]) -> str:
        label, target, bold, code, italic = match.groups()
        if target:
            safe = label
            href = target if target.startswith(("https://", "http://", "mailto:")) else None
            return hold(f'<link href="{href}">{safe}</link>' if href else safe)
        if bold:
            return hold(f"<b>{bold}</b>")
        if code:
            return hold(f'<font name="Arial">{code}</font>')
        return hold(f"<i>{italic}</i>")

    value = pattern.sub(replace, value)
    value = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    value = re.sub(r"\x00(\d+)\x00", lambda m: tokens[int(m.group(1))], value)
    return (
        value.replace("\u2011", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2192", "-&gt;")
    )


def register_fonts() -> None:
    pdfmetrics.registerFont(TTFont("Arial", str(FONT_DIR / "arial.ttf")))
    pdfmetrics.registerFont(TTFont("Arial-Bold", str(FONT_DIR / "arialbd.ttf")))
    pdfmetrics.registerFont(TTFont("Arial-Italic", str(FONT_DIR / "ariali.ttf")))
    pdfmetrics.registerFontFamily(
        "Arial", normal="Arial", bold="Arial-Bold", italic="Arial-Italic", boldItalic="Arial-Bold"
    )


def markdown_table(lines: list[str], styles: dict[str, ParagraphStyle], width: float) -> Table:
    rows = [[cell.strip() for cell in line.strip().strip("|").split("|")] for line in lines]
    if len(rows) < 2:
        raise ValueError("Malformed Markdown table")
    if len(rows[0]) == 5 and rows[0][0].strip().lower() == "ac/risk":
        return traceability_table(rows, styles, width)
    data = [[Paragraph(inline_markup(cell), styles["table"]) for cell in rows[0]]]
    data.extend(
        [[Paragraph(inline_markup(cell), styles["table"]) for cell in row] for row in rows[2:]]
    )
    count = len(data[0])
    if count == 6:
        fractions = [0.07, 0.13, 0.19, 0.19, 0.33, 0.08]
    elif count == 5:
        fractions = [0.17, 0.25, 0.21, 0.12, 0.24]
    else:
        fractions = [1 / count] * count
    table = Table(
        data,
        colWidths=[width * part for part in fractions],
        repeatRows=1,
        splitByRow=1,
        hAlign="LEFT",
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#16324F")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Arial-Bold"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#B9C7D4")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F0F5F8")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def traceability_table(
    rows: list[list[str]], styles: dict[str, ParagraphStyle], width: float
) -> Table:
    data = [
        [
            Paragraph("AC/risk", styles["table_header"]),
            Paragraph("Traceability", styles["table_header"]),
        ]
    ]
    for row in rows[2:]:
        details = "<br/>".join(
            f"<b>{label}:</b> {inline_markup(value)}"
            for label, value in zip(
                ("Test", "Implementation", "Commit", "Evidence/result"), row[1:], strict=True
            )
        )
        data.append(
            [
                Paragraph(inline_markup(row[0]), styles["trace"]),
                Paragraph(details, styles["trace"]),
            ]
        )
    table = Table(
        data,
        colWidths=[width * 0.28, width * 0.72],
        repeatRows=1,
        splitByRow=1,
        hAlign="LEFT",
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#16324F")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Arial-Bold"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#B9C7D4")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F0F5F8")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def make_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "TitleVN",
            parent=base["Title"],
            fontName="Arial-Bold",
            fontSize=19,
            leading=23,
            textColor=colors.HexColor("#16324F"),
            alignment=TA_LEFT,
            spaceAfter=8,
        ),
        "h1": ParagraphStyle(
            "H1VN",
            parent=base["Heading1"],
            fontName="Arial-Bold",
            fontSize=14,
            leading=17,
            textColor=colors.HexColor("#16324F"),
            spaceBefore=8,
            spaceAfter=5,
            keepWithNext=True,
        ),
        "h2": ParagraphStyle(
            "H2VN",
            parent=base["Heading2"],
            fontName="Arial-Bold",
            fontSize=11,
            leading=14,
            textColor=colors.HexColor("#146C74"),
            spaceBefore=6,
            spaceAfter=3,
            keepWithNext=True,
        ),
        "body": ParagraphStyle(
            "BodyVN",
            parent=base["BodyText"],
            fontName="Arial",
            fontSize=9.4,
            leading=12.2,
            alignment=TA_LEFT,
            spaceAfter=4,
            splitLongWords=1,
        ),
        "quote": ParagraphStyle(
            "QuoteVN",
            parent=base["BodyText"],
            fontName="Arial-Bold",
            fontSize=10,
            leading=14,
            textColor=colors.HexColor("#146C74"),
            leftIndent=8,
            rightIndent=8,
            borderColor=colors.HexColor("#146C74"),
            borderWidth=1,
            borderPadding=6,
            spaceBefore=10,
            spaceAfter=7,
        ),
        "table": ParagraphStyle(
            "TableVN",
            parent=base["BodyText"],
            fontName="Arial",
            fontSize=8,
            leading=10,
            splitLongWords=1,
        ),
        "table_header": ParagraphStyle(
            "TableHeaderVN",
            parent=base["BodyText"],
            fontName="Arial-Bold",
            fontSize=8.4,
            leading=10.5,
            textColor=colors.white,
        ),
        "trace": ParagraphStyle(
            "TraceVN",
            parent=base["BodyText"],
            fontName="Arial",
            fontSize=8.5,
            leading=11,
            splitLongWords=1,
        ),
        "bullet": ParagraphStyle(
            "BulletVN",
            parent=base["BodyText"],
            fontName="Arial",
            fontSize=9.2,
            leading=12,
            leftIndent=10,
            firstLineIndent=-7,
            spaceAfter=2,
        ),
    }


def build_story(text: str, styles: dict[str, ParagraphStyle], width: float) -> list[object]:
    story: list[object] = []
    lines = text.splitlines()
    paragraph: list[str] = []
    index = 0

    def flush() -> None:
        if paragraph:
            story.append(
                Paragraph(
                    inline_markup(" ".join(item.strip() for item in paragraph)), styles["body"]
                )
            )
            paragraph.clear()

    while index < len(lines):
        line = lines[index].rstrip()
        if line.startswith("<!-- APPENDIX:"):
            flush()
            story.append(PageBreak())
            index += 1
            continue
        if line.startswith("## Phụ lục C - Execution log excerpts") or line.startswith(
            "## Phụ lục C — Execution log excerpts"
        ):
            flush()
            story.append(PageBreak())
        if not line.strip():
            flush()
            index += 1
            continue
        if line.startswith("|"):
            flush()
            table_lines = []
            while index < len(lines) and lines[index].lstrip().startswith("|"):
                table_lines.append(lines[index])
                index += 1
            story.append(markdown_table(table_lines, styles, width))
            story.append(Spacer(1, 5))
            continue
        if line.startswith("### "):
            flush()
            story.append(Paragraph(inline_markup(line[4:]), styles["h2"]))
        elif line.startswith("## "):
            flush()
            story.append(Paragraph(inline_markup(line[3:]), styles["h1"]))
        elif line.startswith("# "):
            flush()
            story.append(Paragraph(inline_markup(line[2:]), styles["title"]))
        elif line.startswith("> "):
            flush()
            story.append(Paragraph(inline_markup(line[2:]), styles["quote"]))
        elif line.startswith("- "):
            flush()
            story.append(Paragraph("• " + inline_markup(line[2:]), styles["bullet"]))
        else:
            paragraph.append(line)
        index += 1
    flush()
    return story


def draw_page(canvas, document) -> None:
    canvas.saveState()
    page_width, page_height = A4
    canvas.setStrokeColor(colors.HexColor("#D4DEE6"))
    canvas.setLineWidth(0.5)
    canvas.line(15 * mm, page_height - 10 * mm, page_width - 15 * mm, page_height - 10 * mm)
    canvas.setFont("Arial", 7)
    canvas.setFillColor(colors.HexColor("#526779"))
    canvas.drawString(15 * mm, 7 * mm, "BT3 · 23001890 · VT-069")
    canvas.drawRightString(page_width - 15 * mm, 7 * mm, f"Trang {document.page}")
    canvas.restoreState()


def main() -> None:
    register_fonts()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    page_width, _ = A4
    doc = SimpleDocTemplate(
        str(OUTPUT),
        pagesize=A4,
        rightMargin=16 * mm,
        leftMargin=16 * mm,
        topMargin=17 * mm,
        bottomMargin=16 * mm,
        title="BT3 23001890 VT-069",
        author="Ngô Quang Hưng",
        subject="Production Readiness Lab - VT-069",
    )
    story = build_story(SOURCE.read_text(encoding="utf-8"), make_styles(), page_width - 32 * mm)
    doc.build(story, onFirstPage=draw_page, onLaterPages=draw_page)
    reader = PdfReader(str(OUTPUT))
    if len(reader.pages) == 0:
        raise RuntimeError("No PDF pages were generated")
    appendix_page = next(
        (
            i + 1
            for i, page in enumerate(reader.pages)
            if "Phụ lục A" in (page.extract_text() or "")
        ),
        None,
    )
    main_pages = (appendix_page - 1) if appendix_page else len(reader.pages)
    print(f"output={OUTPUT}")
    print(f"pages={len(reader.pages)} main_pages={main_pages} appendix_start={appendix_page}")
    if main_pages > 8:
        raise RuntimeError(f"Main content is {main_pages} pages; maximum is 8")


if __name__ == "__main__":
    main()
