"""Tests for pipeline/matching/earth_library.py (scoring side).

The key test re-does the BROWSER re-scoring exactly as written in docs/plan.md s15a,
using only earth_library.json + the raw Mars values from the grid file, and checks it
gives the same twins as the Python pipeline. If this passes, the web team's formula is right.

Run from the repo folder:  python -m pytest tests/
"""

import json
import math

import numpy as np

from pipeline.matching import earth_library, match
from test_matching import fake_earth, fake_mars   # same made-up data as the scoring tests

LEVELS = match.DEFAULT_LEVELS


def build_fake_library(band="upper_metre"):
    cells, info = match.match_band(fake_mars(), fake_earth(), band)
    library = earth_library.build({band: info}, LEVELS, 0.5, 200)
    # Round-trip through JSON text, exactly like the browser receives it.
    return cells, json.loads(json.dumps(library))


# --- The browser algorithm, step by step from plan s15a (deliberately plain Python) ---

def browser_normalize(x, feature):
    if x is None:
        return None
    compare = feature["compare"]
    if compare == "percentile":
        q = feature["mars_quantiles"]
        return float(np.interp(x, q, np.linspace(0, 1, len(q))))
    if compare in ("absolute", "steps"):
        return float(x)
    if compare == "log_z":
        return (math.log(1 + max(x, 0)) - feature["earth_log_mean"]) / feature["earth_log_std"]
    if compare == "same":
        word = str(x).strip().lower()
        return feature["codes"].get(word)
    raise ValueError(compare)


def browser_rescore(band_data, mars_raw, levels, min_coverage, spacing_km):
    features = band_data["features"]
    weights = [band_data["weights"][f["name"]] for f in features]
    m = [browser_normalize(mars_raw.get(column_of(f)), f) for f in features]
    scored = []
    for place in band_data["places"]:
        total, used, weight_all = 0.0, 0.0, sum(weights)
        diffs = []
        for f, w, mv, ev in zip(features, weights, m, place["v"]):
            if mv is None or ev is None:
                diffs.append(None)
                continue
            if f["compare"] == "same":
                d = 0.0 if mv == ev else 1.0
            else:
                s = {"percentile": 1, "steps": f.get("max", 1) - f.get("min", 0)}.get(
                    f["compare"], f.get("scale", 1))
                d = min(abs(ev - mv) / s, 1.0)
            diffs.append(d)
            total += w * d
            used += w
        if used == 0:
            continue
        scored.append((total / used, used / weight_all, place, diffs))
    scored.sort(key=lambda item: item[0])

    twins = []
    for distance, coverage, place, diffs in scored:
        if any(match.distance_km(place["lat"], place["lon"], np.array([t["lat"]]),
                                 np.array([t["lon"]]))[0] < spacing_km for t in twins):
            continue
        pct = 100 * math.exp(-distance / band_data["steepness"])
        level = match.level_for(round(pct, 1), levels)
        if coverage < min_coverage:
            level = "insufficient_data"
        twins.append({"id": place["id"], "lat": place["lat"], "lon": place["lon"],
                      "match_pct": pct, "level": level})
        if len(twins) == 3:
            break
    return twins


def column_of(feature):
    """Browser side: the raw Mars value's key in the grid file. For our features the
    grid's `mars` keys are the table column names (temp_mean_rank reads temp_mean)."""
    from pipeline.matching import normalize
    return normalize.load_feature_file()["features"][feature["name"]]["column"]


# --- Tests ---

def test_library_shape():
    _, library = build_fake_library()
    band = library["bands"]["upper_metre"]
    assert {"features", "weights", "steepness", "places"} <= set(band)
    for f in band["features"]:
        assert f["label"] and f["compare"]
    place = band["places"][0]
    assert set(place) == {"id", "name", "new_site", "lat", "lon", "v"}
    assert len(place["v"]) == len(band["features"])


def test_browser_formula_matches_python_pipeline():
    cells, library = build_fake_library()
    band = library["bands"]["upper_metre"]
    for cell in cells:
        browser = browser_rescore(band, cell["mars"], LEVELS, library["min_coverage"],
                                  library["spacing_km"])
        python = cell["twins"]
        assert [t["id"] for t in browser] == [t["region_id"] for t in python]
        for b, p in zip(browser, python):
            assert abs(b["match_pct"] - p["match_pct"]) < 0.5
            assert b["level"] == p["level"]


def test_write_is_compact_json(tmp_path):
    _, library = build_fake_library()
    path = earth_library.write(library, tmp_path / "earth_library.json")
    text = path.read_text(encoding="utf-8")
    assert ": " not in text and json.loads(text)["spacing_km"] == 200


def test_lookup_cells_normalized_like_library():
    """earth_lookup.json rows use the library's settings: re-normalizing the library's own Earth
    places must give back its values (percentile within 1 rank step, the rest exactly)."""
    prepared = match.prepare_band(fake_mars(), fake_earth(), "skin")
    again = earth_library.normalize_like(prepared["earth"], prepared)
    lib = prepared["earth_grid"]
    for j, feature in enumerate(prepared["features"].values()):
        a, b = again[:, j], lib[:, j]
        both = ~np.isnan(a) & ~np.isnan(b)
        assert np.array_equal(np.isnan(a), np.isnan(b))
        tol = 1.0 / max(1, len(a) - 1) + 1e-9 if feature["compare"] == "percentile" else 1e-9
        assert np.all(np.abs(a[both] - b[both]) <= tol), feature["compare"]
