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
    _selectel_stock_and_price,
    _selectel_storefront_visible,
    _selectel_visible_locations,
    _parse_timeweb_cloud_nuxt,
    _parse_timeweb_html,
    _parse_hostkey_html,
    _parse_itlite_html,
    _resolve_nuxt,
)

TODAY = "2026-06-02"
FIXTURES = Path(__file__).parent / "fixtures"
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

    def test_period_price_layout_2026_09(self):
        # вёрстка 2026-09: _per-months_one на родителе, цена месяца —
        # __price-value[data-period-price]. Кейс RD-30055: 5 740, а не
        # перечёркнутые 8 200; RD-58446: не брать соседнюю цену за день.
        discounted = """
        <div class="b-dedicated-servers-list-item-cloud">
          <p class="b-dedicated-servers-list-item-cloud__cpu-title">Xeon E3-1230v3</p>
          <p class="b-dedicated-servers-list-item-cloud__ram">16 ГБ DDR3</p>
          <p class="b-dedicated-servers-list-item-cloud__hdds">2 x 1 ТБ HDD SATA</p>
          <div class="b-dedicated-servers-list-item-cloud__price b-dedicated-servers-list-item-cloud__price_per-months_one b-dedicated-servers-list-item-cloud__price_type_discount">
            <div class="b-dedicated-servers-list-item-cloud__price-value" data-period-price="">5 740 <span>₽</span> /мес</div>
            <p class="b-dedicated-servers-list-item-cloud__base-price">8 200 <span>₽</span> /мес</p>
          </div>
        </div>
        """
        per_day = """
        <div class="b-dedicated-servers-list-item-cloud">
          <p class="b-dedicated-servers-list-item-cloud__cpu-title">2 × AMD EPYC 9654</p>
          <p class="b-dedicated-servers-list-item-cloud__ram">1536 ГБ DDR5</p>
          <p class="b-dedicated-servers-list-item-cloud__hdds">2 x 3.8 ТБ SSD NVMe</p>
          <div class="b-dedicated-servers-list-item-cloud__price b-dedicated-servers-list-item-cloud__price_per-months_one">
            <p class="b-dedicated-servers-list-item-cloud__price-value b-dedicated-servers-list-item-cloud__price-value_per-day" data-one-day-price="">20 000 ₽/день</p>
            <div class="b-dedicated-servers-list-item-cloud__price-value" data-period-price="">588 500 <span>₽</span> /мес</div>
          </div>
        </div>
        """
        rows = _parse_regcloud_html(discounted + per_day, TODAY)
        assert [r["price_rub"] for r in rows] == [5740.0, 588500.0]

    def test_real_markup_2026_09(self):
        # Регрессия на живой разметке 14.09.2026 (tests/fixtures/
        # regcloud_dedicated_2026_09.html): парсер из 5e49b07 терял карточку
        # без скидки (RD-55039: нет ни __current-price, ни __base-price,
        # цена только в __price-value[data-period-price]) и брал у скидочной
        # RD-30055 перечёркнутые 8 200 вместо 5 740.
        from storefront_check import diff_regcloud

        html = (FIXTURES / "regcloud_dedicated_2026_09.html").read_text(
            encoding="utf-8")
        rows = {r["plan_id"]: r for r in _parse_regcloud_html(html, TODAY)}
        assert set(rows) == {"RD-55039", "RD-30055"}

        plain = rows["RD-55039"]
        assert plain["price_rub"] == 33300.0
        # паритет с витриной (15.09): без скидки — без пометки; со скидкой —
        # в таблице 5 740 как на карточке, условия мелким текстом
        assert plain["price_note"] == ""
        assert plain["price_list_rub"] == 33300.0
        sale = rows["RD-30055"]
        assert sale["price_rub"] == 5740.0
        assert sale["price_list_rub"] == 8200.0
        assert sale["price_note"] == "скидка 30\u00a0%, было 8\u00a0200"
        assert plain["cpu_model"] == "Intel Xeon Gold 5218R"
        assert plain["cpu_sockets"] == 2
        assert plain["cpu_cores_total"] == 40
        assert plain["ram_gb"] == 64
        assert plain["disk_pools"] == [
            {"disk_type": "SSD", "disk_count": 2, "disk_size_gb": 480}]

        sale = rows["RD-30055"]
        assert sale["price_rub"] == 5740.0
        assert sale["cpu_model"] == "Xeon E3-1230v3"
        assert sale["cpu_sockets"] == 1
        assert sale["cpu_cores_total"] == 4
        assert sale["ram_gb"] == 16
        assert sale["disk_pools"] == [
            {"disk_type": "HDD", "disk_count": 2, "disk_size_gb": 1000}]

        # та же разметка глазами сверки с витриной: data-price = наша цена
        assert diff_regcloud(list(rows.values()), html) == []

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


