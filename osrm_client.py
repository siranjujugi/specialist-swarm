"""OSRM routing client used by the route specialists' `get_route` tool.

Place names are geocoded with Nominatim (restricted to the San Francisco /
SFO service area), routed with OSRM, and trimmed to a compact summary so the
specialist sees distances, durations, and main roads rather than raw geometry.

Defaults to the public FOSSGIS servers, which serve foot, bike, and car
profiles. They are shared community services: results are cached in memory and
requests are throttled. Point at your own servers for anything heavier:

    OSRM_FOOT_URL / OSRM_BIKE_URL / OSRM_CAR_URL   OSRM base URL per profile
    NOMINATIM_URL                                  Nominatim base URL
    OSRM_USER_AGENT                                identifying User-Agent

Usage:
    python osrm_client.py "Ferry Building" "Golden Gate Park" --profile foot
"""

import argparse
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


PROFILE_BASE_URLS = {
    "foot": os.environ.get("OSRM_FOOT_URL", "https://routing.openstreetmap.de/routed-foot"),
    "bike": os.environ.get("OSRM_BIKE_URL", "https://routing.openstreetmap.de/routed-bike"),
    "car": os.environ.get("OSRM_CAR_URL", "https://routing.openstreetmap.de/routed-car"),
}
NOMINATIM_URL = os.environ.get("NOMINATIM_URL", "https://nominatim.openstreetmap.org")
USER_AGENT = os.environ.get("OSRM_USER_AGENT", "specialist-swarm-route-demo/0.1")

# San Francisco plus SFO and the northern peninsula: (west, south, east, north).
SERVICE_AREA = (-122.52, 37.58, -122.35, 37.84)

MAX_WAYPOINTS = 10
MAX_MAIN_ROADS = 3
REQUEST_TIMEOUT_S = 15
MIN_REQUEST_INTERVAL_S = 1.0  # Nominatim's usage policy allows at most 1 req/s.


TOOL_DEFINITION = {
    "type": "custom",
    "name": "get_route",
    "description": (
        "Get road-network distance and duration between ordered waypoints in "
        "the San Francisco / SFO area from OSRM. Waypoints may be place names "
        "(geocoded with OpenStreetMap) or coordinates. Durations are free-flow "
        "estimates with no live traffic, transit, or elevation data. Returns "
        "the resolved coordinates of each waypoint so ambiguous places can be "
        "flagged, plus per-leg distance, duration, and main roads."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "profile": {
                "type": "string",
                "enum": sorted(PROFILE_BASE_URLS),
                "description": "Travel mode: foot, bike, or car.",
            },
            "waypoints": {
                "type": "array",
                "minItems": 2,
                "maxItems": MAX_WAYPOINTS,
                "description": "Ordered stops, origin first and destination last.",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Place name or address."},
                        "lat": {"type": "number"},
                        "lon": {"type": "number"},
                    },
                },
            },
            "alternatives": {
                "type": "boolean",
                "description": "Also return up to two alternative routes (two-waypoint requests only).",
            },
        },
        "required": ["profile", "waypoints"],
    },
}


class OSRMError(Exception):
    """A geocoding or routing request failed in a way the agent should see."""


_cache: dict[str, Any] = {}
_last_request_by_host: dict[str, float] = {}
_throttle_lock = threading.Lock()


