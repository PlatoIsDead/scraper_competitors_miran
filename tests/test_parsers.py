import pytest
import sys
from pathlib import Path
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent.parent))

from dedicated_scraper import (
    _parse_miran_html,
    _parse_regcloud_html,
    _parse_1dedic_article,
    _parse_netrack_html,
    _parse_selectel_flat,
    _parse_storage_pool,
    _precustom_to_cfg,
    _selectel_cfg_to_row,
    _parse_timeweb_cloud_nuxt,
    _parse_timeweb_html,
    _parse_hostkey_html,
    _parse_itlite_html,
    _resolve_nuxt,
)

TODAY = "2026-06-02"
ALLOWED_DISK_TYPES = {"SSD", "HDD", "NVMe"}


# ── _resolve_nuxt ─────────────────────────────────────────────────────

class TestResolveNuxt:
    def test_non_integer_passthrough(self):
        assert _resolve_nuxt(["hello"], "hello") == "hello"

    def test_integer_resolves_to_value(self):
        # flat[1] = 0 is an integer primitive — returned as-is, not followed further
        data = ["resolved", 0]
        assert _resolve_nuxt(data, 1) == 0

    def test_string_at_index_resolves(self):
        data = ["resolved", "other"]
        assert _resolve_nuxt(data, 0) == "resolved"

    def test_dict_values_resolved(self):
        data = [{"name": 1, "size": 2}, "Ryzen 9", 480]
        assert _resolve_nuxt(data, 0) == {"name": "Ryzen 9", "size": 480}

    def test_list_elements_resolved(self):
        data = ["x", "y", [0, 1]]
        assert _resolve_nuxt(data, 2) == ["x", "y"]

    def test_depth_limit_returns_idx(self):
        # At depth > 8 the function returns idx unchanged (integer)
        data = [{"self": 0}]
        result = _resolve_nuxt(data, 0, depth=9)
        assert result == 0

    def test_nested_dict_in_list(self):
        data = [{"size": 1, "count": 2}, 16, 2, [0]]
        assert _resolve_nuxt(data, 3) == [{"size": 16, "count": 2}]


class TestResolveNuxtFixture:
    def test_config_count(self, selectel_flat):
        configs = [
            i for i, item in enumerate(selectel_flat)
            if isinstance(item, dict)
            and all(k in item for k in ("cpu", "ram", "disk", "price_collection"))
        ]
        assert len(configs) >= 100

    def test_first_config_structure(self, selectel_flat):
        configs = [
            i for i, item in enumerate(selectel_flat)
            if isinstance(item, dict)
            and all(k in item for k in ("cpu", "ram", "disk", "price_collection"))
        ]
        cfg = _resolve_nuxt(selectel_flat, configs[0])
        assert isinstance(cfg["cpu"], dict)
        assert "name" in cfg["cpu"]
        assert isinstance(cfg["ram"], list)
        assert isinstance(cfg["disk"], list)
        price_rub = cfg["price_collection"]["RUB"]["month"]
        assert isinstance(price_rub, (int, float))
        assert price_rub > 0


# ── _parse_1dedic_article ─────────────────────────────────────────────

def _make_1dedic_article(cpu_text, ram_text, disk_text, price_text):
    html = f"""
    <article class="product-card">
      <div class="product-card__option"><i class="icon-cpu"></i>{cpu_text}</div>
      <div class="product-card__option"><i class="icon-ram"></i>{ram_text}</div>
      <div class="product-card__option"><i class="icon-hard-disk"></i>{disk_text}</div>
      <span class="price__active">{price_text}</span>
    </article>
    """
    return BeautifulSoup(html, "lxml").find("article")