class TestSelectelStorefrontFilter:
    """Зеркало витрины selectel (чанк B3qo2xyo): наличие и цена считаются
    только по локациям msk/spb/nsk с visibility=everywhere и
    primary_resource_ordering=enabled; API отдаёт available[] по всем ДЦ
    (Ташкент, Алматы, Найроби) и распроданные конфиги — сайт их не показывает.
    Фикстуры — реальные ответы API от 2026-09-14, урезанные."""

    # uuid из фикстуры selectel_location.json
    MSK1 = "61c97311-bb14-5679-99fc-98497a701292"
    MSK4 = "b93f81b1-ddce-4988-a2a7-f58fc46efdb4"   # everywhere, enabled_in_admin
    NSK1 = "9b177ad9-d90a-4f08-ae1f-008891062a12"
    TAS2 = "324f8b40-31c4-4cd2-8529-d1906c7cce36"
    ALM1 = "07ff5ae1-7e8b-4c85-af2c-219798aa0d46"

    @pytest.fixture
    def visible(self, selectel_locations):
        return _selectel_visible_locations(selectel_locations)

    def test_visible_locations_rule(self, visible):
        # только РФ-площадки, включённые для заказа с сайта
        assert set(visible.values()) == {
            "MSK-1", "MSK-2", "MSK-3", "MSK-7",
            "SPB-2", "SPB-3", "SPB-4", "SPB-5", "NSK-1",
        }
        assert self.MSK4 not in visible          # enabled_in_admin
        assert self.TAS2 not in visible          # Ташкент
        assert self.ALM1 not in visible          # Алматы

    def test_visible_locations_prefix_and_flags(self):
        locs = [
            {"uuid": "a", "name": "MSK-9", "visibility": "everywhere",
             "primary_resource_ordering": "enabled"},
            {"uuid": "b", "name": "MSK-8", "visibility": "only_in_admin",
             "primary_resource_ordering": "enabled"},
            {"uuid": "c", "name": "spb-x", "visibility": "everywhere",
             "primary_resource_ordering": "enabled"},
            {"uuid": "d", "name": "VRRP MSK", "visibility": "everywhere",
             "primary_resource_ordering": "enabled"},
            {"uuid": "e", "name": "TAS-1", "visibility": "everywhere",
             "primary_resource_ordering": "enabled"},
            "мусор",
        ]
        assert _selectel_visible_locations(locs) == {"a": "MSK-9", "c": "spb-x"}

    def test_only_tashkent_hidden(self, selectel_api_configs, visible):
        # EL46-NVMe: единственный экземпляр в TAS-2 — на сайте карточки нет,
        # а старое правило матчило её с MIR-109
        cfg = selectel_api_configs["EL46-NVMe"]
        assert cfg["is_order"] and not cfg["is_preorder"]
        assert [a for a in cfg["available"] if a["count"]] == [
            {"location": self.TAS2, "count": 1}]
        assert not _selectel_storefront_visible(cfg, visible)
        assert _selectel_storefront_visible(cfg)  # без локаций — как раньше

    def test_moscow_stock_only_counts_and_prices(self, selectel_api_configs, visible):
        # EL42-NVMe: MSK-1:1 + ALM-1:14 + TAS-2:3 → бейдж «1 шт.», цена
        # московская (у MSK-1 нет локальной цены → price_collection 18 400,
        # у Ташкента 33 500)
        cfg = selectel_api_configs["EL42-NVMe"]
        assert _selectel_storefront_visible(cfg, visible)
        row = _selectel_cfg_to_row(cfg, TODAY, visible)
        assert row["quantity_available"] == 1
        assert row["price_rub"] == 18400.0
        assert row["stock_by_location"] == {"MSK-1": 1}
        # старое правило: все ДЦ
        old = _selectel_cfg_to_row(cfg, TODAY)
        assert old["quantity_available"] == 18
        assert "stock_by_location" not in old

    def test_foreign_local_price_ignored(self, selectel_api_configs, visible):
        # EL45-NVMe: MSK-1:1 + TAS-2:23; локальные цены 38 800–43 600 только
        # у зарубежных ДЦ → цена сайта = price_collection 23 100
        cfg = selectel_api_configs["EL45-NVMe"]
        row = _selectel_cfg_to_row(cfg, TODAY, visible)
        assert row["quantity_available"] == 1
        assert row["price_rub"] == 23100.0

    def test_admin_only_location_not_counted(self, selectel_api_configs, visible):
        # MSK-4: visibility=everywhere, но primary_resource_ordering=
        # enabled_in_admin — сайт её не считает
        cfg = dict(selectel_api_configs["EL46-NVMe"])
        cfg["available"] = cfg["available"] + [{"location": self.MSK4, "count": 5}]
        assert not _selectel_storefront_visible(cfg, visible)
        stock = _selectel_stock_and_price(cfg, visible)
        assert stock["quantity"] == 0
        assert stock["stock_by_location"] == {}
        assert _selectel_stock_and_price(cfg, None)["quantity"] == 6

    def test_price_from_location_price_collection(self, selectel_api_configs, visible):
        # EL52-NVMe: price_collection 49 000, но NSK-1 (1 шт.) продаётся за
        # 34 400 → сайт пишет «от 34 400»
        cfg = selectel_api_configs["EL52-NVMe"]
        row = _selectel_cfg_to_row(cfg, TODAY, visible)
        assert row["price_rub"] == 34400.0
        assert row["stock_by_location"] == {
            "MSK-7": 81, "MSK-2": 2, "SPB-4": 66, "SPB-2": 5, "NSK-1": 1}
        assert row["quantity_available"] == 155
        assert _selectel_cfg_to_row(cfg, TODAY)["price_rub"] == 49000.0

    def test_price_note_names_location_of_shown_price(self, selectel_api_configs, visible):
        # EL52-NVMe: карточка «от 34 400» — это NSK-1; в остальных локациях
        # с остатком цена другая → пометка называет, откуда цена
        row = _selectel_cfg_to_row(selectel_api_configs["EL52-NVMe"], TODAY, visible)
        assert row["price_note"].startswith("цена по NSK-1; ")
        assert "MSK-7 — 49\u00a0000" in row["price_note"]
        assert row["price_list_rub"] == 34400.0

    def test_price_note_empty_when_single_price(self, selectel_api_configs, visible):
        # EL42-NVMe: одна локация витрины с остатком (MSK-1), цена одна —
        # оговорок нет
        row = _selectel_cfg_to_row(selectel_api_configs["EL42-NVMe"], TODAY, visible)
        assert row["price_note"] == ""
        assert _selectel_cfg_to_row(selectel_api_configs["EL42-NVMe"], TODAY)["price_note"] == ""

    def test_price_note_preorder(self, selectel_api_configs, visible):
        cfg = dict(selectel_api_configs["EL46-NVMe"])
        cfg["is_preorder"] = True
        cfg["available"] = [{"location": self.MSK1, "count": 0}]
        row = _selectel_cfg_to_row(cfg, TODAY, visible)
        assert row["quantity_available"] is None
        assert row["price_note"] == "предзаказ"

    def test_local_price_without_stock_not_used(self, selectel_api_configs, visible):
        # если единственный дешёвый ДЦ пуст, цена карточки — по локациям
        # с остатком
        cfg = dict(selectel_api_configs["EL52-NVMe"])
        cfg["available"] = [
            {**a, "count": 0} if a["location"] == self.NSK1 else a
            for a in cfg["available"]]
        assert _selectel_cfg_to_row(cfg, TODAY, visible)["price_rub"] == 49000.0

    def test_preorder_listed_without_stock(self, selectel_api_configs, visible):
        # предзаказ: карточка видна, если локация витрины есть в available[]
        # хотя бы с нулём; цена — минимум из price_collection и локальных цен
        cfg = dict(selectel_api_configs["EL46-NVMe"])
        cfg["is_preorder"] = True
        cfg["available"] = [a for a in cfg["available"] if a["location"] == self.TAS2]
        assert not _selectel_storefront_visible(cfg, visible)  # только TAS-2
        cfg["available"] = cfg["available"] + [{"location": self.MSK1, "count": 0}]
        assert _selectel_storefront_visible(cfg, visible)
        row = _selectel_cfg_to_row(cfg, TODAY, visible)
        assert row["quantity_available"] is None
        assert row["price_rub"] == 33700.0
        assert row["stock_by_location"] == {}

    def test_sold_out_hidden(self, selectel_api_configs, visible):
        # DL23: 4 шт. только в Алматы (29 800 там) — на сайте нет
        cfg = selectel_api_configs["DL23"]
        assert not _selectel_storefront_visible(cfg, visible)
        cfg = dict(selectel_api_configs["EL11-SSD"])
        cfg["available"] = [{**a, "count": 0} for a in cfg["available"]]
        assert not _selectel_storefront_visible(cfg, visible)
        assert not _selectel_storefront_visible(cfg)

    def test_not_orderable_hidden(self, selectel_api_configs, visible):
        cfg = {**selectel_api_configs["EL11-SSD"], "is_order": False}
        assert not _selectel_storefront_visible(cfg, visible)

    def test_spb_and_msk_summed(self, selectel_api_configs, visible):
        # EL11-SSD: SPB-5:10 + MSK-2:1 + TAS-2:5 → 11 (было 16)
        row = _selectel_cfg_to_row(selectel_api_configs["EL11-SSD"], TODAY, visible)
        assert row["quantity_available"] == 11
        assert row["price_rub"] == 12800.0

    def test_fallback_without_locations(self, selectel_api_configs, monkeypatch):
        # location недоступен → старое правило, Selectel не обнуляется,
        # в лог — предупреждение
        import dedicated_scraper as ds

        class Resp:
            def __init__(self, payload):
                self._payload = payload

            def raise_for_status(self):
                pass

            def json(self):
                return self._payload

        def fake_get(url, *a, **kw):
            if url == ds.SELECTEL_PUB_LOCATION:
                raise ConnectionError("нет сети")
            if url == ds.SELECTEL_CHIP_API:
                return Resp({"result": []})
            assert url == ds.SELECTEL_PUB_API
            return Resp({"result": list(selectel_api_configs.values())})

        monkeypatch.setattr(ds.requests, "get", fake_get)
        monkeypatch.setattr(ds.time, "sleep", lambda *_: None)
        printed = []
        monkeypatch.setattr("builtins.print", lambda *a, **k: printed.append(" ".join(map(str, a))))
        rows = ds._scrape_selectel_api()
        by_plan = {r["plan_id"]: r for r in rows}
        assert "EL46-NVMe" in by_plan               # как раньше: Ташкент считается
        assert by_plan["EL42-NVMe"]["quantity_available"] == 18
        assert by_plan["EL52-NVMe"]["price_rub"] == 49000.0
        assert all("stock_by_location" not in r for r in rows)
        assert any("ПРЕДУПРЕЖДЕНИЕ" in line and "локаций" in line for line in printed)

    def test_api_flow_with_locations(self, selectel_api_configs, selectel_locations,
                                     monkeypatch):
        import dedicated_scraper as ds

        class Resp:
            def __init__(self, payload):
                self._payload = payload

            def raise_for_status(self):
                pass

            def json(self):
                return self._payload

        def fake_get(url, *a, **kw):
            if url == ds.SELECTEL_PUB_LOCATION:
                return Resp({"result": selectel_locations})
            if url == ds.SELECTEL_CHIP_API:
                return Resp({"result": []})
            assert url == ds.SELECTEL_PUB_API
            return Resp({"result": list(selectel_api_configs.values())})

        monkeypatch.setattr(ds.requests, "get", fake_get)
        rows = ds._scrape_selectel_api()
        by_plan = {r["plan_id"]: r for r in rows}
        assert set(by_plan) == {"EL42-NVMe", "EL45-NVMe", "EL52-NVMe",
                                "EL11-SSD", "AEL10-SSD"}
        assert by_plan["EL42-NVMe"]["quantity_available"] == 1
        assert by_plan["EL52-NVMe"]["price_rub"] == 34400.0


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


