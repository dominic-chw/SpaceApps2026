"""Build the Mars 2 deg grid (+-70 deg) feature table -> data/features/mars_cells.parquet.
Uses real data/mars/layers.parquet if present, else synthetic layers (printed warning).
Step 6; plan s6. Run: python -m pipeline.mars_features.grid
"""
from ..common.config import get_processing
from ..common.io import read_layers, write_features
from ..common.synthetic import mars_layers
from .extract import extract_all


def build_mars(params=None, out_dir=None):
    p = params or get_processing()
    layers = read_layers("mars")
    if layers is None:
        print("WARNING: data/mars/layers.parquet missing; using synthetic Mars layers")
        layers = mars_layers(p)
    return write_features(extract_all(layers, p), "mars", out_dir)


if __name__ == "__main__":
    print(build_mars())
