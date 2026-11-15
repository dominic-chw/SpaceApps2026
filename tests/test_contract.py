"""Contract tests for the fake placeholder outputs (plan s3 + level/confidence)."""
import json
from pathlib import Path

import pytest

from pipeline.common import fake_outputs
from pipeline.common.schema import BANDS, GROUND, ROCK

LEVELS = {"excellent", "good", "fair", "poor", "no_match", "insufficient_data"}
CONFIDENCE = {"high", "medium", "low"}
TWIN_KEYS = {"region_id", "name", "lat", "lon", "match_pct", "level", "best", "worst", "confidence"}


@pytest.fixture(scope="module")
def out(tmp_path_factory):
    d = tmp_path_factory.mktemp("output")
    fake_outputs.write_all(d)
    return Path(d)


def check_twin(t):
    assert set(t) >= TWIN_KEYS
    assert t["level"] in LEVELS and t["confidence"] in CONFIDENCE
    assert len(t["best"]) == 3 and len(t["worst"]) == 3
    assert 0 <= t["match_pct"] <= 100


def check_conditions(m):
    assert m["ground_class"] in GROUND and m["rock"] in ROCK
    assert 0 <= m["hardness"] <= 3 and 0 <= m["frozen_fraction"] <= 1


@pytest.mark.parametrize("band", BANDS)
def test_grid(out, band):
    d = json.load(open(out / "grid" / f"{band}.json"))
    assert d["band"] == band and d["fake"] is True and d["matcher"] in {"knn", "deep", "hybrid"}
    for c in d["cells"]:
        check_conditions(c["mars"])
        assert len(c["twins"]) == 3
        for t in c["twins"]:
            check_twin(t)


def test_showcase(out):
    d = json.load(open(out / "showcase.json"))
    assert d["fake"] is True
    for s in d["sites"]:
        assert set(s["bands"]) == set(BANDS)
        for b in s["bands"].values():
            check_conditions(b["mars"])
            assert set(b["data_quality"].values()) <= {"measured", "modelled", "predicted"}
            assert len(b["twins"]) == 5
            for t in b["twins"]:
                check_twin(t)


def test_other_files(out):
    for name in ("validation.json", "earth_library.json"):
        assert json.load(open(out / name))["fake"] is True
    assert {"recall_at_10", "recall_at_20", "random_recall_at_20", "per_band", "n_pairs", "notes"} <= set(
        json.load(open(out / "validation.json")))


def test_level_cutoffs():
    assert [fake_outputs.level_for(p) for p in (90, 85, 70, 50, 30, 10)] == [
        "excellent", "excellent", "good", "fair", "poor", "no_match"]
