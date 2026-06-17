# Unit tests for dedicated_scraper normalization functions

import pytest
import sys
import os

# Add parent directory to path to import dedicated_scraper
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dedicated_scraper import (
    normalize_disk_gb,
    normalize_ram_gb,
    extract_cpu_generation,
    normalize_cpu_model,
    normalize_disk_type,
)


def test_normalize_disk_gb():
    """Test disk size snapping to standards."""
    assert normalize_disk_gb(960) == 1000
    assert normalize_disk_gb(1000) == 1000
    assert normalize_disk_gb(1024) == 1000
    assert normalize_disk_gb(1920) == 2000
    assert normalize_disk_gb(2000) == 2000
    assert normalize_disk_gb(2048) == 2000
    assert normalize_disk_gb(480) == 480
    assert normalize_disk_gb(500) == 480
    assert normalize_disk_gb(240) == 240
    assert normalize_disk_gb(250) == 240


def test_normalize_ram_gb():
    """Test RAM snapping to power-of-2 standards."""
    assert normalize_ram_gb(63) == 64
    assert normalize_ram_gb(64) == 64
    assert normalize_ram_gb(65) == 64
    assert normalize_ram_gb(127) == 128
    assert normalize_ram_gb(128) == 128
    assert normalize_ram_gb(129) == 128
    assert normalize_ram_gb(31) == 32
    assert normalize_ram_gb(32) == 32
    assert normalize_ram_gb(33) == 32


def test_extract_cpu_generation():
    """Test CPU generation extraction from model names."""
    # Intel E3 series
    assert extract_cpu_generation("Intel Xeon E3-1230 V5") == "Skylake"
    assert extract_cpu_generation("Intel Xeon E3-1270 V6") == "Kaby Lake"
    assert extract_cpu_generation("Intel Xeon E3-1240 V4") == "Broadwell"
    assert extract_cpu_generation("Intel Xeon E3-1220 V3") == "Haswell"
    assert extract_cpu_generation("Intel Xeon E3-1230 V2") == "Ivy Bridge"
    assert extract_cpu_generation("Intel Xeon E3-1230") == "Sandy Bridge"

    # Intel E5 series
    assert extract_cpu_generation("Intel Xeon E5-2630 V4") == "Broadwell EP"
    assert extract_cpu_generation("Intel Xeon E5-2680 V3") == "Haswell EP"
    assert extract_cpu_generation("Intel Xeon E5-2650 V2") == "Ivy Bridge EP"
    assert extract_cpu_generation("Intel Xeon E5-2670") == "Sandy Bridge EP"

    # Intel Scalable
    assert extract_cpu_generation("Intel Xeon Gold 6226R") == "Cascade Lake"
    assert extract_cpu_generation("Intel Xeon Gold 6140") == "Skylake-SP"
    assert extract_cpu_generation("Intel Xeon Silver 4214R") == "Cascade Lake"
    assert extract_cpu_generation("Intel Xeon Bronze 3204") == "Skylake-SP"

    # AMD EPYC
    assert extract_cpu_generation("AMD EPYC 9654") == "Genoa"
    assert extract_cpu_generation("AMD EPYC 7302") == "Milan"
    assert extract_cpu_generation("AMD EPYC 7302P") == "Milan"
    assert extract_cpu_generation("AMD EPYC 7002") == "Rome/Naples"

    # Unknown
    assert extract_cpu_generation("Unknown Chip X99") == ""
    assert extract_cpu_generation("Intel Core i7-9700K") == ""


def test_normalize_cpu_model():
    """Test CPU model normalization."""
    assert normalize_cpu_model("Intel Xeon  E3-1230  V5") == "intel xeon e3-1230 v5"
    assert normalize_cpu_model("  AMD EPYC 7302  ") == "amd epyc 7302"
    assert normalize_cpu_model("Intel Xeon Gold 6226R.") == "intel xeon gold 6226r"
    assert normalize_cpu_model("Intel   Xeon   E5-2630") == "intel xeon e5-2630"


def test_normalize_disk_type():
    assert normalize_disk_type("NVMe") == "NVMe"
    assert normalize_disk_type("SSD NVMe M.2") == "NVMe"  # nvme wins over ssd
    assert normalize_disk_type("SSD SATA") == "SSD"
    assert normalize_disk_type("SSD") == "SSD"
    assert normalize_disk_type("HDD SATA") == "HDD"
    assert normalize_disk_type("hdd") == "HDD"
    assert normalize_disk_type("") == "HDD"  # default
