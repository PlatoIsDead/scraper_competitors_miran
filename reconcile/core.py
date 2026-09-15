# Чистые функции сверки: карточки витрины → сравнение с парами отчёта →
# md/csv. Ни сети, ни файлов — всё приходит параметрами (тесты на снимках).

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from config_loader import CpuSpec, MatchingRules, ReferenceConfig
from dedicated_scraper import (
    _selectel_stock_and_price,
    _selectel_storefront_visible,
    normalize_disk_gb,
    normalize_disk_type,
)
from matching import CompetitorOffer, canonical_cpu, match_offer, within_pct

REGCLOUD_CARD_URL = "https://reg.cloud/dedicated/server_details/{num}"
TIMEWEB_LIST_URL = "https://timeweb.cloud/services/dedicated-server?location=ru"
SELECTEL_LIST_URL = "https://selectel.ru/services/dedicated/"
TIMEWEB_API_LOCATIONS = {"ru": "ru-1", "msk": "ru-3"}


@dataclass
class Card:
    """Карточка конкурента, как её видит посетитель (независимо от парсера)."""
    competitor_id: str
    plan_id: str
    visible: bool
    price: float | None          # цена, показанная на карточке
    cpu_text: str                # «2 × Intel Xeon Gold 5218R»
    ram_gb: int | None
    disks_text: str              # «2 x 480 ГБ SSD, 2 x 4 ТБ HDD»
    url: str
    price_list: float | None = None  # помесячная/без скидки, если известна
    detail: str = ""


@dataclass
class PairCheck:
    config_id: str
    competitor_id: str
    plan_id: str
    our_price: float
    site_price: float | None
    ok: bool
    reasons: list[str] = field(default_factory=list)
    url: str = ""


# ── Карточки витрин ──────────────────────────────────────────────────

def _num(text: str) -> float | None:
    m = re.search(r"(\d[\d\s ]*)", text or "")
    if not m:
        return None
    digits = re.sub(r"[\s ]", "", m.group(1))
    return float(digits) if digits else None


def regcloud_cards(html: str) -> dict[str, Card]:
    """Листинг reg.cloud: код из data-id, цена из data-price (атрибут, а не
    разобранный текст), CPU/RAM/диски — сырой текст блоков карточки."""
    soup = BeautifulSoup(html, "lxml")
    cards: dict[str, Card] = {}
    for item in soup.find_all("div", class_="b-dedicated-servers-list-item-cloud"):
        code = item.get("data-id")
        if not code:
            m = re.search(r"RD-(\d+)", str(item.get("data-server-id") or ""))
            code = m.group(1) if m else None
        if not code:
            continue
        plan_id = f"RD-{code}"
        price = _num(str(item.get("data-price") or ""))
        classes = " ".join(item.get("class") or [])
        detail = "скрыта за «Показать ещё»" if "_display_none" in classes else ""

        def text(cls: str) -> str:
            el = item.find(class_=f"b-dedicated-servers-list-item-cloud__{cls}")
            return el.get_text(" ", strip=True) if el else ""

        ram = _num(text("ram"))
        base = _num(text("base-price"))
        cards[plan_id] = Card(
            competitor_id="reg_cloud", plan_id=plan_id, visible=True,
            price=price, cpu_text=text("cpu-title"),
            ram_gb=int(ram) if ram else None, disks_text=text("hdds"),
            url=REGCLOUD_CARD_URL.format(num=code),
            price_list=base or price, detail=detail,
        )
    return cards


def timeweb_cards(
    presets: list[dict], locations: tuple[str, ...],
    shown_prices: dict[str, float] | None = None,
) -> dict[str, Card]:
    """Каталог landing-api (независимый от __NUXT_DATA__ JSON): помесячная
    цена, CPU, RAM, диски. Цена, показанная на карточке (вкладка «12 мес
    −10 %» по умолчанию), в каталоге отсутствует — берётся из shown_prices
    (поле price страницы по имени тарифа); без неё карточка сверяется по
    помесячной цене с пометкой."""
    want = {TIMEWEB_API_LOCATIONS.get(loc, loc) for loc in locations}
    cards: dict[str, Card] = {}
    for p in presets:
        if p.get("location") not in want:
            continue
        name = (p.get("description") or "").strip()
        if not name:
            continue
        cpu = p.get("cpu") or {}
        mem = p.get("memory") or {}
        disk = p.get("disk") or {}
        # memory.size — суммарный объём в МБ (count — число модулей);
        # cpu.count — ядра, а не сокеты: сокеты в description_short «2 x …»
        ram_mb = mem.get("size") or 0
        cpu_text = str(cpu.get("description_short") or cpu.get("description") or "")
        list_price = float(p.get("price") or 0) or None
        shown = (shown_prices or {}).get(name)
        detail = "" if shown is not None else "цена карточки недоступна, сверка по помесячной"
        cards[name] = Card(
            competitor_id="timeweb", plan_id=name, visible=True,
            price=shown if shown is not None else list_price,
            cpu_text=cpu_text, ram_gb=int(ram_mb / 1024) if ram_mb else None,
            disks_text=str(disk.get("description") or ""),
            url=TIMEWEB_LIST_URL, price_list=list_price, detail=detail,
        )
    return cards


