# Streamlit-интерфейс отчёта по ТЗ Миран: эталонные конфигурации vs цены конкурентов.
# Тёмная тема по UI-спеке 2026-08: палитра #00232F/#00344B, акценты лайм/тил,
# сайдбар = панель управления, KPI-карточки, компактная таблица сравнения,
# карточки матчей вместо широкой таблицы, сырой скрейп в экспандере.
# All UI text in Russian (Cyrillic)

import json
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

# ── Constants ────────────────────────────────────────────────────────

DATA_DIR = "data"
REPORTS_DIR = Path(DATA_DIR) / "reports"

COMP_LABELS = {
    "selectel": "Selectel",
    "reg_cloud": "Reg.cloud",
    "regcloud": "Reg.cloud",
    "timeweb": "Timeweb",
    "timeweb_cloud": "Timeweb",
}

# ── Палитра (тёмная тема из спеки, §8) ───────────────────────────────

FONT_LINK = (
    '<link href="https://fonts.googleapis.com/css2'
    '?family=Inter:wght@300;400;600;700;900&display=swap" rel="stylesheet">'
)

CSS = """
html, body, [class*="css"], [data-testid="stAppViewContainer"] {
  font-family: Inter, system-ui, sans-serif;
}
.block-container { padding: 28px 32px 48px; max-width: 1440px; }
h1, h2, h3 { color: #fff; }
section[data-testid="stSidebar"] {
  background: #001d28; border-right: 1px solid rgba(255,255,255,.14);
}
hr { border-color: rgba(255,255,255,.14); }
a { color: #0079C5; } a:hover { color: #8fd0f5; }

.stButton > button {
  background: #BEDC3C; color: #00344b; font-weight: 700;
  border: 0; border-radius: 6px;
}
.stButton > button:hover { background: #9cb62d; color: #00232f; }
.stDownloadButton > button {
  background: transparent; color: #fff; border: 1px solid rgba(255,255,255,.25);
  border-radius: 6px; font-weight: 600; font-size: 13px;
}
.stDownloadButton > button:hover { border-color: #BEDC3C; color: #BEDC3C; }
.stMultiSelect span[data-baseweb="tag"] {
  background: rgba(0,121,197,.3) !important; color: #dbeefc !important;
  border-radius: 999px;
}

/* ── Шапка ── */
.overline {
  text-transform: uppercase; font-size: 10px; font-weight: 700;
  letter-spacing: .14em; color: #BEDC3C; margin-bottom: 6px;
}
.h1 {
  font-weight: 700; font-size: 30px; line-height: 1.1;
  letter-spacing: -.02em; color: #fff; margin: 0 0 8px;
}
.subline { font-weight: 300; font-size: 14px; color: rgba(255,255,255,.6); }

/* ── KPI-карточки ── */
.kpi-grid {
  display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px;
  margin: 22px 0 6px;
}
.kpi {
  background: #00344b; border: 1px solid rgba(255,255,255,.14);
  border-top: 3px solid #BEDC3C; border-radius: 10px; padding: 16px 18px;
}
.kpi.t2 { border-top-color: #009687; }
.kpi.t3 { border-top-color: #d0432f; }
.kpi.t4 { border-top-color: #0079C5; }
.kpi .lab {
  text-transform: uppercase; letter-spacing: .12em; font-size: 10px;
  font-weight: 700; color: rgba(255,255,255,.55); margin-bottom: 4px;
}
.kpi .val {
  font-weight: 900; font-size: 30px; color: #fff;
  font-variant-numeric: tabular-nums; line-height: 1.15;
}
.kpi .sub { font-size: 11px; color: rgba(255,255,255,.55); margin-top: 2px; }
.kpi .sub.lime { color: #BEDC3C; font-weight: 600; }

/* ── Секции ── */
.sec {
  display: flex; align-items: baseline; justify-content: space-between;
  border-bottom: 1px solid rgba(255,255,255,.14);
  padding-bottom: 10px; margin: 26px 0 14px;
}
.sec h2 { font-weight: 700; font-size: 17px; color: #fff; margin: 0; border: 0; padding: 0; }
.sec .legend { font-size: 12px; color: rgba(255,255,255,.55); }
.sec .legend .sw {
  display: inline-block; width: 10px; height: 10px; border-radius: 2px;
  background: rgba(190,220,60,.35); margin-right: 5px; vertical-align: -1px;
}

/* ── Таблица сравнения ── */
.cmp-wrap {
  border: 1px solid rgba(255,255,255,.14); border-radius: 10px;
  overflow: hidden; background: #00232f;
}
.cmp-scroll { max-height: 620px; overflow: auto; }
table.cmp {
  width: 100%; border-collapse: collapse; font-size: 12.5px;
  font-variant-numeric: tabular-nums;
}
.cmp thead th {
  position: sticky; top: 0; z-index: 1;
  background: #00344b; color: #fff; text-align: left;
  text-transform: uppercase; font-size: 10px; font-weight: 700;
  letter-spacing: .1em; padding: 11px 12px;
  border-bottom: 1px solid rgba(255,255,255,.2);
}
.cmp td { padding: 11px 12px; border-bottom: 1px solid rgba(255,255,255,.07); color: rgba(255,255,255,.78); }
.cmp tbody tr:nth-child(even) td { background: rgba(255,255,255,.03); }
.cmp .num, .cmp thead th.num { text-align: right; }
.cmp .id { font-weight: 700; color: #fff; white-space: nowrap; }
.cmp .spec { color: rgba(255,255,255,.45); }
.cmp td.miran, .cmp th.miran { border-left: 1px solid rgba(255,255,255,.2); }
.cmp td.miran { font-weight: 600; color: #fff; }
.cmp .best { background: rgba(190,220,60,.16) !important; color: #BEDC3C !important; font-weight: 600; }
.cmp .empty { color: rgba(255,255,255,.28); }
.delta-pos { color: #ff8a75; font-weight: 600; }
.delta-neg { color: #BEDC3C; font-weight: 600; }
.cmp-footer {
  background: #00344b; padding: 8px 14px; font-size: 11.5px;
  color: rgba(255,255,255,.55); border-top: 1px solid rgba(255,255,255,.14);
}

/* ── Карточки матчей ── */
.mc-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.match-card {
  background: #00344b; border: 1px solid rgba(255,255,255,.14);
  border-left: 3px solid #9B9B9B; border-radius: 10px; padding: 14px 16px;
}
.match-card.best-offer { border-left-color: #BEDC3C; }
.mc-top { display: flex; justify-content: space-between; align-items: baseline; }
.mc-name { font-weight: 700; font-size: 14px; color: #fff; }
.mc-price { font-weight: 900; font-size: 20px; color: #fff; font-variant-numeric: tabular-nums; white-space: nowrap; }
.mc-spec { font-size: 12px; color: rgba(255,255,255,.6); margin: 6px 0 10px; }
.pill {
  display: inline-block; border-radius: 999px; font-weight: 600;
  font-size: 10px; padding: 5px 8px; margin-right: 6px;
  text-transform: uppercase; letter-spacing: .06em;
}
.pill-score { background: rgba(0,150,135,.28); color: #67e8db; }
.pill-stock { background: rgba(0,121,197,.28); color: #8fd0f5; }
.pill-na { background: rgba(255,255,255,.08); color: rgba(255,255,255,.5); }

/* ── Сайдбар ── */
.sb-logo { display: flex; align-items: center; gap: 10px; margin-bottom: 2px; }
.sb-logo .mark {
  width: 26px; height: 26px; border-radius: 7px;
  background: linear-gradient(120deg, #BEDC3C, #009687);
}
.sb-logo .name { font-weight: 900; font-size: 19px; color: #fff; }
.sb-tagline { font-size: 12px; color: rgba(255,255,255,.5); margin-bottom: 18px; }
.sb-label {
  text-transform: uppercase; font-size: 10px; font-weight: 700;
  letter-spacing: .14em; color: rgba(255,255,255,.5); margin: 14px 0 8px;
}
.sb-card {
  background: rgba(255,255,255,.04); border: 1px solid rgba(255,255,255,.14);
  border-radius: 10px; padding: 12px 14px; font-size: 12.5px;
  color: rgba(255,255,255,.78);
}
.sb-card .row { margin: 2px 0; }
.sb-card .dot {
  display: inline-block; width: 7px; height: 7px; border-radius: 50%;
  background: #009687; margin-right: 6px; vertical-align: 1px;
}
"""


