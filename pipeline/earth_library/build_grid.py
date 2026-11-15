"""Build the Earth feature table: bare-ground filter, gap-filling, heat model, features per band.
Writes data/features/earth_cells.parquet (+ gap_model_cv.json). Cells are the named regions
and sample points; both are kept (matching decides what to use).
Step 7-8; plan s6. Run: python -m pipeline.earth_library.build_grid
"""
import json
from pathlib import Path

import pandas as pd

from ..common.config import get_processing
from ..common.ground import hardness
from ..common.io import FEATURES_DIR, feature_row, write_features
from ..common.schema import BANDS
from ..earth_ml.train_subsurface import fill_gaps
from ..physics.heat_model import band_top_m, earth_band_temps, earth_frozen_fraction
from .local_layers import GLIM_LOOKUP, glim_to_rock, load_layers

ROOT = Path(__file__).resolve().parents[2]


def ground_for_band(rec, band, p):
    top = band_top_m(band, p)
    bd = rec["bedrock_depth_m"]
    if bd == bd and bd <= top:
        return "bedrock", False
    ground = rec["ground_class"]
    if ground == "bedrock":                      # labelled bedrock but depth says deeper
        ground = "dry_regolith"
    # Ice in this band if the cell has ground ice AND either the band is below the summer thaw
    # (active layer) or ERA5-Land says this band stays frozen all year (e.g. Dry Valleys,
    # where the top metre never thaws; MANIFEST rule for skin / upper_metre).
    frozen_all_year = real_frozen_fraction(rec, band) >= 0.99
    ice = bool(rec["ice"]) and (top >= p["ground_rules"]["earth_active_layer_m"] - 1e-9 or frozen_all_year)
    if not ice and ground in ("ice_cemented", "massive_ice"):
        ground = "dry_regolith"                  # active layer above the permafrost table
    return ground, ice


# ERA5-Land soil layers behind each band: 1 = 0-7 cm, 2 = 7-28 cm, 3 = 28-100 cm, 4 = 100-289 cm.
FROZEN_LAYERS = {"skin": ["frozen_l1"], "upper_metre": ["frozen_l2", "frozen_l3"],
                 "drill_zone": ["frozen_l4"], "deep": ["frozen_l4"]}


def real_frozen_fraction(rec, band):
    """Share of months below 0 °C from ERA5-Land soil temperature (build_layers extras), or NaN."""
    values = [rec.get(c, float("nan")) for c in FROZEN_LAYERS[band]]
    values = [v for v in values if v == v and v is not None]
    return sum(values) / len(values) if values else float("nan")


def add_real_frozen(layers):
    """Bring the ERA5-Land frozen-share extras (frozen_l1..4) from data/earth/layers.parquet.

    common/sources.py keeps only the contract columns, so these extras are re-joined here
    by cell_id. Cells without them fall back to the heat model in extract_earth.
    """
    path = ROOT / "data" / "earth" / "layers.parquet"
    extras = ["frozen_l1", "frozen_l2", "frozen_l3", "frozen_l4"]
    if not path.exists() or all(c in layers for c in extras):
        return layers
    real = pd.read_parquet(path)
    if not set(extras) <= set(real.columns):
        return layers
    return layers.merge(real[["cell_id"] + extras], on="cell_id", how="left")


def extract_earth(layers, params=None):
    p = params or get_processing()
    lookup = dict(pd.read_csv(GLIM_LOOKUP).values)
    rows = []
    for rec in layers.to_dict("records"):
        rock = glim_to_rock(rec["glim_class"], lookup)
        for band in BANDS:
            mean, swing = earth_band_temps(rec["mean_temp_k"], rec["annual_range_k"],
                                           rec["diurnal_amp_k"], rec["thermal_inertia"], band, p)
            frozen = real_frozen_fraction(rec, band)
            if frozen != frozen:                 # NaN: no ERA5 soil layers -> heat model estimate
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
            # Unknown values (None / NaN, e.g. rock over water) are left out: scoring skips them.
            rows += [feature_row("earth", rec["cell_id"], rec["lat"], rec["lon"], band, f, v, q)
                     for f, v, q in vals if v is not None and v == v]
    return rows


def build_earth(params=None, out_dir=None, layers=None):
    """layers: optional ready-made layers table (e.g. the real data/earth/layers.parquet from
    build_layers.py); default = whatever config processing.sources gives (synthetic in the repo)."""
    p = params or get_processing()
    layers = load_layers(p) if layers is None else layers
    layers = add_real_frozen(layers)
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
