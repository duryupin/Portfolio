# -*- coding: utf-8 -*-
"""Каталог вопросов экспертизы.

Вопрос описывает вид факта. Число и слово берутся из файла при прогоне.
Адрес, шифр и конкретные числа одного комплекта в каталог не входят.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "reports" / "questions.json"
MD_PATH = ROOT / "reports" / "QUESTIONS.md"

STATUSES = (
    "правило",
    "измерено, замечания нет",
    "не прочитано",
    "ждёт карточку",
)
GROUPS = (
    "Комплектность",
    "Ссылки",
    "Исходные данные",
    "Решения",
    "Графика",
    "Смежные разделы",
    "Пожарные и санитарные",
    "Другие разделы",
    "Отчёт",
)
# Вопросы, которые уже исполняет отчёт: шесть правил пакета и фразы отчёта.
# 7–9 и 12 — название СП и сноска. 14 — пустая площадь и пустая нагрузка.
RULES = {2, 3, 6, 7, 8, 9, 12, 14, 19, 26, 27, 28, 40}
REQUIRED = ("id", "group", "fact", "read_from", "needs", "status")
FORBIDDEN = ("9,53", "9.53", "Врубеля", "1/05-26", "В проекте:")
SP_YEAR = re.compile(r"СП\s*\d+\.\d+")


def load() -> list[dict]:
    return json.loads(PATH.read_text(encoding="utf-8"))


def self_check(rows: list[dict] | None = None) -> list[str]:
    rows = load() if rows is None else rows
    errors = []
    seen = set()
    claimed = set()
    for row in rows:
        number = row.get("id")
        if not isinstance(number, int):
            errors.append(f"номер не целое: {number}")
            continue
        if number in seen:
            errors.append(f"повтор номера {number}")
        seen.add(number)
        for key in REQUIRED:
            if not str(row.get(key) or "").strip():
                errors.append(f"{number}: пустое поле {key}")
        status = row.get("status")
        if status not in STATUSES:
            errors.append(f"{number}: статус {status}")
        if row.get("group") not in GROUPS:
            errors.append(f"{number}: группа {row.get('group')}")
        if status == "правило":
            claimed.add(number)
        text = " ".join(str(row.get(key, "")) for key in REQUIRED)
        for token in FORBIDDEN:
            if token in text:
                errors.append(f"{number}: в вопросе зашито {token}")
        if SP_YEAR.search(text):
            errors.append(f"{number}: в вопросе шифр с годом")
        if "|" in text:
            errors.append(f"{number}: в вопросе символ таблицы")
    missing = RULES - seen
    if missing:
        errors.append(f"нет вопросов правил: {sorted(missing)}")
    extra = claimed - RULES
    if extra:
        errors.append(f"статус правила без исполнения: {sorted(extra)}")
    for number in RULES:
        row = next((item for item in rows if item.get("id") == number), None)
        if row and row.get("status") != "правило":
            errors.append(f"{number}: правило записано как {row.get('status')}")
    return errors


def by_group(rows: list[dict]) -> list[tuple[str, list[dict]]]:
    buckets = {group: [] for group in GROUPS}
    for row in rows:
        buckets[row["group"]].append(row)
    result = []
    for group in GROUPS:
        items = sorted(buckets[group], key=lambda item: item["id"])
        if items:
            result.append((group, items))
    return result


def render_md(rows: list[dict] | None = None) -> str:
    rows = load() if rows is None else rows
    lines = [
        "# Вопросы экспертизы",
        "",
        "Дата: 2026-10-06. Машиночитаемый список: `reports\\questions.json`.",
        "",
        "Это виды вопросов, которые экспертиза задаёт к разделу инженерных систем: комплектность, ссылки, исходные данные, решения, графика, смежные разделы, пожарные и санитарные требования. Число и слово берутся из файла при прогоне. Адрес, шифр и конкретные числа одного комплекта в вопросы не входят.",
        "",
        "Замечание ставит только статус «правило», и только когда величина прочитана. Статус «измерено, замечания нет» значит: величина снята, в замечания пакета она не внесена. «Не прочитано» — факта в этом прогоне нет. «Ждёт карточку» — без пункта и редакции замечание не ставится.",
        "",
        "Список не кодирует каждый пункт каждого свода. Вопрос без карточки остаётся вопросом. Отчёт по-прежнему не заключение экспертизы.",
        "",
    ]
    for group, items in by_group(rows):
        lines.extend(
            [
                f"## {group}",
                "",
                "| № | Вопрос | Откуда | Что нужно | Статус |",
                "|---:|---|---|---|---|",
            ]
        )
        for row in items:
            lines.append(
                f"| {row['id']} | {row['fact']} | {row['read_from']} | {row['needs']} | {row['status']} |"
            )
        lines.append("")
    return "\n".join(lines)


def report_lines(rows: list[dict] | None = None) -> list[str]:
    rows = load() if rows is None else rows
    lines = []
    for _group, items in by_group(rows):
        for row in items:
            if row["status"] == "правило":
                continue
            lines.append(f"{row['id']}. {row['fact']} Статус: {row['status']}.")
    return lines


def main() -> int:
    rows = load()
    errors = self_check(rows)
    if errors:
        (ROOT / "reports" / "questions_check.txt").write_text(
            "\n".join(errors) + "\n", encoding="utf-8"
        )
        print(f"questions fail {len(errors)}")
        return 1
    MD_PATH.write_text(render_md(rows), encoding="utf-8")
    print(f"questions {len(rows)} ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
