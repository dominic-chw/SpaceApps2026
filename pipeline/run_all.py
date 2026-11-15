"""Entry point: run the pipeline end to end and write every output/*.json (plan s3, s15a).

Run from the repo folder:
    python -m pipeline.run_all            # score using the existing feature tables
    python -m pipeline.run_all --build    # first rebuild the feature tables (data side)

Steps:
1. (optional) Data side builds data/features/{mars,earth}_cells.parquet (pipeline.common.build_all).
2. For each depth band: reshape the tables, find the top Earth twins for every Mars cell
   -> output/grid/<band>.json
3. Showcase sites (sites/showcase.csv, or a default list) get top 5 twins per band
   -> output/showcase.json
4. Slider data for the web app -> output/earth_library.json
5. Validation against known analog pairs (if known_pairs.csv has any) -> output/validation.json

Matcher: config.yaml `matcher`. Only `knn` exists; `deep`/`hybrid` fall back to knn
with a warning (plan s10: deep is optional, never required).
Outputs are marked "fake": true while the data side is still using synthetic layers.
"""

import argparse
import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from pipeline.matching import earth_library, load_tables, match
from pipeline.validation import evaluate

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.yaml"
OUTPUT = ROOT / "output"
SHOWCASE_CSV = ROOT / "sites" / "showcase.csv"

# Used only until the team adds sites/showcase.csv (site_id,name,lat,lon,radius_km,reason).
DEFAULT_SHOWCASE = [
    ("jezero", "Jezero crater (Perseverance)", 18.4, 77.5),
    ("phoenix", "Phoenix landing site", 68.2, -125.7),
    ("gale", "Gale crater (Curiosity)", -5.4, 137.8),
    ("insight", "Elysium Planitia (InSight)", 4.5, 135.6),
    ("arcadia", "Arcadia Planitia (buried ice)", 46.0, -170.0),
]


def load_config(path=CONFIG_PATH):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def showcase_sites():
    """Showcase sites from sites/showcase.csv, or the default list if it doesn't exist yet."""
    if SHOWCASE_CSV.exists():
        table = pd.read_csv(SHOWCASE_CSV)
        return list(table[["site_id", "name", "lat", "lon"]].itertuples(index=False, name=None))
    warnings.warn("sites/showcase.csv not found; using the default showcase list")
    return DEFAULT_SHOWCASE


def using_synthetic_data():
    """True while any source variable (config processing.sources) is still synthetic -> outputs are placeholders."""
    from pipeline.common.sources import VARIABLES, source_specs
    return any(s.get("source", "synthetic") == "synthetic"
               for planet in VARIABLES for s in source_specs(planet).values())


def nearest_row(table, lat, lon):
    """Row position of the cell closest to (lat, lon); 'closest' is the same on any sphere."""
    gaps = match.distance_km(lat, lon, table["lat"].to_numpy(float), table["lon"].to_numpy(float))
    return int(np.argmin(gaps))


def write_json(data, path):
    """Compact JSON (no spaces) to keep the web downloads small."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"), ensure_ascii=False)
    print(f"  wrote {path} ({path.stat().st_size / 1e6:.1f} MB)")


def run(build=False):
    config = load_config()
    if config.get("matcher", "knn") != "knn":
        warnings.warn(f"matcher '{config['matcher']}' not available yet; using knn")
    matcher = "knn"

    if build:
        from pipeline.common.build_all import build_earth, build_mars
        build_mars()
        build_earth()

    fake = using_synthetic_data()
    levels = config["levels"]
    spacing = config["min_twin_spacing_km"]
    mars_long = load_tables.read_long("mars")
    earth_long = load_tables.read_long("earth")
    sites = showcase_sites()
    showcase = [{"site_id": s, "name": n, "lat": la, "lon": lo, "bands": {}} for s, n, la, lo in sites]

    info_by_band, mars_tables, earth_tables = {}, {}, {}
    for band in config["bands"]:
        print(f"Band {band}:")
        mars, earth = load_tables.load_band_tables(band, mars_long=mars_long, earth_long=earth_long)
        mars_tables[band], earth_tables[band] = mars, earth

        # 2. Every Mars cell -> top 3 twins.
        cells, info = match.match_band(mars, earth, band, how_many=config["top_twins_grid"],
                                       spacing_km=spacing, levels=levels)
        info_by_band[band] = info
        write_json({"band": band, "grid_deg": config["grid_deg"], "matcher": matcher,
                    "fake": fake, "cells": cells}, OUTPUT / "grid" / f"{band}.json")

        # 3. Showcase sites -> top 5 twins, same % curve as the grid so numbers agree.
        rows = [nearest_row(mars, site["lat"], site["lon"]) for site in showcase]
        site_cells, _ = match.match_band(mars, earth, band, how_many=config["top_twins_showcase"],
                                         spacing_km=spacing, levels=levels, rows=rows,
                                         steepness=info["curve_steepness"])
        for site, row, cell in zip(showcase, rows, site_cells):
            quality = {c[:-len("_quality")]: mars.iloc[row][c]
                       for c in mars.columns if c.endswith("_quality") and pd.notna(mars.iloc[row][c])}
            site["bands"][band] = {"mars": cell["mars"], "data_quality": quality,
                                   "twins": cell["twins"]}

    write_json({"fake": fake, "matcher": matcher, "sites": showcase}, OUTPUT / "showcase.json")

    # 4. Slider data for the web app.
    library = earth_library.build(info_by_band, levels, info_by_band[config["bands"][0]]["prepared"]
                                  ["confidence_settings"]["min_coverage"], spacing, fake=fake)
    write_json(library, OUTPUT / "earth_library.json")

    # 5. Validation (only once the team has filled in known_pairs.csv).
    pairs = evaluate.load_known_pairs()
    if len(pairs):
        result = evaluate.evaluate(mars_tables, earth_tables, pairs, matcher=matcher)
        result["fake"] = fake
        evaluate.write_validation(result, OUTPUT / "validation.json")
        print(f"  wrote output/validation.json ({len(pairs)} pairs)")
    else:
        warnings.warn("pipeline/validation/known_pairs.csv is empty; validation.json not updated")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Analog Finder pipeline.")
    parser.add_argument("--build", action="store_true", help="rebuild the feature tables first")
    run(build=parser.parse_args().build)
