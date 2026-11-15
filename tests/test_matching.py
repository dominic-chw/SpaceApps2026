"""Tests for the scoring code (pipeline/matching). Uses small made-up tables, no real data.

Run from the repo folder:  python -m pytest tests/
"""

import math

import numpy as np
import pandas as pd
import pytest

from pipeline.matching import explain, match, normalize

FEATURE_FILE = normalize.load_feature_file()


# ---------------------------------------------------------------------------
# Made-up data: 3 Mars cells and 4 Earth places
# ---------------------------------------------------------------------------

def fake_mars():
    return pd.DataFrame({
        "cell_id": ["icy_plain", "dry_volcano", "unknown_spot"],
        "lat": [68.0, 18.0, -30.0],
        "lon": [-126.0, 77.0, 10.0],
        "temp_mean": [190.0, 215.0, np.nan],
        "temp_swing": [60.0, 80.0, np.nan],
        "frozen_fraction": [1.0, 1.0, np.nan],
        "ice": ["yes", "no", np.nan],
        "ground_class": ["ice_cemented", "dry_regolith", np.nan],
        "hardness": [2, 1, np.nan],
        "rock": ["volcanic", "volcanic", "sedimentary"],
        "slope_deg": [1.0, 3.0, np.nan],
        "roughness_m": [0.5, 2.0, np.nan],
        "dryness": [0.9, 0.95, np.nan],
        "ice_quality": ["measured", "measured", None],
    })


def fake_earth():
    return pd.DataFrame({
        "region_id": ["dry_valleys", "devon", "atacama", "hawaii"],
        "name": ["Dry Valleys", "Devon Island", "Atacama", "Hawaii"],
        "lat": [-77.5, 75.4, -24.0, 19.5],
        "lon": [161.0, -89.8, -69.0, -155.5],
        "temp_mean": [253.0, 258.0, 290.0, 285.0],
        "temp_swing": [30.0, 35.0, 25.0, 10.0],
        "frozen_fraction": [1.0, 0.9, 0.0, 0.0],
        "ice": ["TRUE", "yes", "false", "no"],          # mixed spellings on purpose
        "ground_class": ["ice_cemented", "ice_cemented", "dry_regolith", "bedrock"],
        "hardness": [2, 2, 1, 3],
        "rock": ["sedimentary", "sedimentary", "volcanic", "volcanic"],
        "slope_deg": [2.0, 1.5, 3.0, 5.0],
        "roughness_m": [0.6, 0.4, 1.8, 3.0],
        "dryness": [0.8, 0.5, 0.99, 0.3],
        "ice_quality": ["measured", "predicted", "measured", "measured"],
    })


# ---------------------------------------------------------------------------
# features.yaml and weights
# ---------------------------------------------------------------------------

def test_every_feature_has_a_known_compare_method():
    known = {"percentile", "absolute", "log_z", "same", "steps"}
    for name, feature in FEATURE_FILE["features"].items():
        assert feature["compare"] in known, name


def test_bands_pick_the_right_features():
    deep = normalize.features_for_band(FEATURE_FILE, "deep")
    skin = normalize.features_for_band(FEATURE_FILE, "skin")
    assert "dryness" in skin and "dryness" not in deep
    assert "temp_swing" not in deep


def test_weights_only_name_real_features_and_bands():
    weights = match.load_weights()
    assert set(weights) == {"skin", "upper_metre", "drill_zone", "deep"}
    for band, band_weights in weights.items():
        for name, value in band_weights.items():
            assert name in FEATURE_FILE["features"], f"{band}: unknown feature {name}"
            assert value >= 0


def test_missing_weight_defaults_to_one():
    features = {"a": {}, "b": {}}
    assert match.weights_for_band({"skin": {"a": 3}}, "skin", features).tolist() == [3.0, 1.0]


# ---------------------------------------------------------------------------
# normalize.py
# ---------------------------------------------------------------------------

