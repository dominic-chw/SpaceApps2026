"""Step 2 of scoring: find the best Earth "twins" for every Mars place.

How it works (plain maths, no AI model):
1. For each feature, compare the Mars value with every Earth value and get a
   difference from 0 (identical) to 1 (completely different).
2. Combine them with the band's weights into ONE number per Earth place:
   the weighted Gower distance = sum(weight x difference) / sum(weight).
   A feature that is missing on either planet is simply skipped
   (and the match's confidence goes down instead).
3. Sort Earth places from closest to farthest and keep the top few, skipping any
   that are within 200 km of one already picked (so we don't list one area 3 times).
   This "keep the k closest" step is k-nearest-neighbours (kNN).
4. Turn distance into a match % with an exponential curve, scaled so that a
   typical best twin scores about 75%, then give it a level (excellent ... no_match).

In:  Mars and Earth feature tables for ONE band, features.yaml, weights/default.json.
Out: list of cells in the plan s3 grid format:
     {cell_id, lat, lon, mars:{features}, twins:[{region_id, name, lat, lon,
      match_pct, level, best[3], worst[3], confidence}]}
"""

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from pipeline.matching import explain, normalize

REPO_ROOT = Path(__file__).resolve().parents[2]
WEIGHTS_PATH = REPO_ROOT / "weights" / "default.json"
EARTH_RADIUS_KM = 6371.0

# Default level cut-offs (lowest match % for each level). config.yaml overrides these.
DEFAULT_LEVELS = {"excellent": 85, "good": 70, "fair": 50, "poor": 30}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def load_weights(path=WEIGHTS_PATH):
    """Read weights/default.json: {band: {feature: weight}}."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def weights_for_band(all_weights, band, features):
    """Weight per feature for this band, in feature order. Missing = 1 (plan s3)."""
    band_weights = all_weights.get(band, {})
    return np.array([float(band_weights.get(name, 1.0)) for name in features])


def feature_differences(mars_row, earth_grid, scales, is_category):
    """Differences (0-1) between ONE Mars place and EVERY Earth place.

    mars_row:   1-D array, one normalized value per feature.
    earth_grid: 2-D array, one row per Earth place.
    Returns a 2-D array the same shape as earth_grid. NaN where either value is missing.
    """
    raw = np.abs(earth_grid - mars_row)              # how far apart, in normalized units
    differences = raw / scales                       # scale so 1 = full mismatch
    # Categories: any difference at all means "different" -> 1.
    differences[:, is_category] = np.where(
        np.isnan(raw[:, is_category]), np.nan, (raw[:, is_category] > 0).astype(float)
    )
    return np.minimum(differences, 1.0)              # never more than a full mismatch


def gower_distance(differences, weights):
    """Weighted average of the differences, skipping missing values.

    Returns one distance (0-1) per Earth place; NaN if nothing could be compared.
    """
    present = ~np.isnan(differences)
    weighted = np.where(present, differences, 0.0) * weights
    weight_used = (present * weights).sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(weight_used > 0, weighted.sum(axis=1) / weight_used, np.nan)


def distance_km(lat, lon, lats, lons):
    """Great-circle distance on Earth from one point to many points (haversine formula)."""
    lat1, lon1 = np.radians(lat), np.radians(lon)
    lat2, lon2 = np.radians(lats), np.radians(lons)
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(a))


def pick_twins(distances, earth_lats, earth_lons, how_many, spacing_km):
    """Closest Earth places first, skipping any within spacing_km of one already picked.

    Returns a list of row positions in the Earth table.
    """
    picked = []
    for i in np.argsort(distances):                  # closest first
        if np.isnan(distances[i]):
            break                                    # NaNs sort last: nothing more to compare
        if picked:
            gaps = distance_km(earth_lats[i], earth_lons[i], earth_lats[picked], earth_lons[picked])
            if gaps.min() < spacing_km:
                continue                             # too close to a twin we already have
        picked.append(i)
        if len(picked) == how_many:
            break
    return picked


def curve_steepness(best_distances, typical_pct=75):
    """How steep the distance -> match % curve is.

    match % = 100 x exp(-distance / steepness). We choose steepness so the
    MEDIAN best-twin distance scores `typical_pct` (plan: typical good match ~ 75).
    So the % is relative: it says how good a match is compared to the others.
    """
    good = np.nanmedian(best_distances) if len(best_distances) else np.nan
    if np.isnan(good) or good <= 0:
        return 0.1  # fallback when there is nothing to calibrate on
    return good / math.log(100 / typical_pct)


def match_pct(distance, steepness):
    """Distance (0 = identical) -> match % (100 = identical)."""
    return round(100 * math.exp(-distance / steepness), 1)


def level_for(pct, levels=DEFAULT_LEVELS):
    """Match % -> level word, using the cut-offs (checked from the highest down)."""
    for name, lowest in sorted(levels.items(), key=lambda item: -item[1]):
        if pct >= lowest:
            return name
    return "no_match"


def plain_value(value):
    """Make a value safe for JSON (NaN -> None, numpy numbers -> normal numbers)."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, np.generic):
        return plain_value(value.item())
    return value


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def prepare_band(mars, earth, band, feature_file=None, all_weights=None):
    """Do the set-up shared by matching and validation for one band.

    Loads the feature list and weights, normalizes both tables and reads the
    quality tags. Returns a dictionary ("prepared") used by cell_distances()
    and match_band(). Validation passes its own weights here to test
    "what if this feature group were switched off".
    """
    feature_file = feature_file or normalize.load_feature_file()
    all_weights = all_weights or load_weights()
    features = normalize.features_for_band(feature_file, band)
    confidence_settings = feature_file["confidence"]
    mars = mars.reset_index(drop=True)
    earth = earth.reset_index(drop=True)
    mars_grid, earth_grid, settings = normalize.normalize_pair(mars, earth, features)
    scales, is_category = normalize.difference_scales(features)
    return {
        "band": band,
        "mars": mars,
        "earth": earth,
        "features": features,
        "names": list(features),
        "weights": weights_for_band(all_weights, band, features),
        "scales": scales,
        "is_category": is_category,
        "confidence_settings": confidence_settings,
        "mars_grid": mars_grid,
        "earth_grid": earth_grid,
        "settings": settings,
        "mars_quality": explain.quality_grid(mars, features, confidence_settings),
        "earth_quality": explain.quality_grid(earth, features, confidence_settings),
        "earth_lats": earth["lat"].to_numpy(dtype=float),
        "earth_lons": earth["lon"].to_numpy(dtype=float),
    }


