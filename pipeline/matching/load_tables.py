"""Read Dominic's feature tables and reshape them for scoring.

Dominic's data side writes ONE "long" table per planet:
    data/features/{mars,earth}_cells.parquet
    columns: planet, cell_id, lat, lon, band, feature, value_num, value_cat, data_quality
    (one row per place x band x feature)

Scoring wants ONE "wide" table per band: one row per place, one column per feature,
plus a <feature>_quality column per feature. This file does that reshaping, so the
data side and the scoring side can each keep the layout that suits them.

Earth places: if a cell_id is one of the regions in regions.geojson (e.g. "dry_valleys"),
it gets that region_id + name (a KNOWN analog). Otherwise region_id/name stay empty and
scoring treats it as a NEW site. If the table already has region_id/name columns, those win.
"""

import json
from pathlib import Path

import pandas as pd

from pipeline.matching import normalize

ROOT = Path(__file__).resolve().parents[2]
FEATURES_DIR = ROOT / "data" / "features"
REGIONS_PATH = ROOT / "pipeline" / "earth_library" / "regions.geojson"


def known_regions(path=REGIONS_PATH):
    """{region_id: name} from regions.geojson (empty if the file is missing)."""
    if not Path(path).exists():
        return {}
    with open(path, encoding="utf-8") as f:
        geo = json.load(f)
    return {feat["properties"]["region_id"]: feat["properties"].get("name", feat["properties"]["region_id"])
            for feat in geo.get("features", [])}


def read_long(planet, folder=FEATURES_DIR):
    """Read a planet's long table (parquet, or CSV with the same columns)."""
    folder = Path(folder)
    parquet = folder / f"{planet}_cells.parquet"
    if parquet.exists():
        return pd.read_parquet(parquet)
    return pd.read_csv(folder / f"{planet}_cells.csv")


def to_wide(long_table, band):
    """Long table -> one row per place for this band.

    The value is value_cat when present (words like "volcanic"), else value_num.
    data_quality becomes a "<feature>_quality" column.
    """
    rows = long_table[long_table["band"] == band].copy()
    rows["value"] = rows["value_cat"].where(rows["value_cat"].notna(), rows["value_num"])
    keys = ["cell_id", "lat", "lon"]

    values = rows.pivot_table(index=keys, columns="feature", values="value", aggfunc="first")
    quality = rows.pivot_table(index=keys, columns="feature", values="data_quality", aggfunc="first")
    quality.columns = [f"{c}_quality" for c in quality.columns]

    wide = values.join(quality).reset_index()
    wide.columns.name = None

    # Word-type features (features.yaml type: category) are shown as words:
    # e.g. ice stored as 0/1 -> "no"/"yes", as plan s3 says.
    for feature in normalize.load_feature_file()["features"].values():
        if feature["type"] == "category" and feature["column"] in wide:
            wide[feature["column"]] = wide[feature["column"]].map(normalize.clean_word)
    return wide


def tag_regions(earth_wide, regions):
    """Add region_id + name for Earth cells that are known analog regions."""
    if "region_id" not in earth_wide:
        earth_wide["region_id"] = earth_wide["cell_id"].where(earth_wide["cell_id"].isin(regions))
    if "name" not in earth_wide:
        earth_wide["name"] = earth_wide["region_id"].map(regions)
    return earth_wide


def load_band_tables(band, folder=FEATURES_DIR, mars_long=None, earth_long=None):
    """(mars, earth) wide tables for one band, ready for match.match_band()."""
    mars_long = read_long("mars", folder) if mars_long is None else mars_long
    earth_long = read_long("earth", folder) if earth_long is None else earth_long
    mars = to_wide(mars_long, band)
    earth = tag_regions(to_wide(earth_long, band), known_regions())
    return mars, earth
