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

    def test_single_disk_ssd_snaps(self):
        art = _make_1dedic_article(
            "Intel Xeon E3-1230 V5 3.4 ГГц, 4 ядра",
            "16 Гб", "750 Гб SSD", "5 368"
        )
        row = _parse_1dedic_article(art, TODAY)
        assert row is not None
        assert row["disk_count"] == 1
        assert row["disk_size_gb"] == 1000  # 750 snaps to nearest standard
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
        assert rows[0]["disk_size_gb"] == 4000  # 3800 snaps to 4000
        assert rows[0]["disk_type"] == "NVMe"

    def test_decimal_tb_19(self):
        html = _make_regcloud_item(
            "Intel Xeon Gold 6342", "256 ГБ DDR4",
            "2 x 1.9 ТБ SSD NVMe", "base-price", "50\xa0000₽/мес"
        )
        rows = _parse_regcloud_html(html, TODAY)
        assert rows[0]["disk_size_gb"] == 2000  # 1900 snaps to 2000

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
