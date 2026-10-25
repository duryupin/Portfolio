# -*- coding: utf-8 -*-
"""Внутренняя сверка выводов. Человек этот шаг не делает.

Модуль не строит карточку заново и не вызывает select_footnotes.
Он читает уже записанную карточку и сверяет каждое поле с листом,
штампом и файлом нормы. Чужой шифр, цитата не из файла и чужая
картинка участка здесь отклоняются.

Запуск из корня norm_check:
  .venv\\Scripts\\python.exe scripts\\review_conclusions.py
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
CARDS_PATH = ROOT / "norms" / "cards.json"
INBOX = ROOT / "inbox"
LINES_DIR = ROOT / "reports" / "stage3"
CARD_PATH = ROOT / "reports" / "stage5" / "cards.json"
OUT_PATH = ROOT / "reports" / "stage5" / "review.json"
LIBRARY = ROOT.parent / "sp_docs_llm" / "pdf_files"
CROP_SCALE = 3
DISCLAIMER = "Это не заключение экспертизы проектной документации."

SP_CODE = re.compile(r"СП\s*(\d+\.\d+)(?:\.(\d{4}))?", re.IGNORECASE)


def squeeze(text: str) -> str:
    folded = (text or "").lower().replace("ё", "е")
    return "".join(char for char in folded if char.isalpha())


def flat(text: str) -> str:
    return " ".join((text or "").split())


def norm_code(code: str) -> str:
    return code.replace(" ", "").upper()


def load_documents() -> list[dict]:
    return json.loads(CARDS_PATH.read_text(encoding="utf-8"))["documents"]


def index_documents(documents: list[dict]) -> dict[str, dict]:
    return {norm_code(document["code"]): document for document in documents}


def mentions(text: str) -> list[str]:
    found = []
    for match in SP_CODE.finditer(text or ""):
        number = match.group(1)
        year = match.group(2)
        found.append(number + (("." + year) if year else ""))
    return found


def check(name: str, ok: bool, detail: str) -> dict:
    return {"name": name, "ok": bool(ok), "detail": detail}


def stamp_number(words: list[dict]) -> str | None:
    """Номер листа под полем «Лист» в одной строке со «Стадия» и «Листов»."""
    for stage in words:
        if stage["text"] != "Стадия":
            continue
        same_row = [
            item
            for item in words
            if abs(item["top"] - stage["top"]) < 3 and item["x0"] >= stage["x0"] - 1
        ]
        sheet = next((item for item in same_row if item["text"] == "Лист"), None)
        total = next((item for item in same_row if item["text"] == "Листов"), None)
        if sheet is None or total is None or sheet["x0"] >= total["x0"]:
            continue
        for item in words:
            center = (item["x0"] + item["x1"]) / 2
            under = sheet["x0"] - 8 <= center <= sheet["x1"] + 8
            below = 0 < item["top"] - sheet["bottom"] < 40
            if item["text"].isdigit() and under and below:
                return item["text"]
    return None


def overlaps(word: dict, box: dict, slack: float = 0.6) -> bool:
    return not (
        word["x1"] < box["x0"] - slack
        or word["x0"] > box["x1"] + slack
        or word["bottom"] < box["top"] - slack
        or word["top"] > box["bottom"] + slack
    )


def local_pdf(document: dict) -> Path | None:
    digest = document.get("file_sha256")
    size = document.get("file_bytes")
    if not digest or not LIBRARY.is_dir():
        return None
    paths = [path for path in LIBRARY.glob("*.pdf") if not size or path.stat().st_size == size]
    for path in paths:
        if hashlib.sha256(path.read_bytes()).hexdigest() == digest:
            return path
    return None


def file_text(path: Path, cache: dict[Path, str]) -> str:
    if path not in cache:
        parts = []
        with pdfplumber.open(path) as document:
            for page in document.pages:
                parts.append(page.extract_text() or "")
        cache[path] = "\n".join(parts)
    return cache[path]


def catalog_clauses(document: dict) -> list[str]:
    clauses = []
    if document.get("quote_clause"):
        clauses.append(str(document["quote_clause"]))
    if document.get("quote_2_clause"):
        clauses.append(str(document["quote_2_clause"]))
    return clauses


def expected_footnote_codes(cited: dict, documents: list[dict]) -> set[str]:
    """Какие шифры обязаны быть в сноске. Тот же смысл, что у сборщика, но код другой."""
    expected = set()
    if cited.get("footnote_ok"):
        expected.add(cited["code"])
    replacement = None
    replaced_by = cited.get("replaced_by")
    if replaced_by:
        replacement = index_documents(documents).get(norm_code(replaced_by))
    if replacement is None:
        for document in documents:
            replaces = document.get("replaces")
            if replaces and norm_code(replaces) == norm_code(cited["code"]):
                replacement = document
                break
    if replacement and replacement.get("footnote_ok") and replacement["code"] != cited["code"]:
        expected.add(replacement["code"])
        blob = " ".join(
            part for part in (replacement.get("quote"), replacement.get("quote_2")) if part
        )
        for number in mentions(blob):
            for document in documents:
                code = document["code"].replace(" ", "")
                if number in code and document.get("footnote_ok") and document["code"] != cited["code"]:
                    expected.add(document["code"])
    if not cited.get("footnote_ok"):
        expected.discard(cited["code"])
    return expected


def review_card_fields(card: dict, documents: list[dict], page_words: list[dict], norm_text: dict[str, str], dwg_text: str) -> list[dict]:
    checks = []
    catalog = index_documents(documents)
    cited = catalog.get(norm_code(card.get("code_in_project", "")))
    checks.append(check("шифр есть в карточках", cited is not None, card.get("code_in_project", "")))
    if cited is None:
        return checks

    checks.append(
        check(
            "закрытая карточка не названа открытой",
            bool(card.get("cited_footnote_ok")) == bool(cited.get("footnote_ok")),
            f"в карточке {card.get('cited_footnote_ok')}, в каталоге {cited.get('footnote_ok')}",
        )
    )
    footnotes = card.get("footnotes") or []
    footnote_codes = {note.get("code") for note in footnotes}
    if not cited.get("footnote_ok"):
        checks.append(
            check(
                "шифр проекта не стоит в сноске",
                cited["code"] not in footnote_codes,
                cited["code"],
            )
        )
    expected = expected_footnote_codes(cited, documents)
    checks.append(
        check(
            "в сноске те шифры, которые открыты каталогом",
            footnote_codes == expected,
            f"карточка {sorted(footnote_codes)} каталог {sorted(expected)}",
        )
    )

    for note in footnotes:
        document = catalog.get(norm_code(note.get("code", "")))
        if document is None or not document.get("footnote_ok"):
            checks.append(check("сноска с footnote_ok", False, note.get("code", "")))
            continue
        same_link = note.get("file_url") == document.get("file_url") and bool(note.get("file_url"))
        checks.append(check(f"ссылка {note['code']} п. {note['clause']}", same_link, note.get("file_url") or "пусто"))
        same_note = note.get("edition_note") == document.get("edition_note")
        checks.append(check(f"пометка издания {note['code']}", same_note, "совпала" if same_note else "разошлась"))
        if note.get("clause") not in catalog_clauses(document):
            checks.append(check(f"пункт есть в каталоге {note['code']}", False, note.get("clause", "")))
        body = norm_text.get(note["code"], "")
        quote = flat(note.get("quote"))
        found = bool(quote) and quote in flat(body)
        checks.append(check(f"цитата в файле {note['code']} п. {note['clause']}", found, "найдена" if found else "нет в файле"))
        if found:
            position = flat(body).find(quote)
            window = flat(body)[max(0, position - 16) : position]
            checks.append(
                check(
                    f"пункт перед цитатой {note['code']} п. {note['clause']}",
                    note["clause"] in window,
                    window[-16:],
                )
            )

    number = stamp_number(page_words)
    checks.append(
        check(
            "номер листа в штампе",
            number is not None and number == str(card.get("sheet_number")),
            f"на листе {number}, в карточке {card.get('sheet_number')}",
        )
    )
    as_read = card.get("sheet_name_as_read") or ""
    name_on_page = any(squeeze(word["text"]) == squeeze(as_read) and squeeze(as_read) for word in page_words)
    # Имя может быть склеено из нескольких слов текстового слоя.
    if not name_on_page and as_read:
        joined = squeeze(" ".join(word["text"] for word in page_words))
        name_on_page = squeeze(as_read) in joined
    checks.append(check("имя листа есть на странице", name_on_page, as_read or "пусто"))
    shown = card.get("sheet_name") or ""
    checks.append(
        check(
            "имя листа с пробелами — то же имя",
            bool(shown) and squeeze(shown) == squeeze(as_read),
            shown,
        )
    )

    region = (card.get("region") or {}).get("words")
    title = card.get("title_in_pdf_text_layer") or card.get("title_in_project") or ""
    code_number = card["code_in_project"].split()[-1]
    if not region:
        checks.append(check("участок указан", False, "нет прямоугольника"))
    else:
        inside = [word for word in page_words if overlaps(word, region)]
        blob_raw = "".join(word["text"] for word in inside)
        blob = squeeze(blob_raw)
        checks.append(
            check(
                "в участке шифр и название",
                code_number in blob_raw.replace(" ", "") and squeeze(title) in blob,
                f"слов в участке {len(inside)}",
            )
        )

    if dwg_text:
        dwg_flat = dwg_text.replace(" ", "")
        checks.append(
            check(
                "тот же шифр есть в тексте DWG",
                code_number in dwg_flat and squeeze(card.get("title_in_project") or "") in squeeze(dwg_text),
                ", ".join(card.get("dwg_text") or []),
            )
        )

    allowed = {norm_code(cited["code"])}
    allowed.update(norm_code(code) for code in footnote_codes if code)
    prose_codes = []
    remark = card.get("remark") or ""
    # Коды внутри кавычек-цитат нормы не считаем новым шифром отчёта.
    without_quotes = re.sub(r"«[^»]*»", " ", remark)
    for number_text in mentions(without_quotes):
        if len(number_text.split(".")) < 3:
            continue
        prose_codes.append("СП" + number_text.replace(" ", ""))
    unknown = [code for code in prose_codes if code.upper() not in {item.upper() for item in allowed}]
    checks.append(check("в тексте карточки нет чужого шифра", not unknown, ", ".join(unknown) or "нет"))
    return checks


def render_crop(pdf_path: Path, page_number: int, box: dict) -> Image.Image:
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        bitmap = document[page_number - 1].render(scale=CROP_SCALE)
        image = bitmap.to_pil().convert("RGB")
        pixel = (
            int(box["x0"] * CROP_SCALE),
            int(box["top"] * CROP_SCALE),
            int(box["x1"] * CROP_SCALE),
            int(box["bottom"] * CROP_SCALE),
        )
        return image.crop(pixel)
    finally:
        document.close()


def review_image(card: dict, pdf_path: Path) -> dict:
    region = card.get("region") or {}
    image_rel = region.get("image")
    crop = region.get("crop")
    if not image_rel or not crop:
        return check("картинка участка", False, "нет файла или рамки")
    image_path = ROOT / image_rel
    if not image_path.is_file():
        return check("картинка участка", False, str(image_rel))
    saved = Image.open(image_path).convert("RGB")
    fresh = render_crop(pdf_path, int(card["page"]), crop)
    same = saved.size == fresh.size and saved.tobytes() == fresh.tobytes()
    return check(
        "картинка участка совпала с листом",
        same,
        f"файл {saved.size[0]}x{saved.size[1]}, лист {fresh.size[0]}x{fresh.size[1]}",
    )


def image_count(pdf_path: Path) -> int:
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LTContainer, LTImage

    def walk(item) -> int:
        found = 1 if isinstance(item, LTImage) else 0
        if isinstance(item, LTContainer):
            found += sum(walk(child) for child in item)
        return found

    total = 0
    for page in extract_pages(str(pdf_path)):
        total += walk(page)
    return total


def package_facts(package: dict) -> list[dict]:
    rows = []
    for rule in package.get("rules") or []:
        if rule.get("id") == "sp_title":
            continue
        rows.extend(rule.get("findings") or [])
    return rows


def review_package_report(
    pdf_path: Path,
    card: dict | None,
    meta: dict,
    source_pdf: Path,
    package: dict,
) -> dict:
    """Сверка PDF пакета с повторным чтением листа. Новых шифров быть не должно."""
    import check_package

    with pdfplumber.open(pdf_path) as document:
        text = "\n".join((page.extract_text() or "") for page in document.pages)
    flat_text = " ".join(text.split())
    checks = [
        check("фраза о не-экспертизе", DISCLAIMER in flat_text, "есть" if DISCLAIMER in flat_text else "нет"),
        check(
            "блок «что не проверялось»",
            "Что не проверялось" in flat_text,
            "есть" if "Что не проверялось" in flat_text else "нет",
        ),
    ]
    if card:
        inherited = review_report(pdf_path, card)
        seen = {item["name"] for item in checks}
        checks.extend(
            item
            for item in inherited["checks"]
            if item["name"] not in seen and item["name"] != "в отчёте одна картинка участка"
        )
        checks.append(review_image(card, source_pdf))
    else:
        foreign = [
            number
            for number in mentions(flat_text)
            if number.count(".") >= 2
        ]
        checks.append(
            check("в отчёте нет шифра с годом", not foreign, ", ".join(foreign) or "нет")
        )
    regions = meta.get("regions") or []
    pictures = image_count(pdf_path)
    checks.append(
        check(
            "число картинок равно числу участков",
            pictures == len(regions),
            f"картинок {pictures}, участков {len(regions)}",
        )
    )
    fresh = check_package.fact_findings(source_pdf)
    stored = package_facts(package)
    checks.append(
        check(
            "повторное чтение дало те же замечания",
            fresh == stored,
            f"сейчас {len(fresh)}, в пакете {len(stored)}",
        )
    )
    marked = flat_text.count("В проекте:")
    checks.append(
        check(
            "в отчёте нет лишней величины",
            marked == len(fresh),
            f"строк {marked}, замечаний {len(fresh)}",
        )
    )
    with pdfplumber.open(source_pdf) as source:
        source_pages = [(page.extract_text() or "") for page in source.pages]
    for fact in fresh:
        pages = fact.get("pages") or [fact.get("page")]
        pages = [int(number) for number in pages if number]
        fields_ok = (
            fact["quantity"] in flat_text
            and fact["project_value"] in flat_text
            and fact["direction"] in flat_text
            and check_package.where_text(fact) in flat_text
        )
        checks.append(
            check(
                f"в отчёте есть {fact['quantity']}",
                fields_ok,
                fact["project_value"],
            )
        )
        on_sheet = True
        for number in pages:
            raw = " ".join(source_pages[number - 1].split())
            if fact.get("line"):
                on_sheet = on_sheet and " ".join(fact["line"].split()) in raw
            else:
                token = str(fact["project_value"]).split()[0]
                on_sheet = on_sheet and (token in raw or token.replace(",", ".") in raw)
        checks.append(
            check(
                f"лист содержит {fact['quantity']}",
                on_sheet,
                ", ".join(str(number) for number in pages),
            )
        )
        if fact.get("norm_value") is not None:
            checks.append(check(f"у {fact['quantity']} нет пункта нормы", False, "норма приписана"))
    for region in regions:
        if region.get("kind") == "title":
            continue
        image_rel = region.get("image")
        crop = region.get("crop")
        image_path = ROOT / image_rel if image_rel else None
        if not image_path or not image_path.is_file() or not crop:
            checks.append(check("картинка участка пакета", False, str(image_rel)))
            continue
        saved = Image.open(image_path).convert("RGB")
        fresh_image = render_crop(source_pdf, int(region["page"]), crop)
        same = saved.size == fresh_image.size and saved.tobytes() == fresh_image.tobytes()
        checks.append(
            check(
                f"картинка стр. {region['page']} совпала с листом",
                same,
                f"{saved.size[0]}x{saved.size[1]}",
            )
        )
    for rule in package.get("rules") or []:
        withdrawn = rule.get("withdrawn")
        if withdrawn:
            checks.append(check("снятое сравнение названо", withdrawn in flat_text, withdrawn))
    return {"ok": all(item["ok"] for item in checks), "checks": checks}


def review_report(pdf_path: Path, card: dict) -> dict:
    """Сверка уже собранного PDF с карточкой. Новых шифров быть не должно."""
    with pdfplumber.open(pdf_path) as document:
        text = "\n".join((page.extract_text() or "") for page in document.pages)
    card_blob = json.dumps(card, ensure_ascii=False).replace(" ", "")
    foreign = []
    for number in mentions(text):
        if number.count(".") < 2:
            continue
        if number not in card_blob:
            foreign.append(number)
    checks = [
        check("фраза о не-экспертизе", DISCLAIMER in text, "есть" if DISCLAIMER in text else "нет"),
        check("блок «что не проверялось»", "Что не проверялось" in text, "есть" if "Что не проверялось" in text else "нет"),
        check("в отчёте нет чужого шифра с годом", not foreign, ", ".join(foreign) or "нет"),
        check("шифр проекта в отчёте", card["code_in_project"] in text, card["code_in_project"]),
        check("название проекта в отчёте", card["title_in_project"] in text, card["title_in_project"]),
        check("название карточки в отчёте", card["title_in_catalog"] in text, card["title_in_catalog"]),
        check("имя листа в отчёте", card["sheet_name"] in text, card["sheet_name"]),
    ]
    for note in card.get("footnotes") or []:
        quote_ok = flat(note["quote"]) in flat(text)
        checks.append(
            check(
                f"цитата отчёта {note['code']} п. {note['clause']}",
                quote_ok and note["code"] in text and note["clause"] in text,
                "есть" if quote_ok else "цитата не прочиталась из PDF",
            )
        )
    pictures = image_count(pdf_path)
    checks.append(check("в отчёте одна картинка участка", pictures == 1, f"картинок {pictures}"))
    return {"ok": all(item["ok"] for item in checks), "checks": checks}


def self_check() -> None:
    documents = [
        {
            "code": "СП 1.13330.2020",
            "footnote_ok": False,
            "replaced_by": "СП 1.13330.2022",
            "file_url": None,
        },
        {
            "code": "СП 1.13330.2022",
            "footnote_ok": True,
            "file_url": "https://example.test/new",
            "edition_note": "издание без изменений",
            "quote_clause": "1.1",
            "quote": "Область включает дома и отсылает к СП 2.13330.",
            "replaces": "СП 1.13330.2020",
        },
        {
            "code": "СП 2.13330.2016",
            "footnote_ok": True,
            "file_url": "https://example.test/other",
            "edition_note": "базовый файл",
            "quote_clause": "1.1",
            "quote": "Только одни дома.",
        },
    ]
    words = [
        {"text": "Стадия", "x0": 400, "top": 100, "x1": 440, "bottom": 110},
        {"text": "Лист", "x0": 450, "top": 100, "x1": 480, "bottom": 110},
        {"text": "Листов", "x0": 500, "top": 100, "x1": 540, "bottom": 110},
        {"text": "2", "x0": 460, "top": 120, "x1": 468, "bottom": 132},
        {"text": "Имялиста", "x0": 200, "top": 160, "x1": 260, "bottom": 170},
        {"text": "СП1.13330.2020", "x0": 10, "top": 20, "x1": 40, "bottom": 28},
        {"text": "Чужоеназвание", "x0": 42, "top": 20, "x1": 90, "bottom": 28},
    ]
    good = {
        "code_in_project": "СП 1.13330.2020",
        "cited_footnote_ok": False,
        "title_in_project": "Чужое название",
        "title_in_pdf_text_layer": "Чужоеназвание",
        "sheet_number": "2",
        "sheet_name": "Имя листа",
        "sheet_name_as_read": "Имялиста",
        "region": {"words": {"x0": 10, "top": 20, "x1": 90, "bottom": 28}},
        "footnotes": [
            {
                "code": "СП 1.13330.2022",
                "clause": "1.1",
                "quote": "Область включает дома и отсылает к СП 2.13330.",
                "file_url": "https://example.test/new",
                "edition_note": "издание без изменений",
            },
            {
                "code": "СП 2.13330.2016",
                "clause": "1.1",
                "quote": "Только одни дома.",
                "file_url": "https://example.test/other",
                "edition_note": "базовый файл",
            },
        ],
        "remark": "У шифра СП 1.13330.2020 написано другое. Сноски: СП 1.13330.2022 п. 1.1. «Область включает дома и отсылает к СП 2.13330.»",
        "dwg_text": [],
    }
    norm_text = {
        "СП 1.13330.2022": "1 Область 1.1 Область включает дома и отсылает к СП 2.13330. 1.2 Дальше.",
        "СП 2.13330.2016": "1.1 Только одни дома.",
    }
    good_checks = review_card_fields(good, documents, words, norm_text, "")
    failed = [item for item in good_checks if not item["ok"]]
    if failed:
        raise SystemExit("Самопроверка: годная карточка отклонена: " + failed[0]["name"] + " " + failed[0]["detail"])

    bad = json.loads(json.dumps(good))
    bad["footnotes"][0]["quote"] = "Этой фразы в файле нет."
    bad_checks = review_card_fields(bad, documents, words, norm_text, "")
    if any(item["ok"] and item["name"].startswith("цитата в файле СП 1") for item in bad_checks):
        raise SystemExit("Самопроверка: чужая цитата прошла")
    if not any(not item["ok"] and item["name"].startswith("цитата в файле") for item in bad_checks):
        raise SystemExit("Самопроверка: чужая цитата не отклонена")

    closed = json.loads(json.dumps(good))
    closed["footnotes"].insert(0, {"code": "СП 1.13330.2020", "clause": "1.1", "quote": "нет", "file_url": None})
    closed_checks = review_card_fields(closed, documents, words, norm_text, "")
    if not any(not item["ok"] and "шифр проекта" in item["name"] for item in closed_checks):
        raise SystemExit("Самопроверка: закрытый шифр остался в сноске")


def main() -> int:
    self_check()
    payload = json.loads(CARD_PATH.read_text(encoding="utf-8"))
    cards = payload.get("cards") or []
    if len(cards) != 1:
        raise SystemExit(f"Ожидалась одна карточка, в файле {len(cards)}")
    card = cards[0]
    documents = load_documents()
    pdfs = list(INBOX.glob(card["pdf"]))
    if len(pdfs) != 1:
        raise SystemExit("PDF из карточки в inbox не найден")
    pdf_path = pdfs[0]
    with pdfplumber.open(pdf_path) as document:
        page = document.pages[int(card["page"]) - 1]
        words = page.extract_words() or []
    cache: dict[Path, str] = {}
    norm_text = {}
    for note in card.get("footnotes") or []:
        document = index_documents(documents).get(norm_code(note["code"]))
        if document is None:
            continue
        path = local_pdf(document)
        norm_text[note["code"]] = file_text(path, cache) if path else ""
    dwg_parts = []
    for name in card.get("dwg_text") or []:
        path = LINES_DIR / name
        if path.is_file():
            dwg_parts.append(path.read_text(encoding="utf-8"))
    checks = review_card_fields(card, documents, words, norm_text, "\n".join(dwg_parts))
    checks.append(review_image(card, pdf_path))
    report = {"ok": all(item["ok"] for item in checks), "checks": checks}
    OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"review {'ok' if report['ok'] else 'fail'} checks {len(checks)}")
    for item in checks:
        if not item["ok"]:
            print("FAIL", item["name"], item["detail"])
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
