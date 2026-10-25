# -*- coding: utf-8 -*-
"""Длина прямого участка на слое воздуховода и трубы.

Слой берётся из имени: DUCT или PIPE, затем CNTR (ось) или OTLN (стенка).
Ось, если она есть, — длина участка. Стенка подтверждает ось и заменяет её
только на листе, где оси нет: две параллельные стенки одной длины дают
один участок. Короткие стыки не склеивают соседние участки. Подпись длины —
число с единицей м или мм. Сечение, расход и голое число длиной не считаются
и с длиной не сравниваются. Скорость не считается.

Запуск из корня norm_check:
  .venv\\Scripts\\python.exe scripts\\segment_length.py
"""

from __future__ import annotations

import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path

import ezdwg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from read_dwg import clean_mtext, layer_name_map  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "inbox"
OUT_DIR = ROOT / "reports" / "length"

MIN_SEGMENT_MM = 80.0
OFFSET_TOL_MM = 3.0
DUP_GAP_MM = 1.0
LABEL_RADIUS_MM = 1200.0
WIDTH_MIN_MM = 15.0
WIDTH_MAX_MM = 500.0
OUTLINE_DEDUPE_MM = 220.0
OUTLINE_MIN_MM = 200.0

SECTION = re.compile(r"\d+(?:[.,]\d+)?\s*[xх×]\s*\d+", re.IGNORECASE)
FLOW = re.compile(r"^L\d+$", re.IGNORECASE)
LENGTH_VALUE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(мм|mm|м|m)\b", re.IGNORECASE
)
THICKNESS_WORDS = ("толщин", "не менее", "изоляц", "пенофол", "k-flex", "kflex")


def layer_role(name: str) -> str | None:
    upper = name.upper()
    if any(skip in upper for skip in ("CDFF", "EQPM", "TTLB", "NOTE")):
        return None
    duct = "DUCT" in upper
    pipe = "PIPE" in upper
    if duct == pipe:
        return None
    system = "duct" if duct else "pipe"
    if "CNTR" in upper:
        return f"{system}-center"
    if "OTLN" in upper:
        return f"{system}-outline"
    if "IDEN" in upper:
        return f"{system}-label"
    return None


def classify_label(text: str) -> str:
    folded = text.lower().replace("ё", "е")
    if any(word in folded for word in THICKNESS_WORDS):
        return "thickness"
    if SECTION.search(text):
        return "section"
    if FLOW.search(text.strip()):
        return "flow"
    if re.match(r"^L\d+\*", text.strip(), re.IGNORECASE):
        return "mark"
    if LENGTH_VALUE.search(text):
        return "length"
    if re.search(r"\d", text):
        return "bare_number"
    return "other"


def length_label_mm(text: str) -> float | None:
    if classify_label(text) != "length":
        return None
    match = LENGTH_VALUE.search(text)
    if not match:
        return None
    number = float(match.group(1).replace(",", "."))
    unit = match.group(2).lower()
    if unit in {"м", "m"}:
        return number * 1000.0
    return number


def _frame(p, q):
    vx, vy = q[0] - p[0], q[1] - p[1]
    length = math.hypot(vx, vy)
    if length < 1.0:
        return None
    ux, uy = vx / length, vy / length
    if ux < -1e-9 or (abs(ux) < 1e-9 and uy < 0):
        ux, uy = -ux, -uy
        p, q = q, p
        vx, vy = -vx, -vy
    angle = math.degrees(math.atan2(uy, ux))
    offset = p[0] * -uy + p[1] * ux
    t0 = p[0] * ux + p[1] * uy
    t1 = q[0] * ux + q[1] * uy
    if t1 < t0:
        t0, t1 = t1, t0
    return {
        "length": length,
        "angle": angle,
        "dir": (ux, uy),
        "offset": offset,
        "t0": t0,
        "t1": t1,
        "p": p,
        "q": q,
    }


