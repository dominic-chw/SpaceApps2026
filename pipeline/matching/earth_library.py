"""Build output/earth_library.json: what the web app needs to re-score with the sliders.

The web app's advanced panel lets users move weight sliders and re-rank Earth sites
in the browser. To do that with EXACTLY the same maths as match.py it needs:
- every Earth place's values, already normalized (so it only has to normalize ONE Mars cell),
- the settings to normalize a Mars value (Mars quantiles, Earth log averages, word codes),
- the default weights, the match % curve steepness, the level cut-offs.
The step-by-step browser formula is in docs/plan.md section 15a.

In:  the `info` returned by match.match_band() for each band.
Out: output/earth_library.json
"""

import json
import math
from pathlib import Path

OUTPUT_PATH = Path(__file__).resolve().parents[2] / "output" / "earth_library.json"


def rounded(value, digits=4):
    """Round a number for a smaller file; NaN becomes None (JSON null)."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return round(float(value), digits)


def feature_entries(prepared):
    """One entry per feature: name, label, unit, compare + only the settings that method needs."""
    entries = []
    for name, feature in prepared["features"].items():
        settings = prepared["settings"][name]
        compare = feature["compare"]
        entry = {"name": name, "label": feature.get("label", name.replace("_", " ")),
                 "unit": feature.get("unit", ""), "compare": compare}
        if compare == "percentile":
            entry["mars_quantiles"] = settings["mars_quantiles"]
        elif compare == "absolute":
            entry["scale"] = feature["scale"]
        elif compare == "log_z":
            entry["scale"] = feature["scale"]
            entry["earth_log_mean"] = rounded(settings["earth_log_mean"], 6)
            entry["earth_log_std"] = rounded(settings["earth_log_std"], 6)
        elif compare == "steps":
            entry["min"], entry["max"] = feature["min"], feature["max"]
        elif compare == "same":
            entry["codes"] = settings["codes"]
        entries.append(entry)
    return entries


def band_entry(info):
    """Everything the browser needs for one band, from match_band()'s `info`."""
    prepared = info["prepared"]
    places = []
    for row in range(len(prepared["earth"])):
        places.append({
            "id": str(prepared["site_ids"][row]),
            "name": str(prepared["site_names"][row]),
            "new_site": bool(prepared["new_site"][row]),
            "lat": rounded(prepared["earth_lats"][row], 3),
            "lon": rounded(prepared["earth_lons"][row], 3),
            "v": [rounded(v) for v in prepared["earth_grid"][row]],
        })
    return {
        "features": feature_entries(prepared),
        "weights": {name: float(w) for name, w in zip(prepared["names"], prepared["weights"])},
        "steepness": rounded(info["curve_steepness"], 6),
        "places": places,
    }


def build(info_by_band, levels, min_coverage, spacing_km, fake=False, hand_weights=None,
          learned_weights=None, weights_in_use=None):
    """Assemble the whole earth_library.json dictionary.

    Extra keys (additions only, plan s3): per band `hand_weights` and `learned_weights` (for the
    app's Hand-tuned / Learned toggle) and top-level `weights_in_use` (file the pipeline matched with).
    """
    bands = {band: band_entry(info) for band, info in info_by_band.items()}
    for band, entry in bands.items():
        names = [f["name"] for f in entry["features"]]
        if hand_weights and band in hand_weights:
            entry["hand_weights"] = {n: float(hand_weights[band].get(n, 1.0)) for n in names}
        if learned_weights and band in learned_weights:
            entry["learned_weights"] = {n: float(learned_weights[band].get(n, 1.0)) for n in names}
    out = {
        "fake": fake,
        "levels": levels,
        "min_coverage": min_coverage,
        "spacing_km": spacing_km,
        "bands": bands,
    }
    if weights_in_use:
        out["weights_in_use"] = weights_in_use
    return out


def normalize_like(earth, prepared):
    """Normalize a raw Earth wide table (load_tables.to_wide) EXACTLY like the library places of
    `prepared` (match.prepare_band), so its rows can be scored in the browser the same way.
    percentile -> position among the library's Earth values; log_z -> the library's Earth log mean/std;
    same -> the library's word codes (unknown word = NaN); absolute / steps -> raw value."""
    import numpy as np
    import pandas as pd
    from . import normalize
    cols = []
    for name, feature in prepared["features"].items():
        column, compare = feature["column"], feature["compare"]
        settings = prepared["settings"][name]
        raw = earth[column] if column in earth else pd.Series(np.nan, index=earth.index)
        if compare == "same":
            codes = settings["codes"]
            vals = raw.map(normalize.clean_word).map(lambda w: codes.get(w, np.nan) if w is not None else np.nan)
        else:
            x = pd.to_numeric(raw, errors="coerce").to_numpy(float)
            if compare == "percentile":
                ref = np.sort(pd.to_numeric(prepared["earth"][column], errors="coerce").dropna().to_numpy(float))
                vals = np.full(len(x), np.nan)
                if len(ref) == 1:
                    vals[np.isfinite(x)] = 0.5
                elif len(ref) > 1:
                    ok = np.isfinite(x)
                    vals[ok] = np.interp(x[ok], ref, np.linspace(0, 1, len(ref)))
            elif compare == "log_z":
                vals = (np.log1p(np.clip(x, 0, None)) - settings["earth_log_mean"]) / settings["earth_log_std"]
            else:
                vals = x
        cols.append(np.asarray(vals, dtype=float))
    return np.column_stack(cols) if cols else np.empty((len(earth), 0))


def build_lookup(info_by_band, all_long, fake=False):
    """output/earth_lookup.json: every land cell that is not a library place, normalized like the library,
    for the reverse search only (these cells are never offered as Mars twins).
    {fake, ids, lat, lon, bands: {band: {features: [names], v: [[...] per cell]}}}"""
    from . import load_tables
    wide = {band: load_tables.to_wide(all_long, band) for band in info_by_band}
    ids = sorted(set.intersection(*(set(w["cell_id"]) for w in wide.values())))
    if not ids:
        return None
    first = wide[next(iter(wide))].set_index("cell_id").loc[ids]
    out = {"fake": fake, "ids": ids, "lat": [rounded(v, 3) for v in first["lat"]],
           "lon": [rounded(v, 3) for v in first["lon"]], "bands": {}}
    for band, info in info_by_band.items():
        table = wide[band].set_index("cell_id").loc[ids].reset_index()
        grid = normalize_like(table, info["prepared"])
        out["bands"][band] = {"features": list(info["prepared"]["names"]),
                              "v": [[rounded(v, 3) for v in row] for row in grid]}
    return out


def write(library, path=OUTPUT_PATH):
    """Save as compact JSON (no spaces) to keep the download small."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(library, f, separators=(",", ":"), ensure_ascii=False)
    return path
