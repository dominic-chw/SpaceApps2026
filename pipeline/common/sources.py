"""Pluggable source layers: one loader per variable, chosen in config.yaml `processing.sources`.

Each variable (e.g. earth `soil_temp`) provides a fixed set of layer columns (VARIABLES below, the same
columns as common/synthetic.py). Its config entry says where the values come from:

    {source: synthetic}                                    built-in synthetic values (default)
    {source: file,   path: data/earth/soil_temp.csv}       table with cell_id (or lat/lon) + the columns;
                                                           optional `columns: {file_col: layer_col}`
    {source: ee,     id: "ECMWF/ERA5_LAND/MONTHLY_AGGR", bands: [...], path: data/earth/ee/soil_temp.csv}
                                                           Earth Engine: reduced to the grid server-side by
                                                           export_ee() (one small table per variable), then
                                                           read like `file`
    {source: raster, path: data/mars/x.tif, band: 1, reducer: mean|median|mode, scale: 1, offset: 0,
                     nodata: null, lon_360: false}         local raster reduced to the grid cells

Every loader returns the same thing: a DataFrame of cell_id, lat, lon + its columns.
Switching a variable from synthetic to real = one config line (+ a small loader if the format is new).
`cells` is a variable too: which grid cells exist (Mars 2 deg grid; Earth regions + grid / samples).

assemble(planet) -> layers DataFrame, or None when every variable is synthetic (callers then use the
synthetic tables directly, unchanged behaviour). io.read_layers() calls this.
"""
import os
from pathlib import Path

import numpy as np
import pandas as pd

from .config import get_processing, load_config

ROOT = Path(__file__).resolve().parents[2]

VARIABLES = {
    "mars": {
        "cells": ["cell_id", "lat", "lon"],
        "thermal_inertia": ["thermal_inertia"],
        "ice": ["ice_frac"],
        "geology": ["unit"],
        "terrain": ["slope_deg", "roughness_m"],
        "water_vapour": ["water_vapour_pr_um"],
    },
    "earth": {
        "cells": ["cell_id", "name", "lat", "lon"],
        "elevation": ["elevation_m"],
        "terrain": ["slope_deg", "roughness_m"],
        "land_cover": ["bare_fraction"],
        "soil_temp": ["mean_temp_k", "annual_range_k"],
        "surface_temp": ["diurnal_amp_k"],
        "soil_properties": ["thermal_inertia"],
        "aridity": ["aridity"],
        "permafrost": ["perm_prob"],
        "lithology": ["glim_class"],
        "bedrock": ["bedrock_depth_m"],
        "labels": ["ice_label", "ground_label"],
    },
}
KEY = ["cell_id", "lat", "lon"]


def earth_grid_deg(params=None):
    """Earth grid spacing: config top-level `earth_grid_deg` (plan s9 step 7), overridable via params."""
    return (params or {}).get("earth_grid_deg") or load_config().get("earth_grid_deg", 0.5)


def source_specs(planet, params=None):
    """{variable: spec} with every variable present (missing ones default to synthetic)."""
    p = params or get_processing()
    if os.environ.get("SPACEAPPS_SOURCES") == "synthetic" and not (params or {}).get("sources_force"):
        return {v: {"source": "synthetic"} for v in VARIABLES[planet]}   # tests / runs without data/
    cfg = (p.get("sources") or {}).get(planet) or {}
    unknown = set(cfg) - set(VARIABLES[planet])
    if unknown:
        raise ValueError(f"unknown {planet} source variables in config: {sorted(unknown)}")
    return {v: dict(cfg.get(v) or {"source": "synthetic"}) for v in VARIABLES[planet]}


def all_synthetic(planet, params=None):
    return all(s.get("source", "synthetic") == "synthetic" for s in source_specs(planet, params).values())


# ---------------------------------------------------------------- loaders
def _synthetic_table(planet, params):
    from .synthetic import earth_layers, mars_layers
    if planet == "mars":
        return mars_layers(params)
    from ..earth_library.local_layers import load_regions
    return earth_layers(load_regions(), params=params)


