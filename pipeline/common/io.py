"""Read/write helpers for the feature tables and source layers. Step 1-2; plan s3."""
from pathlib import Path

import pandas as pd

from .schema import CATEGORICAL, COLUMNS, QUALITY, feature_in_band

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
FEATURES_DIR = DATA / "features"


def feature_row(planet, cell_id, lat, lon, band, feature, value, quality):
    """One long-format row; None if the feature doesn't exist in that band."""
    if not feature_in_band(feature, band):
        return None
    assert quality in QUALITY, quality
    cat = feature in CATEGORICAL
    return {"planet": planet, "cell_id": cell_id, "lat": lat, "lon": lon, "band": band,
            "feature": feature, "value_num": None if cat else float(value),
            "value_cat": str(value) if cat else None, "data_quality": quality}


def write_features(rows, planet, out_dir=None):
    df = pd.DataFrame([r for r in rows if r], columns=COLUMNS)
    path = Path(out_dir or FEATURES_DIR) / f"{planet}_cells.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return path


def read_features(planet, out_dir=None):
    return pd.read_parquet(Path(out_dir or FEATURES_DIR) / f"{planet}_cells.parquet")


def read_layers(planet, name="layers"):
    """Real source layers from data/<planet>/<name>.parquet if present, else None."""
    p = DATA / planet / f"{name}.parquet"
    return pd.read_parquet(p) if p.exists() else None
