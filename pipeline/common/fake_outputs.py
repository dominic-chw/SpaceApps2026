"""Writes FAKE placeholder output/*.json in the plan s3 shape so the web team can
build before real results exist. Every file carries "fake": true.

Run from the repo root:  python -m pipeline.common.fake_outputs
Numbers are random (seeded) and mean nothing. Only the shape is real.
"""
import json
import random
from pathlib import Path

from pipeline.common.schema import BANDS, GROUND, ROCK

LEVELS = [(85, "excellent"), (70, "good"), (50, "fair"), (30, "poor")]  # config.yaml levels
CONFIDENCE = ["high", "medium", "low"]
FEATURES = ["temp_mean", "frozen_fraction", "temp_swing", "ice", "ground_class",
            "hardness", "rock", "slope_deg", "roughness_m", "dryness"]
SHOWCASE = [("jezero", "Jezero crater", 18.4, 77.5), ("gale", "Gale crater", -5.4, 137.8),
            ("phoenix", "Phoenix landing site", 68.2, -125.7)]
FAKE_GRID_DEG = 10  # coarse on purpose: keeps the fake files small


def level_for(match_pct):
    """Turn a match % into a level word (cut-offs 85/70/50/30)."""
    for cutoff, name in LEVELS:
        if match_pct >= cutoff:
            return name
    return "no_match"


def fake_conditions(rng, lat):
    """Made-up feature values for one place."""
    return {"temp_mean": round(210 - abs(lat) * 0.5 + rng.uniform(-5, 5), 1),
            "frozen_fraction": 1.0, "temp_swing": round(rng.uniform(1, 40), 1),
            "ice": rng.choice(["yes", "no"]), "ground_class": rng.choice(GROUND),
            "hardness": rng.randint(0, 3), "rock": rng.choice(ROCK),
            "slope_deg": round(rng.uniform(0, 15), 1),
            "roughness_m": round(rng.uniform(0.1, 5), 2),
            "dryness": round(rng.uniform(0, 1), 2)}


def fake_twins(rng, regions, n):
    """n twins from the region list, best first, in the contract shape."""
    pcts = sorted((rng.uniform(25, 95) for _ in range(n)), reverse=True)
    twins = []
    for r, pct in zip(rng.sample(regions, n), pcts):
        feats = rng.sample(FEATURES, 6)
        twins.append({"region_id": r["region_id"], "name": r["name"],
                      "lat": r["lat"], "lon": r["lon"], "match_pct": round(pct, 1),
                      "level": level_for(pct), "best": feats[:3], "worst": feats[3:],
                      "confidence": rng.choice(CONFIDENCE)})
    return twins


def load_regions(path="pipeline/earth_library/regions.geojson"):
    feats = json.load(open(path))["features"]
    return [{"region_id": f["properties"]["region_id"], "name": f["properties"]["name"],
             "lon": f["geometry"]["coordinates"][0], "lat": f["geometry"]["coordinates"][1]}
            for f in feats]


def write_all(out="output", seed=0):
    rng = random.Random(seed)
    regions = load_regions()
    out = Path(out)
    (out / "grid").mkdir(parents=True, exist_ok=True)
    for band in BANDS:
        cells = []
        for lat in range(-70, 71, FAKE_GRID_DEG):
            for lon in range(-180, 180, FAKE_GRID_DEG):
                cells.append({"cell_id": f"{lat}_{lon}", "lat": lat, "lon": lon,
                              "mars": fake_conditions(rng, lat),
                              "twins": fake_twins(rng, regions, 3)})
        data = {"band": band, "grid_deg": FAKE_GRID_DEG, "matcher": "knn",
                "fake": True, "cells": cells}
        (out / "grid" / f"{band}.json").write_text(json.dumps(data, separators=(",", ":")))
    sites = []
    for sid, name, lat, lon in SHOWCASE:
        bands = {}
        for band in BANDS:
            quality = {f: rng.choice(["measured", "modelled", "predicted"]) for f in FEATURES}
            bands[band] = {"mars": fake_conditions(rng, lat), "data_quality": quality,
                           "twins": fake_twins(rng, regions, 5)}
        sites.append({"site_id": sid, "name": name, "lat": lat, "lon": lon, "bands": bands})
    (out / "showcase.json").write_text(json.dumps({"fake": True, "matcher": "knn", "sites": sites}, indent=1))
    val = {"fake": True, "matcher": "knn", "recall_at_10": 0.7, "recall_at_20": 0.85,
           "random_recall_at_20": 0.1, "per_band": {b: 0.7 for b in BANDS},
           "n_pairs": 7, "notes": "FAKE placeholder"}
    (out / "validation.json").write_text(json.dumps(val, indent=1))
    lib = {"fake": True, "normalization": {}, "regions": [dict(r, features=fake_conditions(rng, r["lat"])) for r in regions]}
    (out / "earth_library.json").write_text(json.dumps(lib, indent=1))


if __name__ == "__main__":
    write_all()
