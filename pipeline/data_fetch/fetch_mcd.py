"""Fetch Mars Climate Database (v6.2) global maps via the web interface's ASCII output.
Form inspected at http://www-mars.lmd.jussieu.fr/mcd_python/ : GET cgi-bin/mcdcgi.py, the
response links '../txt/<hash>.txt' (ASCII data) and '../img/<hash>.png'.
Writes data/mars/mcd/tsurf_ls<Ls>_lt<hh>.txt, h2o_ls<Ls>_lt12.txt and manifest.txt.
Run: python -m pipeline.data_fetch.fetch_mcd [--force]
"""
import argparse
import datetime as dt
import re
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "mars" / "mcd"
BASE = "https://www-mars.lmd.jussieu.fr/mcd_python/"
UA = {"User-Agent": "Mozilla/5.0 (SpaceApps2026 data fetch)"}
PAUSE_S = 2
LS = (0, 90, 180, 270)

# Field names/values copied from the form HTML (defaults kept unless the task sets them).
FORM = {
    "var2": "none", "var3": "none", "var4": "none",
    "datekeyhtml": "1",            # Mars date (Ls + local time)
    "latitude": "all", "longitude": "all",
    "altitude": "0", "zkey": "3",  # 0 m above surface
    "spacecraft": "none", "betatest": "on",
    "isfixedlt": "on",             # same local time everywhere
    "dust": "1", "hrkey": "1",     # climatology scenario, high-res (form defaults)
    "averaging": "off", "zonmean": "off", "diumean": "off",
    "animation": "off", "animframes": "12", "dpi": "80", "islog": "off",
    "colorm": "jet", "proj": "cyl", "iswind": "off",
}


def jobs():
    for ls in LS:
        for lt in (12, 0):
            yield f"tsurf_ls{ls}_lt{lt:02d}.txt", "tsurf", ls, lt
    for ls in LS:
        yield f"h2o_ls{ls}_lt12.txt", "col_h2ovapor", ls, 12


def fetch_one(session, var, ls, lt):
    params = dict(FORM, var1=var, ls=str(ls), localtime=str(lt))
    r = session.get(BASE + "cgi-bin/mcdcgi.py", params=params, timeout=300)
    r.raise_for_status()
    m = re.search(r"href='\.\./(txt/[0-9a-f]+\.txt)'", r.text)
    if not m:
        raise RuntimeError(f"no ASCII link in response for {var} Ls{ls} LT{lt}")
    time.sleep(PAUSE_S)
    t = session.get(BASE + m.group(1), timeout=120)
    t.raise_for_status()
    return r.url, t.text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="re-download existing files")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    s = requests.Session()
    s.headers.update(UA)
    log, first = [], True
    for name, var, ls, lt in jobs():
        dest = OUT / name
        if dest.exists() and dest.stat().st_size > 0 and not a.force:
            print("skip", name)
            log.append(f"{name}\t{var}\tLs={ls}\tLT={lt}\t(existing)")
            continue
        if not first:
            time.sleep(PAUSE_S)
        first = False
        url, text = fetch_one(s, var, ls, lt)
        dest.write_text(text, encoding="utf-8")
        print("ok  ", name, len(text), "bytes")
        log.append(f"{name}\t{var}\tLs={ls}\tLT={lt}\t{url}")
    fixed = "\n".join(f"  {k} = {v}" for k, v in FORM.items())
    (OUT / "manifest.txt").write_text(
        "Mars Climate Database v6.2 web interface (ASCII output)\n"
        f"Source: {BASE}\nDownloaded: {dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}\n"
        "Settings: Mars date mode (Ls + local time), same local time everywhere = on,\n"
        "  latitude all, longitude all, altitude 0 m above surface, dust scenario 1 (climatology)\n"
        "Variables: tsurf = surface temperature (K); col_h2ovapor = water vapour column (kg/m2)\n"
        f"Fixed form fields:\n{fixed}\n\nFiles:\n" + "\n".join(log) + "\n",
        encoding="utf-8")
    print("wrote manifest.txt")


if __name__ == "__main__":
    main()
