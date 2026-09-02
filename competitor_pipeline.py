# CLI matching-пайплайна: скрейп 3 конкурентов → нормализация в CompetitorOffer
# → сопоставление с эталоном miran_configs.json → отчёты в data/reports/.
#
#   python competitor_pipeline.py [--no-scrape] [--xlsx] [--out-dir data/reports] [-v]
#
# Ошибка одного конкурента не прерывает прогон — его колонки остаются пустыми.

import argparse
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path

import dedicated_scraper as ds
from competitor_report import DEFAULT_REPORTS_DIR, write_reports
from config_loader import (
    COMPETITORS_JSON,
    CPU_SPECS_JSON,
    DISK_CLASSES_JSON,
    MATCHING_JSON,
    MIRAN_CONFIGS_JSON,
    Competitor,
    load_competitors,
    load_cpu_specs,
    load_disk_classes,
    load_matching_rules,
    load_reference_configs,
)
from matching import CompetitorOffer, match_all

log = logging.getLogger("competitor_pipeline")

# provider в ServerRow / имени raw-JSON для каждого профиля парсинга
PROFILE_PROVIDERS = {
    "selectel_nuxt_cdn": "selectel",
    "regcloud_playwright": "regcloud",
    "timeweb_cloud_nuxt": "timeweb_cloud",
}


def _scrape_competitor(comp: Competitor) -> list[dict]:
    if comp.parsing_profile == "selectel_nuxt_cdn":
        return ds.scrape_selectel()
    if comp.parsing_profile == "regcloud_playwright":
        return ds.scrape_regcloud()
    if comp.parsing_profile == "timeweb_cloud_nuxt":
        locations = tuple(comp.extra.get("locations") or ("msk",))
        return ds.scrape_timeweb_cloud(locations)
    raise ValueError(f"Неизвестный parsing_profile: {comp.parsing_profile}")


def _load_latest_raw(provider: str) -> list[dict]:
    """Свежайший data/{provider}_YYYYMMDD.json для --no-scrape."""
    files = sorted(Path(ds.DATA_DIR).glob(f"{provider}_*.json"))
    if not files:
        return []
    latest = files[-1]
    log.info("[%s] Использую сохранённые данные: %s", provider, latest.name)
    return json.loads(latest.read_text(encoding="utf-8"))


def rows_to_offers(rows: list[dict], comp: Competitor) -> list[CompetitorOffer]:
    """ServerRow-словари → CompetitorOffer; строки без расширенных полей
    (старые raw-JSON) пропускаются с предупреждением.

    GPU-серверы в сопоставление не идут (требование клиента 2026-08-25):
    цена с видеокартами несравнима с эталонами Миран без GPU.
    """
    offers = []
    skipped = 0
    skipped_gpu = 0
    for row in rows:
        if row.get("gpu"):
            skipped_gpu += 1
            continue
        pools = row.get("disk_pools")
        if not pools:
            skipped += 1
            continue
        offers.append(CompetitorOffer(
            competitor_id=comp.competitor_id,
            plan_id=row.get("plan_id") or "",
            cpu_model=row["cpu_model"],
            cpu_model_norm=row["cpu_model_norm"],
            cpu_sockets=row.get("cpu_sockets") or 1,
            cpu_cores_total=row.get("cpu_cores_total") or 0,
            ram_gb=row["ram_gb"],
            disk_pools=tuple(pools),
            price_value=float(row["price_rub"]),
            currency=row.get("currency") or comp.currency,
            price_period=row.get("price_period") or comp.price_period,
            stock_count=row.get("quantity_available"),
        ))
    if skipped:
        log.warning(
            "[%s] Пропущено строк без disk_pools (старый формат данных): %d",
            comp.competitor_id, skipped,
        )
    if skipped_gpu:
        log.info(
            "[%s] Исключено GPU-серверов из сопоставления: %d",
            comp.competitor_id, skipped_gpu,
        )
    return offers


