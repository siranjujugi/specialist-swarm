"""OSRM parsing and leg refinement — sample payloads only, no network."""
import pytest

from app import agents, osrm, tools
from app.live import FeedError

SAMPLE = {"code": "Ok", "routes": [{"distance": 2184.0, "duration": 1747.0, "geometry": {
    "type": "LineString",
    "coordinates": [[-122.3937, 37.7955], [-122.4000, 37.7990], [-122.4058, 37.8024]]}}]}


def test_parse_route_converts_units_and_flips_coordinates():
    r = osrm.parse_route(SAMPLE)
    assert r["distance_mi"] == pytest.approx(1.357, abs=0.001)
    assert r["duration_min"] == pytest.approx(29.1, abs=0.1)
    assert r["path"][0] == [37.7955, -122.3937]


def test_parse_route_rejects_errors():
    with pytest.raises(FeedError):
        osrm.parse_route({"code": "NoRoute", "routes": []})


def test_real_distance_drives_walk_numbers():
    est = tools.walk_leg("ferry_building", "coit_tower")
    real = tools.walk_leg("ferry_building", "coit_tower", dist=1.357,
                          path=osrm.parse_route(SAMPLE)["path"], source="osrm.foot")
    assert real.distance_mi > est.distance_mi
    assert real.minutes > est.minutes and real.kcal > est.kcal and real.steps > est.steps
    assert len(real.path) == 3 and real.source == "osrm.foot"


def test_ride_uses_free_flow_time_times_traffic():
    noon = tools.ride_leg("ferry_building", "coit_tower", hour=12, dist=1.57, free_flow_min=6)
    rush = tools.ride_leg("ferry_building", "coit_tower", hour=17, dist=1.57, free_flow_min=6)
    assert noon.minutes == pytest.approx(6 * 1.35, abs=0.1)
    assert rush.minutes > noon.minutes


def test_routing_agent_swaps_legs(monkeypatch):
    monkeypatch.delenv("HOPPER_OFFLINE", raising=False)
    monkeypatch.setattr(osrm, "route", lambda mode, a, b: {**osrm.parse_route(SAMPLE), "cache": "live"})
    req = {**agents.DEFAULTS, "hour": 12, "event": False}
    trip = tools.Trip([tools.walk_leg("ferry_building", "coit_tower")], "test")
    trace = agents.Trace()
    assert agents.routing_agent([trip], req, trace) == "OSRM streets"
    assert trip.legs[0].source == "osrm.foot" and len(trip.legs[0].path) == 3


def test_routing_agent_keeps_estimates_when_osrm_fails(monkeypatch):
    monkeypatch.delenv("HOPPER_OFFLINE", raising=False)

    def boom(*_):
        raise FeedError("down")
    monkeypatch.setattr(osrm, "route", boom)
    req = {**agents.DEFAULTS, "hour": 12, "event": False}
    trip = tools.Trip([tools.walk_leg("ferry_building", "coit_tower")], "test")
    assert agents.routing_agent([trip], req, agents.Trace()).startswith("estimated")
    assert trip.legs[0].source == "walk_leg"
