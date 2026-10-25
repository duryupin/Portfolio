# -*- coding: utf-8 -*-
"""Этап 10. Учётка, срок хранения, журнал правил.

Чертёж в модель не отправляется. Банк не вызывается: отметка оплаты
только для прогона. reports/stage6/report.pdf и reports/stage8/report.pdf
этот скрипт не перезаписывает. Правила этапа 8 не меняет.

Запуск из корня norm_check:
  .venv\\Scripts\\python.exe scripts\\account_hold.py
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pdfplumber

import build_report
import review_conclusions as review

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_PATH = ROOT / "reports" / "stage8" / "package.json"
CARD_PATH = ROOT / "reports" / "stage5" / "cards.json"
OUT_DIR = ROOT / "reports" / "stage10"
PDF_PATH = OUT_DIR / "report.pdf"
UPLOADS = ROOT / "uploads" / "stage10-selfcheck"
KEEP_DAYS = 7
USER_NAME = re.compile(r"[a-z0-9_-]{1,32}")
SP_CODE = re.compile(r"СП\s*\d+\.\d+\.\d{4}", re.IGNORECASE)

CONTRACT = "Автоматическая проверка по перечисленным правилам, не экспертиза."
NO_FULL = "Полного нормоконтроля эта проверка не обещает."
BANK_NOTE = "Банк не подключён. Это тестовая отметка прогона, не списание."
NO_MODEL = "Чертёж в модель не отправляется."

LABELS = {
    "sp_title": "Название при шифре СП",
    "sheet_name": "Имя листа в ведомости",
    "one_cipher": "Один шифр на листах ведомости",
    "zero_area": "Площадь на своей строке",
    "zero_room_load": "Нагрузка помещения в своём столбце",
    "pipe_diameter": "Наружный диаметр дюймового ряда",
    "register_sheet": "Строка ведомости и штамп",
    "room_number": "Номер помещения",
    "segment_length": "Подпись длины участка",
}


def expect(name: str, ok: bool, detail: str) -> dict:
    if not ok:
        raise SystemExit(f"{name}: {detail}")
    return {"name": name, "ok": True, "detail": detail}


def stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def parse_stamp(value: str) -> datetime:
    return datetime.fromisoformat(value)


def user_dir(root: Path, name: str) -> Path:
    if not USER_NAME.fullmatch(name or ""):
        raise SystemExit("Имя учётки не подходит")
    return root / name


def open_account(root: Path, name: str) -> Path:
    folder = user_dir(root, name)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "files").mkdir(exist_ok=True)
    account = folder / "account.json"
    if not account.is_file():
        account.write_text(
            json.dumps({"name": name}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    payment = folder / "payment.json"
    if not payment.is_file():
        payment.write_text(
            json.dumps(
                {"paid": False, "provider": "none", "note": "Отметки списания нет."},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return folder


def mark_paid(folder: Path) -> None:
    (folder / "payment.json").write_text(
        json.dumps(
            {"paid": True, "provider": "none", "note": BANK_NOTE},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def is_paid(folder: Path) -> bool:
    path = folder / "payment.json"
    if not path.is_file():
        return False
    return json.loads(path.read_text(encoding="utf-8")).get("paid") is True


def issue_allowed(folder: Path) -> None:
    if not is_paid(folder):
        raise RuntimeError("нет отметки оплаты")


def store_file(folder: Path, name: str, data: bytes, now: datetime, keep_days: int) -> Path:
    file_id = uuid.uuid4().hex
    target = folder / "files" / file_id
    target.mkdir()
    (target / "blob").write_bytes(data)
    delete_at = now.astimezone(timezone.utc) + timedelta(days=keep_days)
    (target / "meta.json").write_text(
        json.dumps(
            {
                "name": Path(name).name,
                "stored_at": stamp(now),
                "delete_at": stamp(delete_at),
                "deleted": False,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return target


def purge(folder: Path, now: datetime) -> list[str]:
    removed = []
    files = folder / "files"
    if not files.is_dir():
        return removed
    moment = now.astimezone(timezone.utc)
    for meta_path in sorted(files.glob("*/meta.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("deleted"):
            continue
        if parse_stamp(meta["delete_at"]) > moment:
            continue
        blob = meta_path.parent / "blob"
        if blob.is_file():
            blob.unlink()
        meta["deleted"] = True
        meta["deleted_at"] = stamp(moment)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        removed.append(meta_path.parent.name)
    return removed


def applied_clauses(cards: dict) -> list[dict]:
    rows = []
    seen = set()
    for card in cards.get("cards") or []:
        for note in card.get("footnotes") or []:
            if not note.get("footnote_ok"):
                continue
            code = note.get("code") or ""
            clause = str(note.get("clause") or "")
            if not code or not clause:
                continue
            key = (code, clause)
            if key in seen:
                continue
            seen.add(key)
            rows.append({"code": code, "clause": clause})
    return rows


def journal_from(package: dict, cards: dict) -> list[dict]:
    clauses = applied_clauses(cards)
    rows = []
    for rule in package.get("rules") or []:
        rule_id = rule.get("id") or ""
        if rule_id not in LABELS:
            raise SystemExit(f"В пакете правило без подписи: {rule_id}")
        used = clauses if rule_id == "sp_title" else []
        rows.append(
            {
                "id": rule_id,
                "label": LABELS[rule_id],
                "question": rule.get("question"),
                "ran": True,
                "findings": len(rule.get("findings") or []),
                "clauses": used,
            }
        )
    if not rows:
        raise SystemExit("В пакете нет правил")
    return rows


def rule_line(row: dict) -> str:
    if row["clauses"]:
        cited = "; ".join(f"{item['code']} п. {item['clause']}" for item in row["clauses"])
        tail = f"Приложенные пункты: {cited}."
    else:
        tail = "Пункт СП не применяется."
    return f"{row['label']}. Запускалось. {tail}"


def write_pdf(path: Path, rows: list[dict]) -> None:
    build_report.fonts()
    look = build_report.styles()
    path.parent.mkdir(parents=True, exist_ok=True)
    story = [
        build_report.paragraph("Нормоконтроль", look["title"]),
        build_report.paragraph(f"{CONTRACT} {review.DISCLAIMER} {NO_FULL}", look["body"]),
        build_report.paragraph(NO_MODEL, look["body"]),
        build_report.paragraph("Правила, которые запускались", look["h"]),
    ]
    for row in rows:
        story.append(build_report.paragraph(rule_line(row), look["body"]))
    story.append(build_report.paragraph("Оплата", look["h"]))
    story.append(build_report.paragraph(BANK_NOTE, look["body"]))
    story.append(
        build_report.paragraph(
            f"Файл учётки хранится {KEEP_DAYS} суток и затем удаляется.",
            look["body"],
        )
    )
    document = build_report.SimpleDocTemplate(
        str(path),
        pagesize=build_report.A4,
        leftMargin=18 * build_report.mm,
        rightMargin=18 * build_report.mm,
        topMargin=16 * build_report.mm,
        bottomMargin=16 * build_report.mm,
        title="Нормоконтроль",
    )
    document.build(story, onFirstPage=build_report.draw_footer, onLaterPages=build_report.draw_footer)


def pdf_text(path: Path) -> str:
    with pdfplumber.open(path) as document:
        return "\n".join((page.extract_text() or "") for page in document.pages)


def review_pdf(path: Path, rows: list[dict]) -> dict:
    text = " ".join(pdf_text(path).split())
    folded = text.lower().replace("ё", "е")
    checks = [
        expect("договорная формулировка", CONTRACT in text, CONTRACT),
        expect("фраза о не-экспертизе", review.DISCLAIMER in text, review.DISCLAIMER),
        expect("нет обещания полного нормоконтроля", NO_FULL in text, NO_FULL),
        expect("чертёж не уходит в модель", NO_MODEL in text, NO_MODEL),
        expect("банк не назван подключённым", BANK_NOTE in text, BANK_NOTE),
        expect(
            "нет обещания полного контроля",
            "выполнен полный нормоконтроль" not in folded and "проверены все нормы" not in folded,
            "нет",
        ),
    ]
    allowed = set()
    for row in rows:
        checks.append(expect(f"в отчёте есть {row['id']}", row["label"] in text, row["label"]))
        for item in row["clauses"]:
            phrase = f"{item['code']} п. {item['clause']}"
            checks.append(expect(f"в отчёте пункт {phrase}", phrase in text, phrase))
            allowed.add(re.sub(r"\s+", "", item["code"]).upper())
    found = {re.sub(r"\s+", "", match.group(0)).upper() for match in SP_CODE.finditer(text)}
    foreign = sorted(found - allowed)
    checks.append(expect("в отчёте нет чужого шифра", not foreign, ", ".join(foreign) or "нет"))
    return {"ok": all(item["ok"] for item in checks), "checks": checks}


def retention_check(root: Path) -> dict:
    now = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
    first = open_account(root, "test")
    second = open_account(root, "other")
    due = store_file(first, "due.bin", b"due", now - timedelta(days=KEEP_DAYS), KEEP_DAYS)
    fresh = store_file(first, "fresh.bin", b"fresh", now, KEEP_DAYS)
    other = store_file(second, "other.bin", b"other", now - timedelta(days=KEEP_DAYS), KEEP_DAYS)
    refused = False
    try:
        issue_allowed(first)
    except RuntimeError:
        refused = True
    mark_paid(first)
    issue_allowed(first)
    removed = purge(first, now)
    other_removed = purge(second, now - timedelta(days=1))
    due_gone = not (due / "blob").is_file()
    fresh_kept = (fresh / "blob").is_file()
    other_kept = (other / "blob").is_file() and not other_removed
    checks = [
        expect("без оплаты отчёт не выпускается", refused and is_paid(first), "отметка после проверки"),
        expect("файл удалён в срок", due.name in removed and due_gone, due.name),
        expect("файл раньше срока на месте", fresh_kept and fresh.name not in removed, fresh.name),
        expect("чужая учётка не задета", other_kept, other.name),
    ]
    return {"ok": all(item["ok"] for item in checks), "checks": checks, "keep_days": KEEP_DAYS}


def load_json(path: Path) -> dict:
    if not path.is_file():
        raise SystemExit(f"Нет файла {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    if UPLOADS.exists():
        shutil.rmtree(UPLOADS)
    hold = retention_check(UPLOADS)
    shutil.rmtree(UPLOADS)
    package = load_json(PACKAGE_PATH)
    if package.get("false_findings"):
        raise SystemExit("В пакете есть ложные находки, контур не собран")
    cards = load_json(CARD_PATH)
    rows = journal_from(package, cards)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "journal.json").write_text(
        json.dumps({"rules": rows, "keep_days": KEEP_DAYS}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_pdf(PDF_PATH, rows)
    verdict = review_pdf(PDF_PATH, rows)
    (OUT_DIR / "review.json").write_text(
        json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT_DIR / "retention.json").write_text(
        json.dumps(hold, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    ok = verdict["ok"] and hold["ok"]
    if not ok and PDF_PATH.is_file():
        PDF_PATH.unlink()
    print(
        f"rules {len(rows)} review {'ok' if verdict['ok'] else 'fail'} "
        f"retention {'ok' if hold['ok'] else 'fail'} closed false"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
