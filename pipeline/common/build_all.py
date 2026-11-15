"""Build both feature tables for the matching coder: data/features/{mars,earth}_cells.parquet.
Run: python -m pipeline.common.build_all   (matching/ and run_all.py are not touched here)
"""
from ..earth_library.build_grid import build_earth
from ..mars_features.grid import build_mars

if __name__ == "__main__":
    print(build_mars())
    print(build_earth())
