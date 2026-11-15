"""Earth Engine export of Earth layers at region points (run by you; needs `earthengine-api`
and your EE project in .env as EARTHENGINE_PROJECT). Planetary Computer is the backup.
Exports a CSV to Drive; convert it to data/earth/layers.parquet (columns in
common/synthetic.earth_layers) once downloaded. Untested without EE access.
Step 7; plan s5, s12. Run: python -m pipeline.earth_library.ee_export
"""
import os

from .local_layers import load_regions

# bands: soil temp layer 1-4 (to ~2.9 m), skin temperature, DEM
ERA5 = "ECMWF/ERA5_LAND/MONTHLY_AGGR"
BANDS = ["soil_temperature_level_1", "soil_temperature_level_4", "skin_temperature"]


def export_regions(start="2015-01-01", end="2023-01-01", scale_m=11132):
    import ee  # imported lazily so the rest of the pipeline needs no EE install

    ee.Initialize(project=os.environ.get("EARTHENGINE_PROJECT"))
    regions = load_regions()
    pts = ee.FeatureCollection([
        ee.Feature(ee.Geometry.Point([r.lon, r.lat]), {"region_id": r.region_id})
        for r in regions.itertuples()])
    img = ee.ImageCollection(ERA5).filterDate(start, end).select(BANDS)
    stats = ee.Image.cat([img.mean().rename([b + "_mean" for b in BANDS]),
                          img.reduce(ee.Reducer.max()).subtract(img.reduce(ee.Reducer.min()))
                          .rename([b + "_range" for b in BANDS])])
    out = stats.reduceRegions(pts, ee.Reducer.first(), scale_m)
    task = ee.batch.Export.table.toDrive(out, description="earth_layers_regions", fileFormat="CSV")
    task.start()
    return task


if __name__ == "__main__":
    print(export_regions())
