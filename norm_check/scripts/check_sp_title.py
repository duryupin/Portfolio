# -*- coding: utf-8 -*-
"""Этап 4. Название при шифре СП против карточки этого шифра.

Правило одно и не привязано к объекту. В тексте ищется «СП номер «название»».
Название сверяется с полем title карточки того же шифра в norms/cards.json.
Сокращение официального названия замечанием не считается. Другое название считается.
Шифра нет в карточках — строка не проверена, это не замечание.
Год редакции сам по себе не проверяется.

Запуск из корня norm_check:
  .venv\\Scripts\\python.exe scripts\\check_sp_title.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CARDS_PATH = ROOT / "norms" / "cards.json"
INBOX = ROOT / "inbox"
LINES_DIR = ROOT / "reports" / "stage3"
OUT_DIR = ROOT / "reports" / "stage4"

CITATION = re.compile(
    r"СП\s*(\d+\.\d+\.\d{4})\s*[«\"]\s*([^»\"]+?)\s*[»\"]",
    re.IGNORECASE,
)
WORD = re.compile(r"[0-9a-zа-я]+")


def fold(text: str) -> str:
    return text.lower().replace("ё", "е")


def tokens(text: str) -> list[str]:
    return WORD.findall(fold(text))


def squeeze(text: str) -> str:
    return "".join(char for char in fold(text) if char.isalpha())


def titles_compatible(quoted: str, official: str) -> bool:
    """Сокращение официального названия совместимо. Подмена слова — нет."""
    quoted_tokens = tokens(quoted)
    official_tokens = tokens(official)
    if len(quoted_tokens) >= 2 and len(official_tokens) >= 2:
        rest = iter(official_tokens)
        if all(any(token == item for item in rest) for token in quoted_tokens):
            return True
    # Пробел между словами мог пропасть: «вентиляцияикондиционирование».
    quoted_flat = squeeze(quoted)
    official_flat = squeeze(official)
    if not quoted_flat or not official_flat:
        return False
    return quoted_flat == official_flat or official_flat.startswith(quoted_flat)


def load_cards(path: Path) -> dict[str, dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cards = {}
    for document in payload["documents"]:
        code = document["code"].replace(" ", "")
        cards[code.upper()] = document
    return cards


def check_text(text: str, cards: dict[str, dict], source: str) -> dict:
    findings = []
    unchecked = []
    seen = set()
    for match in CITATION.finditer(text):
        number = match.group(1)
        quoted = " ".join(match.group(2).split())
        code = f"СП{number}".upper()
        key = (code, quoted)
        if key in seen:
            continue
        seen.add(key)
        card = cards.get(code)
        if card is None:
            unchecked.append({"source": source, "code": f"СП {number}", "title": quoted})
            continue
        official = card.get("title") or ""
        if titles_compatible(quoted, official):
            continue
        findings.append(
            {
                "source": source,
                "code_in_project": f"СП {number}",
                "title_in_project": quoted,
                "catalog_code": card["code"],
                "title_in_catalog": official,
                "footnote_ok": bool(card.get("footnote_ok")),
            }
        )
    return {"source": source, "findings": findings, "unchecked": unchecked}


def corrected_text(text: str, findings: list[dict]) -> str:
    corrected = text
    for finding in findings:
        corrected = corrected.replace(
            finding["title_in_project"], finding["title_in_catalog"]
        )
    return corrected


def read_pdf_pages(path: Path) -> list[tuple[int, str]]:
    try:
        import pdfplumber
    except ImportError:
        return []
    pages = []
    with pdfplumber.open(path) as doc:
        for index, page in enumerate(doc.pages, start=1):
            pages.append((index, page.extract_text() or ""))
    return pages


def self_check(cards: dict[str, dict]) -> None:
    sp54 = cards["СП54.13330.2016"]
    sp55 = cards["СП55.13330.2016"]
    sp60 = cards["СП60.13330.2020"]
    short60 = " ".join(sp60["title"].split()[:-1])
    flat_short60 = squeeze(short60)
    glued60 = short60.replace(" ", "", 1)
    flat_other = squeeze(sp55["title"])
    samples = [
        (f"СП 54.13330.2016 «{sp55['title']}»", 1),
        (f"СП 54.13330.2016 «{sp54['title']}»", 0),
        (f"СП 60.13330.2020 «{short60}»", 0),
        (f"СП60.13330.2020 «{flat_short60}»", 0),
        (f"СП 60.13330.2020 «{glued60}»", 0),
        (f'СП54.13330.2016 "{flat_other}"', 1),
        ("СП 99.13330.2099 «Любое название»", 0),
    ]
    for text, expected in samples:
        result = check_text(text, cards, "self-check")
        got = len(result["findings"])
        if got != expected:
            raise SystemExit(f"Самопроверка: {text!r} дала {got}, ожидалось {expected}")
    unknown = check_text(samples[-1][0], cards, "self-check")
    if len(unknown["unchecked"]) != 1:
        raise SystemExit("Самопроверка: неизвестный шифр должен остаться непроверенным")


def main() -> None:
    cards = load_cards(CARDS_PATH)
    self_check(cards)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    runs = []
    for path in sorted(LINES_DIR.glob("*.lines.txt")):
        runs.append(check_text(path.read_text(encoding="utf-8"), cards, path.name))
    pdfs = sorted(INBOX.glob("*.pdf"))
    for pdf in pdfs:
        for number, text in read_pdf_pages(pdf):
            runs.append(check_text(text, cards, f"{pdf.name} стр. {number}"))
    if pdfs and not any(item["source"].endswith("стр. 1") for item in runs):
        print("pdfplumber не установлен, PDF не прочитан")

    dirty = [item for item in runs if item["findings"]]
    clean_runs = []
    for item in dirty:
        source_text = ""
        if item["source"].endswith(".lines.txt"):
            source_text = (LINES_DIR / item["source"]).read_text(encoding="utf-8")
        else:
            # Повтор только по уже найденным цитатам: тот же текст, название из карточки.
            source_text = "\n".join(
                f"{row['code_in_project']} «{row['title_in_project']}»"
                for row in item["findings"]
            )
        fixed = corrected_text(source_text, item["findings"])
        again = check_text(fixed, cards, item["source"] + " название из карточки")
        clean_runs.append(
            {
                "source": again["source"],
                "findings": len(again["findings"]),
                "unchecked": len(again["unchecked"]),
            }
        )
        if again["findings"]:
            raise SystemExit("Чистый текст снова дал замечание: " + again["source"])

    report = {"runs": runs, "clean_copies": clean_runs}
    (OUT_DIR / "findings.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    hit_n = sum(len(item["findings"]) for item in runs)
    print(f"findings {hit_n} sources {len(runs)} clean_copies {len(clean_runs)}")
    for item in runs:
        if item["findings"] or item["unchecked"]:
            print(item["source"], "findings", len(item["findings"]), "unchecked", len(item["unchecked"]))
            for finding in item["findings"]:
                print(
                    " ",
                    finding["code_in_project"],
                    finding["title_in_project"],
                    "=>",
                    finding["title_in_catalog"],
                )


if __name__ == "__main__":
    sys.exit(main())
