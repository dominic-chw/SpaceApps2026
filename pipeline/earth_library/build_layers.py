"""Build data/earth/layers.parquet: the real GLOBAL Earth grid (0.5 deg bare-ground cells).

Columns = common/synthetic.earth_layers (Dominic's contract) + a few extras the
downstream code ignores. One row per 0.5 deg land cell; build_grid.py applies the
bare-ground filter (and always keeps the 20 named regions).

Inputs (data/ is gitignored):
  data/earth/ee/earth_layers_grid_*.csv     Earth Engine land-grid export (ee_export --land-grid)
  data/earth/ee/earth_layers_antarctica.csv ice-free Antarctica (ee_export --antarctica)
  data/earth/ee/earth_modis_lc_*.csv        NASA MODIS barren / snow-ice fractions (ee_export --modis-lc)
  data/earth/raw/glim/                      GLiM 0.5 deg grid + Classnames.txt  -> glim_class
  data/earth/raw/gruber_pzi/PZI.flt         Gruber 2012 permafrost zonation     -> perm_prob
  data/earth/raw/pelletier_dtb/.../average_soil_and_sedimentary-deposit_thickness.tif -> bedrock_depth_m
Rules for perm_prob / ice_label / ground_label: data/earth/raw/MANIFEST.md and ground_lookup.csv.

Run: python -m pipeline.earth_library.build_layers
"""
import glob
from pathlib import Path

import numpy as np
import pandas as pd

from .local_layers import load_regions

ROOT = Path(__file__).resolve().parents[2]
EARTH = ROOT / "data" / "earth"
EE_DIR = EARTH / "ee"
RAW = EARTH / "raw"
GRID = 0.5                  # degrees
FINE = 1 / 120              # 30 arc-seconds (~1 km): Gruber and Pelletier
CRYSTALLINE = {"va", "vi", "vb", "pa", "pi", "pb", "mt"}   # GLiM codes for the bedrock fallback rule
ANTARCTIC_LAT = -60.0


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def cell_index(lat, lon, top=90.0, grid=GRID):
    """Row and column of the grid cell that contains each (lat, lon); row 0 = northernmost."""
    row = np.floor((top - np.asarray(lat, float)) / grid).astype(int)
    col = np.floor((np.asarray(lon, float) + 180.0) / grid).astype(int) % int(round(360 / grid))
    return row, col


def lookup(grid, lat, lon, top=90.0):
    """Value of a 0.5 deg grid at each point; NaN where outside the grid."""
    row, col = cell_index(lat, lon, top)
    inside = (row >= 0) & (row < grid.shape[0])
    out = np.full(len(row), np.nan, dtype=object if grid.dtype == object else float)
    out[inside] = grid[row[inside], col[inside]]
    return out


