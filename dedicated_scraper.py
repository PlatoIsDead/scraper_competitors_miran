# Dedicated Server Price Comparison Scraper
# Скрейпит 8 хостингов: miran.ru, selectel.ru, 1dedic.ru, reg.cloud,
# hostkey.ru, it-lite.ru, netrack.ru, timeweb.com

import os
import re
import json
import time
import requests
import tls_client
from datetime import date
from typing import NotRequired, TypedDict
import pandas as pd
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}


def make_session(base_url: str | None = None) -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    if base_url:
        session.headers.update({"Referer": base_url})
    return session

# Optional SOCKS5/HTTP proxy for providers that block direct connections.
# Example: export SCRAPER_PROXY="socks5://127.0.0.1:1080"
_PROXY = os.environ.get("SCRAPER_PROXY") or None


# ── Constants ────────────────────────────────────────────────────────

DATA_DIR = "data"
HISTORY_CSV = os.path.join(DATA_DIR, "history.csv")

DISK_STANDARDS = [120, 240, 480, 1000, 2000, 4000, 8000]
RAM_STANDARDS = [8, 16, 24, 32, 48, 64, 96, 128, 192, 256, 384, 512, 768, 1024]

# CPU generation lookup (order matters — more specific rules first)
CPU_GEN_RULES = [
    # E3 Vx
    (r"E3-\d+\s*[Vv]6", "Kaby Lake"),
    (r"E3-\d+\s*[Vv]5", "Skylake"),
    (r"E3-\d+\s*[Vv]4", "Broadwell"),
    (r"E3-\d+\s*[Vv]3", "Haswell"),
    (r"E3-\d+\s*[Vv]2", "Ivy Bridge"),
    (r"E3-\d+", "Sandy Bridge"),  # no V suffix
    # E5 Vx
    (r"E5-\d+\s*[Vv]4", "Broadwell EP"),
    (r"E5-\d+\s*[Vv]3", "Haswell EP"),
    (r"E5-\d+\s*[Vv]2", "Ivy Bridge EP"),
    (r"E5-\d+", "Sandy Bridge EP"),
    # Scalable — R suffix = Cascade Lake; no R = Skylake-SP
    (r"(?:Gold|Silver|Platinum|Bronze)\s+\d+R\b", "Cascade Lake"),
    (r"(?:Gold|Silver|Platinum|Bronze)\s+\d+", "Skylake-SP"),
    # AMD EPYC
    (r"EPYC\s+9\d{3}", "Genoa"),
    (r"EPYC\s+73\d{2}", "Milan"),
    (r"EPYC\s+7[012]\d{2}", "Rome/Naples"),
]

DEDUP_KEY_COLS = [
    "scraped_at", "provider", "cpu_model_norm",
    "ram_gb", "disk_count", "disk_size_gb", "disk_type"
]


# ── Data model ───────────────────────────────────────────────────────

class ServerRow(TypedDict):
    provider: str
    cpu_model: str
    cpu_model_norm: str
    cpu_generation: str
    ram_gb: int
    disk_count: int
    disk_size_gb: int
    disk_type: str
    price_rub: float
    quantity_available: int | None
    scraped_at: str
    # Extended fields for the competitor-matching pipeline; legacy disk_*
    # fields keep holding the FIRST pool for history.csv/pivot compatibility.
    plan_id: NotRequired[str]
    cpu_sockets: NotRequired[int]
    cpu_cores_total: NotRequired[int]  # total across sockets, as sites publish
    disk_pools: NotRequired[list[dict]]  # [{"disk_type","disk_count","disk_size_gb"}]
    currency: NotRequired[str]
    price_period: NotRequired[str]


# history.csv schema is frozen to these columns; extended ServerRow fields
# (incl. list-valued disk_pools, which would break drop_duplicates) never leak in.
LEGACY_HISTORY_COLS = [
    "provider", "cpu_model", "cpu_model_norm", "cpu_generation",
    "ram_gb", "disk_count", "disk_size_gb", "disk_type",
    "price_rub", "quantity_available", "scraped_at",
]


# ── Normalization functions ──────────────────────────────────────────

def normalize_disk_gb(raw_gb: int) -> int:
    """Snap to a standard disk size only when the deviation is small (960 → 1000).

    Безусловный снап к сетке ломал matching: 1600 (1.6 ТБ) прилипал к 2000,
    6000 — к 4000, 15360 — к 8000. Эквивалентность «маркетинговых» размеров
    (960 ≈ 1 ТБ) теперь решает config/disk_classes.json, а не сетка.
    """
    nearest = min(DISK_STANDARDS, key=lambda s: abs(s - raw_gb))
    if abs(nearest - raw_gb) <= raw_gb * 0.05:
        return nearest
    return int(raw_gb)


def normalize_ram_gb(raw_gb: int) -> int:
    """Snap to a standard RAM size only when the deviation is small (63/65 → 64).

    Серверные объёмы не только степени двойки: 96/192/384/768 — легальные
    6-канальные конфиги, их нельзя «прилипать» к 64/128/256 (ломает matching).
    """
    nearest = min(RAM_STANDARDS, key=lambda s: abs(s - raw_gb))
    if abs(nearest - raw_gb) <= max(2, raw_gb * 0.05):
        return nearest
    return int(raw_gb)


def extract_cpu_generation(model: str) -> str:
    """Match CPU model against generation rules."""
    for pattern, generation in CPU_GEN_RULES:
        if re.search(pattern, model, re.IGNORECASE):
            return generation
    return ""


