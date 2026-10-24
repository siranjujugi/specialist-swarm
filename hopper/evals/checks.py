"""Eval checks. Each takes plan output and returns a list of failure strings ([] = pass).

Checks read the planner's *response* (what the rider sees) and judge it against the spec in
cases.json — they never call the code under test to decide what's right, so a bug in the
planner can't hide by also being in the oracle.
"""
import json
import re

from app.agents import grounded

TRANSIT = ("muni", "bart", "cable", "bus", "tram", "metro", "rail", "ferry")
ACTIVE = ("walk", "bike", "ebike")
EPS = 1e-6


def modes_of(trip):
    return [l["mode"] for l in trip["legs"]]


def active_share(trip):
    return sum(l["minutes"] for l in trip["legs"] if l["mode"] in ACTIVE) / (trip["total_min"] or 1)


def trip_dict(t):
    """A feasible Trip object → the same shape as a response option."""
    return t.to_dict() if hasattr(t, "to_dict") else t


# ── Integrity: the trip is a real, connected journey ─────────────────────────

def integrity(resp):
    fails = []
    origin, dest = resp["request"]["origin"], resp["request"]["destination"]
    if not resp["options"]:
        return ["no options returned"]
    for o in resp["options"] + [resp["baseline"]]:
        legs, tag = o["legs"], o.get("label", "baseline")
        if legs[0]["frm"] != origin or legs[-1]["to"] != dest:
            fails.append(f"{tag}: starts at {legs[0]['frm']} / ends at {legs[-1]['to']}")
        for a, b in zip(legs, legs[1:]):
            if a["to"] != b["frm"]:
                fails.append(f"{tag}: gap between legs ({a['to']} → {b['frm']})")
        for l in legs:
            if min(l["distance_mi"], l["minutes"], l["cost"], l["wait_min"]) < 0:
                fails.append(f"{tag}: negative number on a {l['mode']} leg")
    return fails


# ── Grounding + faithfulness of the one-line explanation ─────────────────────

def deltas(option, baseline):
    """Independent recomputation of the trade-offs vs. the door-to-door ride."""
    return {"saves": round(baseline["cost"] - option["cost"], 2),
            "slower_min": round(option["total_min"] - baseline["total_min"]),
            "extra_kcal": option["kcal"] - baseline["kcal"]}


CLAIMS = (
    # pattern, which delta it quotes, how the quoted number relates to that delta
    (re.compile(r"saves? \$(\d+(?:\.\d+)?)"), "saves", lambda n, d: abs(n - d) <= 0.011),
    (re.compile(r"costs? \$(\d+(?:\.\d+)?) more"), "saves", lambda n, d: abs(n + d) <= 0.011),
    (re.compile(r"(\d+) min(?:utes)? faster"), "slower_min", lambda n, d: n == -d),
    (re.compile(r"\+(\d+) min"), "slower_min", lambda n, d: n == d),
    (re.compile(r"(\d+) min(?:utes)? (?:longer|slower)"), "slower_min", lambda n, d: n == d),
    (re.compile(r"\+(\d+) kcal"), "extra_kcal", lambda n, d: n == d),
)


def explanation(resp):
    """Every number in `why` must come from the facts (grounding), and every trade-off it
    states must be true and point the right way — "saves" vs "costs more" (faithfulness)."""
    fails = []
    base = resp["baseline"]
    for o in resp["options"]:
        why, d = o.get("why") or "", deltas(o, base)
        if not why:
            fails.append(f"{o['label']}: empty explanation")
            continue
        # Same facts the Explainer is given — including whole-minute totals — minus the sentence itself.
        facts = json.dumps({"option": {k: v for k, v in o.items() if k != "why"}, "baseline": base, "deltas": d,
                            "minutes": round(o["total_min"]), "baseline_minutes": round(base["total_min"]),
                            "abs": {k: abs(v) for k, v in d.items()}})
        if not grounded(why, facts):
            fails.append(f"{o['label']}: ungrounded number in “{why}”")
        for pattern, key, ok in CLAIMS:
            for m in pattern.finditer(why):
                if not ok(float(m.group(1)), d[key]):
                    fails.append(f"{o['label']}: claims “{m.group(0)}” but {key} = {d[key]}")
    return fails


# ── Label honesty: chips match the numbers ───────────────────────────────────

def labels(resp):
    fails, opts = [], resp["options"]
    for o in opts:
        lbl = o["label"]
        if "Fastest" in lbl and any(p["total_min"] < o["total_min"] - EPS for p in opts):
            fails.append(f"“{lbl}” is not the fastest option shown")
        if "Cheapest" in lbl and any(p["cost"] < o["cost"] - EPS for p in opts):
            fails.append(f"“{lbl}” is not the cheapest option shown")
        if "Healthiest" in lbl and any(p["kcal"] > o["kcal"] for p in opts):
            fails.append(f"“{lbl}” is not the most active option shown")
    return fails


# ── Mode correctness: the top pick obeys and serves its Mode ─────────────────

def complies(trip, spec, feasible):
    """Does a trip satisfy a Mode's hard rules (judged against all feasible trips)?"""
    walks = [l["distance_mi"] for l in trip["legs"] if l["mode"] == "walk"]
    if any(w > spec["max_walk_leg_mi"] + EPS for w in walks) or sum(walks) > spec["max_total_walk_mi"] + EPS:
        return False
    if spec.get("needs_transit"):
        if not any(m in TRANSIT for m in modes_of(trip)) or active_share(trip) < spec["min_active_share"] - EPS:
            return False
        transit = [t for t in feasible if any(m in TRANSIT for m in modes_of(t))]
        if transit and trip["total_min"] > min(t["total_min"] for t in transit) * spec["max_slowdown_vs_fastest_transit"] + EPS:
            return False
    if spec.get("max_slowdown"):
        if trip["total_min"] > min(t["total_min"] for t in feasible) * spec["max_slowdown"] + EPS:
            return False
    return True


