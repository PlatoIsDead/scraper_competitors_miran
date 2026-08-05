import sys
from pathlib import Path

import openpyxl
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from excel_to_configs import convert, parse_ref_cpu, parse_ref_disks, pick_sheet

ALIASES = {
    "intel xeon silver 4214r": {"canonical": "Intel Xeon Silver 4214R", "cores": 12},
    "silver 4314": {"canonical": "Intel Xeon Silver 4314", "cores": 16},
    "5317": {"canonical": "Intel Xeon Gold 5317", "cores": 12},
    "6336y": {"canonical": "Intel Xeon Gold 6336Y", "cores": 24},
    "6448y": {"canonical": "Intel Xeon Gold 6448Y", "cores": 32},
}


class TestParseRefCpu:
    def test_full_model_with_cores(self):
        cpu = parse_ref_cpu(
            "2 × Intel Xeon Silver 4214R 2.4 ГГц, 12 ядер 24 потока", ALIASES
        )
        assert cpu == {
            "cpu_model": "Intel Xeon Silver 4214R",
            "cpu_sockets": 2,
            "cpu_cores_per_socket": 12,
        }

    def test_star_separator_and_comma_freq(self):
        cpu = parse_ref_cpu(
            "2 * Silver 4314 (@2,4 - 3,4, 16 ядер, 32 потока)", ALIASES
        )
        assert cpu["cpu_model"] == "Intel Xeon Silver 4314"
        assert cpu["cpu_sockets"] == 2
        assert cpu["cpu_cores_per_socket"] == 16

    def test_bare_model_resolved_via_alias(self):
        cpu = parse_ref_cpu("2 * 5317 (@3,0 - 3,6, 12 ядер, 24 потока)", ALIASES)
        assert cpu["cpu_model"] == "Intel Xeon Gold 5317"

    def test_typo_otoka_still_parses_cores(self):
        # опечатка воркбука «64 отока» не мешает: ядра берутся по «32 ядра»
        cpu = parse_ref_cpu("2 * 6336Y (2,4 - 3,6, 24 ядра, 48 потоков)", ALIASES)
        assert cpu["cpu_cores_per_socket"] == 24

    def test_unknown_model_kept_raw(self):
        cpu = parse_ref_cpu("2 * 9999X (@2,0, 8 ядер, 16 потоков)", ALIASES)
        assert cpu["cpu_model"] == "9999X"

    def test_no_cores_returns_none(self):
        assert parse_ref_cpu("2 поколение Intel", ALIASES) is None

    def test_empty_returns_none(self):
        assert parse_ref_cpu("", ALIASES) is None


class TestParseRefDisks:
    def test_simple_gb_ssd(self):
        assert parse_ref_disks("2 * 960 ГБ SSD") == [
            {"disk_type": "SSD", "disk_count": 2, "disk_size_gb": 1000}
        ]  # 960 снапится к 1000

    def test_decimal_tb(self):
        assert parse_ref_disks("2 * 1.9 ТБ SSD") == [
            {"disk_type": "SSD", "disk_count": 2, "disk_size_gb": 2000}
        ]

    def test_no_space_tb_nvme(self):
        assert parse_ref_disks("2 * 1ТБ NVME") == [
            {"disk_type": "NVMe", "disk_count": 2, "disk_size_gb": 1000}
        ]

    def test_vroc_suffix_ignored(self):
        pools = parse_ref_disks("2 * 1 ТБ NVME + VROC")
        assert pools == [{"disk_type": "NVMe", "disk_count": 2, "disk_size_gb": 1000}]

    def test_multi_pool(self):
        pools = parse_ref_disks("2 * 1 ТБ NVME + 2 * 2 ТБ SSD")
        assert len(pools) == 2
        assert pools[0]["disk_type"] == "NVMe"
        assert pools[1] == {"disk_type": "SSD", "disk_count": 2, "disk_size_gb": 2000}

    def test_latin_tb(self):
        assert parse_ref_disks("2 * 2 TB NVME")[0]["disk_size_gb"] == 2000

    def test_pathological_row_111(self):
        # кириллическая «х» и отсутствующая единица (10 → ТБ)
        pools = parse_ref_disks("2 x 3,84 TB NVMe + 2 х 10 HDD")
        assert len(pools) == 2
        assert pools[0] == {"disk_type": "NVMe", "disk_count": 2, "disk_size_gb": 4000}
        assert pools[1]["disk_type"] == "HDD"
        assert pools[1]["disk_size_gb"] == 8000  # 10 ТБ снапится к 8000 (край сетки)

    def test_empty(self):
        assert parse_ref_disks("") == []


@pytest.fixture
def workbook(tmp_path):
    wb = openpyxl.Workbook()
    default = wb.active
    default.title = "Лист 65"
    auto = wb.create_sheet("Июнь_26_auto")
    auto.append(["CPU", "RAM", "HDD"])
    ws = wb.create_sheet("Март_26_Тест")
    rows = [
        ["CPU", "RAM", "HDD"],
        ["2 поколение Intel", None, None],
        ["2 × Intel Xeon Silver 4214R 2.4 ГГц, 12 ядер 24 потока",
         "64 ГБ", "2 * 960 ГБ SSD"],
        ["2 * 5317 (@3,0 - 3,6, 12 ядер, 24 потока)", "192 ГБ", "2 * 1 ТБ NVME"],
        ["2 * 6346 (@3,1 -3,6, 16 ядер, 32 потока)", "узнать цену закупки", None],
        ["2 * Silver 4314 (@2,4 - 3,4, 16 ядер, 32 потока)", "128 ГБ",
         "2 * 480 ГБ SSD + 2 * 2 ТБ SSD"],
        ["Скидки", None, None],
    ]
    for row in rows:
        ws.append(row)
    # ручной лист должен идти раньше — переносим в начало после «Лист 65»
    wb.move_sheet("Март_26_Тест", offset=-(len(wb.sheetnames) - 2))
    path = tmp_path / "test.xlsx"
    wb.save(path)
    return path


class TestConvert:
    def test_sheet_autopick_skips_list_and_auto(self, workbook):
        wb = openpyxl.load_workbook(workbook, read_only=True)
        assert pick_sheet(wb) == "Март_26_Тест"
        wb.close()

    def test_convert_produces_expected_configs(self, workbook):
        result = convert(workbook, None, ALIASES)
        configs = result["configs"]
        assert len(configs) == 3  # заголовок/поколение/«узнать цену»/футер отсеяны
        assert configs[0]["config_id"] == "MIR-001"
        assert configs[0]["cpu_model"] == "Intel Xeon Silver 4214R"
        assert configs[0]["ram_gb"] == 64
        assert configs[1]["cpu_model"] == "Intel Xeon Gold 5317"
        assert len(configs[2]["disk_pools"]) == 2
        assert result["generated_from"] == "Март_26_Тест"

    def test_source_rows_recorded(self, workbook):
        configs = convert(workbook, None, ALIASES)["configs"]
        assert [c["source_row"] for c in configs] == [3, 4, 6]

    def test_explicit_missing_sheet_raises(self, workbook):
        with pytest.raises(ValueError, match="не найден"):
            convert(workbook, "Нет_такого", ALIASES)
