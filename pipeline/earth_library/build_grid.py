"""Build the Earth feature table: bare-ground filter, gap-filling, heat model, features per band.
Writes data/features/earth_cells.parquet (+ gap_model_cv.json). Cells are the named regions
and sample points; both are kept (matching decides what to use).
Step 7-8; plan s6. Run: python -m pipeline.earth_library.build_grid
"""
import json

import pandas as pd

from ..common.config import get_processing
from ..common.ground import hardness
from ..common.io import FEATURES_DIR, feature_row, write_features
from ..common.schema import BANDS
from ..earth_ml.train_subsurface import fill_gaps
from ..physics.heat_model import band_top_m, earth_band_temps, earth_frozen_fraction
from .local_layers import GLIM_LOOKUP, glim_to_rock, load_layers


def ground_for_band(rec, band, p):
    top = band_top_m(band, p)
    bd = rec["bedrock_depth_m"]
    if bd == bd and bd <= top:
        return "bedrock", False
    ground = rec["ground_class"]
    if ground == "bedrock":                      # labelled bedrock but depth says deeper
        ground = "dry_regolith"
    ice = bool(rec["ice"]) and top >= p["ground_rules"]["earth_active_layer_m"] - 1e-9
    if not ice and ground in ("ice_cemented", "massive_ice"):
        ground = "dry_regolith"                  # active layer above the permafrost table
    return ground, ice


def extract_earth(layers, params=None):
    p = params or get_processing()
    lookup = dict(pd.read_csv(GLIM_LOOKUP).values)
    rows = []
    for rec in layers.to_dict("records"):
        rock = glim_to_rock(rec["glim_class"], lookup)
        for band in BANDS:
            mean, swing = earth_band_temps(rec["mean_temp_k"], rec["annual_range_k"],
                                           rec["diurnal_amp_k"], rec["thermal_inertia"], band, p)
            frozen = earth_frozen_fraction(rec["mean_temp_k"], rec["annual_range_k"],
                                           rec["thermal_inertia"], band, p)
            ground, ice = ground_for_band(rec, band, p)
            q_ice, q_gc = rec["ice_q"], rec["ground_class_q"]
            vals = [("temp_mean", mean, "modelled"), ("frozen_fraction", frozen, "modelled"),
                    ("temp_swing", swing, "modelled"), ("ice", int(ice), q_ice), ("ground_class", ground, q_gc),
                    ("hardness", hardness(ground, rock, p), q_gc), ("rock", rock, "measured"),
                    ("slope_deg", rec["slope_deg"], "measured"),
                    ("roughness_m", rec["roughness_m"], "measured"),
                    ("dryness", rec["aridity"], "measured")]
            rows += [feature_row("earth", rec["cell_id"], rec["lat"], rec["lon"], band, f, v, q)
                     for f, v, q in vals]
    return rows


def build_earth(params=None, out_dir=None):
    p = params or get_processing()
    layers = load_layers(p)
    # named analog regions are always kept (team-chosen sites; a region point can fall on a lake or ice
    # sheet, and WorldCover has no Antarctica); grid cells must pass the bare-ground filter
    named = layers["name"].fillna("").ne("")
    bare = layers["bare_fraction"] >= p["bare_ground_min_fraction"]
    layers = layers[named | bare].reset_index(drop=True)
    filled, cv = fill_gaps(layers, p)
    path = write_features(extract_earth(filled, p), "earth", out_dir)
    cv_path = path.parent / "gap_model_cv.json"
    cv_path.write_text(json.dumps(cv))
    return path


if __name__ == "__main__":
    print(build_earth())