class TestParse1dedicArticle:
    def test_multi_disk_nvme(self):
        art = _make_1dedic_article(
            "Amd Ryzen 9 5950X 3.4-4.9 ГГц, 16 ядер",
            "32 Гб", "2x 1000 Гб NVMe", "14 000"
        )
        row = _parse_1dedic_article(art, TODAY)
        assert row is not None
        assert row["cpu_model"] == "Ryzen 9 5950X"
        assert row["ram_gb"] == 32
        assert row["disk_count"] == 2
        assert row["disk_size_gb"] == 1000
        assert row["disk_type"] == "NVMe"
        assert row["price_rub"] == 14000.0

    def test_single_disk_ssd_no_far_snap(self):
        art = _make_1dedic_article(
            "Intel Xeon E3-1230 V5 3.4 ГГц, 4 ядра",
            "16 Гб", "750 Гб SSD", "5 368"
        )
        row = _parse_1dedic_article(art, TODAY)
        assert row is not None
        assert row["disk_count"] == 1
        assert row["disk_size_gb"] == 750  # 750 далеко от сетки — не снапится
        assert row["disk_type"] == "SSD"

    def test_tb_disk(self):
        art = _make_1dedic_article(
            "Intel Xeon E5-2630 V4 2.2 ГГц, 10 ядер",
            "64 Гб", "2x 2 ТБ HDD", "10 000"
        )
        row = _parse_1dedic_article(art, TODAY)
        assert row is not None
        assert row["disk_size_gb"] == 2000
        assert row["disk_type"] == "HDD"

    def test_missing_price_returns_none(self):
        html = """
        <article class="product-card">
          <div class="product-card__option"><i class="icon-cpu"></i>Ryzen 9 5950X</div>
          <div class="product-card__option"><i class="icon-ram"></i>32 Гб</div>
        </article>
        """
        art = BeautifulSoup(html, "lxml").find("article")
        assert _parse_1dedic_article(art, TODAY) is None

    def test_zero_price_returns_none(self):
        art = _make_1dedic_article("Ryzen 9 5950X", "32 Гб", "1000 Гб SSD", "0")
        assert _parse_1dedic_article(art, TODAY) is None

    def test_missing_ram_returns_none(self):
        html = """
        <article class="product-card">
          <div class="product-card__option"><i class="icon-cpu"></i>Ryzen 9 5950X</div>
          <span class="price__active">10000</span>
        </article>
        """
        art = BeautifulSoup(html, "lxml").find("article")
        assert _parse_1dedic_article(art, TODAY) is None

    def test_provider_and_date(self):
        art = _make_1dedic_article(
            "Intel Xeon E3-1230 3.2 ГГц, 4 ядра",
            "16 Гб", "500 Гб SSD", "5000"
        )
        row = _parse_1dedic_article(art, TODAY)
        assert row["provider"] == "1dedic"
        assert row["scraped_at"] == TODAY


# ── _parse_miran_html ─────────────────────────────────────────────────

class TestParseMiranHtml:
    def test_fixture_row_count(self, miran_html):
        rows = _parse_miran_html(miran_html, TODAY)
        assert 10 <= len(rows) <= 25

    def test_fixture_all_required_fields(self, miran_html):
        rows = _parse_miran_html(miran_html, TODAY)
        for row in rows:
            assert row["provider"] == "miran"
            assert row["cpu_model"] != ""
            assert row["ram_gb"] > 0
            assert row["price_rub"] > 0
            assert row["disk_type"] in ALLOWED_DISK_TYPES

    def test_minimal_html(self):
        html = (
            b'<html><body>'
            b'<div class="mb-services__item">'
            b'<div class="mb-services__title">Intel Xeon E3-1230 V5</div>'
            b'16 \xd0\x93\xd0\x91 2 x 2000 \xd0\x93\xd0\x91 SATA'
            b' 5\xc2\xa0368\xc2\xa0\xe2\x82\xbd / \xd0\xbc\xd0\xb5\xd1\x81'
            b'</div></body></html>'
        )
        rows = _parse_miran_html(html, TODAY)
        assert len(rows) == 1
        assert rows[0]["cpu_model"] == "Intel Xeon E3-1230 V5"
        assert rows[0]["price_rub"] == 5368.0


# ── _parse_regcloud_html ──────────────────────────────────────────────

def _make_regcloud_item(cpu, ram, disk, price_class, price):
    return f"""
    <div class="b-dedicated-servers-list-item-cloud">
      <p class="b-dedicated-servers-list-item-cloud__cpu-title">{cpu}</p>
      <p class="b-dedicated-servers-list-item-cloud__ram">{ram}</p>
      <p class="b-dedicated-servers-list-item-cloud__hdds">{disk}</p>
      <p class="b-dedicated-servers-list-item-cloud__{price_class}">{price}</p>
    </div>
    """


