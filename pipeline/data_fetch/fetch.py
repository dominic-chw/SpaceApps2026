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


def download(url, dest, auth=None, chunk=1 << 20, session=None):
    """Stream url to dest with resume. Returns 'skipped' | 'done'."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    http = session or requests
    head = http.head(url, allow_redirects=True, timeout=60, headers=UA, auth=auth)
    total = int(head.headers.get("content-length", 0)) if head.ok else 0
    have = dest.stat().st_size if dest.exists() else 0
    if total and have == total:
        return "skipped"
    headers = dict(UA)
    mode = "wb"
    if have and total and have < total:
        headers["Range"] = f"bytes={have}-"
        mode = "ab"
    with http.get(url, stream=True, timeout=120, headers=headers, auth=auth) as r:
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
            if e.get("access") not in ("http", "earthdata") or e.get("status") != "ready" or "files" not in e:
                continue
            if only and e["id"] not in only:
                continue
            session = earthdata_session() if e["access"] == "earthdata" else None
            for f in e["files"]:
                dest = ROOT / e["local_dir"] / f["name"]
                try:
                    print(f"{e['id']}: {f['name']} ... ", end="", flush=True)
                    print(download(f["url"], dest, session=session))
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


class EarthdataSession(requests.Session):
    """Keeps the Earthdata login on redirects to urs.earthdata.nasa.gov only (never other hosts)."""
    AUTH_HOST = "urs.earthdata.nasa.gov"

    def rebuild_auth(self, prepared_request, response):
        from urllib.parse import urlparse
        super().rebuild_auth(prepared_request, response)
        if urlparse(prepared_request.url).hostname == self.AUTH_HOST and self.auth:
            prepared_request.prepare_auth(self.auth)


def earthdata_session():
    load_env()
    s = EarthdataSession()
    s.headers.update(UA)
    auth = earthdata_auth()
    if auth:
        s.auth = auth                      # else requests falls back to ~/.netrc
    elif not (Path.home() / ".netrc").exists() and not (Path.home() / "_netrc").exists():
        raise SystemExit("No Earthdata login: set EARTHDATA_USERNAME/PASSWORD in .env or add "
                         "urs.earthdata.nasa.gov to ~/.netrc")
    return s


def fetch_earthdata():
    s = earthdata_session()
    entry = next(e for e in load_sources()["earth"] if e["id"] == "nsidc_circumarctic_ground_ice")
    r = s.get(entry["data_url"], timeout=60)
    print(r.status_code, entry["data_url"])
    if r.ok:
        import re
        names = sorted(set(re.findall(r'href="([^"?/][^"]*)"', r.text)))
        print(chr(10).join(names[:60]))
    return s, r


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
