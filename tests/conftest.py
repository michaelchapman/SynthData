import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
EXAMPLE = ROOT / "examples" / "omop-mini"


@pytest.fixture
def dictionary_profile():
    return json.loads((EXAMPLE / "profile-dictionary-only.json").read_text())


@pytest.fixture
def example_overrides():
    return json.loads((EXAMPLE / "overrides.json").read_text())
