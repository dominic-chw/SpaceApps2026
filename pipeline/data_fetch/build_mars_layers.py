"""Build data/mars/layers.parquet from the raw Mars files (columns of common/synthetic.mars_layers).
Input for pipeline/mars_features/ (coworker); this script only writes the parquet.

Per 2 deg cell (common/coords grid, lon -180..180 east):
  thermal_inertia     TES day 2007, median of valid pixels (0 = no data)            J m-2 K-1 s-1/2
  slope_deg           MOLA 463 m DEM, median slope                                 deg
  roughness_m         MOLA 463 m DEM, median std dev of elevation in 3x3 px        m
  unit                SIM 3292 unit at the cell centre -> unit_lookup.csv names (sim3292_unit_map.csv)
  ice_frac            hydrogen map (build_neutron_map.py): clip((h - 0.15) / 0.5, 0, 1)
  water_vapour_pr_um  MCD v6.2 water vapour column (fetch_mcd.py output h2o_ls*_lt12.txt), mean of the
                      4 seasons, kg/m2 x 1000 = precipitable microns; NaN if the files are missing
Run: python -m pipeline.data_fetch.build_mars_layers
"""
import glob
from pathlib import Path

import numpy as np
import pandas as pd

from ..common.config import get_processing
from ..common.coords import cell_id, grid_cells
from .fetch import ROOT

MARS = ROOT / "data" / "mars"
MARS_R_M = 3396190.0
# ice_frac from neutron suppression: 0.15 ~ dry equatorial background (hydrated minerals),
# 0.65 ~ polar ice-rich ground. So ice_frac 0.25 (config ice threshold) ~ h 0.275 (~55 deg lat).
H_DRY, H_ICE = 0.15, 0.65


def tes_ti(cells, grid_deg):
    a = np.fromfile(MARS / "tes_ti" / "global_ti_day_2007.img", dtype=">i2").reshape(3600, 7200).astype(float)
    a[a <= 0] = np.nan
    ppd, h = 20, grid_deg / 2
    out = []
    for lat, lon in cells:
        r0, r1 = int((90 - (lat + h)) * ppd), int((90 - (lat - h)) * ppd)
        c0, c1 = int((lon - h + 180) * ppd), int((lon + h + 180) * ppd)
        w = a[r0:r1, max(c0, 0):min(c1, 7200)]
        out.append(np.nanmedian(w) if np.isfinite(w).any() else np.nan)
    return np.array(out)


def mola_terrain(cells, grid_deg):
    """Median slope and 3x3 roughness per cell from the 463 m MOLA GeoTIFF, processed per latitude band."""
    import rasterio
    from rasterio.windows import Window
    from scipy.ndimage import uniform_filter

    path = MARS / "mola" / "Mars_MGS_MOLA_DEM_mosaic_global_463m.tif"
    slope = {}
    rough = {}
    with rasterio.open(path) as src:
        t = src.transform
        px = abs(t.a)                                  # metres per pixel (equirectangular, true at equator)
        deg_per_px = px / (MARS_R_M * np.pi / 180)
        x0_deg = t.c / (MARS_R_M * np.pi / 180)        # left edge in degrees
        y0_deg = t.f / (MARS_R_M * np.pi / 180)        # top edge in degrees
        lats = sorted({c[0] for c in cells})
        h = grid_deg / 2
        for lat in lats:
            r0 = int((y0_deg - (lat + h)) / deg_per_px) - 1
            r1 = int((y0_deg - (lat - h)) / deg_per_px) + 1
            z = src.read(1, window=Window(0, r0, src.width, r1 - r0)).astype(float)
            if src.nodata is not None:
                z[z == src.nodata] = np.nan
            dx = px * np.cos(np.radians(lat))
            gy, gx = np.gradient(z, px, dx)
            s = np.degrees(np.arctan(np.hypot(gx, gy)))
            m1 = uniform_filter(z, 3)
            m2 = uniform_filter(z * z, 3)
            r = np.sqrt(np.clip(m2 - m1 * m1, 0, None))
            for clat, clon in [c for c in cells if c[0] == lat]:
                c0 = int(((clon - h) - x0_deg) / deg_per_px)
                c1 = int(((clon + h) - x0_deg) / deg_per_px)
                slope[(clat, clon)] = float(np.nanmedian(s[1:-1, max(c0, 1):min(c1, src.width - 1)]))
                rough[(clat, clon)] = float(np.nanmedian(r[1:-1, max(c0, 1):min(c1, src.width - 1)]))
    return np.array([slope[c] for c in cells]), np.array([rough[c] for c in cells])


