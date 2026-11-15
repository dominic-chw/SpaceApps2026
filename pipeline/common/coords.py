"""Lat/lon <-> grid cell helpers. lon east-positive -180..180.
Grid size and latitude limit come from config `processing:`; pass `params` to override.
Check with Jezero (18.4 N, 77.5 E). Step 3; plan s3.
"""
from .config import get_processing


def wrap_lon(lon, west_positive=False):
    """Convert any longitude to east-positive -180..180.
    Mars data often comes as 0..360 east (fine, wraps) or west-positive (pass west_positive=True)."""
    if west_positive:
        lon = -lon
    return (lon + 180) % 360 - 180


def snap(lat, lon, params=None):
    """Snap lat/lon to the centre of its grid cell."""
    g = (params or get_processing())["grid_deg"]
    lat = min(max(lat, -90), 90 - 1e-9)  # keep the pole inside the top cell
    wrap = wrap_lon(lon)
    return (lat // g) * g + g / 2, (wrap // g) * g + g / 2


def in_range(lat, params=None):
    return abs(lat) <= (params or get_processing())["lat_limit"]


def cell_id(lat, lon, params=None):
    clat, clon = snap(lat, lon, params)
    return f"{clat:+06.1f}_{clon:+06.1f}"


def grid_cells(params=None):
    """All (lat, lon) cell centres within +-lat_limit."""
    p = params or get_processing()
    g = p["grid_deg"]
    lats = [-90 + g / 2 + i * g for i in range(int(180 / g))]
    lons = [-180 + g / 2 + j * g for j in range(int(360 / g))]
    return [(la, lo) for la in lats if abs(la) <= p["lat_limit"] for lo in lons]