def cell_distances(prepared, m):
    """Distance from Mars place number m to every Earth place.

    Returns (distances, differences): one distance per Earth place, and the
    per-feature differences behind them.
    """
    differences = feature_differences(prepared["mars_grid"][m], prepared["earth_grid"],
                                      prepared["scales"], prepared["is_category"])
    return gower_distance(differences, prepared["weights"]), differences


def match_band(mars, earth, band, how_many=3, spacing_km=200, levels=None,
               feature_file=None, all_weights=None):
    """Find the top Earth twins for every Mars place in one depth band.

    mars:  DataFrame with cell_id, lat, lon + feature columns (+ optional <column>_quality).
    earth: DataFrame with region_id, name, lat, lon + the same feature columns.
           (Only rows for this band should be passed in; a `band` column is ignored.)
    Returns (cells, info): cells in the plan s3 grid format, and info about the run
    (normalization settings and curve steepness, for earth_library.json / debugging).
    """
    levels = levels or DEFAULT_LEVELS
    prepared = prepare_band(mars, earth, band, feature_file, all_weights)

    # Short names for things used a lot below.
    mars, earth = prepared["mars"], prepared["earth"]
    features, names, weights = prepared["features"], prepared["names"], prepared["weights"]
    confidence_settings = prepared["confidence_settings"]
    mars_quality, earth_quality = prepared["mars_quality"], prepared["earth_quality"]
    settings = prepared["settings"]

    # Pass 1: find each Mars place's twins and remember their distances.
    found = []  # per Mars place: list of (earth row, distance, differences)
    for m in range(len(mars)):
        distances, differences = cell_distances(prepared, m)
        twins = pick_twins(distances, prepared["earth_lats"], prepared["earth_lons"],
                           how_many, spacing_km)
        found.append([(e, distances[e], differences[e]) for e in twins])

    # Pass 2: now that we know what a typical best distance is, turn distances into %.
    best_distances = np.array([twins[0][1] for twins in found if twins])
    steepness = curve_steepness(best_distances)

    # The raw Mars values to show in the app (each column once, in feature order).
    shown_columns = list(dict.fromkeys(f["column"] for f in features.values()))

    cells = []
    for m, twins in enumerate(found):
        row = mars.iloc[m]
        twin_list = []
        for e, distance, differences in twins:
            pct = match_pct(distance, steepness)
            level = level_for(pct, levels)
            # Too little data compared? Don't let a near-empty match look great.
            if explain.coverage(differences, weights) < confidence_settings["min_coverage"]:
                level = "insufficient_data"
            best, worst = explain.best_and_worst(differences, weights, names)
            label, _score = explain.confidence(
                differences, weights, mars_quality[m], earth_quality[e], band, confidence_settings
            )
            earth_row = earth.iloc[e]
            twin_list.append({
                "region_id": plain_value(earth_row["region_id"]),
                "name": plain_value(earth_row["name"]),
                "lat": plain_value(earth_row["lat"]),
                "lon": plain_value(earth_row["lon"]),
                "match_pct": pct,
                "level": level,
                "best": best,
                "worst": worst,
                "confidence": label,
            })
        cells.append({
            "cell_id": plain_value(row["cell_id"]),
            "lat": plain_value(row["lat"]),
            "lon": plain_value(row["lon"]),
            "mars": {c: plain_value(row[c]) if c in row else None for c in shown_columns},
            "twins": twin_list,
        })

    info = {"normalization": settings, "curve_steepness": steepness, "features": names,
            "weights": dict(zip(names, weights.tolist()))}
    return cells, info
