# Phase 1 — Предлагаемые изменения: chunk_text + extract_clause

**Цель ревью:** Утвердить конкретный код перед применением search_replace.

## Текущие проблемы (напоминание)
- extract_clause ловит случайные числа и коды документов.
- chunk_text часто не сплитит (из-за flatten + плохого regex), даёт мега-чанки до 11k слов.
- Нет структурного уважения к "п. 5.1.3", "Приложение А".

## Предлагаемые новые функции

### 1. extract_clause (замена)

```python
def extract_clause(text: str) -> str | None:
    """Извлекает наиболее вероятный номер пункта/приложения/раздела/таблицы."""
    if not text:
        return None
    # Порядок: от более специфичного к общему
    patterns = [
        r'(?:п\.?\s*|пункт\s+|пп\.\s*)(\d+(?:\.\d+)*[а-яА-Я]?)',
        r'(приложен[ие|ия]\s*[A-Za-zА-Яа-я0-9\-]+)',
        r'(?:раздел|гл\.?)\s*(\d+(?:\.\d+)*)',
        r'(?:таблиц[а|ы]?|табл\.)\s*([A-Za-zА-Яа-я0-9\.\-]+)',
        r'(\d{1,2}(?:\.\d{1,2}){1,3}[а-яА-Я]?)',  # 5.1.3.2
    ]
    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            val = m.group(1) if m.lastindex else m.group(0)
            return val.strip()
    return None
```

### 2. chunk_text (полная замена текущей)

```python
def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """Структурный + sentence-aware чанкинг.

    - Приоритет: заголовки пунктов/приложений (структурный сплит).
    - Группируем **только целые предложения**.
    - Размер контролируем по количеству слов (target ~ CHUNK_SIZE).
    - Никогда не режем предложение посередине.
    - Для таблиц вызывается отдельно (с повтором заголовка).
    """
    if not text or not text.strip():
        return []

    # === 1. Структурный сплит по заголовкам пунктов/приложений ===
    # Ищем паттерны начала пункта: "5.1.3 Текст..." или "Приложение А"
    structural = re.split(
        r'(?m)^(?=(\d{1,2}(?:\.\d{1,2}){0,3}\s+[А-ЯA-Z])|(Приложение\s+[А-ЯЁ]))',
        text
    )
    if len(structural) > 1:
        chunks: List[str] = []
        for part in structural:
            if part and part.strip():
                chunks.extend(chunk_text(part, chunk_size, overlap))
        return chunks

    # === 2. Sentence split (улучшенный, терпимый к PDF-шуму) ===
    sents = re.split(r'(?<=[.!?])\s+(?=[А-ЯA-Z0-9"\u00ab\(\)])', text)
    if len(sents) < 2:
        sents = [text]

    chunks: List[str] = []
    current: List[str] = []
    current_words = 0

    for sent in sents:
        sent = sent.strip()
        if not sent:
            continue
        w = len(sent.split())
        if current and current_words + w > chunk_size:
            chunk_str = ' '.join(current).strip()
            if chunk_str:
                chunks.append(chunk_str)
            # overlap — последние 1-2 полных предложения
            keep: List[str] = []
            keep_words = 0
            for s in reversed(current):
                sw = len(s.split())
                if keep_words + sw > max(overlap, 40):
                    break
                keep.append(s)
                keep_words += sw
            current = list(reversed(keep))
            current_words = keep_words

        current.append(sent)
        current_words += w

    if current:
        chunks.append(' '.join(current).strip())

    # Отсекаем совсем мелкие чанки (шум)
    return [c for c in chunks if len(c.split()) >= 15]
```

## Как будет выглядеть вызов (примерно)

В `process_pdfs`:

```python
cleaned = clean_text(raw_text)
# позже добавим: cleaned = fix_hyphenation(cleaned)
# cleaned = remove_common_boilerplate(cleaned, source_file)

chunks = chunk_text(cleaned, CHUNK_TARGET_WORDS, CHUNK_OVERLAP_WORDS)  # после добавления констант

for i, chunk in enumerate(chunks):
    ...
    "clause": extract_clause(chunk),
    ...
```

Аналогично для table_text.

## Что дальше (после утверждения этих двух)

1. Добавить `fix_hyphenation` + улучшение `extract_text_and_tables_from_pdf` (table_settings + не просто replace \n).
2. Добавить boilerplate removal.
3. Обновить clean_table_text (опционально).
4. Добавить новые константы в `core/config.py`.
5. Reindex + прогон `scripts/eval_retrieval.py`.
6. Ручные тесты 5-7 запросов.

---

**Решение пользователя:** (заполняется после ревью)

- [ ] Утверждаю эти две функции как есть
- [ ] Нужны правки (указать)
- [ ] Хочу увидеть весь набор изменений Phase 1 сразу

Дата ревью: ________
