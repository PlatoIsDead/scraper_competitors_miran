import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config_loader import CpuSpec, DiskPool, MatchingRules, ReferenceConfig
from matching import (
    CompetitorOffer,
    canonical_cpu,
    cpu_matches,
    disks_match,
    match_all,
    match_offer,
    within_pct,
)

SPECS = {
    "intel xeon silver 4214r": CpuSpec(
        canonical="Intel Xeon Silver 4214R", cores=12,
        family="silver", generation="Cascade Lake",
        aliases=("silver 4214r", "4214r"),
    ),
    "intel xeon silver 4215r": CpuSpec(
        canonical="Intel Xeon Silver 4215R", cores=8,
        family="silver", generation="Cascade Lake",
        aliases=("silver 4215r", "4215r"),
    ),
    "intel xeon gold 5317": CpuSpec(
        canonical="Intel Xeon Gold 5317", cores=12,
        family="gold", generation="Ice Lake",
        aliases=("gold 5317", "5317"),
    ),
}
# alias keys, как их строит load_cpu_specs
for _key in list(SPECS):
    for _alias in SPECS[_key].aliases:
        SPECS.setdefault(_alias, SPECS[_key])

RULES = MatchingRules(
    cpu_match_mode="model",
    cores_tolerance_pct=25,
    ram_tolerance_pct=25,
    disk_rule="gte",
    disk_tolerance_pct=30,
    disk_type_must_match=True,
    score_weights={"cpu": 0.2, "cores": 0.35, "ram": 0.25, "disk": 0.2},
)

REF = ReferenceConfig(
    config_id="MIR-001",
    cpu_model="Intel Xeon Silver 4214R",
    cpu_sockets=2,
    cpu_cores_per_socket=12,  # total 24
    ram_gb=64,
    disk_pools=(DiskPool("SSD", 2, 960),),
)


def _offer(**overrides) -> CompetitorOffer:
    base = dict(
        competitor_id="selectel",
        plan_id="AR-1",
        cpu_model="Intel Xeon Silver 4214R",
        cpu_model_norm="intel xeon silver 4214r",
        cpu_sockets=2,
        cpu_cores_total=24,
        ram_gb=64,
        disk_pools=({"disk_type": "SSD", "disk_count": 2, "disk_size_gb": 960},),
        price_value=10000.0,
        currency="RUB",
        price_period="month",
        stock_count=None,
    )
    base.update(overrides)
    return CompetitorOffer(**base)


class TestWithinPct:
    def test_exact_boundary_passes(self):
        assert within_pct(100, 125, 25)
        assert within_pct(100, 75, 25)

    def test_just_over_boundary_fails(self):
        assert not within_pct(100, 125.2, 25)

    def test_zero_ref_fails(self):
        assert not within_pct(0, 100, 25)


class TestCanonicalCpu:
    def test_alias_resolves(self):
        assert canonical_cpu("5317", SPECS) == "intel xeon gold 5317"
        assert canonical_cpu("Silver 4214R", SPECS) == "intel xeon silver 4214r"

    def test_unknown_passthrough_normalized(self):
        assert canonical_cpu("  AMD  EPYC 9999 ", SPECS) == "amd epyc 9999"

    def test_intel_without_xeon_resolves(self):
        # selectel непоследователен: «Intel Silver 4214R» без «Xeon»
        assert canonical_cpu("Intel Silver 4214R", SPECS) == \
            "intel xeon silver 4214r"

    def test_frequency_tail_stripped(self):
        assert canonical_cpu("Intel Silver 4214R (12x2.4 GHz HT)", SPECS) == \
            "intel xeon silver 4214r"

    def test_unknown_model_fallback_drops_tail(self):
        assert canonical_cpu("Intel Gold 9999 (8x3.6 GHz HT)", SPECS) == \
            "intel gold 9999"

    def test_bare_xeon_prefix_resolves(self):
        # reg.cloud пишет «Xeon Silver 4214R» без «Intel» (кейс MIR-002:
        # RD-30312 «Xeon E3-1230v3» не матчился)
        assert canonical_cpu("Xeon Silver 4214R", SPECS) == \
            "intel xeon silver 4214r"


