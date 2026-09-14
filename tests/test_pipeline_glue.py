"""Стыковка веток release: локации Timeweb только из конфига, сырой JSON
selectel со stock_by_location проходит запись → чтение → офферы."""

import json

import pytest

import competitor_pipeline as cp
import dedicated_scraper as ds
from config_loader import Competitor


def _timeweb(extra: dict) -> Competitor:
    return Competitor(
        competitor_id="timeweb", name="Timeweb Cloud (Санкт-Петербург)",
        url="https://timeweb.cloud/services/dedicated-server?location=ru",
        currency="RUB", price_period="month",
        parsing_profile="timeweb_cloud_nuxt", extra=extra,
    )


class TestTimewebLocationsFromConfig:
    def test_locations_and_url_passed_from_config(self, monkeypatch):
        seen = {}

        def fake(locations, url):
            seen["locations"], seen["url"] = locations, url
            return []

        monkeypatch.setattr(ds, "scrape_timeweb_cloud", fake)
        cp._scrape_competitor(_timeweb({"locations": ["ru"]}))
        assert seen == {
            "locations": ("ru",),
            "url": "https://timeweb.cloud/services/dedicated-server?location=ru",
        }

    def test_empty_locations_is_an_error_not_moscow(self, monkeypatch):
        """Раньше пустой extra.locations молча превращался в ("msk",) —
        таблица считалась бы по Москве, а клиент смотрит Санкт-Петербург."""
        monkeypatch.setattr(
            ds, "scrape_timeweb_cloud",
            lambda *a, **k: pytest.fail("скрейп не должен запускаться"))
        with pytest.raises(ValueError, match="extra.locations пуст"):
            cp._scrape_competitor(_timeweb({}))


class TestRawJsonRoundTrip:
    def test_selectel_row_with_stock_by_location(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ds, "DATA_DIR", str(tmp_path))
        row = {
            "provider": "selectel", "cpu_model": "Intel Xeon E-2236",
            "cpu_model_norm": "intel xeon e-2236", "cpu_generation": "Coffee Lake",
            "ram_gb": 32, "disk_count": 2, "disk_size_gb": 480, "disk_type": "SSD",
            "price_rub": 12000.0, "quantity_available": 3,
            "scraped_at": "2026-09-15", "plan_id": "EL11-SSD",
            "cpu_sockets": 1, "cpu_cores_total": 6,
            "disk_pools": [{"disk_type": "SSD", "disk_count": 2,
                            "disk_size_gb": 480}],
            "currency": "RUB", "price_period": "month", "gpu": "",
            "stock_by_location": {"MSK-1": 1, "SPB-2": 2},
        }
        path = cp._save_raw_json("selectel", [row], "20260915")
        assert path == tmp_path / "selectel_20260915.json"
        stored = json.loads(path.read_text(encoding="utf-8"))
        assert stored[0]["stock_by_location"] == {"MSK-1": 1, "SPB-2": 2}

        rows = cp._load_latest_raw("selectel")
        comp = Competitor(
            competitor_id="selectel", name="Селектел", url="https://selectel.ru/",
            currency="RUB", price_period="month",
            parsing_profile="selectel_nuxt_cdn",
        )
        offers = cp.rows_to_offers(rows, comp)
        assert len(offers) == 1
        assert offers[0].stock_count == 3
        assert offers[0].price_value == 12000.0
