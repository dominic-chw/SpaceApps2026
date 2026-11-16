"""Build data/earth/layers.parquet: the real GLOBAL Earth grid (0.5 deg bare-ground cells).

Columns = common/synthetic.earth_layers (Dominic's contract) + a few extras the
downstream code ignores. One row per 0.5 deg land cell; build_grid.py applies the
bare-ground filter (and always keeps the 20 named regions).

Inputs (data/ is gitignored):
  data/earth/ee/earth_layers_grid_*.csv     Earth Engine land-grid export (ee_export --land-grid)
  data/earth/ee/earth_layers_antarctica.csv ice-free Antarctica (ee_export --antarctica)
  data/earth/ee/earth_modis_lc_*.csv        NASA MODIS barren / snow-ice fractions (ee_export --modis-lc)
  data/earth/raw/glim/                      GLiM 0.5 deg grid + Classnames.txt  -> glim_class
  data/earth/raw/gruber_pzi/PZI.flt         Gruber 2012 permafrost zonation (no longer used)
  data/earth/raw/pelletier_dtb/.../average_soil_and_sedimentary-deposit_thickness.tif -> bedrock_depth_m
  data/earth/cci_permafrost/{PFR,ALT}_2021.nc  ESA CCI Permafrost (25-85 N) -> ice inside coverage
Ground ice outside CCI coverage: the permafrost classifier (pipeline/earth_ml/permafrost.py).

Run: python -m pipeline.earth_library.build_layers
"""
import glob
import warnings
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
    grids = sorted(glob.glob(str(EE_DIR / "earth_layers_grid_*.csv")))
    # Several grid exports (e.g. 2 deg and 0.5 deg) would overlap: use the finest one.
    fine = [f for f in grids if "0p5deg" in Path(f).name] or grids
    files = fine + sorted(glob.glob(str(EE_DIR / "earth_layers_antarctica*.csv")))
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


def glim_grid_from_gdb(cache=EARTH / "glim" / "glim_0p5deg_centres.npy"):
    """GLiM class at the CENTRE of every 0.5 deg cell, from the LiMW_GIS 2015 vector geodatabase
    (data/earth/glim/*.gdb, 1.2 M polygons). Used when the 0.5 deg GLiM raster (data/earth/raw/glim)
    isn't there. Spatial join of 259,200 cell centres; cached as .npy (takes a few minutes once)."""
    if cache.exists():
        return np.load(cache, allow_pickle=True)
    import geopandas as gpd
    import pyogrio
    gdb = next((EARTH / "glim").glob("*.gdb"), None)
    if gdb is None:
        return None
    polys = pyogrio.read_dataframe(gdb, columns=["xx"], use_arrow=True)
    n_rows, n_cols = int(180 / GRID), int(360 / GRID)
    lat = 90 - (np.arange(n_rows) + 0.5) * GRID
    lon = -180 + (np.arange(n_cols) + 0.5) * GRID
    lon2, lat2 = np.meshgrid(lon, lat)
    pts = gpd.GeoDataFrame(geometry=gpd.points_from_xy(lon2.ravel(), lat2.ravel()), crs=4326).to_crs(polys.crs)
    joined = gpd.sjoin(pts, polys, predicate="within", how="left")
    joined = joined[~joined.index.duplicated(keep="first")].sort_index()
    codes = joined["xx"].astype(object).where(joined["xx"].notna(), None).to_numpy(object).reshape(n_rows, n_cols)
    codes = np.array([[c.strip() if isinstance(c, str) else None for c in row] for row in codes], dtype=object)
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.save(cache, codes, allow_pickle=True)
    return codes


def glim_grid():
    """GLiM 0.5 deg grid as 2-letter class codes (object array, 360 x 720, row 0 = 90 N)."""
    folder = RAW / "glim"
    if not (folder / "Classnames.txt").exists():
        return glim_grid_from_gdb()
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


CCI_DIR = EARTH / "cci_permafrost"
CCI_TOP, CCI_BOTTOM = 85.0, 25.0     # ESA CCI Permafrost v4 NH grid: 0.01 deg, 85 N .. 25 N


