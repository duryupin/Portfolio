#!/usr/bin/env python3
"""
eval_retrieval.py — Оценка качества retrieval (Phase 0).

Запуск:
    python scripts/eval_retrieval.py --top_k 10

Скрипт загружает текущий индекс, прогоняет golden queries и считает:
- Recall@k (хотя бы один "релевантный" чанк в топ-k)
- Примерные MRR, количество "doc hits"
- Детальный отчёт по каждому запросу

Golden queries утверждены пользователем (21 шт, включая 5 по вентиляции и кондиционированию).
"""

import argparse
import sys
from pathlib import Path
from typing import List, Dict, Any

# Добавляем корень проекта в путь, чтобы импортировать core
sys.path.append(str(Path(__file__).resolve().parents[1]))

from core.assistant import BuildingCodeAssistantLight


# === УТВЕРЖДЁННЫЙ СПИСОК GOLDEN QUERIES (21) ===
# Для проверки используем "должено содержать хотя бы N из positive_keywords в тексте одного из результатов"
# + опционально подсказки по документу (в source или тексте).

GOLDEN_QUERIES: List[Dict[str, Any]] = [
    {
        "id": 1,
        "q": "Какая минимальная высота ограждений лестниц и балконов в многоквартирных жилых зданиях?",
        "positive_keywords": ["0,9", "900", "м", "огражд", "лестниц", "не менее"],
        "min_matches": 3,
        "doc_hint": "СП 54",
        "note": "Высота ограждений ~0.9м"
    },
    {
        "id": 2,
        "q": "Требования к ширине эвакуационных выходов и коридоров в общественных и жилых зданиях.",
        "positive_keywords": ["эвакуац", "ширина", "коридор", "выход", "не менее"],
        "min_matches": 3,
        "doc_hint": "1.13130",
        "note": "Эвакуационные пути"
    },
    {
        "id": 3,
        "q": "Максимально допустимый уклон пандуса для маломобильных групп населения (МГН)?",
        "positive_keywords": ["пандус", "уклон", "1:12", "1:20", "маломобильн", "инвалид"],
        "min_matches": 2,
        "doc_hint": "",
        "note": "Уклон пандуса"
    },
    {
        "id": 4,
        "q": "Какая высота помещений для бассейнов длиной 10 м и более (от обходной дорожки до низа конструкций)?",
        "positive_keywords": ["бассейн", "высота", "10 м", "обходн", "помещен"],
        "min_matches": 3,
        "doc_hint": "СП 310",
        "note": "Высота помещений бассейнов"
    },
    {
        "id": 5,
        "q": "Как выбирать длину свай в зависимости от грунтовых условий и оборудования?",
        "positive_keywords": ["свай", "длина", "грунт", "ростверк", "оборудован"],
        "min_matches": 3,
        "doc_hint": "СП 24",
        "note": "Свайные фундаменты"
    },
    {
        "id": 6,
        "q": "Какая температура теплоносителя в системах отопления жилых зданий (от ЦТП)?",
        "positive_keywords": ["теплоносител", "95", "°С", "полимер", "отоплен"],
        "min_matches": 2,
        "doc_hint": "СП 60",
        "note": "Температура теплоносителя"
    },
    {
        "id": 7,
        "q": "Требования к температуре, давлению и расходу горячей воды в местах водоразбора.",
        "positive_keywords": ["горяч", "водоснабж", "температур", "давлен", "СанПиН"],
        "min_matches": 3,
        "doc_hint": "СП 30",
        "note": "Горячее водоснабжение"
    },
    {
        "id": 8,
        "q": "Параметры микроклимата в жилых и общественных помещениях (температура, влажность).",
        "positive_keywords": ["ГОСТ 30494", "микроклимат", "температур", "влажн"],
        "min_matches": 2,
        "doc_hint": "30494",
        "note": "Микроклимат ГОСТ 30494"
    },
    {
        "id": 9,
        "q": "Нормы отбраковки кольцевых сварных соединений газопроводов (по таблицам дефектов).",
        "positive_keywords": ["сварн", "газопровод", "отбраковк", "Таблица Д.6", "дефект"],
        "min_matches": 3,
        "doc_hint": "СП 86",
        "note": "Сварные соединения газопроводов (таблица)"
    },
    {
        "id": 10,
        "q": "Противопожарные расстояния между жилыми и общественными зданиями.",
        "positive_keywords": ["противопожарн", "расстояни", "м", "здани"],
        "min_matches": 2,
        "doc_hint": "СП 4.13130",
        "note": "Противопожарные разрывы"
    },
    {
        "id": 11,
        "q": "Ширина проезжей части и обочин лесных дорог разных категорий (из таблиц).",
        "positive_keywords": ["лесн", "дорог", "ширина", "категори", "Таблица"],
        "min_matches": 3,
        "doc_hint": "СП 318",
        "note": "Лесные дороги (таблица)"
    },
    {
        "id": 12,
        "q": "Требования к уклону для слива в помещениях с бассейнами.",
        "positive_keywords": ["бассейн", "уклон", "слив", "помещен"],
        "min_matches": 2,
        "doc_hint": "СП 310",
        "note": "Уклон слива в бассейнах"
    },
    {
        "id": 13,
        "q": "Требования к уровням шума и звукоизоляции в зданиях (эквивалентный уровень).",
        "positive_keywords": ["шум", "дБА", "эквивалентн", "Таблица 6.10"],
        "min_matches": 2,
        "doc_hint": "СП 338",
        "note": "Уровни шума (таблица)"
    },
    {
        "id": 14,
        "q": "Основные задачи службы эксплуатации внутренних систем отопления и водоснабжения.",
        "positive_keywords": ["эксплуатац", "СП 347", "служб", "техническ", "отоплен"],
        "min_matches": 3,
        "doc_hint": "СП 347",
        "note": "Эксплуатация (СП 347)"
    },
    {
        "id": 15,
        "q": "Минимальные расстояния от гаража или навеса до границы соседнего участка в СНТ / садоводстве.",
        "positive_keywords": ["гараж", "навес", "расстояни", "границ", "участк", "6.7"],
        "min_matches": 3,
        "doc_hint": "СП 53",
        "note": "Расстояния в СНТ (п.6.7)"
    },
    {
        "id": 16,
        "q": "Оценка качества ненарушенных образцов грунтов по коэффициенту переуплотнения (OCR).",
        "positive_keywords": ["переуплотн", "OCR", "Таблица В.1", "грунт"],
        "min_matches": 2,
        "doc_hint": "СП 23",
        "note": "Грунты, переуплотнение (таблица)"
    },
    # === Вентиляция и кондиционирование (добавлено по запросу пользователя) ===
    {
        "id": 17,
        "q": "Какая кратность воздухообмена требуется в жилых комнатах, на кухнях и в санузлах?",
        "positive_keywords": ["кратность", "воздухообмен", "кухн", "сануз", "м3/ч", "СП 60"],
        "min_matches": 3,
        "doc_hint": "СП 60.13330",
        "note": "Кратность воздухообмена (СП 60)"
    },
    {
        "id": 18,
        "q": "Требования к параметрам приточного воздуха и системам вентиляции/кондиционирования в общественных зданиях.",
        "positive_keywords": ["приточн", "воздух", "кондицион", "вентиляц", "температур", "СП 60"],
        "min_matches": 3,
        "doc_hint": "СП 60",
        "note": "Приточный воздух и ОВК (СП 60)"
    },
    {
        "id": 19,
        "q": "Противопожарные требования к воздуховодам, огнезадерживающим клапанам и вентиляционным системам.",
        "positive_keywords": ["воздуховод", "клапан", "противопожарн", "огнезадерж", "вентиляц"],
        "min_matches": 3,
        "doc_hint": "7.13130",
        "note": "Противопожарные требования к вентиляции"
    },
    {
        "id": 20,
        "q": "Нормы расхода наружного (свежего) воздуха на одного человека в помещениях разного назначения.",
        "positive_keywords": ["наружн", "воздух", "м3/ч", "чел", "на человека", "вентиляц"],
        "min_matches": 3,
        "doc_hint": "СП 60",
        "note": "Расход наружного воздуха на человека"
    },
    {
        "id": 21,
        "q": "Требования к рекуперации тепла и энергоэффективности систем вентиляции и кондиционирования.",
        "positive_keywords": ["рекуперац", "тепл", "энергоэффект", "вентиляц", "кондицион"],
        "min_matches": 2,
        "doc_hint": "СП 60",
        "note": "Рекуперация тепла в ОВК"
    },
]