def selectel_cards(
    configs: list[dict], visible_locations: dict[str, str] | None,
) -> dict[str, Card]:
    """Открытый API selectel по локациям витрины: карточка видна по правилу
    сайта (is_preorder || is_order && остаток в msk/spb/nsk), цена — по
    location_price_collection, как считает сайт."""
    cards: dict[str, Card] = {}
    for cfg in configs:
        if not isinstance(cfg, dict) or not cfg.get("name"):
            continue
        visible = _selectel_storefront_visible(cfg, visible_locations)
        stock = _selectel_stock_and_price(cfg, visible_locations)
        cpu = cfg.get("cpu") or {}
        count = cpu.get("count") or 1
        cpu_text = (f"{count} × " if count > 1 else "") + str(cpu.get("name") or "")
        ram = sum((r.get("size") or 0) * (r.get("count") or 1)
                  for r in cfg.get("ram") or [] if isinstance(r, dict))
        disks = ", ".join(
            f"{d.get('count', 1)} x {d.get('size', 0)} ГБ {d.get('type', '')}"
            for d in cfg.get("disk") or [] if isinstance(d, dict))
        by_loc = stock.get("stock_by_location")
        if visible_locations is None:
            detail = ("локации витрины недоступны — наличие по всем ДЦ: "
                      f"{stock.get('quantity') or 0}")
        elif by_loc:
            detail = "в наличии: " + ", ".join(f"{k} ×{v}" for k, v in by_loc.items())
        else:
            detail = "предзаказ" if cfg.get("is_preorder") else "нет в наличии в msk/spb/nsk"
        cards[str(cfg["name"])] = Card(
            competitor_id="selectel", plan_id=str(cfg["name"]),
            visible=visible, price=stock.get("price_rub"),
            cpu_text=cpu_text, ram_gb=int(ram) if ram else None,
            disks_text=disks, url=SELECTEL_LIST_URL,
            price_list=stock.get("price_rub"), detail=detail,
        )
    return cards


def selectel_precustom_cards(cfgs: list[dict]) -> dict[str, Card]:
    """Линейка PCL* («Configurable Pre-Build»): её нет в service/server, сайт
    собирает карточку из calculator/precustom + items. cfgs — результат
    dedicated_scraper._precustom_to_cfg (None = сайт конфиг не показывает)."""
    cards: dict[str, Card] = {}
    for cfg in cfgs:
        if not isinstance(cfg, dict) or not cfg.get("name"):
            continue
        cpu = cfg.get("cpu") or {}
        count = cpu.get("count") or 1
        cpu_text = (f"{count} × " if count > 1 else "") + str(cpu.get("name") or "")
        ram = sum((r.get("size") or 0) * (r.get("count") or 1)
                  for r in cfg.get("ram") or [] if isinstance(r, dict))
        disks = ", ".join(
            f"{d.get('count', 1)} x {d.get('size', 0)} ГБ {d.get('type', '')}"
            for d in cfg.get("disk") or [] if isinstance(d, dict))
        price = ((cfg.get("price_collection") or {}).get("RUB") or {}).get("month")
        qty = cfg.get("quantity") or 0
        cards[str(cfg["name"])] = Card(
            competitor_id="selectel", plan_id=str(cfg["name"]), visible=qty > 0,
            price=float(price) if price else None, cpu_text=cpu_text,
            ram_gb=int(ram) if ram else None, disks_text=disks,
            url=SELECTEL_LIST_URL, price_list=float(price) if price else None,
            detail=f"сборка из компонентов (PCL), доступно {qty}",
        )
    return cards


