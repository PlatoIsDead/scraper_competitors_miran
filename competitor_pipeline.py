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
from dataclasses import dataclass, field
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
from run_status import RUN_STATUS_VERSION, date_tag_msk, now_msk
from storefront_check import check_provider

log = logging.getLogger("competitor_pipeline")

# Логгеры проекта, чей вывод должен попадать в pipeline_<дата>.log и при
# запуске из Streamlit (где root-логгер по умолчанию на WARNING).
PROJECT_LOGGERS = (
    "competitor_pipeline", "competitor_report", "dedicated_scraper",
    "storefront_check", "matching", "config_loader",
)
# путь → FileHandler: повторные запуски из Streamlit не плодят хендлеры
_LOG_HANDLERS: dict[Path, logging.FileHandler] = {}

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
        # ДЦ — только из competitors.json (extra.locations): молчаливый фолбэк
        # на Москву давал бы таблицу не по той витрине, что смотрит клиент
        # (Санкт-Петербург, решение 14.09.2026)
        locations = tuple(comp.extra.get("locations") or ())
        if not locations:
            raise ValueError(
                f"{comp.competitor_id}: extra.locations пуст в competitors.json "
                "— непонятно, какой дата-центр Timeweb сравнивать"
            )
        return ds.scrape_timeweb_cloud(locations, comp.url)
    raise ValueError(f"Неизвестный parsing_profile: {comp.parsing_profile}")


