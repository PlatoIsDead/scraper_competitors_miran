# Разовый конвертер: ручной лист клиентского воркбука → config/miran_configs.json.
# После генерации файл ведётся руками; конвертер нужен только для пересоздания.
#
# Использование:
#   python excel_to_configs.py [--sheet "Март_26_SilverGold"] [--out config/miran_configs.json] [--dry-run]
#
# Замечание о размерах дисков: normalize_disk_gb снапит к стандартной сетке
# (960→1000, 3840→4000). Офферы конкурентов проходят тот же нормализатор,
# поэтому обе стороны matching сравниваются в одинаковой сетке.

import argparse
import json
import logging
import re
from datetime import date
from pathlib import Path

import openpyxl

from dedicated_scraper import normalize_disk_gb, normalize_disk_type

log = logging.getLogger("excel_to_configs")

DEFAULT_XLSX = Path("data") / "Сравнение стоимости DS.xlsx"
DEFAULT_OUT = Path("config") / "miran_configs.json"

# multiplication signs seen in the workbook: ×, x, X, х (cyrillic), *
_MULT = r"[×xXх*]"

_RAM_RE = re.compile(r"^\s*(\d+)\s*ГБ", re.I)
_SOCKETS_RE = re.compile(rf"^\s*(\d+)\s*{_MULT}\s*")
_CORES_RE = re.compile(r"(\d+)\s*яд", re.I)  # «12 ядер», «12 ядра»
_DISK_SEG_RE = re.compile(
    rf"(\d+)\s*{_MULT}\s*(\d+(?:[.,]\d+)?)\s*(ГБ|ТБ|GB|TB)?\s*(NVME|NVMe|SSD|HDD)?",
    re.I,
)
# модель = текст до первого «N ГГц» / «(@» / «(»
_MODEL_CUT_RE = re.compile(r"\s*(?:\d+[.,]\d*\s*ГГц|\(@|\().*$", re.I)


def parse_ref_cpu(text: str, cpu_aliases: dict) -> dict | None:
    """'2 × Intel Xeon Silver 4214R 2.4 ГГц, 12 ядер 24 потока'
    → {cpu_model, cpu_sockets, cpu_cores_per_socket}. None, если не похоже на CPU."""
    if not text or not isinstance(text, str):
        return None
    sockets_m = _SOCKETS_RE.match(text)
    sockets = int(sockets_m.group(1)) if sockets_m else 1
    body = _SOCKETS_RE.sub("", text, count=1)

    cores_m = _CORES_RE.search(body)
    cores_per_socket = int(cores_m.group(1)) if cores_m else 0

    model_raw = _MODEL_CUT_RE.sub("", body).strip(" ,;")
    model_raw = re.sub(r"\s+", " ", model_raw)
    if not model_raw:
        return None

    # канонизация голых моделей («5317», «Silver 4314») через алиасы словаря
    spec = cpu_aliases.get(model_raw.lower())
    if spec:
        model = spec["canonical"]
        if not cores_per_socket:
            cores_per_socket = spec["cores"]
    else:
        model = model_raw
        log.warning("Модель CPU не найдена в cpu_specs.json: «%s»", model_raw)

    if not cores_per_socket:
        log.warning("Не удалось определить ядра для «%s»", text.strip())
        return None

    return {
        "cpu_model": model,
        "cpu_sockets": sockets,
        "cpu_cores_per_socket": cores_per_socket,
    }


def parse_ref_disks(text: str) -> list[dict]:
    """'2 * 1 ТБ NVME + 2 * 2 ТБ SSD' → список пулов. Сегменты без цифр
    (VROC, RAID) пропускаются. Нет единицы («2 х 10 HDD») → ТБ при ≤32."""
    pools = []
    if not text or not isinstance(text, str):
        return pools
    for segment in text.split("+"):
        segment = segment.strip()
        if not segment or not re.search(r"\d", segment):
            continue
        m = _DISK_SEG_RE.search(segment)
        if not m:
            log.warning("Не удалось разобрать сегмент дисков: «%s»", segment)
            continue
        count = int(m.group(1))
        size = float(m.group(2).replace(",", "."))
        unit = (m.group(3) or "").upper()
        if unit in ("ТБ", "TB"):
            size *= 1000
        elif not unit:
            if size <= 32:
                size *= 1000
                log.warning(
                    "Сегмент «%s» без единицы измерения — предполагаю ТБ", segment
                )
            else:
                log.warning(
                    "Сегмент «%s» без единицы измерения — предполагаю ГБ", segment
                )
        pools.append({
            # тип — по всему сегменту: «SSD NVME» должен дать NVMe
            "disk_type": normalize_disk_type(segment),
            "disk_count": count,
            "disk_size_gb": normalize_disk_gb(int(size)),
        })
    return pools