def normalize_cpu_model(model: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation."""
    return re.sub(r"\s+", " ", model.strip().lower()).rstrip(".,;")


def normalize_disk_type(raw_type: str) -> str:
    """Convert to exactly 'NVMe', 'SSD', or 'HDD'."""
    lower = raw_type.lower()
    if "nvme" in lower:
        return "NVMe"
    elif "ssd" in lower:
        return "SSD"
    else:
        return "HDD"


# ── miran.ru scraper (static HTML, requests + BS4) ───────────────────

def _parse_miran_html(html: bytes, today: str) -> list[ServerRow]:
    """Parse miran dedicated page HTML. Pure function — used by tests."""
    soup = BeautifulSoup(html, "lxml")
    rows = []

    cpu_divs = soup.find_all("div", class_="mb-services__title")
    for cpu_div in cpu_divs:
        cpu_text = cpu_div.get_text(strip=True)
        if not re.search(r"Intel|AMD|Xeon|EPYC|Core", cpu_text, re.I):
            continue

        cpu_model = cpu_text
        parent = cpu_div.parent
        if not parent:
            continue
        spec_text = parent.get_text(separator=" ", strip=True)

        disk_match = re.search(
            r"(\d+)\s*[хxX×]\s*([\d\s]+)\s*(ГБ|ТБ)\s*(NVMe|SSD|HDD|SATA)?",
            spec_text, re.IGNORECASE
        )
        disk_count, disk_size_gb, disk_type_raw = 1, 0, "HDD"
        if disk_match:
            disk_count = int(disk_match.group(1))
            raw_size = int(re.sub(r"\s+", "", disk_match.group(2)))
            unit = disk_match.group(3)
            if "ТБ" in unit:
                raw_size *= 1000
            disk_size_gb = normalize_disk_gb(raw_size)
            disk_type_raw = disk_match.group(4) or "HDD"

        disk_type = normalize_disk_type(disk_type_raw)

        spec_no_disk = re.sub(
            r"\d+\s*[хxX×]\s*[\d\s]+(ГБ|ТБ)\s*(?:NVMe|SSD|HDD|SATA)?",
            "", spec_text, flags=re.I
        )
        ram_match = re.search(r"(\d+)\s*ГБ", spec_no_disk, re.I)
        ram_gb = normalize_ram_gb(int(ram_match.group(1))) if ram_match else 0

        price_match = re.search(
            r"([\d\s\u00a0]+)\s*₽\s*/\s*(?:мес|месяц)",
            spec_text, re.I
        )
        if not price_match:
            continue
        price_rub = float(re.sub(r"[\s\u00a0]", "", price_match.group(1)))

        rows.append({
            "provider": "miran",
            "cpu_model": cpu_model,
            "cpu_model_norm": normalize_cpu_model(cpu_model),
            "cpu_generation": extract_cpu_generation(cpu_model),
            "ram_gb": ram_gb,
            "disk_count": disk_count,
            "disk_size_gb": disk_size_gb,
            "disk_type": disk_type,
            "price_rub": price_rub,
            "quantity_available": None,
            "scraped_at": today,
        })

    return rows


def scrape_miran() -> list[ServerRow]:
    """Scrape miran.ru/services/dedicated using requests + BeautifulSoup."""
    session = make_session("https://miran.ru")
    try:
        r = session.get("https://miran.ru/services/dedicated", timeout=30)
        r.raise_for_status()
    except Exception as e:
        print(f"[miran] HTTP error: {e}")
        return []
    return _parse_miran_html(r.content, date.today().isoformat())


# ── Playwright helper ────────────────────────────────────────────────

def _scrape_with_playwright(
    url: str,
    provider: str,
    wait_selector: str | None = None,
    _retry_after_install: bool = True,
) -> str:
    """Returns page HTML after JS renders. Returns empty string on failure."""
    try:
        with sync_playwright() as p:
            # Try system chromium first, fallback to playwright's if available
            executable_path = None
            if os.path.exists("/usr/bin/chromium-browser"):
                executable_path = "/usr/bin/chromium-browser"

            launch_args = {"headless": True}
            if executable_path:
                launch_args["executable_path"] = executable_path
                launch_args["args"] = ["--no-sandbox", "--disable-dev-shm-usage"]

            browser = p.chromium.launch(**launch_args)
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36",
                locale="ru-RU",
            )
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=25000)

            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=10000)
                except Exception:
                    # Selector not found, try to continue anyway
                    pass
            else:
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    # Timeout on networkidle, continue anyway
                    pass

            # Give JS some time to render
            page.wait_for_timeout(3000)
            html = page.content()
            browser.close()
            return html
    except Exception as e:
        # Streamlit Cloud: пакет playwright есть, а браузер не скачан —
        # ставим chromium на месте и повторяем один раз
        if _retry_after_install and "Executable doesn't exist" in str(e):
            print(f"[{provider}] Chromium не установлен — качаем (playwright install)...")
            import subprocess
            import sys
            res = subprocess.run(
                [sys.executable, "-m", "playwright", "install", "chromium"],
                capture_output=True, text=True, timeout=300,
            )
            if res.returncode == 0:
                return _scrape_with_playwright(
                    url, provider, wait_selector, _retry_after_install=False)
            print(f"[{provider}] playwright install не удался: {res.stderr[-300:]}")
        print(f"[{provider}] Playwright error: {e}")
        return ""


# ── selectel.ru scraper (tls-client + CDN JSON payload) ──────────────

def _resolve_nuxt(data: list, idx, depth: int = 0):
    """Resolve Nuxt devalue flat-array references.

    Nuxt serialises page state as a flat list. Integers within dicts/lists
    are pointers (indices) to other positions. Once dereferenced, primitive
    values (int, str, bool, None) are final — not followed further.
    """
    if depth > 8:
        return idx
    if isinstance(idx, int):
        val = data[idx]
        if isinstance(val, dict):
            return {k: _resolve_nuxt(data, v, depth + 1) for k, v in val.items()}
        if isinstance(val, list):
            return [_resolve_nuxt(data, v, depth + 1) for v in val]
        return val  # primitive (int, str, bool, None) — this IS the value
    return idx


def _get_selectel_cdn_url() -> str | None:
    """Fetch selectel dedicated page with Chrome TLS fingerprint, extract Nuxt CDN payload URL.

    selectel.ru/services/dedicated/ blocks Python's default TLS fingerprint (Cloudflare
    JA3 check). tls-client impersonates Chrome 120 to get through. The CDN URL for the
    Nuxt payload is embedded in the page as <script id="__NUXT_DATA__" data-src="...">.
    The CDN itself (cdn.selectel.ru) is accessible with plain requests.
    """
    try:
        session = tls_client.Session(client_identifier="chrome_120")
        r = session.get("https://selectel.ru/services/dedicated/", timeout_seconds=25)
        if r.status_code != 200:
            print(f"[selectel] Страница вернула {r.status_code}")
            return None
        soup = BeautifulSoup(r.text, "lxml")
        nuxt_el = soup.find(id="__NUXT_DATA__")
        if not nuxt_el:
            print("[selectel] Элемент __NUXT_DATA__ не найден")
            return None
        cdn_url = nuxt_el.get("data-src")
        return cdn_url or None
    except Exception as e:
        print(f"[selectel] Ошибка получения страницы: {e}")
        return None


def _selectel_cfg_to_row(cfg: dict, today: str) -> "ServerRow | None":
    """Один конфиг selectel (resolved payload или объект API) → ServerRow."""
    # Price (monthly RUB)
    price_collection = cfg.get("price_collection") or {}
    rub = price_collection.get("RUB") or {}
    price_rub = rub.get("month")
    if not price_rub:
        return None

    # CPU
    cpu_info = cfg.get("cpu") or {}
    cpu_model = cpu_info.get("name", "")
    if not cpu_model:
        return None

    # RAM — sum all entries (size × count)
    ram_list = cfg.get("ram") or []
    total_ram = sum(r.get("size", 0) * r.get("count", 1) for r in ram_list if isinstance(r, dict))
    if total_ram == 0:
        return None
    ram_gb = normalize_ram_gb(total_ram)

    # Disks — all pools; legacy disk_* fields keep the first one
    disk_list = cfg.get("disk") or []
    if not disk_list or not isinstance(disk_list[0], dict):
        return None
    disk_pools = [
        {
            "disk_type": normalize_disk_type(d.get("type", "HDD")),
            "disk_count": d.get("count", 1),
            "disk_size_gb": normalize_disk_gb(d.get("size", 0)),
        }
        for d in disk_list if isinstance(d, dict)
    ]
    first_disk = disk_list[0]
    disk_count = first_disk.get("count", 1)
    disk_size_raw = first_disk.get("size", 0)
    disk_type_raw = first_disk.get("type", "HDD")  # e.g. "SSD SATA", "SSD NVMe M.2", "HDD SATA"
    disk_size_gb = normalize_disk_gb(disk_size_raw)
    disk_type = normalize_disk_type(disk_type_raw)

    cpu_sockets = cpu_info.get("count") or 1
    cores_per_cpu = cpu_info.get("cores_per_cpu") or 0

    # Quantity: сумма available[].count по всем ДЦ (= бейдж «N шт.» на сайте).
    # Поле quantity API — константа 1 (мин. заказ), НЕ наличие — фолбэк, если available нет.
    available = cfg.get("available") or []
    quantity = sum(a.get("count", 0) for a in available if isinstance(a, dict))
    if not available:
        quantity = cfg.get("quantity") or 0

    return {
        "provider": "selectel",
        "cpu_model": cpu_model,
        "cpu_model_norm": normalize_cpu_model(cpu_model),
        "cpu_generation": extract_cpu_generation(cpu_model),
        "ram_gb": ram_gb,
        "disk_count": disk_count,
        "disk_size_gb": disk_size_gb,
        "disk_type": disk_type,
        "price_rub": float(price_rub),
        "quantity_available": quantity if quantity > 0 else None,
        "scraped_at": today,
        "plan_id": cfg.get("name") or "",
        "cpu_sockets": cpu_sockets,
        "cpu_cores_total": cpu_sockets * cores_per_cpu,
        "disk_pools": disk_pools,
        "currency": "RUB",
        "price_period": "month",
    }


def _parse_selectel_flat(flat: list, today: str) -> list[ServerRow]:
    """Build rows from the resolved Nuxt flat array. Pure function — used by tests."""
    rows = []

    # Find server config entries: dicts with cpu/ram/disk/price_collection keys
    for i, item in enumerate(flat):
        if not isinstance(item, dict):
            continue
        if not ("cpu" in item and "ram" in item and "disk" in item and "price_collection" in item):
            continue

        try:
            cfg = _resolve_nuxt(flat, i)
        except Exception:
            continue

        row = _selectel_cfg_to_row(cfg, today)
        if row:
            rows.append(row)

    if not rows:
        print("[selectel] JSON получен, но конфиги не распознаны")

    return rows


SELECTEL_PUB_API = "https://api.selectel.ru/servers/v2/pub/service/server"


def _scrape_selectel_api() -> list[ServerRow]:
    """Открытый API selectel: полный список готовых серверов одним запросом.

    С 2026-08 страница /services/dedicated/ больше не кладёт конфиги в Nuxt-payload —
    фронт берёт их отсюда же (servers/v2/pub/). Ретраи — из-за флапа исходящей сети WSL.
    """
    last_err = None
    for attempt in range(3):
        try:
            r = requests.get(SELECTEL_PUB_API, timeout=25,
                             headers={"User-Agent": HEADERS["User-Agent"]})
            r.raise_for_status()
            configs = r.json().get("result") or []
            break
        except Exception as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    else:
        print(f"[selectel] Ошибка запроса API: {last_err}")
        return []

    today = date.today().isoformat()
    rows = []
    for cfg in configs:
        if not isinstance(cfg, dict) or not cfg.get("is_order"):
            continue
        row = _selectel_cfg_to_row(cfg, today)
        if row:
            rows.append(row)
    return rows


SELECTEL_CALC_PRECUSTOM = "https://api.selectel.ru/servers/v2/pub/calculator/precustom"
SELECTEL_CALC_ITEMS = "https://api.selectel.ru/servers/v2/pub/calculator/items"


def _fetch_selectel_calc(url: str):
    """calculator/* отвечают только Chrome TLS-fingerprint (tls_client);
    plain requests висит в ReadTimeout навсегда. Ретраи — флап сети WSL."""
    last_err = None
    for attempt in range(3):
        try:
            s = tls_client.Session(client_identifier="chrome_120")
            r = s.get(url, timeout_seconds=30)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            return r.json()
        except Exception as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    print(f"[selectel] Ошибка calculator API {url}: {last_err}")
    return None


def _precustom_config_components(config: list) -> list[dict]:
    """Компоненты конфига в едином виде [{id, count}]. Второй формат API —
    список словарей {"<id>": count} (зеркало функций M/q фронта selectel)."""
    comps = []
    for entry in config or []:
        if isinstance(entry, dict) and "id" in entry:
            comps.append({"id": int(entry["id"]), "count": int(entry.get("count") or 0)})
        elif isinstance(entry, dict) and len(entry) == 1:
            (cid, count), = entry.items()
            comps.append({"id": int(cid), "count": int(count)})
    return comps


def _precustom_available(comps: list[dict], items_by_id: dict) -> int:
    """Наличие конфига = min по компонентам floor((quantity − spte) / нужно).
    Компонент без записи в items → 0 (зеркало функции F фронта selectel —
    сайт такие конфиги не показывает вовсе)."""
    need: dict[int, int] = {}
    for c in comps:
        need[c["id"]] = need.get(c["id"], 0) + c["count"]
    result = None
    for cid, cnt in need.items():
        if cnt <= 0:
            continue
        item = items_by_id.get(cid)
        avail = (item["quantity"] - item.get("spte", 0)) if item else 0
        fit = avail // cnt
        result = fit if result is None else min(result, fit)
    return result or 0


def _precustom_to_cfg(pre: dict, items_by_id: dict) -> dict | None:
    """Один конфиг calculator/precustom → dict в форме service/server
    (для повторного использования _selectel_cfg_to_row).

    Зеркало функции сборки фронта selectel (чанк seidoPrecustomServersStore):
    цена = сумма price.rub × count по компонентам, найденным в items,
    с enable и без is_hidden; ненайденные ПРОПУСКАЮТСЯ (как на сайте).
    Конфиги с GPU не выводим — эталоны Миран без GPU, сравнение цен
    с GPU-сервером было бы некорректным.
    """
    comps = _precustom_config_components(pre.get("config"))
    if not comps:
        return None

    total = 0.0
    cpu = None
    ram: list[dict] = []
    disks: list[dict] = []
    has_gpu = False
    for comp in comps:
        item = items_by_id.get(comp["id"])
        count = comp["count"]
        if not item or not count or not item.get("enable") or item.get("is_hidden"):
            continue
        model = item.get("model")
        param = item.get("param") or {}
        if model == "pcie":
            model = param.get("type")
        total += (item.get("price", {}).get("rub") or 0) * count
        if model == "cpu":
            cpu = {"name": item["name"], "count": count,
                   "cores_per_cpu": param.get("core") or 0}
        elif model == "gpu":
            has_gpu = True
        elif model == "ram":
            ram.append({"count": count, "size": param.get("size") or 0})
        elif model == "disk":
            disk_type = param.get("type") or ""
            if param.get("interface"):
                disk_type = f"{disk_type} {param['interface']}"
            disks.append({"count": count, "size": param.get("size") or 0,
                          "type": disk_type or "unknown"})

    # как на сайте: без CPU/RAM/дисков конфиг не собирается
    if not cpu or not ram or not disks or has_gpu:
        return None
    available = _precustom_available(comps, items_by_id)
    if available <= 0:
        return None  # сайт фильтрует available_count > 0 — не показывается

    return {
        "name": pre.get("name") or "",
        "cpu": cpu,
        "ram": ram,
        "disk": disks,
        "price_collection": {"RUB": {"month": total}},
        "quantity": available,
    }


def _scrape_selectel_precustom() -> list[ServerRow]:
    """Линейка «Configurable Pre-Build» (PCL*): собирается фронтом из
    calculator/precustom + calculator/items, в service/server её нет."""
    precustom = _fetch_selectel_calc(SELECTEL_CALC_PRECUSTOM)
    items = _fetch_selectel_calc(SELECTEL_CALC_ITEMS)
    if not isinstance(precustom, list) or not isinstance(items, list):
        return []
    items_by_id = {i["id"]: i for i in items
                   if isinstance(i, dict) and i.get("id") is not None}
    today = date.today().isoformat()
    rows = []
    for pre in precustom:
        if not isinstance(pre, dict):
            continue
        cfg = _precustom_to_cfg(pre, items_by_id)
        if not cfg:
            continue
        row = _selectel_cfg_to_row(cfg, today)
        if row:
            rows.append(row)
    print(f"[selectel] Precustom (PCL*): {len(rows)} из {len(precustom)} "
          "конфигов доступны и собраны")
    return rows


def scrape_selectel() -> list[ServerRow]:
    """Scrape selectel.ru: сначала открытый API, при неудаче — старый Nuxt CDN payload.

    Старый путь (актуален до 2026-08, оставлен как фолбэк):
    1. Fetch the page with tls-client (Chrome TLS fingerprint bypasses Cloudflare).
    2. Extract the CDN payload URL from <script id="__NUXT_DATA__" data-src="...">.
    3. Fetch the JSON from cdn.selectel.ru with plain requests (CDN is open).
    4. Parse with existing _resolve_nuxt decoder.
    """
    rows = _scrape_selectel_api()
    if rows:
        try:
            rows.extend(_scrape_selectel_precustom())
        except Exception as e:
            print(f"[selectel] Precustom-линейка недоступна: {e}")
        return rows

    cdn_url = _get_selectel_cdn_url()
    if not cdn_url:
        print("[selectel] Не удалось получить CDN URL")
        return []

    try:
        pr = requests.get(cdn_url, timeout=25, headers={"User-Agent": HEADERS["User-Agent"]})
        pr.raise_for_status()
        flat = pr.json()
    except Exception as e:
        print(f"[selectel] Ошибка загрузки CDN payload: {e}")
        return []

    if not isinstance(flat, list):
        print("[selectel] Неожиданный формат CDN payload")
        return []

    return _parse_selectel_flat(flat, date.today().isoformat())


# ── 1dedic.ru scraper (Playwright scroll + HTML parsing) ─────────────

def _parse_1dedic_article(article, today: str) -> "ServerRow | None":
    """Parse a single <article class='product-card'> element into a ServerRow.

    Structure (confirmed via browser DevTools):
      icon-cpu    → CPU vendor + model + freq/cores line
      icon-ram    → RAM size (e.g. "32 Гб")
      icon-hard-disk → disk spec (e.g. "2x 1000 Гб NVMe" or "750 Гб SSD")
      span.price__active → monthly price (e.g. "14 000 ₽")
    """
    options = article.find_all("div", class_="product-card__option")

    cpu_model = ""
    ram_gb = 0
    disk_count = 1
    disk_size_gb = 0
    disk_type = "HDD"

    for opt in options:
        icon = opt.find("i")
        if not icon:
            continue
        icon_cls = " ".join(icon.get("class", []))
        text = opt.get_text(separator=" ", strip=True)

        if "icon-cpu" in icon_cls:
            # "Amd Ryzen 9 5950X 3.4-4.9 ГГц, 16 ядер" → "Ryzen 9 5950X"
            cpu_model = re.sub(r"^(Amd|Intel)\s+", "", text, flags=re.I)
            cpu_model = re.sub(r"\s*\d+[\d.]*-[\d.]+\s*ГГц.*$", "", cpu_model, flags=re.I).strip()

        elif "icon-ram" in icon_cls:
            m = re.search(r"(\d+)", text)
            if m:
                ram_gb = normalize_ram_gb(int(m.group(1)))

        elif "icon-hard-disk" in icon_cls:
            # "2x 1000 Гб NVMe" — multi disk
            multi = re.search(
                r"(\d+)\s*[xX×]\s*(\d+)\s*(Гб|ТБ|GB|TB)\s*(NVMe|SSD|HDD)?",
                text, re.I
            )
            # "750 Гб SSD" — single disk
            single = re.search(r"(\d+)\s*(Гб|ТБ|GB|TB)\s*(NVMe|SSD|HDD)?", text, re.I)
            if multi:
                disk_count = int(multi.group(1))
                raw = int(multi.group(2))
                if "ТБ" in multi.group(3).upper() or "TB" in multi.group(3).upper():
                    raw *= 1000
                disk_size_gb = normalize_disk_gb(raw)
                disk_type = normalize_disk_type(text)
            elif single:
                disk_count = 1
                raw = int(single.group(1))
                if "ТБ" in single.group(2).upper() or "TB" in single.group(2).upper():
                    raw *= 1000
                disk_size_gb = normalize_disk_gb(raw)
                disk_type = normalize_disk_type(text)

    if not cpu_model or ram_gb == 0:
        return None

    price_el = article.find("span", class_="price__active")
    if not price_el:
        return None
    price_text = re.sub(r"[^\d]", "", price_el.get_text(strip=True))
    if not price_text:
        return None
    price_rub = float(price_text)
    if price_rub == 0:
        return None

    return {
        "provider": "1dedic",
        "cpu_model": cpu_model,
        "cpu_model_norm": normalize_cpu_model(cpu_model),
        "cpu_generation": extract_cpu_generation(cpu_model),
        "ram_gb": ram_gb,
        "disk_count": disk_count,
        "disk_size_gb": disk_size_gb,
        "disk_type": disk_type,
        "price_rub": price_rub,
        "quantity_available": None,
        "scraped_at": today,
    }


def scrape_1dedic() -> list[ServerRow]:
    """Scrape 1dedic.ru/ready_servers via Playwright scroll + HTML article parsing.

    The page is Drupal SSR + Vue 3. Initial load renders ~10 cards; the rest load
    as the Vue app initializes (hydrates from the tariff store). We wait for the
    card count to stabilise after a brief scroll loop, then parse all
    <article class='product-card'> elements using the confirmed DOM structure.

    Set SCRAPER_PROXY=socks5://host:port if needed (optional).
    """
    today = date.today().isoformat()
    rows = []

    try:
        with sync_playwright() as p:
            launch_args: dict = {"headless": True}
            if os.path.exists("/usr/bin/chromium-browser"):
                launch_args["executable_path"] = "/usr/bin/chromium-browser"
                launch_args["args"] = ["--no-sandbox", "--disable-dev-shm-usage"]

            browser = p.chromium.launch(**launch_args)
            ctx_kwargs: dict = {
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36",
                "locale": "ru-RU",
                "viewport": {"width": 1920, "height": 1080},
            }
            if _PROXY:
                ctx_kwargs["proxy"] = {"server": _PROXY}
            ctx = browser.new_context(**ctx_kwargs)
            page = ctx.new_page()
            try:
                page.goto(
                    "https://1dedic.ru/ready_servers",
                    wait_until="domcontentloaded",
                    timeout=45000,
                )
            except Exception:
                pass  # timeout on domcontentloaded is OK — parse what we have
            # Wait for Vue to hydrate and render the first batch of cards
            try:
                page.wait_for_selector("article.product-card", timeout=10000)
            except Exception:
                pass
            page.wait_for_timeout(3000)

            # Scroll loop: keep scrolling until card count stabilises.
            # Require 2 consecutive equal counts before stopping — one scroll
            # cycle (1200ms) is sometimes not enough for the server to respond.
            prev_count = 0
            zero_streak = 0
            stable_streak = 0
            for _ in range(35):
                count = page.locator("article.product-card").count()
                if count == 0:
                    zero_streak += 1
                    if zero_streak >= 4:
                        break
                else:
                    zero_streak = 0
                if count > 0 and count == prev_count:
                    stable_streak += 1
                    if stable_streak >= 2:
                        break
                else:
                    stable_streak = 0
                prev_count = count
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1200)

            html = page.content()
            browser.close()

    except Exception as e:
        print(f"[1dedic] Playwright error: {e}")
        html = ""

    if not html:
        print("[1dedic] Страница не загружена")
        return []

    soup = BeautifulSoup(html, "lxml")
    articles = soup.find_all("article", class_=re.compile(r"product-card"))

    print(f"[1dedic] Найдено карточек: {len(articles)}")
    if not articles and len(html) > 10000:
        all_arts = soup.find_all("article")
        print(f"[1dedic] <article> теги (любой класс): {len(all_arts)}")
        if all_arts:
            print(f"[1dedic] Классы первого: {all_arts[0].get('class')}")
        else:
            print(f"[1dedic] Заголовок HTML: {html[:1500]}")

    for article in articles:
        row = _parse_1dedic_article(article, today)
        if row:
            rows.append(row)

    if not rows:
        print("[1dedic] Карточки найдены, но не распарсены")

    return rows


# ── reg.cloud scraper (Playwright + JS) ──────────────────────────────

def _parse_regcloud_html(html: str, today: str) -> list[ServerRow]:
    """Parse reg.cloud dedicated page HTML. Pure function — used by tests."""
    soup = BeautifulSoup(html, "lxml")
    rows = []

    server_items = soup.find_all("div", class_="b-dedicated-servers-list-item-cloud")
    print(f"[regcloud] Найдено {len(server_items)} элементов серверов")

    for item in server_items:
        try:
            cpu_elem = item.find("p", class_="b-dedicated-servers-list-item-cloud__cpu-title")
            if not cpu_elem:
                continue
            cpu_model = cpu_elem.get_text(strip=True)
            # Capture leading socket count, e.g. "2 × AMD EPYC 9334" → sockets=2
            socket_match = re.match(r'^(\d+)\s*[×xX]\s*', cpu_model)
            cpu_sockets = int(socket_match.group(1)) if socket_match else 1
            cpu_model = re.sub(r'^\d+\s*[×xX]\s*', '', cpu_model).strip()
            if not cpu_model:
                continue

            plan_id = ""
            title_elem = item.find(class_="b-dedicated-servers-list-item-cloud__title")
            if title_elem:
                title_text = title_elem.get_text(strip=True)
                rd_match = re.search(r"RD-\d+", title_text)
                plan_id = rd_match.group(0) if rd_match else title_text

            cpu_cores_total = 0
            power_elem = item.find(class_="b-dedicated-servers-list-item-cloud__cpu-power")
            if power_elem:
                cores_match = re.search(
                    r"(\d+)\s*яд", power_elem.get_text(" ", strip=True), re.I
                )
                if cores_match:
                    cpu_cores_total = int(cores_match.group(1))

            ram_elem = item.find("p", class_="b-dedicated-servers-list-item-cloud__ram")
            if not ram_elem:
                continue
            ram_text = ram_elem.get_text(strip=True)
            ram_match = re.search(r"(\d+)\s*ГБ", ram_text, re.I)
            if not ram_match:
                continue
            ram_gb = normalize_ram_gb(int(ram_match.group(1)))

            disk_elem = item.find("p", class_="b-dedicated-servers-list-item-cloud__hdds")
            disk_text = disk_elem.get_text(strip=True) if disk_elem else ""
            # get_text(strip=True) glues adjacent pools ("…SSD SATA2 x 12 ТБ…"),
            # so pools are extracted from a space-separated variant
            disk_text_spaced = disk_elem.get_text(" ", strip=True) if disk_elem else ""
            disk_pools = []
            for pool_m in re.finditer(
                # type tail is multi-word ("SSD NVMe U.2") — normalize_disk_type
                # prioritises NVMe over SSD within it
                r"(\d+)\s*[хxX×]\s*(\d+(?:[.,]\d+)?)\s*(ГБ|ТБ)"
                r"((?:\s*(?:NVMe|SSD|HDD|SATA|U\.2|M\.2))*)",
                disk_text_spaced, re.I
            ):
                pool_size = float(pool_m.group(2).replace(",", "."))
                if "ТБ" in pool_m.group(3).upper():
                    pool_size *= 1000
                disk_pools.append({
                    "disk_type": normalize_disk_type(pool_m.group(4) or ""),
                    "disk_count": int(pool_m.group(1)),
                    "disk_size_gb": normalize_disk_gb(int(pool_size)),
                })

            # Bug B fix: support decimal TB sizes (e.g. "3.8 ТБ", "1.9 ТБ")
            disk_match = re.search(
                r"(\d+)\s*[хxX×]\s*(\d+(?:[.,]\d+)?)\s*(ГБ|ТБ)\s*(NVMe|SSD|HDD|SATA)?",
                disk_text, re.I
            )
            disk_count, disk_size_gb, disk_type = 1, 0, "HDD"
            if disk_match:
                disk_count = int(disk_match.group(1))
                raw_size = float(re.sub(r"\s+", "", disk_match.group(2)).replace(",", "."))
                unit = disk_match.group(3)
                if "ТБ" in unit.upper():
                    raw_size = int(raw_size * 1000)
                else:
                    raw_size = int(raw_size)
                disk_size_gb = normalize_disk_gb(raw_size)
                disk_type = normalize_disk_type(disk_text)
            else:
                disk_single = re.search(
                    r"(\d+(?:[.,]\d+)?)\s*(ГБ|ТБ)\s*(NVMe|SSD|HDD)?",
                    disk_text, re.I
                )
                if disk_single:
                    raw_size = float(re.sub(r"\s+", "", disk_single.group(1)).replace(",", "."))
                    unit = disk_single.group(2)
                    if "ТБ" in unit.upper():
                        raw_size = int(raw_size * 1000)
                    else:
                        raw_size = int(raw_size)
                    disk_size_gb = normalize_disk_gb(raw_size)
                    disk_type = normalize_disk_type(disk_text)

            if disk_size_gb == 0:
                continue

            # Bug A fix: class names changed; prefer current-price (discounted) over base-price
            price_elem = (
                item.find("p", class_="b-dedicated-servers-list-item-cloud__current-price")
                or item.find("p", class_="b-dedicated-servers-list-item-cloud__base-price")
            )

            if not price_elem:
                continue

            price_text = price_elem.get_text(strip=True)
            price_match = re.search(r"([\d\s\u00a0]+)", price_text)
            if not price_match:
                continue
            price_rub = float(re.sub(r"[\s\u00a0]", "", price_match.group(1)))

            if not disk_pools and disk_size_gb:
                disk_pools = [{
                    "disk_type": disk_type,
                    "disk_count": disk_count,
                    "disk_size_gb": disk_size_gb,
                }]

            rows.append({
                "provider": "regcloud",
                "cpu_model": cpu_model,
                "cpu_model_norm": normalize_cpu_model(cpu_model),
                "cpu_generation": extract_cpu_generation(cpu_model),
                "ram_gb": ram_gb,
                "disk_count": disk_count,
                "disk_size_gb": disk_size_gb,
                "disk_type": disk_type,
                "price_rub": price_rub,
                "quantity_available": None,
                "scraped_at": today,
                "plan_id": plan_id,
                "cpu_sockets": cpu_sockets,
                "cpu_cores_total": cpu_cores_total,
                "disk_pools": disk_pools,
                "currency": "RUB",
                "price_period": "month",
            })

        except Exception:
            continue

    if not rows:
        print(f"[regcloud] Не удалось извлечь конфигурации. HTML preview:")
        print(html[:2000])

    return rows


def scrape_regcloud() -> list[ServerRow]:
    """Scrape reg.cloud/dedicated/ using Playwright."""
    html = _scrape_with_playwright(
        "https://reg.cloud/dedicated/",
        "regcloud",
        wait_selector=".b-dedicated-servers-list-item-cloud"
    )
    if not html:
        return []
    return _parse_regcloud_html(html, date.today().isoformat())


# ── netrack.ru scraper (static HTML, data-* attributes) ──────────────

def _parse_netrack_html(html: bytes | str, today: str) -> list[ServerRow]:
    """Parse netrack.ru/dedicated. Static cards with data-* attrs.
    data-price '8 194₽', data-cpu, data-ram '64', data-disk1/2/3 '1 ТБ'/'960 GB',
    data-disk_nvme 'NVMe'. Pure function — used by tests.
    """
    soup = BeautifulSoup(html, "lxml")
    rows = []

    for el in soup.find_all(attrs={"data-price": True}):
        try:
            price_raw = re.sub(r"[^\d]", "", el.get("data-price", ""))
            if not price_raw:
                continue
            price_rub = float(price_raw)
            if price_rub == 0:
                continue

            cpu_model = el.get("data-cpu", "").strip()
            if not cpu_model:
                continue

            ram_raw = el.get("data-ram", "0")
            ram_gb = normalize_ram_gb(int(re.sub(r"[^\d]", "", ram_raw) or "0"))
            if ram_gb == 0:
                continue

            # Count populated disk slots (data-disk1, data-disk2, data-disk3)
            disk_slots = [el.get(f"data-disk{i}", "").strip() for i in range(1, 4)]
            disk_slots = [d for d in disk_slots if d]
            disk_count = len(disk_slots) if disk_slots else 1

            # Parse size from first slot ("1 ТБ", "960 GB", "480 ГБ")
            disk_size_gb = 0
            if disk_slots:
                m = re.search(
                    r"(\d+(?:[.,]\d+)?)\s*(ТБ|TB|ГБ|GB)", disk_slots[0], re.I
                )
                if m:
                    raw = float(m.group(1).replace(",", "."))
                    unit = m.group(2).upper()
                    if unit in ("ТБ", "TB"):
                        raw = raw * 1000
                    disk_size_gb = normalize_disk_gb(int(raw))

            if disk_size_gb == 0:
                continue

            # Disk type: data-disk_nvme carries 'NVMe'; if absent check slots text
            disk_type_hint = el.get("data-disk_nvme", "").strip()
            if not disk_type_hint and disk_slots:
                disk_type_hint = " ".join(disk_slots)
            disk_type = normalize_disk_type(disk_type_hint) if disk_type_hint else "HDD"

            rows.append({
                "provider": "netrack",
                "cpu_model": cpu_model,
                "cpu_model_norm": normalize_cpu_model(cpu_model),
                "cpu_generation": extract_cpu_generation(cpu_model),
                "ram_gb": ram_gb,
                "disk_count": disk_count,
                "disk_size_gb": disk_size_gb,
                "disk_type": disk_type,
                "price_rub": price_rub,
                "quantity_available": None,
                "scraped_at": today,
            })
        except Exception:
            continue

    return rows


def scrape_netrack() -> list[ServerRow]:
    """Scrape netrack.ru/dedicated using Playwright.

    The 'Готовые серверы' (Ready Servers) tab content is AJAX-loaded on tab click —
    not present in static HTML. We click the tab button and wait for content to appear.
    """
    url = "https://netrack.ru/dedicated"
    today = date.today().isoformat()
    try:
        with sync_playwright() as p:
            launch_args: dict = {"headless": True}
            if os.path.exists("/usr/bin/chromium-browser"):
                launch_args["executable_path"] = "/usr/bin/chromium-browser"
                launch_args["args"] = ["--no-sandbox", "--disable-dev-shm-usage"]
            browser = p.chromium.launch(**launch_args)
            ctx_kwargs: dict = {
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36",
                "locale": "ru-RU",
            }
            if _PROXY:
                ctx_kwargs["proxy"] = {"server": _PROXY}
            ctx = browser.new_context(**ctx_kwargs)
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)
            # Click "Готовые серверы" tab to trigger AJAX load of ready-server catalog
            try:
                page.click("button.ready_btn", timeout=5000)
                page.wait_for_timeout(3000)
            except Exception:
                pass
            html = page.content()
            browser.close()
    except Exception as e:
        print(f"[netrack] Playwright error: {e}")
        return []
    return _parse_netrack_html(html, today)


# ── timeweb.com scraper (static HTML cards) ───────────────────────────

def _parse_timeweb_html(html: bytes | str, today: str) -> list[ServerRow]:
    """Parse timeweb.com dedicated server listing.
    Pure function — used by tests.

    TODO(RU-IP): selectors/regexes need confirmation from a Russian IP.
    Capture a live fixture via tests/capture_fixtures.py and refine.
    """
    if isinstance(html, bytes):
        html = html.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "lxml")
    rows = []

    # Timeweb renders ready-config cards; try common card wrappers.
    cards = (
        soup.find_all("div", class_=re.compile(r"tariff|server|dedic|card", re.I))
        or soup.find_all("article")
    )

    for card in cards:
        try:
            text = card.get_text(separator=" ", strip=True)

            cpu_m = re.search(
                r"((?:Intel\s+)?Xeon\s+[\w\-]+(?:\s+[Vv]\d+)?|"
                r"(?:AMD\s+)?(?:EPYC|Ryzen)\s+\d[\w\-]*|"
                r"Core\s+i\d[-\w]+)",
                text, re.I,
            )
            if not cpu_m:
                continue
            cpu_model = cpu_m.group(0).strip()

            ram_m = re.search(r"(\d+)\s*(ГБ|GB)\s*(?:DDR|RAM|памяти|оперативн)", text, re.I)
            if not ram_m:
                # Fallback: first standalone "NN ГБ" that isn't a disk size
                ram_m = re.search(r"\b(\d+)\s*(ГБ|GB)\b", text, re.I)
            if not ram_m:
                continue
            ram_gb = normalize_ram_gb(int(ram_m.group(1)))
            if ram_gb == 0:
                continue

            disk_multi = re.search(
                r"(\d+)\s*[×xX\*]\s*(\d+(?:[.,]\d+)?)\s*(ГБ|ТБ|GB|TB)\s*(NVMe|SSD|HDD)?",
                text, re.I,
            )
            disk_single = re.search(
                r"(\d+(?:[.,]\d+)?)\s*(ГБ|ТБ|GB|TB)\s*(NVMe|SSD|HDD)?",
                text, re.I,
            )
            disk_count, disk_size_gb, disk_type = 1, 0, "HDD"
            if disk_multi:
                disk_count = int(disk_multi.group(1))
                raw = float(disk_multi.group(2).replace(",", "."))
                unit = disk_multi.group(3).upper()
                if unit in ("ТБ", "TB"):
                    raw *= 1000
                disk_size_gb = normalize_disk_gb(int(raw))
                disk_type = normalize_disk_type(disk_multi.group(4) or text)
            elif disk_single:
                raw = float(disk_single.group(1).replace(",", "."))
                unit = disk_single.group(2).upper()
                if unit in ("ТБ", "TB"):
                    raw *= 1000
                disk_size_gb = normalize_disk_gb(int(raw))
                disk_type = normalize_disk_type(disk_single.group(3) or text)

            if disk_size_gb == 0:
                continue

            price_m = re.search(r"([\d\s ]+)\s*₽\s*/?\s*(?:мес|месяц|мо|month)?", text, re.I)
            if not price_m:
                continue
            price_rub = float(re.sub(r"[\s ]", "", price_m.group(1)))
            if price_rub == 0:
                continue

            rows.append({
                "provider": "timeweb",
                "cpu_model": cpu_model,
                "cpu_model_norm": normalize_cpu_model(cpu_model),
                "cpu_generation": extract_cpu_generation(cpu_model),
                "ram_gb": ram_gb,
                "disk_count": disk_count,
                "disk_size_gb": disk_size_gb,
                "disk_type": disk_type,
                "price_rub": price_rub,
                "quantity_available": None,
                "scraped_at": today,
            })
        except Exception:
            continue

    if not rows:
        print("[timeweb] Конфигурации не найдены (нужно уточнить CSS-селекторы с RU IP)")
    return rows