def cci_grid(var):
    """ESA CCI Permafrost 2021 (`PFR` fraction 0-1 or `ALT` active-layer metres) as a 0.5 deg
    grid on the same 360 x 720 layout as the other grids (row 0 = 90 N), MEAN of the 0.01 deg
    pixels with data; NaN outside CCI coverage (south of 25 N, oceans). Cached as .npy."""
    cache = CCI_DIR / f"{var.lower()}_0p5deg_mean.npy"
    if cache.exists():
        return np.load(cache)
    path = CCI_DIR / f"{var}_2021.nc"
    if not path.exists():
        return None
    import h5py
    with h5py.File(path, "r") as h:
        ds = h[var]
        fill = int(ds.attrs["_FillValue"][0])
        scale = 0.01 if var == "PFR" else float(ds.attrs.get("scale_factor", [1.0])[0])
        lats = h["lat"][:]
        step = int(round(GRID / 0.01))                                   # 50 pixels per cell
        n_rows, n_cols = ds.shape[1] // step, ds.shape[2] // step
        out = np.full((n_rows, n_cols), np.nan)
        north_first = lats[0] > lats[-1]
        for r in range(n_rows):
            r0 = r * step if north_first else ds.shape[1] - (r + 1) * step
            block = ds[0, r0:r0 + step, :].astype(float)
            block[block == fill] = np.nan
            cells = block.reshape(step, n_cols, step).transpose(1, 0, 2).reshape(n_cols, -1)
            with np.errstate(all="ignore"), __import__("warnings").catch_warnings():
                __import__("warnings").simplefilter("ignore", RuntimeWarning)
                out[r] = np.nanmean(cells, axis=1) * scale
    full = np.full((int(180 / GRID), int(360 / GRID)), np.nan)
    top = int(round((90 - CCI_TOP) / GRID))
    full[top:top + out.shape[0]] = out
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.save(cache, full)
    return full


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
COLD_K = 271.15  # ground averaging below -2 C is permafrost where no map covers it (Dominic, PR #12)


def ground_labels(ice, ice_frac, glim, bare, slope, bedrock_depth):
    """ground_label from the FINAL ice label (CCI or permafrost model).

    massive_ice (MODIS snow/ice >= 0.5) > ice_cemented (ice == 1) > bedrock (fallback when no
    Pelletier depth: crystalline GLiM, bare >= 0.5, slope >= 15 deg) > dry_regolith (anything else
    with data). None where ice is unknown (the model was unsure) or nothing is known: the gap model
    fills those, marked predicted. build_grid.py refines bedrock / active layer per depth band.
    """
    ice = np.asarray(ice, float)
    ice_frac0 = np.nan_to_num(np.asarray(ice_frac, float), nan=0.0)
    has_data = np.isfinite(ice) | np.isfinite(np.asarray(ice_frac, float)) | pd.notna(glim)
    ground = np.where(has_data, "dry_regolith", None).astype(object)
    fallback_bedrock = (np.isnan(bedrock_depth) & np.isin(glim, list(CRYSTALLINE))
                        & (np.nan_to_num(bare) >= 0.5) & (np.nan_to_num(slope) >= 15))
    ground[fallback_bedrock] = "bedrock"
    ground[ice == 1] = "ice_cemented"
    ground[np.isnan(ice) & (ice_frac0 < 0.5)] = None
    ground[ice_frac0 >= 0.5] = "massive_ice"
    return ground


def ice_labels(df, params=None, report_path=None, model_path=None):
    """Ground ice per cell: ESA CCI inside its coverage (25-85 N, measured), the permafrost model
    outside it (predicted; NaN when its 90% conformal set is {0, 1}). MODIS snow/ice >= 0.5 is ice.
    Returns (ice, ice_source, ice_prob, pfr, alt_m, report)."""
    from ..earth_ml import permafrost
    lat, lon = df["lat"].to_numpy(float), df["lon"].to_numpy(float)
    pfr_grid, alt_grid = cci_grid("PFR"), cci_grid("ALT")
    pfr = lookup(pfr_grid, lat, lon) if pfr_grid is not None else np.full(len(df), np.nan)
    alt = lookup(alt_grid, lat, lon) if alt_grid is not None else np.full(len(df), np.nan)
    work = df.assign(pfr=pfr)
    bundle, report = permafrost.train_and_report(work, params, report_path=report_path)
    prob, model_label = permafrost.predict(bundle, work)
    if model_path is not None:
        import joblib
        Path(model_path).parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(bundle, model_path)
    in_cci = np.isfinite(pfr)
    ice = np.where(in_cci, (pfr >= permafrost.ICE_MIN).astype(float), model_label)
    source = np.where(in_cci, "measured", "predicted").astype(object)
    snow_ice = df["modis_snow_ice"].to_numpy(float) if "modis_snow_ice" in df else np.full(len(df), np.nan)
    glacier = np.nan_to_num(snow_ice) >= 0.5
    ice[glacier], source[glacier] = 1.0, "measured"
    return ice, source, np.where(in_cci, pfr, prob), pfr, alt, report


