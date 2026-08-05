# Streamlit dashboard for dedicated server price comparison
# All UI text in Russian (Cyrillic)

import io

import streamlit as st
import pandas as pd
import os
from datetime import date
from pathlib import Path

from dedicated_scraper import scrape_all, save_history
from pivot_utils import PROVIDERS, build_pivot


# ── Constants ────────────────────────────────────────────────────────

DATA_DIR = "data"
HISTORY_CSV = os.path.join(DATA_DIR, "history.csv")
REPORTS_DIR = Path(DATA_DIR) / "reports"


# ── Data loading ─────────────────────────────────────────────────────

@st.cache_data(ttl=60)
def load_history() -> pd.DataFrame:
    """Load history.csv with proper numeric types."""
    if not os.path.exists(HISTORY_CSV):
        return pd.DataFrame()
    df = pd.read_csv(HISTORY_CSV)
    for col in ["ram_gb", "disk_count", "disk_size_gb"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    return df


# ── Helper functions ─────────────────────────────────────────────────



def style_pivot(pivot: pd.DataFrame):
    """Style pivot table with color coding."""
    price_cols = [c for c in PROVIDERS if c in pivot.columns]

    def highlight_row(row):
        """Highlight min green, max red, NaN grey."""
        values = row[price_cols]
        valid = values.dropna()
        if len(valid) == 0:
            # All NaN
            return ["color: #9e9e9e;" for _ in price_cols]

        min_val = valid.min()
        max_val = valid.max()

        styles = []
        for v in values:
            if pd.isna(v):
                styles.append("color: #9e9e9e;")
            elif v == min_val:
                styles.append("background-color: #c8e6c9; color: #1b5e20;")
            elif v == max_val:
                styles.append("background-color: #ffcdd2; color: #b71c1c;")
            else:
                styles.append("")
        return styles

    styled = pivot[price_cols].style.apply(highlight_row, axis=1, subset=price_cols)
    styled = styled.format(na_rep="—", precision=0, thousands=" ", decimal=",")
    return styled


# ── Page config ──────────────────────────────────────────────────────

st.set_page_config(
    page_title="Дедики — сравнение цен",
    page_icon="🖥️",
    layout="wide",
)


# ── Sidebar ──────────────────────────────────────────────────────────

with st.sidebar:
    st.title("🖥️ Дедики")
    st.divider()

    # Update button
    if st.button("🔄 Обновить данные", type="primary", use_container_width=True):
        with st.spinner("Скрейпим..."):
            new_df = scrape_all()
            save_history(new_df)
            st.cache_data.clear()
        st.success(f"Готово — {len(new_df)} конфигов")
        st.rerun()

    if st.button("📊 Записать в Excel", use_container_width=True):
        from excel_export import export_to_excel
        df_all_now = load_history()
        if df_all_now.empty:
            st.warning("Нет данных — сначала обнови данные.")
        else:
            latest_now = df_all_now[df_all_now["scraped_at"] == df_all_now["scraped_at"].max()]
            try:
                with st.spinner("Пишем в Excel..."):
                    sheet = export_to_excel(latest_now)
                st.success(f"Лист «{sheet}» добавлен в файл")
            except PermissionError:
                st.error("Файл Excel открыт — закрой его и попробуй снова.")
            except Exception as exc:
                st.error(f"Ошибка: {exc}")

    # Last update info
    df_all = load_history()
    if not df_all.empty:
        last_date = df_all["scraped_at"].max()
        st.caption(f"Последнее обновление: {last_date}")

    st.divider()
    st.subheader("Фильтры")

    # Без истории скрейпов работают только отчёты конкурентов (таб 4)
    if df_all.empty:
        st.info("Истории скрейпов нет — нажми «Обновить данные». "
                "Таб «Конкуренты vs Миран» работает и без неё.")
        latest = df_all
        sel_ram = sel_disk = sel_dtype = sel_gen = sel_prov = []
    else:
        # Get latest snapshot for filters
        latest = df_all[df_all["scraped_at"] == df_all["scraped_at"].max()]

        # Filters
        sel_ram = st.multiselect(
            "RAM (ГБ)",
            sorted(latest["ram_gb"].dropna().unique())
        )
        sel_disk = st.multiselect(
            "Диск (ГБ)",
            sorted(latest["disk_size_gb"].dropna().unique())
        )
        sel_dtype = st.multiselect(
            "Тип диска",
            sorted(latest["disk_type"].dropna().unique())
        )
        sel_gen = st.multiselect(
            "Поколение CPU",
            sorted(latest["cpu_generation"].dropna().unique())
        )
        sel_prov = st.multiselect(
            "Провайдер",
            PROVIDERS
        )


# ── Apply filters ────────────────────────────────────────────────────

df_filtered = latest.copy()
if sel_ram:
    df_filtered = df_filtered[df_filtered["ram_gb"].isin(sel_ram)]
if sel_disk:
    df_filtered = df_filtered[df_filtered["disk_size_gb"].isin(sel_disk)]
if sel_dtype:
    df_filtered = df_filtered[df_filtered["disk_type"].isin(sel_dtype)]
if sel_gen:
    df_filtered = df_filtered[df_filtered["cpu_generation"].isin(sel_gen)]
if sel_prov:
    df_filtered = df_filtered[df_filtered["provider"].isin(sel_prov)]


# ── Tabs ─────────────────────────────────────────────────────────────

tab1, tab2, tab3, tab4 = st.tabs(
    ["Сравнение цен", "Динамика запасов", "Все данные", "Конкуренты vs Миран"]
)


# ── Tab 1: Price comparison ──────────────────────────────────────────

with tab1:
    st.subheader("Сравнение цен по конфигурациям")

    if df_filtered.empty:
        st.info("Нет данных для выбранных фильтров")
    else:
        pivot = build_pivot(df_filtered)
        config_cols = ["CPU", "RAM", "Диск"]
        display_cols = config_cols + [p for p in PROVIDERS if p in pivot.columns]

        # Display pivot with styled prices
        st.dataframe(
            pivot[display_cols],
            use_container_width=True,
            hide_index=True,
            column_config={
                "miran":    "miran.ru (₽)",
                "selectel": "selectel.ru (₽)",
                "1dedic":   "1dedic.ru (₽)",
                "regcloud": "reg.cloud (₽)",
                "hostkey":  "hostkey.ru (₽)",
                "it-lite":  "it-lite.ru (₽)",
                "netrack":  "netrack.ru (₽)",
                "timeweb":  "timeweb.com (₽)",
            }
        )
        st.caption(f"{len(pivot)} конфигураций")

        # Excel download in XLSX format
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            pivot[display_cols].to_excel(
                writer, index=False, sheet_name=f"Scraped_{date.today()}"
            )
        st.download_button(
            "Скачать Excel",
            data=buf.getvalue(),
            file_name=f"dedicated_servers_{date.today()}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


# ── Tab 2: Inventory history ─────────────────────────────────────────

with tab2:
    st.subheader("Динамика запасов")

    # Build config labels for dropdown
    config_options = [] if df_all.empty else (
        df_all.dropna(subset=["cpu_model_norm"])
        .assign(
            label=lambda d: (
                d["cpu_model_norm"] + " | " +
                d["ram_gb"].astype(str) + "ГБ | " +
                d["disk_count"].astype(str) + "×" +
                d["disk_size_gb"].astype(str) + "ГБ " +
                d["disk_type"].fillna("")
            )
        )["label"]
        .drop_duplicates()
        .sort_values()
        .tolist()
    )

    if not config_options:
        st.info("Нет данных")
    else:
        selected_config = st.selectbox("Выбери конфигурацию", config_options)

        # Filter to selected config
        df_config = df_all.copy()
        df_config["label"] = (
            df_config["cpu_model_norm"] + " | " +
            df_config["ram_gb"].astype(str) + "ГБ | " +
            df_config["disk_count"].astype(str) + "×" +
            df_config["disk_size_gb"].astype(str) + "ГБ " +
            df_config["disk_type"].fillna("")
        )
        filtered_config = df_config[df_config["label"] == selected_config]

        if filtered_config["quantity_available"].isna().all():
            st.info("Данные о количестве недоступны для этой конфигурации")
        else:
            chart_data = filtered_config.pivot_table(
                index="scraped_at",
                columns="provider",
                values="quantity_available"
            )
            st.line_chart(chart_data)


# ── Tab 3: All data ──────────────────────────────────────────────────

with tab3:
    st.subheader("Все данные")
    st.dataframe(df_all, use_container_width=True, hide_index=True)

    if not df_all.empty:
        st.download_button(
            "Скачать CSV",
            data=df_all.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
            file_name=f"dedicated_servers_{date.today().isoformat()}.csv",
            mime="text/csv",
        )


# ── Tab 4: Competitors vs Miran reference configs (ТЗ) ──────────────

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


with tab4:
    st.subheader("Цены конкурентов по эталонным конфигурациям Миран")

    col_run, col_info = st.columns([1, 3])
    with col_run:
        if st.button("🔄 Запустить сравнение", type="primary", use_container_width=True):
            with st.spinner("Скрейпим конкурентов и сопоставляем (до минуты)..."):
                code = run_competitor_pipeline()
            st.cache_data.clear()
            if code == 0:
                st.success("Готово")
                st.rerun()
            else:
                st.error("Ни один конкурент не дал данных — см. лог в data/reports/")

    wide_df, long_df, date_tag = load_competitor_reports()

    if wide_df.empty:
        st.info("Отчётов ещё нет — нажми «Запустить сравнение»")
    else:
        pretty_date = f"{date_tag[6:8]}.{date_tag[4:6]}.{date_tag[:4]}" if len(date_tag) == 8 else date_tag
        with col_info:
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
