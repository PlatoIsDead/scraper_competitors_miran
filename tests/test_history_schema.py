import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

import dedicated_scraper
from dedicated_scraper import LEGACY_HISTORY_COLS, save_history


def _row_with_extended_fields():
    return {
        "provider": "selectel",
        "cpu_model": "Intel Xeon E3-1230v5",
        "cpu_model_norm": "intel xeon e3-1230v5",
        "cpu_generation": "Skylake",
        "ram_gb": 16,
        "disk_count": 2,
        "disk_size_gb": 480,
        "disk_type": "SSD",
        "price_rub": 5850.0,
        "quantity_available": 3,
        "scraped_at": "2026-07-29",
        # extended fields must NOT leak into history.csv
        "plan_id": "AR20-SSD",
        "cpu_sockets": 1,
        "cpu_cores_total": 4,
        "disk_pools": [{"disk_type": "SSD", "disk_count": 2, "disk_size_gb": 480}],
        "currency": "RUB",
        "price_period": "month",
    }


class TestHistorySchemaFrozen:
    def test_extended_fields_do_not_leak(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dedicated_scraper, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(
            dedicated_scraper, "HISTORY_CSV", str(tmp_path / "history.csv")
        )
        save_history(pd.DataFrame([_row_with_extended_fields()]))
        saved = pd.read_csv(tmp_path / "history.csv")
        assert list(saved.columns) == LEGACY_HISTORY_COLS

    def test_append_to_existing_keeps_schema(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dedicated_scraper, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(
            dedicated_scraper, "HISTORY_CSV", str(tmp_path / "history.csv")
        )
        legacy_row = {
            k: v for k, v in _row_with_extended_fields().items()
            if k in LEGACY_HISTORY_COLS
        }
        pd.DataFrame([legacy_row]).to_csv(
            tmp_path / "history.csv", index=False, encoding="utf-8-sig"
        )
        row2 = _row_with_extended_fields()
        row2["scraped_at"] = "2026-07-30"
        save_history(pd.DataFrame([row2]))
        saved = pd.read_csv(tmp_path / "history.csv")
        assert list(saved.columns) == LEGACY_HISTORY_COLS
        assert len(saved) == 2