def load_synthetic(spec, columns, cells, planet, params):
    t = _synthetic_table(planet, params)
    if cells is None:
        return t[[c for c in t.columns if c in KEY + columns]]
    return cells[KEY].merge(t[["cell_id"] + [c for c in columns if c not in KEY]], on="cell_id", how="left")


def _read_table(path):
    path = ROOT / path if not Path(path).is_absolute() else Path(path)
    if not path.exists():
        raise FileNotFoundError(f"source file not found: {path}")
    return pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)


def nearest_join(cells, table, columns):
    """Values from `table` (lat, lon, columns) at the nearest table point to each cell (great-circle-ish)."""
    lat, lon = np.radians(table["lat"].to_numpy(float)), np.radians(table["lon"].to_numpy(float))
    xyz = np.stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)], 1)
    clat, clon = np.radians(cells["lat"].to_numpy(float)), np.radians(cells["lon"].to_numpy(float))
    cxyz = np.stack([np.cos(clat) * np.cos(clon), np.cos(clat) * np.sin(clon), np.sin(clat)], 1)
    idx = np.argmax(cxyz @ xyz.T, axis=1)        # fine for grid-sized tables (cells x points floats)
    out = cells[KEY].copy()
    for c in columns:
        out[c] = table[c].to_numpy()[idx]
    return out


def load_file(spec, columns, cells, planet, params):
    t = _read_table(spec["path"]).rename(columns=spec.get("columns") or {})
    if "lon" in t and spec.get("lon_360"):
        t["lon"] = ((t["lon"] + 180) % 360) - 180
    missing = [c for c in columns if c not in t]
    if missing:
        raise ValueError(f"{spec['path']} lacks columns {missing}")
    if cells is None:                                # this is the `cells` variable itself
        cols = list(dict.fromkeys(c for c in KEY + columns if c in t))     # unique, keep order
        return t[cols].drop_duplicates("cell_id")
    if "cell_id" in t and spec.get("match", "cell_id") == "cell_id":
        vals = t[["cell_id"] + [c for c in columns if c not in KEY]].drop_duplicates("cell_id")
        return cells[KEY].merge(vals, on="cell_id", how="left")
    return nearest_join(cells, t, [c for c in columns if c not in KEY])


def grid_reduce(array, west, north, dx, dy, cells, half_deg, reducer="mean", nodata=None):
    """Reduce a lat/lon raster (row 0 = north edge) to cell boxes of +-half_deg. Pure numpy; tested on
    tiny arrays. Returns one value per cell (NaN where no valid pixel)."""
    a = np.asarray(array, float).copy()
    if nodata is not None:
        a[a == nodata] = np.nan
    out = np.full(len(cells), np.nan)
    for k, (lat, lon) in enumerate(zip(cells["lat"].to_numpy(float), cells["lon"].to_numpy(float))):
        r0, r1 = int(np.floor((north - (lat + half_deg)) / dy)), int(np.ceil((north - (lat - half_deg)) / dy))
        c0, c1 = int(np.floor((lon - half_deg - west) / dx)), int(np.ceil((lon + half_deg - west) / dx))
        w = a[max(r0, 0):max(r1, 0), max(c0, 0):max(c1, 0)]
        w = w[np.isfinite(w)]
        if w.size == 0:
            continue
        if reducer == "mean":
            out[k] = w.mean()
        elif reducer == "median":
            out[k] = np.median(w)
        elif reducer == "mode":
            vals, counts = np.unique(w, return_counts=True)
            out[k] = vals[np.argmax(counts)]
        else:
            raise ValueError(f"unknown reducer {reducer}")
    return out