# ── Data loading ─────────────────────────────────────────────────────

def _latest_report(pattern: str) -> "Path | None":
    files = sorted(REPORTS_DIR.glob(pattern)) if REPORTS_DIR.exists() else []
    return files[-1] if files else None


@st.cache_data(ttl=60)
def load_competitor_reports() -> tuple[pd.DataFrame, pd.DataFrame, str]:
    """Свежайшая пара отчётов пайплайна: (широкий, длинный, дата-тег)."""
    wide_path = _latest_report("dedicated_competitors_*.csv")
    if wide_path is None:
        return pd.DataFrame(), pd.DataFrame(), ""
    date_tag = wide_path.stem.rsplit("_", 1)[-1]
    wide = pd.read_csv(wide_path)
    if "miran_price" not in wide.columns:
        wide["miran_price"] = pd.NA
    long_path = REPORTS_DIR / f"matches_{date_tag}.csv"
    long = pd.read_csv(long_path) if long_path.exists() else pd.DataFrame()
    return wide, long, date_tag


@st.cache_data(ttl=60)
def load_all_offers() -> pd.DataFrame:
    """Все предложения конкурентов из свежайших raw-JSON скрейпа —
    целиком, без фильтра matching."""
    from competitor_report import format_disk_pools

    rows_out = []
    for provider in ("selectel", "regcloud", "timeweb_cloud"):
        files = sorted(Path(DATA_DIR).glob(f"{provider}_2*.json"))
        if not files:
            continue
        for r in json.loads(files[-1].read_text(encoding="utf-8")):
            pools = r.get("disk_pools") or [{
                "disk_type": r.get("disk_type"),
                "disk_count": r.get("disk_count"),
                "disk_size_gb": r.get("disk_size_gb"),
            }]
            rows_out.append({
                "provider": provider,
                "plan_id": r.get("plan_id") or "",
                "cpu_model": r.get("cpu_model"),
                "cpu_sockets": r.get("cpu_sockets"),
                "cpu_cores_total": r.get("cpu_cores_total"),
                "ram_gb": r.get("ram_gb"),
                "disks": format_disk_pools(pools),
                "price_rub": r.get("price_rub"),
                "quantity_available": r.get("quantity_available"),
                "scraped_at": r.get("scraped_at"),
            })
    return pd.DataFrame(rows_out)


