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
from ..earth_ml.profile_nn import SUB_COLS, band_temp_k
from ..physics.heat_model import band_mid_depth_m, band_top_m, earth_band_temps, earth_frozen_fraction
from .local_layers import GLIM_LOOKUP, glim_to_rock, load_layers

ROOT = Path(__file__).resolve().parents[2]
SUBSURFACE_BANDS = ("upper_metre", "drill_zone", "deep")   # skin keeps the surface heat model


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
    if rec["ice"] != rec["ice"] or rec["ice"] is None:     # unknown: the permafrost model was unsure
        return ground, None
    frozen_all_year = real_frozen_fraction(rec, band) >= 0.99
    # Active layer: ESA CCI active-layer thickness where it exists, else the config default.
    alt = rec.get("cci_alt_m", float("nan"))
    active = alt if alt == alt and alt is not None else p["ground_rules"]["earth_active_layer_m"]
    ice = bool(rec["ice"]) and (top >= active - 1e-9 or frozen_all_year)
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


def near_known_pair_site(layers, radius_km=100):
    """True for cells within radius_km of an Earth site in pipeline/validation/known_pairs.csv.

    Known analog sites (e.g. Tuktoyaktuk tundra) are kept even when not bare ground,
    like the named regions, so validation can check them.
    """
    import numpy as np
    pairs_csv = ROOT / "pipeline" / "validation" / "known_pairs.csv"
    keep = np.zeros(len(layers), dtype=bool)
    if not pairs_csv.exists():
        return keep
    pairs = pd.read_csv(pairs_csv)
    if not {"earth_lat", "earth_lon"} <= set(pairs.columns):
        return keep
    lat = np.radians(layers["lat"].to_numpy(float))
    lon = np.radians(layers["lon"].to_numpy(float))
    for plat, plon in pairs[["earth_lat", "earth_lon"]].dropna().drop_duplicates().to_numpy(float):
        a, b = np.radians(plat), np.radians(plon)
        h = np.sin((lat - a) / 2) ** 2 + np.cos(lat) * np.cos(a) * np.sin((lon - b) / 2) ** 2
        keep |= 2 * 6371.0 * np.arcsin(np.sqrt(h)) <= radius_km
    return keep


def add_real_frozen(layers):
    """Bring the extras (ERA5-Land frozen share frozen_l1..4, ice_source, CCI active layer cci_alt_m)
    from data/earth/layers.parquet.

    common/sources.py keeps only the contract columns, so these extras are re-joined here
    by cell_id. Cells without them fall back to the heat model in extract_earth.
    """
    path = ROOT / "data" / "earth" / "layers.parquet"
    extras = ["frozen_l1", "frozen_l2", "frozen_l3", "frozen_l4", "ice_source", "cci_alt_m",
              *SUB_COLS, "sub_source"]
    if not path.exists() or all(c in layers for c in extras):
        return layers
    real = pd.read_parquet(path)
    extras = [c for c in extras if c in real.columns and c not in layers]
    if not extras or "cell_id" not in layers:
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
            q_temp = "modelled"
            # Below the skin: measured-model CCI ground temperature or the PyTorch ensemble (profile_nn)
            # instead of "mean at depth = surface mean". Extrapolating cells keep the heat model.
            if band in SUBSURFACE_BANDS:
                sub_k = band_temp_k(rec, band_mid_depth_m(band, p))
                if sub_k == sub_k:
                    mean = sub_k
                    q_temp = "predicted" if rec.get("sub_source") == "nn" else "modelled"
            frozen = real_frozen_fraction(rec, band)
            if frozen != frozen:                 # NaN: no ERA5 soil layers -> heat model estimate
                frozen = earth_frozen_fraction(rec["mean_temp_k"], rec["annual_range_k"],
                                               rec["thermal_inertia"], band, p)
            ground, ice = ground_for_band(rec, band, p)
            q_ice, q_gc = rec["ice_q"], rec["ground_class_q"]
            vals = [("temp_mean", mean, q_temp), ("frozen_fraction", frozen, "modelled"),
                    ("temp_swing", swing, "modelled"), ("ice", None if ice is None else int(ice), q_ice), ("ground_class", ground, q_gc),
                    ("hardness", hardness(ground, rock, p), q_gc), ("rock", rock, "measured"),
                    ("slope_deg", rec["slope_deg"], "measured"),
                    ("roughness_m", rec["roughness_m"], "measured"),
                    ("dryness", rec["aridity"], "measured")]
            # Unknown values (None / NaN, e.g. rock over water) are left out: scoring skips them.
            rows += [feature_row("earth", rec["cell_id"], rec["lat"], rec["lon"], band, f, v, q)
                     for f, v, q in vals if v is not None and v == v]
    return rows


LOOKUP_NAME = "earth_all"   # data/features/earth_all_cells.parquet


def build_lookup_table(all_land, candidates, p):
    """Features for every land cell that is NOT a matching candidate (cities, farmland, forest...),
    so the reverse search ("where on Mars is like Calgary?") can use the clicked cell itself.
    Matching never reads this table. Same rules as the candidates; the ground-class gap model is
    re-fitted on all land so its predictions cover these cells."""
    rest = all_land[~all_land["cell_id"].isin(set(candidates["cell_id"]))].reset_index(drop=True)
    if rest.empty:
        return None
    filled, _ = fill_gaps(all_land.reset_index(drop=True), p)
    filled = filled[filled["cell_id"].isin(set(rest["cell_id"]))]
    return write_features(extract_earth(filled, p), LOOKUP_NAME)


def build_earth(params=None, out_dir=None, layers=None):
    """layers: optional ready-made layers table (e.g. the real data/earth/layers.parquet from
    build_layers.py); default = whatever config processing.sources gives (synthetic in the repo)."""
    p = params or get_processing()
    layers = load_layers(p) if layers is None else layers
    layers = add_real_frozen(layers)
    # A named region whose cell has no measured data (e.g. Lanzarote: its 0.5 deg cell is ocean)
    # would only carry gap-model guesses and could look like a perfect match: drop it.
    if layers["mean_temp_k"].notna().any():
        layers = layers[layers["mean_temp_k"].notna()].reset_index(drop=True)
    # named analog regions are always kept (team-chosen sites; a region point can fall on a lake or ice
    # sheet, and WorldCover has no Antarctica); grid cells must pass the bare-ground filter
    named = layers["name"].fillna("").ne("")
    bare = layers["bare_fraction"] >= p["bare_ground_min_fraction"]
    all_land = layers
    layers = layers[named | bare | near_known_pair_site(layers)].reset_index(drop=True)
    if out_dir is None and "ice_source" in layers:
        build_lookup_table(all_land, layers, p)
    filled, cv = fill_gaps(layers, p)
    path = write_features(extract_earth(filled, p), "earth", out_dir)
    cv_path = path.parent / "gap_model_cv.json"
    cv_path.write_text(json.dumps(cv))
    if "ice_source" in layers and out_dir is None:
        from ..earth_ml.permafrost import write_report
        write_report({"ground_class_gap_model": {
            "task": "ground_class where it is still unknown (cells where the permafrost model was unsure)",
            "spatial_block_cv_accuracy": cv.get("ground_class"),
            "note": "perm_prob removed from the predictors (it leaked the labels; CV was ~0.99 before)"}})
    return path


if __name__ == "__main__":
    print(build_earth())
