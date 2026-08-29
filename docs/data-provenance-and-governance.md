# Data provenance and governance

*Why two data layers are marked "extension", what that marking buys, and what
would change on real data.*

This answers the question a reviewer should ask on seeing
`(synthetic extension)` in an answer: **is this incomplete, or is this
deliberate?** It is deliberate, and it is the same discipline the brief asks
for under *Explainable AI & Transparent Analytics* (#9) and *Secure
Role-Based Access & Governance* (#10).

For the architectural decisions themselves see
[ADR-0005](adr/0005-synthetic-financial-extension.md) (financial) and
[ADR-0006](adr/0006-analytics-are-computed-not-prompted.md) (analytics are
arithmetic, never prompted).

---

## 1. Three layers, three provenance rules

The schema is prefixed by origin, and the prefix is load-bearing — it decides
how a statement built from the row is rendered.

| Prefix | Origin | Rendered as |
|---|---|---|
| `curated_*` | The organiser's FIR schema, **column names unchanged** | source record, cited by CrimeNo |
| `cip_*` | Platform-derived (graph, scores, embeddings, hotspots) | `(inferred)` where a link or score is derived |
| `ext_*` | **Not in the organiser's schema** — a platform extension | `(synthetic extension)` |

Two `ext_` layers exist: `ext_financial_transaction` and
`ext_socioeconomic_indicator`. Both are required capabilities (#7 financial
link analysis, #4 socio-economic correlation) for which the FIR schema
provides no table. The alternative to an extension was either to alter the
organiser's schema — which was a hard constraint — or to not build the
capability. Marking it was the third option.

---

## 2. Why the marking is a feature, not a disclaimer

An intelligence product that cannot tell an officer which of its statements
rest on a police record and which rest on a modelled approximation is not
safe for police use, regardless of how good its analysis is. The marking
exists so that distinction survives all the way to the reader.

It survives every transformation, and that is enforced by tests, not
convention:

- evidence carries `Provenance.SYNTHETIC_EXTENSION`;
- the answer composer appends `(synthetic extension)` to the rendered claim;
- the LLM polish pass is **re-verified** — a rewrite that drops the marker is
  discarded rather than shipped;
- Kannada rendering preserves it;
- the console shows it as a badge and repeats it under every financial table
  and money-flow edge;
- the PDF export repeats it, because a PDF outlives the conversation that
  produced it;
- `/api/v1/capabilities` reports the layer as an extension.

The same machinery carries `(inferred)` for derived links. A judge testing
this can delete the marker from a claim and watch the verifier reject the
rewrite.

---

## 3. What would change on real data — layer by layer

The two extensions are **not** in the same position. One is a data-loading
exercise; the other is legally blocked. Treating them as one item obscures
that.

### 3a. Socio-economic — ready now, no code change

`ext_socioeconomic_indicator` carries `data_source`, `data_quality` and
`is_extension` columns, and the provenance is read from the data:

```python
provenance=Provenance.SYNTHETIC_EXTENSION
    if result.data_quality == "synthetic"
    else Provenance.DETERMINISTIC_COMPUTATION
```
`application/agents/crime_analytics.py`

Loading rows with `data_quality='official'` drops the marker automatically.
No code changes.

The generator's values are already *calibrated against* published sources —
Census of India 2011 (KA-08 series), NSSO 68th round, Planning Commission BPL
estimates — and `data_source` records which. Those sources are public
aggregate statistics; there is no private data and no data-owner sign-off
needed. **This layer is unblocked whenever someone supplies the actual
published tables.**

**The honest caveat.** Real indicators correlated against *synthetic* FIRs
still produce a demonstration, not a finding. Swapping in real census data
proves the ingestion path is real; it does not make the correlation a fact
about Karnataka. Anyone doing this swap must not let the label
`data_quality='official'` imply the *conclusion* is official. Approximate
values honestly labelled are better than half-verified values labelled
official.

### 3b. Financial — blocked, and should stay that way

There is no public equivalent to load. Real FIR-linked bank transactions are
protected under the DPDP Act, the Banking Regulation Act, PMLA and RBI
rules. No dataset can be substituted in, and none should be improvised.

What exists is the *analysis*: onward-transfer chains, counterparty
concentration, per-account activity bursts, amount banding and network
position — all deterministic arithmetic, all tested, all stating observations
rather than findings ("above the 90th percentile for this dataset", never
"suspicious"). On approved real data, the same code runs unchanged.

The `is_extension` flag is now read from the row rather than hard-coded, so
an approved ingestion is a data change, not a code change — matching the
socio-economic layer. The rule is one-directional: an aggregate is an
extension if *any* contributing row is, and an unlabelled row counts as an
extension, because absence of a label is not evidence of provenance.

---

## 4. What a reviewer can check in two minutes

1. Ask a financial question. Every claim carries `(synthetic extension)`.
2. Ask for a link analysis. Derived edges carry `(inferred)` and render
   dashed in the diagram — always.
3. Export the conversation to PDF. Both markers are still there.
4. Ask the same question as `analyst.state` and as `io.bengaluru`. The
   answers differ, and the platform says why — including how many links it
   withheld for being outside the caller's scope.
5. Ask "predict who will commit a crime next month". It refuses, before
   routing, and explains what it does instead.
6. Ask `policy.home` for anything naming an individual. Aggregates only.

None of that is prompt wording. It is `application/services/evidence.py`,
`application/services/authorization.py`, and the tests that pin them.

---

## 5. The line this platform will not cross

Scores describe records, not people. The offender score summarises recorded
history — case count, offence variety, recency, gravity escalation, network
position — with every weight published, and is labelled as such in every
answer that carries it. It is not a prediction, and the platform refuses to
be used as one: a question asking whether a *named individual* will offend is
declined before it reaches an agent.

That refusal is the single most important behaviour in the system, and it is
the one a reviewer should try first.
