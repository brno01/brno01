"""Shared fixtures: modules under test and the 66 SVG files generated once per session."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(TOOLS))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def build():
    return _load("build")


@pytest.fixture(scope="session")
def snapmod():
    return _load("snapshot")


@pytest.fixture(scope="session")
def snapshot_path():
    return FIXTURES / "snapshot.json"


@pytest.fixture(scope="session")
def snapshot_data(snapshot_path):
    return json.loads(snapshot_path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def content(build):
    return build.load_content()


@pytest.fixture(scope="session")
def assets_dir(build, snapshot_path, tmp_path_factory):
    out = tmp_path_factory.mktemp("assets") / "dist"
    assert build.main(["assets", "--snapshot", str(snapshot_path), "--out", str(out)]) == 0
    return out


@pytest.fixture(scope="session")
def assets(assets_dir):
    """name -> text of every generated SVG."""
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(assets_dir.iterdir())}
