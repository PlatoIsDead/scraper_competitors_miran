import json
import sys
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).parent.parent))

from competitor_report import (
    COMPETITOR_COL_SUFFIXES,
    build_long_df,
    build_wide_df,
    format_disk_pools,
    write_reports,
)
from config_loader import Competitor, DiskPool, ReferenceConfig
from matching import CompetitorOffer, MatchResult

COMPETITORS = [
    Competitor("selectel", "Селектел", "https://selectel.ru", "RUB", "month",
               "selectel_nuxt_cdn"),
    Competitor("reg_cloud", "REG.Cloud", "https://reg.cloud", "RUB", "month",
               "regcloud_playwright"),
    Competitor("timeweb", "Timeweb", "https://timeweb.cloud", "RUB", "month",
               "timeweb_cloud_nuxt"),
]

REF1 = ReferenceConfig("MIR-001", "Intel Xeon Silver 4214R", 2, 12, 64,
                       (DiskPool("SSD", 2, 960),))
REF2 = ReferenceConfig("MIR-002", "Intel Xeon Gold 5317", 2, 12, 192,
                       (DiskPool("NVMe", 2, 1000),))


def _offer(cid="selectel", plan="P1", price=10000.0, stock=None):
    return CompetitorOffer(
        competitor_id=cid, plan_id=plan,
        cpu_model="Intel Xeon Silver 4214R",
        cpu_model_norm="intel xeon silver 4214r",
        cpu_sockets=2, cpu_cores_total=24, ram_gb=64,
        disk_pools=({"disk_type": "SSD", "disk_count": 2, "disk_size_gb": 960},),
        price_value=price, currency="RUB", price_period="month",
        stock_count=stock,
    )


def _match(offer, config_id="MIR-001", score=100.0):
    return MatchResult(config_id, offer, score, 0.0, 0.0, 0.0, 0.0)


MATCHES = {
    "MIR-001": [
        _match(_offer("selectel", "SEL-cheap", 9000.0, stock=3)),
        _match(_offer("selectel", "SEL-dear", 12000.0), score=95.0),
        _match(_offer("timeweb", "TW-1", 11000.0)),
    ],
    "MIR-002": [],
}


class TestFormatDiskPools:
    def test_multi_pool(self):
        pools = ({"disk_type": "NVMe", "disk_count": 2, "disk_size_gb": 1000},
                 {"disk_type": "SSD", "disk_count": 2, "disk_size_gb": 2000})
        assert format_disk_pools(pools) == "2×1000 ГБ NVMe + 2×2000 ГБ SSD"

    def test_dataclass_pools(self):
        assert format_disk_pools((DiskPool("SSD", 2, 960),)) == "2×960 ГБ SSD"


class TestBuildLongDf:
    def test_one_row_per_match(self):
        df = build_long_df(MATCHES)
        assert len(df) == 3
        assert set(df["config_id"]) == {"MIR-001"}
        assert list(df.columns)[:3] == ["config_id", "competitor_id", "plan_id"]


class TestBuildWideDf:
    def test_row_per_config(self):
        df = build_wide_df([REF1, REF2], MATCHES, COMPETITORS)
        assert len(df) == 2

    def test_competitor_column_groups_present(self):
        df = build_wide_df([REF1, REF2], MATCHES, COMPETITORS)
        for comp in COMPETITORS:
            for suffix in COMPETITOR_COL_SUFFIXES:
                assert f"{comp.competitor_id}_{suffix}" in df.columns

    def test_cheapest_match_selected(self):
        df = build_wide_df([REF1], MATCHES, COMPETITORS)
        row = df.iloc[0]
        assert row["selectel_price"] == 9000.0
        assert row["selectel_plan_id"] == "SEL-cheap"
        assert row["selectel_match_count"] == 2
        assert row["selectel_stock_count"] == 3

    def test_no_match_cells_empty(self):
        df = build_wide_df([REF1, REF2], MATCHES, COMPETITORS)
        row2 = df[df["config_id"] == "MIR-002"].iloc[0]
        assert row2["selectel_price"] is None or str(row2["selectel_price"]) == "nan"
        row1 = df[df["config_id"] == "MIR-001"].iloc[0]
        assert row1["reg_cloud_price"] is None or str(row1["reg_cloud_price"]) == "nan"


class TestWriteReports:
    def test_files_written(self, tmp_path):
        written = write_reports([REF1, REF2], MATCHES, COMPETITORS,
                                "20260729", out_dir=tmp_path, xlsx=True)
        assert written["long"].exists()
        assert written["wide"].exists()
        assert written["xlsx"].exists()
        assert written["wide"].name == "dedicated_competitors_20260729.csv"

    def test_xlsx_has_headers(self, tmp_path):
        written = write_reports([REF1], MATCHES, COMPETITORS,
                                "20260729", out_dir=tmp_path, xlsx=True)
        wb = openpyxl.load_workbook(written["xlsx"])
        ws = wb["Сравнение"]
        headers = [c.value for c in ws[1]]
        assert "config_id" in headers
        assert "selectel_price" in headers
        wb.close()

    def test_csv_utf8_sig(self, tmp_path):
        written = write_reports([REF1], MATCHES, COMPETITORS,
                                "20260729", out_dir=tmp_path)
        raw = written["wide"].read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf")


class TestWriteRunStatus:
    """Статус источников: пустая колонка из-за сбоя скрейпа должна отличаться
    от честного «совпадений нет»."""

    SOURCES = [
        {"competitor_id": "selectel", "name": "Селектел", "url": "u1",
         "offers": 118, "status": "ok"},
        {"competitor_id": "reg_cloud", "name": "REG.Cloud", "url": "u2",
         "offers": 0, "status": "error"},
    ]

    def test_file_written_with_sources(self, tmp_path):
        from competitor_pipeline import write_run_status

        path = write_run_status(self.SOURCES, "20260902", out_dir=tmp_path)
        assert path.name == "run_status_20260902.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["generated_at"]
        assert [s["status"] for s in data["sources"]] == ["ok", "error"]

    def test_app_reads_failed_sources(self, tmp_path, monkeypatch):
        from competitor_pipeline import write_run_status

        write_run_status(self.SOURCES, "20260902", out_dir=tmp_path)
        import dedicated_app

        monkeypatch.setattr(dedicated_app, "REPORTS_DIR", tmp_path)
        sources = dedicated_app.run_sources.__wrapped__("20260902")
        assert [s["competitor_id"] for s in sources
                if s["status"] != "ok"] == ["reg_cloud"]

    def test_missing_file_is_empty(self, tmp_path, monkeypatch):
        import dedicated_app

        monkeypatch.setattr(dedicated_app, "REPORTS_DIR", tmp_path)
        assert dedicated_app.run_sources.__wrapped__("20990101") == []
