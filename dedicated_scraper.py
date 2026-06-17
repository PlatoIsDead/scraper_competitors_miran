# Dedicated Server Price Comparison Scraper
# Скрейпит 8 хостингов: miran.ru, selectel.ru, 1dedic.ru, reg.cloud,
# hostkey.ru, it-lite.ru, netrack.ru, timeweb.com

import os
import re
import json
import requests
import tls_client
from datetime import date
from typing import TypedDict
import pandas as pd
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from scraper import HEADERS, make_session

# Optional SOCKS5/HTTP proxy for providers that block direct connections.
# Example: export SCRAPER_PROXY="socks5://127.0.0.1:1080"
_PROXY = os.environ.get("SCRAPER_PROXY") or None


# ── Constants ────────────────────────────────────────────────────────

DATA_DIR = "data"
HISTORY_CSV = os.path.join(DATA_DIR, "history.csv")

DISK_STANDARDS = [120, 240, 480, 1000, 2000, 4000, 8000]
RAM_STANDARDS = [8, 16, 32, 64, 128, 256, 512, 1024]

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


# ── Normalization functions ──────────────────────────────────────────

def normalize_disk_gb(raw_gb: int) -> int:
    """Snap to nearest standard disk size."""
    return min(DISK_STANDARDS, key=lambda s: abs(s - raw_gb))


def normalize_ram_gb(raw_gb: int) -> int:
    """Snap to nearest standard RAM size."""
    return min(RAM_STANDARDS, key=lambda s: abs(s - raw_gb))


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
    wait_selector: str | None = None
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


def scrape_selectel() -> list[ServerRow]:
    """Scrape selectel.ru via their Nuxt CDN JSON payload.

    1. Fetch the page with tls-client (Chrome TLS fingerprint bypasses Cloudflare).
    2. Extract the CDN payload URL from <script id="__NUXT_DATA__" data-src="...">.
    3. Fetch the JSON from cdn.selectel.ru with plain requests (CDN is open).
    4. Parse with existing _resolve_nuxt decoder.
    """
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

    today = date.today().isoformat()
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

        # Price (monthly RUB)
        price_collection = cfg.get("price_collection") or {}
        rub = price_collection.get("RUB") or {}
        price_rub = rub.get("month")
        if not price_rub:
            continue

        # CPU
        cpu_info = cfg.get("cpu") or {}
        cpu_model = cpu_info.get("name", "")
        if not cpu_model:
            continue

        # RAM — sum all entries (size × count)
        ram_list = cfg.get("ram") or []
        total_ram = sum(r.get("size", 0) * r.get("count", 1) for r in ram_list if isinstance(r, dict))
        if total_ram == 0:
            continue
        ram_gb = normalize_ram_gb(total_ram)

        # Disk — use first entry
        disk_list = cfg.get("disk") or []
        if not disk_list or not isinstance(disk_list[0], dict):
            continue
        first_disk = disk_list[0]
        disk_count = first_disk.get("count", 1)
        disk_size_raw = first_disk.get("size", 0)
        disk_type_raw = first_disk.get("type", "HDD")  # e.g. "SSD SATA", "SSD NVMe M.2", "HDD SATA"
        disk_size_gb = normalize_disk_gb(disk_size_raw)
        disk_type = normalize_disk_type(disk_type_raw)

        # Quantity — sum across all locations
        available = cfg.get("available") or []
        quantity = sum(a.get("count", 0) for a in available if isinstance(a, dict))

        rows.append({
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
        })

    if not rows:
        print("[selectel] JSON получен, но конфиги не распознаны")

    return rows


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
            # Strip leading socket count, e.g. "2 × AMD EPYC 9334" → "AMD EPYC 9334"
            cpu_model = re.sub(r'^\d+\s*[×xX]\s*', '', cpu_model).strip()
            if not cpu_model:
                continue

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
