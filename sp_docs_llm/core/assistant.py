"""
Лёгкий ассистент для RAG: загрузка индекса FAISS + семантический поиск.
Оптимизирован под малые вычислительные ресурсы: грузит модель на CPU,
нормализует эмбеддинги и использует IndexFlatIP (cosine) поиск.
"""

import sys
import os
import json
import faiss
import numpy as np
import requests
from sentence_transformers import SentenceTransformer
import logging
from pathlib import Path
from typing import List, Dict

logging.basicConfig(
    level=logging.INFO,
    filename="assistant.log",
    format="%(asctime)s - %(levelname)s - %(message)s",
    encoding="utf-8"
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / 'data'
INDEX_PATH = DATA_DIR / 'faiss.index'
METADATA_PATH = DATA_DIR / 'metadata.json'
EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
DEVICE = "cpu"
YC_API_KEY = os.environ.get('YC_API_KEY', '')
YC_MODEL_URI = os.environ.get('YC_MODEL_URI', '')


class BuildingCodeAssistantLight:
    def __init__(self):
        if not INDEX_PATH.exists() or not METADATA_PATH.exists():
            raise FileNotFoundError('Индекс или метаданные не найдены. Запустите process_pdfs_light.py сначала')

        self.index = faiss.read_index(str(INDEX_PATH))
        with open(METADATA_PATH, 'r', encoding='utf-8') as f:
            self.metadata = json.load(f)
        logger.info(f"Loaded index ({self.index.ntotal}) and metadata ({len(self.metadata)})")

        # модель для кодирования запросов
        self.model = SentenceTransformer(EMBEDDING_MODEL, device=DEVICE)
        logger.info(f"Loaded embedding model {EMBEDDING_MODEL} on {DEVICE}")

        if self.index.ntotal != len(self.metadata):
            logger.warning("FAISS index size and metadata length mismatch")

    def search(self, query: str, top_k: int = 5, include_neighbors: bool = True) -> List[Dict]:
        q_emb = self.model.encode([query], show_progress_bar=False, normalize_embeddings=True).astype('float32')
        D, I = self.index.search(q_emb, top_k)
        scores = D[0]
        indices = I[0]
        logger.info(f"Search scores: {scores}; indices: {indices}")

        results = []
        seen = set()
        THRESH = 0.6  # порог cosine similarity; подбери эмпирически
        for score, idx in zip(scores, indices):
            if idx < 0:
                continue
            if score < THRESH:
                continue
            if idx in seen:
                continue
            seen.add(idx)
            doc = self.metadata[idx]
            results.append(doc)
            # добавить соседние чанки (контекст)
            if include_neighbors:
                for n in (idx - 1, idx + 1):
                    if 0 <= n < len(self.metadata) and n not in seen:
                        seen.add(n)
                        results.append(self.metadata[n])
        return results

    def generate_answer(self, query: str) -> str:
        docs = self.search(query)
        if not docs:
            return "❌ Требование не регламентировано в предоставленных документах."

        # Формируем компактный контекст
        parts = []
        for d in docs:
            txt = d.get('text', '')
            if len(txt) > 800:
                txt = txt[:800] + '...'
            if d.get('is_table'):
                table = d.get('table_data', [])
                table_lines = [' | '.join(str(c) for c in r) for r in table]
                parts.append(f"{d['source']} {d.get('clause','?')} (table):\n" + '\n'.join(table_lines))
            else:
                parts.append(f"{d['source']} {d.get('clause','?')}:\n{txt}")
        context = '\n\n'.join(parts)
        prompt = f"""
Ты — эксперт по строительным нормам РФ с 20-летним стажем. Отвечай строго на основе предоставленного контекста (ниже). 
Если в контексте нет информации, честно скажи, что её нет — не придумывай и не делай выводов на основе общих знаний.

--- Инструкции по поиску и приоритетам ---
1) Ищи в контексте упоминания норм, требований, предельных значений, допусков, формул или расчётов.
2) Приоритет при ответе:
   a. Цифровые требования (точные числа, диапазоны, единицы) — если есть, приводи первыми.
   b. Формулы/методы расчёта — если есть, приводи с объяснением входных величин.
   c. Общие принципы / качественные требования — только если численных данных нет.
3) Нельзя придумывать: если информации недостаточно — ответ: "❌ Требование не регламентировано в предоставленных документах."
4) Для таблиц: извлекай и используй числовые данные из таблицы — указывай строку/столбец или заголовки, откуда взята цифра.

--- Формат поиска по пунктам ---
- При возможности указать **номер пункта** (например, `1.2.3` или `Приложение А`) — обязательно укажи.
- Если контекст содержит несколько документов, приводи **источник(и)** с именем файла/документа.

--- Ограничения на ответ ---
- Не добавляй ничего, чего нет в контексте.
- Не включай общие рассуждения без источника; если нужны допущения, явно пометь их как "допущение".
- Если ответ опирается на таблицу — вставь точную цитату ячеек в блоке кода.

--- Требуемый формат ответа (обязательно, Markdown) ---
1) **Прямой ответ** (один абзац, сразу по существу)
2) **Номер пункта**: <номер или '—' если не указан>
3) **Название документа / источник**: <имя файла или список файлов>
4) **Точная цитата** (если есть) — в блоке кода или в кавычках. Если это таблица — приводи строки/столбцы.
5) **Краткая ссылка на логику** (1–2 предложения, только если требуется пояснение; при этом опирайся на контекст)
6) **Confidence**: укажи степень уверенности в ответе (высокая/средняя/низкая) и причину (например, "точное число в тексте" vs "вывод из таблицы" vs "контекст частичный")

--- Входные данные ---
Контекст:
{context}

Вопрос:
{query}

--- Если нет релевантного фрагмента в контексте ---
Выдай ровно: "❌ Требование не регламентировано в предоставленных документах."

--- Технические параметры для LLM ---
- Температура: 0.0–0.2 (чем ближе к 0 — тем меньше творчества)
- Максимальная длина вывода: аккуратно ограничь (например, 600–1200 токенов), чтобы не перебирать весь документ
"""


        try:
            # Простой вызов YandexGPT
            resp = requests.post(
                "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
                headers={"Authorization": f"Api-Key {YC_API_KEY}"},
                json={
                    "modelUri": YC_MODEL_URI,
                    "messages": [{"role": "user", "text": prompt}],
                    "completionOptions": {"temperature": 0.0}
                },
                timeout=15
            )
            resp.raise_for_status()
            data = resp.json()
            ans = data.get('result', {}).get('alternatives', [])[0].get('message', {}).get('text', '')
            return ans or "❌ Пустой ответ от LLM"
        except Exception as e:
            logger.error(f"LLM request failed: {e}")
            return f"❌ Ошибка при запросе к LLM: {e}"


if __name__ == '__main__':
    a = BuildingCodeAssistantLight()
    while True:
        q = input('Запрос (exit для выхода): ')
        if q.lower() in ('exit', 'выход'):
            break
        print(a.generate_answer(q))