def coarsen(read_rows, n_rows, n_cols, how, cache):
    """Shrink a 30" (~1 km) raster to 0.5 deg cells, 60 x 60 fine pixels per cell.

    read_rows(r0, r1) returns fine rows r0..r1 as float with NaN for nodata.
    how = "mean" or "median". The result is cached as .npy (these rasters are GBs).
    """
    if cache.exists():
        return np.load(cache)
    step = int(round(GRID / FINE))                     # 60
    out = np.full((n_rows // step, n_cols // step), np.nan)
    reducer = np.nanmean if how == "mean" else np.nanmedian
    for r in range(n_rows // step):
        block = read_rows(r * step, (r + 1) * step)    # (60, 43200)
        cells = block.reshape(step, n_cols // step, step).transpose(1, 0, 2).reshape(n_cols // step, -1)
        with np.errstate(all="ignore"), __import__("warnings").catch_warnings():
            __import__("warnings").simplefilter("ignore", RuntimeWarning)
            out[r] = reducer(cells, axis=1)
    np.save(cache, out)
    return out


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

def load_ee():
    """All Earth Engine grid CSVs (land grid + Antarctica), joined with MODIS land cover."""
    files = sorted(glob.glob(str(EE_DIR / "earth_layers_grid_*.csv"))) + \
        sorted(glob.glob(str(EE_DIR / "earth_layers_antarctica*.csv")))
    if not files:
        raise SystemExit("No Earth Engine grid CSVs in data/earth/ee/ (download the spaceapps_ee Drive folder)")
    df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    df = df.drop(columns=["system:index", ".geo"], errors="ignore").drop_duplicates("cell_id", keep="last")
    lc_files = sorted(glob.glob(str(EE_DIR / "earth_modis_lc_*.csv")))
    if lc_files:
        lc = pd.concat([pd.read_csv(f) for f in lc_files], ignore_index=True)[
            ["cell_id", "modis_barren", "modis_snow_ice"]].drop_duplicates("cell_id")
        df = df.drop(columns=["modis_barren", "modis_snow_ice"], errors="ignore").merge(lc, on="cell_id", how="left")
    for c in ("modis_barren", "modis_snow_ice"):
        if c not in df:
            df[c] = np.nan
    return df.reset_index(drop=True)


def glim_grid():
    """GLiM 0.5 deg grid as 2-letter class codes (object array, 360 x 720, row 0 = 90 N)."""
    folder = RAW / "glim"
    names = pd.read_csv(folder / "Classnames.txt", sep=";")
    code_of = dict(zip(names["Value_"].astype(int), names["xx"].str.strip()))
    ids = np.loadtxt(next(folder.glob("*.asc")), skiprows=6)
    codes = np.full(ids.shape, None, dtype=object)
    for value, code in code_of.items():
        codes[ids == value] = code
    return codes


def gruber_grid():
    """Gruber 2012 Permafrost Zonation Index, mean per 0.5 deg cell (300 x 720, 90 N .. 60 S)."""
    flt = RAW / "gruber_pzi" / "PZI.flt"
    n_rows, n_cols = 18000, 43200
    if not flt.exists() or flt.stat().st_size != n_rows * n_cols * 4:
        print("WARNING: Gruber PZI missing or still downloading; perm_prob from Gruber skipped")
        return None
    data = np.memmap(flt, dtype="<f4", mode="r", shape=(n_rows, n_cols))

    def rows(r0, r1):
        block = np.array(data[r0:r1], dtype=float)
        block[block < 0] = np.nan          # -9999 nodata (ocean / outside model)
        return block
    return coarsen(rows, n_rows, n_cols, "mean", RAW / "gruber_pzi" / "pzi_0p5deg_mean.npy")


def pelletier_grid():
    """Pelletier 2016 soil + sediment thickness (m), MEAN per 0.5 deg cell (300 x 720, 90 N .. 60 S).

    Mean, not median: values are whole metres and the median is 0-1 m almost everywhere, which made
    nearly every cell "bedrock" (e.g. Atacama). The mean keeps the deeper sediment pockets a
    drill team would actually pick (Hanksville 3.9 m, Atacama high 2.5 m)."""
    tifs = glob.glob(str(RAW / "pelletier_dtb" / "**" / "average_soil_and_sedimentary-deposit_thickness.tif"),
                     recursive=True)
    if not tifs:
        print("WARNING: Pelletier thickness not found; bedrock_depth_m left empty")
        return None
    import rasterio
    from rasterio.windows import Window
    src = rasterio.open(tifs[0])

    def rows(r0, r1):
        block = src.read(1, window=Window(0, r0, src.width, r1 - r0)).astype(float)
        block[block < 0] = np.nan          # nodata -1
        return block
    try:
        return coarsen(rows, src.height, src.width, "mean", RAW / "pelletier_dtb" / "dtb_0p5deg_mean.npy")
    finally:
        src.close()


# ---------------------------------------------------------------------------
# Rules (MANIFEST.md / ground_lookup.csv)
# ---------------------------------------------------------------------------

WARM_K = 278.0   # ground averaging above +5 C can't hold permafrost ice (Dominic's rule)


def labels(lat, perm, ice_frac, glim, bare, slope, bedrock_depth, mean_temp_k=None):
    """perm_prob is final here; returns (ice_label, ground_label, assumed_antarctic).

    ice_label:    1 if ice_frac >= 0.5, perm >= 0.5 or ice-free Antarctica;
                  0 if perm < 0.1 and ice_frac < 0.1 (missing ice_frac counts as 0); else NaN.
    ground_label: massive_ice (ice_frac >= 0.5) > ice_cemented (perm >= 0.5 or Antarctica)
                  > bedrock (fallback when no Pelletier depth: crystalline GLiM, bare >= 0.5, slope >= 15 deg)
                  > dry_regolith (anything else with data) > NaN (nothing known).
                  build_grid.py refines bedrock / active layer per depth band.
    """
    antarctic = lat < ANTARCTIC_LAT
    ice_frac0 = np.nan_to_num(ice_frac, nan=0.0)
    ice = np.full(len(lat), np.nan)
    ice[(perm < 0.1) & (ice_frac0 < 0.1)] = 0.0
    if mean_temp_k is not None:
        ice[np.isnan(ice) & (np.asarray(mean_temp_k, float) > WARM_K)] = 0.0
    ice[(ice_frac0 >= 0.5) | (perm >= 0.5) | antarctic] = 1.0

    has_data = np.isfinite(perm) | np.isfinite(ice_frac) | pd.notna(glim)
    ground = np.where(has_data, "dry_regolith", None).astype(object)
    fallback_bedrock = (np.isnan(bedrock_depth) & np.isin(glim, list(CRYSTALLINE))
                        & (np.nan_to_num(bare) >= 0.5) & (np.nan_to_num(slope) >= 15))
    ground[fallback_bedrock] = "bedrock"
    ground[(perm >= 0.5) | antarctic] = "ice_cemented"
    ground[ice_frac0 >= 0.5] = "massive_ice"
    return ice, ground, antarctic


def soil_thermal_inertia(sand_gkg, bdod_cgcm3):
    """SoilGrids proxy: sqrt(k * rho * c), k = 0.3 + 1.5 sand, c = 800 J/kg/K, clipped 300..2500."""
    sand = np.asarray(sand_gkg, float) / 1000
    rho = np.asarray(bdod_cgcm3, float) * 10          # cg/cm3 -> kg/m3
    k = 0.3 + 1.5 * sand
    return np.clip(np.sqrt(k * rho * 800.0), 300, 2500)


def tag_regions(df):
    """The cell containing each regions.geojson point takes its region_id as cell_id + its name."""
    df = df.copy()
    df["name"] = ""
    regions = load_regions()
    rows, cols = cell_index(df["lat"], df["lon"])
    where = {(r, c): i for i, (r, c) in enumerate(zip(rows, cols))}
    r_rows, r_cols = cell_index(regions["lat"], regions["lon"])
    for region, r, c in zip(regions.itertuples(), r_rows, r_cols):
        i = where.get((r, c))
        if i is None:
            print(f"WARNING: region {region.region_id} is not on an exported land cell")
        elif df.at[i, "name"]:
            print(f"WARNING: region {region.region_id} shares a cell with {df.at[i, 'cell_id']}; kept the first")
        else:
            df.at[i, "cell_id"], df.at[i, "name"] = region.region_id, region.name
    return df


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build(ee_df=None, glim=None, gruber=None, pelletier=None, out=EARTH / "layers.parquet"):
    ee_df = load_ee() if ee_df is None else ee_df
    ee_df = tag_regions(ee_df)
    lat, lon = ee_df["lat"].to_numpy(float), ee_df["lon"].to_numpy(float)
    antarctic = lat < ANTARCTIC_LAT

    glim = glim_grid() if glim is None else glim
    gruber = gruber_grid() if gruber is None else gruber
    pelletier = pelletier_grid() if pelletier is None else pelletier

    glim_class = lookup(glim, lat, lon)
    glim_class = np.where(pd.isna(glim_class), "nd", glim_class)
    perm = lookup(gruber, lat, lon) if gruber is not None else np.full(len(lat), np.nan)
    perm = np.where(np.isnan(perm) & ~antarctic & (gruber is not None), 0.0, perm)   # outside zones = 0
    perm = np.where(antarctic, 1.0, perm)                                           # assumed (MANIFEST)
    depth = lookup(pelletier, lat, lon) if pelletier is not None else np.full(len(lat), np.nan)

    get = lambda c: ee_df[c].to_numpy(float) if c in ee_df else np.full(len(lat), np.nan)
    bare = get("bare_fraction")
    ice, ground, assumed = labels(lat, perm, get("modis_snow_ice"), glim_class, bare,
                                  get("slope_deg"), depth, get("skin_temperature_mean"))
    aridity = get("aridity")
    aridity = np.where(antarctic & np.isnan(aridity), 0.0, aridity)                 # assumed (MANIFEST)

    df = pd.DataFrame({
        "cell_id": ee_df["cell_id"], "name": ee_df["name"], "lat": lat, "lon": lon,
        "elevation_m": get("elevation_m"), "bare_fraction": bare,
        "mean_temp_k": get("skin_temperature_mean"), "annual_range_k": get("skin_temperature_range"),
        "diurnal_amp_k": (get("lst_day_k") - get("lst_night_k")) / 2,
        "thermal_inertia": soil_thermal_inertia(get("sand_0_2m"), get("bdod_0_2m")),
        "aridity": aridity, "perm_prob": perm, "glim_class": glim_class,
        "slope_deg": get("slope_deg"), "roughness_m": get("roughness_m"),
        "bedrock_depth_m": depth, "ice_label": ice, "ground_label": ground,
        # extras (ignored downstream unless used on purpose)
        "frozen_l1": get("soil_temperature_level_1_frozen"), "frozen_l2": get("soil_temperature_level_2_frozen"),
        "frozen_l3": get("soil_temperature_level_3_frozen"), "frozen_l4": get("soil_temperature_level_4_frozen"),
        "modis_barren": get("modis_barren"), "modis_snow_ice": get("modis_snow_ice"),
        "antarctic_assumed": assumed,
    })
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(out, index=False)
        print(f"wrote {out}: {len(df)} cells, {int((df['name'] != '').sum())} named regions, "
              f"ice known {int(np.isfinite(ice).sum())}, bedrock depth known {int(np.isfinite(depth).sum())}")
    return df


if __name__ == "__main__":
    build()