def scrape_timeweb() -> list[ServerRow]:
    """Scrape timeweb.com/ru/services/dedicated-server/ via requests+BS4, tls_client fallback."""
    url = "https://timeweb.com/ru/services/dedicated-server/"
    today = date.today().isoformat()
    try:
        session = make_session(url)
        r = session.get(url, timeout=25)
        if r.status_code == 200:
            rows = _parse_timeweb_html(r.content, today)
            if rows:
                return rows
    except Exception:
        pass
    try:
        kwargs: dict = {"timeout_seconds": 25}
        if _PROXY:
            kwargs["proxy"] = _PROXY
        s = tls_client.Session(client_identifier="chrome_120")
        r2 = s.get(url, **kwargs)
        if r2.status_code == 200:
            return _parse_timeweb_html(r2.text, today)
        print(f"[timeweb] HTTP {r2.status_code}")
    except Exception as e:
        print(f"[timeweb] Ошибка: {e}")
    return []


# ── timeweb.cloud scraper (inline __NUXT_DATA__ JSON) ─────────────────
# Отдельный сайт Timeweb Cloud (ТЗ клиента: location=msk). Нужен только
# matching-пайплайну (competitor_pipeline.py) — в scrape_all() не входит.

def _parse_storage_pool(text: str) -> dict | None:
    """Parse one storageList entry like '2 x 480 ГБ SSD' / '1 x 3.84 ТБ NVMe'."""
    m = re.search(
        r"(?:(\d+)\s*[хxX×]\s*)?(\d+(?:[.,]\d+)?)\s*(ГБ|ТБ|GB|TB)",
        text, re.I
    )
    if not m:
        return None
    size = float(m.group(2).replace(",", "."))
    if m.group(3).upper() in ("ТБ", "TB"):
        size *= 1000
    return {
        # one storageList entry = one pool, so the whole string is safe for type
        "disk_type": normalize_disk_type(text),
        "disk_count": int(m.group(1) or 1),
        "disk_size_gb": normalize_disk_gb(int(size)),
    }


