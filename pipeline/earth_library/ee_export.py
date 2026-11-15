"""Earth Engine export of Earth layers (items 7-11 of data_sources.yaml) at the region points,
plus the WorldCover bare-ground fraction on the global grid.
Project id: EE_PROJECT in .env (EARTHENGINE_PROJECT also accepted). Needs a one-time
`earthengine authenticate` (credentials in ~/.config/earthengine). Planetary Computer is the backup.
Exports go to Google Drive folder `spaceapps_ee` as CSV/GeoTIFF; copy them into data/earth/ee/.
Atacama regions run first, then all other regions, then the bare grid.
Step 7; plan s5, s9, s12. Run: python -m pipeline.earth_library.ee_export [--dry-run] [--only atacama]
"""
import argparse
import os

from ..data_fetch.fetch import load_env
from .local_layers import load_regions

ERA5 = "ECMWF/ERA5_LAND/MONTHLY_AGGR"
ERA5_BANDS = ["soil_temperature_level_1", "soil_temperature_level_2", "soil_temperature_level_3",
              "soil_temperature_level_4", "skin_temperature"]
MODIS = "MODIS/061/MOD11A2"
DEM = "COPERNICUS/DEM/GLO30_2024_1"     # GLO30 is deprecated on EE
WORLDCOVER = "ESA/WorldCover/v200"          # class 60 = bare / sparse vegetation
SOILGRIDS = ["projects/soilgrids-isric/sand_mean", "projects/soilgrids-isric/clay_mean",
             "projects/soilgrids-isric/bdod_mean"]
SOIL_DEPTHS_CM = [(0, 5), (5, 15), (15, 30), (30, 60), (60, 100), (100, 200)]   # to 2 m
ARIDITY = "projects/sat-io/open-datasets/global_ai/global_ai_yearly"   # AI x 10000 (community catalog)
TERRAIN_M = 500                              # config processing.terrain_resolution_m
FOLDER = "spaceapps_ee"
from pathlib import Path
ROOT_EE = Path(__file__).resolve().parents[2] / "data" / "earth" / "ee"
POINT_BUFFER_M = 2500


def init_ee():
    import ee  # lazy: the rest of the pipeline needs no EE install
    load_env()
    project = os.environ.get("EE_PROJECT") or os.environ.get("EARTHENGINE_PROJECT")
    if not project:
        raise SystemExit("Set EE_PROJECT=<your earth engine project id> in .env")
    ee.Initialize(project=project)
    return ee


def era5_stats(ee, start, end):
    img = ee.ImageCollection(ERA5).filterDate(start, end).select(ERA5_BANDS)
    mean = img.mean().rename([b + "_mean" for b in ERA5_BANDS])
    rng = img.max().subtract(img.min()).rename([b + "_range" for b in ERA5_BANDS])
    return mean.addBands(rng)


def modis_stats(ee, start, end):
    col = ee.ImageCollection(MODIS).filterDate(start, end).select(["LST_Day_1km", "LST_Night_1km"])
    k = 0.02   # scale factor -> kelvin
    return col.mean().multiply(k).rename(["lst_day_k", "lst_night_k"])


def terrain_stats(ee):
    """Elevation, slope and roughness at ~500 m, the same scale as the Mars MOLA 463 m DEM."""
    col = ee.ImageCollection(DEM).select("DEM")
    dem30 = col.mosaic().setDefaultProjection(col.first().projection())
    dem = dem30.reduceResolution(ee.Reducer.mean(), maxPixels=1024).reproject("EPSG:4326", None, TERRAIN_M)
    slope = ee.Terrain.slope(dem).rename("slope_deg")
    # std dev of elevation in a 3 x 3 window of 500 m pixels (Mars: same on the 463 m MOLA grid)
    rough = dem.reduceNeighborhood(ee.Reducer.stdDev(), ee.Kernel.square(1, "pixels")).rename("roughness_m")
    return dem.rename("elevation_m").addBands(slope).addBands(rough)


def aridity(ee):
    return ee.Image(ARIDITY).select("b1").divide(10000).rename("aridity")


def worldcover_bare(ee):
    wc = ee.ImageCollection(WORLDCOVER).first().select("Map")
    return wc.eq(60).rename("bare_fraction")


