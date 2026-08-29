# Deployment configuration

**No secret value appears in this file, and none may be added to it.** This is
an inventory of *setting names* and what selecting each one commits you to.
Values live in Catalyst configuration/secrets, never in the repository.

---

## 1. The zero-credential default

A clean checkout runs with no configuration at all:

```bash
python cip.py
```

That build uses SQLite, a local file store, a relational key/value store, the
in-process cache, the offline Kannada glossary, and the deterministic local LLM
provider. Every setting below exists to move one of those onto a hosted
service. Nothing is required until you choose to move something.

---

## 2. Backend selectors

Each port is selected independently. They are separate switches on purpose: a
deployment states what it is using rather than inheriting it.

| Variable | Values | Default | Selecting the non-default requires |
|---|---|---|---|
| `KSPCIP_DATASTORE_BACKEND` | `sqlite` \| `catalyst` | `sqlite` | project id + OAuth triple |
| `KSPCIP_FILESTORE_BACKEND` | `local` \| `catalyst` | `local` | project id, Stratus bucket |
| `KSPCIP_KEYVALUE_BACKEND` | `relational` \| `catalyst` | `relational` | project id |
| `KSPCIP_CACHE_BACKEND` | `memory` \| `catalyst` | `memory` | project id, cache segment |
| `KSPCIP_IDENTITY_BACKEND` | `local` \| `catalyst` | `local` | `KSPCIP_CATALYST_AUTH_ISSUER` |

**Enforced combination.** `DATASTORE_BACKEND=catalyst` with
`FILESTORE_BACKEND=local` is refused at startup. A conversation export written
to a Catalyst function's local disk is gone at the next cold start, and the
audit row would then cite a file nobody can fetch.

---

## 3. Catalyst connection

| Variable | Notes |
|---|---|
| `KSPCIP_CATALYST_PROJECT_ID` | Required by every Catalyst backend |
| `KSPCIP_CATALYST_ENVIRONMENT` | `Development` \| `Production`. Smoke tests refuse to run against `Production` |
| `KSPCIP_CATALYST_BASE_URL` | Defaults to the IN DC (`api.catalyst.zoho.in`) |
| `KSPCIP_CATALYST_ACCOUNTS_URL` | Defaults to `accounts.zoho.in` |
| `KSPCIP_CATALYST_OAUTH_CLIENT_ID` | **secret** |
| `KSPCIP_CATALYST_OAUTH_CLIENT_SECRET` | **secret** |
| `KSPCIP_CATALYST_OAUTH_REFRESH_TOKEN` | **secret** |
| `KSPCIP_CATALYST_STRATUS_BUCKET` | Default `cip-ingest` |
| `KSPCIP_CATALYST_NOSQL_TABLE` | Default `cip_kv` |
| `KSPCIP_CATALYST_CACHE_SEGMENT` | Cache segment name |
| `KSPCIP_CATALYST_AUTH_ISSUER` | Expected `iss` claim; required for Catalyst identity |
| `KSPCIP_CATALYST_AUTH_AUDIENCE` | Expected `aud` claim, if enforced |

Data residency: keep every URL on `.zoho.in`. The defaults already do.

---

## 4. External providers

| Variable | Notes |
|---|---|
| `KSPCIP_LANGUAGE_PROVIDER` | `local` \| `bhashini` \| `ai4bharat` |
| `KSPCIP_BHASHINI_USER_ID` / `KSPCIP_BHASHINI_API_KEY` | **secret**; both required for `bhashini` |
| `KSPCIP_BHASHINI_PIPELINE_ID` | Pipeline identifier |
| `KSPCIP_AI4BHARAT_BASE_URL` | Required for `ai4bharat`. **Not a secret** — it is the URL of a speech service you host yourself (`speech-service/`), so there is no vendor account or key. See [voice-ai4bharat.md](voice-ai4bharat.md) |
| `KSPCIP_AI4BHARAT_ASR_MODEL` / `_TTS_MODEL` / `_TTS_SPEAKER` | Model selection |
| `KSPCIP_AI4BHARAT_TIMEOUT_SECONDS` | Default 60 |
| `KSPCIP_VOICE_MAX_AUDIO_BYTES` | Default 10 MB. Provider-neutral ceiling on one uploaded utterance, enforced at the HTTP boundary *and* in the adapter |
| `KSPCIP_LLM_PROVIDER` | `local` \| `anthropic` \| `gemini` \| `groq` \| `openai_compatible` |
| `KSPCIP_LLM_API_KEY` | **secret**; required for any non-local provider |
| `KSPCIP_LLM_MODEL` / `KSPCIP_LLM_BASE_URL` | Provider-specific |

