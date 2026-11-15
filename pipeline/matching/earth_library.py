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


def build(info_by_band, levels, min_coverage, spacing_km, fake=False):
    """Assemble the whole earth_library.json dictionary."""
    return {
        "fake": fake,
        "levels": levels,
        "min_coverage": min_coverage,
        "spacing_km": spacing_km,
        "bands": {band: band_entry(info) for band, info in info_by_band.items()},
    }


def write(library, path=OUTPUT_PATH):
    """Save as compact JSON (no spaces) to keep the download small."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(library, f, separators=(",", ":"), ensure_ascii=False)
    return path
