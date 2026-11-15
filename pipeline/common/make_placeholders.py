"""Make the small FAKE placeholder output/*.json for the web team.

Runs the REAL pipeline (data side + scoring via pipeline.run_all) on synthetic layers,
but on a coarse Mars grid so the files stay small (a 2 degree grid is ~50 MB of JSON).
Validation uses made-up pairs (NOT saved to known_pairs.csv), only to show the shape.
Everything carries "fake": true.

Run from the repo root:  python -m pipeline.common.make_placeholders
"""
import numpy as np
import pandas as pd

from pipeline import run_all
from pipeline.validation import evaluate

from ..earth_library.build_grid import build_earth
from ..mars_features.grid import build_mars
from .config import get_processing

GRID_DEG = 10
# Made-up pairs; Earth ids are real region ids, Mars spots are only rough.
FAKE_PAIRS = [("f1", "Phoenix", 68.2, -125.7, "dry_valleys"), ("f2", "Arcadia", 46.0, -170.0, "devon_haughton"),
              ("f3", "Jezero", 18.4, 77.5, "lake_salda"), ("f4", "Gale", -5.4, 137.8, "hanksville"),
              ("f5", "Meridiani", -2.0, -5.0, "rio_tinto"), ("f6", "Plains", 20.0, -40.0, "atacama_high")]


def make(grid_deg=GRID_DEG):
    # 1. Data side on the coarse grid (synthetic layers).
    params = get_processing({"grid_deg": grid_deg})
    punch_holes(build_mars(params))
    build_earth(params)

    # 2. Scoring for every band, with the grid size reported as the coarse one.
    config = run_all.load_config()
    config["grid_deg"] = grid_deg
    run_all.load_config = lambda path=None: config
    pairs_file = evaluate.load_known_pairs
    evaluate.load_known_pairs = lambda path=None: _fake_pairs()
    try:
        run_all.run(build=False)
    finally:
        evaluate.load_known_pairs = pairs_file


def punch_holes(path, share=0.08, seed=0):
    """Drop most feature rows for ~8% of Mars cells (like missing data), so the
    "insufficient_data" level shows up. Unknown = no row; nothing is invented."""
    table = pd.read_parquet(path)
    rng = np.random.default_rng(seed)
    cells = table["cell_id"].drop_duplicates()
    holes = set(cells[rng.random(len(cells)) < share])
    keep_features = {"temp_mean", "frozen_fraction"}
    drop = table["cell_id"].isin(holes) & ~table["feature"].isin(keep_features)
    table[~drop].to_parquet(path, index=False)


def _fake_pairs():
    pairs = pd.DataFrame(FAKE_PAIRS, columns=["pair_id", "mars_name", "mars_lat", "mars_lon", "earth_region_id"])
    pairs["depth_band"], pairs["source_url"] = "upper_metre", "fake"
    return pairs


if __name__ == "__main__":
    make()
