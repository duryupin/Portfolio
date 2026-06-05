# Phase 1 — Следующий блок для ревью (после chunk_text + extract_clause)

Пользователь утвердил chunk_text и extract_clause. Теперь показываем остальное.

## 1. fix_hyphenation (новая функция)

```python
def fix_hyphenation(text: str) -> str:
    """Склеивает переносы слов в PDF (дефис в конце строки)."""
    if not text:
        return text
    # После flatten (\n -> space) часто остаётся "слово- слово"
    text = re.sub(r'(\w)[-\u00ad]\s+(\w)', r'\1\2', text)
    # Иногда остаётся просто "слово-слово" с дефисом без пробела — оставляем (это может быть составное слово)
    return text
```

## 2. Улучшенный extract_text_and_tables_from_pdf

```python
def extract_text_and_tables_from_pdf(file_path: str) -> Tuple[str, List[List[List[str]]]]:
    text_parts = []
    tables = []
    try:
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                try:
                    page_text = page.extract_text() or ""
                except Exception as e:
                    logger.warning(f"Ошибка извлечения текста: {e}")
                    page_text = ""

                # Не просто replace \n на space — лучше собираем строки
                # + сразу фиксим дефисы
                page_text = fix_hyphenation(page_text)
                # Схлопываем множественные пробелы, но сохраняем абзацы грубо
                page_text = re.sub(r'[ \t]+', ' ', page_text)
                page_text = re.sub(r'\n{2,}', '\n\n', page_text).strip()

                if page_text:
                    text_parts.append(page_text)

                try:
                    page_tables = page.extract_tables(table_settings={
                        "vertical_strategy": "text",
                        "horizontal_strategy": "text",
                        "snap_tolerance": 5,
                        "text_tolerance": 3,
                        "intersection_tolerance": 3,
                    }) or []
                    for t in page_tables:
                        if any(any(cell for cell in row) for row in t):
                            tables.append(t)
                except Exception as e:
                    logger.warning(f"Ошибка извлечения таблиц: {e}")
    except Exception as e:
        logger.error(f"Не удалось открыть PDF {file_path}: {e}")
    return "\n\n".join(text_parts), tables
```

(Обратите внимание: теперь возвращаем с \n\n между страницами — это помогает sentence splitter'у.)

## 3. Простой boilerplate removal (опционально, но рекомендуется)

Можно добавить простую версию (глобальный список + эвристика по повторяющимся строкам в документе). Для старта — хотя бы жёсткий фильтр в clean_text.

Пример лёгкой версии:

```python
BOILERPLATE_FRAGMENTS = [
    "МИНИСТЕРСТВО СТРОИТЕЛЬСТВА",
    "СВОД ПРАВИЛ",
    "Издание официальное",
    "Введен в действие",
    ".....",
]

def remove_boilerplate(text: str) -> str:
    for frag in BOILERPLATE_FRAGMENTS:
        text = text.replace(frag, "")
    # Убираем строки, которые выглядят как чистое оглавление
    lines = text.splitlines()
    cleaned_lines = [l for l in lines if not (l.strip().startswith(".....") or l.strip() == "")]
    return "\n".join(cleaned_lines)
```

Вызывать после fix_hyphenation: `cleaned = remove_boilerplate(cleaned)`

## 4. Предлагаемые новые константы в core/config.py

Добавить после существующих:

```python
# Новые настройки чанкинга (Phase 1)
CHUNK_TARGET_WORDS = 400
CHUNK_OVERLAP_WORDS = 70
MIN_CHUNK_WORDS = 15          # отсекаем совсем мелкие
TABLE_MAX_ROWS_PER_CHUNK = 10 # для больших таблиц
```

(Старые CHUNK_SIZE / CHUNK_OVERLAP можно оставить для совместимости или убрать позже.)

В process_pdf_index.py потом использовать:

```python
chunks = chunk_text(cleaned, CHUNK_TARGET_WORDS, CHUNK_OVERLAP_WORDS)
```

## 5. План применения (после утверждения этого файла)

1. Добавить fix_hyphenation, улучшить extract_text_and_tables..., clean_text.
2. Добавить remove_boilerplate (простую версию).
3. Добавить новые константы в config.py.
4. Обновить вызовы в process_pdfs (использовать новые константы + fix/remove).
5. (Опционально) улучшить clean_table_text.
6. Reindex.
7. Прогон eval + сравнение с baseline + ручные тесты.

---

**Твой вердикт:**

- [ ] Утверждаю этот блок — применяем (fix + extract_text + boilerplate + config)
- [ ] Правки (укажи)
- [ ] Хочу увидеть полный unified diff всего Phase 1 сразу
- [ ] Применить только предобработку сейчас, чанкинг/константы — позже

Файл ревью: PHASE1_PROPOSED_rest_preprocessing.md
