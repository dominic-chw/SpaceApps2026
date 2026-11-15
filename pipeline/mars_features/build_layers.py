"""Build data/mars/layers.parquet: the REAL Mars source layers on the 2 deg grid (+-70 deg).

Columns = common/synthetic.mars_layers (what extract.py reads):
  cell_id, lat, lon, thermal_inertia, ice_frac, unit, slope_deg, roughness_m, water_vapour_pr_um
plus extras: ice_quality, unit_name.

Sources (all NASA / USGS; raw files in data/mars/raw/, gitignored):
  thermal_inertia  TES night-time thermal inertia map, Putzig & Mellon 2007 (PDS MGST_9001), cell median
  unit             USGS Geologic Map of Mars SIM 3292 (Tanaka et al. 2014), majority unit per cell
  slope_deg,       MOLA 463 m DEM: slope and the std. dev. of elevation in a 3 x 3 pixel window,
  roughness_m      the SAME method and scale as the Earth side (500 m), then the cell mean
  ice_frac         ice-stability rule by latitude (SWIM was offline): see ice_by_latitude()
  water_vapour_pr_um  left empty (dryness isn't used on Mars)

Run: python -m pipeline.mars_features.build_layers
"""
from pathlib import Path

import numpy as np
import pandas as pd

from ..common.config import get_processing
from ..common.coords import cell_id, grid_cells

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "mars" / "raw"
OUT = ROOT / "data" / "mars" / "layers.parquet"
MARS_RADIUS_M = 3396190.0

# Cells holding well-known clay / sulfate sites get unit "clay_sulfate_site" (rock clay_sulfate):
# the global geologic map can't show mineralogy. Tagged by hand (plan: assumed -> modelled).
CLAY_SULFATE_SITES = {
    "Meridiani Planum": (-1.95, -5.53), "Mawrth Vallis": (24.0, -19.0), "Gale crater": (-5.4, 137.8),
    "Valles Marineris ILDs": (-7.0, -72.0), "Jezero crater": (18.4, 77.5),
}


def ice_by_latitude(lat):
    """Ground-ice fraction (0-1) from where ice is stable on Mars today. Tagged modelled.

    - |lat| >= 55: ice-cemented ground a few cm down (Phoenix at 68 N found it at ~5 cm;
      neutron spectrometer maps show high hydrogen poleward of ~55-60 deg) -> 0.6
    - 35 N .. 55 N: buried EXCESS ice at ~1-10 m (SHARAD radar / SWIM: Arcadia from ~38 N,
      Bramson et al. 2015; Utopia; Deuteronilus) -> 0.75 (massive_ice in extract.py)
    - 40 S .. 55 S: mid-latitude ice, less mapped -> 0.5
    - equatorward of that (35 N / 40 S): no near-surface ice expected -> 0.0
    The depth of the ice table per latitude is in extract.ice_table_depth_m().
    """
    lat = np.asarray(lat, float)
    a = np.abs(lat)
    return np.select([a >= 55, (lat >= 35) & (lat < 55), (lat <= -40) & (lat > -55)],
                     [0.6, 0.75, 0.5], default=0.0)


def cell_box_index(lat, lon, rows, cols, north=90.0, west=-180.0, half=1.0):
    """Pixel row/col slices of a global lat/lon raster (rows x cols) covering a 2 deg cell."""
    dy, dx = 180.0 / rows, 360.0 / cols
    r0, r1 = int(round((north - (lat + half)) / dy)), int(round((north - (lat - half)) / dy))
    c0, c1 = int(round((lon - half - west) / dx)), int(round((lon + half - west) / dx))
    return slice(r0, r1), slice(c0, c1)