# Витрина по решению клиента (Миран в Санкт-Петербурге, 14.09.2026): payload
# timeweb.cloud содержит все ДЦ, «ru» = Санкт-Петербург, «msk» = Москва.
SPB = ("ru",)
MSK = ("msk",)
# Контрольные тарифы живого снимка 14.09.2026 (фикстура урезана из него).
# Цена = как на карточке по умолчанию (вкладка «12 Месяцев Скидка 10%»,
# Playwright 15.09.2026); помесячная (priceNumber) — в price_list_rub.
SPB_E2236_32 = ("E-2236 / 32 / 960", 14540.0)                    # preset 3871, за 12 мес 13 086
SPB_E2236_16 = ("E-2236 / 16 / 480", 11960.0)                    # preset 3247, за 12 мес 10 764
SPB_RYZEN = ("AMD Ryzen 9 7950X (16 ядер, 4.2-5.7 ГГц, 32 потока)", 37300.0)  # 5243, за 12 мес 33 570
MSK_E2236_32 = ("Intel Xeon E-2236 (6 ядер, 3.4-4.8 ГГц, 12 потоков) / 32 DDR4 "
                "/ 2 x 960 Гб SSD", 12720.0)                      # preset 6121, за 12 мес 11 448


def _plans(rows):
    return {(r["plan_id"], r["price_rub"]) for r in rows}


