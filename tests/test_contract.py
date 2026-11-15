"""Contract tests for the placeholder output/*.json (plan s3 + s15a).

Reads the committed files in output/ (made by `python -m pipeline.common.make_placeholders`).
Checks shapes and vocabularies the web team relies on.
"""
import json
from pathlib import Path

import pytest

from pipeline.common.schema import BANDS, GROUND, ROCK
from pipeline.matching import normalize

OUT = Path(__file__).resolve().parents[1] / "output"
FEATURE_NAMES = set(normalize.load_feature_file()["features"])
LEVELS = {"excellent", "good", "fair", "poor", "no_match", "insufficient_data"}
CONFIDENCE = {"high", "medium", "low"}
TWIN_KEYS = {"region_id", "name", "new_site", "lat", "lon", "match_pct", "level", "best", "worst", "confidence"}
RECALL_KEYS = {"n_pairs", "recall_at_1", "recall_at_3", "recall_at_10", "recall_at_20", "mean_rank",
               "random_recall_at_3", "random_recall_at_20", "random_mean_rank"}


def load(*parts):
    return json.load(open(OUT.joinpath(*parts), encoding="utf-8"))


def check_twin(t):
    assert TWIN_KEYS <= set(t)
    assert isinstance(t["new_site"], bool)
    assert t["level"] in LEVELS and t["confidence"] in CONFIDENCE
    for key in ("best", "worst"):
        assert len(t[key]) <= 3
        assert set(t[key]) <= FEATURE_NAMES      # feature names, not table column names
    assert 0 <= t["match_pct"] <= 100


def check_conditions(m):
    assert m["ground_class"] is None or m["ground_class"] in GROUND
    assert m["rock"] is None or m["rock"] in ROCK
    assert m["hardness"] is None or 0 <= m["hardness"] <= 3
    assert m["frozen_fraction"] is None or 0 <= m["frozen_fraction"] <= 1


@pytest.mark.parametrize("band", BANDS)
def test_grid(band):
    d = load("grid", f"{band}.json")
    assert d["band"] == band and d["fake"] is True and d["matcher"] in {"knn", "deep", "hybrid"}
    assert d["cells"]
    levels = set()
    for c in d["cells"]:
        check_conditions(c["mars"])
        assert 1 <= len(c["twins"]) <= 3
        for t in c["twins"]:
            check_twin(t)
            levels.add(t["level"])
    assert "insufficient_data" in levels          # so the web team can test that state


def test_new_sites_present():
    twins = [t for c in load("grid", "upper_metre.json")["cells"] for t in c["twins"]]
    assert any(t["new_site"] for t in twins)


def test_showcase():
    d = load("showcase.json")
    assert d["fake"] is True and d["sites"]
    for s in d["sites"]:
        assert set(s["bands"]) == set(BANDS)
        for b in s["bands"].values():
            check_conditions(b["mars"])
            assert set(b["data_quality"].values()) <= {"measured", "modelled", "predicted"}
            assert 1 <= len(b["twins"]) <= 5
            for t in b["twins"]:
                check_twin(t)


def test_earth_library_shape():
    d = load("earth_library.json")
    assert d["fake"] is True and set(d["levels"]) == {"excellent", "good", "fair", "poor"}
    assert 0 < d["min_coverage"] <= 1 and d["spacing_km"] > 0
    assert set(d["bands"]) == set(BANDS)
    for band in d["bands"].values():
        names = [f["name"] for f in band["features"]]
        assert set(names) <= FEATURE_NAMES
        assert set(band["weights"]) == set(names) and band["steepness"] > 0
        for f in band["features"]:
            assert {"name", "label", "compare"} <= set(f)
        for p in band["places"]:
            assert {"id", "name", "new_site", "lat", "lon", "v"} <= set(p)
            assert len(p["v"]) == len(names)


def test_validation_shape():
    d = load("validation.json")
    assert d["fake"] is True
    assert {"matcher", "recall_at_10", "recall_at_20", "random_recall_at_20", "per_band", "n_pairs",
            "notes", "overall", "n_regions", "ablation_mean_rank_change"} <= set(d)
    assert isinstance(d["notes"], list) and all(isinstance(n, str) for n in d["notes"])
    assert RECALL_KEYS <= set(d["overall"]) and d["n_regions"] > 0
    assert d["per_band"]
    for band, stats in d["per_band"].items():
        assert band in BANDS and RECALL_KEYS <= set(stats)
    assert set(d["ablation_mean_rank_change"]) <= FEATURE_NAMES