def soilgrids_0_2m(ee):
    out = []
    for asset in SOILGRIDS:
        name = asset.split("/")[-1].replace("_mean", "")
        bands = [f"{name}_{a}-{b}cm_mean" for a, b in SOIL_DEPTHS_CM]
        weights = [b - a for a, b in SOIL_DEPTHS_CM]
        img = ee.Image(asset).select(bands)
        wsum = sum(weights)
        parts = [img.select(b).multiply(w) for b, w in zip(bands, weights)]
        total = parts[0]
        for p in parts[1:]:
            total = total.add(p)
        out.append(total.divide(wsum).rename(name + "_0_2m"))
    return ee.Image.cat(out)


def region_points(ee, regions):
    return ee.FeatureCollection([
        ee.Feature(ee.Geometry.Point([r.lon, r.lat]).buffer(POINT_BUFFER_M), {"region_id": r.region_id})
        for r in regions.itertuples()])


def export_regions(start="2015-01-01", end="2023-01-01", dry_run=False, only=None):
    ee = init_ee() if not dry_run else None
    regions = load_regions()
    atacama = regions[regions.region_id.str.startswith("atacama")]
    others = regions[~regions.region_id.str.startswith("atacama")]
    batches = [("atacama", atacama)] + ([] if only == "atacama" else [("all_other_regions", others)])
    tasks = []
    for label, reg in batches:
        print(f"{label}: {', '.join(reg.region_id)}")
        if dry_run:
            continue
        stack = ee.Image.cat([era5_stats(ee, start, end), modis_stats(ee, start, end),
                              terrain_stats(ee), worldcover_bare(ee), soilgrids_0_2m(ee), aridity(ee)])
        out = stack.reduceRegions(region_points(ee, reg), ee.Reducer.mean(), 1000, tileScale=8)
        task = ee.batch.Export.table.toDrive(out, description=f"earth_layers_{label}",
                                             folder=FOLDER, fileFormat="CSV")
        task.start()
        tasks.append(task)
        print("started", task.id)
    return tasks


def export_bare_grid(grid_deg=0.5, dry_run=False):
    """WorldCover bare fraction per grid cell (global, lat +-85) as a GeoTIFF for the filtered grid."""
    print(f"bare_grid: {grid_deg} deg, lat +-85")
    if dry_run:
        return None
    ee = init_ee()
    # two-step mean: 10 m -> 500 m (50 x 50 px per cell), then 500 m -> grid_deg (~110 x 110 px per cell)
    bare = worldcover_bare(ee)
    bare_500 = bare.reduceResolution(ee.Reducer.mean(), maxPixels=4096).reproject("EPSG:4326", None, 500)
    frac = bare_500.reduceResolution(ee.Reducer.mean(), maxPixels=65536).reproject(
        crs="EPSG:4326", crsTransform=[grid_deg, 0, -180, 0, -grid_deg, 85])
    region = ee.Geometry.Rectangle([-180, -85, 180, 85], "EPSG:4326", False)
    task = ee.batch.Export.image.toDrive(frac, description="worldcover_bare_grid", folder=FOLDER,
                                         region=region, crs="EPSG:4326",
                                         crsTransform=[grid_deg, 0, -180, 0, -grid_deg, 85],
                                         maxPixels=1e9, shardSize=4, fileDimensions=[720, 340])
    task.start()
    print("started", task.id)
    return task


def bare_cells(tif_path, grid_deg=0.5, min_fraction=None):
    """Cell centres (lat, lon) whose WorldCover bare fraction is >= min_fraction, from the exported GeoTIFF."""
    import numpy as np
    import rasterio

    from ..common.config import get_processing
    min_fraction = get_processing()["bare_ground_min_fraction"] if min_fraction is None else min_fraction
    with rasterio.open(tif_path) as src:
        a = src.read(1)
        t = src.transform
    rows, cols = np.where(a >= min_fraction)
    return [(t.f + (r + 0.5) * t.e, t.c + (c + 0.5) * t.a) for r, c in zip(rows, cols)]