class TestParseTimewebCloudNuxt:
    def test_fixture_spb_row_count(self, timeweb_cloud_flat):
        """Снимок 14.09.2026: СПб = 68 тарифов, столько же у landing-api ru-1."""
        rows = _parse_timeweb_cloud_nuxt(timeweb_cloud_flat, TODAY, SPB)
        assert len(rows) == 68

    def test_fixture_all_required_fields(self, timeweb_cloud_flat):
        rows = _parse_timeweb_cloud_nuxt(timeweb_cloud_flat, TODAY, SPB)
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
        rows = _parse_timeweb_cloud_nuxt(timeweb_cloud_flat, TODAY, SPB)
        import re
        dual = [r for r in rows if r["cpu_sockets"] == 2]
        assert dual, "spb tariffs should contain dual-socket configs"
        # socket prefix "2 x " must be stripped from the model
        assert all(not re.match(r"^\d+\s*[xхX×]", r["cpu_model"]) for r in dual)

    def test_multi_pool_present(self, timeweb_cloud_flat):
        rows = _parse_timeweb_cloud_nuxt(timeweb_cloud_flat, TODAY, SPB)
        assert any(len(r["disk_pools"]) > 1 for r in rows)

    def test_location_filter_spb(self, timeweb_cloud_flat):
        """СПб: питерский 3871 за 14 540 входит, московский 6121 за 12 720 — нет."""
        plans = _plans(_parse_timeweb_cloud_nuxt(timeweb_cloud_flat, TODAY, SPB))
        assert SPB_E2236_32 in plans
        assert SPB_E2236_16 in plans
        assert SPB_RYZEN in plans
        assert MSK_E2236_32 not in plans
        assert all(p != 11448.0 for _, p in plans)

    def test_location_filter_msk(self, timeweb_cloud_flat):
        """Обратная сторона: в Москве 6121 есть, а 3871/Ryzen СПб нет."""
        plans = _plans(_parse_timeweb_cloud_nuxt(timeweb_cloud_flat, TODAY, MSK))
        assert MSK_E2236_32 in plans
        assert SPB_E2236_32 not in plans
        assert SPB_RYZEN not in plans

    def test_same_name_different_city_not_mixed(self, timeweb_cloud_flat):
        """«E-2236 / 16 / 480» есть и в СПб (3247), и в Москве (легаси 3853) —
        фильтр по ДЦ оставляет ровно одну строку с этим именем."""
        rows = _parse_timeweb_cloud_nuxt(timeweb_cloud_flat, TODAY, SPB)
        assert [r["plan_id"] for r in rows].count("E-2236 / 16 / 480") == 1

    def test_location_union(self, timeweb_cloud_flat):
        spb = _parse_timeweb_cloud_nuxt(timeweb_cloud_flat, TODAY, SPB)
        msk = _parse_timeweb_cloud_nuxt(timeweb_cloud_flat, TODAY, MSK)
        both = _parse_timeweb_cloud_nuxt(timeweb_cloud_flat, TODAY, ("ru", "msk"))
        assert len(both) == len(spb) + len(msk)

    def test_foreign_locations_excluded(self, timeweb_cloud_flat):
        """Фикстура содержит nl/pl — при СПб они не должны просачиваться."""
        spb = _parse_timeweb_cloud_nuxt(timeweb_cloud_flat, TODAY, SPB)
        everything = _parse_timeweb_cloud_nuxt(
            timeweb_cloud_flat, TODAY, ("ru", "msk", "nl", "pl"))
        assert len(everything) > len(spb) + len(
            _parse_timeweb_cloud_nuxt(timeweb_cloud_flat, TODAY, MSK))

    def test_uses_monthly_price_with_annual_in_note(self, timeweb_cloud_flat):
        """Решение клиента 21.09: в таблицу идёт ПОМЕСЯЧНАЯ цена (3871 =
        14 540) — по ней Светлана сверяет витрину; цена вкладки «12 мес
        −10 %» (13 086) уходит в пометку. Молча подменять цену нельзя."""
        rows = _parse_timeweb_cloud_nuxt(timeweb_cloud_flat, TODAY, SPB)
        assert all(float(r["price_rub"]) == int(r["price_rub"]) for r in rows)
        assert SPB_E2236_32 in _plans(rows)
        assert ("E-2236 / 32 / 960", 13086.0) not in _plans(rows)
        row = next(r for r in rows if r["plan_id"] == "E-2236 / 32 / 960")
        assert row["price_list_rub"] == 14540.0
        assert row["price_note"] == ("помесячно; при оплате за 12 мес — "
                                     "13\u00a0086 (−10\u00a0%)")

    def test_no_note_when_price_field_missing(self):
        """Нет поля price (или оно совпадает с priceNumber) — цена помесячная,
        пометки нет: ничего не выдумываем."""
        flat = [
            {"presets": 1}, [2],
            {"cpu": 3, "presetId": 4, "storageList": 5, "location": 6,
             "cpuParams": 7, "memoryCount": 8, "priceNumber": 9, "name": 10,
             "cpuCount": 11},
            "Intel Xeon E-2236", 3247, [12], "ru",
            "6 ядер, 3.4-4.8 ГГц, 12 потоков", 16, 11960,
            "E-2236 / 16 / 480", 6, "2 x 480 ГБ SSD",
        ]
        rows = _parse_timeweb_cloud_nuxt(flat, TODAY, SPB)
        assert rows[0]["price_rub"] == 11960.0
        assert rows[0]["price_list_rub"] == 11960.0
        assert rows[0]["price_note"] == ""

    def test_cores_from_cpu_params_not_bogus_cpu_count(self, timeweb_cloud_flat):
        """У части тарифов cpuCount забит константой 28 (E-2388G / 128 / 2N,
        Silver 4310, Gold 6312U …) — ядра берём из описания «8 ядер»."""
        rows = _parse_timeweb_cloud_nuxt(timeweb_cloud_flat, TODAY, SPB)
        e2388 = [r for r in rows if "E-2388G" in r["cpu_model"]]
        assert e2388, "фикстура должна содержать spb-тариф на E-2388G"
        assert all(r["cpu_cores_total"] == 8 for r in e2388)
        ryzen = [r for r in rows if r["plan_id"] == SPB_RYZEN[0]]
        assert [r["cpu_cores_total"] for r in ryzen] == [16]

    def test_dual_socket_cores_are_total(self, timeweb_cloud_flat):
        """cpuParams у spb-тарифов даёт суммарные ядра, а не на сокет
        (2 x EPYC 7402 / 256 / 1N: cpuCount=28, cpuParams «48 ядер»)."""
        rows = _parse_timeweb_cloud_nuxt(timeweb_cloud_flat, TODAY, SPB)
        epyc = [r for r in rows
                if "EPYC 7402" in r["cpu_model"] and r["cpu_sockets"] == 2]
        assert epyc, "фикстура должна содержать spb-тариф на 2 x EPYC 7402"
        assert all(r["cpu_cores_total"] == 48 for r in epyc)

    def test_novelty_prefix_stripped(self):
        """timeweb.com подписывает новые карточки «НОВИНКА - …» — префикс не
        должен попадать ни в модель CPU, ни в plan_id."""
        flat = [
            {"presets": 1}, [2],
            {"cpu": 3, "presetId": 4, "storageList": 5, "location": 6,
             "cpuParams": 7, "memoryCount": 8, "priceNumber": 9, "name": 10,
             "cpuCount": 11},
            "НОВИНКА - Intel Xeon E-2236", 3247, [12], "ru",
            "6 ядер, 3.4-4.8 ГГц, 12 потоков", 16, 11960,
            "НОВИНКА - E-2236 / 16 / 480", 6, "2 x 480 ГБ SSD",
        ]
        rows = _parse_timeweb_cloud_nuxt(flat, TODAY, SPB)
        assert len(rows) == 1
        assert rows[0]["cpu_model"] == "Intel Xeon E-2236"
        assert rows[0]["plan_id"] == "E-2236 / 16 / 480"
        assert rows[0]["cpu_cores_total"] == 6
        assert rows[0]["price_rub"] == 11960.0


