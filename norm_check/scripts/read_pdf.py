# -*- coding: utf-8 -*-
"""Этап 2: страницы PDF, текстовый слой, штамп, картинка выбранного листа.

Запуск из корня norm_check тем интерпретатором, где стоят pdfplumber и pypdfium2
(сейчас это venv соседнего sp_docs_llm).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium

ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "inbox"
OUT_DIR = ROOT / "reports" / "stage2"


def find_pdf() -> Path:
    pdfs = sorted(INBOX.glob("*.pdf"))
    if len(pdfs) != 1:
        raise SystemExit(f"Ожидался один PDF в inbox, найдено: {len(pdfs)}")
    return pdfs[0]


def stamp_hits(text: str) -> list[str]:
    keys = (
        "1/05-26-ВиК",
        "Общие данные",
        "Вентиляция. План",
        "Кондиционирование. План",
        "Вентиляция. Схемы",
        "Кондиционирование. Схема",
        "Спецификация",
    )
    return [key for key in keys if key in text]


def main() -> None:
    pdf_path = find_pdf()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pages = []
    with pdfplumber.open(pdf_path) as doc:
        for index, page in enumerate(doc.pages, start=1):
            text = page.extract_text() or ""
            pages.append(
                {
                    "page": index,
                    "width_pt": round(page.width, 1),
                    "height_pt": round(page.height, 1),
                    "text_chars": len(text.strip()),
                    "stamp": stamp_hits(text),
                }
            )
    target = 3
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        bitmap = doc[target - 1].render(scale=120 / 72)
        image = bitmap.to_pil()
        image_path = OUT_DIR / f"page-{target:02d}.png"
        image.save(image_path)
    finally:
        doc.close()
    report = {
        "file": pdf_path.name,
        "pages": len(pages),
        "text_layer_pages": sum(1 for item in pages if item["text_chars"] > 0),
        "rendered_page": target,
        "image": str(image_path.relative_to(ROOT)),
        "page_rows": pages,
    }
    (OUT_DIR / "pages.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"pages {report['pages']} text_pages {report['text_layer_pages']}")
    print(f"image {image_path}")


if __name__ == "__main__":
    sys.exit(main())
