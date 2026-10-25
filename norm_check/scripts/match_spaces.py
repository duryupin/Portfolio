# -*- coding: utf-8 -*-
"""Номера помещений IFC и номера на планах PDF и DWG.

Сетки не строит, пересечения не ищет, выход сети за помещение не считает.
Имя, адрес и шифр объекта в код не входят.

Запуск из корня norm_check:
  .venv\\Scripts\\python.exe scripts\\match_spaces.py путь\\к\\файлу.ifc
  .venv\\Scripts\\python.exe scripts\\match_spaces.py путь\\к\\папке\\с\\одним.ifc
"""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

import ezdwg
import ifcopenshell
import pdfplumber

sys.path.insert(0, str(Path(__file__).resolve().parent))
from read_dwg import clean_mtext, layer_name_map  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "inbox"
OUT_DIR = ROOT / "reports" / "spaces"

ROOM_NUMBER = re.compile(r"^\d{1,2}$")
POM = re.compile(r"пом\.?\s*((?:\d+\s*[,;]\s*)*\d+)", re.IGNORECASE)
WORD = re.compile(r"[А-Яа-яA-Za-z]{4,}")
ISOLATED_PT = 22.0


def text(value) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(str(value).split())
    return cleaned or None


def stem(word: str) -> str:
    cleaned = re.sub(r"[^а-яa-z]", "", word.lower().replace("ё", "е"))
    return cleaned[:4]


def names_share(left: str, right: str) -> bool:
    """Общий корень имени, в том числе в слитной строке PDF."""
    folded = (right or "").lower().replace("ё", "е")
    for word in WORD.findall(left or ""):
        item = stem(word)
        if len(item) < 4 or item == "поме":
            continue
        if item in folded:
            return True
    return False


def read_spaces(path: Path) -> list[dict]:
    model = ifcopenshell.open(str(path))
    rows = []
    for space in model.by_type("IfcSpace"):
        reference = None
        for rel in getattr(space, "IsDefinedBy", []) or []:
            if not rel.is_a("IfcRelDefinesByProperties"):
                continue
            pset = rel.RelatingPropertyDefinition
            if not pset.is_a("IfcPropertySet") or pset.Name != "Pset_SpaceCommon":
                continue
            for prop in pset.HasProperties or []:
                if prop.Name != "Reference":
                    continue
                nominal = getattr(prop, "NominalValue", None)
                reference = text(getattr(nominal, "wrappedValue", nominal))
        name = text(getattr(space, "Name", None)) or ""
        rows.append(
            {
                "global_id": space.GlobalId,
                "number": name if ROOM_NUMBER.match(name) else None,
                "name": name,
                "long_name": text(getattr(space, "LongName", None)),
                "reference": reference,
            }
        )
    return rows


def dwg_area_numbers(path: Path) -> list[dict]:
    doc = ezdwg.read(str(path))
    names = layer_name_map(path)
    rows = []
    for ent in doc.modelspace().query("MTEXT"):
        handle = ent.dxf.get("layer_handle")
        layer = names.get(int(handle)) if isinstance(handle, int) else None
        if not layer:
            continue
        upper = layer.upper()
        if "AREA" not in upper or "IDEN" not in upper:
            continue
        value = clean_mtext(ent.dxf.get("text") or "")
        if not ROOM_NUMBER.match(value):
            continue
        insert = ent.dxf.get("insert") or (0, 0, 0)
        rows.append({"number": value, "x": round(insert[0], 1), "y": round(insert[1], 1)})
    return rows


def isolated_numbers(words: list[dict]) -> list[str]:
    """Голое число — номер на плане, если рядом нет другого слова.

    «1 Все», «10 мм» и число у «кв.» или «Лист» номером помещения не являются.
    """
    centers = []
    for word in words:
        centers.append(
            (
                word["text"],
                (float(word["x0"]) + float(word["x1"])) / 2.0,
                (float(word["top"]) + float(word["bottom"])) / 2.0,
            )
        )
    found = []
    for index, (value, x_pos, y_pos) in enumerate(centers):
        if not ROOM_NUMBER.match(value):
            continue
        nearest = None
        for other_index, (_other, ox, oy) in enumerate(centers):
            if other_index == index:
                continue
            distance = math.hypot(ox - x_pos, oy - y_pos)
            if nearest is None or distance < nearest:
                nearest = distance
        if nearest is not None and nearest < ISOLATED_PT:
            continue
        found.append(value)
    return found


