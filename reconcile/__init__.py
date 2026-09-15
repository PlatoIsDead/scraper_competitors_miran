# Сверка пар отчёта (matches_<дата>.csv) с отрендеренной витриной конкурента:
# карточка видна, цена как показана, CPU, RAM, диски. Запуск:
#   python -m reconcile --fresh            # прогон пайплайна + сверка
#   python -m reconcile --report data/reports/matches_20260915.csv
# Чистая логика — reconcile/core.py, сеть — reconcile/sources.py.