@pytest.mark.integration
def test_scrape_timeweb_cloud_live():
    """Без аргументов — витрина из config/competitors.json (СПб)."""
    from dedicated_scraper import scrape_timeweb_cloud
    rows = scrape_timeweb_cloud()
    assert len(rows) >= 20
    assert SPB_E2236_32 in _plans(rows)
    assert MSK_E2236_32 not in _plans(rows)


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


class TestSelectelChipcoreLine:
    """Линейки Chipcore/Ryzen/Mac (CL*, AR*, MAC*) лежат в отдельном сервисе.

    Фидбек Светланы 11.09: «у селектела тариф в этом семействе похож с нашим
    MIR116, но нет этого конкурента в сравнении» — речь про AR44-NVMe
    (Ryzen 9 7950X). Его не было, потому что service/server его не отдаёт.
    """

    CHIP_CFG = {
        "name": "AR44-NVMe",
        "cpu": {"name": "AMD Ryzen 9 7950X", "count": 1, "cores_per_cpu": 16},
        "ram": [{"size": 128, "count": 1}],
        "disk": [{"type": "SSD NVMe M.2", "size": 2000, "count": 2}],
        "is_order": True,
        "is_preorder": False,
        "available": [{"location": "loc-msk", "count": 1}],
        "price_collection": {"RUB": {"month": 22500.0}},
        "location_price_collection": {"loc-msk": {"RUB": {"month": 23700.0}}},
    }

    def _fake_requests(self, monkeypatch, chip_result, server_result=None):
        import dedicated_scraper as ds

        class Resp:
            def __init__(self, payload): self._payload = payload
            def raise_for_status(self): pass
            def json(self): return self._payload

        calls = []

        def fake_get(url, *a, **kw):
            calls.append(url)
            if url == ds.SELECTEL_PUB_LOCATION:
                return Resp({"result": [
                    {"uuid": "loc-msk", "name": "MSK-1",
                     "visibility": "everywhere",
                     "primary_resource_ordering": "enabled"}]})
            if url == ds.SELECTEL_CHIP_API:
                if isinstance(chip_result, Exception):
                    raise chip_result
                return Resp({"result": chip_result})
            return Resp({"result": server_result or []})

        monkeypatch.setattr(ds.requests, "get", fake_get)
        monkeypatch.setattr(ds.time, "sleep", lambda *_: None)
        return calls

    def test_chip_line_is_fetched_and_parsed(self, monkeypatch):
        import dedicated_scraper as ds
        calls = self._fake_requests(monkeypatch, [self.CHIP_CFG])
        rows = ds._scrape_selectel_api()
        assert ds.SELECTEL_CHIP_API in calls
        by_plan = {r["plan_id"]: r for r in rows}
        assert "AR44-NVMe" in by_plan
        row = by_plan["AR44-NVMe"]
        # цена — по локации витрины, а не общая price_collection
        assert row["price_rub"] == 23700.0
        assert row["quantity_available"] == 1
        assert row["cpu_model"] == "AMD Ryzen 9 7950X"
        assert row["ram_gb"] == 128

    def test_chip_failure_keeps_main_line_and_warns(self, monkeypatch):
        """Отказ отдельного сервиса не должен ронять основную линейку,
        но и молчать нельзя — иначе AR*/CL* тихо исчезают из сравнения."""
        import dedicated_scraper as ds
        self._fake_requests(monkeypatch, ConnectionError("нет сети"),
                            server_result=[dict(self.CHIP_CFG, name="EL11-SSD")])
        printed = []
        monkeypatch.setattr("builtins.print",
                            lambda *a, **k: printed.append(" ".join(map(str, a))))
        rows = ds._scrape_selectel_api()
        assert [r["plan_id"] for r in rows] == ["EL11-SSD"]
        assert any("Chipcore" in line for line in printed)