def load_raster(spec, columns, cells, planet, params):
    import rasterio
    if len(columns) != 1:
        raise ValueError("raster source provides one column; give one raster per column")
    path = ROOT / spec["path"]
    with rasterio.open(path) as src:
        a = src.read(spec.get("band", 1)).astype(float)
        t = src.transform
        nodata = spec.get("nodata", src.nodata)
        if src.crs is not None and src.crs.is_projected:      # e.g. Mars equirectangular in metres
            m_per_deg = (spec.get("radius_m") or src.crs.to_dict().get("R") or 3396190.0) * np.pi / 180
            west, north, dx, dy = t.c / m_per_deg, t.f / m_per_deg, t.a / m_per_deg, -t.e / m_per_deg
        else:
            west, north, dx, dy = t.c, t.f, t.a, -t.e
    if spec.get("lon_360"):
        cells = cells.assign(lon=cells["lon"] % 360)
    half = params.get("grid_deg", 2) / 2 if planet == "mars" else earth_grid_deg(params) / 2
    v = grid_reduce(a, west, north, dx, dy, cells, half, spec.get("reducer", "mean"), nodata)
    out = cells[KEY].copy() if not spec.get("lon_360") else cells[KEY].assign(lon=((cells["lon"] + 180) % 360) - 180)
    out[columns[0]] = v * spec.get("scale", 1) + spec.get("offset", 0)
    return out


def load_ee(spec, columns, cells, planet, params):
    """Read the per-variable table that export_ee() wrote (Drive -> data/<planet>/ee/<variable>.csv)."""
    return load_file({**spec, "path": spec["path"]}, columns, cells, planet, params)


def load_grid(spec, columns, cells, planet, params):
    """`cells` source: every cell centre of a regular grid (Earth adds the named regions)."""
    from .coords import cell_id, grid_cells
    deg = spec.get("deg", earth_grid_deg(params) if planet == "earth" else params["grid_deg"])
    lim = spec.get("lat_limit", 85 if planet == "earth" else params["lat_limit"])
    if planet == "mars":
        cs = grid_cells({**params, "grid_deg": deg, "lat_limit": lim})
        return pd.DataFrame({"cell_id": [cell_id(a, b, params) for a, b in cs],
                             "lat": [c[0] for c in cs], "lon": [c[1] for c in cs]})
    lats = np.arange(-lim + deg / 2, lim, deg)
    lons = np.arange(-180 + deg / 2, 180, deg)
    la, lo = np.meshgrid(lats, lons, indexing="ij")
    g = pd.DataFrame({"lat": la.ravel().round(3), "lon": lo.ravel().round(3)})
    g["cell_id"] = [f"{a:+07.2f}_{b:+07.2f}" for a, b in zip(g.lat, g.lon)]
    g["name"] = ""
    return g


LOADERS = {"synthetic": load_synthetic, "file": load_file, "raster": load_raster, "ee": load_ee,
           "grid": load_grid}


def _cells(planet, spec, params):
    src = spec.get("source", "synthetic")
    if src == "grid":
        cells = load_grid(spec, VARIABLES[planet]["cells"], None, planet, params)
    else:
        cells = LOADERS[src](spec, VARIABLES[planet]["cells"], None, planet, params)
    if planet == "earth":
        if "name" not in cells:
            cells["name"] = ""
        if spec.get("include_regions", True):
            from ..earth_library.local_layers import load_regions
            reg = load_regions().rename(columns={"region_id": "cell_id"})[["cell_id", "name", "lat", "lon"]]
            cells = pd.concat([reg, cells[~cells["cell_id"].isin(reg["cell_id"])]], ignore_index=True)
        cells["name"] = cells["name"].fillna("")
    return cells.reset_index(drop=True)


def assemble(planet, params=None):
    """Layers table for `planet` from the configured sources, or None if all are synthetic."""
    p = params or get_processing()
    if all_synthetic(planet, p):
        return None
    specs = source_specs(planet, p)
    cells = _cells(planet, specs["cells"], p)
    out = cells.copy()
    for var, spec in specs.items():
        if var == "cells":
            continue
        cols = VARIABLES[planet][var]
        src = spec.get("source", "synthetic")
        if src not in LOADERS:
            raise ValueError(f"{planet}.{var}: unknown source '{src}' (use {sorted(LOADERS)})")
        t = LOADERS[src](spec, cols, cells, planet, p)
        out = out.merge(t[["cell_id"] + cols], on="cell_id", how="left")
        empty = [c for c in cols if out[c].isna().all()]
        if empty:
            print(f"WARNING: {planet}.{var} ({src}) gave no values for {empty}")
    return out


