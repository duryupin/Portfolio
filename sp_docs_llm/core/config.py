import os
from pathlib import Path
from dotenv import load_dotenv

# Загружаем переменные окружения из .env
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# Пути к данным
DATA_DIR = BASE_DIR /  "data"
INDEX_PATH = str(DATA_DIR / "sp_index.faiss")
METADATA_PATH = str(DATA_DIR / "metadata.json")
PDF_DIR = BASE_DIR /  "pdf_files"

# Yandex Cloud API
<<<<<<< HEAD
YC_API_KEY = os.getenv("YC_API_KEY", "default_key")
YC_FOLDER_ID = os.getenv("YC_FOLDER_ID", "b1gqog5m3dhlf83oafu9")
YC_MODEL_URI = f"gpt://{YC_FOLDER_ID}/yandexgpt-lite/latest"

# Настройки модели
EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"
CHUNK_SIZE = 200  # Размер чанков для слов в предложениях
CHUNK_OVERLAP = 3  # Перекрытие между чанками в предложениях
=======
YC_API_KEY = "test"
YC_FOLDER_ID = "test"
YC_MODEL_URI = f"gpt://{YC_FOLDER_ID}/yandexgpt-lite/latest"

# Настройки модели
EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
CHUNK_SIZE = 1000  # Размер чанков для текста
CHUNK_OVERLAP = 100
>>>>>>> 8bfc4197abe1a9c3adaca775be227635dd980dbc