@st.cache_data(ttl=60)
def scrape_status() -> tuple[str, int]:
    """(строка «когда скрейпили», число источников) по свежайшим raw-JSON."""
    latest_mtime = None
    sources = 0
    for provider in ("selectel", "regcloud", "timeweb_cloud"):
        files = sorted(Path(DATA_DIR).glob(f"{provider}_2*.json"))
        if not files:
            continue
        sources += 1
        mtime = files[-1].stat().st_mtime
        if latest_mtime is None or mtime > latest_mtime:
            latest_mtime = mtime
    if latest_mtime is None:
        return "нет данных", 0
    dt = datetime.fromtimestamp(latest_mtime)
    if dt.date() == date.today():
        return f"сегодня {dt:%H:%M}", sources
    return f"{dt:%d.%m.%Y %H:%M}", sources


def apply_parser_upload(uploaded) -> tuple[int, int, list[str]]:
    """Загруженный Parser.xlsx → config/miran_configs.json + disk_classes.json.

    Сначала полный разбор во временном файле, запись конфигов — только при
    успехе обоих листов. Возвращает (конфигов, групп дисков, предупреждения).
    """
    import logging

    from excel_to_configs import (
        DEFAULT_CLASSES_OUT, DEFAULT_OUT,
        convert, convert_disk_classes, load_cpu_aliases,
    )

    tmp_path = Path(DATA_DIR) / "Parser_upload.xlsx"
    tmp_path.write_bytes(uploaded.getbuffer())

    warnings: list[str] = []

    class _Collect(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.WARNING:
                warnings.append(record.getMessage())

    log = logging.getLogger("excel_to_configs")
    handler = _Collect()
    log.addHandler(handler)
    try:
        result = convert(tmp_path, load_cpu_aliases())
        classes = convert_disk_classes(tmp_path)
    finally:
        log.removeHandler(handler)

    if not result["configs"]:
        raise ValueError("в листе «Данные» не распознано ни одной конфигурации")

    DEFAULT_OUT.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    DEFAULT_CLASSES_OUT.write_text(
        json.dumps(classes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    tmp_path.replace(Path(DATA_DIR) / "Parser.xlsx")
    return len(result["configs"]), len(classes["groups"]), warnings


def run_competitor_pipeline() -> int:
    """Живой прогон matching-пайплайна (тот же код, что CLI)."""
    import argparse
    from competitor_pipeline import run as pipeline_run
    from competitor_report import DEFAULT_REPORTS_DIR
    from config_loader import (
        COMPETITORS_JSON, CPU_SPECS_JSON, DISK_CLASSES_JSON,
        MATCHING_JSON, MIRAN_CONFIGS_JSON,
    )
    args = argparse.Namespace(
        no_scrape=False, xlsx=True, out_dir=DEFAULT_REPORTS_DIR,
        configs=MIRAN_CONFIGS_JSON, competitors=COMPETITORS_JSON,
        matching=MATCHING_JSON, cpu_specs=CPU_SPECS_JSON,
        disk_classes=DISK_CLASSES_JSON,
    )
    args.out_dir.mkdir(parents=True, exist_ok=True)
    return pipeline_run(args)


# ── Formatting helpers (чистые, без Streamlit) ───────────────────────

def fmt_price(v) -> str:
    """1234567.0 → '1 234 567'; NaN → '—'."""
    if v is None or pd.isna(v):
        return "—"
    return f"{v:,.0f}".replace(",", " ")


def fmt_pct(v: float, signed: bool = True) -> str:
    """13.79 → '+13,8%'; −7.42 → '−7,4%'."""
    sign = ""
    if signed:
        sign = "+" if v > 0 else ("−" if v < 0 else "")
    return f"{sign}{abs(v):.1f}".replace(".", ",") + "%"


def cpu_family(model: str) -> str:
    m = (model or "").lower()
    if "silver" in m:
        return "Xeon Silver"
    if "gold" in m:
        return "Xeon Gold"
    if "e3-" in m:
        return "Xeon E3"
    if "e-2" in m:
        return "Xeon E-2xxx"
    if "ryzen" in m:
        return "Ryzen"
    if "epyc" in m:
        return "EPYC"
    return "Прочие"


def config_summary(row) -> str:
    """Строка спеки: '2×12 · 128 ГБ · 2×1000 SSD + 2×2000 SSD'."""
    disks = str(row.get("disks") or "").replace(" ГБ", "")
    return (f"{row['cpu_sockets']}×{row['cpu_cores_per_socket']} · "
            f"{row['ram_gb']} ГБ · {disks}")


def comp_label(cid: str) -> str:
    return COMP_LABELS.get(cid, cid)


def row_delta_pct(miran, comp_values: list) -> "float | None":
    """Δ Мирана к минимуму рынка: (Миран − мин.конкурент) / мин.конкурент."""
    if miran is None or pd.isna(miran) or not comp_values:
        return None
    best = min(comp_values)
    if not best:
        return None
    return (float(miran) - best) / best * 100


def build_comparison_html(view: pd.DataFrame, price_cols: list[str],
                          total_rows: int) -> str:
    """Компактная таблица сравнения (HTML): ID · CPU · Конфигурация ·
    Миран · конкуренты · Δ к мин. Ровно одна лаймовая ячейка на строку."""
    heads = ['<th>ID</th>', '<th>CPU</th>', '<th>Конфигурация</th>',
             '<th class="num miran">Миран</th>']
    heads += [f'<th class="num">{comp_label(c[:-len("_price")])}</th>'
              for c in price_cols]
    heads.append('<th class="num">Δ к мин.</th>')

    body = []
    for _, r in view.iterrows():
        cells = {"miran_price": r.get("miran_price")}
        for c in price_cols:
            cells[c] = r.get(c)
        valid = {k: float(v) for k, v in cells.items() if pd.notna(v)}
        best_col = min(valid, key=valid.get) if valid else None

        comp_vals = [float(r[c]) for c in price_cols if pd.notna(r.get(c))]
        delta = row_delta_pct(r.get("miran_price"), comp_vals)
        if delta is None:
            delta_html = '<span class="empty">—</span>'
        else:
            cls = "delta-pos" if delta > 0 else "delta-neg"
            delta_html = f'<span class="{cls}">{fmt_pct(delta)}</span>'

        tds = [f'<td class="id">{r["config_id"]}</td>',
               f'<td>{r["cpu_model"]}</td>',
               f'<td class="spec">{config_summary(r)}</td>']
        for col in ["miran_price"] + price_cols:
            v = cells[col]
            klass = "num" + (" miran" if col == "miran_price" else "")
            if pd.isna(v):
                tds.append(f'<td class="{klass}"><span class="empty">—</span></td>')
            else:
                extra = " best" if col == best_col else ""
                tds.append(f'<td class="{klass}{extra}">{fmt_price(v)}</td>')
        tds.append(f'<td class="num">{delta_html}</td>')
        body.append("<tr>" + "".join(tds) + "</tr>")

    return (
        '<div class="cmp-wrap"><div class="cmp-scroll"><table class="cmp">'
        f'<thead><tr>{"".join(heads)}</tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table></div>'
        f'<div class="cmp-footer">Показано {len(view)} из {total_rows} строк</div>'
        "</div>"
    )


def build_match_cards_html(detail: pd.DataFrame) -> str:
    """Карточки офферов по выбранной конфигурации; самый дешёвый — лайм."""
    cards = []
    best_price = detail["price_value"].min() if len(detail) else None
    for _, m in detail.iterrows():
        best = " best-offer" if m["price_value"] == best_price else ""
        stock = m.get("stock_count")
        if pd.notna(stock):
            stock_pill = f'<span class="pill pill-stock">В наличии: {int(stock)}</span>'
        else:
            stock_pill = '<span class="pill pill-na">Наличие неизвестно</span>'
        score = f'<span class="pill pill-score">Score {m["match_score"]:g}</span>'
        spec = (f'{m["cpu_model"]} · {int(m["ram_gb"])} ГБ · {m["disks"]}')
        cards.append(
            f'<div class="match-card{best}">'
            f'<div class="mc-top"><span class="mc-name">'
            f'{comp_label(m["competitor_id"])} · {m["plan_id"]}</span>'
            f'<span class="mc-price">{fmt_price(m["price_value"])} ₽</span></div>'
            f'<div class="mc-spec">{spec}</div>'
            f'<div>{score}{stock_pill}</div></div>'
        )
    return f'<div class="mc-grid">{"".join(cards)}</div>'


# ── Page ─────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Миран · сравнение цен",
    page_icon="🖥️",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.markdown(FONT_LINK, unsafe_allow_html=True)
st.markdown(f"<style>{CSS}</style>", unsafe_allow_html=True)

wide_df, long_df, date_tag = load_competitor_reports()
offers_df = load_all_offers()
pretty_date = (f"{date_tag[6:8]}.{date_tag[4:6]}.{date_tag[:4]}"
               if len(date_tag) == 8 else date_tag)

all_price_cols = [c for c in wide_df.columns
                  if c.endswith("_price") and c != "miran_price"]

# ── Sidebar: панель управления ──
with st.sidebar:
    st.markdown(
        '<div class="sb-logo"><div class="mark"></div>'
        '<span class="name">Миран</span></div>'
        '<div class="sb-tagline">Мониторинг цен конкурентов</div>',
        unsafe_allow_html=True,
    )

    scrape_when, n_sources = scrape_status()
    st.markdown('<div class="sb-label">Данные</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="sb-card">'
        f'<div class="row">Отчёт {pretty_date or "—"}</div>'
        f'<div class="row">Скрейп {scrape_when}</div>'
        f'<div class="row"><span class="dot"></span>'
        f'{n_sources} источника доступны</div></div>',
        unsafe_allow_html=True,
    )

    st.markdown('<div class="sb-label">Фильтры</div>', unsafe_allow_html=True)
    comp_ids = [c[:-len("_price")] for c in all_price_cols]
    sel_comp = st.multiselect(
        "Конкуренты", comp_ids, default=comp_ids, format_func=comp_label,
    )
    families = sorted({cpu_family(m) for m in wide_df.get("cpu_model", pd.Series(dtype=str))})
    sel_family = st.selectbox("Семейство CPU", ["Все семейства"] + families)
    only_matched = st.checkbox("Только с совпадениями", value=True)
    hide_empty = st.checkbox("Скрыть пустые столбцы", value=True)

    st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
    if st.button("Запустить сравнение", type="primary", use_container_width=True):
        import time as _time
        with st.spinner("Скрейпим конкурентов и сопоставляем (до минуты)..."):
            _t0 = _time.time()
            code = run_competitor_pipeline()
            st.session_state["last_run_secs"] = _time.time() - _t0
        st.cache_data.clear()
        if code == 0:
            st.rerun()
        else:
            st.error("Ни один конкурент не дал данных — см. лог в data/reports/")
    if st.session_state.get("last_run_secs"):
        st.caption(f"Последний прогон: {st.session_state['last_run_secs']:.0f} с")

    st.divider()
    uploaded = st.file_uploader(
        "Обновить эталон (Parser.xlsx)",
        type=["xlsx"],
        help="Файл с листами «Данные» и «Сопоставление дисков» — "
             "как ведёт Светлана. Заменяет текущие эталонные конфигурации.",
    )
    if uploaded is not None:
        import hashlib
        digest = hashlib.md5(uploaded.getbuffer()).hexdigest()
        if st.session_state.get("parser_digest") != digest:
            try:
                n_cfg, n_groups, warns = apply_parser_upload(uploaded)
            except ValueError as e:
                st.session_state["parser_status"] = ("error", f"Файл не принят: {e}")
            except Exception as e:
                st.session_state["parser_status"] = (
                    "error", f"Не удалось разобрать файл: {e}")
            else:
                st.session_state["parser_status"] = (
                    "success",
                    f"Эталон обновлён: {n_cfg} конфигураций, {n_groups} групп "
                    "дисков. Нажми «Запустить сравнение», чтобы пересчитать отчёт.",
                )
                st.session_state["parser_warnings"] = warns
                st.cache_data.clear()
            st.session_state["parser_digest"] = digest
        status = st.session_state.get("parser_status")
        if status:
            (st.success if status[0] == "success" else st.error)(status[1])
        warns = st.session_state.get("parser_warnings") or []
        if warns and status and status[0] == "success":
            shown = "\n".join(f"• {w}" for w in warns[:10])
            more = f"\n… и ещё {len(warns) - 10}" if len(warns) > 10 else ""
            st.warning(f"Предупреждения разбора:\n\n{shown}{more}")
    st.caption(
        "В облаке обновление живёт до перезапуска приложения; "
        "постоянное — коммитом Parser.xlsx в репозиторий."
    )

# ── Шапка ──
matched_mask_all = (wide_df[all_price_cols].notna().any(axis=1)
                    if all_price_cols else pd.Series(dtype=bool))
head_l, head_r = st.columns([5, 2])
with head_l:
    st.markdown(
        f'<div class="overline">Отчёт по рынку · {pretty_date or "нет данных"}</div>'
        '<div class="h1">Цены конкурентов по эталонным конфигурациям</div>'
        f'<div class="subline">{len(wide_df)} конфигураций · '
        f'{int(matched_mask_all.sum()) if len(wide_df) else 0} с совпадениями · '
        f'{len(offers_df)} предложений конкурентов</div>',
        unsafe_allow_html=True,
    )
with head_r:
    wide_path = _latest_report("dedicated_competitors_*.csv")
    if wide_path is not None:
        xlsx_path = REPORTS_DIR / f"dedicated_competitors_{date_tag}.xlsx"
        long_path = REPORTS_DIR / f"matches_{date_tag}.csv"
        d1, d2, d3 = st.columns(3)
        with d1:
            st.download_button("CSV", data=wide_path.read_bytes(),
                               file_name=wide_path.name, mime="text/csv",
                               use_container_width=True)
        if xlsx_path.exists():
            with d2:
                st.download_button(
                    "XLSX", data=xlsx_path.read_bytes(), file_name=xlsx_path.name,
                    mime="application/vnd.openxmlformats-officedocument"
                         ".spreadsheetml.sheet",
                    use_container_width=True)
        if long_path.exists():
            with d3:
                st.download_button("Матчи", data=long_path.read_bytes(),
                                   file_name=long_path.name, mime="text/csv",
                                   use_container_width=True)

if wide_df.empty:
    st.info("Отчётов ещё нет — нажми «Запустить сравнение» в панели слева")
else:
    # ── KPI ──
    deltas = []
    for _, r in wide_df[matched_mask_all].iterrows():
        comp_vals = [float(r[c]) for c in all_price_cols if pd.notna(r.get(c))]
        d = row_delta_pct(r.get("miran_price"), comp_vals)
        if d is not None:
            deltas.append(d)
    deltas_s = pd.Series(deltas, dtype=float)
    n_matched = int(matched_mask_all.sum())
    n_cheaper = int((deltas_s <= 0).sum())
    n_pricier = int((deltas_s > 0).sum())
    cheaper_pct = (f"{n_cheaper / len(deltas_s) * 100:.0f}%"
                   if len(deltas_s) else "—")
    max_over = (fmt_pct(float(deltas_s.max()))
                if n_pricier else "—")
    median_gap = (fmt_pct(float(deltas_s.median()))
                  if len(deltas_s) else "—")
    st.markdown(
        '<div class="kpi-grid">'
        f'<div class="kpi"><div class="lab">Совпадений</div>'
        f'<div class="val">{n_matched}</div>'
        f'<div class="sub">из {len(wide_df)}</div></div>'
        f'<div class="kpi t2"><div class="lab">Миран дешевле</div>'
        f'<div class="val">{n_cheaper}</div>'
        f'<div class="sub lime">{cheaper_pct}</div></div>'
        f'<div class="kpi t3"><div class="lab">Дороже рынка</div>'
        f'<div class="val">{n_pricier}</div>'
        f'<div class="sub">макс. {max_over}</div></div>'
        f'<div class="kpi t4"><div class="lab">Медианный разрыв</div>'
        f'<div class="val">{median_gap}</div>'
        f'<div class="sub">к минимуму рынка</div></div>'
        "</div>",
        unsafe_allow_html=True,
    )

    # ── Таблица сравнения ──
    st.markdown(
        '<div class="sec"><h2>Сравнение по конфигурациям</h2>'
        '<span class="legend"><span class="sw"></span>'
        'Лаймом отмечена минимальная цена в строке</span></div>',
        unsafe_allow_html=True,
    )
    price_cols = [c for c in all_price_cols
                  if c[:-len("_price")] in sel_comp]
    view = wide_df
    if sel_family != "Все семейства":
        view = view[view["cpu_model"].map(cpu_family) == sel_family]
    if only_matched:
        cols_now = price_cols or all_price_cols
        view = view[view[cols_now].notna().any(axis=1)]
    if hide_empty:
        price_cols = [c for c in price_cols if view[c].notna().any()]
    if view.empty:
        st.info("Под текущие фильтры не попала ни одна конфигурация")
    else:
        st.markdown(build_comparison_html(view, price_cols, len(wide_df)),
                    unsafe_allow_html=True)

    # ── Карточки матчей ──
    if not long_df.empty:
        st.markdown(
            '<div class="sec"><h2>Все совпадения по конфигурации</h2></div>',
            unsafe_allow_html=True,
        )
        matched_ids = long_df["config_id"].drop_duplicates().tolist()
        labels = {}
        for cid in matched_ids:
            ref_row = wide_df[wide_df["config_id"] == cid]
            if len(ref_row):
                r = ref_row.iloc[0]
                labels[cid] = f"{cid} — {r['cpu_model']} · {r['ram_gb']} ГБ"
            else:
                labels[cid] = cid
        sel_config = st.selectbox(
            "Конфигурация", matched_ids, format_func=lambda c: labels[c],
            label_visibility="collapsed",
        )
        detail = (long_df[long_df["config_id"] == sel_config]
                  .sort_values("price_value"))
        st.markdown(build_match_cards_html(detail), unsafe_allow_html=True)

# ── Сырой скрейп ──
if not offers_df.empty:
    raw_view = offers_df[offers_df["provider"].isin(
        [c for c in offers_df["provider"].unique()
         if c in sel_comp or comp_label(c) in [comp_label(x) for x in sel_comp]]
    )] if sel_comp else offers_df
    with st.expander(f"Сырой скрейп · {len(raw_view)} предложений"):
        show_raw = raw_view.copy()
        for col in ("cpu_sockets", "cpu_cores_total", "quantity_available"):
            show_raw[col] = show_raw[col].astype("Int64")
        st.dataframe(
            show_raw,
            use_container_width=True,
            hide_index=True,
            height=420,
            column_config={
                "provider": "Конкурент",
                "plan_id": "Тариф",
                "cpu_model": "CPU",
                "cpu_sockets": "Сокетов",
                "cpu_cores_total": "Ядер всего",
                "ram_gb": "RAM (ГБ)",
                "disks": "Диски",
                "price_rub": st.column_config.NumberColumn(
                    "Цена, ₽/мес", format="%.0f"),
                "quantity_available": "В наличии",
                "scraped_at": "Дата скрейпа",
            },
        )
        st.download_button(
            "Скачать CSV (все предложения)",
            data=raw_view.to_csv(index=False).encode("utf-8-sig"),
            file_name="все_предложения_конкурентов.csv",
            mime="text/csv",
        )
