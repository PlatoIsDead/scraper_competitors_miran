# Web Scraper

Два инструмента в одном репозитории:

| Приложение | Файл | Назначение |
|---|---|---|
| Универсальный скрейпер | `app.py` | Скрейпит любой сайт, выгружает контент в CSV/TXT |
| Сравнение выделенных серверов | `dedicated_app.py` | Парсит 4 хостинга, сравнивает цены |

---

## Установка

```bash
# Активировать venv проекта
cd ~/code/PlatoIsDead/web_scraper
python -m venv .venv && source .venv/bin/activate

pip install -r requirements.txt

# Playwright нужен только для dedicated_app (JS-сайты)
playwright install chromium
```

---

## Запуск

### Универсальный скрейпер
```bash
streamlit run app.py
```

1. В сайдбаре вводишь URL сайта и разделы (например `/blog/, /cases/`).
2. Нажимаешь **Скрейпить** — прогресс-бар показывает обход страниц.
3. Результаты сохраняются в `data/<domain>_<timestamp>.csv`.
4. Вкладки: **Обзор** (метрики + графики), **Страницы** (поиск по тексту), **Кейсы**.
5. Кнопки **Скачать TXT / CSV** выгружают текущий результат.

### Сравнение выделенных серверов
```bash
streamlit run dedicated_app.py
```

1. Нажимаешь **Обновить данные** — скрейпит 4 провайдера (miran, selectel, 1dedic, regcloud).
2. Новые данные дописываются в `data/history.csv` (дедупликация по дате+провайдер+конфиг).
3. Фильтры в сайдбаре: ОЗУ, диск, тип диска, поколение CPU, провайдер.
4. Таблица сравнения: конфиги — строки, провайдеры — колонки; зелёный = дешевле всех, красный = дороже.
5. Вкладка **Динамика** — линейный график количества серверов в наличии по времени.

---

## Как это работает

### Универсальный скрейпер (`scraper.py` → `app.py`)

```
scraper.py → обходит страницы через requests + BeautifulSoup
           → извлекает заголовки, мета-описание, текстовые блоки
           → callback прогресса → app.py рендерит в real-time
```

- Сессия с куками инициализируется одним запросом к корню сайта.
- Пропускает служебные пути (тэги, RSS, wp-admin, CDN).
- Удаляет шум-теги (nav, header, footer, script, style) перед извлечением текста.
- Каждая страница → одна строка DataFrame, сохраняется в CSV.

### Скрейпер серверов (`dedicated_scraper.py` → `dedicated_app.py`)

```
dedicated_scraper.py:
  miran.ru     → requests + BS4 (статический HTML)
  selectel.ru  → Playwright перехватывает URL CDN JSON-payload,
                 затем requests скачивает JSON напрямую (без рендеринга)
  1dedic.ru    → Playwright (Vue 3, scroll loop до стабилизации карточек)
  reg.cloud    → Playwright (JS-рендеринг)
  
  → нормализация: диски (960/1024 GB → 1000), ОЗУ (63/65 → 64)
  → определение поколения CPU по regex-таблице
  → append в data/history.csv
```

- `scrape_all()` запускает все 4 провайдера, возвращает единый DataFrame.
- `save_history()` дописывает в CSV, пропуская дубликаты того же дня.
- Dashboard группирует конфиги и строит pivot-таблицу цен через `pandas.pivot_table`.

---

## Тесты

```bash
pytest tests/
```

Юнит-тесты покрывают функции нормализации в `dedicated_scraper.py`:
- `normalize_disk_gb` — снэппинг к стандартным размерам (480/1000/2000…)
- `normalize_ram_gb` — снэппинг к степеням двойки (32/64/128…)
- `extract_cpu_generation` — regex-таблица Intel E3/E5/Scalable + AMD EPYC
- `normalize_cpu_model` — нижний регистр, схлопывание пробелов, очистка пунктуации

---

## Структура данных

```
data/
  history.csv           # накопленная история (dedicated_app)
  <domain>_<ts>.csv     # результаты универсального скрейпера
  <domain>_<ts>.json    # метаданные запуска
```

Колонки `history.csv`:
`provider, cpu_model, cpu_generation, ram_gb, disk_count, disk_size_gb, disk_type, price_rub, quantity_available, scraped_at`
