"""Сверка пар отчёта с витриной (reconcile/core.py) — без сети."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config_loader import DiskPool, ReferenceConfig, load_cpu_specs, load_matching_rules
from matching import CompetitorOffer
from reconcile.core import (
    Card,
    check_pairs,
    checks_to_rows,
    compare_pair,
    disk_tokens,
    regcloud_cards,
    reject_reason,
    render_md,
    selectel_cards,
    timeweb_cards,
    unmatched_candidates,
)

FIXTURES = Path(__file__).parent / "fixtures"

ROW = {
    "config_id": "MIR-135", "competitor_id": "reg_cloud", "plan_id": "RD-55039",
    "cpu_model": "Intel Xeon Gold 5218R", "cpu_sockets": "2", "ram_gb": "64",
    "disks": "2×480 ГБ SSD", "price_value": "33300.0",
}


def _card(**kw) -> Card:
    base = dict(competitor_id="reg_cloud", plan_id="RD-55039", visible=True,
                price=33300.0, cpu_text="2 × Intel Xeon Gold 5218R", ram_gb=64,
                disks_text="2 x 480 ГБ SSD", url="https://reg.cloud/dedicated/server_details/55039")
    base.update(kw)
    return Card(**base)


class TestRegcloudCards:
    def test_live_markup_2026_09(self):
        html = (FIXTURES / "regcloud_dedicated_2026_09.html").read_text(encoding="utf-8")
        cards = regcloud_cards(html)
        assert set(cards) == {"RD-55039", "RD-30055"}
        plain = cards["RD-55039"]
        assert plain.price == 33300.0 and plain.price_list == 33300.0
        assert "Gold 5218R" in plain.cpu_text and plain.ram_gb == 64
        assert plain.url.endswith("/server_details/55039")
        sale = cards["RD-30055"]
        assert sale.price == 5740.0          # data-price = видимая цена
        assert sale.price_list == 8200.0     # перечёркнутая


class TestTimewebCards:
    PRESETS = [
        {"description": "E-2236 / 16 / 480", "price": 11960, "location": "ru-1",
         "cpu": {"count": 6, "description_short": "Intel Xeon E-2236"},
         "memory": {"count": 2, "size": 16384}, "disk": {"description": "2 x 480 ГБ SSD"}},
        {"description": "E-2236 / 16 / 480", "price": 9000, "location": "ru-3",
         "cpu": {"count": 6, "description_short": "Intel Xeon E-2236"},
         "memory": {"count": 2, "size": 16384}, "disk": {"description": "2 x 480 ГБ SSD"}},
    ]

    def test_only_wanted_location_and_shown_price(self):
        cards = timeweb_cards(self.PRESETS, ("ru",), {"E-2236 / 16 / 480": 10764.0})
        card = cards["E-2236 / 16 / 480"]
        assert card.price == 10764.0 and card.price_list == 11960.0
        assert card.ram_gb == 16          # memory.size — суммарно в МБ
        assert card.cpu_text == "Intel Xeon E-2236"

    def test_without_shown_price_falls_back_to_monthly(self):
        card = timeweb_cards(self.PRESETS, ("ru",))["E-2236 / 16 / 480"]
        assert card.price == 11960.0 and "помесячной" in card.detail


class TestSelectelCards:
    def test_hidden_when_only_abroad(self, selectel_api_configs, selectel_locations):
        from dedicated_scraper import _selectel_visible_locations
        visible = _selectel_visible_locations(selectel_locations)
        cards = selectel_cards(list(selectel_api_configs.values()), visible)
        assert not cards["EL46-NVMe"].visible          # только Ташкент
        el42 = cards["EL42-NVMe"]
        assert el42.visible and el42.price == 18400.0
        assert el42.detail == "в наличии: MSK-1 ×1"
        assert "2 ×" not in el42.cpu_text or el42.cpu_text.startswith("2 ×")


class TestComparePair:
    def test_ok(self):
        check = compare_pair(ROW, _card())
        assert check.ok and check.reasons == [] and check.site_price == 33300.0

    def test_missing_card(self):
        check = compare_pair(ROW, None)
        assert not check.ok and check.reasons == ["карточки нет на витрине"]

    def test_hidden_card(self):
        check = compare_pair(ROW, _card(visible=False, detail="нет в наличии в msk/spb/nsk"))
        assert not check.ok and check.reasons[0].startswith("карточка скрыта")

    def test_price_mismatch(self):
        check = compare_pair(ROW, _card(price=35000.0))
        assert not check.ok
        assert check.reasons == ["цена на карточке 35 000, в отчёте 33 300"]

    def test_cpu_sockets_ram_disks(self):
        check = compare_pair(ROW, _card(cpu_text="Intel Xeon Gold 5218", ram_gb=128,
                                        disks_text="2 x 960 ГБ SSD"))
        joined = " ".join(check.reasons)
        assert not check.ok
        assert "CPU на карточке" in joined and "процессоров на карточке 1" in joined
        assert "RAM на карточке 128" in joined and "диски на карточке" in joined

    def test_disk_tokens_units_and_classes(self):
        assert disk_tokens("2×480 ГБ SSD + 2×4000 ГБ HDD") == disk_tokens("2 x 480 ГБ SSD, 2 x 4 ТБ HDD")
        assert disk_tokens("1×1000 ГБ HDD", {1000: 960, 960: 960}) == disk_tokens("960 ГБ HDD", {1000: 960, 960: 960})

    def test_source_unavailable_marks_pairs(self):
        checks = check_pairs([ROW], {"reg_cloud": None})
        assert not checks[0].ok and "витрина недоступна" in checks[0].reasons[0]


class TestCandidatesAndReport:
    REF = ReferenceConfig("MIR-135", "Intel Xeon Gold 5218R", 2, 20, 64,
                          (DiskPool("SSD", 2, 480),))

    def _offer(self, ram=64, sockets=2, plan="RD-1"):
        return CompetitorOffer(
            competitor_id="reg_cloud", plan_id=plan, cpu_model="Intel Xeon Gold 5218R",
            cpu_model_norm="intel xeon gold 5218r", cpu_sockets=sockets, cpu_cores_total=40,
            ram_gb=ram, disk_pools=({"disk_type": "SSD", "disk_count": 2, "disk_size_gb": 480},),
            price_value=30000.0, currency="RUB", price_period="month", stock_count=None,
        )

    def test_reject_reason_names_failed_gate(self):
        rules, specs = load_matching_rules(), load_cpu_specs()
        assert reject_reason(self.REF, self._offer(), rules, specs, None) is None
        assert reject_reason(self.REF, self._offer(ram=128), rules, specs, None) == "RAM 128 ГБ, у Мирана 64"
        assert reject_reason(self.REF, self._offer(sockets=1), rules, specs, None) == "процессоров 1, у Мирана 2"

    def test_unmatched_candidates_skip_matched(self):
        rules, specs = load_matching_rules(), load_cpu_specs()
        offers = [self._offer(plan="RD-1"), self._offer(ram=128, plan="RD-2")]
        cands = unmatched_candidates([self.REF], offers, {("MIR-135", "reg_cloud", "RD-1")},
                                     rules, specs, None)
        assert [(c["plan_id"], c["reason"]) for c in cands] == [("RD-2", "RAM 128 ГБ, у Мирана 64")]

    def test_render_md_and_rows(self):
        checks = check_pairs([ROW], {"reg_cloud": {"RD-55039": _card(price=35000.0)}})
        md = render_md("20260915", checks, [{"config_id": "MIR-135", "competitor_id": "reg_cloud",
                                              "plan_id": "RD-2", "price_value": 1000.0, "reason": "RAM"}],
                       {"timeweb": "нет данных"}, {"reg_cloud": "REG.Cloud"})
        assert md.startswith("# Сверка с витринами — 15.09.2026")
        assert "расхождений: 1" in md and "| ✗ |" in md and "[RD-55039](https://reg.cloud" in md
        assert "⚠ timeweb: витрина недоступна" in md and "| RD-2 | 1 000 | RAM |" in md
        rows = checks_to_rows(checks)
        assert rows[0]["result"] == "mismatch" and rows[0]["site_price"] == 35000.0