def check_hit(results: List[Dict], golden: Dict) -> bool:
    """Проверяет, попал ли релевантный чанк в результаты поиска."""
    min_matches = golden.get("min_matches", 2)
    keywords = [k.lower() for k in golden.get("positive_keywords", [])]
    doc_hint = golden.get("doc_hint", "").lower()

    for d in results:
        txt = (d.get("text") or "").lower()
        src = (d.get("source") or "").lower()

        matched = sum(1 for kw in keywords if kw in txt)
        if matched >= min_matches:
            return True

        if doc_hint and (doc_hint in src or doc_hint in txt):
            return True

    return False


def run_eval(top_k: int = 10, verbose: bool = True) -> Dict:
    print(f"\n=== Retrieval Eval (top_k={top_k}) ===")
    try:
        assistant = BuildingCodeAssistantLight()
    except Exception as e:
        print(f"Не удалось загрузить ассистента: {e}")
        print("Убедись, что индекс построен (python scripts/process_pdf_index.py)")
        return {}

    total = len(GOLDEN_QUERIES)
    hits = 0
    mrr_sum = 0.0
    doc_hits = 0

    for g in GOLDEN_QUERIES:
        q = g["q"]
        results = assistant.search(q, top_k=top_k)

        hit = check_hit(results, g)
        if hit:
            hits += 1

        # Простая оценка MRR: позиция первого хита (1-based)
        first_hit_rank = None
        for rank, d in enumerate(results, 1):
            if check_hit([d], g):
                first_hit_rank = rank
                break
        if first_hit_rank:
            mrr_sum += 1.0 / first_hit_rank

        # Doc hint hit
        doc_hit = False
        hint = g.get("doc_hint", "").lower()
        if hint:
            for d in results:
                if hint in (d.get("source") or "").lower() or hint in (d.get("text") or "").lower():
                    doc_hit = True
                    break
        if doc_hit:
            doc_hits += 1

        if verbose:
            status = "HIT" if hit else "MISS"
            print(f"[{g['id']:2d}] {status} | {q[:70]}...")
            if results:
                top_src = results[0].get("source", "?")[:45]
                top_clause = results[0].get("clause")
                print(f"     top1: {top_src} (clause={top_clause})")

    recall = hits / total if total else 0
    mrr = mrr_sum / total if total else 0
    doc_hit_rate = doc_hits / total if total else 0

    print("\n=== ИТОГИ ===")
    print(f"Queries:     {total}")
    print(f"Recall@{top_k}: {recall:.2%}  ({hits}/{total})")
    print(f"MRR@{top_k}:     {mrr:.3f}")
    print(f"Doc-hit rate: {doc_hit_rate:.2%}  ({doc_hits}/{total})")

    return {
        "top_k": top_k,
        "total_queries": total,
        "recall": recall,
        "mrr": mrr,
        "doc_hit_rate": doc_hit_rate,
        "hits": hits,
    }


def main():
    parser = argparse.ArgumentParser(description="Eval retrieval quality for SP docs RAG")
    parser.add_argument("--top_k", type=int, default=10, help="Сколько документов возвращать (top_k)")
    parser.add_argument("--quiet", action="store_true", help="Меньше вывода")
    args = parser.parse_args()

    run_eval(top_k=args.top_k, verbose=not args.quiet)


if __name__ == "__main__":
    main()
