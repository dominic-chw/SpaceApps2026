"""Build a global hydrogen map from the Odyssey GRS/HEND AHD slices (PDS ODY-M-GRS-5-AHD-V1.0).
Each slice = 2592 rows (36 lat x 72 lon, 5 deg cells) of 24 big-... IEEE floats (96-byte rows);
the PDS ships time slices (15 deg Ls), not one map, so this averages them.

Product written to data/mars/neutron/:
  hydrogen_map_5deg.csv  lat, lon (east, 0..360 as in the PDS), epithermal_flux (n/cm2/s, 100 eV-10 keV,
                         exposure-weighted median over slices), n_slices,
                         h_suppression 0..1 = 1 - flux / dry_reference (dry_reference = 90th percentile
                         of cell flux within +-40 deg latitude)
  hydrogen_map_5deg.txt  label: what the numbers mean and what they do not
h_suppression is a RELATIVE hydrogen indicator (epithermal neutrons are suppressed by hydrogen). It is not
calibrated water-equivalent hydrogen (WEH); converting to WEH needs a layered-regolith model (Feldman et al.).
Run: python -m pipeline.data_fetch.build_neutron_map
"""
import re

import numpy as np
import pandas as pd
import requests

from .fetch import ROOT, UA, download

BASE = "https://pds-geosciences.wustl.edu/ody/ody-m-grs-5-ahd-v1/odgh1_xxxx"
INV = f"{BASE}/collection_data_science_averaged_inventory.csv"
OUT = ROOT / "data" / "mars" / "neutron"
DT = np.dtype([(n, ">f4") for n in [
    "lat", "lon", "exposure", "sd_norm", "sd_rate", "sd_err", "md_norm", "md_rate", "md_err",
    "ld_norm", "ld_rate", "ld_err", "isl_norm", "isl_rate", "isl_err", "ish_norm", "ish_rate", "ish_err",
    "epi1", "epi2", "fast1", "fast2", "neutron_norm", "alpha"]])
INVALID = -999


def slice_urls():
    txt = requests.get(INV, headers=UA, timeout=60).text
    out = []
    for m in re.finditer(r"ahd_(\d\d)_(\d{3})_(\d{3})", txt):
        yy, a, b = m.groups()
        out.append(f"{BASE}/yr{yy}/yr{yy}_ls{a}_{b}/ahd_{yy}_{a}_{b}.dat")
    return sorted(set(out))


def main():
    sl = OUT / "slices"
    arrays = []
    urls = slice_urls()
    print(len(urls), "slices")
    for u in urls:
        dest = sl / u.rsplit("/", 1)[1]
        download(u, dest)
        a = np.fromfile(dest, dtype=DT)
        if a.size != 2592:
            raise ValueError(f"{dest.name}: {a.size} rows")
        arrays.append(a)
    flux = np.stack([a["epi1"].astype(float) for a in arrays])           # slices x 2592
    expo = np.stack([a["exposure"].astype(float) for a in arrays])
    ok = (flux > 0) & (expo > 0) & (flux != INVALID)
    flux = np.where(ok, flux, np.nan)
    med = np.nanmedian(flux, axis=0)       # median over time = frost-season outliers have little weight
    n = ok.sum(axis=0)
    lat = arrays[0]["lat"].astype(float)
    lon = arrays[0]["lon"].astype(float)
    mid = np.abs(lat) <= 40
    dry = np.nanpercentile(med[mid], 90)
    df = pd.DataFrame({"lat": lat, "lon": lon, "epithermal_flux": med, "n_slices": n,
                       "h_suppression": np.clip(1 - med / dry, 0, 1)})
    OUT.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT / "hydrogen_map_5deg.csv", index=False)
    (OUT / "hydrogen_map_5deg.txt").write_text(
        f"Built from {len(urls)} Odyssey GRS/HEND AHD slices ({BASE}).\n"
        "epithermal_flux: AHD column EPITHERMAL_FLUX1 (100 eV-10 keV, n/cm2/s), median over slices, 5 deg cells, lon 0..360 east.\n"
        f"h_suppression = 1 - flux / {dry:.4f} (90th percentile of flux within +-40 deg latitude), clipped 0..1.\n"
        "Relative hydrogen indicator ONLY; not calibrated water-equivalent hydrogen. HEND footprint is ~500 km.\n")
    print(df.describe().round(3).to_string())
    print("dry reference", round(dry, 4), "cells without data:", int(df.epithermal_flux.isna().sum()))


if __name__ == "__main__":
    main()
