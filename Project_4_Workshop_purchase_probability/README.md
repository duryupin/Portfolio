# Workshop_2 - Прогнозирование вероятности совершения покупки.
## Описание проекта

Интернет-магазин собирает историю покупателей, проводит рассылки предложений и
планирует будущие продажи. Для оптимизации процессов надо выделить пользователей, которые готовы совершить покупку в ближайшее время.

**Цель**:

Разработать модель предсказания вероятности совершения покупки.

## Стэк:

* python
* pandas
* numpy
* matplotlib
* seaborn
* sklearn
* scipy
* catboost
* shap

## Результат

Лучше всего показала себя **CatBoostClassifier** со следущими параметрами.

**ROC_AUC_SCORE** тестовые данные: 0.712

 | Параметры модели   | Значение       |
|--------------------|----------------|
| random_strength | 3            |
| learning_rate | 0.01              |
| l2_leaf_reg     | 5            |
| iterations     | 2000            |
| depth        | 2 |

