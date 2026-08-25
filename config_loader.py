# Загрузка и валидация конфигов matching-пайплайна (config/*.json).
# Все сообщения об ошибках — по-русски, с именем файла.

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_DIR = Path(__file__).parent / "config"

COMPETITORS_JSON = CONFIG_DIR / "competitors.json"
MATCHING_JSON = CONFIG_DIR / "matching.json"
CPU_SPECS_JSON = CONFIG_DIR / "cpu_specs.json"
MIRAN_CONFIGS_JSON = CONFIG_DIR / "miran_configs.json"
DISK_CLASSES_JSON = CONFIG_DIR / "disk_classes.json"

KNOWN_PARSING_PROFILES = {
    "selectel_nuxt_cdn",
    "regcloud_playwright",
    "timeweb_cloud_nuxt",
}

ALLOWED_DISK_TYPES = {"HDD", "SSD", "NVMe"}
ALLOWED_CPU_MATCH_MODES = {"exact", "model", "family"}
# class — эквивалентность размеров по config/disk_classes.json, количество строго
ALLOWED_DISK_RULES = {"gte", "tolerance", "class"}


@dataclass(frozen=True)
class Competitor:
    competitor_id: str
    name: str
    url: str
    currency: str
    price_period: str
    parsing_profile: str
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class MatchingRules:
    cpu_match_mode: str
    cores_tolerance_pct: float
    ram_tolerance_pct: float
    disk_rule: str
    disk_tolerance_pct: float
    disk_type_must_match: bool
    score_weights: dict
    # NVMe закрывает требование SSD (лучший тип = апгрейд), обратное — нет
    disk_type_allow_upgrade: bool = False


@dataclass(frozen=True)
class CpuSpec:
    canonical: str
    cores: int
    family: str
    generation: str
    aliases: tuple = ()


@dataclass(frozen=True)
class DiskPool:
    disk_type: str
    disk_count: int
    disk_size_gb: int


@dataclass(frozen=True)
class ReferenceConfig:
    config_id: str
    cpu_model: str
    cpu_sockets: int
    cpu_cores_per_socket: int
    ram_gb: int
    disk_pools: tuple
    miran_price: float | None = None  # «Миран по калькулятору»; None — цены нет
    # модель как в файле клиента — для отчёта/UI; матчинг идёт по cpu_model
    cpu_model_display: str = ""

    @property
    def cpu_display(self) -> str:
        return self.cpu_model_display or self.cpu_model

    @property
    def cpu_cores_total(self) -> int:
        return self.cpu_sockets * self.cpu_cores_per_socket


def _load_json(path: Path):
    if not path.exists():
        raise ValueError(f"{path}: файл не найден")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"{path}: некорректный JSON — {e}") from e


def _require(data: dict, key: str, typ, path: Path, ctx: str = ""):
    where = f"{path}{f' ({ctx})' if ctx else ''}"
    if key not in data:
        raise ValueError(f"{where}: отсутствует обязательное поле «{key}»")
    value = data[key]
    if typ is float:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"{where}: поле «{key}» должно быть числом")
        return float(value)
    if typ is int and isinstance(value, bool):
        raise ValueError(f"{where}: поле «{key}» должно быть целым числом")
    if not isinstance(value, typ):
        raise ValueError(
            f"{where}: поле «{key}» должно иметь тип {typ.__name__}"
        )
    return value