def straight_runs(lines: list[tuple], min_len: float = MIN_SEGMENT_MM) -> tuple[list[dict], int]:
    """Схлопнуть точные дубли. Стык короче порога участок не удлиняет.

    Два длинных куска на одной оси остаются двумя участками, даже если между
    ними лежит короткий штрих.
    """
    framed = []
    for p, q in lines:
        row = _frame(p, q)
        if row:
            framed.append(row)
    buckets = defaultdict(list)
    for row in framed:
        key = (round(row["angle"]), round(row["offset"] / OFFSET_TOL_MM))
        buckets[key].append(row)
    runs = []
    short = 0
    for group in buckets.values():
        group.sort(key=lambda row: row["offset"])
        clusters = []
        for row in group:
            if not clusters or abs(row["offset"] - clusters[-1][-1]["offset"]) > OFFSET_TOL_MM:
                clusters.append([row])
            else:
                clusters[-1].append(row)
        for cluster in clusters:
            base = max(cluster, key=lambda row: row["length"])
            ux, uy = base["dir"]
            origin = base["p"]
            intervals = []
            for row in cluster:
                t_a = (row["p"][0] - origin[0]) * ux + (row["p"][1] - origin[1]) * uy
                t_b = (row["q"][0] - origin[0]) * ux + (row["q"][1] - origin[1]) * uy
                intervals.append((min(t_a, t_b), max(t_a, t_b)))
            intervals.sort()
            cur_a, cur_b = intervals[0]
            merged = []
            for a, b in intervals[1:]:
                overlap = cur_b - a
                shorter = min(cur_b - cur_a, b - a)
                # Дубль перекрывает почти весь короткий кусок. Касание торцами
                # и короткий стык между участками длину не складывают.
                if overlap >= max(DUP_GAP_MM, min(20.0, 0.5 * shorter)):
                    cur_b = max(cur_b, b)
                else:
                    merged.append((cur_a, cur_b))
                    cur_a, cur_b = a, b
            merged.append((cur_a, cur_b))
            for a, b in merged:
                length = b - a
                if length < min_len:
                    short += 1
                    continue
                mid = (a + b) / 2.0
                runs.append(
                    {
                        "length_mm": round(length, 1),
                        "mid": [
                            round(origin[0] + ux * mid, 1),
                            round(origin[1] + uy * mid, 1),
                        ],
                        "angle": round(base["angle"], 2),
                        "p": [origin[0] + ux * a, origin[1] + uy * a],
                        "q": [origin[0] + ux * b, origin[1] + uy * b],
                    }
                )
    runs.sort(key=lambda row: -row["length_mm"])
    return runs, short


def _angle_diff(a: float, b: float) -> float:
    diff = abs(a - b) % 180.0
    return min(diff, 180.0 - diff)


def _perp_distance(segment: dict, edge: dict) -> float:
    ux = math.cos(math.radians(edge["angle"]))
    uy = math.sin(math.radians(edge["angle"]))
    mx, my = segment["mid"]
    px, py = edge["p"]
    return abs((mx - px) * -uy + (my - py) * ux)


def _overlap_ratio(segment: dict, edge: dict) -> float:
    ux = math.cos(math.radians(segment["angle"]))
    uy = math.sin(math.radians(segment["angle"]))
    origin = segment["p"]

    def project(point):
        return (point[0] - origin[0]) * ux + (point[1] - origin[1]) * uy

    a0, a1 = sorted((project(segment["p"]), project(segment["q"])))
    b0, b1 = sorted((project(edge["p"]), project(edge["q"])))
    overlap = min(a1, b1) - max(a0, b0)
    if overlap <= 0:
        return 0.0
    return overlap / max(segment["length_mm"], 1.0)


