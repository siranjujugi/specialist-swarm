"""Hopper Flask server: /api/plan runs the swarm, /api/rerank re-runs only the Ranker."""
import os
import uuid
from collections import OrderedDict
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory


def load_dotenv(path=Path(__file__).resolve().parent.parent / ".env"):
    """Minimal KEY=VALUE loader so FIVE11_API_KEY can live in hopper/.env (git-ignored).
    Real environment variables always win."""
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_dotenv()

from .agents import PlanError, orchestrate, rerank  # noqa: E402 — needs env loaded first
from .data import MODE_COLORS, PLACES

STATIC = Path(__file__).parent / "static"
app = Flask(__name__, static_folder=str(STATIC))
PLANS = OrderedDict()   # plan_id → (feasible trips, explanations, baseline); small LRU


@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.get("/api/places")
def places():
    return jsonify({"places": [{"key": k, "name": v[0], "lat": v[1], "lon": v[2], "elev_ft": v[3]}
                               for k, v in sorted(PLACES.items(), key=lambda kv: kv[1][0])],
                    "colors": MODE_COLORS})


@app.post("/api/plan")
def plan():
    try:
        result, feasible, why, baseline = orchestrate(request.get_json(force=True) or {})
    except PlanError as e:
        return jsonify({"error": str(e)}), 422
    plan_id = uuid.uuid4().hex[:10]
    PLANS[plan_id] = (feasible, why, baseline)
    while len(PLANS) > 50:
        PLANS.popitem(last=False)
    return jsonify({**result, "plan_id": plan_id})


@app.post("/api/rerank")
def rerank_plan():
    body = request.get_json(force=True) or {}
    cached = PLANS.get(body.get("plan_id"))
    if not cached:
        return jsonify({"error": "Plan expired — search again."}), 404
    feasible, why, baseline = cached
    options, trace = rerank(feasible, body.get("weights") or {}, why, baseline)
    return jsonify({"options": options, "trace": trace})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5080)), debug=False)
