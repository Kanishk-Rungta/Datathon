# Credentials and external APIs

Every external credential the platform can use, how to obtain it, what it
controls, and what happens without it.

`backend/ksp_cip/config/settings.py` is the source of truth; this file
explains it. All variables use the `KSPCIP_` prefix and can be set in
`backend/.env` (gitignored) or as real environment variables.

---

## 0. The baseline: nothing is required

The platform runs, seeds, serves and passes its full test suite with **zero
credentials**. Defaults are deterministic local providers — an offline LLM
that only re-phrases already-computed facts, an offline Kannada glossary, and
SQLite. That is a deliberate property, not a fallback: it keeps tests hermetic
and lets a reviewer run the whole thing offline.

Everything below is therefore an *upgrade*, not a prerequisite — with one
exception: **Catalyst deployment is mandatory for the submission**, so §1 is
required in practice.

---

## 1. Catalyst core — required for deployment

One project, one OAuth client. This unlocks Data Store, Stratus, Cache and
NoSQL.

| Variable | Required when | Purpose |
|---|---|---|
| `KSPCIP_CATALYST_PROJECT_ID` | any Catalyst backend | identifies the project |
| `KSPCIP_CATALYST_OAUTH_CLIENT_ID` | `DATASTORE_BACKEND=catalyst` | OAuth client |
| `KSPCIP_CATALYST_OAUTH_CLIENT_SECRET` | `DATASTORE_BACKEND=catalyst` | OAuth client |
| `KSPCIP_CATALYST_OAUTH_REFRESH_TOKEN` | `DATASTORE_BACKEND=catalyst` | long-lived grant |
| `KSPCIP_CATALYST_ENVIRONMENT` | optional | `Development` (default) or `Production` |

### ⚠ Pick the right data centre first

Zoho accounts are **data-centre-scoped**, and a credential from one DC will not
work against another. This repo defaults to the **India** DC:

| DC | API base | Accounts base | API console |
|---|---|---|---|
| India *(repo default)* | `https://api.catalyst.zoho.in` | `https://accounts.zoho.in` | `api-console.zoho.in` |
| US | `https://api.catalyst.zoho.com` | `https://accounts.zoho.com` | `api-console.zoho.com` |
| EU | `https://api.catalyst.zoho.eu` | `https://accounts.zoho.eu` | `api-console.zoho.eu` |

If the project is not on the India DC, override `KSPCIP_CATALYST_BASE_URL` and
`KSPCIP_CATALYST_ACCOUNTS_URL` together. Mismatched DCs are the single most
common cause of a confusing `invalid_client` at token refresh.

### How to obtain

1. **Project ID** — Catalyst console → your project → the numeric id in the URL
   or under project settings.
2. **Client id/secret** — the Zoho **API Console** for your DC → *Get Started*.
   - For a quick start pick **Self Client** (one per account; generates a code
     directly in the console, no redirect URI needed).
   - For anything shared or long-lived pick **Server-based Application** and
     register a redirect URI.
3. **Refresh token** — generate a grant code with the scopes below, then
   exchange it once:

```bash
curl -X POST "https://accounts.zoho.in/oauth/v2/token" \
  -d "grant_type=authorization_code" \
  -d "client_id=$CLIENT_ID" \
  -d "client_secret=$CLIENT_SECRET" \
  -d "redirect_uri=$REDIRECT_URI" \
  -d "code=$GRANT_CODE"
```

The response's `refresh_token` is what you store. For the non-Self-Client flow
the authorization URL needs `access_type=offline`, or no refresh token is
issued.

### Scopes this platform actually needs

Grant least privilege — only what the selected backends use:

```
ZohoCatalyst.tables.rows.READ, ZohoCatalyst.tables.rows.CREATE,
ZohoCatalyst.tables.rows.UPDATE, ZohoCatalyst.tables.rows.DELETE,
Stratus.fileop.ALL, ZohoCatalyst.buckets.READ,
ZohoCatalyst.buckets.objects.UPDATE,
ZohoCatalyst.cache.READ, ZohoCatalyst.cache.CREATE, ZohoCatalyst.cache.DELETE,
ZohoCatalyst.nosql.READ, ZohoCatalyst.nosql.rows.ALL
```

