"""The agent eval suite runs in CI, and seeded bugs prove each check can actually fail."""
import copy

from app import modes
from evals import checks, run


def _fmt(failures):
    return "\n".join(f"{r['suite']} · {r['case']}: {'; '.join(r['fails'])}" for r in failures)


def test_offline_evals_pass():
    report = run.run()
    assert report.results, "no eval results"
    assert not report.failures, _fmt(report.failures)


def test_catches_a_mode_that_drifts_from_its_spec(monkeypatch):
    # Someone loosens Hustle's walk limit in code; the spec in cases.json still says 0.4 mi.
    monkeypatch.setitem(modes.MODES, "hustle", {**modes.MODES["hustle"], "max_walk_leg_mi": 2.0,
                                                "max_total_walk_mi": 3.0, "weights": {"time": 0.5, "cost": 3.0, "health": 0.5}})
    report = run.run()
    assert any(r["suite"] == "mode" and "/hustle" in r["case"] for r in report.failures)


def _resp(why):
    leg = {"mode": "ride", "frm": "a", "to": "b", "distance_mi": 2.0, "minutes": 10, "wait_min": 0,
           "cost": 12.0, "line": "", "grade": 0}
    base = {"legs": [dict(leg, cost=15.0)], "total_min": 10, "cost": 15.0, "kcal": 0}
    opt = {"label": "Recommended", "legs": [leg], "total_min": 10, "cost": 12.0, "kcal": 0, "why": why}
    return {"request": {"origin": "a", "destination": "b"}, "options": [opt], "baseline": base}


def test_explanation_checks():
    assert checks.explanation(_resp("Ride 2 mi to b. saves $3.00 vs. a door-to-door ride.")) == []
    # Right number, wrong direction — grounding alone can't catch this; faithfulness does.
    assert "claims" in checks.explanation(_resp("Ride 2 mi to b; costs $3.00 more."))[0]
    # A number that appears nowhere in the facts.
    assert "ungrounded" in checks.explanation(_resp("Ride 2 mi to b, saves 40% on the fare."))[0]


def test_grounding_compares_values_not_digits():
    from app.agents import grounded
    assert grounded("saves $3.00", '{"saves": 3.0}')
    assert not grounded("+100 kcal", '{"kcal": 1}')       # "100" used to strip to "1"
    assert not grounded("40% off", '{"x": 4}')


def test_label_honesty():
    resp = _resp("x")
    slow = copy.deepcopy(resp["options"][0])
    slow.update(label="Fastest", total_min=20)
    resp["options"].append(slow)
    assert checks.labels(resp) == ["“Fastest” is not the fastest option shown"]


def test_golden_drift_is_reported():
    want = {"legs": "walk+muni:N", "total_min": 30, "cost": 2.85, "kcal": 100}
    assert run.golden_diff(want, dict(want, total_min=31)) == []          # within 10 %
    fails = run.golden_diff(want, {"legs": "ride", "total_min": 15, "cost": 18.0, "kcal": 0})
    assert len(fails) == 4


def test_intent_check_is_case_insensitive_and_checks_absent_fields():
    case = {"expect": {"origin_text": "500 Folsom St", "max_cost": 5}, "absent": ["origin"]}
    assert checks.intent(case, {"origin_text": "500 folsom st", "max_cost": 5.0}) == []
    assert len(checks.intent(case, {"origin": "moscone", "max_cost": 6})) == 3
