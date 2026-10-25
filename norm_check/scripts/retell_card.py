# -*- coding: utf-8 -*-
"""Этап 9. Пересказ уже готовой карточки.

В запрос уходят только цитата, пункт и факт с чертежа.
Ответ с новым шифром СП, новым пунктом или без опоры на цитату отбрасывается.
Такой ответ в правило не записывается.

Ключ читается из .env в папке norm_check, не из Директа и не из соседних проектов:
  YC_API_KEY
  YC_FOLDER_ID
Модель по умолчанию YandexGPT Pro 5: gpt://<каталог>/yandexgpt/latest.
Так же она задана в телеграм-боте. Lite (yandexgpt-lite) не используется.
Другую модель можно задать через YC_MODEL или полным YC_MODEL_URI.

Живые ответы копятся в reports/stage9/filter.json.
Новый вызов идёт, пока фильтр не оставил десять пересказов.
После этого повторный запуск модель не вызывает.

Запуск из корня norm_check:
  .venv\\Scripts\\python.exe scripts\\retell_card.py
"""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
CARD_PATH = ROOT / "reports" / "stage5" / "cards.json"
OUT_DIR = ROOT / "reports" / "stage9"
TARGET_KEPT = 10
MAX_NEW_CALLS = 18
ENDPOINT = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
DEFAULT_MODEL = "yandexgpt"
DECLINE = "нет опоры на цитату"

SP_CODE = re.compile(r"СП\s*(\d+\.\d+)(?:\.(\d{4}))?", re.IGNORECASE)
CLAUSE = re.compile(r"(?<!\d)(\d+\.\d+)(?!\d)")
WORD = re.compile(r"[0-9A-Za-zА-Яа-яЁё-]+")


def squeeze(text: str) -> str:
    folded = (text or "").lower().replace("ё", "е")
    return "".join(char for char in folded if char.isalnum())


def words(text: str) -> list[str]:
    return [item.lower().replace("ё", "е") for item in WORD.findall(text or "")]