Add `QuickML.deployment.READ` only if wiring QuickML (§3).

**Note:** the platform never issues DDL. Table creation is a reviewed manual
step (`catalyst-schema.md`), so no schema-modifying scope is needed or wanted.

### What the code refuses

`Settings.deployment_problems()` fails startup — reporting *all* problems at
once, never echoing a value — when:

- a Catalyst backend is selected without `PROJECT_ID`;
- the Catalyst data store is selected without all three OAuth values;
- **the data store is Catalyst but the file store is local** — exports on a
  function filesystem vanish at the next cold start, and the audit row would
  then cite a file nobody can fetch;
- the Catalyst identity backend is selected without an auth issuer;
- `JWT_SECRET` is still the placeholder outside `local`.

### Rotation and control

Refresh tokens are long-lived and do not expire on a timer, so treat one as a
standing key: store it only in `backend/.env` (gitignored) or the Catalyst
console's environment variables, never in the repo. Zoho caps the number of
refresh tokens per client — revoke old ones in the API console rather than
minting endlessly. Revoking the client invalidates every token derived from it.

---

## 2. Platform JWT secret — required for any deployment

| Variable | Default | Action |
|---|---|---|
| `KSPCIP_JWT_SECRET` | `dev-only-secret-change-me` | **must change** |

Not a third-party API — this signs the platform's own session tokens. It is
not obtained from anyone; generate it:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Startup **refuses** the placeholder in any environment other than `local`.
Rotating it invalidates every issued session immediately, which is the
intended emergency control.

---

## 3. Catalyst QuickML — LLM / RAG *(compliance gap)*

Requirement #11 names QuickML for text LLM/RAG. The platform currently uses its
local deterministic provider.

**Blocked on:** the deployed endpoint's **request/response body schema**, which
Zoho publishes only on the endpoint's own console page. The auth surface is
already known:

```
X-QUICKML-ENDPOINT-KEY: <endpoint key>
Authorization: Zoho-oauthtoken <access token>
CATALYST-ORG: <org id>
Environment: Development | Production
```
OAuth scope: `QuickML.deployment.READ`

**To obtain:** Catalyst console → QuickML → deploy a model → the endpoint
details page shows the URL, the endpoint key, and a sample request. Send me
that sample and the provider becomes a small adapter.

**Control:** the endpoint key is per-deployment and revocable independently of
the OAuth client — narrower blast radius than the account credential. Note the
platform's LLM has no authority over facts (ADR-0003): a compromised or broken
LLM key degrades phrasing, never figures.

---

## 4. Catalyst Zia — voice *(compliance gap)*

Requirement #15 names Zia Services for speech-to-text/text-to-speech.

**To obtain:** Catalyst console → Zia Services → enable the speech service →
its endpoint + key. Uses the same project OAuth.

**Ask first:** whether Zia supports **Kannada** STT/TTS. If it does, this
closes the compliance gap *and* removes the GPU requirement in §7 — one change
solving two problems. If it does not, §7 is the only path to real voice.

---

## 5. Catalyst Authentication — login *(partially wired)*

| Variable | Purpose |
|---|---|
| `KSPCIP_IDENTITY_BACKEND=catalyst` | switch on Catalyst token verification |
| `KSPCIP_CATALYST_AUTH_ISSUER` | **required** — expected `iss` claim |
| `KSPCIP_CATALYST_AUTH_AUDIENCE` | optional — expected `aud` claim |

The backend half works: a Catalyst JWT is verified and mapped to a `Principal`,
and the API routes verification through the configured provider.

**Still missing:** the React console has no Catalyst Web SDK wiring, so nothing
issues such a token yet. That needs the project's **client config** from the
console (the `catalyst-config.json` the Web SDK reads).

**The control that matters here:** Catalyst decides *who the person is*;
KSP-CIP decides *what they may see*. Role, home unit and district are read from
`cip_user_account`, **never** from a token claim — so anyone able to mint a
token still cannot choose their own authority. Do not "simplify" this by
trusting a `role` claim.

Signature verification is HS256 only. An RS256 deployment must add JWKS
retrieval in `_verify_signature`, which deliberately refuses any algorithm it
was not built to check rather than accepting an unverified token.

---

## 6. Optional hosted LLMs — phrasing only

