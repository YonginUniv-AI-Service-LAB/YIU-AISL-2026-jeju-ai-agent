import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import pytest
from pydantic import ValidationError
from coach.config import MOCK_DIR
from coach.contracts import StoreInput, Evidence


def test_synthetic_stores_valid():
    for c in json.loads((MOCK_DIR / "synthetic_stores.json").read_text(encoding="utf-8")):
        StoreInput.model_validate(c["store_input"])


def test_extra_field_rejected():
    with pytest.raises(ValidationError):
        StoreInput.model_validate({"industry": "cafe", "radius_500m": 1})


def test_claim_id_pattern():
    with pytest.raises(ValidationError):
        Evidence(claim_id="X1", metric="m", value=1, unit="pct", formula="f", source_nos=[753], period="p")
