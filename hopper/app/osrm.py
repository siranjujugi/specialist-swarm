"""Real street routes from OSRM (Open Source Routing Machine) on OpenStreetMap data.

Uses the public FOSSGIS servers (routing.openstreetmap.de), which run separate foot, bike,
and car profiles — the project-osrm.org demo is car-only. These are shared community
servers: results are cached for a day on disk, each plan only routes the legs the rider
will actually see, and a rolling budget keeps usage polite.
Override with OSRM_BASE_URL (e.g. a local `osrm-backend` container) for heavier use.
"""
import os
import time

from .live import Budget, FeedError, get_json

BASE = os.environ.get("OSRM_BASE_URL", "https://routing.openstreetmap.de")
PROFILE = {"walk": "foot", "bike": "bike", "ebike": "bike", "ride": "car"}
BUDGET = Budget(int(os.environ.get("OSRM_HOURLY_BUDGET", "600")))
M_TO_MI = 1 / 1609.34


def parse_route(payload):
    """OSRM /route response → {distance_mi, duration_min, path [[lat, lon], ...]}."""
    if (payload or {}).get("code") != "Ok" or not payload.get("routes"):
        raise FeedError(f"OSRM: {(payload or {}).get('code', 'no response')}")
    r = payload["routes"][0]
    coords = r["geometry"]["coordinates"]            # GeoJSON is [lon, lat]
    return {"distance_mi": r["distance"] * M_TO_MI, "duration_min": r["duration"] / 60,
            "path": [[round(lat, 6), round(lon, 6)] for lon, lat in coords]}


def route(mode, a_latlon, b_latlon):
    """Street route for a leg mode between two [lat, lon] points. Raises FeedError."""
    profile = PROFILE.get(mode)
    if not profile:
        raise FeedError(f"no OSRM profile for {mode}")
    (alat, alon), (blat, blon) = a_latlon, b_latlon
    # FOSSGIS serves each profile under its own prefix; the path segment stays "driving".
    url = (f"{BASE}/routed-{profile}/route/v1/driving/"
           f"{alon:.5f},{alat:.5f};{blon:.5f},{blat:.5f}")
    params = {"overview": "full", "geometries": "geojson"}
    try:
        data, meta = get_json(url, params, ttl=86400, disk_ttl=86400, budget=BUDGET, timeout=6)
    except FeedError as e:
        if "429" not in str(e):
            raise
        time.sleep(1.0)            # shared server asked us to slow down — one polite retry
        data, meta = get_json(url, params, ttl=86400, disk_ttl=86400, budget=BUDGET, timeout=6)
    return {**parse_route(data), "cache": meta["cache"]}