Alternatives to QuickML for local experimentation. **Using one of these instead
of QuickML is a submission-validity risk** (§3); they are listed because the
adapters exist.

| Provider | `KSPCIP_LLM_PROVIDER` | Where to get a key |
|---|---|---|
| Anthropic | `anthropic` | console.anthropic.com |
| Google Gemini | `gemini` | aistudio.google.com |
| Groq | `groq` | console.groq.com |
| Any OpenAI-shaped endpoint | `openai_compatible` | set `KSPCIP_LLM_BASE_URL` |

Set `KSPCIP_LLM_API_KEY`; startup refuses a non-local provider without one.

**Controls already in place:** a daily token budget
(`KSPCIP_LLM_DAILY_TOKEN_BUDGET`, default 2M) that returns empty rather than
overspending; PII redaction on everything leaving the boundary (phone, Aadhaar,
email) — CrimeNo is deliberately preserved because it is the citation currency
and is meaningless outside the source system; and a verifier that discards any
rewrite introducing a number, name or citation the deterministic draft did not
contain.

---

## 7. Speech / Kannada — two routes, neither needs a vendor key

### 7a. Bhashini (Government of India)

| Variable | Notes |
|---|---|
| `KSPCIP_LANGUAGE_PROVIDER=bhashini` | |
| `KSPCIP_BHASHINI_USER_ID` | from bhashini.gov.in ULCA registration |
| `KSPCIP_BHASHINI_API_KEY` | same |
| `KSPCIP_BHASHINI_PIPELINE_ID` | defaults to a working MeitY pipeline |

Register at **bhashini.gov.in** → ULCA → create an account → API key. Free for
government/public-interest use. Both values are required together or startup
refuses.

### 7b. Self-hosted AI4Bharat — **no API key exists**

| Variable | Notes |
|---|---|
| `KSPCIP_LANGUAGE_PROVIDER=ai4bharat` | |
| `KSPCIP_AI4BHARAT_BASE_URL` | **required** — URL of the service *you* run |

There is no vendor and no account: the models are open weights on Hugging Face
and `speech-service/` is the server. The cost is **hardware, not credentials** —
an NVIDIA/CUDA machine, several GB of PyTorch, and `ffmpeg` on PATH. No key can
substitute for the machine.

Without either, `language_full_fidelity` reports `false` and the console shows
"kannada: offline glossary". Kannada *input* still works via the glossary;
output stays English-dominant.

---

## 8. Neo4j — optional graph backend

| Variable | Default |
|---|---|
| `KSPCIP_GRAPH_BACKEND` | `networkx` |
| `KSPCIP_NEO4J_URI` | `bolt://localhost:7687` |
| `KSPCIP_NEO4J_USER` | `neo4j` |
| `KSPCIP_NEO4J_PASSWORD` | `password` |

Self-hosted; a Docker container satisfies it. **Change the default password.**
Falls back to NetworkX automatically if unreachable, so this is a scale
upgrade, not a dependency. Only matters beyond hackathon scale.

---

## 9. Quick reference — what breaks without what

| Missing | Consequence |
|---|---|
| everything | Platform fully works, locally, offline |
| Catalyst OAuth | Cannot deploy — the submission requirement |
| JWT secret changed | Startup refused outside `local` |
| QuickML | Works; uses local LLM — *compliance risk* |
| Zia | Works; browser voice only — *compliance risk* |
| Catalyst Auth frontend | Works; local demo accounts |
| Hosted LLM key | Works; local deterministic phrasing |
| Bhashini / AI4Bharat | Works; Kannada glossary, no server voice |
| Neo4j | Works; in-memory NetworkX |

---

## 10. Handling rules

- Secrets belong in `backend/.env` (gitignored) or the Catalyst console's
  environment variables. Never in the repo, never in `catalyst.json`.
- `catalyst.json` uses `${CATALYST_PROJECT_ID}` as a placeholder for exactly
  this reason.
- `deployment_problems()` names the missing *setting*, never its value, so a
  health endpoint can report configuration errors without leaking anything.
- Grant per-service scopes, not blanket access. Revoke in the API console;
  revoking the client invalidates every derived token.
- Demo passwords (`ChangeMe#2026`) are for synthetic data only and are
  withheld by the API outside `local`/`development`.
