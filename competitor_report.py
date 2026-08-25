# Формирование отчётов matching-пайплайна:
#   matches_<YYYYMMDD>.csv              — длинный: все подходящие офферы
#   dedicated_competitors_<YYYYMMDD>.csv/.xlsx — широкий: строка на config_id,
#     ячейки конкурента = самый дешёвый из прошедших матчей (все уже в допусках,
#     для анализа цен значим минимум; полный диапазон — в длинном файле).

import logging
from pathlib import Path

import pandas as pd
from openpyxl.styles import Font

from config_loader import Competitor, ReferenceConfig
from matching import MatchResult

log = logging.getLogger("competitor_report")

DEFAULT_REPORTS_DIR = Path("data") / "reports"

COMPETITOR_COL_SUFFIXES = [
    "price", "currency", "price_period", "plan_id",
    "match_score", "stock_count", "match_count",
]


def format_disk_pools(pools) -> str:
    """[{SSD,2,480},{HDD,2,4000}] → '2×480 ГБ SSD + 2×4000 ГБ HDD'."""
    parts = []
    for pool in pools:
        if isinstance(pool, dict):
            t, c, s = pool["disk_type"], pool["disk_count"], pool["disk_size_gb"]
        else:
            t, c, s = pool.disk_type, pool.disk_count, pool.disk_size_gb
        parts.append(f"{c}×{s} ГБ {t}")
    return " + ".join(parts)


def build_long_df(matches: dict[str, list[MatchResult]]) -> pd.DataFrame:
    """Длинная таблица: строка на (config_id, конкурент, оффер)."""
    rows = []
    for config_id, results in matches.items():
        for m in results:
            o = m.offer
            rows.append({
                "config_id": config_id,
                "competitor_id": o.competitor_id,
                "plan_id": o.plan_id,
                "cpu_model": o.cpu_model,
                "cpu_sockets": o.cpu_sockets,
                "cpu_cores_total": o.cpu_cores_total,
                "ram_gb": o.ram_gb,
                "disks": format_disk_pools(o.disk_pools),
                "price_value": o.price_value,
                "currency": o.currency,
                "price_period": o.price_period,
                "stock_count": o.stock_count,
                "match_score": m.match_score,
            })
    return pd.DataFrame(rows)


def build_wide_df(
    refs: list[ReferenceConfig],
    matches: dict[str, list[MatchResult]],
    competitors: list[Competitor],
) -> pd.DataFrame:
    """Широкая таблица ТЗ: строка на config_id, группа колонок на конкурента."""
    rows = []
    for ref in refs:
        row: dict = {
            "config_id": ref.config_id,
            # в отчёте модель как в файле клиента; канон — только для матчинга
            "cpu_model": ref.cpu_display,
            "cpu_sockets": ref.cpu_sockets,
            "cpu_cores_per_socket": ref.cpu_cores_per_socket,
            "ram_gb": ref.ram_gb,
            "disks": format_disk_pools(ref.disk_pools),
            "miran_price": ref.miran_price,
        }
        by_competitor: dict[str, list[MatchResult]] = {}
        for m in matches.get(ref.config_id, []):
            by_competitor.setdefault(m.offer.competitor_id, []).append(m)

        for comp in competitors:
            cid = comp.competitor_id
            found = by_competitor.get(cid, [])
            if found:
                best = found[0]  # match_all сортирует: дешевле → выше score
                row[f"{cid}_price"] = best.offer.price_value
                row[f"{cid}_currency"] = best.offer.currency
                row[f"{cid}_price_period"] = best.offer.price_period
                row[f"{cid}_plan_id"] = best.offer.plan_id
                row[f"{cid}_match_score"] = best.match_score
                row[f"{cid}_stock_count"] = best.offer.stock_count
                row[f"{cid}_match_count"] = len(found)
            else:
                for suffix in COMPETITOR_COL_SUFFIXES:
                    row[f"{cid}_{suffix}"] = None
        rows.append(row)
    return pd.DataFrame(rows)


def write_reports(
    refs: list[ReferenceConfig],
    matches: dict[str, list[MatchResult]],
    competitors: list[Competitor],
    date_tag: str,
    out_dir: Path = DEFAULT_REPORTS_DIR,
    xlsx: bool = False,
) -> dict[str, Path]:
    """Пишет отчёты, возвращает {'long': path, 'wide': path, ['xlsx': path]}."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    long_df = build_long_df(matches)
    long_path = out_dir / f"matches_{date_tag}.csv"
    long_df.to_csv(long_path, index=False, encoding="utf-8-sig")
    written["long"] = long_path
    log.info("Длинный отчёт: %s (строк: %d)", long_path, len(long_df))

    wide_df = build_wide_df(refs, matches, competitors)
    wide_path = out_dir / f"dedicated_competitors_{date_tag}.csv"
    wide_df.to_csv(wide_path, index=False, encoding="utf-8-sig")
    written["wide"] = wide_path
    log.info("Широкий отчёт: %s (конфигураций: %d)", wide_path, len(wide_df))

    if xlsx:
        xlsx_path = out_dir / f"dedicated_competitors_{date_tag}.xlsx"
        with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
            wide_df.to_excel(writer, sheet_name="Сравнение", index=False)
            ws = writer.sheets["Сравнение"]
            for col_idx, col_name in enumerate(wide_df.columns, start=1):
                letter = ws.cell(row=1, column=col_idx).column_letter
                width = max(len(str(col_name)) + 2, 12)
                ws.column_dimensions[letter].width = min(width, 40)
            for cell in ws[1]:
                cell.font = Font(bold=True)
        written["xlsx"] = xlsx_path
        log.info("XLSX-отчёт: %s", xlsx_path)

    return written