def test_percentile_ranks_within_each_planet():
    features = {"t": {"column": "temp_mean", "compare": "percentile"}}
    mars = pd.DataFrame({"temp_mean": [150.0, 200.0]})
    earth = pd.DataFrame({"temp_mean": [250.0, 300.0]})
    m, e, _ = normalize.normalize_pair(mars, earth, features)
    # The coldest place on each planet gets the same rank, despite 100 K difference.
    assert m[:, 0].tolist() == e[:, 0].tolist() == [0.5, 1.0]


def test_categories_share_codes_and_tidy_spelling():
    features = {"ice": {"column": "ice", "compare": "same"}}
    mars = pd.DataFrame({"ice": ["yes", "no"]})
    earth = pd.DataFrame({"ice": ["TRUE", " No "]})
    m, e, _ = normalize.normalize_pair(mars, earth, features)
    assert m[:, 0].tolist() == e[:, 0].tolist()


def test_word_outside_fixed_list_is_rejected():
    features = {"rock": {"column": "rock", "compare": "same", "allowed": ["volcanic"]}}
    with pytest.raises(ValueError, match="basalt"):
        normalize.normalize_pair(pd.DataFrame({"rock": ["basalt"]}),
                                 pd.DataFrame({"rock": ["volcanic"]}), features)


def test_missing_column_becomes_missing_values():
    features = {"x": {"column": "not_there", "compare": "absolute", "scale": 1}}
    m, e, _ = normalize.normalize_pair(pd.DataFrame({"a": [1]}), pd.DataFrame({"a": [1]}), features)
    assert np.isnan(m[0, 0]) and np.isnan(e[0, 0])


# ---------------------------------------------------------------------------
# match.py: distance, twins, %
# ---------------------------------------------------------------------------

def test_identical_places_have_zero_distance():
    differences = np.array([[0.0, 0.0, 0.0]])
    assert match.gower_distance(differences, np.array([1.0, 2.0, 3.0]))[0] == 0


def test_missing_values_are_skipped_not_counted_as_mismatch():
    # Feature 2 missing: distance uses only feature 1.
    differences = np.array([[0.4, np.nan]])
    assert match.gower_distance(differences, np.array([1.0, 5.0]))[0] == pytest.approx(0.4)


def test_heavier_weight_counts_more():
    differences = np.array([[1.0, 0.0]])
    light = match.gower_distance(differences, np.array([1.0, 3.0]))[0]
    heavy = match.gower_distance(differences, np.array([3.0, 1.0]))[0]
    assert heavy > light


def test_differences_are_capped_at_one_and_categories_are_all_or_nothing():
    mars_row = np.array([0.0, 1.0])
    earth_grid = np.array([[500.0, 2.0]])
    diffs = match.feature_differences(mars_row, earth_grid, np.array([60.0, 1.0]),
                                      np.array([False, True]))
    assert diffs.tolist() == [[1.0, 1.0]]


def test_twins_respect_spacing():
    distances = np.array([0.1, 0.2, 0.3])
    lats = np.array([0.0, 0.5, 40.0])       # first two are ~55 km apart
    lons = np.array([0.0, 0.0, 0.0])
    assert match.pick_twins(distances, lats, lons, 2, spacing_km=200) == [0, 2]


def test_typical_best_twin_scores_75():
    steepness = match.curve_steepness(np.array([0.1, 0.2, 0.3]))
    assert match.match_pct(0.2, steepness) == pytest.approx(75, abs=0.1)
    assert match.match_pct(0.0, steepness) == 100


def test_levels():
    assert match.level_for(90) == "excellent"
    assert match.level_for(70) == "good"
    assert match.level_for(55) == "fair"
    assert match.level_for(30) == "poor"
    assert match.level_for(5) == "no_match"


# ---------------------------------------------------------------------------
# explain.py
# ---------------------------------------------------------------------------

def test_best_and_worst():
    names = ["a", "b", "c", "d"]
    differences = np.array([0.0, 0.4, np.nan, 0.9])
    weights = np.array([1.0, 3.0, 1.0, 1.0])
    best, worst = explain.best_and_worst(differences, weights, names, how_many=2)
    assert best == ["a", "b"]
    assert worst == ["b", "d"]          # 3 x 0.4 = 1.2 beats 1 x 0.9
    assert "c" not in best + worst      # missing values are not explained


