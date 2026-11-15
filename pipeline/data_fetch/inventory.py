"""Inventory check: opens every downloaded file and writes data/INVENTORY.md
(present/missing, opens or not, CRS, longitude convention, resolution, range, nodata, feature fed).
Run: python -m pipeline.data_fetch.inventory
"""
import re
import zipfile
from datetime import date
from pathlib import Path

import numpy as np

from .fetch import ROOT, load_sources

OUT = ROOT / "data" / "INVENTORY.md"
# Expected properties for sources that live in Earth Engine or are not on disk yet (not measured here).
EXPECTED = {
    "era5_land_monthly": ("WGS84 (EPSG:4326)", "-180..180 east", "0.1 deg (~11 km)", "K, roughly 200-330", "none (masked ocean)"),
    "modis_lst": ("sinusoidal, 1 km", "-180..180 east", "1 km", "raw DN x 0.02 = K, 7500-65535", "0"),
    "copernicus_dem": ("WGS84", "-180..180 east", "30 m", "m, about -400..8800", "none"),
    "esa_worldcover": ("WGS84", "-180..180 east", "10 m", "classes 10-100 (60 = bare)", "none"),
    "soilgrids": ("Homolosine in files; WGS84 in EE", "-180..180 east", "250 m", "sand/clay g/kg, bdod cg/cm3", "-32768 / masked"),
    "glim": ("WGS84 polygons (file geodatabase)", "-180..180 east", "polygon map, ~1:3.75M", "lithology classes", "none"),
    "nsidc_circumarctic_ground_ice": ("EASE-Grid / polar stereographic (check on download)", "n/a", "~12.5 km (shapefile map)", "ground-ice and permafrost classes", "n/a"),
    "global_aridity_index": ("WGS84", "-180..180 east", "30 arc-sec (~1 km)", "AI x 10000 (0..~65000)", "-9999"),
}


def label_val(text, key):
    m = re.search(rf"^\s*{key}\s*=\s*\"?([^\n\"<]+)", text, re.M | re.I)
    return m.group(1).strip() if m else None


def read_img(img, lbl, dtype):
    t = Path(lbl).read_text(errors="ignore")
    lines, samples = int(label_val(t, "LINES")), int(label_val(t, "LINE_SAMPLES"))
    a = np.fromfile(img, dtype=dtype)
    if a.size != lines * samples:
        raise ValueError(f"size {a.size} != {lines}x{samples}")
    return a.reshape(lines, samples), t


def check_mola(d):
    a, t = read_img(d / "megt90n000eb.img", d / "megt90n000eb.lbl", ">i2")
    return ("opens", "Mars sphere R=3396 km, simple cylindrical, planetocentric",
            "0..360 east (WestMost 0, EastMost 360)", f"{label_val(t, 'MAP_RESOLUTION')} px/deg (~3.7 km/px), {a.shape[1]}x{a.shape[0]}",
            f"{a.min()}..{a.max()} m", "none in label (no fill value found)")


def check_tes(d):
    a, t = read_img(d / "global_ti_day_2007.img", d / "global_ti_day_2007.lbl", ">i2")
    m, _ = read_img(d / "global_ti_msk_day_2007.img", d / "global_ti_msk_day_2007.lbl", ">i1")
    zero = (a == 0).mean() * 100
    interp = (m == 0).mean() * 100
    return ("opens", "Mars sphere R=3396 km, simple cylindrical, planetocentric",
            "-180..180 east", f"{label_val(t, 'MAP_RESOLUTION')} px/deg (~3 km/px), {a.shape[1]}x{a.shape[0]}",
            f"{a[a > 0].min()}..{a.max()} SI thermal inertia (median {int(np.median(a[a > 0]))}); mask 0/1",
            f"0 = no data ({zero:.1f}% of pixels; mask 0 = interpolated, {interp:.1f}%)")


