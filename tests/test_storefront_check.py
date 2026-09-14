import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from storefront_check import (
    card_url,
    check_provider,
    diff_regcloud,
    diff_timeweb,
    split_by_report,
)

CARD = ('<div class="b-dedicated-servers-list-item-cloud" data-price="{price}" '
        'data-server-id="{plan}"></div>')


def _html(pairs):
    return "".join(CARD.format(price=p, plan=k) for k, p in pairs)


class TestDiffRegcloud:
    ROWS = [
        {"plan_id": "RD-30055", "price_rub": 5740.0},
        {"plan_id": "RD-30312", "price_rub": 7000.0},
    ]

    def test_clean_when_prices_match(self):
        html = _html([("RD-30055", 5740), ("RD-30312", 7000)])
        assert diff_regcloud(self.ROWS, html) == []

    def test_catches_struck_through_price(self):
        """Августовский баг: парсер брал перечёркнутую цену 10 000 вместо 7 000."""
        rows = [{"plan_id": "RD-30312", "price_rub": 10000.0}]
        html = _html([("RD-30312", 7000)])
        diffs = diff_regcloud(rows, html)
        assert [d["kind"] for d in diffs] == ["price"]
        assert diffs[0]["site_price"] == 7000
        assert diffs[0]["our_price"] == 10000

    def test_catches_card_missing_from_scrape(self):
        html = _html([("RD-30055", 5740), ("RD-99999", 4000)])
        diffs = diff_regcloud(self.ROWS[:1], html)
        assert [(d["kind"], d["plan_id"]) for d in diffs] == [
            ("missing", "RD-99999")]

    def test_catches_offer_absent_from_storefront(self):
        html = _html([("RD-30055", 5740)])
        diffs = diff_regcloud(self.ROWS, html)
        assert [(d["kind"], d["plan_id"]) for d in diffs] == [
            ("extra", "RD-30312")]

    def test_markup_change_reports_check_failed(self):
        diffs = diff_regcloud(self.ROWS, "<div>вёрстка сменилась</div>")
        assert [d["kind"] for d in diffs] == ["check_failed"]
        assert check_provider("regcloud", self.ROWS,
                              html="<div></div>")["status"] == "failed"

    def test_broken_markup_falls_back_to_data_id(self):
        """Живой случай RD-58998: data-server-id склеен с data-business,
        цел только data-id — карточка не должна считаться пропавшей."""
        html = ('<div class="b-dedicated-servers-list-item-cloud" '
                'data-price="7000" data-id="30312" '
                'data-business="data-server-id=RD-30312"></div>')
        rows = [{"plan_id": "RD-30312", "price_rub": 7000.0}]
        assert diff_regcloud(rows, html) == []

    def test_attribute_order_reversed(self):
        html = ('<div data-server-id="RD-30055" data-price="5740"></div>'
                '<div data-server-id="RD-30312" data-price="7000"></div>')
        assert diff_regcloud(self.ROWS, html) == []


class TestDiffTimeweb:
    PRESETS = [
        {"description": "E-2388 / 64/ 1N", "price": 24150, "location": "ru-3"},
        {"description": "E-2236 / 16 / 480", "price": 11960, "location": "ru-3"},
        {"description": "E3-1240 / 8 / 1", "price": 7270, "location": "ru-1"},
    ]
    ROWS = [
        {"plan_id": "E-2388 / 64/ 1N", "price_rub": 24150.0},
        {"plan_id": "E-2236 / 16 / 480", "price_rub": 11960.0},
    ]

    def test_clean_when_catalogue_matches(self):
        assert diff_timeweb(self.ROWS, self.PRESETS) == []

    def test_other_location_ignored(self):
        """Питерские тарифы (ru-1) не должны считаться пропущенными в Москве."""
        assert not [d for d in diff_timeweb(self.ROWS, self.PRESETS)
                    if d.get("plan_id") == "E3-1240 / 8 / 1"]

    def test_catches_price_drift(self):
        rows = [{"plan_id": "E-2236 / 16 / 480", "price_rub": 10490.0},
                {"plan_id": "E-2388 / 64/ 1N", "price_rub": 24150.0}]
        diffs = diff_timeweb(rows, self.PRESETS)
        assert [d["kind"] for d in diffs] == ["price"]
        assert (diffs[0]["site_price"], diffs[0]["our_price"]) == (11960, 10490)

    def test_catches_preset_missing_from_scrape(self):
        diffs = diff_timeweb(self.ROWS[:1], self.PRESETS)
        assert [(d["kind"], d["plan_id"]) for d in diffs] == [
            ("missing", "E-2236 / 16 / 480")]

    def test_empty_catalogue_is_check_failed(self):
        assert [d["kind"] for d in diff_timeweb(self.ROWS, [])] == [
            "check_failed"]


class TestCheckProvider:
    def test_clean_status(self):
        html = _html([("RD-30055", 5740)])
        result = check_provider(
            "regcloud", [{"plan_id": "RD-30055", "price_rub": 5740.0}], html)
        assert result == {"status": "clean", "discrepancies": []}

    def test_unknown_provider_is_failed(self):
        assert check_provider("selectel", [])["status"] == "failed"


class TestSplitByReport:
    def test_unmatched_price_mismatch_is_outside_report(self):
        """Фидбек 11.09: RD-30111/30170/30189 не совпали с Мираном — шум."""
        diffs = [
            {"kind": "price", "plan_id": "RD-30055"},
            {"kind": "price", "plan_id": "RD-30111"},
            {"kind": "extra", "plan_id": "RD-30189"},
        ]
        relevant, rest = split_by_report(diffs, {"RD-30055"})
        assert [d["plan_id"] for d in relevant] == ["RD-30055"]
        assert [d["plan_id"] for d in rest] == ["RD-30111", "RD-30189"]

    def test_missing_card_always_relevant(self):
        # не разобранная карточка могла бы совпасть — прятать её нельзя
        relevant, rest = split_by_report(
            [{"kind": "missing", "plan_id": "RD-90167"}], set())
        assert len(relevant) == 1 and rest == []


def test_card_url():
    assert (card_url("reg_cloud", "RD-30111")
            == "https://reg.cloud/dedicated/server_details/30111")
    assert card_url("timeweb", "AMD Ryzen 9 7950X / 128") == ""