class TestRegcloudClearanceHidden:
    """Карточки «Распродажа» есть в DOM, но не в листинге /dedicated/.

    21.09.2026: счётчик сайта — 110 конфигураций, в разметке 157, и все
    47 лишних несли этот бейдж. Клиент сверяет отчёт с тем, что видит
    по нашей ссылке (фидбек 11.09 про RD-30111/30170/30189).
    """

    SNAPSHOT = (Path(__file__).parent / "fixtures" / "snapshots" / "2026-09-15"
                / "regcloud_dedicated.html")

    def test_clearance_cards_are_dropped(self):
        import dedicated_scraper as ds
        from bs4 import BeautifulSoup
        html = self.SNAPSHOT.read_text(encoding="utf-8")
        items = BeautifulSoup(html, "lxml").find_all(
            "div", class_="b-dedicated-servers-list-item-cloud")
        clearance = [i for i in items if ds._regcloud_is_clearance(i)]
        assert len(clearance) == 49, "в снимке 15.09 ровно 49 карточек распродажи"

        rows = ds._parse_regcloud_html(html, "2026-09-15")
        assert len(rows) == len(items) - len(clearance)
        assert all("распродажа" not in (r.get("price_note") or "") for r in rows)
        # RD-30370 — распродажная карточка, в отчёт она попадать не должна
        assert "RD-30370" not in {r["plan_id"] for r in rows}

    def test_sale_tag_does_not_hide_server_of_the_day(self):
        """«Сервер дня» остаётся: это обычная карточка листинга со скидкой."""
        import dedicated_scraper as ds
        html = self.SNAPSHOT.read_text(encoding="utf-8")
        rows = ds._parse_regcloud_html(html, "2026-09-15")
        notes = [r.get("price_note") or "" for r in rows]
        assert any("Сервер дня" in n for n in notes)