def load_competitors(path: Path = COMPETITORS_JSON) -> list[Competitor]:
    data = _load_json(path)
    items = _require(data, "competitors", list, path)
    if not items:
        raise ValueError(f"{path}: список «competitors» пуст")
    competitors = []
    seen_ids = set()
    for item in items:
        cid = _require(item, "competitor_id", str, path)
        if cid in seen_ids:
            raise ValueError(f"{path}: дубликат competitor_id «{cid}»")
        seen_ids.add(cid)
        profile = _require(item, "parsing_profile", str, path, cid)
        if profile not in KNOWN_PARSING_PROFILES:
            raise ValueError(
                f"{path} ({cid}): неизвестный parsing_profile «{profile}»; "
                f"доступны: {', '.join(sorted(KNOWN_PARSING_PROFILES))}"
            )
        competitors.append(Competitor(
            competitor_id=cid,
            name=_require(item, "name", str, path, cid),
            url=_require(item, "url", str, path, cid),
            currency=_require(item, "currency", str, path, cid),
            price_period=_require(item, "price_period", str, path, cid),
            parsing_profile=profile,
            extra=item.get("extra") or {},
        ))
    return competitors


def load_matching_rules(path: Path = MATCHING_JSON) -> MatchingRules:
    data = _load_json(path)
    mode = _require(data, "cpu_match_mode", str, path)
    if mode not in ALLOWED_CPU_MATCH_MODES:
        raise ValueError(
            f"{path}: cpu_match_mode «{mode}» не поддерживается; "
            f"доступны: {', '.join(sorted(ALLOWED_CPU_MATCH_MODES))}"
        )
    disk_rule = _require(data, "disk_rule", str, path)
    if disk_rule not in ALLOWED_DISK_RULES:
        raise ValueError(
            f"{path}: disk_rule «{disk_rule}» не поддерживается; "
            f"доступны: {', '.join(sorted(ALLOWED_DISK_RULES))}"
        )
    tolerances = {}
    for key in ("cores_tolerance_pct", "ram_tolerance_pct", "disk_tolerance_pct"):
        val = _require(data, key, float, path)
        # 0 = строгое совпадение (жёсткий RAM по ТЗ клиента)
        if not 0 <= val <= 100:
            raise ValueError(f"{path}: {key} должен быть числом от 0 до 100")
        tolerances[key] = val
    weights = _require(data, "score_weights", dict, path)
    missing = {"cpu", "cores", "ram", "disk"} - weights.keys()
    if missing:
        raise ValueError(
            f"{path}: в score_weights не хватает ключей: {', '.join(sorted(missing))}"
        )
    total = sum(weights[k] for k in ("cpu", "cores", "ram", "disk"))
    if not math.isclose(total, 1.0, abs_tol=0.01):
        raise ValueError(
            f"{path}: сумма score_weights должна быть равна 1 (сейчас {total:g})"
        )
    return MatchingRules(
        cpu_match_mode=mode,
        cores_tolerance_pct=tolerances["cores_tolerance_pct"],
        ram_tolerance_pct=tolerances["ram_tolerance_pct"],
        disk_rule=disk_rule,
        disk_tolerance_pct=tolerances["disk_tolerance_pct"],
        disk_type_must_match=_require(data, "disk_type_must_match", bool, path),
        score_weights=dict(weights),
        disk_type_allow_upgrade=bool(data.get("disk_type_allow_upgrade", False)),
    )


def load_cpu_specs(path: Path = CPU_SPECS_JSON) -> dict[str, CpuSpec]:
    """Возвращает словарь lookup-ключ → CpuSpec (включая ключи-алиасы)."""
    data = _load_json(path)
    if not isinstance(data, dict) or not data:
        raise ValueError(f"{path}: ожидается непустой JSON-объект модель → спецификация")
    specs: dict[str, CpuSpec] = {}
    for key, item in data.items():
        spec = CpuSpec(
            canonical=_require(item, "canonical", str, path, key),
            cores=_require(item, "cores", int, path, key),
            family=_require(item, "family", str, path, key),
            generation=_require(item, "generation", str, path, key),
            aliases=tuple(item.get("aliases") or ()),
        )
        if spec.cores <= 0:
            raise ValueError(f"{path} ({key}): cores должно быть положительным")
        specs[key.lower()] = spec
        for alias in spec.aliases:
            specs.setdefault(alias.lower(), spec)
    return specs


