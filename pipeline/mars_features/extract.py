"""Extract Mars features per cell and band from source layers.
In: layers DataFrame (cell_id, lat, lon, thermal_inertia, ice_frac, unit, slope_deg,
roughness_m, water_vapour_pr_um) + unit_lookup.csv. Out: long-format rows (common/schema.py).
Step 5; plan s6. Thresholds come from config `processing.ground_rules`.
"""
from pathlib import Path

import pandas as pd

from ..common.config import get_processing
from ..common.ground import hardness
from ..common.io import feature_row
from ..common.schema import BANDS
from ..physics.heat_model import band_top_m, mars_band_temps

UNIT_LOOKUP = Path(__file__).with_name("unit_lookup.csv")


def unit_to_rock(unit, lookup=None):
    lookup = lookup if lookup is not None else dict(pd.read_csv(UNIT_LOOKUP).values)
    return lookup.get(unit, "impact_mixed")


def ice_table_depth_m(lat, params=None):
    r = (params or get_processing())["ground_rules"]
    return max(0.0, (r["ice_table_lat"] - abs(lat)) * r["ice_table_m_per_deg"])


def ground_for_band(ti, ice_frac, lat, band, params=None):
    p = params or get_processing()
    r = p["ground_rules"]
    ice_here = ice_frac >= r["ice_frac"] and band_top_m(band, p) >= ice_table_depth_m(lat, p) - 1e-9
    if ice_here:
        return "massive_ice" if ice_frac >= r["massive_ice_frac"] else "ice_cemented", True
    return ("bedrock" if ti >= r["bedrock_ti"] else "dry_regolith"), False


def extract_cell(rec, lookup=None, params=None):
    p = params or get_processing()
    rock = unit_to_rock(rec["unit"], lookup)
    rows = []
    for band in BANDS:
        mean, swing = mars_band_temps(rec["lat"], rec["thermal_inertia"], band, p)
        ground, ice = ground_for_band(rec["thermal_inertia"], rec["ice_frac"], rec["lat"], band, p)
        # SWIM/neutron ice is measured only for the shallow bands; deeper is extrapolated
        q_ice = "measured" if band in ("skin", "upper_metre") else "modelled"
        vals = [("temp_mean", mean, "modelled"), ("temp_swing", swing, "modelled"),
                ("ice", int(ice), q_ice), ("ground_class", ground, "modelled"),
                ("hardness", hardness(ground, rock, p), "modelled"), ("rock", rock, "measured"),
                ("slope_deg", rec["slope_deg"], "measured"),
                ("roughness_m", rec["roughness_m"], "measured"),
                ("dryness", rec["water_vapour_pr_um"], "modelled")]
        rows += [feature_row("mars", rec["cell_id"], rec["lat"], rec["lon"], band, f, v, q)
                 for f, v, q in vals]
    return [r for r in rows if r]


def extract_all(layers, params=None):
    p = params or get_processing()
    lookup = dict(pd.read_csv(UNIT_LOOKUP).values)
    rows = []
    for rec in layers.to_dict("records"):
        rows += extract_cell(rec, lookup, p)
    return rows
