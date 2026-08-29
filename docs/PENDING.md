# Pending work and blockers

Status as of 30 Aug 2026, after the first sustained run against the live
Catalyst project. Every item says **what** is left, **why** it is blocked, and
**who or what unblocks it**.

Suite: **635 passing, 10 skipped** (skips are live-Catalyst and GPU tests).

---

## A. The live deployment right now

Project `KSP-CIP` (`54586000000013047`), India DC, `Development`.
`cip-api` and `cip-console` are up; `cases: 4200`, `seeded: true`,
`datastore: catalyst`, `filestore: catalyst`, `ready: true`.

**9 of 10 capability areas verified working on the deployed site**, including
the individual-prediction refusal:

| Area | Live |
|---|---|
| FIR retrieval · networks · trend · hotspots · seasonality | ✅ |
| Repeat-offender profiling | ✅ *(was empty; fixed this run)* |
| Forecast · early warning | ✅ |
| Safety refusal ("predict who will commit a crime") | ✅ refuses |
| Financial link analysis · case briefing · ego network | ✅ |
| Kannada round trip (160 Kannada glyphs in the reply) | ✅ |
| PDF export via Stratus (reported bytes == downloaded bytes) | ✅ |
| **Demographic / sociology breakdown** | ❌ **500 — fix committed, not deployed** |
| Scope labels for `io.bengaluru` / `sp.mysuru` | ⚠️ read "no unit assigned" — **fix committed, not deployed** |

Two further defects were found and fixed this run but, like the demographic
one, reach the live site only on the next deploy:

- `ReferenceRepository.unit()`/`district()` compared an int to the string the
  Data Store returns, so a scoped officer's console said "no unit assigned"
  instead of naming their station, and hotspot cells lost their district label.
- Graph ACL trimming compared edge `unit_ids` the same way. Not yet reachable
  live (the graph was loaded from the SQLite seed, so its JSON holds ints) but
  it becomes reachable the moment the intelligence refresh runs *on* Catalyst
  — which is what `cip_refresh` and the nightly circuit exist to do. It fails
  closed, so nothing leaks; a scoped officer would simply see an empty graph.

---

## B. Blocked on a deploy

### B1. Redeploy `cip-api`
Two fixes are committed and green locally but not on the live site:

- the demographic crosstab join-limit fix (the remaining live 500);
- the alias-collision fix, without which the identity review queue shows one
  name where two different people are joined.

**Blocker:** `catalyst deploy` needs `catalyst login`, a browser OAuth flow
only the project owner can complete. It cannot be scripted or delegated.

---

## C. Blocked on OAuth scopes — one token regeneration fixes both

The current refresh token carries:

```
ZohoCatalyst.zcql.CREATE  ZohoCatalyst.tables.ALL  ZohoCatalyst.tables.rows.ALL
ZohoCatalyst.tables.bulk.READ  ZohoCatalyst.tables.bulk.CREATE
Stratus.fileop.ALL  Stratus.bucketop.ALL
```

Two capabilities need scopes that are **not** in that set:

| Needs | Scope | Symptom today |
|---|---|---|
| Creating/altering Data Store columns | `ZohoCatalyst.tables.columns.ALL` | admin API answers `OAUTH_SCOPE_MISMATCH`; also makes the column *listing* endpoint 401, so any audit of live columns reads as "no columns" unless it distinguishes error from empty |
| QuickML LLM serving (#11) | `QuickML.deployment.READ` | endpoint answers `401 INVALID_OAUTHSCOPE` for either Authorization spelling |

**Unblocked by:** regenerating the refresh token with those two scopes appended
to the existing list. Nothing else changes — the QuickML adapter is written and
unit-tested against the console's own contract, and reads the same credentials.

**Left half-done by this:** `ctl_schema_version` now exists on the live project
but has no columns, because creating them needs the scope above. It is
otherwise correctly declared and manifested; the provisioning script is
column-aware and will complete it on a re-run with the right token.

---

## D. Blocked on hardware or a vendor answer

- **Kannada full fidelity / server voice** — `language_full_fidelity: false`.
  Kannada *input* works via the offline glossary; output stays
  English-dominant. Needs `speech-service/` on an NVIDIA/CUDA box with
  `KSPCIP_LANGUAGE_PROVIDER=ai4bharat`. No API key substitutes for the machine.
  **Ask first:** whether Catalyst **Zia** covers Kannada STT/TTS — if it does,
  that closes compliance #15 *and* removes the GPU requirement.
- **SmartBrowz (#16)** — PDF generation. The SDK form (`app.smart_browz()`,
  `convert_to_pdf`) needs the Catalyst server SDK inside the AppSail runtime.
  The current `reportlab` path works and is evidence-bound, so this is a
  compliance swap, not a repair.
- **Neo4j (#C2)** — adapter complete, never run against a live instance. A
  Docker container satisfies it. Only matters beyond hackathon scale.

---

## E. ZCQL is not SQL — the constraints found by running against it

All confirmed live. Each one is invisible to a contract test.

| Constraint | Consequence |
|---|---|
| **`AS alias` is discarded** | rows come back keyed by the underlying column; 73 aliased reads would `KeyError`. Restored by the adapter, namespaced by table alias so a self-join keeps both sides |
| **Max 4 joins** | *"More than 4 joins are not allowed"*. The shared analytics `_FROM` spends all four on labelling; labels now come from the in-memory reference cache instead |
| **Max 300 rows per `LIMIT`** | the pager clamps and pages beneath a caller's bound; both `LIMIT n` and `LIMIT offset, count` are parsed |
| **Joins need a real foreign key** | *"No relationship between tables"* — arbitrary join predicates are rejected |
| **`key` is reserved** | `cip_kv.key` had to become `kv_key` |
| **`COUNT(*)` rejected** | translated to `COUNT(ROWID)` |
| **`WHERE 1 = 0` rejected** | portable "match nothing" is `WHERE <pk> IS NULL` |
| **Every column reads back as a string** | ids arrive as `'2'`, not `2`; call sites coerce |
| **No `executescript`, no DDL** | schema provisioning is a reviewed manual step |
| **No list-objects endpoint (Stratus)** | `list_keys` refuses with a reason rather than a bare 405 |
| **Numeric columns provisioned at `decimal_digits: 2`** | a value below 0.005 rounds to 0.00 and the row is **rejected outright** — this silently cost 160 of 327 offender rows until found |

---

## F. Governance — deliberate, not gaps

Full reasoning in [`data-provenance-and-governance.md`](data-provenance-and-governance.md).

- **Financial data stays synthetic.** No public dataset exists and real
  FIR-linked bank records are protected (DPDP Act, Banking Regulation Act,
  PMLA). `is_extension` is read from the row, so an approved ingestion is a
  data change rather than a code change.
- **Socio-economic is *not* blocked.** Values are calibrated against published
  Census 2011 / NSSO / Planning Commission figures, and provenance is read from
  `data_quality`, so loading real rows with `data_quality='official'` drops the
  marker with no code change. Needs someone to supply the actual tables — and
  note the correlation stays a demonstration while the crime data is synthetic.

---

## G. Suggested order

1. **Regenerate the refresh token** with the two extra scopes (§C). One action,
   unblocks QuickML and the column provisioning.
2. **Redeploy `cip-api`** (§B). Clears the last live 500.
3. Re-run `scripts/provision_catalyst_datastore.js` to finish
   `ctl_schema_version`.
4. Ask whether Zia covers Kannada before sourcing a GPU box.
5. SmartBrowz, Neo4j, socio-economic data — all optional to the demo.