class TestCpuMatches:
    def test_same_model_via_alias(self):
        offer = _offer(cpu_model="Silver 4214R")
        ok, penalty = cpu_matches(REF, offer, SPECS, RULES)
        assert ok and penalty == 0.0

    def test_different_model_fails_in_model_mode(self):
        offer = _offer(cpu_model="Intel Xeon Silver 4215R")
        ok, _ = cpu_matches(REF, offer, SPECS, RULES)
        assert not ok

    def test_family_mode_same_generation_passes_with_penalty(self):
        rules = MatchingRules(**{**RULES.__dict__, "cpu_match_mode": "family"})
        offer = _offer(cpu_model="Intel Xeon Silver 4215R")
        ok, penalty = cpu_matches(REF, offer, SPECS, rules)
        assert ok and penalty > 0

    def test_family_mode_different_generation_fails(self):
        rules = MatchingRules(**{**RULES.__dict__, "cpu_match_mode": "family"})
        offer = _offer(cpu_model="Intel Xeon Gold 5317")
        ok, _ = cpu_matches(REF, offer, SPECS, rules)
        assert not ok


class TestDisksMatch:
    def test_gte_larger_passes(self):
        ok, dev = disks_match(
            REF.disk_pools,
            ({"disk_type": "SSD", "disk_count": 2, "disk_size_gb": 1000},),
            RULES,
        )
        assert ok
        assert dev > 0

    def test_gte_smaller_fails(self):
        ok, _ = disks_match(
            REF.disk_pools,
            ({"disk_type": "SSD", "disk_count": 2, "disk_size_gb": 480},),
            RULES,
        )
        assert not ok

    def test_type_mismatch_nvme_vs_ssd_fails(self):
        ok, _ = disks_match(
            REF.disk_pools,
            ({"disk_type": "NVMe", "disk_count": 2, "disk_size_gb": 960},),
            RULES,
        )
        assert not ok

    def test_upgrade_nvme_satisfies_ssd_requirement(self):
        """PCL67-кейс: эталон SSD, оффер NVMe того же объёма — матч при апгрейде."""
        rules = MatchingRules(**{**RULES.__dict__, "disk_type_allow_upgrade": True})
        ok, _ = disks_match(
            REF.disk_pools,
            ({"disk_type": "NVMe", "disk_count": 2, "disk_size_gb": 960},),
            rules,
        )
        assert ok

    def test_upgrade_is_one_way_ssd_never_satisfies_nvme(self):
        rules = MatchingRules(**{**RULES.__dict__, "disk_type_allow_upgrade": True})
        ok, _ = disks_match(
            (DiskPool("NVMe", 2, 960),),
            ({"disk_type": "SSD", "disk_count": 2, "disk_size_gb": 960},),
            rules,
        )
        assert not ok

    def test_upgrade_ssd_satisfies_hdd_requirement(self):
        rules = MatchingRules(**{**RULES.__dict__, "disk_type_allow_upgrade": True})
        ok, _ = disks_match(
            (DiskPool("HDD", 2, 2000),),
            ({"disk_type": "SSD", "disk_count": 2, "disk_size_gb": 2000},),
            rules,
        )
        assert ok

    def test_tolerance_rule_smaller_within_pct_passes(self):
        rules = MatchingRules(**{**RULES.__dict__, "disk_rule": "tolerance"})
        ok, _ = disks_match(
            REF.disk_pools,
            # 2×720 = 1440 vs 1920: −25% в допуске 30%
            ({"disk_type": "SSD", "disk_count": 2, "disk_size_gb": 720},),
            rules,
        )
        assert ok

    def test_multipool_ref_needs_both(self):
        ref_pools = (DiskPool("NVMe", 2, 1000), DiskPool("SSD", 2, 2000))
        ok, _ = disks_match(
            ref_pools,
            ({"disk_type": "NVMe", "disk_count": 2, "disk_size_gb": 1000},),
            RULES,
        )
        assert not ok

    def test_extra_offer_pool_ignored(self):
        ok, _ = disks_match(
            REF.disk_pools,
            (
                {"disk_type": "SSD", "disk_count": 2, "disk_size_gb": 960},
                {"disk_type": "HDD", "disk_count": 2, "disk_size_gb": 8000},
            ),
            RULES,
        )
        assert ok