def tes_thermal_inertia(cells):
    """Median TES night-time thermal inertia per cell (J m-2 K-1 s-1/2); 0 / negative = no data."""
    img = next((RAW / "tes_ti").rglob("global_ti_night_2007.img"), None)
    if img is None:
        print("WARNING: TES thermal inertia not found; thermal_inertia left empty")
        return np.full(len(cells), np.nan)
    a = np.fromfile(img, dtype=">i2").reshape(3600, 7200).astype(float)
    a[a <= 0] = np.nan
    out = []
    for lat, lon in zip(cells["lat"], cells["lon"]):
        rs, cs = cell_box_index(lat, lon, 3600, 7200)
        block = a[rs, cs]
        out.append(np.nanmedian(block) if np.isfinite(block).any() else np.nan)
    return np.array(out)


def geologic_units(cells, per_side=5):
    """Majority USGS SIM 3292 unit per cell: sample a 5 x 5 lattice of points in each cell."""
    gdb = next((RAW / "sim3292").rglob("SIM3292_geodatabase.gdb"), None)
    if gdb is None:
        print("WARNING: SIM 3292 geologic map not found; unit left empty")
        return pd.Series([None] * len(cells)), pd.Series([None] * len(cells))
    import geopandas as gpd
    geo = gpd.read_file(gdb, layer="SIM3292_Global_Geology")[["Unit", "UnitDesc", "geometry"]]
    offsets = (np.arange(per_side) + 0.5) / per_side * 2.0 - 1.0          # -0.8 .. 0.8 deg
    pts = [(i, lat + dy, lon + dx) for i, (lat, lon) in enumerate(zip(cells["lat"], cells["lon"]))
           for dy in offsets for dx in offsets]
    idx, plat, plon = zip(*pts)
    # The map is in a Mars sphere Robinson projection: build points on the same Mars sphere.
    mars_geo = geo.crs.geodetic_crs
    p = gpd.GeoDataFrame({"i": idx}, geometry=gpd.points_from_xy(plon, plat), crs=mars_geo).to_crs(geo.crs)
    hit = gpd.sjoin(p, geo, how="left", predicate="within")
    majority = hit.dropna(subset=["Unit"]).groupby("i")["Unit"].agg(lambda s: s.value_counts().index[0])
    desc = dict(zip(geo["Unit"], geo["UnitDesc"]))
    units = pd.Series(range(len(cells))).map(majority)
    return units, units.map(desc)


OVERLAP_PX = 2   # extra pixels read around each strip so the 3 x 3 window and gradient see real neighbours


def strip_terrain(z, lat_top, deg_per_px, px_m):
    """Slope (deg) and roughness (m) for one strip of the DEM, all in float32.

    z: float32 elevations (rows x cols), already padded by OVERLAP_PX on every side
    (real rows above/below, wrapped columns across the 180 deg seam).
    Returns arrays the same shape as z; the caller crops the overlap off.
    """
    from scipy.ndimage import uniform_filter
    # East-west pixel size shrinks with latitude: one value per row.
    row_lat = lat_top - (np.arange(z.shape[0], dtype=np.float32) + 0.5) * deg_per_px
    dx = (px_m * np.cos(np.radians(row_lat))).astype(np.float32)[:, None]
    gy = np.gradient(z, axis=0) / np.float32(px_m)
    gx = np.gradient(z, axis=1) / dx
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    del gx, gy
    # Roughness = std. dev. in a 3 x 3 window = sqrt(mean(z^2) - mean(z)^2). Subtract the strip's
    # mean first: elevations reach ~20 km, and squaring raw values in float32 would lose precision.
    zc = z - np.float32(np.nanmean(z))
    m1 = uniform_filter(zc, 3)
    m2 = uniform_filter(zc * zc, 3)
    rough = np.sqrt(np.clip(m2 - m1 * m1, 0, None))
    return slope.astype(np.float32), rough.astype(np.float32)