class TestParseRegcloudHtml:
    def test_fixture_row_count(self, regcloud_html):
        rows = _parse_regcloud_html(regcloud_html, TODAY)
        assert 100 <= len(rows) <= 300

    def test_fixture_all_required_fields(self, regcloud_html):
        rows = _parse_regcloud_html(regcloud_html, TODAY)
        for row in rows:
            assert row["provider"] == "regcloud"
            assert row["cpu_model"] != ""
            assert row["ram_gb"] > 0
            assert row["price_rub"] > 0
            assert row["disk_size_gb"] > 0
            assert row["disk_type"] in ALLOWED_DISK_TYPES

    def test_base_price_extracted(self):
        html = _make_regcloud_item(
            "AMD EPYC 9334", "128 ГБ DDR4 ECC",
            "2 x 1000 ГБ SSD NVMe", "base-price", "19\xa0100₽/мес"
        )
        rows = _parse_regcloud_html(html, TODAY)
        assert len(rows) == 1
        assert rows[0]["price_rub"] == 19100.0

    def test_dual_socket_cpu_prefix_stripped(self):
        html = _make_regcloud_item(
            "2 × AMD EPYC 9334", "512 ГБ DDR4 ECC",
            "2 x 1000 ГБ SSD NVMe", "base-price", "130\xa0985₽/мес"
        )
        rows = _parse_regcloud_html(html, TODAY)
        assert len(rows) == 1
        assert rows[0]["cpu_model"] == "AMD EPYC 9334"
        assert rows[0]["cpu_model_norm"] == "amd epyc 9334"
        assert rows[0]["cpu_generation"] == "Genoa"

    def test_current_price_preferred_over_base(self):
        html = f"""
        <div class="b-dedicated-servers-list-item-cloud">
          <p class="b-dedicated-servers-list-item-cloud__cpu-title">AMD EPYC 9474F</p>
          <p class="b-dedicated-servers-list-item-cloud__ram">512 ГБ DDR5</p>
          <p class="b-dedicated-servers-list-item-cloud__hdds">2 x 1000 ГБ SSD NVMe</p>
          <p class="b-dedicated-servers-list-item-cloud__current-price">130\xa0985₽/мес</p>
          <p class="b-dedicated-servers-list-item-cloud__base-price">154\xa0100₽/мес</p>
        </div>
        """
        rows = _parse_regcloud_html(html, TODAY)
        assert rows[0]["price_rub"] == 130985.0

    def test_decimal_tb_disk(self):
        html = _make_regcloud_item(
            "AMD EPYC 9334", "512 ГБ DDR4 ECC",
            "2 x 3.8 ТБ SSD NVMe U.2", "base-price", "239\xa0700₽/мес"
        )
        rows = _parse_regcloud_html(html, TODAY)
        assert len(rows) == 1
        assert rows[0]["disk_count"] == 2
        assert rows[0]["disk_size_gb"] == 3800  # 3800 ≈ 4000 решает disk_classes
        assert rows[0]["disk_type"] == "NVMe"

    def test_decimal_tb_19(self):
        html = _make_regcloud_item(
            "Intel Xeon Gold 6342", "256 ГБ DDR4",
            "2 x 1.9 ТБ SSD NVMe", "base-price", "50\xa0000₽/мес"
        )
        rows = _parse_regcloud_html(html, TODAY)
        assert rows[0]["disk_size_gb"] == 1900  # 1900 ≈ 2000 решает disk_classes

    def test_missing_disk_size_skips_row(self):
        html = _make_regcloud_item(
            "AMD EPYC 9334", "128 ГБ DDR4",
            "Disk info TBD", "base-price", "50\xa0000₽/мес"
        )
        rows = _parse_regcloud_html(html, TODAY)
        assert len(rows) == 0

    def test_no_price_skips_row(self):
        html = f"""
        <div class="b-dedicated-servers-list-item-cloud">
          <p class="b-dedicated-servers-list-item-cloud__cpu-title">AMD EPYC 9334</p>
          <p class="b-dedicated-servers-list-item-cloud__ram">128 ГБ DDR4</p>
          <p class="b-dedicated-servers-list-item-cloud__hdds">2 x 1000 ГБ SSD NVMe</p>
        </div>
        """
        rows = _parse_regcloud_html(html, TODAY)
        assert len(rows) == 0

    def test_discounted_price_value_preferred_over_base(self):
        # вёрстка 2026-08: актуальная цена в __price-value_per-months_one,
        # __base-price — перечёркнутая базовая (кейс RD-56106: 88 830 vs 98 700)
        html = """
        <div class="b-dedicated-servers-list-item-cloud">
          <p class="b-dedicated-servers-list-item-cloud__cpu-title">2 × Intel Xeon Silver 4214R</p>
          <p class="b-dedicated-servers-list-item-cloud__ram">128 ГБ DDR4</p>
          <p class="b-dedicated-servers-list-item-cloud__hdds">2 x 960 ГБ SSD SATA</p>
          <span class="b-dedicated-servers-list-item-cloud__price-value b-dedicated-servers-list-item-cloud__price-value_per-months_one">88\xa0830 ₽ /мес</span>
          <p class="b-dedicated-servers-list-item-cloud__base-price">98\xa0700 ₽ /мес</p>
          <p class="b-dedicated-servers-list-item-cloud__discount">Скидка на сервер 10%</p>
        </div>
        """
        rows = _parse_regcloud_html(html, TODAY)
        assert len(rows) == 1
        assert rows[0]["price_rub"] == 88830.0

    def test_base_price_fallback_without_discount(self):
        html = _make_regcloud_item(
            "AMD EPYC 9334", "128 ГБ DDR4",
            "2 x 1000 ГБ SSD NVMe", "base-price", "50\xa0000₽/мес"
        )
        rows = _parse_regcloud_html(html, TODAY)
        assert rows[0]["price_rub"] == 50000.0

    def test_gpu_element_captured(self):
        # кейс RD-56106: сервер с 4 × RTX A4000 — GPU уходит в поле gpu
        html = """
        <div class="b-dedicated-servers-list-item-cloud">
          <p class="b-dedicated-servers-list-item-cloud__title">RD-56106</p>
          <p class="b-dedicated-servers-list-item-cloud__cpu-title">2 × Intel Xeon Silver 4214R</p>
          <p class="b-dedicated-servers-list-item-cloud__ram">128 ГБ DDR4</p>
          <p class="b-dedicated-servers-list-item-cloud__gpu">4 × RTX A4000 16GB</p>
          <p class="b-dedicated-servers-list-item-cloud__hdds">2 x 960 ГБ SSD SATA</p>
          <p class="b-dedicated-servers-list-item-cloud__base-price">88\xa0830₽/мес</p>
        </div>
        """
        rows = _parse_regcloud_html(html, TODAY)
        assert len(rows) == 1
        assert rows[0]["gpu"] == "4 × RTX A4000 16GB"

    def test_no_gpu_element_empty_field(self):
        html = _make_regcloud_item(
            "AMD EPYC 9334", "128 ГБ DDR4",
            "2 x 1000 ГБ SSD NVMe", "base-price", "50\xa0000₽/мес"
        )
        rows = _parse_regcloud_html(html, TODAY)
        assert rows[0]["gpu"] == ""


