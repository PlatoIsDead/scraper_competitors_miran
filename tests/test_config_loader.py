import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from config_loader import (
    load_competitors,
    load_cpu_specs,
    load_matching_rules,
    load_reference_configs,
)

PROJECT_CONFIG = Path(__file__).parent.parent / "config"


def _write(tmp_path, name, data):
    path = tmp_path / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


VALID_MATCHING = {
    "cpu_match_mode": "model",
    "cores_tolerance_pct": 25,
    "ram_tolerance_pct": 25,
    "disk_rule": "gte",
    "disk_tolerance_pct": 30,
    "disk_type_must_match": True,
    "score_weights": {"cpu": 0.2, "cores": 0.35, "ram": 0.25, "disk": 0.2},
}

VALID_CONFIGS = {
    "configs": [
        {
            "config_id": "MIR-001",
            "cpu_model": "Intel Xeon Silver 4214R",
            "cpu_sockets": 2,
            "cpu_cores_per_socket": 12,
            "ram_gb": 64,
            "disk_pools": [
                {"disk_type": "SSD", "disk_count": 2, "disk_size_gb": 960}
            ],
        }
    ]
}


class TestProjectConfigsLoad:
    """Реальные файлы репозитория должны проходить собственную валидацию."""

    def test_competitors(self):
        competitors = load_competitors(PROJECT_CONFIG / "competitors.json")
        assert {c.competitor_id for c in competitors} == {
            "selectel", "reg_cloud", "timeweb"
        }

    def test_matching(self):
        rules = load_matching_rules(PROJECT_CONFIG / "matching.json")
        assert rules.disk_rule in ("gte", "tolerance", "class")

    def test_cpu_specs_with_aliases(self):
        specs = load_cpu_specs(PROJECT_CONFIG / "cpu_specs.json")
        # голая модель из воркбука резолвится через алиас
        assert specs["5317"].canonical == "Intel Xeon Gold 5317"
        assert specs["intel xeon gold 5317"] is specs["5317"]


class TestMatchingValidation:
    def test_unknown_mode_rejected(self, tmp_path):
        bad = dict(VALID_MATCHING, cpu_match_mode="fuzzy")
        with pytest.raises(ValueError, match="cpu_match_mode"):
            load_matching_rules(_write(tmp_path, "m.json", bad))

    def test_tolerance_out_of_range(self, tmp_path):
        bad = dict(VALID_MATCHING, ram_tolerance_pct=150)
        with pytest.raises(ValueError, match="ram_tolerance_pct"):
            load_matching_rules(_write(tmp_path, "m.json", bad))

    def test_weights_must_sum_to_one(self, tmp_path):
        bad = dict(VALID_MATCHING, score_weights={"cpu": 0.5, "cores": 0.5, "ram": 0.5, "disk": 0.5})
        with pytest.raises(ValueError, match="score_weights"):
            load_matching_rules(_write(tmp_path, "m.json", bad))

    def test_missing_field(self, tmp_path):
        bad = {k: v for k, v in VALID_MATCHING.items() if k != "disk_rule"}
        with pytest.raises(ValueError, match="disk_rule"):
            load_matching_rules(_write(tmp_path, "m.json", bad))


class TestCompetitorsValidation:
    def test_unknown_profile_rejected(self, tmp_path):
        bad = {"competitors": [{
            "competitor_id": "x", "name": "X", "url": "https://x",
            "currency": "RUB", "price_period": "month",
            "parsing_profile": "no_such_profile",
        }]}
        with pytest.raises(ValueError, match="parsing_profile"):
            load_competitors(_write(tmp_path, "c.json", bad))

    def test_duplicate_id_rejected(self, tmp_path):
        item = {
            "competitor_id": "x", "name": "X", "url": "https://x",
            "currency": "RUB", "price_period": "month",
            "parsing_profile": "selectel_nuxt_cdn",
        }
        with pytest.raises(ValueError, match="дубликат"):
            load_competitors(_write(tmp_path, "c.json", {"competitors": [item, item]}))

    def test_missing_file(self, tmp_path):
        with pytest.raises(ValueError, match="не найден"):
            load_competitors(tmp_path / "nope.json")


class TestReferenceConfigsValidation:
    def test_valid_loads(self, tmp_path):
        configs = load_reference_configs(_write(tmp_path, "r.json", VALID_CONFIGS))
        assert configs[0].cpu_cores_total == 24
        assert configs[0].disk_pools[0].disk_size_gb == 960

    def test_bad_disk_type(self, tmp_path):
        bad = json.loads(json.dumps(VALID_CONFIGS))
        bad["configs"][0]["disk_pools"][0]["disk_type"] = "SATA"
        with pytest.raises(ValueError, match="disk_type"):
            load_reference_configs(_write(tmp_path, "r.json", bad))

    def test_empty_pools(self, tmp_path):
        bad = json.loads(json.dumps(VALID_CONFIGS))
        bad["configs"][0]["disk_pools"] = []
        with pytest.raises(ValueError, match="disk_pools"):
            load_reference_configs(_write(tmp_path, "r.json", bad))

    def test_duplicate_config_id(self, tmp_path):
        bad = {"configs": VALID_CONFIGS["configs"] * 2}
        with pytest.raises(ValueError, match="дубликат"):
            load_reference_configs(_write(tmp_path, "r.json", bad))

    def test_nonpositive_ram(self, tmp_path):
        bad = json.loads(json.dumps(VALID_CONFIGS))
        bad["configs"][0]["ram_gb"] = 0
        with pytest.raises(ValueError, match="ram_gb"):
            load_reference_configs(_write(tmp_path, "r.json", bad))