def _save_raw_json(provider: str, rows: list[dict], date_tag: str) -> Path:
    """Сырой скрейп → data/{provider}_<дата по МСК>.json (в отличие от
    ds.save_raw_json, который берёт дату по часам контейнера)."""
    data_dir = Path(ds.DATA_DIR)
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / f"{provider}_{date_tag}.json"
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


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
            price_note=str(row.get("price_note") or ""),
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
    """Статус источников прогона → data/reports/run_status_<date>.json.
    Время — tz-aware по Москве; это единственный источник правды о том,
    когда скрейпили (mtime файлов в облаке врёт после перезапуска)."""
    reports_dir = Path(out_dir or DEFAULT_REPORTS_DIR)
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"run_status_{date_tag}.json"
    payload = {
        "version": RUN_STATUS_VERSION,
        "timezone": "Europe/Moscow",
        "date_tag": date_tag,
        "generated_at": now_msk().isoformat(timespec="seconds"),
        "sources": sources,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def ensure_pipeline_log(out_dir: str | Path | None, date_tag: str) -> Path:
    """Файл data/reports/pipeline_<дата>.log для прогона из UI и CLI.

    Идемпотентно: один FileHandler на путь, повторные вызовы (каждый клик
    «Запустить сравнение» в Streamlit) хендлеры не дублируют; хендлеры
    прошлых дат закрываются. Логгеры проекта поднимаются до INFO, если их
    эффективный уровень выше (root в Streamlit = WARNING).
    """
    reports_dir = Path(out_dir or DEFAULT_REPORTS_DIR)
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = (reports_dir / f"pipeline_{date_tag}.log").resolve()
    root = logging.getLogger()
    for old_path, old_handler in list(_LOG_HANDLERS.items()):
        if old_path != path:
            root.removeHandler(old_handler)
            old_handler.close()
            del _LOG_HANDLERS[old_path]
    handler = _LOG_HANDLERS.get(path)
    if handler is None:
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        _LOG_HANDLERS[path] = handler
    if handler not in root.handlers:
        root.addHandler(handler)
    for name in PROJECT_LOGGERS:
        logger = logging.getLogger(name)
        if logger.getEffectiveLevel() > logging.INFO:
            logger.setLevel(logging.INFO)
    return path


def _error_text(exc: BaseException, limit: int = 300) -> str:
    text = f"{type(exc).__name__}: {exc}".strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def collect_sources(
    competitors: list[Competitor],
    *,
    no_scrape: bool,
    date_tag: str,
    scrape=None,
    load_raw=None,
    check=None,
    save_raw=None,
) -> tuple[list[CompetitorOffer], list[dict]]:
    """Скрейп (или чтение raw-JSON) по каждому конкуренту → офферы и
    статус по источникам. Ошибка одного конкурента не прерывает прогон:
    у него status=error и текст ошибки, у остальных — свои данные.

    Зависимости (scrape/load_raw/check/save_raw) вынесены в параметры,
    чтобы тесты подменяли их без сети.
    """
    # разрешаются при вызове, а не при определении: monkeypatch в тестах
    # и подмена в UI должны действовать
    scrape = scrape or _scrape_competitor
    load_raw = load_raw or _load_latest_raw
    check = check or check_provider
    save_raw = save_raw or _save_raw_json
    all_offers: list[CompetitorOffer] = []
    source_status: list[dict] = []
    for comp in competitors:
        status = {
            "competitor_id": comp.competitor_id, "name": comp.name,
            "url": comp.url, "provider": None, "offers": 0,
            "status": "error", "error": None,
            "started_at": now_msk().isoformat(timespec="seconds"),
            "finished_at": None,
        }
        source_status.append(status)
        try:
            provider = PROFILE_PROVIDERS[comp.parsing_profile]
            status["provider"] = provider
            if no_scrape:
                rows = load_raw(provider)
                if not rows:
                    log.error("[%s] Нет сохранённых данных в data/ — пропуск",
                              comp.competitor_id)
                    status["error"] = "нет сохранённых данных в data/"
                    continue
            else:
                log.info("[%s] Загрузка %s ...", comp.competitor_id, comp.url)
                rows = scrape(comp)
                if rows:
                    save_raw(provider, rows, date_tag)
            offers = rows_to_offers(rows, comp)
            with_stock = sum(1 for o in offers if o.stock_count is not None)
            log.info(
                "[%s] Офферов: %d; stock_count найден у %d, отсутствует у %d",
                comp.competitor_id, len(offers), with_stock,
                len(offers) - with_stock,
            )
            status["offers"] = len(offers)
            status["status"] = "ok" if offers else "empty"
            if rows and not no_scrape:
                result = check(
                    provider, rows,
                    html=ds.LAST_RENDERED_HTML.get(provider, ""),
                )
                status["check"] = result
                n_diff = len(result["discrepancies"])
                if result["status"] == "ok":
                    log.warning(
                        "[%s] СВЕРКА С ВИТРИНОЙ: расхождений %d, первое: %s",
                        comp.competitor_id, n_diff, result["discrepancies"][0],
                    )
                elif result["status"] == "clean":
                    log.info("[%s] Сверка с витриной: расхождений нет",
                             comp.competitor_id)
                else:
                    log.warning("[%s] Сверка с витриной не выполнена: %s",
                                comp.competitor_id, result.get("detail", ""))
            if not offers:
                log.error("[%s] Данные недоступны — колонки останутся пустыми",
                          comp.competitor_id)
                status["error"] = "скрейп вернул 0 предложений"
            all_offers.extend(offers)
        except Exception as e:
            log.exception("[%s] Сбой обработки — конкурент пропущен",
                          comp.competitor_id)
            status["status"] = "error"
            status["error"] = _error_text(e)
        finally:
            status["finished_at"] = now_msk().isoformat(timespec="seconds")
    return all_offers, source_status


@dataclass
class RunResult:
    """Итог прогона для UI: код, статус по конкурентам, отчёты, лог."""
    code: int
    sources: list[dict] = field(default_factory=list)
    written: dict[str, Path] = field(default_factory=dict)
    log_path: "Path | None" = None
    error: "str | None" = None

    def source_errors(self) -> list[str]:
        return [f"{s.get('name') or s.get('competitor_id')}: "
                f"{s.get('error') or 'данные не получены'}"
                for s in self.sources if s.get("status") != "ok"]


def run_pipeline(args) -> RunResult:
    """Полный прогон: скрейп → сопоставление → отчёты + run_status + лог."""
    date_tag = date_tag_msk()
    log_path = ensure_pipeline_log(args.out_dir, date_tag)
    competitors = load_competitors(args.competitors)
    rules = load_matching_rules(args.matching)
    specs = load_cpu_specs(args.cpu_specs)
    refs = load_reference_configs(args.configs)
    size_classes = load_disk_classes(args.disk_classes)
    log.info("Эталонных конфигураций: %d, конкурентов: %d",
             len(refs), len(competitors))

    all_offers, source_status = collect_sources(
        competitors, no_scrape=args.no_scrape, date_tag=date_tag,
    )

    if not all_offers:
        msg = "Ни один конкурент не дал данных — отчёты не сформированы"
        log.error(msg)
        return RunResult(code=1, sources=source_status, log_path=log_path,
                         error=msg)

    matches = match_all(refs, all_offers, rules, specs, size_classes)
    matched_configs = sum(1 for results in matches.values() if results)
    total_matches = sum(len(results) for results in matches.values())
    log.info("Конфигураций с совпадениями: %d из %d (всего матчей: %d)",
             matched_configs, len(refs), total_matches)
    for config_id, results in matches.items():
        if not results:
            log.warning("Без совпадений: %s", config_id)

    # Статус источников рядом с отчётом: пустая колонка из-за сбоя скрейпа
    # внешне неотличима от «совпадений нет» — интерфейс должен различать.
    write_run_status(source_status, date_tag, out_dir=args.out_dir)
    written = write_reports(
        refs, matches, competitors, date_tag,
        out_dir=args.out_dir, xlsx=args.xlsx,
    )
    return RunResult(code=0, sources=source_status, written=written,
                     log_path=log_path)


def run(args) -> int:
    """CLI-обёртка: печатает пути отчётов, возвращает код выхода."""
    result = run_pipeline(args)
    for kind, path in result.written.items():
        print(f"Отчёт ({kind}): {path}")
    return result.code


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
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler()],
    )
    # файл pipeline_<дата по МСК>.log подключает сам прогон (общий с UI)
    try:
        sys.exit(run(args))
    except ValueError as e:
        log.error("%s", e)
        sys.exit(2)


if __name__ == "__main__":
    main()