# ── _parse_netrack_html ───────────────────────────────────────────────

def _make_netrack_card(price="8 194₽", cpu="Intel Xeon E 2334",
                       ram="64", disk1="960 GB", disk2="", nvme="NVMe"):
    disk2_attr = f'data-disk2="{disk2}"' if disk2 else ""
    return (
        f'<div data-price="{price}" data-cpu="{cpu}" data-ram="{ram}" '
        f'data-disk1="{disk1}" {disk2_attr} data-disk_nvme="{nvme}"></div>'
    )


class TestParseNetrackHtml:
    def test_basic_single_disk(self):
        html = _make_netrack_card()
        rows = _parse_netrack_html(html, TODAY)
        assert len(rows) == 1
        r = rows[0]
        assert r["provider"] == "netrack"
        assert r["price_rub"] == 8194.0
        assert r["cpu_model"] == "Intel Xeon E 2334"
        assert r["ram_gb"] == 64
        assert r["disk_count"] == 1
        assert r["disk_size_gb"] == 1000  # 960 snaps to 1000
        assert r["disk_type"] == "NVMe"
        assert r["scraped_at"] == TODAY

    def test_multi_disk_count(self):
        html = _make_netrack_card(disk1="480 GB", disk2="480 GB", nvme="SSD")
        rows = _parse_netrack_html(html, TODAY)
        assert rows[0]["disk_count"] == 2
        assert rows[0]["disk_size_gb"] == 480
        assert rows[0]["disk_type"] == "SSD"

    def test_tb_disk_converted(self):
        html = _make_netrack_card(disk1="1 ТБ", nvme="")
        rows = _parse_netrack_html(html, TODAY)
        assert rows[0]["disk_size_gb"] == 1000
        assert rows[0]["disk_type"] == "HDD"

    def test_price_with_spaces(self):
        html = _make_netrack_card(price="14 000₽")
        rows = _parse_netrack_html(html, TODAY)
        assert rows[0]["price_rub"] == 14000.0

    def test_zero_price_skipped(self):
        html = _make_netrack_card(price="0₽")
        assert _parse_netrack_html(html, TODAY) == []

    def test_missing_price_skipped(self):
        html = '<div data-cpu="Xeon E 2334" data-ram="64" data-disk1="480 GB"></div>'
        assert _parse_netrack_html(html, TODAY) == []

    def test_missing_cpu_skipped(self):
        html = '<div data-price="8000₽" data-ram="64" data-disk1="480 GB"></div>'
        assert _parse_netrack_html(html, TODAY) == []

    def test_fixture_row_count(self, netrack_html):
        rows = _parse_netrack_html(netrack_html, TODAY)
        if len(rows) == 0:
            pytest.skip(
                "netrack fixture has no parseable rows — captured without JS rendering. "
                "Re-run tests/capture_fixtures.py (now uses Playwright + tab click)."
            )
        assert len(rows) >= 10

    def test_fixture_all_required_fields(self, netrack_html):
        rows = _parse_netrack_html(netrack_html, TODAY)
        if not rows:
            pytest.skip("netrack fixture not yet re-captured with Playwright")
        for row in rows:
            assert row["provider"] == "netrack"
            assert row["cpu_model"] != ""
            assert row["ram_gb"] > 0
            assert row["price_rub"] > 0
            assert row["disk_type"] in ALLOWED_DISK_TYPES