# ---------------------------------------------------------------- Earth Engine export (one table per variable)
def ee_reducer(ee, name):
    return {"mean": ee.Reducer.mean(), "median": ee.Reducer.median(), "mode": ee.Reducer.mode()}[name]


TEMPORAL = ("mean", "range", "max", "min")


def ee_image(ee, spec):
    """Server-side image for an ee spec. Plain: mean over time of `bands` (x `multiply`).
    With `outputs: {column: {band, temporal: mean|range|max|min, multiply}}` one band per layer column,
    named after the column (so the exported table needs no renaming)."""
    def base(band):
        if not spec.get("collection", True):
            return None, ee.Image(spec["id"]).select(band)
        col = ee.ImageCollection(spec["id"]).select(band)
        if spec.get("start"):
            col = col.filterDate(spec["start"], spec["end"])
        return col, None

    if not spec.get("outputs"):
        col, img = base(spec["bands"])
        img = col.mean() if col is not None else img
        return img.multiply(spec["multiply"]) if spec.get("multiply") else img
    parts = []
    for name, o in spec["outputs"].items():
        t = o.get("temporal", "mean")
        if t not in TEMPORAL:
            raise ValueError(f"{name}: temporal must be one of {TEMPORAL}")
        col, img = base(o["band"])
        if col is not None:
            img = {"mean": col.mean(), "max": col.max(), "min": col.min(),
                   "range": col.max().subtract(col.min())}[t]
        if o.get("multiply"):
            img = img.multiply(o["multiply"])
        parts.append(img.rename(name))
    return ee.Image.cat(parts)


def export_ee(variable, planet="earth", params=None, limit=None, region_ids=None, dry_run=False):
    """Reduce one EE dataset to the grid cells server-side and export ONE small table (cell_id + bands)
    to Drive folder `spaceapps_ee` as <variable>.csv. Use `limit` / `region_ids` for a tiny test slice first.
    Spec keys: id, bands, reducer (mean), scale_m (5000), collection (true), start, end, multiply."""
    p = params or get_processing()
    spec = source_specs(planet, p)[variable]
    if spec.get("source") != "ee":
        raise ValueError(f"{planet}.{variable} is not an ee source in config")
    cells = _cells(planet, source_specs(planet, p)["cells"], p)
    if region_ids:
        cells = cells[cells["cell_id"].isin(region_ids)]
    if limit:
        cells = cells.head(limit)
    print(f"{planet}.{variable}: {spec['id']} {spec.get('bands')} over {len(cells)} cells")
    if dry_run:
        return cells
    from ..earth_library.ee_export import init_ee
    ee = init_ee()
    img = ee_image(ee, spec)
    h = earth_grid_deg(p) / 2
    feats = [ee.Feature(ee.Geometry.Rectangle([r.lon - h, r.lat - h, r.lon + h, r.lat + h], "EPSG:4326", False),
                        {"cell_id": r.cell_id, "lat": r.lat, "lon": r.lon}) for r in cells.itertuples()]
    out = img.reduceRegions(ee.FeatureCollection(feats), ee_reducer(ee, spec.get("reducer", "mean")),
                            spec.get("scale_m", 5000), tileScale=8)
    names = list(spec["outputs"]) if spec.get("outputs") else list(spec["bands"])
    task = ee.batch.Export.table.toDrive(out, description=variable, folder="spaceapps_ee", fileFormat="CSV",
                                         selectors=["cell_id", "lat", "lon"] + names)
    task.start()
    print("started", task.id)
    return task


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Export one Earth Engine variable to the grid (small table).")
    ap.add_argument("variable")
    ap.add_argument("--limit", type=int, help="first N cells only (test slice)")
    ap.add_argument("--regions", nargs="*", help="only these region_ids (test slice)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    export_ee(a.variable, limit=a.limit, region_ids=a.regions, dry_run=a.dry_run)