# ── Сравнение пары с карточкой ───────────────────────────────────────

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def disk_tokens(text: str, size_classes: dict[int, int] | None = None) -> list[tuple]:
    """«2×480 ГБ SSD + 2×4000 ГБ HDD» / «2 x 4 ТБ HDD» → [(2, класс, тип)]."""
    out = []
    for m in re.finditer(
        r"(?:(\d+)\s*[хxX×]\s*)?(\d+(?:[.,]\d+)?)\s*(ГБ|ТБ|GB|TB)"
        r"((?:\s*(?:NVMe|SSD|HDD|SATA|U\.2|M\.2))*)", text or "", re.I,
    ):
        size = float(m.group(2).replace(",", "."))
        if m.group(3).upper() in ("ТБ", "TB"):
            size *= 1000
        gb = normalize_disk_gb(int(size))
        if size_classes:
            gb = size_classes.get(gb, gb)
        out.append((int(m.group(1) or 1), gb, normalize_disk_type(m.group(4) or "")))
    return sorted(out)


def _sockets_of(cpu_text: str) -> int:
    m = re.match(r"^\s*(\d+)\s*[×xX]\s*", cpu_text or "")
    return int(m.group(1)) if m else 1


def compare_pair(
    row: dict, card: Card | None, size_classes: dict[int, int] | None = None,
) -> PairCheck:
    """row — строка matches_<дата>.csv (config_id, competitor_id, plan_id,
    cpu_model, cpu_sockets, ram_gb, disks, price_value)."""
    our_price = float(row.get("price_value") or 0)
    check = PairCheck(
        config_id=str(row.get("config_id")), competitor_id=str(row.get("competitor_id")),
        plan_id=str(row.get("plan_id")), our_price=our_price, site_price=None,
        ok=True, url=card.url if card else "",
    )
    if card is None:
        check.ok = False
        check.reasons.append("карточки нет на витрине")
        return check
    check.site_price = card.price
    if not card.visible:
        check.ok = False
        check.reasons.append("карточка скрыта: " + (card.detail or "нет в наличии"))
    if card.price is None or int(round(card.price)) != int(round(our_price)):
        check.ok = False
        check.reasons.append(f"цена на карточке {_fmt(card.price)}, "
                             f"в отчёте {_fmt(our_price)}")
    our_cpu = _norm(str(row.get("cpu_model")))
    if our_cpu and our_cpu not in _norm(card.cpu_text):
        check.ok = False
        check.reasons.append(f"CPU на карточке «{card.cpu_text}»")
    try:
        our_sockets = int(float(row.get("cpu_sockets") or 1))
    except (TypeError, ValueError):
        our_sockets = 1
    if _sockets_of(card.cpu_text) != our_sockets:
        check.ok = False
        check.reasons.append(f"процессоров на карточке {_sockets_of(card.cpu_text)}, "
                             f"в отчёте {our_sockets}")
    try:
        our_ram = int(float(row.get("ram_gb") or 0))
    except (TypeError, ValueError):
        our_ram = 0
    if card.ram_gb is not None and card.ram_gb != our_ram:
        check.ok = False
        check.reasons.append(f"RAM на карточке {card.ram_gb} ГБ, в отчёте {our_ram}")
    ours_disks = disk_tokens(str(row.get("disks") or ""), size_classes)
    site_disks = disk_tokens(card.disks_text, size_classes)
    if site_disks and ours_disks != site_disks:
        check.ok = False
        check.reasons.append(f"диски на карточке «{card.disks_text}»")
    if check.ok and card.detail:
        check.reasons.append(card.detail)
    return check


def check_pairs(
    rows: list[dict], cards_by_competitor: dict[str, dict[str, Card] | None],
    size_classes: dict[int, int] | None = None,
) -> list[PairCheck]:
    """Все пары отчёта против карточек. Конкурент без карточек (None) —
    его пары помечаются «витрина недоступна» и считаются ✗."""
    out = []
    for row in rows:
        cid = str(row.get("competitor_id"))
        cards = cards_by_competitor.get(cid)
        if cards is None:
            check = compare_pair(row, None, size_classes)
            check.reasons = ["витрина недоступна — сверка не выполнена"]
            out.append(check)
            continue
        out.append(compare_pair(row, cards.get(str(row.get("plan_id"))), size_classes))
    return out


# ── Кандидаты: совпал CPU, но пары нет ───────────────────────────────