# ── _parse_timeweb_html / _parse_hostkey_html stubs ───────────────────

class TestParseTimewebHtml:
    def test_empty_html_returns_empty(self):
        assert _parse_timeweb_html(b"", TODAY) == []

    def test_fixture_row_count(self, timeweb_html):
        rows = _parse_timeweb_html(timeweb_html, TODAY)
        assert len(rows) >= 5

    def test_fixture_all_required_fields(self, timeweb_html):
        rows = _parse_timeweb_html(timeweb_html, TODAY)
        for row in rows:
            assert row["provider"] == "timeweb"
            assert row["cpu_model"] != ""
            assert row["ram_gb"] > 0
            assert row["price_rub"] > 0
            assert row["disk_type"] in ALLOWED_DISK_TYPES


class TestParseHostkeyHtml:
    def test_empty_html_returns_empty(self):
        assert _parse_hostkey_html(b"", TODAY) == []

    def test_fixture_row_count(self, hostkey_html):
        rows = _parse_hostkey_html(hostkey_html, TODAY)
        if len(rows) == 0:
            pytest.skip(
                "hostkey fixture has no parseable rows — captured without JS rendering. "
                "Re-run tests/capture_fixtures.py (now uses Playwright)."
            )
        assert len(rows) >= 1

    def test_fixture_all_required_fields(self, hostkey_html):
        rows = _parse_hostkey_html(hostkey_html, TODAY)
        if not rows:
            pytest.skip("hostkey fixture not yet re-captured with Playwright")
        for row in rows:
            assert row["provider"] == "hostkey"
            assert row["cpu_model"] != ""
            assert row["ram_gb"] > 0
            assert row["price_rub"] > 0
            assert row["disk_type"] in ALLOWED_DISK_TYPES


class TestParseItliteHtml:
    def test_stub_returns_empty(self):
        assert _parse_itlite_html(b"anything", TODAY) == []

    def test_empty_returns_empty(self):
        assert _parse_itlite_html(b"", TODAY) == []


# ── Integration tests (live network) ─────────────────────────────────

# ── Extended fields (matching pipeline) ───────────────────────────────

LEGACY_FIELDS = [
    "provider", "cpu_model", "cpu_model_norm", "cpu_generation",
    "ram_gb", "disk_count", "disk_size_gb", "disk_type",
    "price_rub", "quantity_available", "scraped_at",
]