def _parse_timeweb_cloud_nuxt(
    flat: list, today: str, locations: tuple[str, ...] = ("msk",)
) -> list[ServerRow]:
    """Parse timeweb.cloud Nuxt flat array. Pure function — used by tests.

    priceNumber = стандартная месячная цена; поле price — скидочная цена при
    аренде на leaseTerm месяцев, для паритета с помесячными ценами конкурентов
    не используется (решение подтвердить у клиента).
    """
    rows: list[ServerRow] = []
    for i, item in enumerate(flat):
        if not isinstance(item, dict):
            continue
        if not {"cpu", "presetId", "storageList"} <= item.keys():
            continue
        try:
            cfg = _resolve_nuxt(flat, i)
        except Exception:
            continue

        if cfg.get("location") not in locations:
            continue

        cpu_raw = (cfg.get("cpu") or "").strip()
        if not cpu_raw:
            continue
        socket_match = re.match(r"^(\d+)\s*[хxX×]\s*", cpu_raw)
        cpu_sockets = int(socket_match.group(1)) if socket_match else 1
        cpu_model = re.sub(r"^\d+\s*[хxX×]\s*", "", cpu_raw).strip()

        cpu_cores_total = cfg.get("cpuCount") or 0
        if not cpu_cores_total:
            cores_match = re.search(r"(\d+)\s*яд", cfg.get("cpuParams") or "", re.I)
            if cores_match:
                cpu_cores_total = int(cores_match.group(1))

        ram_raw = cfg.get("memoryCount") or 0
        if not ram_raw:
            continue
        ram_gb = normalize_ram_gb(int(ram_raw))

        disk_pools = []
        for pool_text in cfg.get("storageList") or []:
            if not isinstance(pool_text, str):
                continue
            pool = _parse_storage_pool(pool_text)
            if pool:
                disk_pools.append(pool)
        if not disk_pools:
            continue

        price = cfg.get("priceNumber")
        if not price:
            continue

        rows.append({
            "provider": "timeweb_cloud",
            "cpu_model": cpu_model,
            "cpu_model_norm": normalize_cpu_model(cpu_model),
            "cpu_generation": extract_cpu_generation(cpu_model),
            "ram_gb": ram_gb,
            "disk_count": disk_pools[0]["disk_count"],
            "disk_size_gb": disk_pools[0]["disk_size_gb"],
            "disk_type": disk_pools[0]["disk_type"],
            "price_rub": float(price),
            "quantity_available": None,
            "scraped_at": today,
            "plan_id": cfg.get("name") or "",
            "cpu_sockets": cpu_sockets,
            "cpu_cores_total": cpu_cores_total,
            "disk_pools": disk_pools,
            "currency": "RUB",
            "price_period": "month",
        })

    return rows