def check_geology(d):
    z = zipfile.ZipFile(d / "sim3292_database.zip")
    bad = z.testzip()
    names = z.namelist()
    shp = [n for n in names if n.lower().endswith(".shp")]
    prj = next((n for n in names if "Global_Geology" in n and n.endswith(".prj")), None) or \
        next((n for n in names if n.endswith(".prj")), None)
    crs = z.read(prj).decode(errors="ignore")[:60].replace("\n", " ") if prj else "see .prj"
    return ("opens" if bad is None else f"CORRUPT ({bad})", f"shapefiles, {crs}...", "see .prj (Mars lon, check on load)",
            f"vector, 1:20M, {len(shp)} shapefiles in {len(names)} files", "geologic unit polygons (Noachian..Amazonian)", "n/a")


def check_cci(d):
    import h5py
    out = []
    for f in sorted(d.glob("*.nc")):
        with h5py.File(f) as h:
            names = [k for k in h if k not in ("lat", "lon", "time", "spatial_ref") and not k.endswith("uncertainty")]
            v = h[names[0]]
            lat, lon = h["lat"][:], h["lon"][:]
            fv = v.attrs.get("_FillValue")
            out.append(f"{f.name}: vars {','.join(names)}, lat {lat.min():.0f}..{lat.max():.0f}, "
                       f"lon {lon.min():.0f}..{lon.max():.0f}, fill {fv}")
    return ("opens", "WGS84 lat/lon (EPSG:4326)", "-180..180 east", "0.01 deg (~1 km), 6000x36000",
            "ground temp: raw x 0.01 (valid 20315..31315, units degrees C offset); ALT m; PFR 0..1 fraction",
            "65535 (_FillValue); northern hemisphere 25-85 N only; " + " | ".join(out)[:200])


def check_glim(d):
    z = zipfile.ZipFile(d / "LiMW_GIS-2015.gdb.zip")
    bad = z.testzip()
    return ("opens" if bad is None else f"CORRUPT ({bad})", "file geodatabase (WGS84 polygons, see .gdb)", "-180..180 east",
            f"polygon map, ~1:3.75M, {len(z.namelist())} files in zip", "lithology classes (16 first-level)", "n/a")


CHECKS = {"glim": check_glim, "geologic_map_sim3292": check_geology, "mola_megdr_16ppd": check_mola,
          "tes_thermal_inertia_day_2007": check_tes, "esa_cci_permafrost": check_cci}


def row(e):
    d = ROOT / e["local_dir"] if "local_dir" in e else None
    feats = ", ".join(e.get("features", []))
    if e["id"] in CHECKS and d and d.exists():
        try:
            res = CHECKS[e["id"]](d)
            return ("present", *res, feats)
        except Exception as exc:
            return ("present", f"OPEN FAILED: {exc}", "", "", "", "", "", feats)
    if e["id"] == "mars_climate_database":
        got = list(d.glob("*.nc")) if d and d.exists() else []
        st = "present" if got else "missing (manual export; manifest only)"
        return (st, "opens" if got else "-", "-", "-", "-", "-", "-", feats)
    if e.get("access") == "earthengine" and e.get("status") == "ready":
        x = EXPECTED.get(e["id"], ("-",) * 5)
        return ("missing (Earth Engine export not run yet)", "n/a", *x, feats)
    if e["id"] in EXPECTED:
        x = EXPECTED[e["id"]]
        return (f"missing ({e.get('status')})", "-", *x, feats)
    return (f"missing ({e.get('status')})", "-", "-", "-", "-", "-", "-", feats)


def main():
    src = load_sources()
    cols = ["dataset", "present?", "opens?", "coordinate system", "longitude convention", "resolution",
            "value range", "nodata", "feature fed"]
    lines = [f"# Data inventory ({date.today()})", "",
             "Generated by `python -m pipeline.data_fetch.inventory`. Rows with Earth Engine or manual sources "
             "show *expected* properties, not measured ones, until the file is on disk. `data/` is gitignored; "
             "copy it to Drive.", ""]
    for planet in ("mars", "earth"):
        for role in ("required", "optional"):
            ents = [e for e in src[planet] if e.get("role") == role]
            if not ents:
                continue
            lines += [f"## {planet.title()} ({role})", "", "| " + " | ".join(cols) + " |",
                      "|" + "---|" * len(cols)]
            for e in ents:
                r = row(e)
                lines.append("| " + " | ".join([e["id"], *[str(c).replace("|", "/") for c in r]]) + " |")
            lines.append("")
    lines += ["## Skipped", "", ", ".join(s["title"] for s in src["skip"]), ""]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
