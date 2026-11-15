"""Synthetic source layers so the pipeline runs end to end before real rasters exist.

Same columns the real loaders must provide (data/mars/layers.parquet,
data/earth/layers.parquet). Deterministic: seed from config `processing.synthetic`.
Values are plausible, not real; outputs built from them are flagged by the caller.
"""
import numpy as np
import pandas as pd

from .config import get_processing
from .coords import cell_id, grid_cells

MARS_UNITS = ["Noachian_highland", "Hesperian_volcanic", "Amazonian_volcanic", "Noachian_clay",
              "Hesperian_sulfate", "Sedimentary_plains", "Polar_layered", "Impact_ejecta"]
GLIM = ["vb", "va", "ss", "su", "sc", "sm", "ev", "mt", "pi", "nd"]


def mars_layers(params=None):
    p = params or get_processing()
    rng = np.random.default_rng(p["synthetic"]["seed"])
    cells = grid_cells(p)
    lat = np.array([c[0] for c in cells])
    lon = np.array([c[1] for c in cells])
    n = len(cells)
    polar = 1 / (1 + np.exp(-(np.abs(lat) - 55) / 5))
    return pd.DataFrame({
        "cell_id": [cell_id(a, b, p) for a, b in cells], "lat": lat, "lon": lon,
        "thermal_inertia": np.clip(rng.normal(250, 120, n), 40, 1200),
        "ice_frac": np.clip(polar * rng.uniform(0.3, 1.0, n) + rng.normal(0, 0.03, n), 0, 1),
        "unit": np.where(polar > 0.8, "Polar_layered", rng.choice(MARS_UNITS[:-2], n)),
        "slope_deg": np.abs(rng.gamma(1.5, 1.5, n)),
        "roughness_m": np.abs(rng.gamma(2.0, 1.5, n)),
        "water_vapour_pr_um": np.clip(rng.normal(12, 6, n) + 0.2 * np.abs(lat), 0, 80),
    })


def _truth_ice(temp_k, aridity, perm_prob):
    return ((perm_prob > 0.5) | ((temp_k < 268) & (aridity < 0.5))).astype(int)


def earth_layers(sites, n_samples=None, params=None):
    """Layers for named `sites` (DataFrame region_id,name,lat,lon) plus random labelled samples.

    Label columns (ice_label, ground_label) are NaN for the sites partly, mimicking gaps
    in CCI / ground-ice map / boreholes that the gap model must fill.
    """
    p = params or get_processing()
    rng = np.random.default_rng(p["synthetic"]["seed"] + 1)
    n_samples = p["synthetic"]["earth_sample_points"] if n_samples is None else n_samples
    ids = list(sites["region_id"]) + [f"sample_{i:04d}" for i in range(n_samples)]
    names = list(sites["name"]) + [""] * n_samples
    lat = np.concatenate([sites["lat"].to_numpy(float), rng.uniform(-70, 80, n_samples)])
    lon = np.concatenate([sites["lon"].to_numpy(float), rng.uniform(-180, 180, n_samples)])
    n = len(ids)
    elev = np.clip(rng.gamma(2, 600, n), 0, 5500)
    mean_t = 300 - 45 * np.sin(np.radians(lat)) ** 2 - 0.0065 * elev + rng.normal(0, 2, n)
    aridity = np.clip(rng.beta(1.2, 2.0, n) * 1.5 * (1 - np.abs(np.sin(np.radians(lat))) * 0.3), 0, 2)
    perm_prob = np.clip((270 - mean_t) / 15 + rng.normal(0, 0.15, n), 0, 1)
    ice = _truth_ice(mean_t, aridity, perm_prob)
    bedrock = np.clip(rng.exponential(3, n), 0, 30)
    ground = np.where(bedrock < 0.5, "bedrock",
             np.where(ice & (perm_prob > 0.85), "massive_ice",
             np.where(ice == 1, "ice_cemented", "dry_regolith")))
    known = np.arange(n) >= len(sites)           # samples are labelled; sites partly
    known |= rng.random(n) < 0.3
    return pd.DataFrame({
        "cell_id": ids, "name": names, "lat": lat, "lon": lon, "elevation_m": elev,
        # named sites are chosen as bare ground; random samples may be filtered out
        "bare_fraction": np.where(np.arange(n) < len(sites), 0.95, np.clip(rng.beta(4, 1.2, n), 0, 1)),
        "mean_temp_k": mean_t, "annual_range_k": np.clip(10 + 0.35 * np.abs(lat) + rng.normal(0, 3, n), 4, 60),
        "diurnal_amp_k": np.clip(rng.normal(8, 3, n) + 6 * (1 - aridity / 2), 1, 25),
        "thermal_inertia": np.clip(rng.normal(1500, 400, n), 300, 2500),
        "aridity": aridity, "perm_prob": np.where(rng.random(n) < 0.15, np.nan, perm_prob),
        "glim_class": rng.choice(GLIM, n), "slope_deg": np.abs(rng.gamma(1.5, 2.0, n)),
        "roughness_m": np.abs(rng.gamma(2.0, 1.5, n)),
        "bedrock_depth_m": np.where(rng.random(n) < 0.3, np.nan, bedrock),
        "ice_label": np.where(known, ice, np.nan),
        "ground_label": np.where(known, ground, None),
    })