def write_run_status(
    sources: list[dict], date_tag: str, out_dir: str | Path | None = None
) -> Path:
    """Статус источников прогона → data/reports/run_status_<date>.json."""
    reports_dir = Path(out_dir or DEFAULT_REPORTS_DIR)
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"run_status_{date_tag}.json"
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "sources": sources,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def run(args) -> int:
    competitors = load_competitors(args.competitors)
    rules = load_matching_rules(args.matching)
    specs = load_cpu_specs(args.cpu_specs)
    refs = load_reference_configs(args.configs)
    size_classes = load_disk_classes(args.disk_classes)
    log.info("Эталонных конфигураций: %d, конкурентов: %d",
             len(refs), len(competitors))

    all_offers: list[CompetitorOffer] = []
    source_status: list[dict] = []
    for comp in competitors:
        provider = PROFILE_PROVIDERS[comp.parsing_profile]
        status = {"competitor_id": comp.competitor_id, "name": comp.name,
                  "url": comp.url, "offers": 0, "status": "error"}
        source_status.append(status)
        try:
            if args.no_scrape:
                rows = _load_latest_raw(provider)
                if not rows:
                    log.error("[%s] Нет сохранённых данных в data/ — пропуск",
                              comp.competitor_id)
                    continue
            else:
                log.info("[%s] Загрузка %s ...", comp.competitor_id, comp.url)
                rows = _scrape_competitor(comp)
                if rows:
                    ds.save_raw_json(provider, rows)
            offers = rows_to_offers(rows, comp)
            with_stock = sum(1 for o in offers if o.stock_count is not None)
            log.info(
                "[%s] Офферов: %d; stock_count найден у %d, отсутствует у %d",
                comp.competitor_id, len(offers), with_stock,
                len(offers) - with_stock,
            )
            status["offers"] = len(offers)
            status["status"] = "ok" if offers else "empty"
            if not offers:
                log.error("[%s] Данные недоступны — колонки останутся пустыми",
                          comp.competitor_id)
            all_offers.extend(offers)
        except Exception:
            log.exception("[%s] Сбой обработки — конкурент пропущен",
                          comp.competitor_id)

    if not all_offers:
        log.error("Ни один конкурент не дал данных — отчёты не сформированы")
        return 1

    matches = match_all(refs, all_offers, rules, specs, size_classes)
    matched_configs = sum(1 for results in matches.values() if results)
    total_matches = sum(len(results) for results in matches.values())
    log.info("Конфигураций с совпадениями: %d из %d (всего матчей: %d)",
             matched_configs, len(refs), total_matches)
    for config_id, results in matches.items():
        if not results:
            log.warning("Без совпадений: %s", config_id)

    date_tag = date.today().isoformat().replace("-", "")
    # Статус источников рядом с отчётом: пустая колонка из-за сбоя скрейпа
    # внешне неотличима от «совпадений нет» — интерфейс должен различать.
    write_run_status(source_status, date_tag, out_dir=args.out_dir)
    written = write_reports(
        refs, matches, competitors, date_tag,
        out_dir=args.out_dir, xlsx=args.xlsx,
    )
    for kind, path in written.items():
        print(f"Отчёт ({kind}): {path}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Сравнение тарифов конкурентов с эталонными конфигурациями miran.ru"
    )
    parser.add_argument("--no-scrape", action="store_true",
                        help="использовать свежайшие raw-JSON из data/ вместо скрейпа")
    parser.add_argument("--xlsx", action="store_true",
                        help="дополнительно записать широкий отчёт в XLSX")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_REPORTS_DIR)
    parser.add_argument("--configs", type=Path, default=MIRAN_CONFIGS_JSON)
    parser.add_argument("--competitors", type=Path, default=COMPETITORS_JSON)
    parser.add_argument("--matching", type=Path, default=MATCHING_JSON)
    parser.add_argument("--cpu-specs", type=Path, default=CPU_SPECS_JSON)
    parser.add_argument("--disk-classes", type=Path, default=DISK_CLASSES_JSON)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    date_tag = date.today().isoformat().replace("-", "")
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(args.out_dir / f"pipeline_{date_tag}.log",
                                encoding="utf-8"),
        ],
    )
    try:
        sys.exit(run(args))
    except ValueError as e:
        log.error("%s", e)
        sys.exit(2)


if __name__ == "__main__":
    main()
