import pytest
import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def miran_html():
    path = FIXTURES / "miran_dedicated.html"
    if not path.exists():
        pytest.skip("fixture miran_dedicated.html not captured yet — run tests/capture_fixtures.py")
    return path.read_bytes()


@pytest.fixture(scope="session")
def regcloud_html():
    path = FIXTURES / "regcloud_dedicated.html"
    if not path.exists():
        pytest.skip("fixture regcloud_dedicated.html not captured yet — run tests/capture_fixtures.py")
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def selectel_flat():
    path = FIXTURES / "selectel_payload.json"
    if not path.exists():
        pytest.skip("fixture selectel_payload.json not captured yet — run tests/capture_fixtures.py")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def netrack_html():
    path = FIXTURES / "netrack_dedicated.html"
    if not path.exists():
        pytest.skip("fixture netrack_dedicated.html not captured yet — run tests/capture_fixtures.py")
    return path.read_bytes()


@pytest.fixture(scope="session")
def timeweb_html():
    path = FIXTURES / "timeweb_dedicated.html"
    if not path.exists():
        pytest.skip("fixture timeweb_dedicated.html not captured yet — run tests/capture_fixtures.py")
    return path.read_bytes()


@pytest.fixture(scope="session")
def hostkey_html():
    path = FIXTURES / "hostkey_dedicated.html"
    if not path.exists():
        pytest.skip("fixture hostkey_dedicated.html not captured yet — run tests/capture_fixtures.py")
    return path.read_bytes()
