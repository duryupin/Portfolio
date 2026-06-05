# process_pdfs_light.py
"""
Оптимизированная версия pipeline для извлечения текста из PDF, создания эмбеддингов
и сохранения индекса FAISS, адаптированная под ноутбуки с ограниченными ресурсами.

ФАЙЛ объединяет удобные утилиты и точки расширения (кэширование эмбеддингов,
безопасная синхронизация документов и эмбеддингов, опция выбора индекса FAISS).
"""

import sys
import os
import json
import numpy as np
import faiss
import re
from tqdm import tqdm
from pathlib import Path
import pdfplumber
from sentence_transformers import SentenceTransformer
import logging
import warnings
from typing import List, Tuple

warnings.filterwarnings("ignore", category=FutureWarning, module="transformers")

logging.basicConfig(
    level=logging.INFO,
    filename="process_light.log",
    format="%(asctime)s - %(levelname)s - %(message)s",
    encoding="utf-8"
)
logger = logging.getLogger(__name__)

sys.path.append(str(Path(__file__).absolute().parent.parent)) 

from core.config import *

# === Конфиг (подправь пути под свой проект) ===

PDF_DIR.mkdir(parents=True, exist_ok=True) 
DATA_DIR.mkdir(parents=True, exist_ok=True)
BATCH_SIZE = 2  # маленький батч для слабых машин
DEVICE = "cpu"  # явно указываем cpu
USE_NORMALIZE = True  # нормализуем эмбеддинги => используем IndexFlatIP

PDF_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

# === Утилиты ===

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


def clean_table_text(table: List[List]) -> str:
    """Простая сериализация таблицы. Для больших таблиц сплит делается на уровне чанкинга."""
    if not table:
        return ""
    cleaned_rows = []
    for row in table:
        cleaned_row = " | ".join(str(cell).strip() if cell is not None else "" for cell in row)
        if cleaned_row.strip():
            cleaned_rows.append(cleaned_row)
    return "\n".join(cleaned_rows)


def split_large_table(table: List[List], max_rows: int = 10) -> List[List[List]]:
    """Разбивает большую таблицу на куски с повтором заголовка (если есть)."""
    if not table or len(table) <= max_rows + 1:
        return [table]
    header = table[0] if len(table) > 1 else None
    chunks = []
    start = 1 if header else 0
    for i in range(start, len(table), max_rows):
        piece = []
        if header:
            piece.append(header)
        piece.extend(table[i:i + max_rows])
        chunks.append(piece)
    return chunks


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

                # Улучшенная обработка: фиксим дефисы + не разрушаем всё в одну строку
                page_text = fix_hyphenation(page_text)
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


def clean_text(text: str) -> str:
    # Не приводим в lower, т.к. в нормативных документах важна регистрозависимая аббревиатура
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'[\x00-\x1F\x7F-\x9F]', '', text)
    return text.strip()


def fix_hyphenation(text: str) -> str:
    """Склеивает переносы слов в PDF (дефис в конце строки)."""
    if not text:
        return text
    # После flatten часто остаётся "слово- слово"
    text = re.sub(r'(\w)[-\u00ad]\s+(\w)', r'\1\2', text)
    return text


BOILERPLATE_FRAGMENTS = [
    "МИНИСТЕРСТВО СТРОИТЕЛЬСТВА И ЖИЛИЩНО-КОММУНАЛЬНОГО ХОЗЯЙСТВА РОССИЙСКОЙ ФЕДЕРАЦИИ",
    "СВОД ПРАВИЛ",
    "Издание официальное",
    "Введен в действие",
    "ВВЕДЕН ВПЕРВЫЕ",
]