def sheet_is_plan(page_text: str) -> bool:
    """Лист — план, если в штампе у слова «Формат» стоит «План».

    Ведомость листов выше штампа и страница без формата планом не считаются.
    """
    lines = [line.strip() for line in page_text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if "формат" not in line.lower():
            continue
        window = " ".join(lines[max(0, index - 3) : index + 1]).lower()
        return "план" in window
    return False


def pom_mentions(page_text: str) -> list[dict]:
    lines = page_text.splitlines()
    rows = []
    for index, line in enumerate(lines):
        for match in POM.finditer(line):
            numbers = re.findall(r"\d+", match.group(1))
            before = line[: match.start()]
            context = line.strip()
            if not WORD.search(before):
                previous = ""
                for earlier in reversed(lines[:index]):
                    if earlier.strip():
                        previous = earlier.strip()
                        break
                if previous:
                    context = previous + " " + context
            rows.append(
                {
                    "numbers": numbers,
                    "context": " ".join(context.split()),
                }
            )
    return rows


def bind_number(number: str, spaces: list[dict], dwg_rows: list[dict], pdf_rows: list[dict], mentions: list[dict]) -> dict:
    ifc_rows = [row for row in spaces if row["number"] == number]
    dwg_hits = [row for row in dwg_rows if row["number"] == number]
    pdf_hits = [row for row in pdf_rows if row["number"] == number]
    said = []
    for mention in mentions:
        if number not in mention["numbers"]:
            continue
        long_names = [row["long_name"] or "" for row in ifc_rows]
        shared = [name for name in long_names if names_share(name, mention["context"])]
        cited = set()
        for pom in POM.finditer(mention["context"]):
            cited.update(re.findall(r"\d+", pom.group(1)))
        others = [
            {"number": row["number"], "long_name": row["long_name"]}
            for row in spaces
            if row["number"] not in cited and row.get("long_name") and names_share(row["long_name"], mention["context"])
        ]
        said.append(
            {
                "page": mention["page"],
                "context": mention["context"],
                "long_names_sharing_stem": shared,
                "other_spaces_sharing_stem": others,
            }
        )
    dwg_by_file: dict[str, int] = {}
    for row in dwg_hits:
        dwg_by_file[row["file"]] = dwg_by_file.get(row["file"], 0) + 1
    pdf_by_page: dict[str, int] = {}
    for row in pdf_hits:
        key = str(row["page"])
        pdf_by_page[key] = pdf_by_page.get(key, 0) + 1
    return {
        "number": number,
        "ifc": [
            {"global_id": row["global_id"], "long_name": row["long_name"], "reference": row["reference"]}
            for row in ifc_rows
        ],
        "dwg_by_file": dwg_by_file,
        "pdf_by_page": pdf_by_page,
        "mentions": said,
    }


def self_check() -> list[str]:
    errors = []
    words = [
        {"text": "4", "x0": 100, "x1": 108, "top": 100, "bottom": 110},
        {"text": "1", "x0": 10, "x1": 16, "top": 10, "bottom": 18},
        {"text": "Все", "x0": 24, "x1": 40, "top": 10, "bottom": 18},
        {"text": "10", "x0": 200, "x1": 212, "top": 40, "bottom": 50},
        {"text": "мм", "x0": 214, "x1": 226, "top": 40, "bottom": 50},
        {"text": "2", "x0": 300, "x1": 308, "top": 400, "bottom": 410},
        {"text": "Лист", "x0": 312, "x1": 332, "top": 400, "bottom": 410},
    ]
    got = isolated_numbers(words)
    if got != ["4"]:
        errors.append(f"изолированный номер разобран как {got}")
    mentions = pom_mentions("Вытяжная система кухни (пом. 5)\n(пом. 4, 8)")
    numbers = [number for row in mentions for number in row["numbers"]]
    if numbers != ["5", "4", "8"]:
        errors.append(f"пом. разобрано как {numbers}")
    if not names_share("Кухня", "Вытяжная система кухни (пом. 5)"):
        errors.append("корень «кухн» не совпал")
    wardrobe = pom_mentions("Вытяжнаясистемагардеробных\n(пом. 4, 8)")
    if "гардеробных" not in wardrobe[0]["context"]:
        errors.append("строка над пом. не попала в контекст")
    register = "Ведомость\nВентиляция. План\nстрока\nстрока\nстрока\nР 1\nОбщие данные\nФормат А4"
    if sheet_is_plan(register):
        errors.append("ведомость над штампом принята за план")
    if not sheet_is_plan("Р 2\nВентиляция. План\nФормат А3"):
        errors.append("штамп плана не узнан")
    if sheet_is_plan("Температура в помещении 27"):
        errors.append("страница без формата принята за план")
    if names_share("Ванная комната", "гардеробных (пом. 8)"):
        errors.append("ванная совпала с гардеробом")
    if names_share("Помещение", "в помещениях: кухня-гостиная"):
        errors.append("слово «помещениях» совпало с именем Помещение")
    if not names_share("Гардероб", "Вытяжнаясистемагардеробных (пом. 4, 8)"):
        errors.append("гардероб не найден в слитной строке")
    spaces = [
        {"number": "4", "global_id": "a", "long_name": "Постирочная", "reference": "Постирочная 4"},
        {"number": "4", "global_id": "b", "long_name": "Помещение", "reference": "Помещение 4"},
    ]
    bound = bind_number(
        "4",
        spaces,
        [{"number": "4", "file": "2.dwg"}, {"number": "4", "file": "2.dwg"}],
        [{"number": "4", "page": 4}, {"number": "4", "page": 4}],
        [],
    )
    if len(bound["ifc"]) != 2 or bound["pdf_by_page"] != {"4": 2} or bound["dwg_by_file"] != {"2.dwg": 2}:
        errors.append("два помещения с одним номером склеились")
    return errors


def main() -> int:
    errors = self_check()
    if errors:
        raise SystemExit("Самопроверка не сошлась: " + "; ".join(errors))
    if len(sys.argv) != 2:
        raise SystemExit("Нужен путь к IFC или к папке с одним IFC")
    source = Path(sys.argv[1])
    if source.is_dir():
        files = [path for path in source.glob("*.ifc") if path.is_file()]
        if len(files) != 1:
            raise SystemExit(f"В папке нужен один IFC, найдено {len(files)}")
        source = files[0]
    if not source.is_file():
        raise SystemExit(f"Нет файла: {source}")
    spaces = read_spaces(source)
    dwg_files = []
    for path in sorted(INBOX.glob("*.dwg"), key=lambda item: item.name):
        tags = dwg_area_numbers(path)
        dwg_files.append({"file": path.name, "tags": tags})
        print(f"{path.name} area {len(tags)}")
    pdfs = sorted(INBOX.glob("*.pdf"))
    if len(pdfs) != 1:
        raise SystemExit(f"В inbox нужен один PDF, найдено {len(pdfs)}")
    plan_pages = []
    mentions = []
    with pdfplumber.open(pdfs[0]) as doc:
        for index, page in enumerate(doc.pages, start=1):
            page_text = page.extract_text() or ""
            for row in pom_mentions(page_text):
                row["page"] = index
                mentions.append(row)
            if not sheet_is_plan(page_text):
                continue
            words = page.extract_words() or []
            numbers = isolated_numbers(words)
            plan_pages.append({"page": index, "numbers": numbers})
            print(f"pdf {index} plan {numbers}")
    dwg_rows = [tag | {"file": item["file"]} for item in dwg_files for tag in item["tags"]]
    pdf_rows = [
        {"number": number, "page": item["page"]}
        for item in plan_pages
        for number in item["numbers"]
    ]
    numbers = sorted(
        {row["number"] for row in spaces if row["number"]}
        | {row["number"] for row in dwg_rows}
        | {row["number"] for row in pdf_rows}
        | {number for row in mentions for number in row["numbers"]},
        key=lambda value: int(value),
    )
    bound = [bind_number(number, spaces, dwg_rows, pdf_rows, mentions) for number in numbers]
    payload = {
        "ifc_file": source.name,
        "geometry": "не снималась",
        "clashes": "не искались",
        "room_exit": "не считался",
        "velocity": None,
        "self_check": "ok",
        "spaces": spaces,
        "dwg": dwg_files,
        "pdf_plans": plan_pages,
        "pom_mentions": mentions,
        "by_number": bound,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "match.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"spaces {len(spaces)} numbers {len(bound)} wrote {OUT_DIR / 'match.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