class TestDisksMatchByClass:
    """Правила ТЗ 2026-08: размеры эквивалентны в пределах класса
    (960 ≈ 1000 ≈ 1 ТБ), количество дисков учитывается строго."""

    CLASSES = {960: 960, 1000: 960, 1900: 1900, 1920: 1900, 2000: 1900}
    RULES_CLASS = MatchingRules(**{**RULES.__dict__, "disk_rule": "class"})

    def _pools(self, *specs):
        return tuple(
            {"disk_type": t, "disk_count": c, "disk_size_gb": s}
            for t, c, s in specs
        )

    def test_client_example_split_pools_equal(self):
        # 2×960 + 8×1000 = 10×1ТБ (один класс, суммы совпадают)
        ok, dev = disks_match(
            self._pools(("SSD", 2, 960), ("SSD", 8, 1000)),
            self._pools(("SSD", 10, 1000)),
            self.RULES_CLASS, self.CLASSES,
        )
        assert ok and dev == 0.0

    def test_client_example_mixed_split_equal(self):
        # 2×960 + 8×1000 = 2×960 + 8×1ТБ
        ok, _ = disks_match(
            self._pools(("SSD", 2, 960), ("SSD", 8, 1000)),
            self._pools(("SSD", 2, 960), ("SSD", 8, 1000)),
            self.RULES_CLASS, self.CLASSES,
        )
        assert ok

    def test_client_example_count_shortfall_fails(self):
        # 2×960 + 8×1000 ≠ 2×960
        ok, _ = disks_match(
            self._pools(("SSD", 2, 960), ("SSD", 8, 1000)),
            self._pools(("SSD", 2, 960)),
            self.RULES_CLASS, self.CLASSES,
        )
        assert not ok

    def test_extra_offer_disks_fail(self):
        # оффер с лишними дисками = другая конфигурация
        ok, _ = disks_match(
            self._pools(("SSD", 2, 960)),
            self._pools(("SSD", 2, 960), ("SSD", 8, 1000)),
            self.RULES_CLASS, self.CLASSES,
        )
        assert not ok

    def test_different_class_fails(self):
        # 1 ТБ и 2 ТБ — разные классы
        ok, _ = disks_match(
            self._pools(("SSD", 2, 1000)),
            self._pools(("SSD", 2, 2000)),
            self.RULES_CLASS, self.CLASSES,
        )
        assert not ok

    def test_class_variants_equal(self):
        # 1920 ≈ 1900 ≈ 2000 — один класс из файла сопоставления
        ok, _ = disks_match(
            self._pools(("SSD", 2, 1920)),
            self._pools(("SSD", 2, 2000)),
            self.RULES_CLASS, self.CLASSES,
        )
        assert ok

    def test_upgrade_nvme_covers_ssd(self):
        rules = MatchingRules(
            **{**self.RULES_CLASS.__dict__, "disk_type_allow_upgrade": True}
        )
        ok, _ = disks_match(
            self._pools(("SSD", 2, 960)),
            self._pools(("NVMe", 2, 1000)),
            rules, self.CLASSES,
        )
        assert ok

    def test_upgrade_one_way_only(self):
        rules = MatchingRules(
            **{**self.RULES_CLASS.__dict__, "disk_type_allow_upgrade": True}
        )
        ok, _ = disks_match(
            self._pools(("NVMe", 2, 960)),
            self._pools(("SSD", 2, 960)),
            rules, self.CLASSES,
        )
        assert not ok

    def test_upgrade_does_not_starve_nvme_demand(self):
        # NVMe-диски оффера сперва закрывают NVMe-требование, SSD — апгрейдом
        rules = MatchingRules(
            **{**self.RULES_CLASS.__dict__, "disk_type_allow_upgrade": True}
        )
        ok, _ = disks_match(
            self._pools(("SSD", 2, 960), ("NVMe", 2, 960)),
            self._pools(("NVMe", 4, 960)),
            rules, self.CLASSES,
        )
        assert ok

    def test_unknown_size_identity_class(self):
        # размера нет в словаре классов — совпадает только сам с собой
        ok, _ = disks_match(
            self._pools(("HDD", 2, 6000)),
            self._pools(("HDD", 2, 6000)),
            self.RULES_CLASS, self.CLASSES,
        )
        assert ok
        ok, _ = disks_match(
            self._pools(("HDD", 2, 6000)),
            self._pools(("HDD", 2, 8000)),
            self.RULES_CLASS, self.CLASSES,
        )
        assert not ok


