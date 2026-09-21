"""Вкладки проверки: скрейп витрины рядом с конфигурациями Мирана.

Мотив (21.09): клиент сверяет наши цифры с сайтом конкурента глазами и
хочет видеть слева конфигурации Мирана, справа — весь листинг, который мы
сняли. Основной отчёт при этом остаётся отдельным разделом.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_status import date_tag_msk  # noqa: E402

WIDE = (
    "config_id,cpu_model,cpu_sockets,cpu_cores_per_socket,ram_gb,disks,"
    "miran_price,reg_cloud_price\n"
    "MIR-001,Intel Xeon E-2386G,1,6,64,2×480 ГБ SSD,12000,9000\n"
)
MATCHES = (
    "config_id,competitor_id,plan_id,cpu_model,cpu_sockets,cpu_cores_total,"
    "ram_gb,disks,price_value,price_note,currency,price_period,stock_count,"
    "match_score\n"
    "MIR-001,reg_cloud,RD-1,Intel Xeon E-2386G,1,6,64,2×480 ГБ SSD,9000,"
    "\"скидка 30 %, было 12 900\",RUB,month,3,100\n"
)
RAW = [
    {"plan_id": "RD-1", "cpu_model": "Intel Xeon E-2386G", "cpu_sockets": 1,
     "cpu_cores_total": 6, "ram_gb": 64,
     "disk_pools": [{"disk_type": "SSD", "disk_count": 2, "disk_size_gb": 480}],
     "price_rub": 9000, "price_note": "скидка 30 %, было 12 900",
     "quantity_available": 3, "scraped_at": "2026-09-21T10:00:00"},
    {"plan_id": "RD-2", "cpu_model": "AMD Ryzen 9 7950X", "cpu_sockets": 1,
     "cpu_cores_total": 16, "ram_gb": 128,
     "disk_pools": [{"disk_type": "NVMe", "disk_count": 2,
                     "disk_size_gb": 1000}],
     "price_rub": 20300, "price_note": "", "quantity_available": 1,
     "scraped_at": "2026-09-21T10:00:00"},
]


def _seed(tmp_path: Path, *, with_raw: bool = True) -> None:
    reports = tmp_path / "data" / "reports"
    reports.mkdir(parents=True)
    tag = date_tag_msk()
    (reports / f"dedicated_competitors_{tag}.csv").write_text(
        WIDE, encoding="utf-8-sig")
    (reports / f"matches_{tag}.csv").write_text(MATCHES, encoding="utf-8-sig")
    if with_raw:
        (tmp_path / "data" / f"regcloud_{tag}.json").write_text(
            json.dumps(RAW), encoding="utf-8")


def _run(tmp_path, monkeypatch, section: "str | None" = None):
    pytest.importorskip("streamlit.testing.v1")
    from streamlit.testing.v1 import AppTest
    import streamlit as st

    st.cache_data.clear()  # кеш переживает запуски в одном процессе
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(ROOT))
    at = AppTest.from_file(str(ROOT / "dedicated_app.py"), default_timeout=120)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    if section is not None:
        at.radio[0].set_value(section).run()
        assert not at.exception, [e.value for e in at.exception]
    return at


def _texts(at) -> str:
    parts = [m.value for m in at.markdown]
    parts += [i.value for i in at.info] + [w.value for w in at.warning]
    parts += [c.value for c in at.caption]
    return "\n".join(str(p) for p in parts)


class TestCheckTabs:
    def test_one_tab_per_competitor_plus_report(self, tmp_path, monkeypatch):
        at = _run(tmp_path, monkeypatch)
        assert list(at.radio[0].options) == [
            "Отчёт по рынку", "Проверка · Selectel",
            "Проверка · Reg.cloud", "Проверка · Timeweb",
        ]
        assert at.radio[0].value == "Отчёт по рынку"

    def test_report_tab_untouched(self, tmp_path, monkeypatch):
        """Основной раздел остаётся прежним: KPI и таблица сравнения на месте."""
        _seed(tmp_path)
        at = _run(tmp_path, monkeypatch)
        text = _texts(at)
        assert "Сравнение по конфигурациям" in text
        assert "Сверка с витриной" not in text

    def test_scrape_right_miran_left(self, tmp_path, monkeypatch):
        _seed(tmp_path)
        at = _run(tmp_path, monkeypatch, "Проверка · Reg.cloud")
        assert len(at.dataframe) == 2, "слева Миран, справа скрейп"
        left, right = at.dataframe[0].value, at.dataframe[1].value
        # слева — конфигурация Мирана с её ценой и тарифом конкурента
        assert list(left["config_id"]) == ["MIR-001"]
        assert float(left["miran_price"].iloc[0]) == 12000
        assert list(left["plan_id"]) == ["RD-1"]
        assert round(float(left["delta_pct"].iloc[0]), 1) == 33.3
        # справа — ВЕСЬ листинг витрины, включая тариф без пары
        assert list(right["plan_id"]) == ["RD-1", "RD-2"]
        assert list(right["matched"]) == ["MIR-001", ""]
        assert right["card"].iloc[0].endswith("/dedicated/server_details/1")
        assert right["card"].iloc[1].endswith("/dedicated/server_details/2")

    def test_unpaired_offers_counted(self, tmp_path, monkeypatch):
        _seed(tmp_path)
        at = _run(tmp_path, monkeypatch, "Проверка · Reg.cloud")
        text = _texts(at)
        assert "Показано 2 из 2 предложений · без пары с Мираном: 1" in text
        assert "https://reg.cloud/dedicated/" in text

    def test_only_paired_checkbox_filters(self, tmp_path, monkeypatch):
        _seed(tmp_path)
        at = _run(tmp_path, monkeypatch, "Проверка · Reg.cloud")
        box = next(c for c in at.checkbox
                   if c.label == "Только тарифы, легшие на Миран")
        box.set_value(True).run()
        assert not at.exception, [e.value for e in at.exception]
        assert list(at.dataframe[1].value["plan_id"]) == ["RD-1"]

    def test_tab_without_scrape_says_so(self, tmp_path, monkeypatch):
        """Пустая вкладка не должна выглядеть как «у конкурента ничего нет»."""
        _seed(tmp_path)
        at = _run(tmp_path, monkeypatch, "Проверка · Timeweb")
        assert "нет сырого скрейпа" in _texts(at)
        assert not at.dataframe

    def test_timeweb_tab_names_the_spb_storefront(self, tmp_path, monkeypatch):
        _seed(tmp_path)
        at = _run(tmp_path, monkeypatch, "Проверка · Timeweb")
        text = _texts(at)
        assert "Санкт-Петербург" in text and "?location=ru" in text
