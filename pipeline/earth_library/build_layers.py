"""Build data/earth/layers.parquet (columns of common/synthetic.earth_layers) from the Earth Engine
CSVs in data/earth/ee/ plus the local GLiM, CCI Permafrost and NSIDC ground-ice files.
Rows: the 20 named regions (earth_layers_*regions*.csv / earth_layers_atacama.csv) and the bare-ground
0.5 deg grid cells (earth_layers_grid_*.csv) when present.

Column sources
  elevation_m, slope_deg, roughness_m   Copernicus DEM averaged to 500 m (EE)
  bare_fraction                         WorldCover class 60 fraction (EE)
  mean_temp_k, annual_range_k           ERA5-Land skin temperature mean / max-min of monthly means (EE)
  diurnal_amp_k                         MODIS LST (day - night) / 2 (EE)
  thermal_inertia                       SoilGrids proxy: sqrt(k * rho * c) with k = 0.3 + 1.5 sand,
                                        rho = bulk density, c = 800 J/kg/K, clipped 300..2500
  aridity                               Global Aridity Index (EE)
  glim_class                            GLiM `xx` at the point
  perm_prob                             CCI permafrost fraction (0..1), else NSIDC extent class
  ice_label, ground_label               CCI + NSIDC where they cover the point; warm ground (> 278 K) = no ice;
                                        NaN elsewhere (filled by earth_ml gap model, marked predicted)
  bedrock_depth_m                       NaN (optional dataset not downloaded)
Run: python -m pipeline.earth_library.build_layers
"""
import glob

import numpy as np
import pandas as pd

from ..data_fetch.fetch import ROOT
from .local_layers import load_regions

EARTH = ROOT / "data" / "earth"
EE_DIR = EARTH / "ee"
NSIDC_PROB = {"C": 0.95, "D": 0.7, "S": 0.3, "I": 0.05}
WARM_K = 278.0


def load_ee():
    frames = []
    regions = load_regions().set_index("region_id")
    for f in sorted(glob.glob(str(EE_DIR / "earth_layers_*.csv"))):
        d = pd.read_csv(f).drop(columns=["system:index", ".geo"], errors="ignore")
        if "region_id" in d:
            d["cell_id"] = d["region_id"]
            d["name"] = d["region_id"].map(regions["name"])
            d["lat"] = d["region_id"].map(regions["lat"])
            d["lon"] = d["region_id"].map(regions["lon"])
            d = d.drop(columns=["region_id"])
        else:
            d["name"] = ""
        frames.append(d)
    if not frames:
        raise SystemExit("No Earth Engine CSVs in data/earth/ee/")
    return pd.concat(frames, ignore_index=True).drop_duplicates("cell_id", keep="last").reset_index(drop=True)


def soil_thermal_inertia(sand_gkg, bdod_cgcm3):
    sand = np.asarray(sand_gkg, float) / 1000
    rho = np.asarray(bdod_cgcm3, float) * 10          # cg/cm3 -> kg/m3
    k = 0.3 + 1.5 * sand
    return np.clip(np.sqrt(k * rho * 800.0), 300, 2500)


def cci_sample(name, var, lat, lon, scale=1.0, offset=0.0):
    import h5py
    with h5py.File(EARTH / "cci_permafrost" / f"{name}.nc") as h:
        la, lo = h["lat"][:], h["lon"][:]
        a = h[var][0]
        fill = h[var].attrs.get("_FillValue", [None])[0]
        i = np.clip(np.round((lat - la[0]) / (la[1] - la[0])).astype(int), 0, len(la) - 1)
        j = np.clip(np.round((lon - lo[0]) / (lo[1] - lo[0])).astype(int), 0, len(lo) - 1)
        inside = (lat >= la.min()) & (lat <= la.max())
        v = a[i, j].astype(float)
        v[(v == fill) | ~inside] = np.nan
        return v * scale + offset