def scrape_timeweb_cloud(locations: tuple[str, ...] = ("msk",)) -> list[ServerRow]:
    """Scrape timeweb.cloud/services/dedicated-server (inline __NUXT_DATA__)."""
    url = "https://timeweb.cloud/services/dedicated-server?location=msk"
    today = date.today().isoformat()

    html = None
    try:
        session = make_session(url)
        if _PROXY:
            session.proxies = {"http": _PROXY, "https": _PROXY}
        r = session.get(url, timeout=25)
        if r.status_code == 200:
            html = r.text
        else:
            print(f"[timeweb_cloud] HTTP {r.status_code}, пробую tls_client")
    except Exception as e:
        print(f"[timeweb_cloud] requests не прошёл ({e}), пробую tls_client")

    if html is None:
        try:
            kwargs: dict = {"timeout_seconds": 25}
            if _PROXY:
                kwargs["proxy"] = _PROXY
            s = tls_client.Session(client_identifier="chrome_120")
            r2 = s.get(url, **kwargs)
            if r2.status_code != 200:
                print(f"[timeweb_cloud] HTTP {r2.status_code}")
                return []
            html = r2.text
        except Exception as e:
            print(f"[timeweb_cloud] Ошибка загрузки: {e}")
            return []

    soup = BeautifulSoup(html, "lxml")
    nuxt_el = soup.find(id="__NUXT_DATA__")
    if not nuxt_el or not nuxt_el.string:
        print("[timeweb_cloud] Элемент __NUXT_DATA__ не найден")
        return []
    try:
        flat = json.loads(nuxt_el.string)
    except Exception as e:
        print(f"[timeweb_cloud] Ошибка разбора __NUXT_DATA__: {e}")
        return []
    if not isinstance(flat, list):
        print("[timeweb_cloud] Неожиданный формат __NUXT_DATA__")
        return []

    rows = _parse_timeweb_cloud_nuxt(flat, today, locations)
    print(f"[timeweb_cloud] Распарсено тарифов: {len(rows)} (локации: {', '.join(locations)})")
    return rows


