"""Tests for pipeline/validation/evaluate.py, on small made-up tables.

Run from the repo folder:  python -m pytest tests/
"""

import json

import numpy as np
import pandas as pd
import pytest

from pipeline.validation import evaluate
from test_matching import fake_earth, fake_mars   # same made-up data as the scoring tests


def fake_pairs():
    return pd.DataFrame({
        "pair_id": ["p1", "p2", "p3"],
        "mars_name": ["icy plain", "dry volcano", "nowhere"],
        "mars_lat": [67.5, 18.4, 68.0],              # close to, not exactly on, the cells
        "mars_lon": [-125.0, 77.5, -126.0],
        "earth_region_id": ["devon", "atacama", "not_in_library"],
        "depth_band": ["upper_metre", "upper_metre", "upper_metre"],
        "source_url": ["x", "y", "z"],
    })


def test_region_uses_its_best_cell():
    distances = np.array([0.9, 0.1, 0.5])
    regions = np.array(["a", "a", "b"])
    assert evaluate.region_ranking(distances, regions) == ["a", "b"]


def test_nearest_mars_row():
    mars = pd.DataFrame({"lat": [0.0, 50.0], "lon": [0.0, 0.0]})
    assert evaluate.nearest_mars_row(mars, 48.0, 1.0) == 1


def test_random_baseline_is_about_k_over_n():
    rng = np.random.default_rng(0)
    result = evaluate.summarize([1, 2, 3, 4], n_regions=20, runs=2000, rng=rng)
    assert result["random_recall_at_10"] == pytest.approx(10 / 20, abs=0.03)
    assert result["recall_at_1"] == 0.25 and result["mean_rank"] == 2.5


def test_evaluate_end_to_end(tmp_path):
    tables_mars = {"upper_metre": fake_mars()}
    tables_earth = {"upper_metre": fake_earth()}
    result = evaluate.evaluate(tables_mars, tables_earth, fake_pairs(), runs=200)

    # The keys the plan (s3) requires are all there.
    for key in ["matcher", "recall_at_10", "recall_at_20", "random_recall_at_20",
                "per_band", "n_pairs", "notes"]:
        assert key in result

    # p3 can't be checked (region not in library) and is explained in notes.
    assert result["n_pairs"] == 2
    assert any("not_in_library" in note for note in result["notes"])

    # On this tiny made-up set, both agreed regions should come out on top.
    assert result["overall"]["recall_at_1"] == 1.0
    assert "upper_metre" in result["per_band"]
    assert set(result["ablation_mean_rank_change"]) >= {"ice", "rock"}

    path = evaluate.write_validation(result, tmp_path / "validation.json")
    assert json.loads(path.read_text())["n_pairs"] == 2
