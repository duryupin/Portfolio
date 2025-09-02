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
from nltk.tokenize import sent_tokenize

# Подавляем предупреждения от transformers
warnings.filterwarnings("ignore", category=FutureWarning, module="transformers")

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    filename="process.log",
    format="%(asctime)s - %(levelname)s - %(message)s",
    encoding="utf-8"
)
logger = logging.getLogger(__name__)

sys.path.append(str(Path(__file__).absolute().parent.parent))

from core.config import *

def extract_clause(text: str) -> str | None:
    """Извлекает номер пункта (clause) из текста"""
    match = re.search(r'(\d+\.\d+\.\d+|\d+\.\d+)', text)
    return match.group(0) if match else None

def clean_table_text(table: list) -> str:
    """Объединяет фрагментированный текст таблицы в связный текст"""
    cleaned_rows = []
    for row in table:
        # Заменяем None на пустую строку и объединяем ячейки в одну строку
        cleaned_row = " ".join(str(cell) if cell is not None else "" for cell in row).strip()
        if cleaned_row:
            cleaned_rows.append(cleaned_row)
    # Объединяем строки таблицы в одну, добавляя переносы для читаемости
    return "\n".join(cleaned_rows)

def process_pdfs():
    """
    Основная функция обработки PDF-документов и создания индекса
    """
    logger.info("Начало обработки PDF-документов")
    model = SentenceTransformer(EMBEDDING_MODEL)
    logger.info(f"Модель {EMBEDDING_MODEL}                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  загружена")
    
    documents = []
    embeddings = []
    batch_size = 16  # Уменьшено для оптимизации памяти
    
    for file in tqdm(os.listdir(PDF_DIR), desc="Обработка документов"):
        if file.endswith(".pdf"):
            file_path = os.path.join(PDF_DIR, file)
            logger.info(f"Обработка файла {file}")
            text, tables = extract_text_and_tables_from_pdf(file_path)
            cleaned_text = clean_text(text)
            chunks = chunk_text(cleaned_text, CHUNK_SIZE, CHUNK_OVERLAP)
            logger.info(f"Извлечено {len(chunks)} чанков и {len(tables)} таблиц из {file}")
            
            for i, chunk in enumerate(chunks):
                if not chunk.strip():
                    logger.warning(f"Пропущен пустой чанк {i} в {file}")
                    continue
                clause = extract_clause(chunk)
                documents.append({
                    "doc_id": f"{file}_{i}",
                    "source": file,
                    "chunk_index": i,
                    "text": chunk,
                    "is_table": False,
                    "clause": clause
                })
            
            for table_idx, table_rows in enumerate(tables):
                table_text = clean_table_text(table_rows)
                if not table_text.strip():
                    logger.warning(f"Пропущена пустая таблица {table_idx} в {file}")
                    continue
                clause = extract_clause(table_text)
                documents.append({
                    "doc_id": f"{file}_table_{table_idx}",
                    "source": file,
                    "chunk_index": f"table_{table_idx}",
                    "text": table_text,
                    "is_table": True,
                    "table_data": table_rows,
                    "clause": clause
                })
            
            all_texts = [doc["text"] for doc in documents if doc["source"] == file and doc["text"].strip()]
            if not all_texts:
                logger.warning(f"Нет текстов для создания эмбеддингов в {file}")
                continue
            for i in range(0, len(all_texts), batch_size):
                batch = all_texts[i:i + batch_size]
                try:
                    batch_embeddings = model.encode(batch, 
                                                    show_progress_bar=False, 
                                                    normalize_embeddings=True)
                    embeddings.extend(batch_embeddings)
                    logger.info(f"Создано {len(batch_embeddings)} эмбеддингов для пакета из {file}")
                except Exception as e:
                    logger.error(f"Ошибка при создании эмбеддингов для пакета в {file}: {e}")
                    continue
    
    logger.info(f"Всего обработано документов: {len(documents)}")
    save_index(documents, embeddings)

def extract_text_and_tables_from_pdf(file_path):
    """Извлекает текст и таблицы из PDF, игнорируя изображения"""
    text = ""
    tables = []
    try:
        with pdfplumber.open(file_path) as pdf:
            for page_num, page in enumerate(pdf.pages, 1):
                page_has_content = False
                try:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
                        page_has_content = True
                except Exception as e:
                    logger.warning(f"Ошибка извлечения текста на странице {page_num} в {file_path}: {e}")
                
                try:
                    page_tables = page.extract_tables()
                    for table in page_tables:
                        if table and any(any(cell for cell in row) for row in table):
                            tables.append([row for row in table])
                            page_has_content = True
                    logger.info(f"Извлечено {len(page_tables)} таблиц на странице {page_num} в {file_path}")
                except Exception as e:
                    logger.warning(f"Ошибка извлечения таблиц на странице {page_num} в {file_path}: {e}")
                
                if not page_has_content:
                    logger.warning(f"Страница {page_num} в {file_path} не содержит текста или таблиц")
            
        if not text.strip() and not tables:
            logger.warning(f"Не удалось извлечь текст или таблицы из {file_path}")
    except Exception as e:
        logger.error(f"Ошибка при обработке {file_path}: {e}")
    return text, tables

def clean_text(text) -> str:
    """Очистка и нормализация текста"""
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'[\x00-\x1F\x7F-\x9F]', '', text)
    text = text.lower()

    return text.strip()

def chunk_text(text: str, chunk_size: int, overlap: int) -> list:
    """Разбивает текст на перекрывающиеся фрагменты по предложениям"""
    sentences = sent_tokenize(text, language="russian")
    chunks = []
    current_chunk = []
    current_length = 0
    for sentence in sentences:
        sentence_length = len(sentence.split())
        if current_length + sentence_length > chunk_size:
            chunk = " ".join(current_chunk)
            if chunk.strip():
                chunks.append(chunk)
                logger.info(f"Создан чанк {len(chunks)} с {len(current_chunk)} предложениями, {current_length} словами")
            current_chunk = current_chunk[-overlap:]
            current_length = sum(len(s.split()) for s in current_chunk)
        current_chunk.append(sentence)
        current_length += sentence_length
    if current_chunk:
        chunk = " ".join(current_chunk)
        if chunk.strip():
            chunks.append(chunk)
            logger.info(f"Создан последний чанк с {len(current_chunk)} предложениями, {current_length} словами")
    return chunks

def save_index(documents: list, embeddings: list):
    """Создает и сохраняет индекс FAISS"""
    if not embeddings:
        logger.error("Нет эмбеддингов для создания индекса")
        return

    embeddings_array = np.array(embeddings).astype('float32')

    if len(embeddings_array.shape) != 2:
        logger.error(f"Неправильная размерность эмбеддингов {embeddings_array.shape}")
        return
    
    dim = embeddings_array.shape[1]
    logger.info(f"Размерность векторов: {dim}")
    logger.info(f"Всего векторов: {len(embeddings_array)}")

    index = faiss.IndexFlatL2(dim)
    index.add(embeddings_array)
    
    faiss.write_index(index, str(INDEX_PATH))
    
    with open(METADATA_PATH, "w", encoding="utf-8") as f:
        json.dump(documents, f, ensure_ascii=False, indent=2)
    
    logger.info(f"Индекс создан: {len(documents)} фрагментов, {index.ntotal} векторов")

if __name__ == "__main__":
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    process_pdfs()