# ── hostkey.ru scraper (static HTML cards) ────────────────────────────

def _parse_hostkey_html(html: bytes | str, today: str) -> list[ServerRow]:
    """Parse hostkey.ru/dedicated-servers/instant/.
    Pure function — used by tests.

    TODO(RU-IP): selectors/regexes need confirmation from a Russian IP.
    Capture a live fixture via tests/capture_fixtures.py and refine.
    """
    if isinstance(html, bytes):
        html = html.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "lxml")
    rows = []

    cards = (
        soup.find_all("div", class_=re.compile(r"server|tariff|card|item|config", re.I))
        or soup.find_all("article")
    )

    for card in cards:
        try:
            text = card.get_text(separator=" ", strip=True)

            cpu_m = re.search(
                r"((?:Intel\s+)?Xeon\s+[\w\-]+(?:\s+[Vv]\d+)?|"
                r"(?:AMD\s+)?(?:EPYC|Ryzen)\s+\d[\w\-]*|"
                r"Core\s+i\d[-\w]+)",
                text, re.I,
            )
            if not cpu_m:
                continue
            cpu_model = cpu_m.group(0).strip()

            ram_m = re.search(r"\b(\d+)\s*(ГБ|GB|GiB)\b", text, re.I)
            if not ram_m:
                continue
            ram_gb = normalize_ram_gb(int(ram_m.group(1)))
            if ram_gb == 0:
                continue

            disk_multi = re.search(
                r"(\d+)\s*[×xX\*]\s*(\d+(?:[.,]\d+)?)\s*(ГБ|ТБ|GB|TB)\s*(NVMe|SSD|HDD)?",
                text, re.I,
            )
            disk_single = re.search(
                r"(\d+(?:[.,]\d+)?)\s*(ГБ|ТБ|GB|TB)\s*(NVMe|SSD|HDD)?",
                text, re.I,
            )
            disk_count, disk_size_gb, disk_type = 1, 0, "HDD"
            if disk_multi:
                disk_count = int(disk_multi.group(1))
                raw = float(disk_multi.group(2).replace(",", "."))
                unit = disk_multi.group(3).upper()
                if unit in ("ТБ", "TB"):
                    raw *= 1000
                disk_size_gb = normalize_disk_gb(int(raw))
                disk_type = normalize_disk_type(disk_multi.group(4) or text)
            elif disk_single:
                raw = float(disk_single.group(1).replace(",", "."))
                unit = disk_single.group(2).upper()
                if unit in ("ТБ", "TB"):
                    raw *= 1000
                disk_size_gb = normalize_disk_gb(int(raw))
                disk_type = normalize_disk_type(disk_single.group(3) or text)

            if disk_size_gb == 0:
                continue

            price_m = re.search(r"([\d\s ]+)\s*₽", text)
            if not price_m:
                # Hostkey also shows prices in USD/EUR on some pages
                price_m = re.search(r"\$\s*([\d\s ,]+)", text)
            if not price_m:
                continue
            price_rub = float(re.sub(r"[\s ,]", "", price_m.group(1)))
            if price_rub == 0:
                continue

            rows.append({
                "provider": "hostkey",
                "cpu_model": cpu_model,
                "cpu_model_norm": normalize_cpu_model(cpu_model),
                "cpu_generation": extract_cpu_generation(cpu_model),
                "ram_gb": ram_gb,
                "disk_count": disk_count,
                "disk_size_gb": disk_size_gb,
                "disk_type": disk_type,
                "price_rub": price_rub,
                "quantity_available": None,
                "scraped_at": today,
            })
        except Exception:
            continue

    if not rows:
        print("[hostkey] Конфигурации не найдены (нужно уточнить CSS-селекторы с RU IP)")
    return rows


