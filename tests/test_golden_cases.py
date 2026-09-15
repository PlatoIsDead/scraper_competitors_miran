"""Сквозная регрессия scrape → match → reconcile на снимках источников
15.09.2026 (tests/fixtures/snapshots/2026-09-15/, без сети) — кейсы из
фидбека Светланы 11.09 и решений 14–15.09."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import dedicated_scraper as ds
from competitor_pipeline import rows_to_offers
from competitor_report import build_long_df, build_wide_df
from config_loader import (
    load_competitors,
    load_cpu_specs,
    load_disk_classes,
    load_matching_rules,
    load_reference_configs,
)
from matching import match_all
from reconcile.core import check_pairs, regcloud_cards, selectel_cards, timeweb_cards
from reconcile.sources import timeweb_shown_prices

SNAP = Path(__file__).parent / "fixtures" / "snapshots" / "2026-09-15"
TODAY = "2026-09-15"
SPB = ("ru",)


@pytest.fixture(scope="module")
def snap():
    if not SNAP.exists():
        pytest.skip("снимки 2026-09-15 отсутствуют")
    comps = {c.competitor_id: c for c in load_competitors()}
    reg_html = (SNAP / "regcloud_dedicated.html").read_text(encoding="utf-8")
    reg_rows = ds._parse_regcloud_html(reg_html, TODAY)
    flat = json.loads((SNAP / "timeweb_cloud_nuxt.json").read_text(encoding="utf-8"))
    tw_rows = ds._parse_timeweb_cloud_nuxt(flat, TODAY, SPB)
    sel_cfgs = json.loads((SNAP / "selectel_servers.json").read_text(encoding="utf-8"))["result"]
    locs = json.loads((SNAP / "selectel_location.json").read_text(encoding="utf-8"))["result"]
    visible = ds._selectel_visible_locations(locs)
    sel_rows = []
    for cfg in sel_cfgs:
        if ds._selectel_storefront_visible(cfg, visible):
            row = ds._selectel_cfg_to_row(cfg, TODAY, visible)
            if row:
                sel_rows.append(row)
    offers = (rows_to_offers(sel_rows, comps["selectel"])
              + rows_to_offers(reg_rows, comps["reg_cloud"])
              + rows_to_offers(tw_rows, comps["timeweb"]))
    refs = load_reference_configs()
    classes = load_disk_classes()
    matches = match_all(refs, offers, load_matching_rules(), load_cpu_specs(), classes)
    long_df = build_long_df(matches)
    presets = json.loads((SNAP / "timeweb_presets_ru-1.json").read_text(encoding="utf-8"))
    # __NUXT_DATA__ обратно в строку — ровно то, что читает timeweb_shown_prices
    page = ('<html><body><script id="__NUXT_DATA__" type="application/json">'
            + json.dumps(flat, ensure_ascii=False) + "</script></body></html>")
    cards = {
        "reg_cloud": regcloud_cards(reg_html),
        "timeweb": timeweb_cards(presets["dedicated_servers_presets"], SPB,
                                 timeweb_shown_prices(page, SPB)),
        "selectel": selectel_cards(sel_cfgs, visible),
    }
    return {
        "reg_rows": reg_rows, "tw_rows": tw_rows, "sel_rows": sel_rows,
        "refs": refs, "matches": matches, "long": long_df, "cards": cards,
        "classes": classes, "comps": list(comps.values()),
    }


def _pairs(snap, config_id, competitor_id=None):
    df = snap["long"]
    df = df[df["config_id"] == config_id]
    if competitor_id:
        df = df[df["competitor_id"] == competitor_id]
    return {(r.plan_id, r.price_value) for r in df.itertuples()}


class TestSnapshotsParsed:
    def test_row_counts(self, snap):
        assert len(snap["reg_rows"]) == 155
        assert len(snap["tw_rows"]) == 68
        assert len(snap["sel_rows"]) == 90


class TestRegcloudCases:
    def test_mir_135_to_rd_55039(self, snap):
        assert _pairs(snap, "MIR-135", "reg_cloud") == {("RD-55039", 33300.0)}

    def test_mir_138_two_pairs(self, snap):
        assert {p for p, _ in _pairs(snap, "MIR-138", "reg_cloud")} == {"RD-58700", "RD-58699"}

    def test_mir_139_to_rd_58699(self, snap):
        assert {p for p, _ in _pairs(snap, "MIR-139", "reg_cloud")} == {"RD-58699"}

    def test_mir_151_to_rd_55040(self, snap):
        assert {p for p, _ in _pairs(snap, "MIR-151", "reg_cloud")} == {"RD-55040"}

    def test_mir_002_sold_card_gone(self, snap):
        """RD-30312 продана: карточки нет в листинге → пары нет, прочерк верный."""
        assert "RD-30312" not in {r["plan_id"] for r in snap["reg_rows"]}
        assert _pairs(snap, "MIR-002") == set()

    def test_bare_e3_1230_is_not_v5_v6(self, snap):
        """MIR-007/008: RD-59192/RD-59261 с голым «E3-1230» (Sandy Bridge,
        DDR3) не пара для E3-1230v5/v6."""
        plans = {r["plan_id"]: r for r in snap["reg_rows"]}
        assert plans["RD-59192"]["cpu_model"].endswith("E3-1230")
        assert _pairs(snap, "MIR-007", "reg_cloud") == set()
        assert _pairs(snap, "MIR-008", "reg_cloud") == set()

    def test_discount_price_and_note(self, snap):
        rows = {r["plan_id"]: r for r in snap["reg_rows"]}
        assert rows["RD-30055"]["price_rub"] == 5740.0
        assert rows["RD-30055"]["price_note"] == "скидка 30 %, было 8 200"
        assert rows["RD-55039"]["price_note"] == ""

    def test_server_of_the_day_reads_visible_price(self, snap):
        """3 карточки «Сервер дня» без data-period-price: цена из
        __current-price = data-price, а не перечёркнутая."""
        rows = {r["plan_id"]: r for r in snap["reg_rows"]}
        cards = snap["cards"]["reg_cloud"]
        for plan in ("RD-30324", "RD-54956", "RD-40936"):
            assert rows[plan]["price_rub"] == cards[plan].price
            assert rows[plan]["price_rub"] < rows[plan]["price_list_rub"]
            assert rows[plan]["price_note"].startswith("Сервер дня (скидка только сегодня)")
        assert rows["RD-30324"]["price_rub"] == 13230.0
        assert rows["RD-30324"]["price_list_rub"] == 18900.0

    def test_every_card_price_equals_data_price(self, snap):
        cards = snap["cards"]["reg_cloud"]
        for r in snap["reg_rows"]:
            assert r["price_rub"] == cards[r["plan_id"]].price, r["plan_id"]


class TestTimewebCases:
    def test_mir_045_and_047_shown_price_with_monthly_note(self, snap):
        p45 = _pairs(snap, "MIR-045", "timeweb")
        p47 = _pairs(snap, "MIR-047", "timeweb")
        assert p45 == {("E-2236 / 16 / 480", 10764.0)}
        assert p47 == {("E-2236 / 32 / 960", 13086.0)}
        notes = dict(zip(snap["long"]["plan_id"], snap["long"]["price_note"]))
        assert notes["E-2236 / 16 / 480"] == "при оплате за 12 мес (−10 %); помесячно 11 960"
        assert notes["E-2236 / 32 / 960"] == "при оплате за 12 мес (−10 %); помесячно 14 540"

    @pytest.mark.parametrize("config_id", ["MIR-046", "MIR-048", "MIR-051", "MIR-052", "MIR-053"])
    def test_moscow_only_tariffs_absent_in_spb(self, snap, config_id):
        assert _pairs(snap, config_id, "timeweb") == set()

    def test_monthly_price_matches_landing_api(self, snap):
        cards = snap["cards"]["timeweb"]
        for r in snap["tw_rows"]:
            assert cards[r["plan_id"]].price_list == r["price_list_rub"], r["plan_id"]


class TestSelectelCases:
    def test_mir_109_only_tashkent(self, snap):
        assert not snap["cards"]["selectel"]["EL46-NVMe"].visible
        assert "EL46-NVMe" not in {r["plan_id"] for r in snap["sel_rows"]}
        assert _pairs(snap, "MIR-109", "selectel") == set()

    def test_mir_097_el42_stock_by_storefront(self, snap):
        pairs = _pairs(snap, "MIR-097", "selectel")
        assert {p for p, _ in pairs} == {"EL42-NVMe"}
        row = next(r for r in snap["sel_rows"] if r["plan_id"] == "EL42-NVMe")
        assert set(row["stock_by_location"]) <= {
            "MSK-1", "MSK-2", "MSK-3", "MSK-7", "SPB-2", "SPB-3", "SPB-4", "SPB-5", "NSK-1"}
        assert row["quantity_available"] == sum(row["stock_by_location"].values())


class TestReconcileOnSnapshots:
    def test_every_pair_matches_its_card(self, snap):
        rows = snap["long"].astype(str).to_dict("records")
        checks = check_pairs(rows, snap["cards"], snap["classes"])
        bad = [(c.config_id, c.plan_id, c.reasons) for c in checks if not c.ok]
        assert len(checks) == len(rows) > 0
        assert bad == []

    def test_wide_report_carries_notes(self, snap):
        wide = build_wide_df(snap["refs"], snap["matches"], snap["comps"])
        row = wide[wide["config_id"] == "MIR-045"].iloc[0]
        assert row["timeweb_price"] == 10764.0
        assert row["timeweb_price_note"].startswith("при оплате за 12 мес")
