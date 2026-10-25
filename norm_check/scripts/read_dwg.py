# -*- coding: utf-8 -*-
"""Этап 3: слои, блоки и тексты одного или всех DWG из inbox.

Геометрию с нормами не сравнивает. Скорости и сечения не считает.
Читатель — пакет ezdwg в .venv этого проекта, не установленный в систему
конвертер и не AutoCAD. Постановка ODA File Converter сюда не входит.

Запуск из корня norm_check:
  .venv\\Scripts\\python.exe scripts\\read_dwg.py
  .venv\\Scripts\\python.exe scripts\\read_dwg.py 1.dwg
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

import ezdwg

ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "inbox"
OUT_DIR = ROOT / "reports" / "stage3"

UNICODE_ESCAPE = re.compile(r"\\?U\+([0-9A-Fa-f]{4})")
FONT_CODE = re.compile(r"\\[FfHhWwCcAaQqTt][^;\\]*;")
PARA_CODE = re.compile(r"\\p[^;]*;")
TOGGLE = re.compile(r"\\[LlOoKkPp~]")
BRACES = re.compile(r"\\[{}]")
PERCENT = {"%%d": "°", "%%p": "±", "%%c": "Ø", "%%%": "%"}

KEEP_EXTRA = set(" .,;:!?-–—()[]«»\"'/\\№%+*=°±Ø_<>")
BLOCK_ID_SUFFIX = re.compile(r"-\d+-О_\S+$")
CYR_PHRASE = re.compile(r"[А-ЯЁ][а-яё]+(?:\s+[А-Яа-яЁё][а-яё]+)+")
LEGEND_WORDS = ("воздуховод", "диффузор", "решетк", "вставка", "клапан")


def clean_mtext(value: str) -> str:
    text = value or ""
    for src, dst in PERCENT.items():
        text = text.replace(src, dst)
    text = UNICODE_ESCAPE.sub(lambda match: chr(int(match.group(1), 16)), text)
    text = FONT_CODE.sub("", text)
    text = PARA_CODE.sub(" ", text)
    text = TOGGLE.sub("", text)
    text = BRACES.sub("", text)
    kept = []
    for char in text:
        if (
            "А" <= char <= "я"
            or char in "Ёё"
            or "A" <= char <= "Z"
            or "a" <= char <= "z"
            or char.isdigit()
            or char in KEEP_EXTRA
        ):
            kept.append(char)
    return " ".join("".join(kept).split())


def lines_from_mtext(rows: list[tuple]) -> list[str]:
    items: list[tuple[float, float, float, str]] = []
    for row in rows:
        text = clean_mtext(row[1] or "")
        if not text:
            continue
        insert = row[2]
        height = row[6] or 2.5
        items.append((float(insert[1]), float(insert[0]), float(height), text))
    items.sort(key=lambda item: (-item[0], item[1]))
    grouped: list[list] = []
    for y_pos, x_pos, height, text in items:
        if grouped and abs(grouped[-1][0] - y_pos) <= max(0.8, height * 0.45):
            grouped[-1][1].append((x_pos, text))
        else:
            grouped.append([y_pos, [(x_pos, text)]])
    result = []
    for _y_pos, parts in grouped:
        parts.sort()
        result.append(" ".join(part for _x, part in parts))
    return result


def count_entities(layout) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for entity in layout.query():
        counts[entity.dxftype] += 1
    return dict(counts)


def universal_from_block(name: str) -> str | None:
    """Имя изделия из имени блока, без марки на выноске.

    В блоке Revit марка часто стоит префиксом через «_», а наименование
    написано словами: «Щелевой диффузор линейный». Если префикса нет,
    имя — вся часть семейства, как она лежит в DWG. Коды FP_ и «О_Вент»
    именем не считаются.
    """
    if not name or name.startswith("*"):
        return None
    trimmed = BLOCK_ID_SUFFIX.sub("", name).strip()
    trimmed = re.sub(r"\s+копия(?:\s+\d+)?$", "", trimmed).strip()
    family, _sep, _type_name = trimmed.partition(" - ")
    family = family.strip()
    if re.fullmatch(r"FP_\d+-О_(?:Вент|Конд)", family):
        return None
    if re.fullmatch(r"Семейство\d+", family):
        return None
    if re.fullmatch(r"-?\d+|_?\d+_|-V\d+", family):
        return None
    if "_" in family:
        phrases = CYR_PHRASE.findall(family)
        if phrases:
            return phrases[-1]
    words = re.findall(r"[А-Яа-яЁё]{4,}", family)
    if not words or set(words) <= {"Вент", "Конд"}:
        return None
    return family


def universal_names(inserts: dict[str, int]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for block_name, count in inserts.items():
        title = universal_from_block(block_name)
        if not title:
            continue
        row = grouped.get(title)
        if row is None:
            grouped[title] = {"name": title, "inserts": count, "blocks": [block_name]}
        else:
            row["inserts"] += count
            row["blocks"].append(block_name)
    return sorted(grouped.values(), key=lambda row: row["name"])


def legend_names(lines: list[str]) -> list[str]:
    found = []
    seen = set()
    for line in lines:
        chunk: list[str] = []

        def emit() -> None:
            text = " ".join(chunk).strip()
            chunk.clear()
            if not text or text in seen:
                return
            folded = text.lower()
            if not any(word in folded for word in LEGEND_WORDS):
                return
            if "теплоизоляц" in folded:
                return
            seen.add(text)
            found.append(text)

        for token in line.split():
            if token in {"-", "–"}:
                continue
            if any(char.isdigit() for char in token) or any(
                mark in token for mark in ("РЭД", "SIR", "КСД", "AVE", "AVW")
            ):
                emit()
                continue
            chunk.append(token)
        emit()
    return found


def insert_names(layout) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for entity in layout.query("INSERT MINSERT"):
        name = entity.dxf.get("name") or ""
        if name:
            counts[name] += 1
    return dict(counts)


def readable_name(name: str) -> bool:
    if not name:
        return False
    for char in name:
        if char.isascii() or "\u0400" <= char <= "\u04FF":
            continue
        return False
    return True


def layer_name_map(path: Path) -> dict[int, str]:
    names = {}
    for handle, name in ezdwg.raw.decode_layer_names(str(path)):
        if not readable_name(name):
            continue
        names[int(handle)] = name
    return names


def bind_layers(layout, names: dict[int, str]) -> tuple[int, int, list[dict]]:
    """Имя слоя берётся из layer_handle примитива, не из пустого поля layer."""
    counts: Counter[str] = Counter()
    unbound = 0
    for entity in layout.query():
        handle = entity.dxf.get("layer_handle")
        name = names.get(int(handle)) if isinstance(handle, int) else None
        if not name:
            unbound += 1
            continue
        counts[name] += 1
    rows = [{"name": name, "entities": count} for name, count in counts.most_common()]
    return sum(counts.values()), unbound, rows


def layer_rows(path: Path) -> tuple[list[dict], int]:
    readable = []
    seen = set()
    undecoded = 0
    for handle, name in ezdwg.raw.decode_layer_names(str(path)):
        if not readable_name(name):
            undecoded += 1
            continue
        if name in seen:
            continue
        seen.add(name)
        readable.append({"name": name, "handle": handle})
    return readable, undecoded


def block_names_of(path: Path) -> list[str]:
    names = []
    seen = set()
    for _handle, name in ezdwg.raw.decode_block_header_names(str(path)):
        if name in seen or not readable_name(name):
            continue
        seen.add(name)
        names.append(name)
    return names


def read_one(path: Path) -> dict:
    doc = ezdwg.read(str(path))
    header = doc.header_variables()
    mtext_rows = ezdwg.raw.decode_mtext_entities(str(path))
    text_rows = ezdwg.raw.decode_text_entities(str(path))
    lines = lines_from_mtext(mtext_rows)
    single_line = []
    for row in text_rows:
        # (handle, text, insert, ...) — текст на месте 1, как у MTEXT.
        if len(row) > 1 and isinstance(row[1], str):
            cleaned = clean_mtext(row[1])
            if cleaned:
                single_line.append(cleaned)
    layers, undecoded_layers = layer_rows(path)
    names_by_handle = layer_name_map(path)
    block_names = block_names_of(path)
    model_inserts = insert_names(doc.modelspace())
    names = universal_names(model_inserts)
    model_bound, model_unbound, model_layers = bind_layers(doc.modelspace(), names_by_handle)
    paper_bound, paper_unbound, paper_layers = bind_layers(doc.paperspace(), names_by_handle)
    return {
        "file": path.name,
        "bytes": path.stat().st_size,
        "version": doc.version,
        "decode_version": doc.decode_version,
        "units": doc.units,
        "insunits": doc.insunits,
        "extmin": header.get("extmin"),
        "extmax": header.get("extmax"),
        "textsize": header.get("textsize"),
        "layers": layers,
        "layers_undecoded": undecoded_layers,
        "entities_with_layer_name": model_bound,
        "entities_layer_unresolved": model_unbound,
        "model_layer_counts": model_layers,
        "paper_entities_with_layer_name": paper_bound,
        "paper_entities_layer_unresolved": paper_unbound,
        "paper_layer_counts": paper_layers,
        "blocks": block_names,
        "model_entities": count_entities(doc.modelspace()),
        "paper_entities": count_entities(doc.paperspace()),
        "model_inserts": model_inserts,
        "paper_inserts": insert_names(doc.paperspace()),
        "universal_names": names,
        "mtext_records": len(mtext_rows),
        "text_records": len(text_rows),
        "text_lines": lines,
        "legend_names": legend_names(lines),
        "single_line_text": single_line,
    }


def main() -> None:
    if len(sys.argv) > 1:
        paths = [INBOX / name for name in sys.argv[1:]]
    else:
        paths = sorted(INBOX.glob("*.dwg"), key=lambda item: item.name)
    if not paths:
        raise SystemExit("В inbox нет DWG")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in paths:
        if not path.is_file():
            raise SystemExit(f"Нет файла: {path}")
        report = read_one(path)
        stem = path.stem
        payload = dict(report)
        lines = payload.pop("text_lines")
        singles = payload.pop("single_line_text")
        legend = payload.pop("legend_names")
        (OUT_DIR / f"{stem}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        body = [f"# {path.name}", ""]
        body.append("## Имена")
        body.append("")
        if report["universal_names"]:
            body.append("Из имени блока, без марки:")
            for row in report["universal_names"]:
                body.append(row["name"])
        else:
            body.append("В именах блоков наименования без марки нет.")
        body.append("")
        body.append("Из условных обозначений на листе:")
        body.extend(legend or ["(нет)"])
        body.extend(["", "## Подписи листа", ""])
        body.append("Марки, размеры и штамп. Марка диффузора здесь не имя изделия.")
        body.append("")
        body.extend(lines or ["(нет)"])
        body.extend(["", "## Однострочный TEXT", ""])
        body.extend(singles or ["(нет)"])
        (OUT_DIR / f"{stem}.lines.txt").write_text("\n".join(body), encoding="utf-8")
        model_n = sum(report["model_entities"].values())
        paper_n = sum(report["paper_entities"].values())
        print(
            f"{path.name} {report['version']} {report['units']} "
            f"layers {len(report['layers'])} blocks {len(report['blocks'])} "
            f"model {model_n} paper {paper_n} "
            f"layer_bound {report['entities_with_layer_name']} "
            f"layer_open {report['entities_layer_unresolved']} lines {len(lines)}"
        )


if __name__ == "__main__":
    main()
