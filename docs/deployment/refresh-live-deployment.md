# Refreshing the live Development deployment

Two things make the deployed console current after the V2.1→V3 merge: a
**redeploy** (new code + rebuilt console) and a **re-seed** (more than the 100
cases currently loaded). The merge and the console build are already done in
this working tree; the two steps below are the ops actions that touch the live
Catalyst project, and they need the project's OAuth credentials.

---

## 1. Why this is needed

Testing the live site (`cip-console-...development.catalystappsail.in`) on
2026-08-24 found it was built from V3 *before* the V2.1 features merged in:

- forecasting, spatiotemporal projection, organised-activity alerting and the
  socio-economic correlation all fell back to `GENERAL_QA` or empty results;
- **"predict which person will commit a crime" was answered with a trend
  chart instead of being refused** — the individual-prediction safety guard
  was not in the deployed build;
- `SOCIOECONOMIC_QUERY` returned a 500 (`evidence_missing`);
- early warning rendered as a plain table, not alert cards;
- only **100 cases** were seeded, so hotspots were empty and seasonality
  reported "insufficient history".

The merge fixes the first four (they are code). The last is data volume,
fixed by re-seeding.

---

## 2. Redeploy the code

From a machine with the Catalyst CLI authenticated to the Development project:

```bash
cd ksp-cip
# console is already built into frontend/dist by this branch; rebuild only if
# you changed frontend source since:
#   (cd frontend && npm ci && npm run build)

catalyst deploy            # or the project's usual appsail deploy command
```

Confirm the new build is live — the safety guard is the quickest signal:

```bash
B=https://cip-console-<id>.development.catalystappsail.in/api/v1
TOKEN=$(curl -s -X POST $B/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"analyst.state","password":"ChangeMe#2026"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

# must now REFUSE, not answer with a chart:
curl -s -X POST $B/chat -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"message":"Predict which person will commit a crime next month","session_id":"c1"}' \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d["intent"], "::", d["claims"][0]["text"][:80])'
# expect: the refusal wording "does not forecast whether a particular person will offend"

# forecasting must now route (not GENERAL_QA):
curl -s -X POST $B/chat -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"message":"Forecast cases for the next quarter","session_id":"c2"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["intent"])'
# expect: FORECAST_QUERY
```

---

## 3. Re-seed with a demo-sized dataset

100 cases is too few for the analytics to show signal. The seeder writes to
whatever `DataStore` is selected, so pointing it at Catalyst re-seeds the live
Data Store. Run it with the Development project's credentials set:

```bash
cd ksp-cip/backend

export KSPCIP_ENVIRONMENT=development
export KSPCIP_DATASTORE_BACKEND=catalyst
export KSPCIP_FILESTORE_BACKEND=catalyst
export KSPCIP_CATALYST_PROJECT_ID=...            # the Development project id
export KSPCIP_CATALYST_ENVIRONMENT=Development    # never Production
export KSPCIP_CATALYST_OAUTH_CLIENT_ID=...        # secret
export KSPCIP_CATALYST_OAUTH_CLIENT_SECRET=...    # secret
export KSPCIP_CATALYST_OAUTH_REFRESH_TOKEN=...    # secret
export KSPCIP_CATALYST_STRATUS_BUCKET=cip-ingest

# --reset clears the old rows first; 2000 cases over 30 months plants every
# signal (surge, hotspots, ring, repeat offenders, financial burst) with
# enough density for the analytics to recover them. 4200 is the default and
# is fine too if the load time is acceptable.
python -m ksp_cip.cli seed --cases 2000 --months 30 --reset
```

Then confirm the volume and that hotspots/seasonality now have material:

```bash
curl -s $B/health | python3 -c 'import sys,json;print("cases:",json.load(sys.stdin)["cases"])'
# expect: cases: 2000
```

> **The seed is idempotent on a deterministic key but `--reset` deletes the
> current rows first.** It is safe here because all data is synthetic. Never
> run this against a Production project.

---

## 4. Two smaller fixes already in this branch

- **Demo credentials on the login screen.** `/auth/demo-accounts` now lists the
  accounts and shared password in `development` as well as `local` (withheld in
  staging/production), so a reviewer can actually sign in. Pinned by
  `test_api.py::TestAuthentication::test_demo_accounts_are_listed_in_synthetic_environments`.

- **The `SOCIOECONOMIC_QUERY` 500.** The `payload_type` Literal now accepts the
  new renderer types; verify after redeploy with
  `{"message":"correlation with literacy across districts"}` — it should return
  200 with a `socioeconomic_correlation` payload, not a 500.

---

## 5. Things a redeploy does *not* fix

- **Cold start.** The first request after the AppSail instance idles can time
  out; a warm-up ping before a demo avoids the reviewer hitting it.
- **Concurrency.** One worker serialises requests, so latency rises to ~5s under
  several simultaneous users. Raise the AppSail instance/worker count if the
  demo will have concurrent reviewers.
- **Kannada full fidelity / server ASR.** `language_full_fidelity` is `false`
  on the deployment — Kannada is the offline glossary and voice uses the
  browser recogniser. Real ASR needs the `speech-service/` running with weights
  (a GPU box) and `KSPCIP_LANGUAGE_PROVIDER=ai4bharat` + `KSPCIP_AI4BHARAT_BASE_URL`
  pointed at it. See `docs/voice-ai4bharat.md`.
