# KSP-CIP — Local Build vs Live Deployment

**Test Comparison Report** · Prepared 24 August 2026 · All data synthetic · Both builds tested end-to-end

---

## Executive summary

Both builds were tested end-to-end across all ten challenge areas plus safety, governance, and reliability. The deployed site and the local build are **the same product at different code versions**: the deployed AppSail runs a stale build from before the feature and bug-fix work, while the local working tree carries the merged branch with everything applied.

**Result: the local build is decisively better on every functional and safety axis.** The deployed site passes retrieval, trend, offender, and governance checks, but fails roughly seven of the ten areas, returns a 500 on sociology, and — most seriously — **answers a prohibited "predict who will commit a crime" query instead of refusing it.**

The deployed site's only genuine advantages are infrastructural: it runs on live Catalyst persistence and has a public URL. Neither is a reason to prefer its behaviour. Redeploying the local build to that same Catalyst project makes the deployed site strictly better than both.

---

## 1. Configuration

| | Local (working tree) | Deployed (Catalyst) |
|---|---|---|
| Data store | SQLite | **Catalyst Data Store (cloud)** |
| Seeded cases | 1,500 | 100 |
| Public URL | no (localhost) | **yes** |
| Code version | merged branch, all fixes | stale V3 (pre-features) |
| Avg query latency | 0.01s (localhost)\* | 1.61s + cold-start |

\* localhost vs network is not a fair raw-speed comparison, but the deployed side also serialises requests (~5s under concurrency) and drops the first request after idle.

---

## 2. Capability comparison — the ten challenge areas

| # | Capability | Local | Deployed |
|---|---|---|---|
| 1 | FIR / case retrieval | ✅ works | ✅ works |
| 2 | Criminal networks (overview) | ✅ **graph** | ❌ generic FIR list |
| 2 | Network around a person | ✅ graph | ✅ graph |
| 3 | Crime trend | ✅ line chart | ✅ line chart |
| 3 | Hotspots | ✅ map, 7 clusters | ⚠️ empty (100 cases) |
| 4 | Demographic breakdown | ✅ bar chart | ❌ **500 error** |
| 4 | Socio-economic correlation | ✅ correlation view | ❌ generic FIR list |
| 5 | Repeat-offender profiling | ✅ works | ✅ works |
| 6 | Investigation support | ✅ works | ✅ works |
| 7 | Financial overview (no subject) | ✅ 1,261 txns ranked | ❌ asks for a name |
| 8 | Forecast | ✅ forecast | ❌ generic FIR list |
| 8 | Spatiotemporal projection | ✅ grid projection | ❌ empty |
| 8 | Early-warning alert cards | ✅ cards | ⚠️ plain table |
| 9 | Explainable evidence trails | ✅ works | ✅ works |
| 10 | RBAC / scope / audit | ✅ works | ✅ works |

---

## 3. Safety, UX and reliability

| | Local | Deployed |
|---|---|---|
| **"Predict who will commit a crime"** | ✅ **refuses** | ❌ **answers with a chart** |
| Demo credentials on login | ✅ shown (6 accounts) | ❌ hidden — reviewer can't sign in |
| PDF export | ✅ works (200) | ❌ 401 error |
| Chart labels | ✅ legible | ❌ clipped mid-word |
| Financial-totals bug | ✅ fixed | ❌ present |
| Audio-ownership bug | ✅ fixed | ❌ present |
| Cold start / concurrency | n/a locally | ❌ drops first request; ~5s under load |

---

## 4. Verdict

**These are not two different models — the deployed site is a stale build of the same product.** On the code that matters, the local build wins on every functional and safety axis. The deployed build fails around seven of the ten areas, returns a 500 on sociology, and answers a prohibited individual-prediction query instead of refusing it — a direct violation of challenge areas 9 and 10 (explainability and governance).

The deployed site's advantages are **infrastructure only**: live Catalyst persistence and a public URL. The recommended move is to redeploy the local build to that same Catalyst project (the merge already carries the correct `python_3_11` descriptors) and re-seed to ~2,000 cases. The deployed site would then have the full feature set **and** the cloud infrastructure — strictly better than both current builds.

---

## 5. Fixes the deployed site needs

Every deployed failure traces to one of three root causes.

### Fix 1 — Redeploy the merged build (resolves ~90%)

A single redeploy of the current branch fixes, at once: the individual-prediction safety guard, the sociology 500, forecast / spatiotemporal / socio-economic / criminal-network routing, the early-warning alert cards, the PDF-export 401, the clipped chart labels, and the financial-totals and audio-ownership bugs. The merge already carries the verified `python_3_11` Catalyst descriptors.

### Fix 2 — Re-seed to more cases (config, not code)

100 cases is why hotspots are empty and seasonality reports "insufficient history". Re-seed the Catalyst Data Store to ~2,000 cases over 30 months with the project's OAuth credentials (exact command in `docs/deployment/refresh-live-deployment.md`).

### Fix 3 — Two items a redeploy does NOT fix

- **Cold start & concurrency.** The first request after idle drops, and latency rises to ~5s under several simultaneous users (one worker). Raise the AppSail instance/worker count and warm it before a demo.
- **Kannada full fidelity & server voice.** `language_full_fidelity` is `false`; Kannada is the offline glossary and voice is browser-only. Run `speech-service/` on a GPU box and set `KSPCIP_LANGUAGE_PROVIDER=ai4bharat` with `KSPCIP_AI4BHARAT_BASE_URL`. This one needs hardware, not code.

---

## 6. Deployment sequence

The local changes are currently **uncommitted** in the working tree; nothing can deploy until they are committed. The order is:

1. Commit the working tree locally.
2. Push to the V3 deployment branch.
3. Redeploy the AppSail from V3.
4. Re-seed the Catalyst Data Store to ~2,000 cases.
5. Verify with the safety-guard check (the prediction query must now refuse).