def pick_sheet(wb) -> str:
    """Первый лист, который не «Лист …» и не «*_auto*» — ручные листы идут
    от новых к старым."""
    for name in wb.sheetnames:
        if name.startswith("Лист"):
            continue
        if "_auto" in name:
            continue
        return name
    raise ValueError("В воркбуке не найден ручной лист с конфигурациями")


def convert(xlsx_path: Path, sheet: str | None, cpu_aliases: dict) -> dict:
    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    try:
        sheet_name = sheet or pick_sheet(wb)
        if sheet_name not in wb.sheetnames:
            raise ValueError(f"Лист «{sheet_name}» не найден в {xlsx_path}")
        ws = wb[sheet_name]

        configs = []
        skipped = 0
        for row_idx, row in enumerate(
            ws.iter_rows(min_row=1, max_col=3, values_only=True), start=1
        ):
            cpu_cell, ram_cell, disk_cell = (list(row) + [None] * 3)[:3]
            ram_m = _RAM_RE.match(str(ram_cell)) if ram_cell else None
            if not ram_m:
                skipped += 1
                continue
            cpu = parse_ref_cpu(str(cpu_cell or ""), cpu_aliases)
            if not cpu:
                log.warning("Строка %d: не удалось разобрать CPU «%s» — пропуск",
                            row_idx, cpu_cell)
                continue
            pools = parse_ref_disks(str(disk_cell or ""))
            if not pools:
                log.warning("Строка %d: не удалось разобрать диски «%s» — пропуск",
                            row_idx, disk_cell)
                continue
            configs.append({
                "config_id": f"MIR-{len(configs) + 1:03d}",
                **cpu,
                "ram_gb": int(ram_m.group(1)),
                "disk_pools": pools,
                "source_row": row_idx,
            })

        log.info("Лист «%s»: конфигураций %d, пропущено строк %d",
                 sheet_name, len(configs), skipped)
        return {
            "generated_from": sheet_name,
            "generated_at": date.today().isoformat(),
            "configs": configs,
        }
    finally:
        wb.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Конвертация ручного листа воркбука в config/miran_configs.json"
    )
    parser.add_argument("--xlsx", type=Path, default=DEFAULT_XLSX)
    parser.add_argument("--sheet", default=None,
                        help="имя листа (по умолчанию — новейший ручной)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--dry-run", action="store_true",
                        help="показать результат без записи файла")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    cpu_aliases: dict = {}
    specs_path = Path("config") / "cpu_specs.json"
    if specs_path.exists():
        raw = json.loads(specs_path.read_text(encoding="utf-8"))
        for key, item in raw.items():
            cpu_aliases[key.lower()] = item
            for alias in item.get("aliases", []):
                cpu_aliases.setdefault(alias.lower(), item)
    else:
        log.warning("%s не найден — канонизация моделей CPU отключена", specs_path)

    result = convert(args.xlsx, args.sheet, cpu_aliases)

    if args.dry_run:
        for cfg in result["configs"]:
            pools_txt = " + ".join(
                f"{p['disk_count']}×{p['disk_size_gb']} ГБ {p['disk_type']}"
                for p in cfg["disk_pools"]
            )
            print(f"{cfg['config_id']} (строка {cfg['source_row']}): "
                  f"{cfg['cpu_sockets']} × {cfg['cpu_model']} "
                  f"({cfg['cpu_cores_per_socket']} ядер), "
                  f"{cfg['ram_gb']} ГБ, {pools_txt}")
        print(f"\nВсего: {len(result['configs'])} конфигураций (файл не записан)")
        return

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Записано {len(result['configs'])} конфигураций в {args.out}")


if __name__ == "__main__":
    main()
