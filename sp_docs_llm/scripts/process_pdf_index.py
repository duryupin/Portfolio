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
    # Более гибкая регулярка: поддерживает 1.2, 1.2а, 1.2.3-1, Приложение A
    m = re.search(r'(приложен[ие|ия]\s*[A-Za-zА-Яа-я0-9\-]+|\d+(?:\.\d+)*[A-Za-zА-Яа-я\-]*)', text)
    return m.group(0) if m else None


def clean_table_text(table: List[List]) -> str:
    cleaned_rows = []
    for row in table:
        cleaned_row = " | ".join(str(cell).strip() if cell is not None else "" for cell in row)
        if cleaned_row.strip():
            cleaned_rows.append(cleaned_row)
    return "\n".join(cleaned_rows)


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
                # Убираем лишние переносы строк, но сохраняем точки
                page_text = page_text.replace('\n', ' ')
                if page_text.strip():
                    text_parts.append(page_text)
                try:
                    page_tables = page.extract_tables() or []
                    for t in page_tables:
                        # фильтруем пустые таблицы
                        if any(any(cell for cell in row) for row in t):
                            tables.append(t)
                except Exception as e:
                    logger.warning(f"Ошибка извлечения таблиц: {e}")
    except Exception as e:
        logger.error(f"Не удалось открыть PDF {file_path}: {e}")
    return " ".join(text_parts), tables


def clean_text(text: str) -> str:
    # Не приводим в lower, т.к. в нормативных документах важна регистрозависимая аббревиатура
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'[\x00-\x1F\x7F-\x9F]', '', text)
    return text.strip()


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    # Более робустный разбиение: сначала пытаемся по предложениям, иначе грубо по словам
    # Снижаем чувствительность к отсутствию нормальных пробелов в PDF
    # Разбиваем по точкам с учётом русской/лат латиницы
    sents = re.split(r'(?<=[.!?;])\s+(?=[А-ЯA-Z0-9\(\"\'])', text)
    if len(sents) < 2:
        sents = text.split()  # fallback
    chunks = []
    cur = []
    cur_len = 0
    for sent in sents:
        words = sent.split()
        wlen = len(words)
        if cur_len + wlen > chunk_size and cur:
            chunks.append(' '.join(cur).strip())
            # сохраняем overlap последних предложений/слов
            cur = cur[-max(0, overlap // 10):]  # простая эвристика
            cur_len = sum(len(s.split()) for s in cur)
        cur.append(sent)
        cur_len += wlen
    if cur:
        chunks.append(' '.join(cur).strip())
    return chunks


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
        chunks = chunk_text(cleaned)
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
        # Добавляем таблицы
        for tidx, table in enumerate(tables):
            table_text = clean_table_text(table)
            if not table_text.strip():
                continue
            doc = {
                "doc_id": f"{file}_table_{tidx}",
                "source": file,
                "chunk_index": f"table_{tidx}",
                "text": table_text,
                "is_table": True,
                "table_data": table,
                "clause": extract_clause(table_text)
            }
            documents.append(doc)
            batch_texts.append(table_text)

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