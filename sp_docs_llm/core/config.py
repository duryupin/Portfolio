import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Пути к данным
DATA_DIR = BASE_DIR /  "data"
INDEX_PATH = DATA_DIR /  "sp_index.faiss"
METADATA_PATH = DATA_DIR /  "metadata.json"
PDF_DIR = DATA_DIR /  "sp_data"

# Yandex Cloud API
YC_API_KEY = "test"
YC_FOLDER_ID = "test"
YC_MODEL_URI = f"gpt://{YC_FOLDER_ID}/yandexgpt-lite/latest"

# Настройки модели
EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
CHUNK_SIZE = 1000  # Размер чанков для текста
CHUNK_OVERLAP = 100
