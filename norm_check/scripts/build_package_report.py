# -*- coding: utf-8 -*-
"""Перенос замечаний этапа 8 в PDF.

Берёт уже сверенную карточку названия и замечания пакета.
reports/stage6/report.pdf не перезаписывает.
Новый файл: reports/stage8/report.pdf.

Запуск из корня norm_check:
  .venv\\Scripts\\python.exe scripts\\build_package_report.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pdfplumber
from reportlab.platypus import KeepTogether

import build_report
import check_package
import finding_card
import question_catalog
import review_conclusions as review

ROOT = Path(__file__).resolve().parents[1]
CARD_PATH = ROOT / "reports" / "stage5" / "cards.json"
CARD_REVIEW = ROOT / "reports" / "stage5" / "review.json"
PACKAGE_PATH = ROOT / "reports" / "stage8" / "package.json"
OUT_DIR = ROOT / "reports" / "stage8"
PDF_PATH = OUT_DIR / "report.pdf"
INBOX = ROOT / "inbox"

NOT_CHECKED = (
    "Ниже вопросы экспертизы, которые этот прогон в замечания не ставил. Номер и статус берутся из каталога, не из чисел одного проекта.",
    "Площадь и нагрузка помещения ищутся как неположительное значение в своём поле. Диаметр ищется как шаг 0,01 мм от дюймового ряда, а не как одно заранее известное число.",
    "Площадь без числа на той же строке замечанием не объявляется.",
    "Слово вне столбца нагрузки помещения замечанием не объявляется.",
    "Размер, который уже записан как в дюймовом ряде, замечанием не является.",
)


def source_pdf() -> Path:
    pdfs = sorted(INBOX.glob("*.pdf"))
    if len(pdfs) != 1:
        raise SystemExit(f"Ожидался один PDF в inbox, найдено: {len(pdfs)}")
    return pdfs[0]


def load_card(package: dict) -> dict | None:
    title = next(rule for rule in package["rules"] if rule["id"] == "sp_title")
    findings = title["findings"]
    if not CARD_PATH.is_file():
        cards = []
    else:
        cards = json.loads(CARD_PATH.read_text(encoding="utf-8")).get("cards") or []
    if findings and not cards:
        raise SystemExit("Название СП найдено, а сверенной карточки нет")
    if cards and not findings:
        raise SystemExit("Карточка названия есть, а правило пакета его не нашло")
    if not cards:
        return None
    if len(cards) != 1:
        raise SystemExit(f"Ожидалась одна карточка, в файле {len(cards)}")
    if not CARD_REVIEW.is_file():
        raise SystemExit("Нет сверки карточки. Сначала scripts\\review_conclusions.py")
    verdict = json.loads(CARD_REVIEW.read_text(encoding="utf-8"))
    if not verdict.get("ok"):
        raise SystemExit("Сверка карточки не прошла, отчёт не собран")
    card = cards[0]
    codes = {row["code_in_project"] for row in findings}
    if codes != {card["code_in_project"]}:
        raise SystemExit("Шифр карточки и шифр пакета различаются")
    return card


def group_rows(words: list[dict]) -> list[list[dict]]:
    rows: list[list[dict]] = []
    for word in sorted(words, key=lambda item: (item["top"], item["x0"])):
        if rows and abs(word["top"] - rows[-1][0]["top"]) <= 3:
            rows[-1].append(word)
        else:
            rows.append([word])
    return rows


def area_row(words: list[dict], project_value: str | None = None) -> list[dict] | None:
    wanted = {"0", "0,0", "0.0", "0,00", "0.00"}
    if project_value:
        token = project_value.replace(" ", "")
        wanted.add(token)
        wanted.add(token.replace(".", ","))
        wanted.add(token.replace(",", "."))
    for row in group_rows(words):
        label = any("площад" in finding_card.titles.squeeze(word["text"]) for word in row)
        hit = any(word["text"].replace(" ", "") in wanted for word in row)
        if label and hit:
            return row
    return None


def diameter_words(words: list[dict], project_value: str) -> list[dict]:
    number = project_value.split()[0].replace(" ", "").replace(",", ".")
    pattern = re.compile(rf"(?<!\d){re.escape(number)}(?!\d)")
    return [word for word in words if pattern.search(word["text"].replace(",", ".").replace(" ", ""))]


def crop_words(pdf_path: Path, page_number: int, words: list[dict], dest: Path) -> dict:
    with pdfplumber.open(pdf_path) as document:
        page = document.pages[page_number - 1]
        width = float(page.width)
        height = float(page.height)
    box = finding_card.pad_box(finding_card.union_box(words), width, height)
    rounded = {key: round(box[key], 1) for key in ("x0", "top", "x1", "bottom")}
    finding_card.crop_page(pdf_path, page_number, rounded, dest)
    return rounded


def fact_regions(pdf_path: Path, facts: list[dict]) -> list[dict]:
    regions = []
    with pdfplumber.open(pdf_path) as document:
        pages = {index: page.extract_words() or [] for index, page in enumerate(document.pages, start=1)}
    for fact in facts:
        if fact.get("drawing"):
            continue
        page_numbers = fact.get("pages") or [fact.get("page")]
        page_numbers = [int(number) for number in page_numbers if number]
        if not page_numbers:
            raise SystemExit(f"У замечания «{fact['quantity']}» нет страницы")
        for page_number in page_numbers:
            words = pages[page_number]
            if fact["rule"] == "zero_area":
                chosen = area_row(words, fact.get("project_value"))
            elif fact["rule"] == "pipe_diameter":
                chosen = diameter_words(words, fact["project_value"])
            else:
                token = str(fact["project_value"]).replace(" ", "")
                chosen = [
                    word
                    for word in words
                    if token and token in word["text"].replace(" ", "")
                ]
            if not chosen:
                raise SystemExit(
                    f"Участок «{fact['quantity']}» на странице {page_number} не найден"
                )
            image_name = f"region-{fact['rule']}-page-{page_number:02d}.png"
            image_path = OUT_DIR / image_name
            crop = crop_words(pdf_path, page_number, chosen, image_path)
            regions.append(
                {
                    "kind": fact["rule"],
                    "page": page_number,
                    "image": str(image_path.relative_to(ROOT)),
                    "crop": crop,
                }
            )
    return regions


def add_title(story: list, look: dict, card: dict) -> None:
    story.append(build_report.paragraph("Название при шифре СП", look["h"]))
    story.append(
        build_report.paragraph(
            f"Страница {card['page']}, лист «{card['sheet_name']}», в штампе номер {card['sheet_number']}.",
            look["body"],
        )
    )
    story.append(
        build_report.paragraph(
            f"У шифра {card['code_in_project']} написано «{card['title_in_project']}». "
            f"Название этого шифра в карточке: «{card['title_in_catalog']}». "
            "В названии другое слово.",
            look["body"],
        )
    )
    image_path = ROOT / card["region"]["image"]
    story.append(build_report.Image(str(image_path), width=160 * build_report.mm, height=18 * build_report.mm, kind="proportional"))
    story.append(build_report.Spacer(1, 2 * build_report.mm))
    story.append(build_report.paragraph("Сноски", look["h"]))
    groups: list[tuple[str, list]] = []
    for note in card["footnotes"]:
        if not groups or groups[-1][0] != note["code"]:
            groups.append((note["code"], []))
        groups[-1][1].append(note)
    for code, notes in groups:
        for note in notes:
            story.append(build_report.paragraph(f"{code}, пункт {note['clause']}.", look["body"]))
            story.append(build_report.paragraph(note["quote"], look["small"]))
        story.append(build_report.paragraph(notes[0]["edition_note"], look["small"]))
        story.append(build_report.paragraph(notes[0]["file_url"], look["small"]))
    if card.get("cited_footnote_ok") is False:
        story.append(
            build_report.paragraph(
                f"Карточка {card['code_in_project']} в сноску не поставлена.",
                look["body"],
            )
        )


def fact_flow(look: dict, fact: dict, regions: list[dict]) -> list:
    chunk = [
        build_report.paragraph(fact["quantity"][:1].upper() + fact["quantity"][1:], look["h"]),
    ]
    other = fact.get("other_value")
    compared = f"Ряд для этой величины: {other}. " if other else ""
    chunk.append(
        build_report.paragraph(
            f"{check_package.where_text(fact)} Величина: {fact['quantity']}. "
            f"В проекте: {fact['project_value']}. {compared}"
            f"{fact['norm_note']} В какую сторону: {fact['direction']}.",
            look["body"],
        )
    )
    pages = {int(number) for number in (fact.get("pages") or [fact.get("page")]) if number}
    for region in regions:
        if region["kind"] != fact["rule"] or int(region["page"]) not in pages:
            continue
        chunk.append(build_report.paragraph(f"Участок страницы {region['page']}.", look["small"]))
        image_path = ROOT / region["image"]
        chunk.append(
            build_report.Image(
                str(image_path),
                width=160 * build_report.mm,
                height=14 * build_report.mm,
                kind="proportional",
            )
        )
        chunk.append(build_report.Spacer(1, 2 * build_report.mm))
    return chunk


def build(card: dict | None, facts: list[dict], regions: list[dict], package: dict) -> None:
    catalog_errors = question_catalog.self_check()
    if catalog_errors:
        raise SystemExit("Каталог вопросов: " + "; ".join(catalog_errors))
    build_report.fonts()
    look = build_report.styles()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    story = [
        build_report.paragraph("Нормоконтроль", look["title"]),
        build_report.paragraph(
            review.DISCLAIMER
            + " Проверены правила пакета: название при шифре СП, имя листа в ведомости, "
            "один шифр, строка ведомости и штамп, "
            "неположительное значение площади и нагрузки помещения, "
            "наружный диаметр на 0,01 мм от дюймового ряда, "
            "номер помещения, подпись длины участка.",
            look["body"],
        ),
        build_report.paragraph("Замечания", look["h"]),
        build_report.paragraph(
            "Ниже только те места, где правило нашло расхождение. Совпавшие проверки в список не входят.",
            look["body"],
        ),
    ]
    if not card and not facts:
        story.append(
            build_report.paragraph("По этим правилам замечаний нет.", look["body"])
        )
    title_region = []
    if card:
        add_title(story, look, card)
        title_region.append(
            {
                "kind": "title",
                "page": card["page"],
                "image": card["region"]["image"],
                "crop": card["region"]["crop"],
            }
        )
    for fact in facts:
        story.append(KeepTogether(fact_flow(look, fact, regions)))
    story.append(build_report.paragraph("Что не проверялось", look["h"]))
    lines = list(NOT_CHECKED)
    lines.extend(question_catalog.report_lines())
    for rule in package["rules"]:
        if rule.get("withdrawn") and rule["withdrawn"] not in lines:
            lines.append(rule["withdrawn"])
    for line in lines:
        story.append(build_report.paragraph(line, look["body"]))
    document = build_report.SimpleDocTemplate(
        str(PDF_PATH),
        pagesize=build_report.A4,
        leftMargin=18 * build_report.mm,
        rightMargin=18 * build_report.mm,
        topMargin=16 * build_report.mm,
        bottomMargin=16 * build_report.mm,
        title="Нормоконтроль",
    )
    document.build(story, onFirstPage=build_report.draw_footer, onLaterPages=build_report.draw_footer)
    meta = {"regions": title_region + regions}
    (OUT_DIR / "report_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> int:
    if not PACKAGE_PATH.is_file():
        raise SystemExit("Нет пакета. Сначала scripts\\check_package.py")
    package = json.loads(PACKAGE_PATH.read_text(encoding="utf-8"))
    if package.get("false_findings"):
        raise SystemExit("В пакете есть ложные находки, отчёт не собран")
    pdf_path = source_pdf()
    if package.get("pdf") and package["pdf"] != pdf_path.name:
        raise SystemExit("Пакет собран по другому PDF")
    card = load_card(package)
    facts = check_package.fact_findings(pdf_path)
    if facts != review.package_facts(package):
        raise SystemExit("Повторное чтение листа не совпало с package.json")
    regions = fact_regions(pdf_path, facts)
    build(card, facts, regions, package)
    meta = json.loads((OUT_DIR / "report_meta.json").read_text(encoding="utf-8"))
    verdict = review.review_package_report(PDF_PATH, card, meta, pdf_path, package)
    (OUT_DIR / "review.json").write_text(
        json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"report {PDF_PATH.name} review {'ok' if verdict['ok'] else 'fail'} checks {len(verdict['checks'])}")
    for item in verdict["checks"]:
        if not item["ok"]:
            print("FAIL", item["name"], item["detail"])
    return 0 if verdict["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