def load_disk_classes(path: Path = DISK_CLASSES_JSON) -> dict[int, int]:
    """config/disk_classes.json → словарь «размер ГБ → канонический размер класса».

    Группы из разных секций (SSD/NVMe/HDD), пересекающиеся по размеру,
    склеиваются транзитивно: 960 ≈ 1000 в любой секции. Канон = минимум группы.
    Размер, которого нет в словаре, образует собственный класс (identity).
    """
    data = _load_json(path)
    groups = _require(data, "groups", list, path)
    if not groups:
        raise ValueError(f"{path}: список «groups» пуст")

    # union-find по размерам
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for idx, group in enumerate(groups):
        disk_type = _require(group, "disk_type", str, path, f"groups[{idx}]")
        if disk_type not in ALLOWED_DISK_TYPES:
            raise ValueError(
                f"{path} (groups[{idx}]): disk_type «{disk_type}» не поддерживается; "
                f"доступны: {', '.join(sorted(ALLOWED_DISK_TYPES))}"
            )
        sizes = _require(group, "sizes_gb", list, path, f"groups[{idx}]")
        if not sizes:
            raise ValueError(f"{path} (groups[{idx}]): sizes_gb пуст")
        clean = []
        for size in sizes:
            if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
                raise ValueError(
                    f"{path} (groups[{idx}]): размер «{size}» должен быть "
                    "положительным целым числом ГБ"
                )
            parent.setdefault(size, size)
            clean.append(size)
        for size in clean[1:]:
            parent[find(size)] = find(clean[0])

    canon: dict[int, int] = {}
    for size in parent:
        root = find(size)
        canon[root] = min(canon.get(root, root), size)
    return {size: canon[find(size)] for size in parent}


def load_reference_configs(path: Path = MIRAN_CONFIGS_JSON) -> list[ReferenceConfig]:
    data = _load_json(path)
    items = _require(data, "configs", list, path)
    if not items:
        raise ValueError(f"{path}: список «configs» пуст")
    configs = []
    seen_ids = set()
    for item in items:
        cfg_id = _require(item, "config_id", str, path)
        if cfg_id in seen_ids:
            raise ValueError(f"{path}: дубликат config_id «{cfg_id}»")
        seen_ids.add(cfg_id)
        pools_raw = _require(item, "disk_pools", list, path, cfg_id)
        if not pools_raw:
            raise ValueError(f"{path} ({cfg_id}): disk_pools не может быть пустым")
        pools = []
        for pool in pools_raw:
            disk_type = _require(pool, "disk_type", str, path, cfg_id)
            if disk_type not in ALLOWED_DISK_TYPES:
                raise ValueError(
                    f"{path} ({cfg_id}): disk_type «{disk_type}» не поддерживается; "
                    f"доступны: {', '.join(sorted(ALLOWED_DISK_TYPES))}"
                )
            pools.append(DiskPool(
                disk_type=disk_type,
                disk_count=_require(pool, "disk_count", int, path, cfg_id),
                disk_size_gb=_require(pool, "disk_size_gb", int, path, cfg_id),
            ))
        for int_key in ("cpu_sockets", "cpu_cores_per_socket", "ram_gb"):
            if _require(item, int_key, int, path, cfg_id) <= 0:
                raise ValueError(
                    f"{path} ({cfg_id}): {int_key} должно быть положительным"
                )
        price = item.get("miran_price")
        if price is not None and (
            not isinstance(price, (int, float)) or isinstance(price, bool)
        ):
            raise ValueError(
                f"{path} ({cfg_id}): miran_price должно быть числом или null"
            )
        configs.append(ReferenceConfig(
            config_id=cfg_id,
            cpu_model=_require(item, "cpu_model", str, path, cfg_id),
            cpu_sockets=item["cpu_sockets"],
            cpu_cores_per_socket=item["cpu_cores_per_socket"],
            ram_gb=item["ram_gb"],
            disk_pools=tuple(pools),
            miran_price=float(price) if price is not None else None,
            cpu_model_display=str(item.get("cpu_model_display") or ""),
        ))
    return configs
