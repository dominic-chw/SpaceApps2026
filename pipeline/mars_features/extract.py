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
from ..physics.heat_model import band_top_m, mars_band_temps, mars_frozen_fraction

UNIT_LOOKUP = Path(__file__).with_name("unit_lookup.csv")


def load_unit_lookup():
    """{geologic unit: rock word} from unit_lookup.csv (columns unit, rock, notes)."""
    t = pd.read_csv(UNIT_LOOKUP)
    return dict(zip(t["unit"], t["rock"]))


def unit_to_rock(unit, lookup=None):
    """Rock word for a unit; unknown / missing unit -> None (left out, lowers confidence)."""
    lookup = lookup if lookup is not None else load_unit_lookup()
    return lookup.get(unit)


def ice_table_depth_m(lat, params=None):
    """Depth (m) to the top of ground ice by latitude (where ice exists, see build_layers.ice_by_latitude).

    Poleward of 65 deg: ~5 cm (Phoenix, 68 N); 55-65 deg: deepening to ~0.5 m;
    35-55 deg (buried mid-latitude excess ice): ~1 m under a dry lag.
    """
    a = abs(lat)
    if a >= 65:
        return 0.05
    if a >= 55:
        return 0.05 + (65 - a) / 10 * 0.45
    return 1.0


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
        q_ice = rec.get("ice_quality") or ("measured" if band in ("skin", "upper_metre") else "modelled")
        frozen = mars_frozen_fraction(rec["lat"], rec["thermal_inertia"], band, p)
        vals = [("temp_mean", mean, "modelled"), ("frozen_fraction", frozen, "modelled"),
                ("temp_swing", swing, "modelled"),
                ("ice", int(ice), q_ice), ("ground_class", ground, "modelled"),
                ("hardness", hardness(ground, rock, p), "modelled"), ("rock", rock, "measured"),
                ("slope_deg", rec["slope_deg"], "measured"),
                ("roughness_m", rec["roughness_m"], "measured"),
                ("dryness", rec["water_vapour_pr_um"], "modelled")]
        rows += [feature_row("mars", rec["cell_id"], rec["lat"], rec["lon"], band, f, v, q)
                 for f, v, q in vals]
    # leave out unknown values (None / NaN), e.g. no TES data or no geologic unit
    def known(r):
        if r["value_cat"] is not None:
            return r["value_cat"] not in ("None", "nan")
        return r["value_num"] is not None and r["value_num"] == r["value_num"]
    return [r for r in rows if r and known(r)]


def extract_all(layers, params=None):
    p = params or get_processing()
    lookup = load_unit_lookup()
    rows = []
    for rec in layers.to_dict("records"):
        rows += extract_cell(rec, lookup, p)
    return rows