def reject_reason(
    ref: ReferenceConfig, offer: CompetitorOffer, rules: MatchingRules,
    specs: dict[str, CpuSpec], size_classes: dict[int, int] | None,
) -> str | None:
    """Почему оффер с тем же CPU не стал парой (None — стал бы)."""
    if match_offer(ref, offer, rules, specs, size_classes):
        return None
    if offer.cpu_sockets != ref.cpu_sockets:
        return f"процессоров {offer.cpu_sockets}, у Мирана {ref.cpu_sockets}"
    if offer.cpu_cores_total and not within_pct(
            ref.cpu_cores_total, offer.cpu_cores_total, rules.cores_tolerance_pct):
        return f"ядер {offer.cpu_cores_total}, у Мирана {ref.cpu_cores_total}"
    if not within_pct(ref.ram_gb, offer.ram_gb, rules.ram_tolerance_pct):
        return f"RAM {offer.ram_gb} ГБ, у Мирана {ref.ram_gb}"
    from competitor_report import format_disk_pools
    return f"диски {format_disk_pools(offer.disk_pools)}"


def unmatched_candidates(
    refs: list[ReferenceConfig], offers: list[CompetitorOffer],
    matched: set[tuple[str, str, str]], rules: MatchingRules,
    specs: dict[str, CpuSpec], size_classes: dict[int, int] | None,
) -> list[dict]:
    """Карточки конкурентов с тем же каноном CPU, что у эталона, но без пары.
    matched — {(config_id, competitor_id, plan_id)} из отчёта."""
    out = []
    for ref in refs:
        ref_key = canonical_cpu(ref.cpu_model, specs)
        for offer in offers:
            if (ref.config_id, offer.competitor_id, offer.plan_id) in matched:
                continue
            if canonical_cpu(offer.cpu_model, specs) != ref_key:
                continue
            reason = reject_reason(ref, offer, rules, specs, size_classes)
            out.append({
                "config_id": ref.config_id, "competitor_id": offer.competitor_id,
                "plan_id": offer.plan_id, "price_value": offer.price_value,
                "reason": reason or "прошёл бы гейты — отчёт устарел?",
            })
    return out


# ── Вывод ────────────────────────────────────────────────────────────

def _fmt(v) -> str:
    return "—" if v is None else f"{int(round(v)):,}".replace(",", " ")


def render_md(
    date_tag: str, checks: list[PairCheck], candidates: list[dict],
    failed_sources: dict[str, str], labels: dict[str, str] | None = None,
) -> str:
    labels = labels or {}
    lab = lambda cid: labels.get(cid, cid)  # noqa: E731
    bad = [c for c in checks if not c.ok]
    lines = [f"# Сверка с витринами — {date_tag[6:8]}.{date_tag[4:6]}.{date_tag[:4]}", ""]
    lines.append(f"Пар в отчёте: {len(checks)}, расхождений: {len(bad)}.")
    for cid, why in failed_sources.items():
        lines.append(f"- ⚠ {lab(cid)}: витрина недоступна ({why}) — пары не сверены")
    lines += ["", "| Конфигурация | Конкурент | Тариф | В отчёте | На карточке | Итог | Причина |",
              "|---|---|---|---|---|---|---|"]
    for c in checks:
        plan = f"[{c.plan_id}]({c.url})" if c.url else c.plan_id
        lines.append(f"| {c.config_id} | {lab(c.competitor_id)} | {plan} | {_fmt(c.our_price)} | "
                     f"{_fmt(c.site_price)} | {'✓' if c.ok else '✗'} | {'; '.join(c.reasons)} |")
    lines += ["", "## Совпал CPU, но пары нет", ""]
    if not candidates:
        lines.append("Нет таких карточек.")
    else:
        lines += ["| Конфигурация | Конкурент | Тариф | Цена | Почему не пара |", "|---|---|---|---|---|"]
        for k in candidates:
            lines.append(f"| {k['config_id']} | {lab(k['competitor_id'])} | {k['plan_id']} | "
                         f"{_fmt(k['price_value'])} | {k['reason']} |")
    lines += ["", "✗ — карточка не найдена/скрыта, либо цена, CPU, RAM или диски на "
              "карточке отличаются от строки отчёта; ссылка ведёт на карточку конкурента."]
    return "\n".join(lines) + "\n"


def checks_to_rows(checks: list[PairCheck]) -> list[dict]:
    return [{
        "config_id": c.config_id, "competitor_id": c.competitor_id, "plan_id": c.plan_id,
        "our_price": c.our_price, "site_price": c.site_price,
        "result": "ok" if c.ok else "mismatch", "reason": "; ".join(c.reasons), "url": c.url,
    } for c in checks]
