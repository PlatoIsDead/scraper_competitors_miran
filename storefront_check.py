# Сверка нашего скрейпа с тем, что видит покупатель на витрине конкурента.
#
# Мотив: все правки августа 2026 (перечёркнутая цена reg.cloud, распроданные
# конфиги selectel, cpuCount=28 у timeweb) были одного класса — мы поверили
# машинному полю, которое расходится с карточкой. Клиент находил это раньше нас.
# Здесь каждый конкурент проверяется ВТОРЫМ, независимым от парсера путём.

import logging
import re

from config_loader import timeweb_cloud_source

log = logging.getLogger("storefront_check")

# Каталог timeweb.cloud отдаётся ещё и отдельным JSON — источник, не зависящий
# от разбора __NUXT_DATA__. Локации там названы иначе, чем в payload страницы:
# ru (Санкт-Петербург) = ru-1, msk (Москва) = ru-3. Какую сверять — решает
# та же запись competitors.json, что и скрейп (timeweb_cloud_source).
TIMEWEB_PRESETS_URL = "https://timeweb.cloud/landing-api/dedicated/presets"
TIMEWEB_LOCATIONS = {"msk": "ru-3", "ru": "ru-1"}


def _regcloud_clearance_ids(html: str) -> set[str]:
    """Коды карточек «Распродажа» — по тому же правилу, что и в скрейпе."""
    from bs4 import BeautifulSoup

    from dedicated_scraper import _regcloud_is_clearance

    out: set[str] = set()
    soup = BeautifulSoup(html, "lxml")
    for item in soup.find_all("div", class_="b-dedicated-servers-list-item-cloud"):
        if not _regcloud_is_clearance(item):
            continue
        code = item.get("data-id") or ""
        if not code:
            raw = item.get("data-server-id") or ""
            code = raw[3:] if raw.startswith("RD-") else ""
        if code:
            out.add(f"RD-{code}")
    return out


def diff_regcloud(rows: list[dict], html: str) -> list[dict]:
    """Цена из карточки (data-price) против цены, которую вытащил парсер.

    Вёрстка листинга reg.cloud за август менялась дважды, и оба раза парсер
    молча брал перечёркнутую цену. Атрибут data-price правит тот же шаблон,
    что и видимую цену, но другим полем — расхождение = сигнал.
    """
    # Код тарифа берём из data-id: у отдельных карточек data-server-id
    # оказывается склеен с соседним атрибутом (живой пример — RD-58998,
    # data-business="data-server-id=RD-58998"), а data-id цел всегда.
    card_prices: dict[str, int] = {}
    for tag in re.finditer(r"<div[^>]*data-price=\"\d+\"[^>]*>", html):
        text = tag.group(0)
        price = re.search(r'data-price="(\d+)"', text)
        code = re.search(r'data-id="(\d+)"', text)
        if not code:
            code = re.search(r'data-server-id="RD-(\d+)"', text)
        if price and code:
            card_prices[f"RD-{code.group(1)}"] = int(price.group(1))
    if not card_prices:
        return [{"kind": "check_failed",
                 "detail": "в разметке листинга нет data-price — вёрстка "
                           "сменилась, сверку цен провести не удалось"}]

    # Карточки «Распродажа» в листинг /dedicated/ не попадают и в скрейп мы их
    # не берём (решение 21.09) — иначе каждая из них тут превращается в мнимое
    # расхождение «на витрине есть, в скрейпе нет».
    for plan_id in _regcloud_clearance_ids(html):
        card_prices.pop(plan_id, None)

    ours = {r.get("plan_id"): r for r in rows if r.get("plan_id")}
    out: list[dict] = []
    for plan_id, card_price in sorted(card_prices.items()):
        row = ours.get(plan_id)
        if row is None:
            out.append({"kind": "missing", "plan_id": plan_id,
                        "site_price": card_price,
                        "detail": "карточка на витрине есть, в скрейпе её нет"})
            continue
        our_price = int(row.get("price_rub") or 0)
        if our_price != card_price:
            out.append({"kind": "price", "plan_id": plan_id,
                        "site_price": card_price, "our_price": our_price,
                        "detail": "цена карточки и цена в отчёте разошлись"})
    for plan_id in sorted(set(ours) - set(card_prices)):
        out.append({"kind": "extra", "plan_id": plan_id,
                    "our_price": int(ours[plan_id].get("price_rub") or 0),
                    "detail": "в отчёте есть, на витрине карточки нет"})
    return out