def points(df, crs=None):
    import geopandas as gpd
    g = gpd.GeoDataFrame({"i": np.arange(len(df))}, geometry=gpd.points_from_xy(df["lon"], df["lat"]), crs="EPSG:4326")
    return g.to_crs(crs) if crs else g


def glim_classes(df):
    import geopandas as gpd
    gdb = glob.glob(str(EARTH / "glim" / "*.gdb"))
    if not gdb:
        raise SystemExit("Unzip data/earth/glim/LiMW_GIS-2015.gdb.zip first")
    geo = gpd.read_file(gdb[0], columns=["xx"])
    hit = gpd.sjoin_nearest(points(df, geo.crs), geo, how="left", max_distance=50000)
    return hit.drop_duplicates("i").sort_values("i")["xx"].fillna("nd").to_numpy()


def nsidc(df):
    import geopandas as gpd
    geo = gpd.read_file(EARTH / "nsidc_ggd318" / "permaice.shp")[["COMBO", "EXTENT", "CONTENT", "geometry"]]
    hit = gpd.sjoin(points(df, geo.crs), geo, how="left", predicate="within")
    return hit.drop_duplicates("i").sort_values("i")[["COMBO", "EXTENT", "CONTENT"]].reset_index(drop=True)


def labels(df, pfr, ns):
    covered_land = ns["COMBO"].eq("ld")
    ext = ns["EXTENT"]
    perm = np.where(np.isfinite(pfr), pfr, ext.map(NSIDC_PROB).astype(float))
    perm = np.where(np.isnan(perm) & covered_land, 0.0, perm)

    ice = np.full(len(df), np.nan)
    ice[np.isfinite(pfr)] = (pfr[np.isfinite(pfr)] >= 0.5).astype(float)
    nsidc_known = np.isnan(ice) & (ext.isin(["C", "D", "I"]) | covered_land).to_numpy()
    ice[nsidc_known] = ext[nsidc_known].isin(["C", "D"]).astype(float)
    ice[np.isnan(ice) & (df["mean_temp_k"].to_numpy() > WARM_K)] = 0.0

    ground = np.where(ice == 1, "ice_cemented", np.where(ice == 0, "dry_regolith", None)).astype(object)
    massive = (ns["CONTENT"].eq("h") & ext.eq("C")).to_numpy() & (ice == 1)
    ground[massive] = "massive_ice"
    return perm, ice, ground


def build():
    ee_df = load_ee()
    lat, lon = ee_df["lat"].to_numpy(float), ee_df["lon"].to_numpy(float)
    df = pd.DataFrame({
        "cell_id": ee_df["cell_id"], "name": ee_df["name"].fillna(""), "lat": lat, "lon": lon,
        "elevation_m": ee_df["elevation_m"], "bare_fraction": ee_df["bare_fraction"],
        "mean_temp_k": ee_df["skin_temperature_mean"], "annual_range_k": ee_df["skin_temperature_range"],
        "diurnal_amp_k": (ee_df["lst_day_k"] - ee_df["lst_night_k"]) / 2,
        "thermal_inertia": soil_thermal_inertia(ee_df["sand_0_2m"], ee_df["bdod_0_2m"]),
        "aridity": ee_df["aridity"] if "aridity" in ee_df else np.nan,
        "glim_class": glim_classes(ee_df),
        "slope_deg": ee_df["slope_deg"], "roughness_m": ee_df["roughness_m"],
        "bedrock_depth_m": np.nan,
    })
    pfr = cci_sample("PFR_2021", "PFR", lat, lon, scale=0.01)
    perm, ice, ground = labels(df, pfr, nsidc(df))
    df["perm_prob"], df["ice_label"], df["ground_label"] = perm, ice, ground
    out = EARTH / "layers.parquet"
    df.to_parquet(out, index=False)
    print(df.describe(include="all").T[["count", "mean", "min", "max"]].to_string())
    print(f"labels known: ice {np.isfinite(ice).sum()}/{len(df)}; wrote {out}")
    return df


if __name__ == "__main__":
    build()
