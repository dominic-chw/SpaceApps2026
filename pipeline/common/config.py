"""Load config.yaml with defaults. Data-processing params live under `processing:`.

Usage: p = get_processing(); p["grid_deg"]. Override per call: get_processing({"grid_deg": 5}).
"""
from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"

DEFAULTS = {
    "grid_deg": 2,
    "lat_limit": 70,
    "band_depth_m": {
        "skin": [0, 0.10],
        "upper_metre": [0.10, 1.0],
        "drill_zone": [1.0, 3.0],
        "deep": [3.0, None],
    },
    "heat_model": {
        "mars_year_days": 687,
        "mars_sol_hours": 24.66,
        "default_thermal_inertia": 250,
        "volumetric_heat_capacity": 1.0e6,
        "surface": {"mean_eq_k": 220, "mean_pole_drop_k": 70, "diurnal_amp_ref_k": 45,
                    "ti_ref": 250, "annual_amp_k": 35},
    },
    "earth_heat_model": {"year_days": 365.25, "day_hours": 24,
                         "default_thermal_inertia": 1500, "volumetric_heat_capacity": 2.0e6},
    "ground_rules": {
        "massive_ice_frac": 0.7, "ice_frac": 0.25, "bedrock_ti": 700,
        "ice_table_lat": 75, "ice_table_m_per_deg": 0.02, "earth_active_layer_m": 0.5,
        "hardness": {"dry_regolith": 0, "ice_cemented": 2, "massive_ice": 2, "bedrock": 3},
        "bedrock_hardness": {"volcanic": 3, "impact_mixed": 3, "sedimentary": 2,
                             "clay_sulfate": 2, "icy": 2},
    },
    "synthetic": {"seed": 0, "earth_sample_points": 800},
    "terrain_resolution_m": 500,
    "bare_ground_min_fraction": 0.8,
    "gap_model": {"n_estimators": 300, "max_depth": 6, "learning_rate": 0.05, "cv_block_deg": 10},
}


def _merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def load_config(path=CONFIG_PATH):
    p = Path(path)
    return yaml.safe_load(p.read_text()) or {} if p.exists() else {}


def get_processing(overrides=None, path=CONFIG_PATH):
    """Defaults < config.yaml `processing:` < overrides."""
    cfg = load_config(path).get("processing", {})
    return _merge(_merge(DEFAULTS, cfg), overrides)
