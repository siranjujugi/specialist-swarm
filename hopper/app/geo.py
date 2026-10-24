"""Real place search: any San Francisco address or point of interest, not just landmarks.

- Search / reverse: Photon (photon.komoot.io — OpenStreetMap, built for type-ahead, no key),
  restricted to the SF bounding box. Cached 1 day.
- Elevation: USGS Elevation Point Query Service (no key), so hill math works for any
  address; falls back to the nearest landmark's elevation.
- Searched places become `geo:<lat>,<lon>` keys registered into data.PLACES, so every
  existing tool (routing, fares, health, Critic) works on them unchanged. Registration is
  capped (LRU) and keys are validated to be inside SF.
"""
import re
import threading
from collections import OrderedDict

from . import tools
from .data import PLACES
from .live import FeedError, get_json

PHOTON = "https://photon.komoot.io/api/"
PHOTON_REVERSE = "https://photon.komoot.io/reverse"
EPQS = "https://epqs.nationalmap.gov/v1/json"
SF_BBOX = "-122.52,37.70,-122.35,37.835"
MAX_DYNAMIC = 500
GEO_KEY = re.compile(r"^geo:(-?\d+\.\d+),(-?\d+\.\d+)$")

_dynamic = OrderedDict()         # geo key → True, oldest first
_lock = threading.Lock()


def is_geo(key):
    return isinstance(key, str) and key.startswith("geo:")


def landmarks():
    """Built-in landmark keys only — searched addresses never become pickup candidates."""
    return [k for k in PLACES if not is_geo(k)]


def make_key(lat, lon):
    return f"geo:{lat:.5f},{lon:.5f}"


def inside_sf(lat, lon):
    return 37.70 <= lat <= 37.835 and -122.52 <= lon <= -122.35


def near_sfo(lat, lon):
    return tools.haversine_pts([lat, lon], tools.latlon("sfo")) <= 1.5


def elevation_ft(lat, lon):
    try:
        data, _ = get_json(EPQS, {"x": f"{lon:.5f}", "y": f"{lat:.5f}", "units": "Feet", "wkid": 4326},
                           ttl=86400, disk_ttl=2592000, timeout=5)
        value = float(data["value"])
        if -50 < value < 1200:        # EPQS returns -1000000 for "no data"
            return round(value)
    except (FeedError, KeyError, TypeError, ValueError):
        pass
    nearest = min(landmarks(), key=lambda k: tools.haversine_pts([lat, lon], tools.latlon(k)))
    return PLACES[nearest][3]


def register(name, lat, lon, elev=None):
    """Make a point usable by the planner. Returns its key. Raises ValueError outside SF."""
    lat, lon = float(lat), float(lon)
    if not (inside_sf(lat, lon) or near_sfo(lat, lon)):
        raise ValueError("That place is outside San Francisco. Hopper covers SF and SFO only.")
    key = make_key(lat, lon)
    with _lock:
        if key not in PLACES or PLACES[key][3] is None:
            PLACES[key] = (str(name)[:80] or "Pinned location", lat, lon,
                           elev if elev is not None else None)
        _dynamic[key] = True
        _dynamic.move_to_end(key)
        while len(_dynamic) > MAX_DYNAMIC:
            old, _ = _dynamic.popitem(last=False)
            PLACES.pop(old, None)
    return key


def ensure_ready(key):
    """Fill in elevation for a searched place before planning (one cached USGS call)."""
    if is_geo(key) and key in PLACES and PLACES[key][3] is None:
        name, lat, lon, _ = PLACES[key]
        PLACES[key] = (name, lat, lon, elevation_ft(lat, lon))


def resolve(value):
    """A landmark key, a geo key, or {name, lat, lon} → a registered key (or None)."""
    if isinstance(value, dict) and "lat" in value and "lon" in value:
        return register(value.get("name") or "Pinned location", value["lat"], value["lon"])
    if isinstance(value, str):
        if value in PLACES:
            return value
        m = GEO_KEY.match(value)
        if m:
            return register("Pinned location", float(m.group(1)), float(m.group(2)))
    return None


def _label(p):
    """Human name + secondary line from Photon properties."""
    street = " ".join(x for x in (p.get("housenumber"), p.get("street")) if x)
    name = p.get("name") or street or "Unnamed place"
    area = p.get("district") or p.get("locality") or p.get("city") or "San Francisco"
    secondary = ", ".join(x for x in (street if street and street != name else "", area) if x)
    return name, secondary


def parse_photon(payload):
    """Photon GeoJSON → [{name, address, lat, lon, kind}] inside SF (or at SFO), deduped."""
    out, seen = [], set()
    for f in (payload or {}).get("features", []):
        try:
            lon, lat = f["geometry"]["coordinates"][:2]
        except (KeyError, TypeError, ValueError):
            continue
        if not (inside_sf(lat, lon) or near_sfo(lat, lon)):
            continue
        p = f.get("properties", {})
        name, secondary = _label(p)
        sig = (name.lower(), round(lat, 4), round(lon, 4))
        if sig in seen:
            continue
        seen.add(sig)
        out.append({"name": name, "address": secondary, "lat": round(lat, 6), "lon": round(lon, 6),
                    "kind": p.get("osm_value") or p.get("type") or "place"})
    return out


def search(query, limit=6):
    q = (query or "").strip()
    if len(q) < 2:
        return []
    data, _ = get_json(PHOTON, {"q": q, "bbox": SF_BBOX, "limit": limit + 4, "lang": "en",
                                "lat": "37.7749", "lon": "-122.4194"}, ttl=86400, disk_ttl=86400, timeout=6)
    results = parse_photon(data)[:limit]
    for r in results:
        r["key"] = make_key(r["lat"], r["lon"])
    return results


def reverse(lat, lon):
    data, _ = get_json(PHOTON_REVERSE, {"lat": f"{lat:.5f}", "lon": f"{lon:.5f}", "lang": "en"},
                       ttl=86400, disk_ttl=86400, timeout=6)
    found = parse_photon(data)
    name = found[0]["name"] if found else "Current location"
    return {"name": name, "address": found[0]["address"] if found else "", "lat": lat, "lon": lon,
            "key": make_key(lat, lon)}


def geocode_first(text):
    """Best single match for free text (used by the Intent agent's fallback)."""
    try:
        hits = search(text, limit=1)
    except FeedError:
        return None
    if not hits:
        return None
    h = hits[0]
    return register(h["name"], h["lat"], h["lon"])