def confirm_outline(segment: dict, outlines: list[dict]) -> float | None:
    best = None
    for edge in outlines:
        if _angle_diff(segment["angle"], edge["angle"]) > 3.0:
            continue
        distance = _perp_distance(segment, edge)
        if not WIDTH_MIN_MM <= distance <= WIDTH_MAX_MM:
            continue
        if _overlap_ratio(segment, edge) < 0.75:
            continue
        if abs(edge["length_mm"] - segment["length_mm"]) > 40.0:
            continue
        if best is None or abs(edge["length_mm"] - segment["length_mm"]) < abs(best - segment["length_mm"]):
            best = edge["length_mm"]
    return best


def pair_outlines(edges: list[dict]) -> tuple[list[dict], int]:
    """Две параллельные стенки одной длины — один участок."""
    used = set()
    pairs = []
    order = sorted(range(len(edges)), key=lambda i: -edges[i]["length_mm"])
    for i in order:
        if i in used:
            continue
        best = None
        best_dist = None
        for j in order:
            if j == i or j in used:
                continue
            other = edges[j]
            if abs(edges[i]["length_mm"] - other["length_mm"]) > max(
                30.0, 0.06 * edges[i]["length_mm"]
            ):
                continue
            if _angle_diff(edges[i]["angle"], other["angle"]) > 4.0:
                continue
            distance = _perp_distance(edges[i], other)
            if not WIDTH_MIN_MM <= distance <= WIDTH_MAX_MM:
                continue
            if _overlap_ratio(edges[i], other) < 0.7:
                continue
            if best_dist is None or distance < best_dist:
                best = j
                best_dist = distance
        if best is None:
            continue
        used.add(i)
        used.add(best)
        left, right = edges[i], edges[best]
        length = round((left["length_mm"] + right["length_mm"]) / 2.0, 1)
        pairs.append(
            {
                "length_mm": length,
                "mid": [
                    round((left["mid"][0] + right["mid"][0]) / 2.0, 1),
                    round((left["mid"][1] + right["mid"][1]) / 2.0, 1),
                ],
                "angle": left["angle"],
                "width_mm": round(best_dist, 1),
                "p": left["p"],
                "q": left["q"],
            }
        )
    pairs.sort(key=lambda row: -row["length_mm"])
    return pairs, len(edges) - len(used)


