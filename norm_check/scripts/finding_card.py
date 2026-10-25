# -*- coding: utf-8 -*-
"""Этап 5. Карточка замечания по уже найденной подмене названия СП.

Берёт находки того же правила, что scripts/check_sp_title.py.
На странице PDF читает штамп формы 3 (стадия, лист, листов) и вырезает
участок строки с шифром. В сноску ставит только карточки с footnote_ok:
сначала документ из поля replaced_by, затем документы, которые этот
документ сам называет в цитате области применения. Карточка шифра,
у которой footnote_ok = false, в сноску не идёт.

Шифр, адрес и имена файлов комплекта в правило не зашиты.

Запуск из корня norm_check:
  .venv\\Scripts\\python.exe scripts\\finding_card.py
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium

import check_sp_title as titles

ROOT = Path(__file__).resolve().parents[1]
CARDS_PATH = ROOT / "norms" / "cards.json"
INBOX = ROOT / "inbox"
LINES_DIR = ROOT / "reports" / "stage3"
OUT_DIR = ROOT / "reports" / "stage5"
LIBRARY = ROOT.parent / "sp_docs_llm" / "pdf_files"

SP_MENTION = re.compile(r"СП\s*(\d+\.\d+)(?:\.\d{4})?", re.IGNORECASE)
DASHES = str.maketrans({"–": "-", "—": "-", "−": "-", "‑": "-"})
LOOKALIKES = str.maketrans({"c": "с", "a": "а", "e": "е", "o": "о", "p": "р", "x": "х", "y": "у"})


def norm_code(code: str) -> str:
    return code.replace(" ", "").upper()


def fold_quote(text: str) -> str:
    collapsed = " ".join(text.translate(DASHES).split())
    return collapsed.translate(LOOKALIKES)


def cards_index(documents: list[dict]) -> dict[str, dict]:
    return {norm_code(document["code"]): document for document in documents}


def clause_rows(card: dict) -> list[tuple[str, str]]:
    rows = []
    if card.get("quote_clause") and card.get("quote"):
        rows.append((str(card["quote_clause"]), card["quote"]))
    if card.get("quote_2_clause") and card.get("quote_2"):
        rows.append((str(card["quote_2_clause"]), card["quote_2"]))
    return rows


def mentioned_codes(text: str, documents: list[dict]) -> list[dict]:
    found = []
    seen = set()
    for match in SP_MENTION.finditer(text):
        number = match.group(1).replace(" ", "")
        for document in documents:
            code = norm_code(document["code"])
            if number in code and code not in seen:
                seen.add(code)
                found.append(document)
    return found


def select_footnotes(cited_code: str, documents: list[dict]) -> list[dict]:
    """Сноски к подмене названия. Чужой шифр и закрытая карточка не проходят."""
    index = cards_index(documents)
    cited = index.get(norm_code(cited_code))
    if cited is None:
        return []
    chosen: list[tuple[dict, str]] = []
    seen = set()

    def add(card: dict | None, reason: str) -> None:
        if card is None or not card.get("footnote_ok"):
            return
        if not card.get("file_url"):
            return
        code = card["code"]
        if code in seen or not clause_rows(card):
            return
        seen.add(code)
        chosen.append((card, reason))

    if cited.get("footnote_ok"):
        add(cited, "scope_of_cited_code")
    replacement = None
    if cited.get("replaced_by"):
        replacement = index.get(norm_code(cited["replaced_by"]))
    if replacement is None:
        for document in documents:
            replaces = document.get("replaces")
            if replaces and norm_code(replaces) == norm_code(cited["code"]):
                replacement = document
                break
    if replacement is not None and replacement["code"] != cited["code"]:
        add(replacement, "replaced_by")
    if replacement is not None:
        blob = " ".join(quote for _clause, quote in clause_rows(replacement))
        for document in mentioned_codes(blob, documents):
            if document["code"] != cited["code"]:
                add(document, "named_in_scope_quote")

    notes = []
    for card, reason in chosen:
        for clause, quote in clause_rows(card):
            notes.append(
                {
                    "code": card["code"],
                    "clause": clause,
                    "quote": quote,
                    "file_url": card.get("file_url"),
                    "card_url": card.get("card_url"),
                    "edition_note": card.get("edition_note"),
                    "file_sha256": card.get("file_sha256"),
                    "reason": reason,
                    "footnote_ok": True,
                }
            )
    return notes


def word(text: str, x0: float, top: float, x1: float, bottom: float) -> dict:
    return {"text": text, "x0": x0, "top": top, "x1": x1, "bottom": bottom}


def title_block(words: list[dict]) -> tuple[dict, dict, dict] | None:
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
        if sheet and total and sheet["x0"] < total["x0"]:
            return stage, sheet, total
    return None


def read_stamp(words: list[dict]) -> dict:
    """Имя листа и номер из основной надписи. Чужие подписи листа не берёт."""
    empty = {
        "sheet_number": None,
        "sheet_name_as_read": None,
        "unread": "штамп с полями Стадия, Лист и Листов не разобран",
    }
    found = title_block(words)
    if found is None:
        return empty
    stage, sheet_label, _total = found
    number = next(
        (
            item
            for item in words
            if item["text"].isdigit()
            and sheet_label["x0"] - 8 <= (item["x0"] + item["x1"]) / 2 <= sheet_label["x1"] + 8
            and 0 < item["top"] - sheet_label["bottom"] < 40
        ),
        None,
    )
    if number is None:
        return empty
    set_name = [
        item
        for item in words
        if stage["x0"] - 220 <= item["x0"] < stage["x0"]
        and sheet_label["bottom"] < item["top"] < number["top"] + 4
    ]
    left_edge = min((item["x0"] for item in set_name), default=stage["x0"] - 200) - 15
    format_top = min(
        (
            item["top"]
            for item in words
            if item["text"].startswith("Формат") and item["top"] > number["bottom"]
        ),
        default=number["bottom"] + 80,
    )
    name_words = [
        item
        for item in words
        if left_edge <= item["x0"] < stage["x0"]
        and number["bottom"] - 2 < item["top"] < format_top
    ]
    name_words.sort(key=lambda item: (round(item["top"], 0), item["x0"]))
    if not name_words:
        return {
            "sheet_number": number["text"],
            "sheet_name_as_read": None,
            "unread": "номер листа в штампе есть, имя листа в ячейке не прочитано",
        }
    return {
        "sheet_number": number["text"],
        "sheet_name_as_read": " ".join(item["text"] for item in name_words),
        "unread": None,
    }


def restore_spaces(text: str | None, lines: list[str]) -> str | None:
    """Вернуть пробелы, если в тексте листа есть одна такая же строка без номера."""
    if not text:
        return text
    # Уже есть пробел: не подменять точку в «Вентиляция. План» строкой без точки.
    if any(char.isspace() for char in text):
        return text
    target = titles.squeeze(text)
    found = []
    for line in lines:
        cleaned = " ".join(line.split())
        if " " not in cleaned or any(char.isdigit() for char in cleaned):
            continue
        if titles.squeeze(cleaned) == target:
            found.append(cleaned)
    unique = sorted(set(found))
    if len(unique) == 1:
        return unique[0]
    return text


def union_box(items: list[dict]) -> dict:
    return {
        "x0": min(item["x0"] for item in items),
        "top": min(item["top"] for item in items),
        "x1": max(item["x1"] for item in items),
        "bottom": max(item["bottom"] for item in items),
    }


def citation_box(words: list[dict], number: str, title: str) -> dict | None:
    title_key = titles.squeeze(title)
    number_key = number.replace(" ", "")
    boxes = []
    for code_word in words:
        flat = code_word["text"].replace(" ", "")
        if number_key not in flat:
            continue
        row = [
            item
            for item in words
            if abs(item["top"] - code_word["top"]) < 3 and item["x0"] >= code_word["x0"] - 1
        ]
        row.sort(key=lambda item: item["x0"])
        taken = []
        for item in row:
            taken.append(item)
            blob = titles.squeeze("".join(piece["text"] for piece in taken))
            if title_key and title_key in blob:
                boxes.append(union_box(taken))
                break
    if not boxes:
        return None
    return union_box(boxes)


def pad_box(box: dict, width: float, height: float, pad_x: float = 8, pad_y: float = 1.5) -> dict:
    return {
        "x0": max(0, box["x0"] - pad_x),
        "top": max(0, box["top"] - pad_y),
        "x1": min(width, box["x1"] + pad_x),
        "bottom": min(height, box["bottom"] + pad_y),
    }


def crop_page(pdf_path: Path, page_number: int, box: dict, dest: Path, scale: float = 3) -> None:
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        page = document[page_number - 1]
        bitmap = page.render(scale=scale)
        image = bitmap.to_pil()
        pixel = (
            int(box["x0"] * scale),
            int(box["top"] * scale),
            int(box["x1"] * scale),
            int(box["bottom"] * scale),
        )
        image.crop(pixel).save(dest)
    finally:
        document.close()


def local_pdf(card: dict) -> Path | None:
    digest = card.get("file_sha256")
    size = card.get("file_bytes")
    if not digest or not LIBRARY.is_dir():
        return None
    paths = list(LIBRARY.glob("*.pdf"))
    if size:
        sized = [path for path in paths if path.stat().st_size == size]
        if sized:
            paths = sized
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


def canon_char(char: str) -> str:
    """Буква для сверки: тире и латинские двойники не рвут цитату, сноска * пропускается."""
    if char == "*":
        return ""
    mapped = char.translate(DASHES).translate(LOOKALIKES)
    return mapped


def canon_text(text: str) -> tuple[str, list[int]]:
    folded = " ".join(text.split())
    chars = []
    origins = []
    for index, char in enumerate(folded):
        mapped = canon_char(char)
        if not mapped:
            continue
        chars.append(mapped)
        origins.append(index)
    return "".join(chars), origins


def quote_span(file_text_raw: str, card_quote: str) -> str | None:
    """Кусок файла, который совпадает с цитатой карточки. Знак сноски * остаётся."""
    folded = " ".join(file_text_raw.split())
    file_canon, origins = canon_text(folded)
    quote_canon, _quote_origins = canon_text(card_quote)
    if not quote_canon:
        return None
    start = file_canon.find(quote_canon)
    if start < 0:
        return None
    stop = start + len(quote_canon) - 1
    return folded[origins[start] : origins[stop] + 1]


def confirm_quotes(notes: list[dict], documents: list[dict], cache: dict[Path, str]) -> None:
    index = cards_index(documents)
    for note in notes:
        card = index[norm_code(note["code"])]
        path = local_pdf(card)
        note["local_file"] = path.name if path else None
        note["hash_matches_card"] = path is not None
        if path is None:
            note["quote_in_file"] = False
            continue
        span = quote_span(file_text(path, cache), note["quote"])
        note["quote_in_file"] = span is not None
        if span is None:
            continue
        card_quote = " ".join(note["quote"].split())
        if span != card_quote:
            note["quote_in_card"] = card_quote
        note["quote"] = span


def spaced_title(lines: list[str], finding: dict) -> str | None:
    found = []
    target = titles.squeeze(finding["title_in_project"])
    for line in lines:
        for match in titles.CITATION.finditer(line):
            quoted = " ".join(match.group(2).split())
            if titles.squeeze(quoted) == target and " " in quoted:
                found.append(quoted)
    unique = sorted(set(found))
    if len(unique) == 1:
        return unique[0]
    return None


def same_finding(left: dict, right: dict) -> bool:
    return (
        left["code_in_project"] == right["code_in_project"]
        and titles.squeeze(left["title_in_project"]) == titles.squeeze(right["title_in_project"])
        and titles.squeeze(left["title_in_catalog"]) == titles.squeeze(right["title_in_catalog"])
    )


def line_pool(paths: list[Path]) -> list[str]:
    pool = []
    for path in paths:
        pool.extend(path.read_text(encoding="utf-8").splitlines())
    return pool


def remark(card: dict) -> str:
    place = f"Страница {card['page']}" if card.get("page") else "Страница PDF не привязана"
    sheet = card.get("sheet_name") or "имя листа не прочитано"
    number = card.get("sheet_number")
    sheet_bit = f"лист «{sheet}»"
    if number:
        sheet_bit += f", в штампе номер {number}"
    lines = [
        f"{place}, {sheet_bit}. "
        f"У шифра {card['code_in_project']} написано «{card['title_in_project']}». "
        f"Название этого шифра в карточке: «{card['title_in_catalog']}». "
        "В названии другое слово."
    ]
    if card["footnotes"]:
        lines.append("Сноски:")
        for note in card["footnotes"]:
            lines.append(
                f"{note['code']} п. {note['clause']}. «{note['quote']}» {note['edition_note']} {note['file_url']}"
            )
    else:
        lines.append("Сноски нет: у карточек, которые можно поставить к этой подмене, footnote_ok = false.")
    if card.get("cited_footnote_ok") is False:
        lines.append(
            f"Карточка {card['code_in_project']} в сноску не поставлена: footnote_ok = false."
        )
    return "\n".join(lines)


def self_check(documents: list[dict]) -> None:
    closed = [
        document
        for document in documents
        if document.get("replaced_by") and not document.get("footnote_ok")
    ]
    if len(closed) != 1:
        raise SystemExit("Самопроверка: нужна одна закрытая карточка с replaced_by")
    cited = closed[0]
    notes = select_footnotes(cited["code"], documents)
    if any(note["code"] == cited["code"] for note in notes):
        raise SystemExit("Самопроверка: закрытая карточка попала в сноску")
    if any(not note["footnote_ok"] for note in notes):
        raise SystemExit("Самопроверка: в сноске карточка без footnote_ok")
    replacement = cards_index(documents)[norm_code(cited["replaced_by"])]
    allowed = {replacement["code"]}
    blob = " ".join(quote for _clause, quote in clause_rows(replacement))
    allowed.update(document["code"] for document in mentioned_codes(blob, documents))
    got = {note["code"] for note in notes}
    if not got or not got <= allowed:
        raise SystemExit(f"Самопроверка сносок: {sorted(got)} вне {sorted(allowed)}")
    expected_rows = len(clause_rows(replacement))
    for document in mentioned_codes(blob, documents):
        if document.get("footnote_ok") and document["code"] != cited["code"]:
            expected_rows += len(clause_rows(document))
    if len(notes) != expected_rows:
        raise SystemExit(f"Самопроверка: пунктов в сноске {len(notes)}, ожидалось {expected_rows}")

    stamp = read_stamp(
        [
            word("Стадия", 400, 100, 430, 110),
            word("Лист", 450, 100, 470, 110),
            word("Листов", 490, 100, 530, 110),
            word("Р", 405, 120, 415, 134),
            word("3", 455, 120, 462, 134),
            word("Раздел", 250, 118, 310, 128),
            word("Имя", 250, 150, 280, 160),
            word("листа", 285, 150, 330, 160),
            word("ФорматА4", 400, 180, 460, 190),
            word("чужое", 20, 150, 70, 160),
            word("Лист", 40, 100, 60, 110),
        ]
    )
    if stamp["sheet_number"] != "3" or stamp["sheet_name_as_read"] != "Имя листа":
        raise SystemExit(f"Самопроверка штампа: {stamp}")
    restored = restore_spaces("Имялиста", ["1 Имя листа", "Имя листа", "Имя листа целиком"])
    if restored != "Имя листа":
        raise SystemExit(f"Самопроверка пробелов: {restored}")
    if restore_spaces("Имя. листа", ["Имя листа"]) != "Имя. листа":
        raise SystemExit("Самопроверка пробелов: строка с пробелом не должна терять точку")
    if restore_spaces("Имялиста", ["Имя листа", "Имяли ста"]) != "Имялиста":
        raise SystemExit("Самопроверка пробелов: два разных разбиения должны остаться как прочитано")
    box = citation_box(
        [
            word("СП60.13330.2020", 10, 10, 40, 18),
            word("СП99.13330.2099", 10, 20, 40, 28),
            word('"Другоеназвание"', 42, 20, 90, 28),
        ],
        "99.13330.2099",
        "Другоеназвание",
    )
    if box is None or box["x0"] != 10 or box["x1"] != 90:
        raise SystemExit(f"Самопроверка участка: {box}")
    span = quote_span(
        "предисловие. 1.1 Настоящий cвод правил до высоты* 75 м. дальше",
        "Настоящий свод правил до высоты 75 м.",
    )
    if span != "Настоящий cвод правил до высоты* 75 м.":
        raise SystemExit(f"Самопроверка цитаты: {span}")
    if quote_span("другой текст про высоту", "Настоящий свод правил до высоты 75 м.") is not None:
        raise SystemExit("Самопроверка цитаты: чужой текст совпал")


def main() -> int:
    payload = json.loads(CARDS_PATH.read_text(encoding="utf-8"))
    documents = payload["documents"]
    cards = titles.load_cards(CARDS_PATH)
    titles.self_check(cards)
    self_check(documents)

    line_paths = sorted(LINES_DIR.glob("*.lines.txt"))
    pool = line_pool(line_paths)
    pdf_hits = []
    line_hits = []
    sheets = []
    pdfs = sorted(INBOX.glob("*.pdf"))
    for pdf_path in pdfs:
        with pdfplumber.open(pdf_path) as document:
            for index, page in enumerate(document.pages, start=1):
                text = page.extract_text() or ""
                words = page.extract_words() or []
                stamp = read_stamp(words)
                if stamp["sheet_number"] and stamp["sheet_name_as_read"]:
                    sheets.append(
                        {
                            "pdf": pdf_path.name,
                            "page": index,
                            "sheet_number": stamp["sheet_number"],
                            "sheet_name": restore_spaces(stamp["sheet_name_as_read"], pool),
                            "sheet_name_as_read": stamp["sheet_name_as_read"],
                        }
                    )
                result = titles.check_text(text, cards, f"{pdf_path.name} стр. {index}")
                for finding in result["findings"]:
                    pdf_hits.append((pdf_path, index, page, words, finding))
    for path in line_paths:
        text = path.read_text(encoding="utf-8")
        result = titles.check_text(text, cards, path.name)
        for finding in result["findings"]:
            line_hits.append((path, finding))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    quote_cache: dict[Path, str] = {}
    built = []
    used_lines: set[Path] = set()
    for pdf_path, page_number, page, words, finding in pdf_hits:
        if page.rotation:
            raise SystemExit(f"Страница {page_number} повёрнута, координаты участка не сверены")
        stamp = read_stamp(words)
        sheet_name = restore_spaces(stamp["sheet_name_as_read"], pool)
        number = finding["code_in_project"].split()[-1]
        box = citation_box(words, number, finding["title_in_project"])
        if box is None:
            raise SystemExit(f"Строка шифра на странице {page_number} не попала в прямоугольник")
        crop = pad_box(box, page.width, page.height)
        image_name = f"region-page-{page_number:02d}.png"
        image_path = OUT_DIR / image_name
        crop_page(pdf_path, page_number, crop, image_path)
        attached = []
        for path, other in line_hits:
            if path in used_lines or not same_finding(finding, other):
                continue
            lines = path.read_text(encoding="utf-8").splitlines()
            if sheet_name and any(titles.squeeze(line) == titles.squeeze(sheet_name) for line in lines):
                attached.append(path.name)
                used_lines.add(path)
        title_shown = finding["title_in_project"]
        for path, other in line_hits:
            if path.name not in attached or not same_finding(finding, other):
                continue
            spaced = spaced_title(path.read_text(encoding="utf-8").splitlines(), finding)
            if spaced:
                title_shown = spaced
        index = cards_index(documents)
        cited = index.get(norm_code(finding["catalog_code"]))
        notes = select_footnotes(finding["catalog_code"], documents)
        confirm_quotes(notes, documents, quote_cache)
        missed = [note for note in notes if not note["quote_in_file"]]
        if missed:
            failed = ", ".join(f"{note['code']} п. {note['clause']}" for note in missed)
            raise SystemExit("Цитата не найдена в файле ФЦС: " + failed)
        card = {
            "page": page_number,
            "pdf": pdf_path.name,
            "sheet_name": sheet_name,
            "sheet_name_as_read": stamp["sheet_name_as_read"],
            "sheet_number": stamp["sheet_number"],
            "stamp_unread": stamp["unread"],
            "region": {
                "unit": "pt",
                "origin": "top-left",
                "words": {key: round(box[key], 1) for key in ("x0", "top", "x1", "bottom")},
                "crop": {key: round(crop[key], 1) for key in ("x0", "top", "x1", "bottom")},
                "image": str(image_path.relative_to(ROOT)),
            },
            "code_in_project": finding["code_in_project"],
            "title_in_project": title_shown,
            "title_in_pdf_text_layer": finding["title_in_project"],
            "title_in_catalog": finding["title_in_catalog"],
            "cited_footnote_ok": bool(cited and cited.get("footnote_ok")),
            "footnotes": notes,
            "dwg_text": attached,
        }
        card["remark"] = remark(card)
        built.append(card)

    for path, finding in line_hits:
        if path in used_lines:
            continue
        notes = select_footnotes(finding["catalog_code"], documents)
        confirm_quotes(notes, documents, quote_cache)
        card = {
            "page": None,
            "pdf": None,
            "sheet_name": None,
            "sheet_number": None,
            "stamp_unread": "находка есть в тексте DWG, страница PDF с тем же именем листа не привязана",
            "region": None,
            "code_in_project": finding["code_in_project"],
            "title_in_project": finding["title_in_project"],
            "title_in_pdf_text_layer": None,
            "title_in_catalog": finding["title_in_catalog"],
            "cited_footnote_ok": False,
            "footnotes": notes,
            "dwg_text": [path.name],
        }
        card["remark"] = remark(card)
        built.append(card)

    report = {"sheets": sheets, "cards": built}
    (OUT_DIR / "cards.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"cards {len(built)}")
    for card in built:
        print(
            "page",
            card["page"],
            "sheet",
            card["sheet_number"],
            card["sheet_name"],
            "footnotes",
            [(note["code"], note["clause"]) for note in card["footnotes"]],
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
