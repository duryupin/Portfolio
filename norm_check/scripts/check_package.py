# -*- coding: utf-8 -*-
"""Этап 8. Пять правил. У каждого есть пара «ошибка есть / ошибки нет».

Правила ищут вид факта и читают слова из файла. Шифр, марки и номера
помещений комплекта в образцы не зашиты. Норма, которой нет в карточке
с проверенной редакцией, в замечание не ставится.

1. Название при шифре СП. Вопросы 7–9. Уже было правилом этапа 4.
2. Имя листа в штампе и в ведомости основного комплекта. Вопрос 6.
3. Один шифр на листах этой ведомости. Вопрос 3.
4. Площадь на той же строке не является положительным числом. Вопрос 14.
   Это полнота, не цифра нормы. Строка без числа остаётся непрочитанной.
5. Наружный диаметр медной трубы из дюймового ряда. Вопрос 19.
   Длины одного диаметра у разных блоков не сравниваются: это не одна трасса.
   Число на 0,01 мм от размера дюймового ряда — ошибка записи.
   Пункт СП не ставится: карточки с этим рядом нет.
6. Нагрузка помещения в столбце с подписью RMTC. Вопрос 14.
   Ноль, отрицательное число или заполнитель «нет» — полнота, не цифра нормы.
   Обычное слово и одиночный дефис в этом столбце значением не считаются:
   заголовок таблицы попадает в ту же полосу. Паспортный столбец не читается.
7. Строка ведомости без штампа с тем же номером и тем же шифром. Вопрос 2.
8. Номер в строке «пом.» и список помещений. Вопрос 40.
   Одинаковый смысл — общий корень или пара фраз, которые уже есть в тексте.
9. Подпись длины участка. Вопрос 26. Нет подписи — одно замечание на сеть.
   Есть подпись — сравнение с длиной участка. Скорость не считается.

Доля ложных находок = ложные срабатывания на заведомо чистых текстах /
число чистых проверок. Ложное срабатывание останавливает прогон.

Запуск из корня norm_check:
  .venv\\Scripts\\python.exe scripts\\check_package.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pdfplumber

import check_sp_title as titles
import finding_card

ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "inbox"
LINES_DIR = ROOT / "reports" / "stage3"
OUT_DIR = ROOT / "reports" / "stage8"

CIPHER = re.compile(
    r"\d{1,4}/\d{2,4}-\d{2,4}-[0-9A-Za-zА-Яа-яЁё-]{1,16}(?:/[0-9A-Za-zА-Яа-яЁё]{1,8})?"
)
AREA_LINE = re.compile(
    r"(?i)площад\w*(?:\s+\S+){0,6}?\s+(-?\d+(?:[.,]\d+)?)\s*$"
)
PIPE_ANCHOR = re.compile(
    r"(?i)труб\w{0,8}\s+медн\w{0,12}|медн\w{0,12}\s+труб\w{0,8}"
)
PIPE_STOP = re.compile(r"(?i)изоляц|хладагент|дренаж|оборудование")
PIPE_PAIR = re.compile(
    r"[ØΦ⌀φ]\s*(\d+[.,]\d+)\s*(?:0\s+)?(?:м\.|м|m)\s*(\d+[.,]\d+)",
    re.IGNORECASE,
)
# Сотые доли миллиметра. 3/8 дюйма записывают 9,52, не 9,53.
TRADE_HUNDREDTHS = (635, 952, 1270, 1588, 1905, 2222, 2540, 2858, 3492, 4128)
LOAD_HEADER = re.compile(r"^RMTC(?:/RMH[C]?)?(?:\(W\))?$", re.IGNORECASE)
LOAD_SIGNED = re.compile(r"^-?\d+(?:[.,]\d+)?(?:/-?\d+(?:[.,]\d+)?)?$")
LOAD_BLANK = re.compile(r"(?i)^(нет|н/д|н\.д\.|na|n/a)$")
LOAD_UNIT = re.compile(r"(?i)^(Вт|кВт|W|kW)$")
LENGTH_GAP_MM = 50.0


def number(text: str) -> float:
    return float(text.replace(",", "."))


def hundredths(value: float) -> int:
    return int(round(value * 100 + 1e-6))


def trade_mm(code: int) -> str:
    whole, frac = divmod(code, 100)
    return f"{whole},{frac:02d}"


def same_name(left: str, right: str) -> bool:
    return titles.squeeze(left) == titles.squeeze(right) and bool(titles.squeeze(left))


def sheet_register(text: str) -> list[dict]:
    rows = []
    inside = False
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if "Ведомость рабочих чертежей" in line:
            inside = True
            continue
        if not inside:
            continue
        if line.lower().startswith("итого"):
            break
        match = re.fullmatch(r"(\d+)\s+(.+)", line)
        if match and not match.group(2).lower().startswith("наименование"):
            rows.append({"number": match.group(1), "name": match.group(2)})
    return rows


def lowest_cipher(words: list[dict]) -> str | None:
    found = [word for word in words if CIPHER.search(word["text"])]
    if not found:
        return None
    word = max(found, key=lambda item: item["top"])
    return CIPHER.search(word["text"]).group(0)


def read_sheets(pdf_path: Path) -> tuple[list[dict], list[dict]]:
    sheets = []
    register = []
    with pdfplumber.open(pdf_path) as document:
        for index, page in enumerate(document.pages, start=1):
            text = page.extract_text() or ""
            words = page.extract_words() or []
            if not register:
                register = sheet_register(text)
                for row in register:
                    row["page"] = index
            stamp = finding_card.read_stamp(words)
            if not stamp.get("sheet_number") or not stamp.get("sheet_name_as_read"):
                continue
            sheets.append(
                {
                    "pdf": pdf_path.name,
                    "page": index,
                    "number": stamp["sheet_number"],
                    "name": stamp["sheet_name_as_read"],
                    "cipher": lowest_cipher(words),
                }
            )
    return sheets, register


def rule_title(text: str, cards: dict, source: str) -> dict:
    result = titles.check_text(text, cards, source)
    checks = []
    for row in result["unchecked"]:
        checks.append({"ok": True, "kind": "unchecked", "detail": row["code"]})
    # Совместимое название в текст замечания не попадает: это чистая проверка.
    seen = {
        (row["code_in_project"], row["title_in_project"]) for row in result["findings"]
    }
    for match in titles.CITATION.finditer(text):
        code = f"СП {match.group(1)}"
        quoted = " ".join(match.group(2).split())
        if (code, quoted) in seen:
            continue
        card = cards.get(f"СП{match.group(1)}".upper())
        if card and titles.titles_compatible(quoted, card.get("title") or ""):
            checks.append({"ok": True, "kind": "negative", "detail": code})
    return {"findings": result["findings"], "checks": checks}


def main_ciphers(sheets: list[dict], register: list[dict]) -> set[str]:
    names = [row["name"] for row in register]
    return {
        sheet["cipher"]
        for sheet in sheets
        if sheet.get("cipher") and any(same_name(sheet["name"], name) for name in names)
    }


def rule_sheet(sheets: list[dict], register: list[dict]) -> dict:
    findings = []
    checks = []
    by_number = {row["number"]: row["name"] for row in register}
    owned = main_ciphers(sheets, register)
    for sheet in sheets:
        listed = by_number.get(sheet["number"])
        if listed is None:
            checks.append(
                {
                    "ok": True,
                    "kind": "unchecked",
                    "detail": f"лист {sheet['number']} не в ведомости",
                }
            )
            continue
        if same_name(sheet["name"], listed):
            checks.append(
                {
                    "ok": True,
                    "kind": "negative",
                    "detail": f"лист {sheet['number']} «{listed}»",
                }
            )
            continue
        if not sheet.get("cipher") or sheet["cipher"] not in owned:
            checks.append(
                {
                    "ok": True,
                    "kind": "unchecked",
                    "detail": f"лист {sheet['number']} к ведомости не приложен",
                }
            )
            continue
        findings.append(
            {
                "rule": "sheet_name",
                "question": 6,
                "page": sheet.get("page"),
                "quantity": "имя листа",
                "project_value": sheet["name"],
                "other_value": listed,
                "norm_value": None,
                "norm_note": "Сравнение штампа и ведомости комплекта. Норма не прикладывалась.",
                "direction": "при одном номере листа имена разные",
            }
        )
    return {"findings": findings, "checks": checks}


def rule_cipher(sheets: list[dict], register: list[dict]) -> dict:
    names = [row["name"] for row in register]
    main = [
        sheet
        for sheet in sheets
        if sheet.get("cipher") and any(same_name(sheet["name"], name) for name in names)
    ]
    findings = []
    checks = []
    ciphers = {sheet["cipher"] for sheet in main}
    if len(main) < 2:
        checks.append(
            {"ok": True, "kind": "unchecked", "detail": "меньше двух листов ведомости с шифром"}
        )
        return {"findings": findings, "checks": checks}
    if len(ciphers) == 1:
        checks.append(
            {
                "ok": True,
                "kind": "negative",
                "detail": f"листов {len(main)}, шифр один",
            }
        )
        return {"findings": findings, "checks": checks}
    findings.append(
        {
            "rule": "one_cipher",
            "question": 3,
            "page": main[0].get("page"),
            "quantity": "шифр листов ведомости",
            "project_value": ", ".join(sorted(ciphers)),
            "other_value": None,
            "norm_value": None,
            "norm_note": "Листы одной ведомости. Норма не прикладывалась.",
            "direction": "шифры разные",
        }
    )
    return {"findings": findings, "checks": checks}


def rule_area(text: str, page: int) -> dict:
    findings = []
    checks = []
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if not re.search(r"(?i)площад", line):
            continue
        match = AREA_LINE.search(line)
        if not match:
            checks.append(
                {"ok": True, "kind": "unchecked", "detail": "площадь названа, число на строке не прочитано"}
            )
            continue
        value = number(match.group(1))
        if value > 0:
            checks.append({"ok": True, "kind": "negative", "detail": f"площадь {value:g}"})
            continue
        shown = "0" if value == 0 else match.group(1).replace(".", ",")
        findings.append(
            {
                "rule": "zero_area",
                "question": 14,
                "page": page,
                "quantity": "площадь",
                "project_value": shown,
                "other_value": None,
                "norm_value": None,
                "norm_note": "На строке площади нет положительного числа. Это полнота комплекта, не цифра нормы.",
                "direction": "значение не заполнено" if value == 0 else "число меньше нуля",
                "line": line,
            }
        )
    return {"findings": findings, "checks": checks}


def load_word(text: str, x0: float, top: float, x1: float, bottom: float) -> dict:
    return {"text": text, "x0": x0, "top": top, "x1": x1, "bottom": bottom}


def overlaps_x(word: dict, span: tuple[float, float]) -> bool:
    return word["x0"] < span[1] and word["x1"] > span[0]


def load_is_bad(token: str) -> bool:
    if LOAD_UNIT.fullmatch(token) or not token:
        return False
    if LOAD_SIGNED.fullmatch(token):
        return any(number(part) <= 0 for part in token.split("/"))
    return bool(LOAD_BLANK.fullmatch(token))


def rule_room_load(pages: list[tuple[int, list[dict]]]) -> dict:
    """Столбец RMTC. Паспорт и другие столбцы этой таблицы не читает."""
    findings = []
    checks = []
    seen: set[tuple] = set()
    carried: list[tuple[float, float]] = []

    def take(word: dict, page: int) -> None:
        token = word["text"].replace(" ", "")
        if not load_is_bad(token) and not LOAD_SIGNED.fullmatch(token):
            return
        mark = (page, round(word["x0"], 1), round(word["top"], 1), token)
        if mark in seen:
            return
        seen.add(mark)
        if load_is_bad(token):
            findings.append({"page": page, "token": token})
            return
        checks.append({"ok": True, "kind": "negative", "detail": f"нагрузка помещения {token}"})

    for page, words in pages:
        if carried:
            for word in words:
                if any(overlaps_x(word, span) for span in carried):
                    take(word, page)
        headers = [word for word in words if LOAD_HEADER.fullmatch(word["text"].replace(" ", ""))]
        if not headers:
            carried = []
            continue
        spans = [(word["x0"] - 3, word["x1"] + 3) for word in headers]
        for header, span in zip(headers, spans):
            for word in words:
                if word["top"] <= header["top"] + 1:
                    continue
                if overlaps_x(word, span):
                    take(word, page)
        carried = spans

    grouped: dict[str, list[int]] = {}
    for row in findings:
        grouped.setdefault(row["token"], [])
        if row["page"] not in grouped[row["token"]]:
            grouped[row["token"]].append(row["page"])
    built = []
    for token, page_numbers in grouped.items():
        page_numbers = sorted(page_numbers)
        built.append(
            {
                "rule": "zero_room_load",
                "question": 14,
                "page": page_numbers[0],
                "pages": page_numbers,
                "quantity": "нагрузка помещения",
                "project_value": token,
                "other_value": None,
                "norm_value": None,
                "norm_note": (
                    "В столбце нагрузки помещения нет положительного числа. "
                    "Это полнота комплекта, не цифра нормы. Пункт СП не ставится."
                ),
                "direction": "число меньше нуля" if token[:1] == "-" and any(ch.isdigit() for ch in token) else "значение не заполнено",
            }
        )
    return {"findings": built, "checks": checks}


def pipe_records(text: str, page: int) -> list[dict]:
    flat = " ".join(text.split())
    records = []
    for anchor in PIPE_ANCHOR.finditer(flat):
        nxt = PIPE_ANCHOR.search(flat, anchor.end())
        end = anchor.end() + 700
        if nxt:
            end = min(end, nxt.start())
        window = flat[anchor.end() : end]
        stop = PIPE_STOP.search(window)
        if stop:
            window = window[: stop.start()]
        for pair in PIPE_PAIR.finditer(window):
            records.append(
                {
                    "page": page,
                    "diameter_mm": number(pair.group(1)),
                    "length_m": number(pair.group(2)),
                }
            )
    return records


def rule_diameter(records: list[dict]) -> dict:
    """Число на 0,01 мм от размера ряда — ошибка записи. Разные длины одного диаметра не ошибка."""
    findings = []
    checks = []
    wrong: dict[int, list[int]] = {}
    good: set[int] = set()
    for row in records:
        code = hundredths(row["diameter_mm"])
        if code in TRADE_HUNDREDTHS:
            good.add(code)
            continue
        nearest = min(TRADE_HUNDREDTHS, key=lambda item: abs(item - code))
        if abs(nearest - code) == 1:
            wrong.setdefault(code, [])
            if row["page"] not in wrong[code]:
                wrong[code].append(row["page"])
            continue
        checks.append(
            {
                "ok": True,
                "kind": "unchecked",
                "detail": f"Ø{trade_mm(code)} мм не рядом с дюймовым размером",
            }
        )
    for code in sorted(good):
        checks.append({"ok": True, "kind": "negative", "detail": f"Ø{trade_mm(code)} мм"})
    for code, pages in sorted(wrong.items()):
        nearest = min(TRADE_HUNDREDTHS, key=lambda item: abs(item - code))
        findings.append(
            {
                "rule": "pipe_diameter",
                "question": 19,
                "page": pages[0],
                "pages": pages,
                "quantity": "наружный диаметр медной трубы",
                "project_value": f"{trade_mm(code)} мм",
                "other_value": f"{trade_mm(nearest)} мм",
                "norm_value": None,
                "norm_note": (
                    "Дюймовый ряд: этот размер записывают как "
                    f"{trade_mm(nearest)} мм. Карточки с рядом в пакете нет, пункт СП не ставится."
                ),
                "direction": f"{trade_mm(code)} мм — не размер ряда, верно {trade_mm(nearest)} мм",
            }
        )
    return {"findings": findings, "checks": checks}


def rule_register(sheets: list[dict], register: list[dict]) -> dict:
    """Строка ведомости без штампа того же номера внутри того же шифра."""
    checks = []
    findings = []
    if not register:
        return {
            "findings": [],
            "checks": [{"ok": True, "kind": "unchecked", "detail": "ведомости нет"}],
        }
    owned = main_ciphers(sheets, register)
    present = {
        sheet["number"]
        for sheet in sheets
        if sheet.get("number") and sheet.get("cipher") in owned
    }
    for row in register:
        if row["number"] in present:
            checks.append(
                {"ok": True, "kind": "negative", "detail": f"ведомость лист {row['number']}"}
            )
            continue
        findings.append(
            {
                "rule": "register_sheet",
                "question": 2,
                "page": row.get("page"),
                "quantity": "номер листа в ведомости",
                "project_value": row["number"],
                "other_value": row["name"],
                "norm_value": None,
                "norm_note": "Строка ведомости есть, штампа с этим номером и этим шифром нет. Норма не прикладывалась.",
                "direction": "лист из ведомости не найден",
                "line": f"{row['number']} {row['name']}",
            }
        )
    return {"findings": findings, "checks": checks}


def meanings_match(context: str, room_name: str, pairs: list[tuple[str, str]], corpus: str) -> bool:
    import match_spaces

    if match_spaces.names_share(context, room_name) or match_spaces.names_share(room_name, context):
        return True
    folded_corpus = corpus.lower().replace("ё", "е")
    folded_context = (context or "").lower().replace("ё", "е")
    folded_room = (room_name or "").lower().replace("ё", "е")
    for left, right in pairs:
        one = left.lower().replace("ё", "е").strip()
        two = right.lower().replace("ё", "е").strip()
        if not one or not two or one not in folded_corpus or two not in folded_corpus:
            continue
        if (one in folded_context and two in folded_room) or (two in folded_context and one in folded_room):
            return True
    return False


def rule_rooms(
    mentions: list[dict],
    rooms: list[dict],
    plan_numbers: set[str],
    pairs: list[tuple[str, str]] | None = None,
) -> dict:
    """Номер «пом.» на плане. Одинаковый смысл при разных номерах — замечание."""
    pairs = pairs or []
    checks = []
    findings = []
    if not mentions:
        return {
            "findings": [],
            "checks": [{"ok": True, "kind": "unchecked", "detail": "строк пом. нет"}],
        }
    corpus = " ".join(
        [row.get("context") or "" for row in mentions]
        + [row.get("name") or "" for row in rooms]
    )
    for mention in mentions:
        numbers = [str(item) for item in mention.get("numbers") or []]
        context = mention.get("context") or ""
        matched = [
            room
            for room in rooms
            if room.get("name") and room.get("number") and meanings_match(context, room["name"], pairs, corpus)
        ]
        if matched:
            matched_numbers = {str(room["number"]) for room in matched}
            if any(number in matched_numbers for number in numbers):
                checks.append({"ok": True, "kind": "negative", "detail": "номер и название совпали"})
                continue
            findings.append(
                {
                    "rule": "room_number",
                    "question": 40,
                    "page": mention.get("page"),
                    "quantity": "номер помещения",
                    "project_value": numbers[0] if numbers else "",
                    "other_value": ", ".join(
                        f"{room['number']} {room['name']}" for room in matched
                    ),
                    "norm_value": None,
                    "norm_note": "Одинаковый смысл названия, номера разные. Норма не прикладывалась.",
                    "direction": "одинаковый смысл, номера разные",
                    "line": mention.get("line") or context,
                }
            )
            continue
        if not plan_numbers:
            checks.append({"ok": True, "kind": "unchecked", "detail": "плана нет"})
            continue
        missing = [number for number in numbers if number not in plan_numbers]
        if not missing:
            checks.append({"ok": True, "kind": "negative", "detail": f"пом. {', '.join(numbers)}"})
            continue
        findings.append(
            {
                "rule": "room_number",
                "question": 40,
                "page": mention.get("page"),
                "quantity": "номер помещения",
                "project_value": missing[0],
                "other_value": "номера плана",
                "norm_value": None,
                "norm_note": "Номер из строки «пом.» на плане не прочитан. Норма не прикладывалась.",
                "direction": "номера на плане нет",
                "line": mention.get("line") or context,
            }
        )
    return {"findings": findings, "checks": checks}


def lengths_disagree(measured: float, labeled: float) -> bool:
    return abs(measured - labeled) > max(LENGTH_GAP_MM, 0.02 * measured)


def rule_length(reports: list[dict]) -> dict:
    """Подпись длины с единицей. Нет подписи — одно замечание на сеть."""
    checks = []
    findings = []
    if not reports:
        return {
            "findings": [],
            "checks": [{"ok": True, "kind": "unchecked", "detail": "чертежа сети нет"}],
        }
    names = {"duct": "воздуховод", "pipe": "труба"}
    for system, title in names.items():
        segments = []
        files = []
        for report in reports:
            row = (report.get("systems") or {}).get(system) or {}
            own = list(row.get("segments") or [])
            if not own:
                continue
            files.append(report.get("file") or "")
            segments.extend(own)
        if not segments:
            checks.append({"ok": True, "kind": "unchecked", "detail": f"{title}: участков нет"})
            continue
        mismatches = [
            item
            for item in segments
            if item.get("compared") and lengths_disagree(item["length_mm"], item["length_label_mm"])
        ]
        compared = [item for item in segments if item.get("compared")]
        if mismatches:
            labeled = mismatches[0]["length_label_mm"]
            measured = mismatches[0]["length_mm"]
            findings.append(
                {
                    "rule": "segment_length",
                    "question": 26,
                    "drawing": True,
                    "files": [name for name in files if name],
                    "quantity": f"длина участка, {title}",
                    "project_value": f"{round(measured)} мм",
                    "other_value": f"{round(labeled)} мм",
                    "norm_value": None,
                    "norm_note": "Подпись длины и длина участка различаются. Пункт СП не ставится.",
                    "direction": "подпись и участок различаются",
                }
            )
            continue
        if compared:
            checks.append(
                {"ok": True, "kind": "negative", "detail": f"{title}: подпись длины совпала"}
            )
            continue
        findings.append(
            {
                "rule": "segment_length",
                "question": 26,
                "drawing": True,
                "files": [name for name in files if name],
                "quantity": f"длина участка, {title}",
                "project_value": f"участков {len(segments)}",
                "other_value": "подписей длины 0",
                "norm_value": None,
                "norm_note": (
                    "Участки сети прочитаны, подпись длины с единицей не прочитана. "
                    "Это полнота комплекта, не цифра нормы."
                ),
                "direction": "участки есть, подписи длины нет",
            }
        )
    return {"findings": findings, "checks": checks}


def expect(name: str, got: int, wanted: int) -> None:
    if got != wanted:
        raise SystemExit(f"Самопроверка {name}: {got}, ожидалось {wanted}")


def self_check(cards: dict) -> None:
    titles.self_check(cards)
    register = [
        {"number": "1", "name": "Общие данные"},
        {"number": "2", "name": "Вентиляция. План"},
    ]
    clean_sheets = [
        {"page": 1, "number": "1", "name": "Общиеданные", "cipher": "1/01-01-АБ"},
        {"page": 2, "number": "2", "name": "Вентиляция. План", "cipher": "1/01-01-АБ"},
        {
            "page": 8,
            "number": "1",
            "name": "Спецификация оборудования",
            "cipher": "1/01-01-АБ/СО",
        },
    ]
    sheet = rule_sheet(clean_sheets, register)
    expect("чистое имя листа", len(sheet["findings"]), 0)
    dirty_sheets = [
        {"page": 2, "number": "2", "name": "Вентиляция. Разрез", "cipher": "1/01-01-АБ"},
        {"page": 1, "number": "1", "name": "Общие данные", "cipher": "1/01-01-АБ"},
    ]
    dirty = rule_sheet(dirty_sheets, register)
    expect("чужое имя листа", len(dirty["findings"]), 1)
    expect("один шифр", len(rule_cipher(clean_sheets, register)["findings"]), 0)
    split = [
        {"page": 1, "number": "1", "name": "Общие данные", "cipher": "1/01-01-АБ"},
        {"page": 2, "number": "2", "name": "Вентиляция. План", "cipher": "9/02-02-ВГ"},
    ]
    expect("два шифра", len(rule_cipher(split, register)["findings"]), 1)
    area_zero = rule_area("Площадь здания (м2) 0", 1)
    area_full = rule_area("Площадь здания (м2) 48", 1)
    area_blank = rule_area("Площадь кондиционирования", 1)
    expect("нулевая площадь", len(area_zero["findings"]), 1)
    expect("ненулевая площадь", len(area_full["findings"]), 0)
    expect("площадь без числа", len(area_blank["findings"]), 0)
    load_zero = rule_room_load(
        [
            (
                1,
                [
                    load_word("RMTC/RMHC", 100, 10, 160, 18),
                    load_word("0/0", 110, 40, 130, 48),
                    load_word("2800/3200", 200, 40, 260, 48),
                ],
            ),
            (
                2,
                [
                    load_word("0/0", 112, 30, 132, 38),
                    load_word("1500/1600", 108, 70, 158, 78),
                ],
            ),
            (3, [load_word("RMTC/RMHC(W)", 10, 10, 80, 18)]),
            (4, [load_word("0/0", 110, 30, 130, 38)]),
        ]
    )
    expect("нулевая нагрузка помещения", len(load_zero["findings"]), 1)
    if load_zero["findings"][0]["pages"] != [1, 2] or load_zero["findings"][0]["project_value"] != "0/0":
        raise SystemExit(f"Самопроверка нагрузки: {load_zero['findings'][0]}")
    load_full = rule_room_load(
        [(1, [load_word("RMTC/RMH", 100, 10, 150, 18), load_word("1500/1600", 110, 40, 160, 48)])]
    )
    expect("ненулевая нагрузка помещения", len(load_full["findings"]), 0)
    if not load_full["checks"] or load_full["checks"][0]["kind"] != "negative":
        raise SystemExit("Самопроверка: ненулевая нагрузка не чистая проверка")
    load_aside = rule_room_load(
        [(1, [load_word("RMTC/RMH", 100, 10, 140, 18), load_word("0/0", 200, 40, 220, 48)])]
    )
    expect("ноль вне столбца нагрузки", len(load_aside["findings"]), 0)
    if area_full["checks"][0]["kind"] != "negative":
        raise SystemExit("Самопроверка: ненулевая площадь не чистая проверка")
    # Пара размеров ряда: верный и соседний на 0,01 мм. Это не число из проекта.
    off = rule_diameter(
        [
            {"page": 1, "diameter_mm": 9.52, "length_m": 10.1},
            {"page": 2, "diameter_mm": 9.53, "length_m": 26.8},
            {"page": 3, "diameter_mm": 6.35, "length_m": 4.1},
        ]
    )
    expect("диаметр на 0,01 мм от ряда", len(off["findings"]), 1)
    if off["findings"][0]["project_value"] != "9,53 мм" or off["findings"][0]["other_value"] != "9,52 мм":
        raise SystemExit(f"Самопроверка: перепутана сторона диаметра {off['findings'][0]}")
    expect("диаметр ряда", len(rule_diameter([{"page": 1, "diameter_mm": 9.52, "length_m": 1}])["findings"]), 0)
    expect(
        "другие диаметры ряда",
        len(
            rule_diameter(
                [
                    {"page": 1, "diameter_mm": 6.35, "length_m": 1},
                    {"page": 1, "diameter_mm": 12.7, "length_m": 1},
                    {"page": 1, "diameter_mm": 15.88, "length_m": 1},
                ]
            )["findings"]
        ),
        0,
    )
    noise = pipe_records(
        "Шаровый вентиль Ø15.88 шт. 4 Фильтросушитель Ø15,88 шт. 1 "
        "Теплоизоляция 6х6 м. 4.1",
        1,
    )
    expect("не труба", len(noise), 0)
    read = pipe_records(
        "Труба медная дюймовая Ø6.35 м. 4.1 "
        "Медная труба Φ6,35 0 m 5,75 0 Φ9,53 0 m 26,8 Хладагент R410A",
        2,
    )
    if len(read) != 3:
        raise SystemExit(f"Самопроверка чтения труб: {read}")
    expect("минус площади", len(rule_area("Площадь здания (м2) -5", 1)["findings"]), 1)
    expect("ведомость полная", len(rule_register(clean_sheets, register)["findings"]), 0)
    expect(
        "ведомость без штампа",
        len(rule_register(clean_sheets, register + [{"number": "9", "name": "Узел"}])["findings"]),
        1,
    )
    rooms = [{"number": "5", "name": "Кладовая"}]
    same = rule_rooms(
        [{"page": 1, "numbers": ["5"], "context": "кладовая (пом. 5)", "line": "кладовая (пом. 5)"}],
        rooms,
        {"5"},
    )
    expect("номер и название совпали", len(same["findings"]), 0)
    other_number = rule_rooms(
        [{"page": 1, "numbers": ["3"], "context": "кладовая (пом. 3)", "line": "кладовая (пом. 3)"}],
        rooms,
        {"3", "5"},
    )
    expect("одинаковый смысл, другой номер", len(other_number["findings"]), 1)
    missing_plan = rule_rooms(
        [{"page": 1, "numbers": ["3"], "context": "кладовая (пом. 3)", "line": "кладовая (пом. 3)"}],
        [],
        {"1"},
    )
    expect("номера на плане нет", len(missing_plan["findings"]), 1)
    unnamed = rule_rooms(
        [{"page": 1, "numbers": ["5"], "context": "уборная (пом. 5)", "line": "уборная (пом. 5)"}],
        [{"number": "5", "name": "Санузел"}],
        {"5"},
    )
    expect("смысл не подтверждён", len(unnamed["findings"]), 0)
    paired = rule_rooms(
        [{"page": 1, "numbers": ["2"], "context": "уборная (пом. 2)", "line": "уборная (пом. 2)"}],
        [{"number": "5", "name": "Санузел"}],
        {"2", "5"},
        [("уборная", "санузел")],
    )
    expect("пара фраз, номера разные", len(paired["findings"]), 1)
    rejected = rule_rooms(
        [{"page": 1, "numbers": ["5"], "context": "уборная (пом. 5)", "line": "уборная (пом. 5)"}],
        [{"number": "2", "name": "Санузел"}],
        {"5"},
        [("чужое", "санузел")],
    )
    expect("фразы нет в тексте", len(rejected["findings"]), 0)
    agreed = rule_rooms(
        [{"page": 1, "numbers": ["5"], "context": "уборная (пом. 5)", "line": "уборная (пом. 5)"}],
        [{"number": "5", "name": "Санузел"}],
        {"5"},
        [("уборная", "санузел")],
    )
    expect("пара фраз, номера совпали", len(agreed["findings"]), 0)
    no_label = rule_length(
        [
            {
                "file": "a.dwg",
                "systems": {"duct": {"segments": [{"length_mm": 1000, "compared": False}]}},
            }
        ]
    )
    expect("нет подписи длины", len(no_label["findings"]), 1)
    matched_length = rule_length(
        [
            {
                "file": "a.dwg",
                "systems": {
                    "duct": {
                        "segments": [
                            {"length_mm": 1000, "compared": True, "length_label_mm": 1020}
                        ]
                    }
                },
            }
        ]
    )
    expect("подпись длины близко", len(matched_length["findings"]), 0)
    far_length = rule_length(
        [
            {
                "file": "a.dwg",
                "systems": {
                    "pipe": {
                        "segments": [
                            {"length_mm": 1000, "compared": True, "length_label_mm": 2500}
                        ]
                    }
                },
            }
        ]
    )
    expect("подпись длины другая", len(far_length["findings"]), 1)
    expect("чертежа сети нет", len(rule_length([])["findings"]), 0)
    load_letter = rule_room_load(
        [(1, [load_word("RMTC/RMHC", 100, 10, 160, 18), load_word("нет", 110, 40, 130, 48)])]
    )
    expect("буква в столбце нагрузки", len(load_letter["findings"]), 1)
    load_minus = rule_room_load(
        [(1, [load_word("RMTC/RMHC", 100, 10, 160, 18), load_word("-5", 110, 40, 130, 48)])]
    )
    expect("минус в столбце нагрузки", len(load_minus["findings"]), 1)
    load_unit = rule_room_load(
        [(1, [load_word("RMTC/RMHC", 100, 10, 160, 18), load_word("Вт", 110, 40, 130, 48)])]
    )
    expect("единица в столбце нагрузки", len(load_unit["findings"]), 0)
    load_word_row = rule_room_load(
        [(1, [load_word("RMTC/RMHC", 100, 10, 160, 18), load_word("Нагрузка", 110, 40, 160, 48), load_word("-", 110, 70, 120, 78)])]
    )
    expect("слово и дефис не значение", len(load_word_row["findings"]), 0)


def where_text(finding: dict) -> str:
    pages = finding.get("pages") or [finding.get("page")]
    pages = [int(number) for number in pages if number]
    if not pages:
        files = [name for name in (finding.get("files") or []) if name]
        if len(files) == 1:
            return f"Чертёж {files[0]}."
        if len(files) == 2:
            return f"Чертежи {files[0]} и {files[1]}."
        if files:
            return "Чертежи " + ", ".join(files[:-1]) + f" и {files[-1]}."
    if len(pages) == 1:
        return f"Страница {pages[0]}."
    if not pages:
        return "Страница не прочитана."
    listed = [str(number) for number in pages]
    if len(listed) == 2:
        return f"Страницы {listed[0]} и {listed[1]}."
    return "Страницы " + ", ".join(listed[:-1]) + f" и {listed[-1]}."


def room_sides(pdf_path: Path) -> tuple[list[dict], set[str], list[dict]]:
    import match_spaces

    mentions = []
    plan_numbers: set[str] = set()
    with pdfplumber.open(pdf_path) as document:
        for index, page in enumerate(document.pages, start=1):
            text = page.extract_text() or ""
            words = page.extract_words() or []
            lines = text.splitlines()
            for row in match_spaces.pom_mentions(text):
                source = ""
                for line in lines:
                    if "пом" not in line.lower():
                        continue
                    if any(number in line for number in row["numbers"]):
                        source = " ".join(line.split())
                        break
                item = dict(row)
                item["page"] = index
                item["line"] = source or row["context"]
                mentions.append(item)
            if match_spaces.sheet_is_plan(text):
                plan_numbers.update(match_spaces.isolated_numbers(words))
    rooms = []
    for path in sorted(pdf_path.parent.glob("*.ifc")):
        for space in match_spaces.read_spaces(path):
            if space.get("number") and space.get("long_name"):
                rooms.append({"number": space["number"], "name": space["long_name"]})
    for path in sorted(pdf_path.parent.glob("*.dwg")):
        for row in match_spaces.dwg_area_numbers(path):
            plan_numbers.add(row["number"])
    return mentions, plan_numbers, rooms


def length_reports(pdf_path: Path) -> list[dict]:
    import ezdwg

    import segment_length

    reports = []
    for path in sorted(pdf_path.parent.glob("*.dwg")):
        doc = ezdwg.read(str(path))
        reports.append(segment_length.collect(doc, path))
    return reports


def evaluate_facts(pdf_path: Path) -> list[dict]:
    area_findings, pipe_rows, load_findings = read_package_pages(pdf_path)
    sheets, register = read_sheets(pdf_path)
    mentions, plan_numbers, rooms = room_sides(pdf_path)
    sheet = rule_sheet(sheets, register)
    cipher = rule_cipher(sheets, register)
    listed = rule_register(sheets, register)
    pipes = rule_diameter(pipe_rows)
    room = rule_rooms(mentions, rooms, plan_numbers)
    length = rule_length(length_reports(pdf_path))
    area = {"findings": area_findings, "checks": []}
    load = {"findings": load_findings, "checks": []}
    return [
        ("sheet_name", "6", sheet),
        ("one_cipher", "3", cipher),
        ("register_sheet", "2", listed),
        ("zero_area", "14", area),
        ("zero_room_load", "14", load),
        ("pipe_diameter", "19", pipes),
        ("room_number", "40", room),
        ("segment_length", "26", length),
    ]


def read_package_pages(pdf_path: Path) -> tuple[list[dict], list[dict], list[dict]]:
    area_findings = []
    pipe_rows = []
    load_pages = []
    with pdfplumber.open(pdf_path) as document:
        for index, page in enumerate(document.pages, start=1):
            text = page.extract_text() or ""
            area_findings.extend(rule_area(text, index)["findings"])
            pipe_rows.extend(pipe_records(text, index))
            load_pages.append((index, page.extract_words() or []))
    return area_findings, pipe_rows, rule_room_load(load_pages)["findings"]


def fact_findings(pdf_path: Path) -> list[dict]:
    """Замечания правил пакета, кроме названия СП. Чистые пары сюда не входят."""
    findings = []
    for _rule_id, _question, result in evaluate_facts(pdf_path):
        findings.extend(result["findings"])
    return findings


def negative_count(checks: list[dict]) -> int:
    return sum(1 for item in checks if item["kind"] == "negative")


def clean_title_checks(cards: dict, findings: list[dict]) -> tuple[list[dict], int]:
    false_hits = 0
    checks = []
    for finding in findings:
        fixed = titles.corrected_text(
            f"{finding['code_in_project']} «{finding['title_in_project']}»",
            [finding],
        )
        again = rule_title(fixed, cards, "название из карточки")
        false_hits += len(again["findings"])
        checks.extend(item for item in again["checks"] if item["kind"] == "negative")
        if again["findings"]:
            raise SystemExit("Чистая замена названия снова дала замечание")
    return checks, false_hits


def main() -> int:
    cards = titles.load_cards(titles.CARDS_PATH)
    self_check(cards)
    pdfs = sorted(INBOX.glob("*.pdf"))
    if len(pdfs) != 1:
        raise SystemExit(f"Ожидался один PDF в inbox, найдено: {len(pdfs)}")
    pdf_path = pdfs[0]
    false_hits = 0
    title_findings = []
    title_checks = []
    area_findings = []
    area_checks = []
    pipe_rows = []
    load_pages = []
    with pdfplumber.open(pdf_path) as document:
        for index, page in enumerate(document.pages, start=1):
            text = page.extract_text() or ""
            title = rule_title(text, cards, f"{pdf_path.name} стр. {index}")
            title_findings.extend(title["findings"])
            title_checks.extend(title["checks"])
            area = rule_area(text, index)
            area_findings.extend(area["findings"])
            area_checks.extend(area["checks"])
            pipe_rows.extend(pipe_records(text, index))
            load_pages.append((index, page.extract_words() or []))
    room_load = rule_room_load(load_pages)
    for path in sorted(LINES_DIR.glob("*.lines.txt")):
        title = rule_title(path.read_text(encoding="utf-8"), cards, path.name)
        title_findings.extend(title["findings"])
        title_checks.extend(title["checks"])
    clean_checks, clean_false = clean_title_checks(cards, title_findings)
    false_hits += clean_false
    title_checks.extend(clean_checks)

    sheets, register = read_sheets(pdf_path)
    sheet = rule_sheet(sheets, register)
    cipher = rule_cipher(sheets, register)
    pipes = rule_diameter(pipe_rows)
    evaluated = {rule_id: result for rule_id, _question, result in evaluate_facts(pdf_path)}
    if evaluated["zero_area"]["findings"] != area_findings:
        raise SystemExit("Площадь в двух проходах различается")
    if evaluated["zero_room_load"]["findings"] != room_load["findings"]:
        raise SystemExit("Нагрузка в двух проходах различается")
    if evaluated["pipe_diameter"]["findings"] != pipes["findings"]:
        raise SystemExit("Диаметр в двух проходах различается")
    if evaluated["sheet_name"]["findings"] != sheet["findings"]:
        raise SystemExit("Имя листа в двух проходах различается")
    if evaluated["one_cipher"]["findings"] != cipher["findings"]:
        raise SystemExit("Шифр в двух проходах различается")
    area_clean = rule_area("Площадь участка (м2) 48", 0)
    false_hits += len(area_clean["findings"])
    if area_clean["findings"]:
        raise SystemExit("Ненулевая площадь снова дала замечание")
    area_checks.extend(area_clean["checks"])
    clean_diameter = rule_diameter(
        [{"page": 0, "diameter_mm": 9.52, "length_m": 1.0}]
    )
    false_hits += len(clean_diameter["findings"])
    if clean_diameter["findings"]:
        raise SystemExit("Диаметр 9,52 снова дал замечание")
    load_clean = rule_room_load(
        [
            (
                0,
                [
                    load_word("RMTC/RMHC", 10, 0, 50, 8),
                    load_word("1500/1700", 12, 20, 48, 28),
                ],
            )
        ]
    )
    false_hits += len(load_clean["findings"])
    if load_clean["findings"]:
        raise SystemExit("Ненулевая нагрузка помещения снова дала замечание")

    rules = [
        {
            "id": "sp_title",
            "question": "7-9",
            "findings": title_findings,
            "negative_checks": negative_count(title_checks),
            "unchecked": sum(1 for item in title_checks if item["kind"] == "unchecked"),
        },
        {
            "id": "sheet_name",
            "question": "6",
            "findings": sheet["findings"],
            "negative_checks": negative_count(sheet["checks"]),
            "unchecked": sum(1 for item in sheet["checks"] if item["kind"] == "unchecked"),
        },
        {
            "id": "one_cipher",
            "question": "3",
            "findings": cipher["findings"],
            "negative_checks": negative_count(cipher["checks"]),
            "unchecked": sum(1 for item in cipher["checks"] if item["kind"] == "unchecked"),
        },
        {
            "id": "register_sheet",
            "question": "2",
            "findings": evaluated["register_sheet"]["findings"],
            "negative_checks": negative_count(evaluated["register_sheet"]["checks"]),
            "unchecked": sum(1 for item in evaluated["register_sheet"]["checks"] if item["kind"] == "unchecked"),
        },
        {
            "id": "zero_area",
            "question": "14",
            "findings": area_findings,
            "negative_checks": negative_count(area_checks),
            "unchecked": sum(1 for item in area_checks if item["kind"] == "unchecked"),
        },
        {
            "id": "zero_room_load",
            "question": "14",
            "findings": room_load["findings"],
            "negative_checks": negative_count(room_load["checks"]) + negative_count(load_clean["checks"]),
            "unchecked": sum(1 for item in room_load["checks"] if item["kind"] == "unchecked"),
        },
        {
            "id": "pipe_diameter",
            "question": "19",
            "findings": pipes["findings"],
            "negative_checks": negative_count(pipes["checks"]) + negative_count(clean_diameter["checks"]),
            "unchecked": sum(1 for item in pipes["checks"] if item["kind"] == "unchecked"),
            "withdrawn": "Длины одного диаметра у разных блоков замечанием не являются.",
        },
        {
            "id": "room_number",
            "question": "40",
            "findings": evaluated["room_number"]["findings"],
            "negative_checks": negative_count(evaluated["room_number"]["checks"]),
            "unchecked": sum(1 for item in evaluated["room_number"]["checks"] if item["kind"] == "unchecked"),
        },
        {
            "id": "segment_length",
            "question": "26",
            "findings": evaluated["segment_length"]["findings"],
            "negative_checks": negative_count(evaluated["segment_length"]["checks"]),
            "unchecked": sum(1 for item in evaluated["segment_length"]["checks"] if item["kind"] == "unchecked"),
        },
    ]
    negatives = sum(rule["negative_checks"] for rule in rules)
    if negatives == 0:
        raise SystemExit("Чистых проверок нет, долю ложных посчитать нельзя")
    report = {
        "pdf": pdf_path.name,
        "register": register,
        "sheets": sheets,
        "false_findings": false_hits,
        "negative_checks": negatives,
        "false_positive_rate": false_hits / negatives,
        "rules": rules,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "package.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"false {false_hits} negative {negatives} rate {report['false_positive_rate']:.4f}"
    )
    for rule in rules:
        print(
            rule["id"],
            "findings",
            len(rule["findings"]),
            "negative",
            rule["negative_checks"],
            "unchecked",
            rule["unchecked"],
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