def land_cells(grid_deg=0.5, min_lat=-60, max_lat=85):
    """Cell centres of 0.5 deg land cells (ERA5-Land mask, saved by `--land-mask`) between min_lat and max_lat.
    WorldCover has no Antarctica, so cells south of 60 S are skipped."""
    import numpy as np
    a = np.load(ROOT_EE / "land_mask_0p5.npy")              # 0.5 deg mask, rows from 85 N
    k = int(round(grid_deg / 0.5))
    if k > 1:                                                 # coarser grid: land if any 0.5 deg sub-cell is land
        a = (a > 0.5)[: a.shape[0] // k * k, : a.shape[1] // k * k]
        a = a.reshape(a.shape[0] // k, k, a.shape[1] // k, k).mean(axis=(1, 3)) * 2
    rows, cols = np.where(a > 0.5)
    lat = 85 - (rows + 0.5) * grid_deg
    lon = -180 + (cols + 0.5) * grid_deg
    keep = (lat >= min_lat) & (lat <= max_lat)
    return list(zip(lat[keep].round(3), lon[keep].round(3)))


def save_land_mask(grid_deg=0.5):
    import numpy as np
    ee = init_ee()
    img = ee.ImageCollection(ERA5).first().select("skin_temperature")
    land = img.mask().gt(0).unmask(0).reduceResolution(ee.Reducer.mean(), maxPixels=1024).reproject(
        crs="EPSG:4326", crsTransform=[grid_deg, 0, -180, 0, -grid_deg, 85])
    region = ee.Geometry.Rectangle([-180, -85, 180, 85], "EPSG:4326", False)
    a = np.array(land.rename("land").sampleRectangle(region=region, defaultValue=0).get("land").getInfo())
    ROOT_EE.mkdir(parents=True, exist_ok=True)
    np.save(ROOT_EE / "land_mask_0p5.npy", a)
    print("land cells", int((a > 0.5).sum()))


def export_land_grid(grid_deg=0.5, chunk=5000, start="2015-01-01", end="2023-01-01", dry_run=False):
    """Layers (incl. sampled WorldCover bare_fraction) for every land cell; build_grid applies the bare filter.
    Faster than waiting for the global bare-fraction GeoTIFF."""
    cells = land_cells(grid_deg)
    print(f"land grid: {len(cells)} cells, {-(-len(cells) // chunk)} chunks of {chunk}")
    if dry_run:
        return []
    ee = init_ee()
    stack = ee.Image.cat([era5_stats(ee, start, end), modis_stats(ee, start, end), terrain_stats(ee),
                          worldcover_bare(ee), soilgrids_0_2m(ee), aridity(ee)])
    h = grid_deg / 2
    tasks = []
    for i in range(0, len(cells), chunk):
        feats = [ee.Feature(ee.Geometry.Rectangle([lon - h, lat - h, lon + h, lat + h], "EPSG:4326", False),
                            {"cell_id": f"{lat:+07.2f}_{lon:+07.2f}", "lat": lat, "lon": lon})
                 for lat, lon in cells[i:i + chunk]]
        out = stack.reduceRegions(ee.FeatureCollection(feats), ee.Reducer.mean(), 5000, tileScale=8)
        task = ee.batch.Export.table.toDrive(out, description=f"earth_layers_grid_{i // chunk:03d}",
                                             folder=FOLDER, fileFormat="CSV")
        task.start()
        tasks.append(task)
        print("started", task.id)
    return tasks


def fetch_land_grid(grid_deg=2.0, chunk=25, workers=8, min_bare=0.6, start="2015-01-01", end="2023-01-01"):
    """Same layers as export_land_grid, fetched directly (no Drive round trip) into
    data/earth/ee/earth_layers_grid_<deg>deg.csv. For coarse grids (1-2 deg): candidate Earth sites
    outside the named analog regions (they become new_site in the matcher).
    Pass 1 samples only the WorldCover bare fraction (fast) and keeps cells >= min_bare (build_grid
    applies the stricter config filter later); pass 2 fetches the full stack in parallel. Resumable."""
    import time
    from concurrent.futures import ThreadPoolExecutor

    import pandas as pd
    cid = lambda la, lo: f"{la:+07.2f}_{lo:+07.2f}"
    cells = land_cells(grid_deg)
    out_path = ROOT_EE / f"earth_layers_grid_{str(grid_deg).replace('.', 'p')}deg.csv"
    done = pd.read_csv(out_path) if out_path.exists() else pd.DataFrame()
    have = set(done["cell_id"]) if len(done) else set()
    ee = init_ee()
    h = min(grid_deg, 0.5) / 2               # sample the central 0.5 deg of each cell
    box = lambda la, lo: ee.Geometry.Rectangle([lo - h, la - h, lo + h, la + h], "EPSG:4326", False)

    bare = {}
    for i in range(0, len(cells), 500):
        feats = [ee.Feature(box(la, lo), {"cell_id": cid(la, lo)}) for la, lo in cells[i:i + 500]]
        fc = worldcover_bare(ee).reduceRegions(ee.FeatureCollection(feats), ee.Reducer.mean(), 2000, tileScale=4)
        bare.update({f["properties"]["cell_id"]: f["properties"].get("mean") or 0 for f in fc.getInfo()["features"]})
    todo = [(la, lo) for la, lo in cells if bare.get(cid(la, lo), 0) >= min_bare and cid(la, lo) not in have]
    print(f"land grid {grid_deg} deg: {len(cells)} land cells, {sum(v >= min_bare for v in bare.values())} bare, "
          f"{len(todo)} to fetch")

    stack = ee.Image.cat([era5_stats(ee, start, end), modis_stats(ee, start, end), terrain_stats(ee),
                          worldcover_bare(ee), soilgrids_0_2m(ee), aridity(ee)])

    def fetch(part):
        feats = [ee.Feature(box(la, lo), {"cell_id": cid(la, lo), "lat": float(la), "lon": float(lo)}) for la, lo in part]
        for attempt in range(3):
            try:
                fc = stack.reduceRegions(ee.FeatureCollection(feats), ee.Reducer.mean(), 5000, tileScale=8)
                return pd.DataFrame([f["properties"] for f in fc.getInfo()["features"]])
            except Exception as err:                       # EE rate limits / transient errors
                print("  retry:", str(err)[:120]); time.sleep(5 * (attempt + 1))
        return pd.DataFrame()

    frames = [done] if len(done) else []
    parts = [todo[i:i + chunk] for i in range(0, len(todo), chunk)]
    t0, n = time.time(), 0
    with ThreadPoolExecutor(workers) as pool:
        for df in pool.map(fetch, parts):
            frames.append(df); n += len(df)
            pd.concat(frames, ignore_index=True).to_csv(out_path, index=False)
            print(f"  {n}/{len(todo)} ({time.time() - t0:.0f}s)")
    return out_path


def export_grid_samples(tif_path, grid_deg=0.5, chunk=5000, start="2015-01-01", end="2023-01-01", dry_run=False):
    """Layers for every bare-ground 0.5 deg cell (cell mean at 5 km), chunked into several Drive CSVs.
    Run after export_bare_grid finished and its GeoTIFF is in data/earth/ee/."""
    cells = bare_cells(tif_path, grid_deg)
    print(f"grid samples: {len(cells)} bare cells, {-(-len(cells) // chunk)} chunks of {chunk}")
    if dry_run:
        return []
    ee = init_ee()
    stack = ee.Image.cat([era5_stats(ee, start, end), modis_stats(ee, start, end), terrain_stats(ee),
                          worldcover_bare(ee), soilgrids_0_2m(ee), aridity(ee)])
    h = grid_deg / 2
    tasks = []
    for i in range(0, len(cells), chunk):
        feats = [ee.Feature(ee.Geometry.Rectangle([lon - h, lat - h, lon + h, lat + h], "EPSG:4326", False),
                            {"cell_id": f"{lat:+07.2f}_{lon:+07.2f}", "lat": lat, "lon": lon})
                 for lat, lon in cells[i:i + chunk]]
        out = stack.reduceRegions(ee.FeatureCollection(feats), ee.Reducer.mean(), 5000, tileScale=8)
        task = ee.batch.Export.table.toDrive(out, description=f"earth_layers_grid_{i // chunk:03d}",
                                             folder=FOLDER, fileFormat="CSV")
        task.start()
        tasks.append(task)
        print("started", task.id)
    return tasks


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", choices=["atacama"])
    ap.add_argument("--bare-grid-only", action="store_true")
    ap.add_argument("--land-mask", action="store_true", help="save the 0.5 deg ERA5-Land mask")
    ap.add_argument("--land-grid", action="store_true", help="export layers for every land cell")
    ap.add_argument("--grid-deg", type=float, default=0.5)
    ap.add_argument("--fetch-grid", action="store_true", help="fetch layers for bare land cells directly (no Drive)")
    ap.add_argument("--grid-from", help="path to worldcover_bare_grid.tif; exports layers for its bare cells")
    a = ap.parse_args()
    if a.land_mask:
        save_land_mask()
    elif a.fetch_grid:
        print(fetch_land_grid(grid_deg=a.grid_deg))
    elif a.land_grid:
        export_land_grid(grid_deg=a.grid_deg, dry_run=a.dry_run)
    elif a.bare_grid_only:
        export_bare_grid(dry_run=a.dry_run)
    elif a.grid_from:
        export_grid_samples(a.grid_from, dry_run=a.dry_run)
    else:
        export_regions(dry_run=a.dry_run, only=a.only)
        if not a.only:
            export_bare_grid(dry_run=a.dry_run)