def spline_runs(entities) -> list[dict]:
    runs = []
    for ent in entities:
        if ent.dxftype != "SPLINE":
            continue
        points = ent.dxf.get("points") or []
        if len(points) < 2:
            continue
        length = sum(math.dist(points[i], points[i + 1]) for i in range(len(points) - 1))
        if length < MIN_SEGMENT_MM:
            continue
        mid = points[len(points) // 2]
        runs.append(
            {
                "length_mm": round(length, 1),
                "mid": [round(mid[0], 1), round(mid[1], 1)],
                "kind": "spline",
            }
        )
    runs.sort(key=lambda row: -row["length_mm"])
    return runs


def dedupe_runs(runs: list[dict], radius: float = OUTLINE_DEDUPE_MM) -> list[dict]:
    """Две пары граней одного короба в изометрии не складывать дважды."""
    kept = []
    for row in runs:
        close = False
        for other in kept:
            if abs(row["length_mm"] - other["length_mm"]) > max(40.0, 0.05 * row["length_mm"]):
                continue
            distance = math.hypot(row["mid"][0] - other["mid"][0], row["mid"][1] - other["mid"][1])
            if distance <= radius:
                close = True
                break
        if not close:
            kept.append(row)
    return kept


def nearest_label(mid: list[float], labels: list[dict]) -> dict | None:
    best = None
    best_d = None
    for label in labels:
        distance = math.hypot(label["x"] - mid[0], label["y"] - mid[1])
        if distance > LABEL_RADIUS_MM:
            continue
        if best_d is None or distance < best_d:
            best = label
            best_d = distance
    if best is None:
        return None
    return {
        "text": best["text"],
        "kind": best["kind"],
        "distance_mm": round(best_d, 1),
    }


def public_segment(row: dict, labels: list[dict], outline_mm: float | None, kind: str) -> dict:
    label = nearest_label(row["mid"], labels)
    length_near = None
    if label and label["kind"] == "length":
        length_near = length_label_mm(label["text"])
    return {
        "kind": kind,
        "length_mm": row["length_mm"],
        "mid_mm": row["mid"],
        "outline_mm": outline_mm,
        "nearest_label": label,
        "label_bound": False,
        "length_label_mm": length_near,
        "compared": length_near is not None,
    }


def collect(doc, path: Path) -> dict:
    names = layer_name_map(path)
    lines = defaultdict(list)
    labels = defaultdict(list)
    label_kinds = defaultdict(int)
    splines = defaultdict(list)
    centers = defaultdict(list)
    for ent in doc.modelspace().query():
        handle = ent.dxf.get("layer_handle")
        layer = names.get(int(handle)) if isinstance(handle, int) else None
        if not layer:
            continue
        role = layer_role(layer)
        if role and ent.dxftype == "LINE" and role.endswith(("center", "outline")):
            start, end = ent.dxf["start"], ent.dxf["end"]
            lines[role].append(((start[0], start[1]), (end[0], end[1])))
        elif role and ent.dxftype == "SPLINE" and role.endswith("center"):
            splines[role].append(ent)
        elif role and ent.dxftype == "MTEXT" and role.endswith("label"):
            text = clean_mtext(ent.dxf.get("text") or "")
            if not text:
                continue
            kind = classify_label(text)
            label_kinds[kind] += 1
            ins = ent.dxf["insert"]
            labels[role].append({"text": text, "kind": kind, "x": ins[0], "y": ins[1]})
        elif ent.dxftype == "MTEXT":
            text = clean_mtext(ent.dxf.get("text") or "")
            if text and classify_label(text) == "length":
                label_kinds["length_elsewhere"] += 1
    systems = {}
    for system in ("duct", "pipe"):
        center_key = f"{system}-center"
        outline_key = f"{system}-outline"
        label_key = f"{system}-label"
        outline_runs, outline_short = straight_runs(lines[outline_key]) if lines[outline_key] else ([], 0)
        if lines[center_key]:
            runs, short = straight_runs(lines[center_key])
            raw = len(lines[center_key])
            segments = []
            agreed = 0
            for run in runs:
                outline_mm = confirm_outline(run, outline_runs) if outline_runs else None
                if outline_mm is not None:
                    agreed += 1
                segments.append(public_segment(run, labels[label_key], outline_mm, "line"))
            curves = [
                public_segment(run, labels[label_key], None, "spline")
                for run in spline_runs(splines[center_key])
            ]
            systems[system] = {
                "source": "centerline",
                "raw_lines": raw,
                "short_pieces": short,
                "outline_edges": len(outline_runs),
                "outline_agrees": agreed,
                "segments": segments,
                "splines": curves,
            }
        elif lines[outline_key]:
            runs, unpaired = pair_outlines(outline_runs)
            runs = dedupe_runs(runs)
            short_pairs = [row for row in runs if row["length_mm"] < OUTLINE_MIN_MM]
            runs = [row for row in runs if row["length_mm"] >= OUTLINE_MIN_MM]
            segments = [
                public_segment(run, labels[label_key], None, "outline_pair") for run in runs
            ]
            systems[system] = {
                "source": "outline_pair",
                "raw_lines": len(lines[outline_key]),
                "short_pieces": outline_short,
                "short_pairs": len(short_pairs),
                "unpaired_edges": unpaired,
                "segments": segments,
                "splines": [],
            }
    compared = [
        segment
        for system in systems.values()
        for segment in system["segments"] + system["splines"]
        if segment["compared"]
    ]
    return {
        "file": path.name,
        "label_kinds": dict(label_kinds),
        "systems": systems,
        "length_comparisons": len(compared),
    }


def self_check() -> list[str]:
    errors = []
    duplicates, _short = straight_runs(
        [((0.0, 0.0), (1000.0, 0.0)), ((0.0, 0.0), (1000.0, 0.0))]
    )
    if len(duplicates) != 1 or duplicates[0]["length_mm"] != 1000.0:
        errors.append("дубль оси не схлопнулся в один участок 1000 мм")
    gapped, _short = straight_runs(
        [
            ((0.0, 0.0), (1000.0, 0.0)),
            ((1008.0, 0.0), (1800.0, 0.0)),
            ((1001.0, 0.0), (1007.0, 0.0)),
        ]
    )
    lengths = sorted(row["length_mm"] for row in gapped)
    if lengths != [792.0, 1000.0]:
        errors.append(f"короткий стык склеил участки: {lengths}")
    touching, _short = straight_runs(
        [((0.0, 0.0), (1000.0, 0.0)), ((1000.0, 0.0), (1800.0, 0.0))]
    )
    touching_lengths = sorted(row["length_mm"] for row in touching)
    if touching_lengths != [800.0, 1000.0]:
        errors.append(f"торцы двух участков склеились: {touching_lengths}")
    parallel, _short = straight_runs(
        [((0.0, 0.0), (1000.0, 0.0)), ((0.0, 40.0), (1000.0, 40.0))]
    )
    if len(parallel) != 2:
        errors.append("параллельная труба склеилась с соседней")
    samples = {
        "300x250": "section",
        "160": "bare_number",
        "L70": "flow",
        "L0*": "mark",
        "12.7": "bare_number",
        "2,5 м": "length",
        "толщиной не менее 10 мм": "thickness",
        "-125": "bare_number",
    }
    for text, kind in samples.items():
        got = classify_label(text)
        if got != kind:
            errors.append(f"подпись {text!r} разобрана как {got}, ожидалось {kind}")
    if length_label_mm("2,5 м") != 2500.0:
        errors.append("метры не переведены в миллиметры")
    if length_label_mm("100") is not None:
        errors.append("голое число принято за длину")
    same = [
        {"length_mm": 1000.0, "mid": [0.0, 0.0]},
        {"length_mm": 1005.0, "mid": [80.0, 40.0]},
        {"length_mm": 1000.0, "mid": [900.0, 0.0]},
    ]
    folded = dedupe_runs(same)
    if len(folded) != 2:
        errors.append(f"повтор грани изометрии не схлопнут: {len(folded)}")
    return errors


def main() -> None:
    errors = self_check()
    if errors:
        raise SystemExit("Самопроверка не сошлась: " + "; ".join(errors))
    if len(sys.argv) > 1:
        paths = [INBOX / name for name in sys.argv[1:]]
    else:
        paths = sorted(INBOX.glob("*.dwg"), key=lambda item: item.name)
    if not paths:
        raise SystemExit("В inbox нет DWG")
    files = []
    for path in paths:
        if not path.is_file():
            raise SystemExit(f"Нет файла: {path}")
        doc = ezdwg.read(str(path))
        report = collect(doc, path)
        for system in report["systems"].values():
            for segment in system["segments"] + system["splines"]:
                if segment["label_bound"]:
                    raise SystemExit("Подпись приклеена к участку без выноски")
                label = segment["nearest_label"]
                if segment["compared"] and (not label or label["kind"] != "length"):
                    raise SystemExit("Сравнение длины с подписью другого вида")
        files.append(report)
        for system, row in report["systems"].items():
            print(
                f"{path.name} {system} {row['source']} "
                f"segments {len(row['segments'])} "
                f"sum {sum(item['length_mm'] for item in row['segments']):.0f} "
                f"splines {len(row['splines'])} "
                f"comparisons {report['length_comparisons']}"
            )
    payload = {
        "velocity": None,
        "min_segment_mm": MIN_SEGMENT_MM,
        "duplicate_gap_mm": DUP_GAP_MM,
        "label_radius_mm": LABEL_RADIUS_MM,
        "self_check": "ok",
        "files": files,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "segments.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"wrote {OUT_DIR / 'segments.json'}")


if __name__ == "__main__":
    main()
