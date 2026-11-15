"""Earth source layers: regions + sample points with climate, terrain, geology, labels.
Layers come from the configured sources (common/sources.py, config processing.sources);
otherwise synthetic layers are used (printed warning). GLiM class -> rock via glim_lookup.csv.
Step 7; plan s5-s6.
"""
import json
from pathlib import Path

import pandas as pd

from ..common.config import get_processing
from ..common.io import read_layers
from ..common.synthetic import earth_layers

HERE = Path(__file__).parent
REGIONS = HERE / "regions.geojson"
GLIM_LOOKUP = HERE / "glim_lookup.csv"


def load_regions(path=REGIONS):
    feats = json.loads(Path(path).read_text())["features"]
    return pd.DataFrame([{"region_id": f["properties"]["region_id"], "name": f["properties"]["name"],
                          "lon": f["geometry"]["coordinates"][0],
                          "lat": f["geometry"]["coordinates"][1]} for f in feats])


def glim_to_rock(glim_class, lookup=None):
    lookup = lookup if lookup is not None else dict(pd.read_csv(GLIM_LOOKUP).values)
    rock = lookup.get(glim_class)      # wb (water), nd (no data) or unknown -> None (missing)
    return rock if isinstance(rock, str) and rock else None


def load_layers(params=None):
    p = params or get_processing()
    layers = read_layers("earth", p)
    if layers is None:
        print("WARNING: all Earth sources are synthetic (config processing.sources); using synthetic Earth layers")
        layers = earth_layers(load_regions(), params=p)
    return layers
