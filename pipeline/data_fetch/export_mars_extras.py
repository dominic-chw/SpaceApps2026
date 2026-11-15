"""Export the two Mars layers Jad's builder (pipeline/mars_features/build_layers.py) lacks, for him to join.

Out: data/mars/mars_extras.csv, one row per 2 deg cell (common/coords grid, same cell_id):
  cell_id             join key
  hydrogen            Odyssey neutron suppression, nearest 5 deg bin (0.15 ~ dry background,
                      0.65 ~ polar ice-rich ground; ice_frac = clip((h - 0.15) / 0.5, 0, 1))
  water_vapour_pr_um  MCD v6.2 water vapour column, mean of Ls 0/90/180/270 at 12:00, precipitable
                      microns (low = dry). Feeds `dryness`.
Run: python -m pipeline.data_fetch.export_mars_extras
"""
import pandas as pd

from ..common.config import get_processing
from ..common.coords import cell_id, grid_cells
from .build_mars_layers import MARS, hydrogen, water_vapour


def export(params=None):
    p = params or get_processing()
    cells = grid_cells(p)
    df = pd.DataFrame({
        "cell_id": [cell_id(a, b, p) for a, b in cells],
        "hydrogen": hydrogen(cells).round(4),
        "water_vapour_pr_um": water_vapour(cells).round(3),
    })
    out = MARS / "mars_extras.csv"
    df.to_csv(out, index=False)
    print(df.describe().T[["count", "mean", "min", "max"]].to_string())
    print("wrote", out, f"({len(df)} cells)")
    return df


if __name__ == "__main__":
    export()