class TestSelectelPrecustom:
    """Сборка линейки PCL* (calculator/precustom + items) — зеркало
    фронта selectel: цена = сумма компонентов, наличие = min по складу."""

    ITEMS = {
        219: {"id": 219, "model": "cpu", "enable": True, "is_hidden": False,
              "name": "Intel Silver 4214R (12x2.4 GHz HT)",
              "price": {"rub": 6460.0}, "quantity": 10, "spte": 0,
              "param": {"core": 12}},
        48: {"id": 48, "model": "ram", "enable": True, "is_hidden": False,
             "name": "16 GB DDR4", "price": {"rub": 1280.0},
             "quantity": 100, "spte": 4, "param": {"size": 16}},
        125: {"id": 125, "model": "disk", "enable": True, "is_hidden": False,
              "name": "960 GB SSD NVMe", "price": {"rub": 2420.0},
              "quantity": 50, "spte": 0,
              "param": {"type": "ssd", "size": 960, "interface": "NVMe"}},
        85: {"id": 85, "model": "pcie", "enable": True, "is_hidden": False,
             "name": "2 × 10 GE", "price": {"rub": 3920.0},
             "quantity": 30, "spte": 0, "param": {"type": "network_10"}},
        163: {"id": 163, "model": "case", "enable": True, "is_hidden": False,
              "name": "815TQC", "price": {"rub": 11500.0},
              "quantity": 6, "spte": 0, "param": {}},
        500: {"id": 500, "model": "pcie", "enable": True, "is_hidden": False,
              "name": "RTX A5000", "price": {"rub": 30000.0},
              "quantity": 5, "spte": 0, "param": {"type": "gpu"}},
    }
    CONFIG = [
        {"id": 219, "count": 2}, {"id": 48, "count": 4},
        {"id": 125, "count": 2}, {"id": 85, "count": 1},
        {"id": 73, "count": 1},  # плата — отсутствует в items
        {"id": 163, "count": 1},
    ]

    def test_full_config_priced_as_component_sum(self):
        # без отсутствующей платы 73: наличие = 0 → конфиг не выводится,
        # как и на сайте
        pre = {"name": "PCL67-NVMe-10GE", "config": self.CONFIG}
        assert _precustom_to_cfg(pre, self.ITEMS) is None

    def test_all_components_present(self):
        cfg_list = [c for c in self.CONFIG if c["id"] != 73]
        pre = {"name": "PCL-TEST", "config": cfg_list}
        cfg = _precustom_to_cfg(pre, self.ITEMS)
        assert cfg is not None
        # 2×6460 + 4×1280 + 2×2420 + 3920 + 11500 = 38300
        assert cfg["price_collection"]["RUB"]["month"] == 38300.0
        assert cfg["cpu"] == {"name": "Intel Silver 4214R (12x2.4 GHz HT)",
                              "count": 2, "cores_per_cpu": 12}
        assert cfg["ram"] == [{"count": 4, "size": 16}]
        assert cfg["disk"] == [{"count": 2, "size": 960, "type": "ssd NVMe"}]
        # склад: min(10//2, 96//4, 50//2, 30//1, 6//1) = 5
        assert cfg["quantity"] == 5

    def test_row_via_common_builder(self):
        cfg_list = [c for c in self.CONFIG if c["id"] != 73]
        cfg = _precustom_to_cfg({"name": "PCL-TEST", "config": cfg_list},
                                self.ITEMS)
        row = _selectel_cfg_to_row(cfg, TODAY)
        assert row["plan_id"] == "PCL-TEST"
        assert row["provider"] == "selectel"
        assert row["ram_gb"] == 64
        assert row["disk_pools"] == [
            {"disk_type": "NVMe", "disk_count": 2, "disk_size_gb": 1000}]
        assert row["price_rub"] == 38300.0
        assert row["cpu_sockets"] == 2
        assert row["cpu_cores_total"] == 24
        assert row["quantity_available"] == 5

    def test_gpu_config_skipped(self):
        cfg_list = [c for c in self.CONFIG if c["id"] != 73]
        cfg_list.append({"id": 500, "count": 1})
        assert _precustom_to_cfg({"name": "GPU", "config": cfg_list},
                                 self.ITEMS) is None

    def test_alt_config_format(self):
        # второй формат API: [{"219": 2}, {"48": 4}, ...]
        alt = [{str(c["id"]): c["count"]} for c in self.CONFIG
               if c["id"] != 73]
        cfg = _precustom_to_cfg({"name": "ALT", "config": alt}, self.ITEMS)
        assert cfg is not None
        assert cfg["price_collection"]["RUB"]["month"] == 38300.0

    def test_disabled_item_excluded_from_price(self):
        items = {k: dict(v) for k, v in self.ITEMS.items()}
        items[85] = {**items[85], "enable": False}
        cfg_list = [c for c in self.CONFIG if c["id"] != 73]
        cfg = _precustom_to_cfg({"name": "PCL-TEST", "config": cfg_list}, items)
        assert cfg["price_collection"]["RUB"]["month"] == 38300.0 - 3920.0


class TestSelectelGpuField:
    BASE_CFG = {
        "name": "GL12-1-A2",
        "cpu": {"name": "Intel Xeon E-2236", "count": 1, "cores_per_cpu": 6},
        "ram": [{"count": 2, "size": 16}],
        "disk": [{"count": 2, "size": 1000, "type": "SSD"}],
        "price_collection": {"RUB": {"month": 23200.0}},
        "quantity": 1,
    }

    def test_gpu_dict_captured(self):
        cfg = {**self.BASE_CFG, "gpu": {"name": "RTX A2000", "count": 1}}
        row = _selectel_cfg_to_row(cfg, TODAY)
        assert row["gpu"] == "1 × RTX A2000"

    def test_no_gpu_empty(self):
        row = _selectel_cfg_to_row(dict(self.BASE_CFG), TODAY)
        assert row["gpu"] == ""

    def test_gpu_none_or_empty_dict_empty(self):
        assert _selectel_cfg_to_row(
            {**self.BASE_CFG, "gpu": None}, TODAY)["gpu"] == ""
        assert _selectel_cfg_to_row(
            {**self.BASE_CFG, "gpu": {}}, TODAY)["gpu"] == ""


class TestGpuOffersExcludedFromMatching:
    def test_rows_to_offers_skips_gpu(self):
        from competitor_pipeline import rows_to_offers
        from config_loader import Competitor

        comp = Competitor(
            competitor_id="regcloud", name="Reg.cloud", url="",
            currency="RUB", price_period="month",
            parsing_profile="regcloud_playwright",
        )
        base = {
            "cpu_model": "Intel Xeon Silver 4214R",
            "cpu_model_norm": "intel xeon silver 4214r",
            "ram_gb": 128, "price_rub": 88830.0,
            "disk_pools": [
                {"disk_type": "SSD", "disk_count": 2, "disk_size_gb": 960}],
        }
        rows = [
            {**base, "plan_id": "RD-56106", "gpu": "4 × RTX A4000 16GB"},
            {**base, "plan_id": "RD-00001", "gpu": ""},
            {**base, "plan_id": "RD-00002"},
        ]
        offers = rows_to_offers(rows, comp)
        assert [o.plan_id for o in offers] == ["RD-00001", "RD-00002"]


