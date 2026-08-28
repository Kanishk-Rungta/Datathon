# Forecasting and spatial projection (V2.1 M3)

Two aggregate, planning-only capabilities: how many cases to expect, and
where they are likely to concentrate. Both are projections of recorded
history, never predictions about a person, and both say so on every output.

---

## 1. Two different questions, two different answers

| Question | Intent | Method | Not to be confused with |
|---|---|---|---|
| "How many cases next quarter?" | `FORECAST_QUERY` | seasonal-naive / rolling-rate, backtest-selected | `TREND_QUERY` (describes the past), `SEASONAL_QUERY` (calendar recurrence, no projection) |
| "Where will it concentrate?" | `SPATIOTEMPORAL_QUERY` | spatial Poisson / exponential smoothing over the hotspot grid | `HOTSPOT_QUERY` (describes concentration *already recorded*) |

Both are reached only when the question is genuinely forward-looking. The
classifier requires an explicit future-tense phrase for both intents — "where
are the hotspots" stays `HOTSPOT_QUERY`; "where will crime concentrate next
month" is `SPATIOTEMPORAL_QUERY`. See
`test_nlu_routing.py::test_spatial_forecast_does_not_swallow_hotspots_or_forecast`
and the equivalent `FORECAST_QUERY` test.

---

## 2. Count forecasting — `AnalyticsEngine.forecast()`

### Method selection is earned, not chosen

Two reproducible baselines compete on **rolling-origin backtest** — each
method forecasts months it was not shown, and mean absolute error decides:

- **`rolling-rate`** — next month looks like the mean of the last 3 observed
  months. Always available once there is enough history.
- **`seasonal-naive`** — next month looks like the same month a year ago.
  Only offered once there are ≥18 months of history (a prior year plus enough
  remaining months to backtest against).

On a tie, `rolling-rate` wins — seasonality unearned is not assumed. If
neither method beats simply assuming the long-run average, the result says so
explicitly in its caveat rather than presenting a number that "won" a contest
against nothing.

### The interval is measured, not assumed

The confidence range comes from the **selected method's own backtest
residuals** — "this method has historically been wrong by about this much on
this series" — not from a distributional assumption. It widens with distance
from the last observed month, because each further step forecasts partly from
previous forecasts.

### Guards

| Condition | Behaviour |
|---|---|
| Fewer than 6 months of history | `insufficient_history=True`, no figure published |
| Series averages fewer than 3 cases/month **recently** (not historically) | `sparse=True`; caveat says to read the range, not the midpoint |
| A gap month with zero cases | Counted as an observation (zero), never dropped — the same discipline the financial-burst analysis uses for calendar days |
| Neither method beats a flat average | Stated in the caveat, not hidden |

Sparsity is judged on the **recent** level, not the long-run mean — a series
that averaged 0.8/month over three years but is currently running at 7/month
is not one a single incident destabilises, and judging it on the long-run
average was a bug caught by
`test_forecast.py::test_sparsity_is_judged_on_the_recent_level_not_the_long_run`.

### What it will not do

Asking it to forecast a **person** — "predict which person will commit
theft", "will Ramesh reoffend" — is refused before routing, in
`SupervisorAgent._refuse_individual_prediction`, because the same prohibited
phrasing classifies onto `FORECAST_QUERY`, `TREND_QUERY`, or `GENERAL_QA`
depending on wording; an intent-local guard could not cover all three.
Recorded-history questions about a person ("what has Ramesh been charged
with") stay answerable — only the future tense is refused. See
`INDIVIDUAL_PREDICTION_RE` in `application/agents/base.py`.

---

## 3. Spatial projection — `SpatioTemporalForecaster`

Projects expected incident counts per hotspot grid cell over a 7–90 day
horizon, using a spatial Poisson / exponential-smoothing model over the same
grid the (observed) hotspot analysis uses. Each cell reports an expected
count, a range, a hotspot probability, and a risk band (`low`/`medium`/
`high`).

Aggregate by construction — grid cells, never people — and the same
individual-prediction refusal covers this path too.

**Not yet backtested against the same rolling-origin discipline as the count
forecast.** The count forecaster's method-selection contest is the model for
what this should have before its risk bands are trusted operationally; that
work is not done.

---

## 4. Both are rendered as projections, never as records

The console gives each its own payload type (`forecast`,
`spatiotemporal_forecast`) rather than reusing the observed-data chart types.
`ForecastProjection` and `SpatioTemporalForecast` in `PayloadView.jsx` render
a banded table with the method and caveat up front — a projection is never
drawn as the same line chart as recorded history. This was a real gap: the
forecast payload originally hid behind an `is_forecast` flag inside the `line`
case with no render branch, so it rendered blank. See
`test_payload_contract.py` and `test_api.py::TestPayloadsReachTheirRenderers`.

---

## 5. The socio-economic layer — built, and honestly bounded

`ext_socioeconomic_indicator` (one row per district per census year:
literacy, urbanisation, unemployment, poverty headcount, migration inflow,
population density) is **implemented**, not just designed — contrary to
earlier planning notes in this repo. The generator (`pipeline/generators/
socioeconomic.py`) produces plausible district-level approximations
*informed by* Census 2011, NSSO 68th round and Karnataka Economic Survey
ranges, labelled `data_quality='synthetic'` with a `data_source` field citing
what informed each approximation. It is not real data connected under an
approved governance contract — that remains the actual gap M4-01 describes.

`SocioEconomicCorrelator` (routed via `SOCIOECONOMIC_QUERY`) computes a
cross-district association between recorded crime counts and these
indicators and attaches a mandatory caveat on every output: *"This is a
cross-district association, not a causal relationship."* No output claims
urbanisation or poverty causes crime — only that they co-vary across
Karnataka's 31 districts in this synthetic approximation.

**What would need to change before this counts as the real thing:** an
approved external dataset, a named governance owner, and — per M4-01 — small-
cell suppression and an aggregate-only join, both of which the existing
`sociology()` guardrail already implements and this layer should reuse rather
than reinvent.

---

## 6. Where to look

| Concern | File |
|---|---|
| Count forecast, backtest, guards | `application/analytics/engine.py::forecast`, `_backtest`, `_project` |
| Spatial projection | `application/analytics/spatiotemporal.py` |
| Intent routing (both) | `application/nlu/classifier.py`, `application/agents/supervisor.py::INTENT_ROUTING` |
| Individual-prediction refusal | `application/agents/base.py::INDIVIDUAL_PREDICTION_RE`, `supervisor.py::_refuse_individual_prediction` |
| Console rendering | `frontend/src/components/PayloadView.jsx` (`ForecastProjection`, `SpatioTemporalForecast`) |
| Socio-economic generator and correlator | `application/pipeline/generators/socioeconomic.py`, `application/analytics/socioeconomic.py` |
| Tests | `tests/unit/test_forecast.py`, `tests/unit/test_spatiotemporal.py`, `tests/unit/test_socioeconomic.py`, `tests/unit/test_payload_contract.py` |
