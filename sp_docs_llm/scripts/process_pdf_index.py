import sys
import os
import json
import numpy as np
import faiss
import re
from tqdm import tqdm
from pathlib import Path
from PyPDF2 import PdfReader
from sentence_transformers import SentenceTransformer

sys.path.append(str(Path(__file__).absolute().parent.parent))

from core.config import *

def process_pdfs():
    """
    Основная функция обработки PDF-документов и создания индекса
    """
    # Инициализация модели
    model = SentenceTransformer(EMBEDDING_MODEL)
    
    # Подготовка структур данных
    documents = []
    embeddings = []
    
    # Обработка каждого PDF-файла
    for file in tqdm(os.listdir(PDF_DIR), desc="Обработка документов"):
        if file.endswith(".pdf"):
            file_path = os.path.join(PDF_DIR, file)
            # Извлечение текста
            text = extract_text_from_pdf(file_path)
            cleaned_text = clean_text(text)
            chunks = chunk_text(cleaned_text, CHUNK_SIZE, CHUNK_OVERLAP)
            
            # Обработка каждого чанка
            for i, chunk in enumerate(chunks):
                # Сохраняем метаданные
                documents.append({
                    "doc_id": f"{file}_{i}",
                    "source": file,
                    "chunk_index": i,
                    "text": chunk
                })
                
                # Создание эмбеддинга
                embedding = model.encode([chunk])[0]
                embeddings.append(embedding)
    
    # Создание и сохранение индекса
    save_index(documents, embeddings)

def extract_text_from_pdf(file_path):
    """Извлекает текст из PDF с сохранением структуры"""
    text = ""
    with open(file_path, "rb") as f:
        reader = PdfReader(f)
        for page in reader.pages:
            if page_text := page.extract_text():
                    text += page_text + "\n"
    return text


def clean_text(text) -> str:
    """Очистка и нормализация текста"""
    text = re.sub(r'\s+', ' ', text)  # Удаление лишних пробелов
    text = re.sub(r'[^\w\s.,;:!?()\-–%№«»§]', '', text)  # Удаление спецсимволов
    text = text.lower()

    return text.strip()
    
def chunk_text(text: str, chunk_size: int, overlap: int) -> list:
    """Разбивает текст на перекрывающиеся фрагменты"""
    words = text.split()
    chunks = []
    for i in range(0, len(words), chunk_size - overlap):
        chunk = " ".join(words[i:i+chunk_size])
        chunks.append(chunk)

    return chunks

def save_index(documents: list, embeddings: list):
    """Создает и сохраняет индекс FAISS"""
    if not embeddings:
        print("⛔ Нет эмбеддингов для создания индекса")
        return
        
    # Преобразование в numpy array
    embeddings_array = np.array(embeddings).astype('float32')
    
    # Проверка размерности
    if len(embeddings_array.shape) != 2:
        print(f"⚠️ Ошибка: Неправильная размерность эмбеддингов {embeddings_array.shape}")
        return
    
    dim = embeddings_array.shape[1]
    print(f"Размерность векторов: {dim}")
    print(f"Всего векторов: {len(embeddings_array)}")
    
    # Создание индекса
    index = faiss.IndexFlatL2(dim)
    index.add(x=embeddings_array)  # Теперь правильно!
    
    # Сохранение результатов
    with open(INDEX_PATH, "wb") as f:
        faiss.write_index(index, f)
    
    with open(METADATA_PATH, "w", encoding="utf-8") as f:
        json.dump(documents, f, ensure_ascii=False, indent=2)
    
    print(f"✅ Индекс создан: {len(documents)} фрагментов, {index.ntotal} векторов")
    print(f"Путь к индексу: {INDEX_PATH}")
    print(f"Путь к метаданным: {METADATA_PATH}")


if __name__ == "__main__":
    # Создаем директории, если отсутствуют
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    # Запуск обработки
    process_pdfs()