The hosted LLM providers and the Bhashini adapter are the only two outbound
paths that use `httpx` rather than stdlib `urllib`, and they import it lazily.
`httpx` is therefore a **runtime** dependency in `pyproject.toml` and in
`catalyst/appsail/api/requirements.txt`, even though the default configuration
never calls it — otherwise flipping one of these variables starts cleanly and
raises `ModuleNotFoundError` on the first real request. `cip_refresh` does not
carry it: that function neither composes an answer nor translates.

Before enabling any non-local LLM provider, `implementationv2.md` §11.2
requires legal sign-off and a redaction/data-residency review: case text would
leave Catalyst.

---

## 5. Security-relevant settings

| Variable | Notes |
|---|---|
| `KSPCIP_JWT_SECRET` | **secret**. Startup fails outside `local` if left at the placeholder |
| `KSPCIP_ENVIRONMENT` | `local` \| `development` \| `staging` \| `production` |
| `KSPCIP_CORS_ORIGINS` | Comma-separated; set to the deployed console origin only |
| `KSPCIP_ACCESS_TOKEN_TTL_SECONDS` | Default 12 h |
| `KSPCIP_AUDIT_RETENTION_DAYS` | Default 2557 (~7 years) |

---

## 6. Startup validation

`Settings.deployment_problems()` returns every configuration error as a
sentence, and `build_container()` raises with all of them at once rather than
surfacing them one restart at a time. It reports *setting names only* — never a
value — so the output is safe to paste into a ticket.

Check a configuration without starting the API:

```bash
python -m ksp_cip.cli config
```

---

## 7. Live smoke tests

`backend/tests/integration/test_deployment_smoke.py` is skipped unless **all**
of the following hold:

- `KSPCIP_SMOKE_ENABLED=1`
- `KSPCIP_CATALYST_PROJECT_ID` is set
- the OAuth triple is set
- `KSPCIP_CATALYST_ENVIRONMENT` is not `Production`

The tests write and delete rows, so the Production guard holds even when the
opt-in flag is set.

```bash
KSPCIP_SMOKE_ENABLED=1 scripts/test.sh backend/tests/integration/test_deployment_smoke.py
```

---

## 8. Build the deployment artifacts

Catalyst ships only the directory named by a component's `source`. Neither
entrypoint is self-contained in the checkout — the API needs the `ksp_cip`
package and the console needs `frontend/dist`, and both live outside their own
directory. Stage all three before deploying anything:

```bash
python cip.py package        # -> dist/cip-api, dist/cip-refresh, dist/cip-console
```

That builds the console first if it has not been built, then stages all three.
The underlying script takes one target at a time if you need that:
`python scripts/build_catalyst_artifact.py --target console`.

Each Python target is verified by importing its own module set with `sys.path`
containing only the staging directory, under `python -I`. The console target is
verified by checking `server.js` and `dist/index.html` are both inside it. Every
artifact gets a `.manifest.json` with per-file SHA-256.

`catalyst.json` records `source` (where a component is maintained),
`deploy_source` (what to ship) and `build` (the command that produces it) for
each component. Point `catalyst appsail:add --source` at `deploy_source`.

| Component | `deploy_source` | Runtime env it needs |
|---|---|---|
| `cip-api` | `dist/cip-api` | Catalyst project id + OAuth triple; `KSPCIP_JWT_SECRET` |
| `cip-console` | `dist/cip-console` | `CIP_API_URL` pointing at the deployed `cip-api` |
| `cip_refresh` | `dist/cip-refresh` | same as `cip-api`, minus the JWT secret |

`catalyst/_bootstrap.py` runs first in both Python entrypoints. It defaults
`KSPCIP_DATASTORE_BACKEND` **and** `KSPCIP_FILESTORE_BACKEND` to `catalyst`
together (the validator refuses the mixed pair), and derives a valid
`KSPCIP_ENVIRONMENT` from `KSPCIP_CATALYST_ENVIRONMENT`. Every one is a
`setdefault`, so anything you export explicitly wins.

---

## 9. Provisioning order

1. Create separate Catalyst **Development** and **Production** projects.
2. Provision Data Store tables from `backend/ksp_cip/infrastructure/db/schema.sql`
   plus the migrations in `migrations.py`. Do **not** run SQLite
   `executescript()` against Catalyst — use Catalyst table management.
3. Create the Stratus bucket with prefixes `landing/`, `raw/`, `manifests/`,
   `exports/{user_id}/`, `audio/{user_id}/`.
4. Set the OAuth triple and project id in Catalyst configuration.
5. Run the smoke tests against Development.
6. Flip the backend selectors.

Schema version and deployment version are recorded in `ctl_schema_version`; a
deployment whose schema version is behind the code should fail before the API
is rolled out.