def scrape_hostkey() -> list[ServerRow]:
    """Scrape hostkey.ru/dedicated-servers/instant/ using Playwright.

    The server catalog is JS-rendered — static HTML has no price data.
    """
    url = "https://hostkey.ru/dedicated-servers/instant/"
    today = date.today().isoformat()
    try:
        with sync_playwright() as p:
            launch_args: dict = {"headless": True}
            if os.path.exists("/usr/bin/chromium-browser"):
                launch_args["executable_path"] = "/usr/bin/chromium-browser"
                launch_args["args"] = ["--no-sandbox", "--disable-dev-shm-usage"]
            browser = p.chromium.launch(**launch_args)
            ctx_kwargs: dict = {
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36",
                "locale": "ru-RU",
            }
            if _PROXY:
                ctx_kwargs["proxy"] = {"server": _PROXY}
            ctx = browser.new_context(**ctx_kwargs)
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            page.wait_for_timeout(3000)
            html = page.content()
            browser.close()
    except Exception as e:
        print(f"[hostkey] Playwright error: {e}")
        return []
    return _parse_hostkey_html(html, today)


# ── it-lite.ru scraper (stub — structure unverifiable from non-RU IP) ─

def _parse_itlite_html(html: bytes | str, today: str) -> list[ServerRow]:
    """Parse it-lite.ru dedicated server listing.
    Pure function — used by tests.

    TODO(RU-IP): site fully blocked from non-Russian IP — structure unknown.
    Once accessible, determine card selector + CPU/RAM/disk/price field locations,
    capture fixture via tests/capture_fixtures.py, then implement parsing.
    """
    return []


