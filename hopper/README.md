# Hopper — SF multimodal commute prototype

Uber-style trip planner that mixes **walk + ride-hail + Muni/BART/cable car + Bay Wheels**
in one trip, and ranks options by **time, cost, and health** (kcal, steps, climb).
San Francisco only.

## Run

```bash
cd hopper
PORT=5080 python3 -m app.server        # needs flask + anthropic
```

Open http://localhost:5080. With `ANTHROPIC_API_KEY` set, the Intent and Explainer agents
use Claude (`claude-haiku-5-5`); without it (or with `HOPPER_OFFLINE=1`) they fall back to
a rule parser and templates. Every number is identical in both modes.

## Modes

Pick one at the top (or let the Intent agent infer it from your question):

| Mode | Goal | Rules on top of the Critic |
|---|---|---|
| ⚡ **Hustle** | Fastest | Walk ≤ 0.4 mi per leg, ≤ 0.8 mi total; weights time 3 · cost 0.5 · health 0 |
| 💸 **Broke** | Budget | Walk ≤ 1.5 mi per leg; ride-hail only if nothing else works; ≤ 2.5× the fastest trip |
| 🍸 **Bar Crawl** | Hit cool bars | 21+ gate; 2–5 stops; walk-only hops (≤ 0.5 mi, ≤ 0.25 mi in rain); simulated ride home |
| 🔥 **Burn** | Part transit / part workout | Must include a transit leg AND ≥ 40% active minutes; ≤ 2× the fastest *transit* trip |

- Plans are built once with the loosest limits; switching Hustle / Broke / Burn re-runs only
  the Ranker on cached trips (`/api/rerank` with `mode`). "Advanced" sliders = Custom mode.
- If nothing meets a Mode's rules, the closest options are shown with a notice.
- **Bar Crawl** (`/api/crawl`) uses OpenStreetMap bars — live from Overpass (cached 7 days),
  falling back to the bundled snapshot `app/fixtures/sf_bars.json` (497 bars, © OpenStreetMap
  contributors, ODbL). OSM has **no ratings**: the cool score uses landmark status
  (Wikipedia/Wikidata), how thoroughly the bar is mapped, website / Instagram / hours,
  outdoor seating, and your vibe (cocktail, craft beer, dive, wine, live music, sports,
  LGBTQ+, landmarks). Opening hours are shown, not validated — check before you go.

Tests: `python3 -m pytest -q tests` (no network — live feeds are tested with sample payloads)

## Live data (free, no Google)

| Feed | Used for | Key | Fallback |
|---|---|---|---|
| **511 SF Bay** StopMonitoring + service alerts | Real Muni/BART/cable-car wait times; ⚠ alert flags on trips | `FIVE11_API_KEY` (free: https://511.org/open-data/token) | Schedule headway ÷ 2 |
| **NWS** api.weather.gov | Hourly forecast at the origin → rain/fog/clear, wind (> 20 mph turns bikes off) | none | Assume clear (or pick weather manually) |
| **Bay Wheels GBFS** | Real classic / e-bike counts and open docks at the nearest station | none | Deterministic pseudo-availability |
| **OSRM** (routing.openstreetmap.de foot / bike / car) | Real street routes for walk, bike, and ride legs: distance, drive time (× traffic factor 1.35, 1.8 at rush hour), and the polyline drawn on the map | none | Straight-line estimates |

**Routing flow:** the Critic first prunes candidates on cheap estimates; surviving trips and
the door-to-door baseline get OSRM street routes (4 in parallel, ≤ 60 unique legs per plan,
cached on disk for a day); then Health and the Critic re-run on the real distances before
ranking — so cards, labels, explanations, and the map all use the street route.
The FOSSGIS servers are a shared community service: keep volume low, or point
`OSRM_BASE_URL` at your own `osrm-backend`. `HOPPER_NO_OSRM=1` turns routing off.

- Put keys in `hopper/.env` (see `.env.example`; git-ignored). Real env vars win.
- Live arrivals and docks are used only when the trip leaves **this hour** (SF time);
  future hours use schedules.
- 511's free tier is ~60 calls/hour: stop lists are cached on disk for a day, arrivals for
  90 s, alerts for 10 min; at most 4 arrival lookups per plan; a rolling budget
  (`FIVE11_HOURLY_BUDGET`, default 50) stops calls before 511 refuses them.
- `HOPPER_OFFLINE=1` turns off every live feed and Claude.
- The UI shows a weather strip and badges saying which sources were live for each plan.

## Agent swarm

```mermaid
flowchart LR
  Q[Rider request] --> I[Intent agent<br/>Claude Haiku]
  I --> C[Context agent<br/>weather / dark / event]
  C --> W[Walk & Terrain]
  C --> R[Ride-hail]
  C --> T[Transit]
  C --> M[Micromobility]
  W & R & T & M --> H[Health agent<br/>kcal · steps · climb]
  H --> K[Critic<br/>feasibility gate]
  K --> P[Ranker<br/>Pareto + weights]
  P --> E[Explainer<br/>Claude Haiku]
  E --> UI[3 trip cards + map]
```

- **LLM agents do language** (understand the request, explain the trade-off).
- **Deterministic tools do numbers** (`app/tools.py`): fares, minutes, calories, ranking,
  validation. The Critic rejects any leg whose numbers weren't produced by a tool.
- Specialists fan out in parallel. Moving the sliders re-runs **only the Ranker** (`/api/rerank`).
- The UI's "Agent swarm trace" panel shows each agent's model, latency, and tokens.

## Health math

- Walking: ACSM walking equation (net kcal, resting excluded), Tobler's hiking function for
  uphill/downhill speed, 0.762 m stride for steps.
- Cycling: MET 6.8 classic (plus hill load), 4.0 e-bike.

## Known limitations (prototype)

- Places are a fixed gazetteer of ~40 SF landmarks (no free-text geocoding yet).
- Street routes come from OSRM; transit legs are still drawn stop-to-stop, and OSRM car
  times have no live traffic (a fixed traffic factor is applied).
- Hill grade still uses landmark elevations, not the street route's elevation profile.
- Transit routes are a hand-built subset of 7 lines with fixed segment times; single line
  per trip, no transfers. 511 supplies live waits and alerts, not routing. Next step:
  OpenTripPlanner 2 on 511 GTFS + OpenStreetMap (needs Docker or Java 21+).
- 511 direction matching uses the vehicle's destination stop; BART trains aren't filtered
  by branch (an SFO trip could see a Millbrae-only train's ETA).
- **Ride-hail prices are simulated** (base + mile + minute + fee × surge zone). No Uber/Lyft API.
- Bike dock availability is pseudo-random per hour (reproducible), not live GBFS.
- Hills use average grade between endpoints, which understates individual steep blocks.
- Booking is a mock confirmation; no rides are requested and no payment is taken.
