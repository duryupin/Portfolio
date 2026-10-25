# -*- coding: utf-8 -*-
"""Этап 6. PDF-отчёт по уже сверенной карточке.

Берёт reports/stage5/cards.json только если внутренняя сверка
reports/stage5/review.json прошла. Свои шифры не придумывает.
После записи отчёта снова вызывает сверку уже по файлу PDF.

Запуск из корня norm_check:
  .venv\\Scripts\\python.exe scripts\\build_report.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer

import review_conclusions as review

ROOT = Path(__file__).resolve().parents[1]
CARD_PATH = ROOT / "reports" / "stage5" / "cards.json"
CARD_REVIEW = ROOT / "reports" / "stage5" / "review.json"
OUT_DIR = ROOT / "reports" / "stage6"
PDF_PATH = OUT_DIR / "report.pdf"

NOT_CHECKED = (
    "Что не проверялось",
    "Это правило сравнивает название при шифре СП с названием в карточке того же шифра.",
    "Год редакции, геометрию листа, сечения и скорость воздуха это правило не проверяет.",
    "Шифр, которого нет в карточках, в замечания не входит.",
    "Слои чертежа к примитивам не привязаны, проверка по слоям не шла.",
)


def fonts() -> None:
    regular = Path(r"C:\Windows\Fonts\arial.ttf")
    bold = Path(r"C:\Windows\Fonts\arialbd.ttf")
    if not regular.is_file() or not bold.is_file():
        raise SystemExit("Нет Arial в C:\\Windows\\Fonts, отчёт не собран")
    pdfmetrics.registerFont(TTFont("Arial", str(regular)))
    pdfmetrics.registerFont(TTFont("Arial-Bold", str(bold)))


def styles() -> dict[str, ParagraphStyle]:
    return {
        "title": ParagraphStyle(
            "title", fontName="Arial-Bold", fontSize=16, leading=20, textColor="black", spaceAfter=6
        ),
        "h": ParagraphStyle(
            "h", fontName="Arial-Bold", fontSize=12, leading=15, spaceBefore=10, spaceAfter=4
        ),
        "body": ParagraphStyle(
            "body", fontName="Arial", fontSize=10, leading=13, alignment=TA_JUSTIFY, spaceAfter=4
        ),
        "small": ParagraphStyle(
            "small", fontName="Arial", fontSize=9, leading=12, alignment=TA_JUSTIFY, spaceAfter=6
        ),
    }


def draw_footer(canvas, _doc) -> None:
    canvas.saveState()
    canvas.setFont("Arial", 8)
    canvas.drawString(18 * mm, 10 * mm, review.DISCLAIMER)
    canvas.restoreState()


def paragraph(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(text).replace("\n", "<br/>"), style)


def build(card: dict) -> None:
    fonts()
    look = styles()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    story = [
        paragraph("Нормоконтроль", look["title"]),
        paragraph(review.DISCLAIMER + " Проверено одно правило: название при шифре СП.", look["body"]),
        paragraph("Замечание", look["h"]),
        paragraph(
            f"Страница {card['page']}, лист «{card['sheet_name']}», в штампе номер {card['sheet_number']}.",
            look["body"],
        ),
        paragraph(
            f"У шифра {card['code_in_project']} написано «{card['title_in_project']}». "
            f"Название этого шифра в карточке: «{card['title_in_catalog']}». "
            "В названии другое слово.",
            look["body"],
        ),
    ]
    image_path = ROOT / card["region"]["image"]
    picture = Image(str(image_path), width=160 * mm, height=18 * mm, kind="proportional")
    story.append(picture)
    story.append(Spacer(1, 2 * mm))
    story.append(paragraph("Сноски", look["h"]))
    groups: list[tuple[str, list]] = []
    for note in card["footnotes"]:
        if not groups or groups[-1][0] != note["code"]:
            groups.append((note["code"], []))
        groups[-1][1].append(note)
    for code, notes in groups:
        for note in notes:
            story.append(paragraph(f"{code}, пункт {note['clause']}.", look["body"]))
            story.append(paragraph(note["quote"], look["small"]))
        story.append(paragraph(notes[0]["edition_note"], look["small"]))
        story.append(paragraph(notes[0]["file_url"], look["small"]))
    story.append(paragraph(NOT_CHECKED[0], look["h"]))
    for line in NOT_CHECKED[1:]:
        story.append(paragraph(line, look["body"]))
    if card.get("cited_footnote_ok") is False:
        story.append(
            paragraph(
                f"Карточка {card['code_in_project']} в сноску не поставлена.",
                look["body"],
            )
        )
    document = SimpleDocTemplate(
        str(PDF_PATH),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title="Нормоконтроль",
    )
    document.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)


def main() -> int:
    if not CARD_REVIEW.is_file():
        raise SystemExit("Нет сверки карточки. Сначала scripts\\review_conclusions.py")
    verdict = json.loads(CARD_REVIEW.read_text(encoding="utf-8"))
    if not verdict.get("ok"):
        raise SystemExit("Сверка карточки не прошла, отчёт не собран")
    payload = json.loads(CARD_PATH.read_text(encoding="utf-8"))
    cards = payload.get("cards") or []
    if len(cards) != 1:
        raise SystemExit(f"Ожидалась одна карточка, в файле {len(cards)}")
    build(cards[0])
    report_verdict = review.review_report(PDF_PATH, cards[0])
    (OUT_DIR / "review.json").write_text(
        json.dumps(report_verdict, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"report {PDF_PATH.name} review {'ok' if report_verdict['ok'] else 'fail'}")
    for item in report_verdict["checks"]:
        if not item["ok"]:
            print("FAIL", item["name"], item["detail"])
    return 0 if report_verdict["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