def geology_units(cells):
    import geopandas as gpd
    shp = glob.glob(str(MARS / "geology" / "sim3292" / "**" / "SIM3292_Global_Geology.shp"), recursive=True)
    if not shp:
        raise SystemExit("Unzip data/mars/geology/sim3292_database.zip into data/mars/geology/sim3292/")
    geo = gpd.read_file(shp[0])[["Unit", "geometry"]]
    pts = gpd.GeoDataFrame({"i": range(len(cells))},
                           geometry=gpd.points_from_xy([c[1] for c in cells], [c[0] for c in cells]), crs=geo.crs)
    hit = gpd.sjoin_nearest(pts, geo, how="left").drop_duplicates("i").sort_values("i")
    umap = dict(pd.read_csv(Path(__file__).with_name("sim3292_unit_map.csv"))[["unit_sim3292", "unit"]].values)
    missing = sorted(set(hit["Unit"]) - set(umap))
    if missing:
        raise ValueError(f"SIM 3292 units without a mapping: {missing}")
    return hit["Unit"].map(umap).to_numpy()


def hydrogen(cells):
    """Odyssey neutron suppression (0 = none, ~0.65 = polar ice) at each cell, nearest 5 deg bin."""
    h = pd.read_csv(MARS / "neutron" / "hydrogen_map_5deg.csv")
    lon360 = h["lon"].to_numpy()
    lat = h["lat"].to_numpy()
    out = []
    for clat, clon in cells:
        d = (lat - clat) ** 2 + (((lon360 - clon % 360) + 180) % 360 - 180) ** 2
        out.append(h["h_suppression"].to_numpy()[np.argmin(d)])
    return np.array(out)


def ice_fraction(cells):
    return np.clip((hydrogen(cells) - H_DRY) / (H_ICE - H_DRY), 0, 1)


def read_mcd_ascii(path):
    """MCD web ASCII map: '----' header row of latitudes, then rows 'lon || v1 v2 ...'. Returns lat, lon, grid."""
    lat, lons, rows = None, [], []
    for line in Path(path).read_text().splitlines():
        if "||" not in line:
            continue
        head, vals = line.split("||", 1)
        v = np.array(vals.split(), float)
        if head.strip().startswith("----"):
            lat = v
        else:
            lons.append(float(head))
            rows.append(v)
    return lat, np.array(lons), np.array(rows)            # rows: lon x lat


def water_vapour(cells):
    """Annual mean water vapour column (pr-um) from the MCD exports (Ls 0/90/180/270, 12:00)."""
    files = sorted(glob.glob(str(MARS / "mcd" / "h2o_ls*_lt12.txt")))
    if not files:
        print("NOTE: no data/mars/mcd/h2o_ls*_lt12.txt; water_vapour_pr_um = NaN (dryness unknown)")
        return np.full(len(cells), np.nan)
    maps = [read_mcd_ascii(f) for f in files]
    lat, lon = maps[0][0], maps[0][1]
    grid = np.mean([m[2] for m in maps], axis=0) * 1000.0     # kg/m2 -> precipitable microns
    out = []
    for clat, clon in cells:
        i = np.argmin(np.abs(((lon - clon) + 180) % 360 - 180))
        j = np.argmin(np.abs(lat - clat))
        out.append(grid[i, j])
    return np.array(out)


def build(params=None):
    p = params or get_processing()
    cells = grid_cells(p)
    g = p["grid_deg"]
    slope, rough = mola_terrain(cells, g)
    df = pd.DataFrame({
        "cell_id": [cell_id(a, b, p) for a, b in cells],
        "lat": [c[0] for c in cells], "lon": [c[1] for c in cells],
        "thermal_inertia": tes_ti(cells, g),
        "ice_frac": ice_fraction(cells),
        "unit": geology_units(cells),
        "slope_deg": slope, "roughness_m": rough,
        "water_vapour_pr_um": water_vapour(cells),
    })
    out = MARS / "layers.parquet"
    df.to_parquet(out, index=False)
    print(df.describe(include="all").T[["count", "mean", "min", "max"]].to_string())
    print("wrote", out)
    return df


if __name__ == "__main__":
    build()
