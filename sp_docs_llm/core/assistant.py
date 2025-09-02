import sys
from pathlib import Path
import json
import faiss
import numpy as np
import requests
from sentence_transformers import SentenceTransformer
from typing import List, Dict
import os
import logging

logging.basicConfig(
    level=logging.INFO,
    filename="process.log",
    format="%(asctime)s - %(levelname)s - %(message)s",
    encoding="utf-8"
)
logger = logging.getLogger(__name__)

sys.path.append(str(Path(__file__).resolve().parent.parent))

from core.config import *

class BuildingCodeAssistant:
    def __init__(self):
        if not os.path.exists(INDEX_PATH):
            logger.error(f"Файл индекса {INDEX_PATH} не существует. Запустите process_pdf_index.py.")
            raise FileNotFoundError(f"Файл индекса {INDEX_PATH} не найден")
        self.index = faiss.read_index(INDEX_PATH)
        if self.index.ntotal == 0:
            logger.error("Индекс FAISS пустой. Запустите process_pdf_index.py.")
            raise ValueError("Индекс FAISS пустой")
        logger.info(f"Индекс загружен: {self.index.ntotal} векторов")

        if not os.path.exists(METADATA_PATH):
            logger.error(f"Файл метаданных {METADATA_PATH} не существует. Запустите process_pdf_index.py.")
            raise FileNotFoundError(f"Файл метаданных {METADATA_PATH} не найден")
        with open(METADATA_PATH, "r", encoding="utf-8") as f:
            self.metadata = json.load(f)
        if not self.metadata:
            logger.error("Метаданные пусты. Запустите process_pdf_index.py.")
            raise ValueError("Метаданные пусты")
        logger.info(f"Метаданные загружены: {len(self.metadata)} записей")

        self.model = SentenceTransformer(EMBEDDING_MODEL)
        logger.info(f"Модель {EMBEDDING_MODEL} загружена")

    def search(self, query: str, top_k: int = 5, include_neighbors: bool = True) -> List[Dict]:
        """Семантический поиск по индексу + добавление соседних чанков"""
        query_embed = self.model.encode([query], 
                                        show_progress_bar=False, 
                                        normalize_embeddings=True)
        distances, indices = self.index.search(np.array(query_embed), top_k)
        results = []
        seen = set()
        MAX_DISTANCE = 1.0

        for i, distance in zip(indices[0], distances[0]):
            if i < 0:
                continue
            #and distance < MAX_DISTANCE
            if i < len(self.metadata) :
                if i not in seen:
                    results.append(self.metadata[i])
                    seen.add(i)
                if include_neighbors:
                    if i - 1 >= 0 and (i - 1) not in seen:
                        results.append(self.metadata[i - 1])
                        seen.add(i - 1)
                    if i + 1 < len(self.metadata) and (i + 1) not in seen:
                        results.append(self.metadata[i + 1])
                        seen.add(i + 1)
        if not results:
            logger.warning(f"Поиск для запроса '{query}' не вернул релевантных результатов. Дистанции: {list(distances[0])}")
        else:
            logger.info(f"Поиск для запроса '{query}' вернул {len(results)} чанков: {[r['doc_id'] for r in results]}")

        logger.info(f"Distances: {list(distances[0])}")
        logger.info(f"Indices: {list(indices[0])}")
        
        return results

    def generate_answer(self, query: str) -> str:
        """Генерация ответа через YandexGPT"""
        context_docs = self.search(query, top_k=5, include_neighbors=True)
        if not context_docs:
            logger.warning(f"Поиск для запроса '{query}' не вернул документов")
            return "❌ Требование не регламентировано в предоставленных документах."

        context_parts = []
        for doc in context_docs:
            if doc.get("is_table", False):
                # Форматируем таблицу в читаемый Markdown
                table_lines = ["| " + " | ".join(str(cell) if cell is not None else "" for cell in row) + " |" 
                              for row in doc.get("table_data", [])]
                table_text = "\n".join(table_lines)
                context_parts.append(f"{doc['source']}, п.{doc.get('clause', '?')} (таблица):\n```\n{table_text}\n```")
            else:
                context_parts.append(f"{doc['source']}, п.{doc.get('clause', '?')}:\n{doc['text']}")
        context = "\n\n".join(context_parts)
        MAX_CONTEXT_LENGTH = 4000
        context = context[:MAX_CONTEXT_LENGTH]
        logger.info(f"Контекст для запроса '{query}': {context[:500]}...")

        prompt = f"""
        Ты — эксперт по строительным нормам РФ с 20-летним стажем.
        Отвечай строго на основе предоставленного контекста. 
        Не придумывай ничего, если информации нет. 
        Если ответа нет в контексте, ответь: "❌ Требование не регламентировано в предоставленных документах."
        Контекст:
        {context}
        Вопрос: {query}
        Требуемый формат ответа (Markdown):
        1. **Прямой ответ** на вопрос
        2. **Номер пункта** (если указан в документе)
        3. **Название документа**
        4. **Точная цитата** из норматива (в кавычках или блоке)
        Если контекст содержит таблицу, используй данные из таблицы в ответе.
        """
        try:
            response = requests.post(
                "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
                headers={"Authorization": f"Api-Key {YC_API_KEY}"},
                json={
                    "modelUri": YC_MODEL_URI,
                    "messages": [{"role": "user", "text": prompt}],
                    "completionOptions": {"temperature": 0.1}
                },
                timeout=10)
            response.raise_for_status()
            result = response.json()
            if 'result' not in result or not result['result'].get('alternatives'):
                logger.error("Пустой ответ от YandexGPT")
                return "❌ Пустой ответ от YandexGPT"
            answer = result['result']['alternatives'][0]['message']['text']
            logger.info(f"Ответ от YandexGPT для запроса '{query}': {answer[:500]}...")
            return answer
        except requests.RequestException as e:
            logger.error(f"Ошибка при запросе к YandexGPT: {e}")
            return f"❌ Ошибка при запросе к YandexGPT: {e}"

if __name__ == "__main__":
    assistant = BuildingCodeAssistant()
    while True:
        query = input("Введите ваш запрос (или 'выход' для завершения): ")
        if query.lower() == "выход":
            break
        answer = assistant.generate_answer(query)
        print("\nОтвет:\n", answer, "\n")