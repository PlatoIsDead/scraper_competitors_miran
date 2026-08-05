# Сопоставление тарифов конкурентов с эталонными конфигурациями miran.ru.
# Чистые функции без I/O — вся конфигурация приходит параметрами.

import re
from dataclasses import dataclass

from config_loader import CpuSpec, MatchingRules, ReferenceConfig


@dataclass(frozen=True)
class CompetitorOffer:
    competitor_id: str
    plan_id: str
    cpu_model: str
    cpu_model_norm: str
    cpu_sockets: int
    cpu_cores_total: int  # 0 = сайт не публикует
    ram_gb: int
    disk_pools: tuple  # tuple[dict]: disk_type / disk_count / disk_size_gb
    price_value: float
    currency: str
    price_period: str
    stock_count: int | None


@dataclass(frozen=True)
class MatchResult:
    config_id: str
    offer: CompetitorOffer
    match_score: float
    cpu_penalty: float
    cores_deviation_pct: float
    ram_deviation_pct: float
    disk_deviation_pct: float


FAMILY_MATCH_PENALTY = 15.0  # штраф score при совпадении только семейства


def canonical_cpu(model: str, specs: dict[str, CpuSpec]) -> str:
    """Нормализованный ключ модели: через словарь алиасов, иначе как есть."""
    norm = re.sub(r"\s+", " ", model.strip().lower()).rstrip(".,;")
    spec = specs.get(norm)
    return spec.canonical.lower() if spec else norm


def cpu_matches(
    ref: ReferenceConfig,
    offer: CompetitorOffer,
    specs: dict[str, CpuSpec],
    rules: MatchingRules,
) -> tuple[bool, float]:
    """Возвращает (прошёл ли CPU-гейт, штраф к score)."""
    ref_key = canonical_cpu(ref.cpu_model, specs)
    offer_key = canonical_cpu(offer.cpu_model, specs)

    if ref_key == offer_key:
        return True, 0.0
    if rules.cpu_match_mode in ("exact", "model"):
        return False, 0.0

    # family: одно семейство и поколение по словарю
    ref_spec = specs.get(ref_key)
    offer_spec = specs.get(offer_key)
    if ref_spec and offer_spec and (
        ref_spec.family == offer_spec.family
        and ref_spec.generation == offer_spec.generation
    ):
        return True, FAMILY_MATCH_PENALTY
    return False, 0.0


def within_pct(ref_val: float, offer_val: float, tol_pct: float) -> bool:
    if ref_val <= 0:
        return False
    return abs(offer_val - ref_val) / ref_val * 100 <= tol_pct


def _deviation_pct(ref_val: float, offer_val: float) -> float:
    return abs(offer_val - ref_val) / ref_val * 100


def _pool_capacity(pool) -> int:
    if isinstance(pool, dict):
        return pool["disk_count"] * pool["disk_size_gb"]
    return pool.disk_count * pool.disk_size_gb


def _pool_type(pool) -> str:
    return pool["disk_type"] if isinstance(pool, dict) else pool.disk_type


def disks_match(
    ref_pools, offer_pools, rules: MatchingRules
) -> tuple[bool, float]:
    """Жадное сопоставление пулов по убыванию ёмкости.

    Каждому эталонному пулу ищется свободный пул оффера с тем же типом
    (NVMe ≠ SSD) и ёмкостью по disk_rule. Лишние пулы оффера не мешают.
    Возвращает (прошёл ли гейт, среднее отклонение ёмкости в %).
    """
    if not ref_pools or not offer_pools:
        return False, 0.0

    ref_sorted = sorted(ref_pools, key=_pool_capacity, reverse=True)
    offer_sorted = sorted(offer_pools, key=_pool_capacity, reverse=True)
    used = [False] * len(offer_sorted)
    deviations = []

    for ref_pool in ref_sorted:
        ref_cap = _pool_capacity(ref_pool)
        found = False
        for j, offer_pool in enumerate(offer_sorted):
            if used[j]:
                continue
            if rules.disk_type_must_match and _pool_type(offer_pool) != _pool_type(ref_pool):
                continue
            offer_cap = _pool_capacity(offer_pool)
            if rules.disk_rule == "gte":
                ok = offer_cap >= ref_cap
            else:  # tolerance
                ok = within_pct(ref_cap, offer_cap, rules.disk_tolerance_pct)
            if ok:
                used[j] = True
                deviations.append(_deviation_pct(ref_cap, offer_cap))
                found = True
                break
        if not found:
            return False, 0.0

    return True, sum(deviations) / len(deviations)


def match_offer(
    ref: ReferenceConfig,
    offer: CompetitorOffer,
    rules: MatchingRules,
    specs: dict[str, CpuSpec],
) -> MatchResult | None:
    """Все гейты (CPU, ядра, RAM, диски) → MatchResult, иначе None."""
    cpu_ok, cpu_penalty = cpu_matches(ref, offer, specs, rules)
    if not cpu_ok:
        return None

    # Ядра: суммарные vs суммарные; фолбэк — словарь моделей
    ref_cores = ref.cpu_cores_total
    offer_cores = offer.cpu_cores_total
    if not offer_cores:
        spec = specs.get(canonical_cpu(offer.cpu_model, specs))
        if spec:
            offer_cores = spec.cores * max(offer.cpu_sockets, 1)
    if offer_cores:
        if not within_pct(ref_cores, offer_cores, rules.cores_tolerance_pct):
            return None
        cores_dev = _deviation_pct(ref_cores, offer_cores)
    else:
        # Ядра неизвестны: пропускаем гейт только при совпадении модели
        # (та же модель ⇒ те же ядра). Family-матч без ядер не пускаем.
        if cpu_penalty > 0:
            return None
        cores_dev = 0.0

    if not within_pct(ref.ram_gb, offer.ram_gb, rules.ram_tolerance_pct):
        return None
    ram_dev = _deviation_pct(ref.ram_gb, offer.ram_gb)

    disks_ok, disk_dev = disks_match(ref.disk_pools, offer.disk_pools, rules)
    if not disks_ok:
        return None

    w = rules.score_weights
    score = 100.0 - (
        w["cpu"] * cpu_penalty
        + w["cores"] * cores_dev
        + w["ram"] * ram_dev
        + w["disk"] * disk_dev
    )
    score = max(0.0, min(100.0, round(score, 1)))

    return MatchResult(
        config_id=ref.config_id,
        offer=offer,
        match_score=score,
        cpu_penalty=cpu_penalty,
        cores_deviation_pct=round(cores_dev, 1),
        ram_deviation_pct=round(ram_dev, 1),
        disk_deviation_pct=round(disk_dev, 1),
    )


def match_all(
    refs: list[ReferenceConfig],
    offers: list[CompetitorOffer],
    rules: MatchingRules,
    specs: dict[str, CpuSpec],
) -> dict[str, list[MatchResult]]:
    """config_id → все подходящие офферы, отсортированные по цене (дешевле первее),
    при равной цене — выше score."""
    results: dict[str, list[MatchResult]] = {ref.config_id: [] for ref in refs}
    for ref in refs:
        for offer in offers:
            m = match_offer(ref, offer, rules, specs)
            if m:
                results[ref.config_id].append(m)
        results[ref.config_id].sort(
            key=lambda m: (m.offer.price_value, -m.match_score)
        )
    return results
