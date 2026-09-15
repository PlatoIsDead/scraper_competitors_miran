# python -m reconcile [--fresh] [--report data/reports/matches_YYYYMMDD.csv]
#                     [--out-dir data/reports]
# Код выхода: 0 — все пары сошлись; 1 — есть расхождения; 2 — витрина
# недоступна или отчёта нет.

import argparse
import csv
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

import dedicated_scraper as ds
from competitor_pipeline import PROFILE_PROVIDERS, rows_to_offers
from competitor_report import DEFAULT_REPORTS_DIR
from config_loader import (
    load_competitors, load_cpu_specs, load_disk_classes, load_matching_rules,
    load_reference_configs,
)
from run_status import date_tag_msk

from .core import check_pairs, checks_to_rows, render_md, unmatched_candidates
from .sources import FETCHERS

log = logging.getLogger("reconcile")


def latest_report(reports_dir: Path) -> Path | None:
    files = sorted(reports_dir.glob("matches_*.csv"))
    return files[-1] if files else None


def load_offers(competitors, data_dir: Path):
    """Свежайшие raw-JSON по каждому конкуренту → офферы (для кандидатов)."""
    offers = []
    for comp in competitors:
        provider = PROFILE_PROVIDERS.get(comp.parsing_profile)
        files = sorted(data_dir.glob(f"{provider}_2*.json")) if provider else []
        if not files:
            continue
        offers += rows_to_offers(json.loads(files[-1].read_text(encoding="utf-8")), comp)
    return offers


@dataclass
class ReconcileResult:
    """Итог сверки для CLI и кнопки в UI."""
    code: int                      # 0 — всё ✓, 1 — есть ✗, 2 — витрина/отчёт недоступны
    md_path: Path | None = None
    csv_path: Path | None = None
    checks: list = field(default_factory=list)
    failed_sources: dict = field(default_factory=dict)
    error: str | None = None


def reconcile_report(
    report: Path, *, out_dir: Path, configs, competitors_path, matching,
    cpu_specs, disk_classes, fetchers: dict | None = None,
) -> ReconcileResult:
    """Сверка готового matches_<дата>.csv с витринами → reconcile_<дата>.md/.csv.
    fetchers — {competitor_id: () -> cards|None}; по умолчанию сетевые."""
    fetchers = fetchers or FETCHERS
    m = re.search(r"(\d{8})", Path(report).name)
    date_tag = m.group(1) if m else date_tag_msk()
    rows = pd.read_csv(report, dtype=str).fillna("").to_dict("records")

    competitors = load_competitors(competitors_path)
    labels = {c.competitor_id: c.name for c in competitors}
    needed = {str(r.get("competitor_id")) for r in rows}
    cards, failed = {}, {}
    for cid in sorted(needed):
        fetch = fetchers.get(cid)
        if not fetch:
            failed[cid] = "сверка не реализована"
            cards[cid] = None
            continue
        log.info("[%s] запрашиваю витрину…", cid)
        try:
            cards[cid] = fetch()
        except Exception as e:  # сеть/вёрстка — не роняем остальное
            log.exception("[%s] сбой сверки", cid)
            cards[cid] = None
            failed[cid] = str(e)[:200]
        if cards[cid] is None:
            failed.setdefault(cid, "нет данных")
    size_classes = load_disk_classes(disk_classes)
    checks = check_pairs(rows, cards, size_classes)

    refs = load_reference_configs(configs)
    rules = load_matching_rules(matching)
    specs = load_cpu_specs(cpu_specs)
    offers = load_offers(competitors, Path(ds.DATA_DIR))
    matched = {(str(r["config_id"]), str(r["competitor_id"]), str(r["plan_id"])) for r in rows}
    candidates = unmatched_candidates(refs, offers, matched, rules, specs, size_classes)

    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"reconcile_{date_tag}.md"
    csv_path = out_dir / f"reconcile_{date_tag}.csv"
    md_path.write_text(render_md(date_tag, checks, candidates, failed, labels), encoding="utf-8")
    out_rows = checks_to_rows(checks)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0]) if out_rows else
                           ["config_id", "competitor_id", "plan_id", "our_price",
                            "site_price", "result", "reason", "url"])
        w.writeheader()
        w.writerows(out_rows)
    bad = [c for c in checks if not c.ok]
    code = 2 if failed else (1 if bad else 0)
    return ReconcileResult(code=code, md_path=md_path, csv_path=csv_path,
                           checks=checks, failed_sources=failed)


def run(args) -> int:
    reports_dir = Path(args.out_dir)
    if args.fresh:
        from competitor_pipeline import run_pipeline
        pipe_args = argparse.Namespace(
            no_scrape=False, xlsx=False, out_dir=reports_dir,
            configs=args.configs, competitors=args.competitors,
            matching=args.matching, cpu_specs=args.cpu_specs,
            disk_classes=args.disk_classes,
        )
        result = run_pipeline(pipe_args)
        if result.code != 0:
            print(f"Пайплайн не дал отчёта: {result.error}")
            return 2
        report = result.written["long"]
    else:
        report = Path(args.report) if args.report else latest_report(reports_dir)
    if not report or not Path(report).exists():
        print("Отчёт matches_<дата>.csv не найден — запустите с --fresh")
        return 2
    result = reconcile_report(
        Path(report), out_dir=reports_dir, configs=args.configs,
        competitors_path=args.competitors, matching=args.matching,
        cpu_specs=args.cpu_specs, disk_classes=args.disk_classes,
    )
    bad = [c for c in result.checks if not c.ok]
    print(f"Сверка: пар {len(result.checks)}, расхождений {len(bad)} → {result.md_path}")
    for c in bad:
        print(f"  ✗ {c.config_id} {c.competitor_id} {c.plan_id}: {'; '.join(c.reasons)}")
    for cid, why in result.failed_sources.items():
        print(f"  ⚠ {cid}: витрина недоступна ({why})")
    return result.code


def main() -> None:
    p = argparse.ArgumentParser(description="Сверка пар отчёта с витринами конкурентов")
    p.add_argument("--fresh", action="store_true", help="сначала полный прогон пайплайна")
    p.add_argument("--report", type=Path, help="matches_<дата>.csv для сверки (по умолчанию свежайший)")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_REPORTS_DIR)
    from config_loader import (
        COMPETITORS_JSON, CPU_SPECS_JSON, DISK_CLASSES_JSON, MATCHING_JSON, MIRAN_CONFIGS_JSON,
    )
    p.add_argument("--configs", type=Path, default=MIRAN_CONFIGS_JSON)
    p.add_argument("--competitors", type=Path, default=COMPETITORS_JSON)
    p.add_argument("--matching", type=Path, default=MATCHING_JSON)
    p.add_argument("--cpu-specs", type=Path, default=CPU_SPECS_JSON)
    p.add_argument("--disk-classes", type=Path, default=DISK_CLASSES_JSON)
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    sys.exit(run(args))


if __name__ == "__main__":
    main()
