# Сетевые фетчеры витрин для сверки. Каждый возвращает карточки или None
# (витрина недоступна) — решать, что с этим делать, будет вызывающий код.

import json
import logging

import requests
from bs4 import BeautifulSoup

import dedicated_scraper as ds
from config_loader import timeweb_cloud_source
from storefront_check import fetch_timeweb_presets

from .core import Card, regcloud_cards, selectel_cards, timeweb_cards

log = logging.getLogger("reconcile")


def fetch_regcloud() -> dict[str, Card] | None:
    html = ds._scrape_with_playwright(
        "https://reg.cloud/dedicated/", "regcloud",
        wait_selector=".b-dedicated-servers-list-item-cloud",
    )
    if not html:
        return None
    cards = regcloud_cards(html)
    return cards or None


def timeweb_shown_prices(html: str, locations: tuple[str, ...]) -> dict[str, float]:
    """Имя тарифа → цена, показанная на карточке (поле price __NUXT_DATA__
    как строка «10 764 ₽/мес»), только для нужных ДЦ."""
    soup = BeautifulSoup(html, "lxml")
    el = soup.find(id="__NUXT_DATA__")
    if not el or not el.string:
        return {}
    try:
        flat = json.loads(el.string)
    except Exception:
        return {}
    out: dict[str, float] = {}
    for i, item in enumerate(flat):
        if not isinstance(item, dict) or not {"cpu", "presetId", "storageList"} <= item.keys():
            continue
        try:
            cfg = ds._resolve_nuxt(flat, i)
        except Exception:
            continue
        if cfg.get("location") not in locations:
            continue
        name = ds._strip_timeweb_novelty(str(cfg.get("name") or ""))
        shown, _ = ds._timeweb_shown_price(cfg, float(cfg.get("priceNumber") or 0))
        if name and shown:
            out[name] = shown
    return out


def fetch_timeweb() -> dict[str, Card] | None:
    url, locations = timeweb_cloud_source()
    presets = fetch_timeweb_presets()
    if not presets:
        return None
    shown: dict[str, float] = {}
    try:
        r = ds.make_session(url).get(url, timeout=25)
        if r.status_code == 200:
            shown = timeweb_shown_prices(r.text, locations)
    except Exception as e:
        log.warning("[timeweb] страница витрины недоступна: %s", e)
    cards = timeweb_cards(presets, locations, shown)
    return cards or None


def fetch_selectel() -> dict[str, Card] | None:
    configs = None
    for _ in range(3):
        try:
            r = requests.get(ds.SELECTEL_PUB_API, timeout=40,
                             headers={"User-Agent": ds.HEADERS["User-Agent"]})
            r.raise_for_status()
            configs = r.json().get("result") or []
            break
        except Exception as e:
            log.warning("[selectel] API: %s", e)
    if not configs:
        return None
    visible = ds._fetch_selectel_visible_locations()
    if visible is None:
        log.warning("[selectel] список локаций недоступен — сверка по всем ДЦ")
    return selectel_cards(configs, visible) or None


FETCHERS = {
    "reg_cloud": fetch_regcloud,
    "timeweb": fetch_timeweb,
    "selectel": fetch_selectel,
}