def subsurface(df, report_path=None, model_path=None):
    """Ground temperature at 0/1/2/5/10 m per cell (earth_ml/profile_nn): ESA CCI inside its coverage,
    the physics-informed PyTorch ensemble outside it, NaN where the ensemble is extrapolating.
    None when torch is not installed (build_grid then uses the heat model everywhere)."""
    from ..earth_ml import profile_nn
    if not profile_nn.available():
        warnings.warn("torch not installed: no subsurface ensemble; the heat model gives ground temperatures")
        return None
    nn_path = None if model_path is None else Path(model_path).with_name("profile_ensemble.pt")
    sub, summary = profile_nn.subsurface_columns(df, nn_path)
    if report_path is not None:
        from ..earth_ml.permafrost import write_report
        write_report({"subsurface_production": summary}, report_path)
    print(f"subsurface: CCI {summary['from_cci']}, neural net {summary['from_nn']}, "
          f"extrapolating (heat model) {summary['extrapolated']}")
    return sub


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

def build(ee_df=None, glim=None, pelletier=None, out=EARTH / "layers.parquet", params=None,
          report_path=ROOT / "output" / "ml_report.json", model_path=ROOT / "models" / "permafrost.joblib"):
    ee_df = load_ee() if ee_df is None else ee_df
    ee_df = tag_regions(ee_df)
    lat, lon = ee_df["lat"].to_numpy(float), ee_df["lon"].to_numpy(float)
    antarctic = lat < ANTARCTIC_LAT

    glim = glim_grid() if glim is None else glim
    pelletier = pelletier_grid() if pelletier is None else pelletier

    glim_class = lookup(glim, lat, lon) if glim is not None else np.full(len(lat), None, dtype=object)
    glim_class = np.where(pd.isna(glim_class), "nd", glim_class)
    depth = lookup(pelletier, lat, lon) if pelletier is not None else np.full(len(lat), np.nan)

    get = lambda c: ee_df[c].to_numpy(float) if c in ee_df else np.full(len(lat), np.nan)
    # Ice-free Antarctic cells were picked because MODIS sees barren ground at 1 km; their 5 km mean
    # rounds to ~0 and WorldCover has no Antarctica, so they count as bare ground here.
    bare = np.where(antarctic, 1.0, get("bare_fraction"))
    ice, ice_source, ice_prob, pfr, alt, _ = ice_labels(ee_df.assign(glim_class=glim_class), params,
                                                        report_path, model_path)
    ground = ground_labels(ice, get("modis_snow_ice"), glim_class, bare, get("slope_deg"), depth)
    sub = subsurface(ee_df.assign(glim_class=glim_class), report_path, model_path)
    aridity = get("aridity")
    aridity = np.where(antarctic & np.isnan(aridity), 0.0, aridity)                 # assumed (MANIFEST)

    df = pd.DataFrame({
        "cell_id": ee_df["cell_id"], "name": ee_df["name"], "lat": lat, "lon": lon,
        "elevation_m": get("elevation_m"), "bare_fraction": bare,
        "mean_temp_k": get("skin_temperature_mean"), "annual_range_k": get("skin_temperature_range"),
        "diurnal_amp_k": (get("lst_day_k") - get("lst_night_k")) / 2,
        "thermal_inertia": soil_thermal_inertia(get("sand_0_2m"), get("bdod_0_2m")),
        # perm_prob (contract column): CCI permafrost fraction inside 25-85 N, model P(ice) outside.
        # Not a predictor anywhere (earth_ml/permafrost.LEAKY).
        "aridity": aridity, "perm_prob": ice_prob, "glim_class": glim_class,
        "slope_deg": get("slope_deg"), "roughness_m": get("roughness_m"),
        "bedrock_depth_m": depth, "ice_label": ice, "ground_label": ground,
        # extras (ignored downstream unless used on purpose)
        "ice_source": ice_source, "cci_pfr": pfr, "cci_alt_m": alt,
        "frozen_l1": get("soil_temperature_level_1_frozen"), "frozen_l2": get("soil_temperature_level_2_frozen"),
        "frozen_l3": get("soil_temperature_level_3_frozen"), "frozen_l4": get("soil_temperature_level_4_frozen"),
        "modis_barren": get("modis_barren"), "modis_snow_ice": get("modis_snow_ice"),
    })
    if sub is not None:
        df = pd.concat([df, sub.reset_index(drop=True)], axis=1)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(out, index=False)
        pred = ice_source == "predicted"
        print(f"wrote {out}: {len(df)} cells, {int((df['name'] != '').sum())} named regions, "
              f"ice measured {int((~pred).sum())}, predicted {int(np.isfinite(ice[pred]).sum())}, "
              f"uncertain {int(np.isnan(ice).sum())}, bedrock depth known {int(np.isfinite(depth).sum())}")
    return df


if __name__ == "__main__":
    build()
