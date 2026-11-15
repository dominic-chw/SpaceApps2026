"""Tests for load_tables.py (Dominic's long tables -> scoring tables) and run_all.py.

Uses tiny made-up long tables in the same layout as data/features/{mars,earth}_cells.parquet.
Run from the repo folder:  python -m pytest tests/
"""

import json

import pandas as pd

import pipeline.run_all as run_all
from pipeline.matching import load_tables
from pipeline.validation import evaluate

FEATURES = {"temp_mean": 200.0, "frozen_fraction": 1.0, "temp_swing": 30.0, "ice": 1.0,
            "ground_class": "ice_cemented", "hardness": 2.0, "rock": "volcanic",
            "slope_deg": 2.0, "roughness_m": 0.5, "dryness": 0.8}
CATEGORY = {"ground_class", "rock"}


def long_table(planet, cells):
    """cells: list of (cell_id, lat, lon, offset). Every band, every feature."""
    rows = []
    for cell_id, lat, lon, offset in cells:
        for band in ["skin", "upper_metre", "drill_zone", "deep"]:
            for feature, value in FEATURES.items():
                is_word = feature in CATEGORY
                rows.append({"planet": planet, "cell_id": cell_id, "lat": lat, "lon": lon,
                             "band": band, "feature": feature,
                             "value_num": None if is_word else value + (0 if feature == "ice" else offset),
                             "value_cat": value if is_word else None,
                             "data_quality": "modelled"})
    return pd.DataFrame(rows)


MARS = long_table("mars", [("m1", 18.0, 77.0, 0.0), ("m2", 68.0, -126.0, 5.0),
                           ("m3", -5.0, 137.0, 9.0)])
EARTH = long_table("earth", [("dry_valleys", -77.9, 160.7, 0.0), ("sample_0001", 40.0, 10.0, 4.0),
                             ("sample_0002", -20.0, -60.0, 8.0), ("sample_0003", 60.0, 100.0, 1.0)])


def test_to_wide_one_row_per_place_with_quality_and_words():
    wide = load_tables.to_wide(MARS, "upper_metre")
    assert len(wide) == 3
    assert {"temp_mean", "temp_mean_quality", "rock"} <= set(wide.columns)
    assert set(wide["ice"]) == {"yes"}          # 1.0 -> "yes"
    assert set(wide["rock"]) == {"volcanic"}


def test_known_regions_get_tagged_others_are_new():
    _, earth = load_tables.load_band_tables("skin", mars_long=MARS, earth_long=EARTH)
    tagged = earth.set_index("cell_id")
    assert tagged.loc["dry_valleys", "region_id"] == "dry_valleys"
    assert "Dry Valleys" in tagged.loc["dry_valleys", "name"]
    assert pd.isna(tagged.loc["sample_0001", "region_id"])


def test_run_all_writes_every_output(tmp_path, monkeypatch):
    monkeypatch.setattr(run_all, "OUTPUT", tmp_path)
    monkeypatch.setattr(load_tables, "read_long", lambda planet, folder=None: MARS if planet == "mars" else EARTH)
    pairs = pd.DataFrame({"pair_id": ["p1"], "mars_name": ["x"], "mars_lat": [18.0],
                          "mars_lon": [77.0], "earth_region_id": ["dry_valleys"],
                          "depth_band": ["skin"], "source_url": ["u"]})
    monkeypatch.setattr(evaluate, "load_known_pairs", lambda path=None: pairs)
    run_all.run()

    for band in ["skin", "upper_metre", "drill_zone", "deep"]:
        grid = json.loads((tmp_path / "grid" / f"{band}.json").read_text(encoding="utf-8"))
        assert grid["band"] == band and len(grid["cells"]) == 3
        assert grid["cells"][0]["mars"]["ice"] == "yes"
    showcase = json.loads((tmp_path / "showcase.json").read_text(encoding="utf-8"))
    assert set(showcase["sites"][0]["bands"]) == {"skin", "upper_metre", "drill_zone", "deep"}
    assert showcase["sites"][0]["bands"]["skin"]["data_quality"]["rock"] == "modelled"
    library = json.loads((tmp_path / "earth_library.json").read_text(encoding="utf-8"))
    assert set(library["bands"]) == {"skin", "upper_metre", "drill_zone", "deep"}
    validation = json.loads((tmp_path / "validation.json").read_text(encoding="utf-8"))
    assert validation["n_pairs"] == 1
