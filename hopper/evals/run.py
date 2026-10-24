"""Run Hopper's agent evals.

    python -m evals.run                     # offline: deterministic, free — what CI runs
    python -m evals.run --live              # also grades Claude (Intent + Explainer); needs ANTHROPIC_API_KEY
    python -m evals.run --update-golden     # re-record evals/golden.json after an intended change
    python -m evals.run --report summary.md # append a Markdown report (CI: $GITHUB_STEP_SUMMARY)

Live feeds (511, NWS, OSRM, GBFS) are always off, so every run plans on the same fixture
data and the numbers are reproducible; --live only switches the two Claude agents on.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

os.environ["HOPPER_OFFLINE"] = "1"          # before app imports: fixture data, no network

from app import agents, guard                # noqa: E402
from evals import checks                     # noqa: E402

HERE = Path(__file__).parent
CASES = json.loads((HERE / "cases.json").read_text())
GOLDEN_PATH = HERE / "golden.json"
MODES = ("hustle", "broke", "burn")
TOL = {"total_min_pct": 10, "cost_usd": 0.5, "kcal_pct": 15}


def plan(case, mode):
    body = {k: case.get(k) for k in ("origin", "destination", "hour", "weather", "event",
                                     "avoid_steep", "max_cost")}
    body = {k: v for k, v in body.items() if v is not None}
    resp, feasible, *_ = agents.orchestrate({**body, "mode": mode, "_visitor": "evals"})
    return resp, feasible


def fingerprint(option):
    return {"legs": "+".join(f"{l['mode']}:{l['line']}" if l["line"] else l["mode"] for l in option["legs"]),
            "total_min": option["total_min"], "cost": option["cost"], "kcal": option["kcal"]}


def golden_diff(want, got):
    fails = []
    if want["legs"] != got["legs"]:
        fails.append(f"route changed: {want['legs']} → {got['legs']}")
    if abs(got["total_min"] - want["total_min"]) > want["total_min"] * TOL["total_min_pct"] / 100:
        fails.append(f"time {want['total_min']} → {got['total_min']} min")
    if abs(got["cost"] - want["cost"]) > TOL["cost_usd"]:
        fails.append(f"cost ${want['cost']:.2f} → ${got['cost']:.2f}")
    if abs(got["kcal"] - want["kcal"]) > max(10, want["kcal"] * TOL["kcal_pct"] / 100):
        fails.append(f"kcal {want['kcal']} → {got['kcal']}")
    return fails


class Report:
    def __init__(self):
        self.results, self.metrics = [], {}

    def add(self, suite, case, fails):
        self.results.append({"suite": suite, "case": case, "ok": not fails, "fails": fails})

    @property
    def failures(self):
        return [r for r in self.results if not r["ok"]]

    def markdown(self, live):
        suites = {}
        for r in self.results:
            s = suites.setdefault(r["suite"], [0, 0])
            s[0] += r["ok"]
            s[1] += 1
        lines = [f"## Hopper agent evals ({'live Claude' if live else 'offline'})", "",
                 "| Suite | Passed |", "|---|---|"]
        lines += [f"| {s} | {ok}/{n} {'✅' if ok == n else '❌'} |" for s, (ok, n) in suites.items()]
        if self.metrics:
            lines += ["", "| Metric | Value |", "|---|---|"]
            lines += [f"| {k} | {v} |" for k, v in self.metrics.items()]
        if self.failures:
            lines += ["", "### Failures", ""]
            lines += [f"- **{r['suite']} · {r['case']}**: {'; '.join(r['fails'])}" for r in self.failures]
        return "\n".join(lines) + "\n"


def run_trips(report, golden, record):
    spec = CASES["mode_spec"]
    llm_items = llm_dropped = 0
    for case in CASES["trips"]:
        by_mode, feasible_by_mode = {}, {}
        for mode in MODES:
            name = f"{case['id']}/{mode}"
            try:
                resp, feasible = plan(case, mode)
            except agents.PlanError as e:
                report.add("integrity", name, [f"planner refused: {e}"])
                continue
            by_mode[mode], feasible_by_mode[mode] = resp, feasible
            report.add("integrity", name, checks.integrity(resp))
            report.add("explanation", name, checks.explanation(resp))
            report.add("labels", name, checks.labels(resp))
            report.add("mode", name, checks.mode_correctness(mode, resp, feasible, spec[mode]))
            got = fingerprint(resp["options"][0])
            if record:
                golden[name] = got
            elif name in golden:
                report.add("golden", name, golden_diff(golden[name], got))
            else:
                report.add("golden", name, ["no golden recorded — run with --update-golden"])
            for step in resp["trace"]:
                if step["agent"] == "Explainer" and step["kind"] == "llm":
                    m = re.match(r"(\d+) explanations(?:, (\d+) failed)?", step["detail"])
                    if m:
                        llm_dropped += int(m.group(2) or 0)
                        llm_items += int(m.group(1)) + int(m.group(2) or 0)
        if len(by_mode) == len(MODES):
            report.add("cross-mode", case["id"],
                       checks.cross_mode({m: r["options"][0] for m, r in by_mode.items()},
                                         {m: r.get("notice") for m, r in by_mode.items()},
                                         spec, feasible_by_mode["hustle"]))
        if case.get("expect"):
            report.add("expectations", case["id"], checks.expectations(case, by_mode))
    if llm_items:
        report.metrics["Explainer: Claude sentences grounded on first try"] = \
            f"{llm_items - llm_dropped}/{llm_items} ({100 * (llm_items - llm_dropped) / llm_items:.0f}%)"


def run_intents(report, client):
    for case in CASES["intents"]:
        if case.get("llm_only") and client is None:
            continue
        parsed = agents.intent_agent(case["q"], agents.Trace(), client, case.get("sf_context", True))
        report.add("intent (Claude)" if client else "intent (rules)", case["q"], checks.intent(case, parsed))


def run(live=False, update_golden=False):
    report = Report()
    client = None
    if live:
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise SystemExit("--live needs ANTHROPIC_API_KEY")
        from anthropic import Anthropic
        client = Anthropic(api_key=key)
        agents._llm = lambda: client                         # Claude on, live feeds still off
        guard.CLAUDE_DAILY_CAP = guard.CLAUDE_VISITOR_DAILY_CAP = 10 ** 6   # the eval isn't a visitor
    golden = {} if update_golden else (json.loads(GOLDEN_PATH.read_text()) if GOLDEN_PATH.exists() else {})
    run_trips(report, golden, record=update_golden)
    run_intents(report, None)                                # the rules parser is always graded
    if client:
        run_intents(report, client)
    if update_golden:
        GOLDEN_PATH.write_text(json.dumps(dict(sorted(golden.items())), indent=1) + "\n")
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true", help="grade the Claude agents too")
    ap.add_argument("--update-golden", action="store_true", help="re-record evals/golden.json")
    ap.add_argument("--report", help="append a Markdown report to this file")
    ap.add_argument("--json", help="write every result as JSON to this file")
    args = ap.parse_args(argv)
    report = run(args.live, args.update_golden)
    md = report.markdown(args.live)
    print(md)
    if args.report:
        with open(args.report, "a") as f:
            f.write(md)
    if args.json:
        Path(args.json).write_text(json.dumps({"results": report.results, "metrics": report.metrics}, indent=1))
    return 1 if report.failures else 0


if __name__ == "__main__":
    sys.exit(main())