def diff_timeweb(
    rows: list[dict], presets: list[dict], locations: tuple[str, ...]
) -> list[dict]:
    """Наши тарифы против каталога landing-api (независимо от __NUXT_DATA__).

    locations — коды ДЦ как в payload страницы («ru», «msk»); здесь они
    переводятся в коды landing-api (ru-1, ru-3), чтобы сверка смотрела на тот
    же дата-центр, что и скрейп. Сверяются набор и цена по названию тарифа;
    расхождение = либо витрина поменялась между двумя запросами, либо парсер
    разошёлся с каталогом. Одно и то же имя («E-2236 / 16 / 480») живёт и в
    СПб, и в Москве с разной ценой — без фильтра по ДЦ сверка врала бы.
    """
    want = {TIMEWEB_LOCATIONS.get(loc, loc) for loc in locations}
    site: dict[str, list[int]] = {}
    for p in presets:
        if p.get("location") not in want:
            continue
        name = (p.get("description") or "").strip()
        if not name:
            continue
        site.setdefault(name, []).append(int(p.get("price") or 0))
    if not site:
        return [{"kind": "check_failed",
                 "detail": "каталог не отдал тарифы локации "
                           f"{', '.join(sorted(want))}"}]

    # landing-api отдаёт помесячную цену (= priceNumber); в таблице с 15.09
    # в таблице с 21.09 помесячная цена (= priceNumber = price_list_rub)
    ours: dict[str, list[int]] = {}
    for r in rows:
        name = (r.get("plan_id") or "").strip()
        if name:
            ours.setdefault(name, []).append(
                int(r.get("price_list_rub") or r.get("price_rub") or 0))

    out: list[dict] = []
    for name in sorted(set(site) - set(ours)):
        out.append({"kind": "missing", "plan_id": name,
                    "site_price": sorted(site[name])[0],
                    "detail": "тариф есть в каталоге витрины, в скрейпе нет"})
    for name in sorted(set(ours) - set(site)):
        out.append({"kind": "extra", "plan_id": name,
                    "our_price": sorted(ours[name])[0],
                    "detail": "тариф есть в скрейпе, в каталоге витрины нет"})
    for name in sorted(set(site) & set(ours)):
        if sorted(site[name]) != sorted(ours[name]):
            out.append({"kind": "price", "plan_id": name,
                        "site_price": sorted(site[name])[0],
                        "our_price": sorted(ours[name])[0],
                        "detail": "цена тарифа в каталоге и в отчёте разошлись"})
    return out


def fetch_timeweb_presets(timeout: int = 25) -> list[dict]:
    """Каталог timeweb.cloud вторым путём. Пустой список — проверка не удалась."""
    import requests

    try:
        r = requests.get(
            TIMEWEB_PRESETS_URL, timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                   "Chrome/124.0.0.0 Safari/537.36"},
        )
        if r.status_code != 200:
            log.warning("[timeweb] каталог витрины: HTTP %s", r.status_code)
            return []
        return r.json().get("dedicated_servers_presets") or []
    except Exception as e:
        log.warning("[timeweb] каталог витрины недоступен: %s", e)
        return []


def split_by_report(
    discrepancies: list[dict], matched_plans: set[str]
) -> tuple[list[dict], list[dict]]:
    """(влияют на отчёт, вне отчёта).

    Фидбек Светланы 11.09: из пяти строк плашки три были про карточки, которые
    не совпали ни с одной конфигурацией Мирана, — для неё это шум. Цена или
    лишний тариф важны, только если план попал в матчи. Пропавшую из скрейпа
    карточку оставляем всегда: не разобрав её, мы не знаем, совпала бы она.
    """
    relevant, rest = [], []
    for d in discrepancies:
        if d.get("kind") == "missing" or d.get("plan_id") in matched_plans:
            relevant.append(d)
        else:
            rest.append(d)
    return relevant, rest


def card_url(competitor_id: str, plan_id: str) -> str:
    """Страница конкретной карточки — клиент проверяет её сам. Пусто, если нет."""
    m = re.fullmatch(r"RD-(\d+)", plan_id or "")
    if competitor_id == "reg_cloud" and m:
        return f"https://reg.cloud/dedicated/server_details/{m.group(1)}"
    return ""


def check_provider(provider: str, rows: list[dict], html: str = "") -> dict:
    """{'status': ok|clean|failed, 'discrepancies': [...]} по одному конкуренту.

    status=clean — сверка прошла, расхождений нет; ok — расхождения есть;
    failed — сверку выполнить не удалось (её отсутствие само по себе не
    означает, что данные верные).
    """
    if provider == "regcloud":
        if not html:
            return {"status": "failed", "discrepancies": [],
                    "detail": "нет HTML листинга для сверки"}
        diffs = diff_regcloud(rows, html)
    elif provider == "timeweb_cloud":
        presets = fetch_timeweb_presets()
        if not presets:
            return {"status": "failed", "discrepancies": [],
                    "detail": "каталог витрины недоступен"}
        _, locations = timeweb_cloud_source()
        diffs = diff_timeweb(rows, presets, locations)
    else:
        return {"status": "failed", "discrepancies": [],
                "detail": "сверка для этого конкурента ещё не реализована"}

    failed = [d for d in diffs if d["kind"] == "check_failed"]
    if failed:
        return {"status": "failed", "discrepancies": [],
                "detail": failed[0]["detail"]}
    return {"status": "ok" if diffs else "clean", "discrepancies": diffs}