def scrape_itlite() -> list[ServerRow]:
    """Scrape it-lite.ru dedicated page.

    Currently a stub — returns [] until selectors are confirmed from a Russian IP.
    Set SCRAPER_PROXY to a Russian endpoint and run to verify.
    """
    url = "https://it-lite.ru/dedicated/"
    today = date.today().isoformat()
    try:
        kwargs: dict = {"timeout_seconds": 25}
        if _PROXY:
            kwargs["proxy"] = _PROXY
        s = tls_client.Session(client_identifier="chrome_120")
        r = s.get(url, **kwargs)
        if r.status_code == 200:
            rows = _parse_itlite_html(r.text, today)
            if not rows:
                print("[it-lite] Страница загружена, но парсер — заглушка (TODO: заполнить селекторы)")
            return rows
        print(f"[it-lite] HTTP {r.status_code}")
    except Exception as e:
        print(f"[it-lite] Ошибка: {e}")
    return []


# ── Orchestration ────────────────────────────────────────────────────

def scrape_all() -> pd.DataFrame:
    """Scrape all 8 providers, catch exceptions per provider."""
    all_rows = []

    for scrape_fn, provider in [
        (scrape_miran, "miran"),
        (scrape_selectel, "selectel"),
        (scrape_1dedic, "1dedic"),
        (scrape_regcloud, "regcloud"),
        (scrape_netrack, "netrack"),
        (scrape_timeweb, "timeweb"),
        (scrape_hostkey, "hostkey"),
        (scrape_itlite, "it-lite"),
    ]:
        try:
            rows = scrape_fn()
            save_raw_json(provider, rows)
            all_rows.extend(rows)
            print(f"[{provider}] {len(rows)} configs scraped")
        except Exception as e:
            print(f"[{provider}] ERROR: {e}")
            import traceback
            traceback.print_exc()
            # Continue — don't crash the whole run

    if not all_rows:
        # Return empty DataFrame with correct schema
        return pd.DataFrame(columns=[
            "provider", "cpu_model", "cpu_model_norm", "cpu_generation",
            "ram_gb", "disk_count", "disk_size_gb", "disk_type",
            "price_rub", "quantity_available", "scraped_at"
        ])

    return pd.DataFrame(all_rows)


def save_history(df: pd.DataFrame) -> None:
    """Append to history.csv with deduplication."""
    if df.empty:
        return

    df = df[[c for c in LEGACY_HISTORY_COLS if c in df.columns]].copy()

    os.makedirs(DATA_DIR, exist_ok=True)

    if os.path.exists(HISTORY_CSV):
        existing = pd.read_csv(HISTORY_CSV)
        # Cast numeric key cols to int (CSV read makes them float)
        for col in ["ram_gb", "disk_count", "disk_size_gb"]:
            if col in existing.columns:
                existing[col] = existing[col].astype("Int64")
            if col in df.columns:
                df[col] = df[col].astype("Int64")
        combined = pd.concat([existing, df], ignore_index=True)
        combined = combined.drop_duplicates(subset=DEDUP_KEY_COLS, keep="last")
    else:
        combined = df.copy()
        for col in ["ram_gb", "disk_count", "disk_size_gb"]:
            if col in combined.columns:
                combined[col] = combined[col].astype("Int64")

    combined.to_csv(HISTORY_CSV, index=False, encoding="utf-8-sig")


def save_raw_json(provider: str, rows: list) -> None:
    """Save raw scrape results to data/{provider}_YYYYMMDD.json."""
    os.makedirs(DATA_DIR, exist_ok=True)
    today = date.today().isoformat().replace("-", "")
    fname = os.path.join(DATA_DIR, f"{provider}_{today}.json")
    with open(fname, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