def mola_terrain(cells, scale_m=500.0):
    """Mean slope (deg) and roughness (m) per cell from the MOLA 463 m DEM.

    Same recipe as Earth (ee_export.terrain_stats): elevation at ~500 m, slope from the
    gradient, roughness = std. dev. of elevation in a 3 x 3 window, then the cell mean.

    Memory: the 2 GB DEM is never loaded whole. It's read one 2 deg latitude row at a time
    (a rasterio window, ~256 x 46,080 pixels) plus OVERLAP_PX rows above and below, in
    float32 (~50 MB per array, a few hundred MB peak).
    """
    tif = next((RAW / "mola").glob("*.tif"), None)
    if tif is None or tif.stat().st_size < 2_000_000_000:
        print("WARNING: MOLA DEM missing or still downloading; slope/roughness left empty")
        return np.full(len(cells), np.nan), np.full(len(cells), np.nan)
    import rasterio
    from rasterio.windows import Window
    slope = np.full(len(cells), np.nan)
    rough = np.full(len(cells), np.nan)
    pad = OVERLAP_PX
    # GDAL's block cache defaults to a share of system RAM and counts against our process;
    # each strip is read exactly once, so a small cache is enough.
    with rasterio.Env(GDAL_CACHEMAX=64), rasterio.open(tif) as src:
        rows, cols = src.height, src.width
        nodata = src.nodata
        px_m = 2 * np.pi * MARS_RADIUS_M / cols                         # ~463 m at the equator
        deg_per_px = 180.0 / rows
        for lat, members in cells.groupby("lat").groups.items():
            rs, _ = cell_box_index(lat, 0.0, rows, cols)
            r0, r1 = max(rs.start - pad, 0), min(rs.stop + pad, rows)
            z = src.read(1, window=Window(0, r0, cols, r1 - r0)).astype(np.float32)
            if nodata is not None:
                z[z == nodata] = np.nan
            z = np.pad(z, ((0, 0), (pad, pad)), mode="wrap")         # wrap across the 180 deg seam
            s, r = strip_terrain(z, 90.0 - r0 * deg_per_px, deg_per_px, px_m)
            del z
            top = rs.start - r0                                         # crop the overlap rows
            s, r = s[top:top + (rs.stop - rs.start)], r[top:top + (rs.stop - rs.start)]
            for i in members:
                _, cs = cell_box_index(lat, cells.at[i, "lon"], rows, cols)
                cs = slice(cs.start + pad, cs.stop + pad)               # columns shifted by the wrap pad
                slope[i] = np.nanmean(s[:, cs], dtype=np.float64)
                rough[i] = np.nanmean(r[:, cs], dtype=np.float64)
            del s, r
    return slope, rough


def build(out=OUT, params=None):
    p = params or get_processing()
    grid = grid_cells(p)
    cells = pd.DataFrame({"lat": [c[0] for c in grid], "lon": [c[1] for c in grid]})
    cells.insert(0, "cell_id", [cell_id(a, b, p) for a, b in grid])

    units, unit_names = geologic_units(cells)
    for name, (lat, lon) in CLAY_SULFATE_SITES.items():
        i = cells.index[cells["cell_id"] == cell_id(lat, lon, p)][0]     # the cell CONTAINING the site
        units[i], unit_names[i] = "clay_sulfate_site", f"{name} (hand-tagged clay/sulfate site)"
    slope, rough = mola_terrain(cells)

    df = cells.assign(
        thermal_inertia=tes_thermal_inertia(cells),
        ice_frac=ice_by_latitude(cells["lat"]),
        ice_quality="modelled",
        unit=units.to_numpy(), unit_name=unit_names.to_numpy(),
        slope_deg=slope, roughness_m=rough,
        water_vapour_pr_um=np.nan,
    )
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(out, index=False)
        print(f"wrote {out}: {len(df)} cells; TI known {int(df.thermal_inertia.notna().sum())}, "
              f"unit known {int(df.unit.notna().sum())}, slope known {int(np.isfinite(slope).sum())}")
    return df


if __name__ == "__main__":
    build()