def read_env(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    found = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        found[key.strip()] = value.strip().strip('"').strip("'")
    return found


def mentions(text: str) -> set[str]:
    found = set()
    for match in SP_CODE.finditer(text or ""):
        number = match.group(1)
        year = match.group(2)
        found.add(number + (("." + year) if year else ""))
    return found


def without_sp(text: str) -> str:
    return SP_CODE.sub(" ", text or "")


def clauses(text: str) -> set[str]:
    return set(CLAUSE.findall(without_sp(text)))


def sp_allowed(found: str, allowed: set[str]) -> bool:
    if found in allowed:
        return True
    return any(item.startswith(found + ".") for item in allowed)


def grounded(answer: str, quote: str) -> bool:
    quote_words = words(quote)
    if len(quote_words) < 4:
        return False
    need = 4 if len(quote_words) < 8 else 6
    answer_words = " ".join(words(answer))
    for start in range(0, len(quote_words) - need + 1):
        if " ".join(quote_words[start : start + need]) in answer_words:
            return True
    return False


def drawing_fact(card: dict) -> str:
    return (
        f"У шифра {card['code_in_project']} написано «{card['title_in_project']}». "
        f"Название этого шифра в карточке: «{card['title_in_catalog']}»."
    )


def build_prompt(quote: str, clause: str, fact: str) -> str:
    return (
        "Перескажи факт чертежа в двух или трёх предложениях. "
        "Опирайся только на цитату ниже. "
        "Не называй шифр и пункт, которых нет в этих трёх полях. "
        f"Если цитаты не хватает, ответь ровно: {DECLINE}.\n"
        f"Пункт: {clause}\n"
        f"Факт с чертежа: {fact}\n"
        f"Цитата: {quote}"
    )


def judge(quote: str, clause: str, fact: str, answer: str) -> dict:
    text = (answer or "").strip()
    allowed_sp = mentions(quote) | mentions(fact)
    allowed_clauses = clauses(quote) | clauses(fact) | clauses(clause)
    extra_sp = sorted(code for code in mentions(text) if not sp_allowed(code, allowed_sp))
    extra_clauses = sorted(code for code in clauses(text) if code not in allowed_clauses)
    if squeeze(text) == squeeze(DECLINE):
        status = "declined"
    elif extra_sp or extra_clauses or not grounded(text, quote):
        status = "rejected"
    else:
        status = "kept"
    return {
        "status": status,
        "extra_sp": extra_sp,
        "extra_clauses": extra_clauses,
        "grounded": grounded(text, quote),
    }


def tasks_from_card(card: dict) -> list[dict]:
    fact = drawing_fact(card)
    tasks = []
    for note in card.get("footnotes") or []:
        if not note.get("footnote_ok"):
            continue
        quote = note.get("quote") or ""
        clause = str(note.get("clause") or "")
        if not quote or not clause:
            continue
        tasks.append(
            {
                "source": "card",
                "code": note.get("code") or "",
                "clause": clause,
                "quote": quote,
                "fact": fact,
                "prompt": build_prompt(quote, clause, fact),
            }
        )
    return tasks


def model_uri(folder: str, env: dict[str, str]) -> str:
    explicit = (env.get("YC_MODEL_URI") or "").strip()
    if explicit:
        return explicit
    model = (env.get("YC_MODEL") or DEFAULT_MODEL).strip().strip("/")
    if model.endswith(("/latest", "/rc", "/deprecated")):
        name = model
    else:
        name = f"{model}/latest"
    return f"gpt://{folder}/{name}"


def complete(prompt: str, env: dict[str, str]) -> str:
    folder = env.get("YC_FOLDER_ID", "")
    key = env.get("YC_API_KEY", "")
    if not key or not folder:
        raise RuntimeError("В .env этой папки нет YC_API_KEY или YC_FOLDER_ID")
    body = json.dumps(
        {
            "modelUri": model_uri(folder, env),
            "completionOptions": {"stream": False, "temperature": 0.1, "maxTokens": "400"},
            "messages": [{"role": "user", "text": prompt}],
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        ENDPOINT,
        data=body,
        headers={
            "Authorization": f"Api-Key {key}",
            "x-folder-id": folder,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return (
        payload.get("result", {})
        .get("alternatives", [{}])[0]
        .get("message", {})
        .get("text", "")
        .strip()
    )


def expect(name: str, got: str, wanted: str) -> None:
    if got != wanted:
        raise SystemExit(f"Самопроверка {name}: {got}, ожидалось {wanted}")


def self_check() -> list[dict]:
    fact = "У шифра СП 1.10000.2000 написано «одно». Название этого шифра в карточке: «много»."
    quote = (
        "Этот проверочный текст говорит только о многоквартирных домах "
        "и называет внутри себя СП 12.00000 без года."
    )
    quote_pair = "Пункты 1.1 и 1.2 этого проверочного текста относятся к одному и тому же предмету."
    span = "говорит только о многоквартирных домах и называет"
    rows = [
        ("опора на цитату", quote, "1.1", fact, f"Пункт 1.1. {span} внутри СП 12.00000.", "kept"),
        ("чужой шифр", quote, "1.1", fact, f"{span}. См. СП 60.13330.2020.", "rejected"),
        ("чужой пункт", quote, "1.1", fact, f"Пункт 5.16. {span}.", "rejected"),
        ("нет опоры", quote, "1.1", fact, "На листе другая подпись, цитату не повторяю.", "rejected"),
        ("отказ модели", quote, "1.1", fact, DECLINE, "declined"),
        ("пункт не из входа", quote, "1.1", fact, f"Пункт 1.2. {span}.", "rejected"),
        ("оба пункта в цитате", quote_pair, "1.1", fact, "Пункты 1.1 и 1.2 этого проверочного текста относятся к одному.", "kept"),
        ("шифр из цитаты", quote, "1.1", fact, f"{span} СП 12.00000.", "kept"),
        ("год не из входа", quote, "1.1", fact, f"{span}. СП 12.00000.2016.", "rejected"),
        ("шифр из факта", quote, "1.1", fact, f"У шифра СП 1.10000.2000 написано «одно». {span}.", "kept"),
    ]
    report = []
    if len(rows) != 10:
        raise SystemExit(f"Самопроверка: карточек {len(rows)}, нужно 10")
    for name, sample_quote, clause, sample_fact, answer, wanted in rows:
        verdict = judge(sample_quote, clause, sample_fact, answer)
        expect(name, verdict["status"], wanted)
        prompt = build_prompt(sample_quote, clause, sample_fact)
        if "http" in prompt or "edition_note" in prompt:
            raise SystemExit("Самопроверка: в запрос попало лишнее поле")
        report.append({"name": name, "status": verdict["status"], "ok": True})
    prompt = build_prompt(quote, "1.1", fact)
    if "5.16" in prompt or "СП 60" in prompt:
        raise SystemExit("Самопроверка: запрос содержит чужой номер")
    uri = model_uri("folder", {})
    if uri != "gpt://folder/yandexgpt/latest" or "lite" in uri:
        raise SystemExit(f"Самопроверка: модель не Pro 5: {uri}")
    return report


def previous_live() -> list:
    path = OUT_DIR / "filter.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    rows = data.get("live")
    return list(rows) if isinstance(rows, list) else []


def kept_count(rows: list) -> int:
    return sum(1 for row in rows if row.get("status") == "kept")


def main() -> int:
    checks = self_check()
    env = read_env(ENV_PATH)
    live = previous_live()
    new_calls = 0
    live_note = "ключа нет, живой пересказ не запрашивался"
    if env.get("YC_API_KEY") and env.get("YC_FOLDER_ID") and kept_count(live) < TARGET_KEPT:
        if not CARD_PATH.is_file():
            raise SystemExit("Нет карточки этапа 5")
        cards = json.loads(CARD_PATH.read_text(encoding="utf-8")).get("cards") or []
        if len(cards) != 1:
            raise SystemExit(f"Ожидалась одна карточка, в файле {len(cards)}")
        tasks = tasks_from_card(cards[0])
        if not tasks:
            raise SystemExit("В карточке нет сносок для пересказа")
        live_note = "запрошен пересказ сносок карточки"
        while kept_count(live) < TARGET_KEPT and new_calls < MAX_NEW_CALLS:
            stop = False
            for task in tasks:
                if kept_count(live) >= TARGET_KEPT or new_calls >= MAX_NEW_CALLS:
                    break
                new_calls += 1
                try:
                    answer = complete(task["prompt"], env)
                except (urllib.error.URLError, RuntimeError, TimeoutError, json.JSONDecodeError) as error:
                    live.append(
                        {
                            "code": task["code"],
                            "clause": task["clause"],
                            "status": "error",
                            "detail": str(error),
                        }
                    )
                    print(
                        f"call {new_calls} {task['code']} п. {task['clause']} error kept {kept_count(live)}",
                        flush=True,
                    )
                    stop = True
                    break
                verdict = judge(task["quote"], task["clause"], task["fact"], answer)
                row = {"code": task["code"], "clause": task["clause"], "status": verdict["status"]}
                if verdict["status"] == "kept":
                    row["text"] = answer
                else:
                    row["extra_sp"] = verdict["extra_sp"]
                    row["extra_clauses"] = verdict["extra_clauses"]
                live.append(row)
                print(
                    f"call {new_calls} {task['code']} п. {task['clause']} {verdict['status']} kept {kept_count(live)}",
                    flush=True,
                )
            if stop:
                break
    elif env.get("YC_API_KEY") and env.get("YC_FOLDER_ID"):
        live_note = "десяти оставленных уже хватает, новый вызов не делался"
    kept = kept_count(live)
    report = {
        "filter_cards": len(checks),
        "filter_ok": all(row["ok"] for row in checks),
        "checks": checks,
        "live_note": live_note,
        "new_calls": new_calls,
        "live": live,
        "live_kept": kept,
        "closed": kept >= TARGET_KEPT,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "filter.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"filter {len(checks)} ok live {len(live)} kept {kept} new {new_calls} closed {kept >= TARGET_KEPT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