def mode_correctness(mode, resp, feasible, spec):
    feasible = [trip_dict(t) for t in feasible]
    top, notice = resp["options"][0], resp.get("notice") or ""
    ok = [t for t in feasible if complies(t, spec, feasible)]
    if not ok:
        return [] if notice else [f"no trip complies with {mode} but no notice was shown"]
    fails = []
    if notice:
        fails.append(f"notice “{notice}” shown although {len(ok)} trips comply")
    if not complies(top, spec, feasible):
        fails.append(f"top pick ({'+'.join(modes_of(top))}, {top['total_min']} min, "
                     f"{top['walk_mi']} mi walk) breaks {mode}'s rules")
    if mode == "hustle":
        best = min(t["total_min"] for t in ok)
        if top["total_min"] > best * (1 + spec["top_within_fastest_pct"] / 100) + EPS:
            fails.append(f"top pick {top['total_min']} min vs fastest compliant {best} min")
    elif mode == "broke":
        no_ride = [t for t in ok if "ride" not in modes_of(t)]
        if spec.get("ride_last_resort") and no_ride and "ride" in modes_of(top):
            fails.append(f"top pick uses a ride though {len(no_ride)} ride-free trips comply")
        pool = no_ride or ok
        best = min(t["cost"] for t in pool)
        if top["cost"] > best + spec["top_within_cheapest_usd"] + EPS:
            fails.append(f"top pick ${top['cost']:.2f} vs cheapest compliant ${best:.2f}")
    elif mode == "burn":
        best = max(t["kcal"] for t in ok)
        if top["kcal"] < best * (1 - spec["top_within_max_kcal_pct"] / 100) - EPS:
            fails.append(f"top pick {top['kcal']} kcal vs most active compliant {best} kcal")
    return fails


GOAL = {"hustle": ("total_min", min, "min"), "broke": ("cost", min, "$"), "burn": ("active_min", max, "active min")}


def cross_mode(tops, notices, spec, feasible):
    """Same request, different Modes: each Mode's pick must do at least as well on its own
    goal as any other Mode's pick that *also obeys this Mode's rules* — Burn's 1.6-mile walk
    to save $1.64 isn't a trip Broke was allowed to choose."""
    fails, feasible = [], [trip_dict(t) for t in feasible]
    for mode, mine in tops.items():
        if notices.get(mode):
            continue
        field, better, unit = GOAL[mode]
        for other, theirs in tops.items():
            if other == mode or not complies(theirs, spec[mode], feasible):
                continue
            if mode == "broke" and "ride" in modes_of(theirs) and "ride" not in modes_of(mine):
                continue                     # a ride-free pick wins Broke even if it costs a bit more
            if better(mine[field], theirs[field]) != mine[field] and abs(mine[field] - theirs[field]) > EPS:
                fails.append(f"{mode.title()} pick ({mine[field]} {unit}) loses to {other.title()}'s "
                             f"({theirs[field]} {unit}), which also obeys {mode.title()}'s rules")
    return fails


# ── Hand-written expectations for a golden case ──────────────────────────────

def expectations(case, by_mode):
    fails, exp = [], case.get("expect") or {}
    every = [o for r in by_mode.values() for o in r["options"]]
    if exp.get("no_bikes"):
        fails += [f"{o['label']} uses bike share in rain" for o in every
                  if {"bike", "ebike"} & set(modes_of(o))]
    if "max_cost" in exp:
        fails += [f"{o['label']} costs ${o['cost']:.2f} > ${exp['max_cost']}" for o in every
                  if o["cost"] > exp["max_cost"] + EPS]
    if exp.get("no_steep_walks"):
        from app.tools import STEEP_GRADE
        fails += [f"{o['label']} walks a {l['grade']:.0%} grade" for o in every for l in o["legs"]
                  if l["mode"] == "walk" and l["grade"] > STEEP_GRADE + EPS]
    broke_top = by_mode["broke"]["options"][0] if "broke" in by_mode else None
    if broke_top and exp.get("broke_top_uses"):
        missing = set(exp["broke_top_uses"]) - set(modes_of(broke_top))
        if missing:
            fails.append(f"Broke top pick ({'+'.join(modes_of(broke_top))}) doesn't use {', '.join(missing)}")
    if broke_top and exp.get("broke_top_no_ride") and "ride" in modes_of(broke_top):
        fails.append(f"Broke top pick uses a ride ({'+'.join(modes_of(broke_top))})")
    return fails


# ── Intent extraction ────────────────────────────────────────────────────────

def intent(case, parsed):
    fails = []
    for k, want in case["expect"].items():
        got = parsed.get(k)
        if k.endswith("_text") and isinstance(got, str):
            same = want.lower() in got.lower()        # "Big Ben, London" still geocodes to Big Ben
        elif isinstance(want, str) and isinstance(got, str):
            same = got.strip().lower() == want.lower()
        elif isinstance(want, (int, float)) and not isinstance(want, bool):
            same = isinstance(got, (int, float)) and abs(got - want) < EPS
        else:
            same = got == want
        if not same:
            fails.append(f"{k}: expected {want!r}, got {got!r}")
    for k in case.get("absent", []):
        if parsed.get(k) is not None:
            fails.append(f"{k}: expected none, got {parsed[k]!r}")
    return fails
