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
DEM = "COPERNICUS/DEM/GLO30"
WORLDCOVER = "ESA/WorldCover/v200"          # class 60 = bare / sparse vegetation
SOILGRIDS = ["projects/soilgrids-isric/sand_mean", "projects/soilgrids-isric/clay_mean",
             "projects/soilgrids-isric/bdod_mean"]
SOIL_DEPTHS_CM = [(0, 5), (5, 15), (15, 30), (30, 60), (60, 100), (100, 200)]   # to 2 m
FOLDER = "spaceapps_ee"
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
    dem = ee.ImageCollection(DEM).select("DEM").mosaic().setDefaultProjection("EPSG:4326", None, 30)
    slope = ee.Terrain.slope(dem).rename("slope_deg")
    rough = dem.reduceNeighborhood(ee.Reducer.stdDev(), ee.Kernel.square(250, "meters")).rename("roughness_m")
    return dem.rename("elevation_m").addBands(slope).addBands(rough)


def worldcover_bare(ee):
    wc = ee.Image(WORLDCOVER).select("Map")
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
                              terrain_stats(ee), worldcover_bare(ee), soilgrids_0_2m(ee)])
        out = stack.reduceRegions(region_points(ee, reg), ee.Reducer.mean(), 1000)
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
    bare = worldcover_bare(ee).reproject("EPSG:4326", None, 100)
    frac = bare.reduceResolution(ee.Reducer.mean(), maxPixels=65536).reproject(
        crs="EPSG:4326", crsTransform=[grid_deg, 0, -180, 0, -grid_deg, 85])
    region = ee.Geometry.Rectangle([-180, -85, 180, 85], "EPSG:4326", False)
    task = ee.batch.Export.image.toDrive(frac, description="worldcover_bare_grid", folder=FOLDER,
                                         region=region, crs="EPSG:4326",
                                         crsTransform=[grid_deg, 0, -180, 0, -grid_deg, 85])
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


def export_grid_samples(tif_path, grid_deg=0.5, chunk=5000, start="2015-01-01", end="2023-01-01", dry_run=False):
    """Layers for every bare-ground 0.5 deg cell (cell mean at 5 km), chunked into several Drive CSVs.
    Run after export_bare_grid finished and its GeoTIFF is in data/earth/ee/."""
    cells = bare_cells(tif_path, grid_deg)
    print(f"grid samples: {len(cells)} bare cells, {-(-len(cells) // chunk)} chunks of {chunk}")
    if dry_run:
        return []
    ee = init_ee()
    stack = ee.Image.cat([era5_stats(ee, start, end), modis_stats(ee, start, end), terrain_stats(ee),
                          worldcover_bare(ee), soilgrids_0_2m(ee)])
    h = grid_deg / 2
    tasks = []
    for i in range(0, len(cells), chunk):
        feats = [ee.Feature(ee.Geometry.Rectangle([lon - h, lat - h, lon + h, lat + h], "EPSG:4326", False),
                            {"cell_id": f"{lat:+07.2f}_{lon:+07.2f}", "lat": lat, "lon": lon})
                 for lat, lon in cells[i:i + chunk]]
        out = stack.reduceRegions(ee.FeatureCollection(feats), ee.Reducer.mean(), 5000)
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
    ap.add_argument("--grid-from", help="path to worldcover_bare_grid.tif; exports layers for its bare cells")
    a = ap.parse_args()
    if a.grid_from:
        export_grid_samples(a.grid_from, dry_run=a.dry_run)
    else:
        export_regions(dry_run=a.dry_run, only=a.only)
        if not a.only:
            export_bare_grid(dry_run=a.dry_run)
