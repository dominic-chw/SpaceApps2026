"""Download the `access: http` datasets in data_sources.yaml into data/mars/ and data/earth/.
Resumable, skips files already complete, never guesses URLs (only reads the yaml).
Run: python -m pipeline.data_fetch.fetch [--only ID ...] [--init-mcd] [--earthdata]
Earthdata (NSIDC) needs EARTHDATA_USERNAME/PASSWORD in .env or a ~/.netrc entry; it is only
used with --earthdata.
"""
import argparse
import os
import shutil
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ROOT / "data_sources.yaml"
UA = {"User-Agent": "Mozilla/5.0 (SpaceApps2026 data fetch)"}


def load_sources():
    return yaml.safe_load(SOURCES.read_text(encoding="utf-8"))


def load_env(path=ROOT / ".env"):
    """Minimal .env reader (no python-dotenv needed); real environment variables win."""
    if Path(path).exists():
        for line in Path(path).read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def download(url, dest, auth=None, chunk=1 << 20):
    """Stream url to dest with resume. Returns 'skipped' | 'done'."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    head = requests.head(url, allow_redirects=True, timeout=60, headers=UA, auth=auth)
    total = int(head.headers.get("content-length", 0)) if head.ok else 0
    have = dest.stat().st_size if dest.exists() else 0
    if total and have == total:
        return "skipped"
    headers = dict(UA)
    mode = "wb"
    if have and total and have < total:
        headers["Range"] = f"bytes={have}-"
        mode = "ab"
    with requests.get(url, stream=True, timeout=120, headers=headers, auth=auth) as r:
        r.raise_for_status()
        if mode == "ab" and r.status_code != 206:
            mode = "wb"
        with open(dest, mode) as f:
            for block in r.iter_content(chunk):
                f.write(block)
    return "done"


def fetch_http(only=None):
    src = load_sources()
    for planet in ("mars", "earth"):
        for e in src[planet]:
            if e.get("access") != "http" or e.get("status") != "ready" or "files" not in e:
                continue
            if only and e["id"] not in only:
                continue
            for f in e["files"]:
                dest = ROOT / e["local_dir"] / f["name"]
                try:
                    print(f"{e['id']}: {f['name']} ... ", end="", flush=True)
                    print(download(f["url"], dest))
                except Exception as exc:  # keep going; report at the end
                    print(f"FAILED ({exc})")


def init_mcd():
    d = ROOT / "data" / "mars" / "mcd"
    d.mkdir(parents=True, exist_ok=True)
    target = d / "manifest.txt"
    if not target.exists():
        shutil.copy(Path(__file__).with_name("mcd_manifest_template.txt"), target)
    print("MCD manifest:", target)


def earthdata_auth():
    """(user, password) from .env, else None so requests falls back to ~/.netrc."""
    load_env()
    u, p = os.environ.get("EARTHDATA_USERNAME"), os.environ.get("EARTHDATA_PASSWORD")
    return (u, p) if u and p else None


def fetch_earthdata():
    load_env()
    auth = earthdata_auth()
    if auth is None and not (Path.home() / ".netrc").exists() and not (Path.home() / "_netrc").exists():
        raise SystemExit("No Earthdata login: set EARTHDATA_USERNAME/PASSWORD in .env or add "
                         "urs.earthdata.nasa.gov to ~/.netrc")
    entry = next(e for e in load_sources()["earth"] if e["id"] == "nsidc_circumarctic_ground_ice")
    r = requests.get(entry["data_url"], auth=auth, headers=UA, timeout=60)
    print(r.status_code, entry["data_url"])
    print("Directory listing needs the login; pick the files from it, add them to the yaml "
          "`files:` list, set status: ready and re-run.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--init-mcd", action="store_true")
    ap.add_argument("--earthdata", action="store_true")
    a = ap.parse_args()
    if a.init_mcd:
        init_mcd()
    elif a.earthdata:
        fetch_earthdata()
    else:
        fetch_http(a.only)
