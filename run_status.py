# Статус прогона по конкурентам и время «по Москве» — чистые функции без
# Streamlit и без файлового ввода-вывода, чтобы их можно было тестировать.
#
# Зачем: приложение живёт на Streamlit Cloud (контейнер в UTC, один инстанс
# на всех). Время по mtime файлов после перезапуска облака равно времени
# клонирования репозитория, и старый отчёт выглядит свежим. Поэтому источник
# правды о времени прогона — только run_status_<дата>.json, записанный
# пайплайном, а даты в именах файлов считаются по Москве.

from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")
TZ_LABEL = "МСК"

# Текущий формат run_status_<дата>.json; отчёты без поля version — старый
# формат (нет времени и текста ошибки по конкурентам).
RUN_STATUS_VERSION = 2

EMPTY_STATE_MESSAGE = "Отчётов ещё нет — нажмите «Запустить сравнение»"
RUN_BUTTON_HINT = "нажмите «Запустить сравнение»"


# ── Время по Москве ──────────────────────────────────────────────────

def now_msk(now: "datetime | None" = None) -> datetime:
    """Текущее время по Москве (tz-aware). Naive `now` трактуется как UTC —
    так считает контейнер Streamlit Cloud."""
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(MSK)


def today_msk(now: "datetime | None" = None) -> date:
    return now_msk(now).date()


def date_tag_msk(now: "datetime | None" = None) -> str:
    """Дата для имён файлов (YYYYMMDD) — по Москве, а не по часам контейнера:
    прогон в 00:00–03:00 МСК иначе записывался бы вчерашней датой."""
    return today_msk(now).strftime("%Y%m%d")


def date_tag_to_date(tag: str) -> "date | None":
    try:
        return datetime.strptime(str(tag), "%Y%m%d").date()
    except (TypeError, ValueError):
        return None


def fmt_date(d: "date | None", with_year: bool = True) -> str:
    if d is None:
        return "—"
    return d.strftime("%d.%m.%Y" if with_year else "%d.%m")


def fmt_date_tag(tag: str, with_year: bool = True) -> str:
    """'20260914' → '14.09.2026'; нераспознанный тег возвращается как есть."""
    d = date_tag_to_date(tag)
    return fmt_date(d, with_year) if d else (tag or "—")


def parse_ts(value) -> "datetime | None":
    """ISO-строка со смещением → datetime по Москве. Naive-строки старого
    формата (часовой пояс контейнера неизвестен) → None: лучше «время не
    записано», чем выдуманное."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(MSK)


def fmt_ts(dt: "datetime | None", with_year: bool = False) -> str:
    """datetime → '14.09 15:21 МСК' (или с годом)."""
    if dt is None:
        return "время не записано"
    dt = dt.astimezone(MSK)
    pattern = "%d.%m.%Y %H:%M" if with_year else "%d.%m %H:%M"
    return f"{dt:{pattern}} {TZ_LABEL}"


# ── run_status: нормализация и подписи ───────────────────────────────

def normalize_source(raw: dict) -> dict:
    """Запись конкурента из run_status → словарь с полным набором ключей.
    Старый формат (без времени, ошибки и provider) читается без падения."""
    status = str(raw.get("status") or "error")
    error = raw.get("error")
    try:
        offers = int(raw.get("offers") or 0)
    except (TypeError, ValueError):
        offers = 0
    return {
        "competitor_id": str(raw.get("competitor_id") or ""),
        "name": str(raw.get("name") or ""),
        "url": str(raw.get("url") or ""),
        "provider": raw.get("provider"),
        "status": status,
        "error": str(error) if error else None,
        "offers": offers,
        "started_at": parse_ts(raw.get("started_at")),
        "finished_at": parse_ts(raw.get("finished_at")),
        "check": raw.get("check"),
    }


def parse_run_status(payload) -> list[dict]:
    """Содержимое run_status_<дата>.json → список нормализованных источников.
    Битый или чужой JSON → пустой список."""
    if not isinstance(payload, dict):
        return []
    sources = payload.get("sources")
    if not isinstance(sources, list):
        return []
    return [normalize_source(s) for s in sources if isinstance(s, dict)]


def run_finished_at(sources: list[dict]) -> "datetime | None":
    """Момент окончания прогона = самое позднее finished_at по конкурентам."""
    stamps = [s["finished_at"] for s in sources if s.get("finished_at")]
    return max(stamps) if stamps else None


def source_line(s: dict, label: "str | None" = None) -> str:
    """Строка шапки по конкуренту:
    'Selectel — 14.09 15:21 МСК, 128 предложений'
    'Reg.cloud — ошибка: TimeoutError: ...'
    'Timeweb — 14.09 15:22 МСК, данных нет'."""
    label = label or s.get("name") or s.get("competitor_id") or "?"
    when = s.get("finished_at") or s.get("started_at")
    if s.get("status") == "error":
        detail = s.get("error") or "данные не получены"
        tail = f"ошибка: {detail}"
        if when:
            tail = f"{fmt_ts(when)}, {tail}"
        return f"{label} — {tail}"
    if s.get("status") == "ok" and s.get("offers"):
        count = f"{s['offers']} предложений"
    else:
        count = "данных нет"
    if when:
        return f"{label} — {fmt_ts(when)}, {count}"
    return f"{label} — {count} (время прогона не записано)"


# ── Свежесть показанного отчёта ──────────────────────────────────────

def report_freshness(date_tag: str, sources: list[dict],
                     today: "date | None" = None) -> dict:
    """Что сказать пользователю о показанном отчёте.

    Возвращает {"state": none|stale|no_status|fresh, "message": str|None}.
    stale — отчёт не за сегодня; no_status — отчёт за сегодня, но без
    run_status (старый формат или файл потерян) — время прогона неизвестно,
    поэтому свежим его тоже не считаем.
    """
    today = today or today_msk()
    report_date = date_tag_to_date(date_tag) if date_tag else None
    if report_date is None:
        return {"state": "none", "message": EMPTY_STATE_MESSAGE}
    shown = f"Показан отчёт от {fmt_date(report_date)}"
    if report_date < today:
        return {
            "state": "stale",
            "message": (f"{shown} — это не свежий прогон, {RUN_BUTTON_HINT}."),
        }
    if not sources:
        return {
            "state": "no_status",
            "message": (f"{shown}, но время прогона по нему не записано — "
                        f"считать его свежим нельзя, {RUN_BUTTON_HINT}."),
        }
    return {"state": "fresh", "message": None}


def raw_staleness(provider_tag: "str | None", report_tag: str,
                  scraped_at: "datetime | None" = None) -> dict:
    """Пометка для сырого скрейпа одного конкурента относительно показанного
    отчёта: {"stale": bool, "label": 'скрейп 31.08.2026 (старее отчёта)'}."""
    if not provider_tag:
        return {"stale": True, "label": "данных нет"}
    when = (fmt_ts(scraped_at, with_year=True) if scraped_at
            else fmt_date_tag(provider_tag))
    if report_tag and provider_tag < report_tag:
        return {"stale": True,
                "label": f"скрейп {when} — старее отчёта "
                         f"от {fmt_date_tag(report_tag)}"}
    return {"stale": False, "label": f"скрейп {when}"}


def read_log_tail(path: "Path | str | None", lines: int = 40) -> str:
    """Последние строки лога прогона для показа в UI; нет файла → ''."""
    if not path:
        return ""
    p = Path(path)
    if not p.exists():
        return ""
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])