class TestStrictRam:
    def test_zero_tolerance_requires_exact(self):
        rules = MatchingRules(**{**RULES.__dict__, "ram_tolerance_pct": 0})
        assert match_offer(REF, _offer(ram_gb=64), rules, SPECS) is not None
        assert match_offer(REF, _offer(ram_gb=63), rules, SPECS) is None
        assert match_offer(REF, _offer(ram_gb=96), rules, SPECS) is None


class TestMatchOffer:
    def test_perfect_match_scores_100(self):
        m = match_offer(REF, _offer(), RULES, SPECS)
        assert m is not None
        assert m.match_score == 100.0

    def test_ram_deviation_reduces_score(self):
        # RAM +25%: score = 100 − 0.25·25 = 93.75 → 93.8 (диски идентичны)
        m = match_offer(REF, _offer(ram_gb=80), RULES, SPECS)
        assert m is not None
        assert m.match_score == 93.8

    def test_ram_over_tolerance_rejected(self):
        assert match_offer(REF, _offer(ram_gb=128), RULES, SPECS) is None

    def test_cores_over_tolerance_rejected(self):
        assert match_offer(REF, _offer(cpu_cores_total=32), RULES, SPECS) is None

    def test_unknown_cores_same_model_passes(self):
        m = match_offer(REF, _offer(cpu_cores_total=0), RULES, SPECS)
        assert m is not None
        assert m.cores_deviation_pct == 0.0

    def test_unknown_cores_falls_back_to_specs(self):
        # модель есть в словаре: 12 ядер × 2 сокета = 24 → проходит
        offer = _offer(cpu_cores_total=0, cpu_sockets=2)
        m = match_offer(REF, offer, RULES, SPECS)
        assert m is not None

    def test_wrong_cpu_rejected(self):
        assert match_offer(REF, _offer(cpu_model="AMD EPYC 9334"), RULES, SPECS) is None

    def test_socket_count_mismatch_rejected(self):
        # 1 × Silver 4214R не закрывает эталон 2 × Silver 4214R,
        # даже если опубликованные ядра пролезают в допуск
        offer = _offer(cpu_sockets=1, cpu_cores_total=20)
        assert match_offer(REF, offer, RULES, SPECS) is None


class TestMatchAll:
    def test_sorted_by_price_then_score(self):
        offers = [
            _offer(plan_id="expensive", price_value=20000.0),
            _offer(plan_id="cheap-worse", price_value=9000.0, ram_gb=80),
            _offer(plan_id="cheap-best", price_value=9000.0),
        ]
        results = match_all([REF], offers, RULES, SPECS)
        matched = results["MIR-001"]
        assert [m.offer.plan_id for m in matched] == [
            "cheap-best", "cheap-worse", "expensive"
        ]

    def test_config_without_matches_present_empty(self):
        ref2 = ReferenceConfig(
            config_id="MIR-002",
            cpu_model="Intel Xeon Gold 5317",
            cpu_sockets=2,
            cpu_cores_per_socket=12,
            ram_gb=192,
            disk_pools=(DiskPool("NVMe", 2, 1000),),
        )
        results = match_all([REF, ref2], [_offer()], RULES, SPECS)
        assert results["MIR-001"] and results["MIR-002"] == []