def test_a_mismatch_is_never_matched_on():
    best, worst = explain.best_and_worst(np.array([1.0]), np.array([1.0]), ["rock"])
    assert best == [] and worst == ["rock"]


def test_confidence_drops_with_missing_and_deep():
    settings = FEATURE_FILE["confidence"]
    weights = np.ones(4)
    full = np.zeros(4)
    half = np.array([0.0, 0.0, np.nan, np.nan])
    trust = np.ones(4)
    label_full, score_full = explain.confidence(full, weights, trust, trust, "skin", settings)
    _, score_half = explain.confidence(half, weights, trust, trust, "skin", settings)
    _, score_deep = explain.confidence(full, weights, trust, trust, "deep", settings)
    assert label_full == "high"
    assert score_half < score_full and score_deep < score_full


# ---------------------------------------------------------------------------
# Whole band, end to end
# ---------------------------------------------------------------------------

def test_match_band_end_to_end():
    cells, info = match.match_band(fake_mars(), fake_earth(), "upper_metre", how_many=3)
    assert len(cells) == 3

    icy = cells[0]
    # An icy, ice-cemented Mars plain should pick an icy Earth site first.
    assert icy["twins"][0]["region_id"] in {"dry_valleys", "devon"}
    for twin in icy["twins"]:
        assert set(twin) == {"region_id", "name", "new_site", "lat", "lon", "match_pct",
                             "level", "best", "worst", "confidence"}
        assert twin["new_site"] is False    # all fake Earth rows are known regions
        assert 0 <= twin["match_pct"] <= 100
        assert twin["confidence"] in {"high", "medium", "low"}
        assert len(twin["best"]) <= 3 and len(twin["worst"]) <= 3

    # Twins come out best first.
    pcts = [t["match_pct"] for t in icy["twins"]]
    assert pcts == sorted(pcts, reverse=True)

    # A cell with almost no data still gets twins, but with low confidence.
    unknown = cells[2]
    assert unknown["twins"] and all(t["confidence"] == "low" for t in unknown["twins"])
    # ...and is never called "excellent": too little was compared.
    assert all(t["level"] == "insufficient_data" for t in unknown["twins"])
    assert all(t["level"] != "insufficient_data" for t in icy["twins"])
    assert unknown["mars"]["temp_mean"] is None      # NaN became JSON-safe None
    assert not math.isnan(info["curve_steepness"])


# ---------------------------------------------------------------------------
# Global Earth grid: unnamed cells are "new sites"
# ---------------------------------------------------------------------------

def test_unnamed_cells_become_labelled_new_sites():
    earth = pd.DataFrame({
        "region_id": ["devon", None, None],
        "name": ["Devon Island", None, None],
        "cell_id": [None, "e_1", None],
        "lat": [75.4, 74.5, -23.5],
        "lon": [-89.8, -89.8, -69.0],
    })
    ids, names, new = match.site_labels(earth)
    assert ids.tolist() == ["devon", "e_1", "earth_-23.50_-69.00"]
    assert new.tolist() == [False, True, True]
    assert names[1].startswith("New site, ") and "Devon Island" in names[1]  # ~100 km away
    assert names[2] == "New site (23.5°S, 69.0°W)"                          # far from known


def test_new_sites_show_up_as_twins():
    earth = fake_earth()
    # Add an unnamed cell that is a near-copy of Devon Island, 3,000+ km away from it.
    extra = earth.iloc[[1]].copy()
    extra[["region_id", "name"]] = None
    extra["lat"], extra["lon"] = 62.0, 100.0
    earth = pd.concat([earth, extra], ignore_index=True)
    cells, _ = match.match_band(fake_mars(), earth, "upper_metre", how_many=3)
    icy_twins = cells[0]["twins"]
    assert any(t["new_site"] for t in icy_twins)
    new = next(t for t in icy_twins if t["new_site"])
    assert new["name"].startswith("New site")