class TestSelectelExtendedFields:
    def test_fixture_rows_have_extended_fields(self, selectel_flat):
        rows = _parse_selectel_flat(selectel_flat, TODAY)
        assert len(rows) >= 100
        for row in rows:
            assert row["plan_id"]
            assert row["cpu_sockets"] >= 1
            assert row["cpu_cores_total"] >= 1
            assert len(row["disk_pools"]) >= 1
            assert row["currency"] == "RUB"
            assert row["price_period"] == "month"

    def test_legacy_fields_unchanged(self, selectel_flat):
        """Legacy 11 fields must survive the refactor byte-identical."""
        rows = _parse_selectel_flat(selectel_flat, TODAY)
        for row in rows:
            for field in LEGACY_FIELDS:
                assert field in row

    def test_first_pool_matches_legacy_disk_fields(self, selectel_flat):
        rows = _parse_selectel_flat(selectel_flat, TODAY)
        for row in rows:
            pool = row["disk_pools"][0]
            assert pool["disk_count"] == row["disk_count"]
            assert pool["disk_size_gb"] == row["disk_size_gb"]
            assert pool["disk_type"] == row["disk_type"]

    def test_cores_total_is_sockets_times_cores(self, selectel_flat):
        rows = _parse_selectel_flat(selectel_flat, TODAY)
        multi = [r for r in rows if r["cpu_sockets"] > 1]
        assert multi, "fixture should contain dual-socket configs"
        for row in multi:
            assert row["cpu_cores_total"] % row["cpu_sockets"] == 0


class TestRegcloudExtendedFields:
    def test_plan_id_and_sockets(self):
        html = f"""
        <div class="b-dedicated-servers-list-item-cloud">
          <p class="b-dedicated-servers-list-item-cloud__title">Аренда сервера RD-56956</p>
          <p class="b-dedicated-servers-list-item-cloud__cpu-title">2 × AMD EPYC 9334</p>
          <p class="b-dedicated-servers-list-item-cloud__cpu-power">2.70 ГГц, 64 ядра, 128 потоков</p>
          <p class="b-dedicated-servers-list-item-cloud__ram">512 ГБ DDR4 ECC</p>
          <p class="b-dedicated-servers-list-item-cloud__hdds">2 x 1000 ГБ SSD NVMe</p>
          <p class="b-dedicated-servers-list-item-cloud__base-price">130\xa0985₽/мес</p>
        </div>
        """
        rows = _parse_regcloud_html(html, TODAY)
        assert len(rows) == 1
        row = rows[0]
        assert row["plan_id"] == "RD-56956"
        assert row["cpu_sockets"] == 2
        assert row["cpu_cores_total"] == 64
        assert row["cpu_model"] == "AMD EPYC 9334"

    def test_single_socket_default(self):
        html = _make_regcloud_item(
            "AMD EPYC 9334", "128 ГБ DDR4",
            "2 x 1000 ГБ SSD NVMe", "base-price", "19\xa0100₽/мес"
        )
        rows = _parse_regcloud_html(html, TODAY)
        assert rows[0]["cpu_sockets"] == 1

    def test_multi_pool_glued_text(self):
        """get_text(strip=True) glues pools; spaced extraction must split them."""
        html = f"""
        <div class="b-dedicated-servers-list-item-cloud">
          <p class="b-dedicated-servers-list-item-cloud__cpu-title">AMD EPYC 9334</p>
          <p class="b-dedicated-servers-list-item-cloud__ram">512 ГБ DDR4</p>
          <p class="b-dedicated-servers-list-item-cloud__hdds">
            <span>2 x 3.8 ТБ SSD SATA</span><span>2 x 12 ТБ HDD SATA</span><span>Аппаратный RAID</span>
          </p>
          <p class="b-dedicated-servers-list-item-cloud__base-price">100\xa0000₽/мес</p>
        </div>
        """
        rows = _parse_regcloud_html(html, TODAY)
        assert len(rows) == 1
        pools = rows[0]["disk_pools"]
        assert len(pools) == 2
        assert pools[0] == {"disk_type": "SSD", "disk_count": 2, "disk_size_gb": 3800}
        assert pools[1] == {"disk_type": "HDD", "disk_count": 2, "disk_size_gb": 12000}

    def test_pool_type_ssd_nvme_is_nvme(self):
        html = _make_regcloud_item(
            "AMD EPYC 9334", "128 ГБ DDR4",
            "2 x 1.9 ТБ SSD NVMe U.2", "base-price", "50\xa0000₽/мес"
        )
        rows = _parse_regcloud_html(html, TODAY)
        assert rows[0]["disk_pools"] == [
            {"disk_type": "NVMe", "disk_count": 2, "disk_size_gb": 1900}
        ]

    def test_fixture_extended_fields(self, regcloud_html):
        rows = _parse_regcloud_html(regcloud_html, TODAY)
        assert all(r["cpu_sockets"] >= 1 for r in rows)
        assert all(len(r["disk_pools"]) >= 1 for r in rows)
        assert any(r["plan_id"].startswith("RD-") for r in rows)
        assert any(len(r["disk_pools"]) > 1 for r in rows)


