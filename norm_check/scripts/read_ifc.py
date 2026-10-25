# -*- coding: utf-8 -*-
"""Опись IFC: помещения, стены и сети.

Геометрию сеток не строит и пересечения не ищет.
Файл передаётся аргументом. Имя, адрес и шифр объекта в код не входят.
AR_MEP_vis этот скрипт не меняет.

Запуск из корня norm_check:
  .venv\\Scripts\\python.exe scripts\\read_ifc.py путь\\к\\файлу.ifc
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import ifcopenshell

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "reports" / "ifc"

KEEP = (
    "IfcSpace",
    "IfcWall",
    "IfcWallStandardCase",
    "IfcSlab",
    "IfcFlowSegment",
    "IfcFlowFitting",
    "IfcFlowTerminal",
    "IfcFlowController",
    "IfcEnergyConversionDevice",
    "IfcBuildingStorey",
)


def text(value) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(str(value).split())
    return cleaned or None


def inventory(path: Path) -> dict:
    model = ifcopenshell.open(str(path))
    products = model.by_type("IfcProduct")
    counts = Counter(entity.is_a() for entity in products)
    spaces = []
    for space in model.by_type("IfcSpace"):
        spaces.append(
            {
                "name": text(getattr(space, "Name", None)),
                "long_name": text(getattr(space, "LongName", None)),
            }
        )
    storeys = [text(getattr(item, "Name", None)) for item in model.by_type("IfcBuildingStorey")]
    return {
        "file": path.name,
        "schema": model.schema,
        "products": len(products),
        "counts": dict(counts.most_common()),
        "kept": {key: counts.get(key, 0) for key in KEEP},
        "storeys": [item for item in storeys if item],
        "spaces": spaces,
        "geometry": "не снималась",
        "clashes": "не искались",
    }


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("Нужен один путь к IFC")
    path = Path(sys.argv[1])
    if not path.is_file():
        raise SystemExit(f"Нет файла: {path}")
    report = inventory(path)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "inventory.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"products {report['products']} spaces {report['kept']['IfcSpace']} "
        f"segments {report['kept']['IfcFlowSegment']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
