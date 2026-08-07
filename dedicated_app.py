# Streamlit-интерфейс отчёта по ТЗ Миран: эталонные конфигурации vs цены конкурентов.
# Легаси-дашборд (сравнение 8 провайдеров, динамика запасов) удалён 2026-08-07 —
# при необходимости он в git-истории (до коммита с этой строкой).
# All UI text in Russian (Cyrillic)

import streamlit as st
import pandas as pd
from pathlib import Path


# ── Constants ────────────────────────────────────────────────────────

DATA_DIR = "data"
REPORTS_DIR = Path(DATA_DIR) / "reports"


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
    long_path = REPORTS_DIR / f"matches_{date_tag}.csv"
    long = pd.read_csv(long_path) if long_path.exists() else pd.DataFrame()
    return wide, long, date_tag


def run_competitor_pipeline() -> int:
    """Живой прогон matching-пайплайна (тот же код, что CLI)."""
    import argparse
    from competitor_pipeline import run as pipeline_run
    from competitor_report import DEFAULT_REPORTS_DIR
    from config_loader import (
        COMPETITORS_JSON, CPU_SPECS_JSON, MATCHING_JSON, MIRAN_CONFIGS_JSON,
    )
    args = argparse.Namespace(
        no_scrape=False, xlsx=True, out_dir=DEFAULT_REPORTS_DIR,
        configs=MIRAN_CONFIGS_JSON, competitors=COMPETITORS_JSON,
        matching=MATCHING_JSON, cpu_specs=CPU_SPECS_JSON,
    )
    args.out_dir.mkdir(parents=True, exist_ok=True)
    return pipeline_run(args)


def style_competitor_wide(df: pd.DataFrame, price_cols: list[str]):
    """Мин. цена конкурента в строке — зелёным, отсутствие — серым."""
    def highlight_row(row):
        valid = row.dropna()
        min_val = valid.min() if len(valid) else None
        styles = []
        for v in row:
            if pd.isna(v):
                styles.append("color: #9e9e9e;")
            elif v == min_val:
                styles.append("background-color: #c8e6c9; color: #1b5e20;")
            else:
                styles.append("")
        return styles

    styled = df.style.apply(highlight_row, axis=1, subset=price_cols)
    return styled.format(na_rep="—", precision=0, thousands=" ", subset=price_cols)


# ── Page ─────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Миран — цены конкурентов",
    page_icon="🖥️",
    layout="wide",
)

with st.sidebar:
    st.title("🖥️ Миран")
    st.caption("Сравнение цен конкурентов с эталонными конфигурациями")
    st.divider()
    if st.button("🔄 Запустить сравнение", type="primary", use_container_width=True):
        with st.spinner("Скрейпим конкурентов и сопоставляем (до минуты)..."):
            code = run_competitor_pipeline()
        st.cache_data.clear()
        if code == 0:
            st.success("Готово")
            st.rerun()
        else:
            st.error("Ни один конкурент не дал данных — см. лог в data/reports/")

st.subheader("Цены конкурентов по эталонным конфигурациям Миран")

wide_df, long_df, date_tag = load_competitor_reports()

if wide_df.empty:
    st.info("Отчётов ещё нет — нажми «Запустить сравнение»")
else:
    pretty_date = f"{date_tag[6:8]}.{date_tag[4:6]}.{date_tag[:4]}" if len(date_tag) == 8 else date_tag
    st.caption(f"Отчёт от {pretty_date} · конфигураций: {len(wide_df)}")

    price_cols = [c for c in wide_df.columns if c.endswith("_price")]
    matched_mask = wide_df[price_cols].notna().any(axis=1)

    only_matched = st.checkbox(
        f"Только с совпадениями ({int(matched_mask.sum())} из {len(wide_df)})",
        value=True,
    )
    view = wide_df[matched_mask] if only_matched else wide_df

    # Компактный вид: эталон + цена/тариф/наличие по каждому конкуренту
    base_cols = ["config_id", "cpu_model", "cpu_sockets",
                 "cpu_cores_per_socket", "ram_gb", "disks"]
    detail_cols = []
    for c in price_cols:
        cid = c[:-len("_price")]
        detail_cols += [c] + [
            col for col in (f"{cid}_plan_id", f"{cid}_stock_count", f"{cid}_match_count")
            if col in view.columns
        ]
    show = view[[c for c in base_cols if c in view.columns] + detail_cols]

    col_config = {
        "config_id": "ID конфига",
        "cpu_model": "CPU",
        "cpu_sockets": "Сокетов",
        "cpu_cores_per_socket": "Ядер/сокет",
        "ram_gb": "RAM (ГБ)",
        "disks": "Диски",
    }
    for c in price_cols:
        cid = c[:-len("_price")]
        col_config[c] = f"{cid}: ₽/мес"
        col_config[f"{cid}_plan_id"] = f"{cid}: тариф"
        col_config[f"{cid}_stock_count"] = f"{cid}: в наличии"
        col_config[f"{cid}_match_count"] = f"{cid}: матчей"

    st.dataframe(
        style_competitor_wide(show, price_cols),
        use_container_width=True,
        hide_index=True,
        column_config=col_config,
    )

    # Downloads: готовые файлы отчёта как есть
    dl1, dl2, dl3 = st.columns(3)
    wide_path = _latest_report("dedicated_competitors_*.csv")
    xlsx_path = REPORTS_DIR / f"dedicated_competitors_{date_tag}.xlsx"
    long_path = REPORTS_DIR / f"matches_{date_tag}.csv"
    with dl1:
        st.download_button(
            "Скачать CSV (широкий)", data=wide_path.read_bytes(),
            file_name=wide_path.name, mime="text/csv",
        )
    if xlsx_path.exists():
        with dl2:
            st.download_button(
                "Скачать XLSX", data=xlsx_path.read_bytes(),
                file_name=xlsx_path.name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
    if long_path.exists():
        with dl3:
            st.download_button(
                "Скачать CSV (все матчи)", data=long_path.read_bytes(),
                file_name=long_path.name, mime="text/csv",
            )

    # Детализация: все матчи выбранной конфигурации
    if not long_df.empty:
        st.divider()
        st.subheader("Все совпадения по конфигурации")
        matched_ids = long_df["config_id"].drop_duplicates().tolist()
        sel_config = st.selectbox("Конфигурация", matched_ids)
        detail = long_df[long_df["config_id"] == sel_config].sort_values("price_value")
        st.dataframe(
            detail,
            use_container_width=True,
            hide_index=True,
            column_config={
                "config_id": "ID конфига",
                "competitor_id": "Конкурент",
                "plan_id": "Тариф",
                "cpu_model": "CPU",
                "cpu_sockets": "Сокетов",
                "cpu_cores_total": "Ядер всего",
                "ram_gb": "RAM (ГБ)",
                "disks": "Диски",
                "price_value": "Цена",
                "currency": "Валюта",
                "price_period": "Период",
                "stock_count": "В наличии",
                "match_score": "Score",
            },
        )
