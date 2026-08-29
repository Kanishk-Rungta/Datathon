# Catalyst runtime decision (P1-01)

## The question

`implementationv2-phases-0-2.md` P1-01 requires confirming, before editing
code, which Catalyst runtime actually hosts the FastAPI application — and
explicitly forbids leaving "an unverified function shim in front of FastAPI."

The repository as inherited did exactly that: `catalyst/functions/cip_api/main.py`
declared an Advanced I/O function whose handler was:

```python
def handler(context, basicio):
    return app
```

This returns the ASGI application object to the runtime and does nothing else.
It does not read a request from `basicio`, does not call the ASGI
`scope/receive/send` contract, and does not write a response. It could not
have worked against any real invocation.

## What was checked

No live Catalyst project is available in this environment, so the documented
contract was checked instead of guessed at, against Zoho's own docs
(fetched during this work — see Sources below):

- **Advanced I/O Python functions** expose `context` and `basicio` modules.
  `basicio.write()` emits output; `basicio.getArgument()` reads individual
  parameters. This is a request/response primitive, not an ASGI bridge — there
  is no documented mechanism for handing Catalyst's request to a full ASGI
  application's `scope/receive/send` interface. Zoho's own function-type
  breakdown lists Advanced I/O only as adding "Headers, and Native Request and
  Response objects" over Basic I/O — still not an ASGI adapter.
- **Event functions** (used by `cip_refresh`) use `def handler(event, context):`
  — a plain payload-in/dict-out contract. This one **is** correctly shaped
  already; no change needed to its signature, only to the two other defects
  described in `phase1-catalyst-runtime.md` (Python version, environment
  variable).
- **AppSail Python** is documented, with working examples (Flask, Bottle,
  Tornado), for exactly this shape of problem: run your own long-lived process,
  bind `0.0.0.0` on the port named by the `X_ZOHO_CATALYST_LISTEN_PORT`
  environment variable, and Catalyst proxies to it. There is no framework
  restriction — "AppSail does not provide any templates for specific Python
  frameworks... You can build your app service using any Python technology you
  prefer." A start command is set in `app-config.json` (e.g. `python3 -u app.py`).

## Decision

**Host the FastAPI application as a Catalyst AppSail Python service running
`uvicorn` directly, not as an Advanced I/O function.** This is option 2 of
P1-01: "If it does not provide a reliable ASGI bridge, run the FastAPI
application as the supported Catalyst AppSail Python web application."

`ksp_cip.interface.api.main:get_app` remains the only application factory.
The new entrypoint (`catalyst/appsail/api/server.py`) does nothing but import
that factory and call `uvicorn.run` against the Catalyst-provided port — the
same "adapter translates, business logic doesn't move" rule used everywhere
else in this codebase.

`cip_refresh` stays an Event function; its handler shape was already correct.

## Topology after this change

```text
Catalyst project
  appsail/
    cip-console   (Node 18, existing)  — static frontend/dist + /api proxy
    cip-api       (Python, NEW)        — uvicorn running ksp_cip's FastAPI app
  functions/
    cip_refresh   (Python event function, existing shape, fixed version/env)
  datastore, stratus, cache, circuits — unchanged
```

`cip-console`'s `CIP_API_URL` now points at the `cip-api` AppSail service's
URL instead of a function URL — a configuration change, not a code change, on
the console side. The proxy itself (`server.js`) was hardened in this pass to
select `http`/`https` based on the target's own protocol, since an
AppSail-to-AppSail internal call is not guaranteed to be TLS the way a public
function URL was.

## What remains unverified

No live Catalyst project exists to deploy this to. What is verified: the
entrypoint runs uvicorn correctly locally on the port named by
`X_ZOHO_CATALYST_LISTEN_PORT` (falls back to 9000, matching the documented
default), and the packaging script (`scripts/build_catalyst_artifact.py`)
produces a self-contained staging directory that imports cleanly. What is
**not** verified: the exact `app-config.json` schema Catalyst's current CLI
generates, the exact supported Python stack identifier string (the Flask
example showed `Python_3_9`; whether `Python_3_11`/`Python_3_12` is available
must be confirmed against the target project at provisioning time — see
`phase1-catalyst-runtime.md`), and the real request path end-to-end.

## Addendum, 29 Aug 2026 — the console had the same defect

The decision above moved the *API* off a path that could not have worked, and
`scripts/build_catalyst_artifact.py` was written so the API artifact carries
`ksp_cip` with it rather than reaching back into the checkout (P1-03).

`cip-console` was left behind. Its `server.js` resolved the document root as
`path.resolve(__dirname, '../../../frontend/dist')` — three levels above
`appsail/console`, and therefore outside the directory Catalyst zips and
ships. The service would have deployed cleanly, started, logged nothing
unusual, and served a 404-then-index fallback with no index to fall back to.
It is the identical defect, one component over.

Two changes:

- `server.js` now resolves its document root in order: `CIP_CONSOLE_DIST`,
  `./dist` beside the entrypoint (the staged layout), then the repo-relative
  `frontend/dist` (the checkout layout) — deliberately mirroring
  `_bootstrap.locate_backend_root`, so one file works in both places.
- `build_catalyst_artifact.py` gained a `console` target that copies
  `frontend/dist` into the staging directory and **fails the build** if the
  console has not been built. Its self-containment check is the Node
  equivalent of the Python one: prove nothing the service serves lives outside
  the directory being shipped.

`catalyst.json` now states `source`, `deploy_source` and `build` for every
component, and `test_catalyst_deployment_descriptor.py::TestDeployableSources`
asserts all three are present and that the named build target exists — so the
next component added cannot quietly repeat this.

The same pass corrected one more thing on this path: `_bootstrap.py` defaulted
`KSPCIP_DATASTORE_BACKEND` to `catalyst` while leaving the file store on
`local`, which `Settings.deployment_problems()` rejects outright. Every first
deploy would have failed at startup on a configuration error the deployer had
no way to anticipate. The two are now defaulted together.

## Sources

- [Catalyst by Zoho — Basic I/O Functions](https://catalyst.zoho.com/help/basicio-functions.html)
- [Functions — Catalyst Docs](https://docs.catalyst.zoho.com/en/serverless/help/functions/introduction/)
- [Exploring Catalyst function types through Ecommerce order processing](https://catalyst.zoho.com/cookbook/catalyst-101/catalyst-function-types/)
- [AppSail — Catalyst Docs](https://docs.catalyst.zoho.com/en/serverless/help/appsail/introduction/)
- [Python — AppSail Help Guides — Catalyst Docs](https://docs.catalyst.zoho.com/en/serverless/help/appsail/help-guides/python/overview/)
- [Flask App with zcatalyst-sdk — Catalyst Docs](https://docs.catalyst.zoho.com/en/serverless/help/appsail/help-guides/python/flask/)
- [Catalyst Configurations for AppSail Services](https://docs.catalyst.zoho.com/en/serverless/help/appsail/key-concepts/catalyst-configurations/)