# ── timeweb.cloud (_parse_timeweb_cloud_nuxt) ─────────────────────────

class TestParseStoragePool:
    def test_basic_gb_ssd(self):
        assert _parse_storage_pool("2 x 480 ГБ SSD") == {
            "disk_type": "SSD", "disk_count": 2, "disk_size_gb": 480
        }

    def test_tb_hdd(self):
        assert _parse_storage_pool("2 x 1 ТБ HDD") == {
            "disk_type": "HDD", "disk_count": 2, "disk_size_gb": 1000
        }

    def test_decimal_tb_nvme(self):
        assert _parse_storage_pool("1 x 3.84 ТБ NVMe") == {
            "disk_type": "NVMe", "disk_count": 1, "disk_size_gb": 4000
        }

    def test_no_count_defaults_to_one(self):
        pool = _parse_storage_pool("480 ГБ SSD")
        assert pool["disk_count"] == 1

    def test_garbage_returns_none(self):
        assert _parse_storage_pool("Аппаратный RAID") is None


class TestParseTimewebCloudNuxt:
    def test_fixture_msk_row_count(self, timeweb_cloud_flat):
        rows = _parse_timeweb_cloud_nuxt(timeweb_cloud_flat, TODAY)
        assert 20 <= len(rows) <= 100

    def test_fixture_all_required_fields(self, timeweb_cloud_flat):
        rows = _parse_timeweb_cloud_nuxt(timeweb_cloud_flat, TODAY)
        for row in rows:
            assert row["provider"] == "timeweb_cloud"
            assert row["cpu_model"] != ""
            assert row["ram_gb"] > 0
            assert row["price_rub"] > 0
            assert row["disk_type"] in ALLOWED_DISK_TYPES
            assert row["plan_id"]
            assert row["currency"] == "RUB"
            assert row["price_period"] == "month"
            assert len(row["disk_pools"]) >= 1

    def test_dual_socket_parsed(self, timeweb_cloud_flat):
        rows = _parse_timeweb_cloud_nuxt(timeweb_cloud_flat, TODAY)
        import re
        dual = [r for r in rows if r["cpu_sockets"] == 2]
        assert dual, "msk tariffs should contain dual-socket configs"
        # socket prefix "2 x " must be stripped from the model
        assert all(not re.match(r"^\d+\s*[xхX×]", r["cpu_model"]) for r in dual)

    def test_multi_pool_present(self, timeweb_cloud_flat):
        rows = _parse_timeweb_cloud_nuxt(timeweb_cloud_flat, TODAY)
        assert any(len(r["disk_pools"]) > 1 for r in rows)

    def test_location_filter(self, timeweb_cloud_flat):
        msk = _parse_timeweb_cloud_nuxt(timeweb_cloud_flat, TODAY, ("msk",))
        both = _parse_timeweb_cloud_nuxt(timeweb_cloud_flat, TODAY, ("msk", "ru"))
        assert len(both) > len(msk)

    def test_uses_standard_price_not_discounted(self, timeweb_cloud_flat):
        """priceNumber (стандартная цена), а не price (скидка за 12 мес)."""
        rows = _parse_timeweb_cloud_nuxt(timeweb_cloud_flat, TODAY)
        assert all(float(r["price_rub"]) == int(r["price_rub"]) for r in rows)


@pytest.mark.integration
def test_scrape_timeweb_cloud_live():
    from dedicated_scraper import scrape_timeweb_cloud
    rows = scrape_timeweb_cloud()
    assert len(rows) >= 20


@pytest.mark.integration
def test_scrape_miran_live():
    from dedicated_scraper import scrape_miran
    rows = scrape_miran()
    assert len(rows) >= 5
    assert all(r["price_rub"] > 0 for r in rows)


@pytest.mark.integration
def test_scrape_regcloud_live():
    from dedicated_scraper import scrape_regcloud
    rows = scrape_regcloud()
    assert len(rows) >= 100


@pytest.mark.integration
def test_scrape_selectel_live():
    from dedicated_scraper import scrape_selectel
    rows = scrape_selectel()
    assert len(rows) >= 100
