# -*- coding: utf-8 -*-
"""Локальная проверка одной папки проекта.

В папке один PDF и, если есть, DWG. Отчёт пишется в uploads/<задание>/stage8.
reports/stage6/report.pdf и reports/stage8/report.pdf не меняет.
Оплату и кабинет не вызывает.

Запуск из корня norm_check:
  .venv\\Scripts\\python.exe scripts\\check_job.py
  .venv\\Scripts\\python.exe scripts\\check_job.py путь\\к\\папке
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pdfplumber
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4

import serve

ROOT = Path(__file__).resolve().parents[1]
UPLOADS = ROOT / "uploads"
SUMMARY = ROOT / "reports" / "local_jobs" / "summary.json"
FONT = Path(r"C:\Windows\Fonts\arial.ttf")
EMPTY = "По этим правилам замечаний нет."


def write_pdf(path: Path, lines: list[str], cipher: str, sheets: list[tuple[str, str]]) -> None:
    if "ArialJob" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("ArialJob", str(FONT)))
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet = canvas.Canvas(str(path), pagesize=A4)
    for index, (number, name) in enumerate(sheets):
        if index:
            sheet.showPage()
        sheet.setFont("ArialJob", 12)
        if index == 0:
            y = 780
            for line in lines:
                sheet.drawString(40, y, line)
                y -= 22
        sheet.drawString(400, 90, "Стадия")
        sheet.drawString(470, 90, "Лист")
        sheet.drawString(540, 90, "Листов")
        sheet.drawString(478, 68, number)
        sheet.drawString(200, 52, name)
        sheet.drawString(400, 36, "Формат")
        sheet.drawString(200, 24, cipher)
    sheet.save()


def copy_project(source: Path, job_id: str) -> Path:
    if not source.is_dir():
        raise SystemExit(f"Нет папки {source}")
    pdfs = sorted(source.glob("*.pdf"))
    dwgs = sorted(source.glob("*.dwg"))
    if len(pdfs) != 1:
        raise SystemExit(f"В папке нужен один PDF, найдено: {len(pdfs)}")
    inbox = UPLOADS / job_id / "in"
    if inbox.exists():
        shutil.rmtree(UPLOADS / job_id)
    inbox.mkdir(parents=True)
    for path in pdfs + dwgs:
        shutil.copy2(path, inbox / path.name)
    return inbox


def run_job(source: Path, job_id: str) -> dict:
    copy_project(source, job_id)
    serve.run_pipeline(job_id)
    out = UPLOADS / job_id / "stage8"
    package = json.loads((out / "package.json").read_text(encoding="utf-8"))
    review = json.loads((out / "review.json").read_text(encoding="utf-8"))
    with pdfplumber.open(out / "report.pdf") as document:
        text = "\n".join((page.extract_text() or "") for page in document.pages)
    rules = []
    for rule in package["rules"]:
        rules.append(
            {
                "id": rule["id"],
                "findings": len(rule.get("findings") or []),
                "negative": rule.get("negative_checks"),
                "unchecked": rule.get("unchecked"),
            }
        )
    return {
        "job": job_id,
        "pdf": package.get("pdf"),
        "false_findings": package.get("false_findings"),
        "review_ok": review.get("ok"),
        "rules": rules,
        "empty_sentence": EMPTY in " ".join(text.split()),
        "report": str((out / "report.pdf").relative_to(ROOT)),
    }


def expect(ok: bool, detail: str) -> None:
    if not ok:
        raise SystemExit(detail)


def fixture_pair() -> list[dict]:
    base = UPLOADS / "local-fixtures"
    if base.exists():
        shutil.rmtree(base)
    clean = base / "clean"
    other = base / "other"
    write_pdf(
        clean / "north.pdf",
        [
            "Ведомость рабочих чертежей",
            "1 План секции",
            "2 Разрез",
            "СП 60.13330.2020 «Отопление, вентиляция и кондиционирование воздуха»",
            "Площадь здания 120",
            "Медная труба Ø9,52 м 3,0",
        ],
        "2/11-25-ОВ",
        [("1", "План секции"), ("2", "Разрез")],
    )
    write_pdf(
        other / "south.pdf",
        [
            "Ведомость рабочих чертежей",
            "1 Схема стояка",
            "2 Узел",
            "СП 51.13330.2011 «Защита от шума»",
            "Площадь участка 0",
            "Медная труба Ø9,53 м 4,0",
        ],
        "8/03-24-ЭОМ",
        [("1", "Схема стояка"), ("2", "Узел")],
    )
    first = run_job(clean, "local-clean")
    second = run_job(other, "local-other")
    expect(first["review_ok"] and first["false_findings"] == 0, "чистый проект не сошёлся")
    expect(all(row["findings"] == 0 for row in first["rules"]), "чистый проект дал замечание")
    expect(first["empty_sentence"], "в чистом отчёте нет фразы об отсутствии замечаний")
    expect(second["review_ok"] and second["false_findings"] == 0, "второй проект не сошёлся")
    found = {row["id"]: row["findings"] for row in second["rules"]}
    expect(found.get("zero_area") == 1, "второй проект не увидел ноль площади")
    expect(found.get("pipe_diameter") == 1, "второй проект не увидел диаметр 9,53")
    expect(found.get("sp_title") == 0, "второй проект принял верное название за ошибку")
    expect(found.get("sheet_name") == 0, "второй проект ошибся в имени листа")
    expect(found.get("one_cipher") == 0, "второй проект ошибся в шифре")
    clean_found = {row["id"]: row for row in first["rules"]}
    other_rows = {row["id"]: row for row in second["rules"]}
    expect(clean_found["sheet_name"]["negative"] >= 1, "чистый проект не прочитал имя листа")
    expect(clean_found["one_cipher"]["negative"] >= 1, "чистый проект не прочитал шифр")
    expect(other_rows["sheet_name"]["negative"] >= 1, "второй проект не прочитал имя листа")
    expect(other_rows["one_cipher"]["negative"] >= 1, "второй проект не прочитал шифр")
    expect(not second["empty_sentence"], "во втором отчёте пустая фраза при замечаниях")
    return [first, second]


def main() -> int:
    if len(sys.argv) > 2:
        raise SystemExit("Нужна одна папка или запуск без аргументов")
    if len(sys.argv) == 2:
        row = run_job(Path(sys.argv[1]), "local-given")
        print(json.dumps(row, ensure_ascii=False))
        return 0 if row["review_ok"] else 1
    rows = fixture_pair()
    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY.write_text(json.dumps({"jobs": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"jobs {len(rows)} clean 0 other area+diameter")
    return 0


if __name__ == "__main__":
    sys.exit(main())