def _get_json(url: str) -> Any:
    if url in _cache:
        return _cache[url]

    host = urllib.parse.urlsplit(url).netloc
    with _throttle_lock:
        wait = _last_request_by_host.get(host, 0.0) + MIN_REQUEST_INTERVAL_S - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request_by_host[host] = time.monotonic()

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
            data = json.load(response)
    except urllib.error.HTTPError as e:
        # OSRM reports routing failures (e.g. NoRoute) as 400s with a JSON body.
        try:
            data = json.load(e)
        except ValueError:
            raise OSRMError(f"{host} returned HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise OSRMError(f"Could not reach {host}: {e}") from e

    _cache[url] = data
    return data


def _in_service_area(lat: float, lon: float) -> bool:
    west, south, east, north = SERVICE_AREA
    return west <= lon <= east and south <= lat <= north


def geocode(place: str) -> dict[str, Any]:
    """Resolve a place name to coordinates inside the service area."""
    west, south, east, north = SERVICE_AREA
    query = urllib.parse.urlencode(
        {
            "q": place,
            "format": "jsonv2",
            "limit": 3,
            "countrycodes": "us",
            "viewbox": f"{west},{north},{east},{south}",
            "bounded": 1,
        }
    )
    results = _get_json(f"{NOMINATIM_URL}/search?{query}")
    if not results:
        raise OSRMError(
            f"No match for {place!r} inside the San Francisco / SFO service area."
        )

    best = results[0]
    other_matches = []
    for r in results[1:]:
        name = r.get("display_name")
        if name and name != best.get("display_name") and name not in other_matches:
            other_matches.append(name)
    return {
        "query": place,
        "resolved_name": best.get("display_name"),
        "lat": float(best["lat"]),
        "lon": float(best["lon"]),
        "other_matches": other_matches,
    }


def _resolve_waypoint(waypoint: dict[str, Any]) -> dict[str, Any]:
    if "lat" in waypoint and "lon" in waypoint:
        lat, lon = float(waypoint["lat"]), float(waypoint["lon"])
        if not _in_service_area(lat, lon):
            raise OSRMError(
                f"({lat}, {lon}) is outside the San Francisco / SFO service area."
            )
        label = waypoint.get("name") or f"{lat:.5f},{lon:.5f}"
        return {"query": label, "resolved_name": label, "lat": lat, "lon": lon, "other_matches": []}
    if waypoint.get("name"):
        return geocode(waypoint["name"])
    raise OSRMError("Each waypoint needs a name or both lat and lon.")


def _main_roads(leg: dict[str, Any]) -> list[str]:
    distance_by_road: dict[str, float] = {}
    for step in leg.get("steps", []):
        road = step.get("name") or step.get("ref")
        if road:
            distance_by_road[road] = distance_by_road.get(road, 0.0) + step.get("distance", 0.0)
    ranked = sorted(distance_by_road, key=distance_by_road.get, reverse=True)
    return ranked[:MAX_MAIN_ROADS]


def _summarize_route(route: dict[str, Any], stops: list[dict[str, Any]]) -> dict[str, Any]:
    legs = []
    for i, leg in enumerate(route["legs"]):
        legs.append(
            {
                "from": stops[i]["query"],
                "to": stops[i + 1]["query"],
                "distance_km": round(leg["distance"] / 1000, 2),
                "duration_min": round(leg["duration"] / 60, 1),
                "main_roads": _main_roads(leg),
            }
        )
    return {
        "distance_km": round(route["distance"] / 1000, 2),
        "duration_min": round(route["duration"] / 60, 1),
        "legs": legs,
    }


def get_route(
    profile: str, waypoints: list[dict[str, Any]], alternatives: bool = False
) -> dict[str, Any]:
    """Geocode the waypoints, route them in order, and return a compact summary."""
    if profile not in PROFILE_BASE_URLS:
        raise OSRMError(f"Unknown profile {profile!r}; use one of {sorted(PROFILE_BASE_URLS)}.")
    if not 2 <= len(waypoints) <= MAX_WAYPOINTS:
        raise OSRMError(f"Provide between 2 and {MAX_WAYPOINTS} waypoints.")

    stops = [_resolve_waypoint(w) for w in waypoints]
    coordinates = ";".join(f"{s['lon']},{s['lat']}" for s in stops)
    query = urllib.parse.urlencode(
        {
            "overview": "false",
            "steps": "true",
            "alternatives": "true" if alternatives and len(stops) == 2 else "false",
        }
    )
    url = f"{PROFILE_BASE_URLS[profile]}/route/v1/{profile}/{coordinates}?{query}"
    data = _get_json(url)
    if data.get("code") != "Ok":
        raise OSRMError(f"OSRM could not route this request: {data.get('code')} {data.get('message', '')}".strip())

    routes = [_summarize_route(r, stops) for r in data["routes"]]
    return {
        "source": "OSRM (OpenStreetMap data), free-flow estimate",
        "profile": profile,
        "waypoints": stops,
        "route": routes[0],
        "alternatives": routes[1:],
    }


def run_tool(name: str, tool_input: dict[str, Any]) -> str:
    """Run a custom tool call and return its result as JSON text."""
    if name != TOOL_DEFINITION["name"]:
        raise OSRMError(f"Unknown tool {name!r}.")
    result = get_route(
        tool_input["profile"],
        tool_input["waypoints"],
        tool_input.get("alternatives", False),
    )
    return json.dumps(result, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(description="Route between San Francisco places with OSRM.")
    parser.add_argument("places", nargs="+", help="Ordered place names, origin first.")
    parser.add_argument("--profile", choices=sorted(PROFILE_BASE_URLS), default="foot")
    parser.add_argument("--alternatives", action="store_true")
    args = parser.parse_args()

    try:
        print(
            run_tool(
                "get_route",
                {
                    "profile": args.profile,
                    "waypoints": [{"name": p} for p in args.places],
                    "alternatives": args.alternatives,
                },
            )
        )
    except OSRMError as e:
        raise SystemExit(f"Error: {e}")


if __name__ == "__main__":
    main()