def remove_boilerplate(text: str) -> str:
    """Удаляет типичный boilerplate и строки оглавления."""
    for frag in BOILERPLATE_FRAGMENTS:
        text = text.replace(frag, "")
    # Убираем строки, которые выглядят как чистое оглавление
    lines = text.splitlines()
    cleaned = []
    for l in lines:
        s = l.strip()
        if not s:
            continue
        if s.startswith(".....") or (s.count(".") > 8 and len(s) > 20):
            continue
        cleaned.append(l)
    return "\n".join(cleaned)


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """Структурный + sentence-aware чанкинг.

    - Приоритет: заголовки пунктов/приложений (структурный сплит).
    - Группируем только целые предложения.
    - Размер контролируем по количеству слов (target ~ chunk_size).
    - Никогда не режем предложение посередине.
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


# === Основная функция ===

def process_pdfs(recreate_index: bool = True):
    logger.info("Start processing PDFs (light)")
    model = SentenceTransformer(EMBEDDING_MODEL, device=DEVICE)
    logger.info(f"Loaded model {EMBEDDING_MODEL} on {DEVICE}")

    documents = []
    batch_texts = []

    for file in tqdm(sorted(os.listdir(PDF_DIR)), desc="pdfs"):
        if not file.lower().endswith('.pdf'):
            continue
        p = PDF_DIR / file
        logger.info(f"Processing {p}")
        raw_text, tables = extract_text_and_tables_from_pdf(str(p))
        if not raw_text.strip() and not tables:
            logger.warning(f"Empty file or failed extraction: {file}")
            continue
        cleaned = clean_text(raw_text)
        cleaned = fix_hyphenation(cleaned)
        cleaned = remove_boilerplate(cleaned)
        # Используем новые целевые размеры (Phase 1). Старые CHUNK_SIZE/OVERLAP оставлены для совместимости.
        chunks = chunk_text(cleaned, CHUNK_TARGET_WORDS, CHUNK_OVERLAP_WORDS)
        # Добавляем текстовые чанки
        for i, chunk in enumerate(chunks):
            if not chunk.strip():
                continue
            doc = {
                "doc_id": f"{file}_chunk_{i}",
                "source": file,
                "chunk_index": i,
                "text": chunk,
                "is_table": False,
                "clause": extract_clause(chunk)
            }
            documents.append(doc)
            batch_texts.append(chunk)
        # Добавляем таблицы (с опциональным сплитом больших таблиц + повтор заголовка)
        table_counter = 0
        for table in tables:
            for sub_table in split_large_table(table, TABLE_MAX_ROWS_PER_CHUNK):
                table_text = clean_table_text(sub_table)
                if not table_text.strip():
                    continue
                doc = {
                    "doc_id": f"{file}_table_{table_counter}",
                    "source": file,
                    "chunk_index": f"table_{table_counter}",
                    "text": table_text,
                    "is_table": True,
                    "table_data": sub_table,
                    "clause": extract_clause(table_text)
                }
                documents.append(doc)
                batch_texts.append(table_text)
                table_counter += 1

    logger.info(f"Total documents (pre-embeddings): {len(documents)}")

    # БЭЧ кодирование с кэшем
    embeddings = []
    if EMBEDDINGS_NPY.exists() and METADATA_PATH.exists():
        try:
            cached_meta = json.load(open(METADATA_PATH, 'r', encoding='utf-8'))
            if len(cached_meta) == len(documents):
                logger.info("Metadata size equals current docs length — загружаем кеш эмбеддингов")
                embeddings = np.load(EMBEDDINGS_NPY)
            else:
                logger.info("Кеш не соответствует текущим документам — перегенерируем эмбеддинги")
        except Exception:
            logger.warning("Не удалось загрузить кеш — пересоздаем")

    if len(embeddings) == 0:
        # Кодируем по батчам
        for i in tqdm(range(0, len(batch_texts), BATCH_SIZE), desc="encoding"):
            batch = batch_texts[i:i + BATCH_SIZE]
            try:
                embs = model.encode(batch, show_progress_bar=False, normalize_embeddings=USE_NORMALIZE)
                embeddings.append(np.array(embs, dtype='float32'))
            except Exception as e:
                logger.error(f"Ошибка кодирования батча {i//BATCH_SIZE}: {e}")
        if embeddings:
            embeddings = np.vstack(embeddings)
            np.save(EMBEDDINGS_NPY, embeddings)
            logger.info(f"Saved embeddings npy: {EMBEDDINGS_NPY}")
        else:
            embeddings = np.zeros((0, model.get_sentence_embedding_dimension()), dtype='float32')

    # Синхронизация длины
    if embeddings.shape[0] != len(documents):
        logger.error(f"Mismatch embeddings ({embeddings.shape[0]}) vs documents ({len(documents)})")
        # Попытка простой коррекции: если метаданные в кеше совпадают, заменить documents
        if EMBEDDINGS_NPY.exists() and METADATA_PATH.exists():
            try:
                cached_meta = json.load(open(METADATA_PATH, 'r', encoding='utf-8'))
                if len(cached_meta) == embeddings.shape[0]:
                    logger.info("Применяем cached_meta вместо current documents")
                    documents = cached_meta
                else:
                    raise ValueError("Не удалось синхронизировать записи")
            except Exception as e:
                raise ValueError("Embeddings/documents mismatch and cannot auto-repair: " + str(e))

    # Создаем индекс FAISS (FlatIP для нормализованных векторов / cosine similarity)
    if recreate_index or not INDEX_PATH.exists():
        dim = embeddings.shape[1]
        logger.info(f"Creating FAISS index dim={dim}")
        # Простая, но точная структура для небольших наборов
        index = faiss.IndexFlatIP(dim)
        # Для больших наборов можно заменить на IndexHNSWFlat или IVF+PQ (см. комментарии)
        index.add(embeddings.astype('float32'))
        faiss.write_index(index, str(INDEX_PATH))
        logger.info(f"Wrote index to {INDEX_PATH}")

        # Сохраняем метаданные
        with open(METADATA_PATH, 'w', encoding='utf-8') as f:
            json.dump(documents, f, ensure_ascii=False, indent=2)
        logger.info(f"Wrote metadata to {METADATA_PATH}")
    else:
        logger.info("Index file already exists and recreate_index=False — пропускаем создание")


if __name__ == '__main__':
    process_pdfs()