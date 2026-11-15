"""Earth source layers: regions + sample points with climate, terrain, geology, labels.
Real layers come from data/earth/layers.parquet (same columns as common/synthetic.earth_layers);
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
    return lookup.get(glim_class, "sedimentary")


def load_layers(params=None):
    p = params or get_processing()
    layers = read_layers("earth")
    if layers is None:
        print("WARNING: data/earth/layers.parquet missing; using synthetic Earth layers")
        layers = earth_layers(load_regions(), params=p)
    return layers
