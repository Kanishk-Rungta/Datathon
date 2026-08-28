# ADR-0005: Financial data is an extension, and says so everywhere

**Status:** Accepted · **Date:** 2026-07-25

## Context

Money-flow analysis is a required capability. The organiser's FIR schema
contains no financial table, and the schema must remain unchanged.

## Decision

Financial transactions live in `ext_financial_transaction` — a table whose
`ext_` prefix, `is_extension` column, repository docstring and every derived
statement mark it as synthetic. No `curated_` table is altered.

The marking is carried end to end rather than mentioned once:

- evidence carries `Provenance.SYNTHETIC_EXTENSION`;
- the composer appends `(synthetic extension)` to the rendered claim;
- the console renders that marker as a distinct badge and repeats it under
  every financial table and money-flow edge;
- the PDF export repeats it;
- `/api/v1/capabilities` reports `financial_data` as an extension.

The analyzer states observations, not findings. Amounts clustered below the
reporting threshold are reported as "an arithmetic observation about the
recorded amounts, not a finding of structuring", because the difference
matters to anyone acting on it.

## The analyses, and what each is careful not to say

Six deterministic observations run over the extension. Each publishes its
parameters in the computation trace (ADR-0006), and each is worded to describe
**records and accounts**, never a person's conduct:

| Analysis | What it computes | The line it does not cross |
|---|---|---|
| Near-threshold amounts | Transfers in the band just under the reporting threshold | "not a finding of structuring" |
| Same-day volume | Several transfers from one party in a day | "common in legitimate business activity" |
| Hop chains | Onward movement, each hop within 14 days of the last | Deliberately **not** called layering — onward movement is a shape, not an intent to obscure |
| Concentration | Fan-in/fan-out above the p90 of the observed degree distribution (floored at 2) | "the account's position in the recorded transfers, not the conduct of any person" |
| Temporal bursts | A day above the account's **own** 30-calendar-day baseline, via the same `z_score` the early-warning analytics use | "a prompt to look, not a conclusion" |
| Network position | Degree and betweenness on the money-flow graph | "a structural position … not a statement about culpability" |

Two design choices are load-bearing:

**Each account is compared against itself.** A busy merchant and a dormant
personal account have no business sharing a baseline, and the baseline runs
over calendar days so dormancy is visible rather than silently skipped.

**Structural analyses run over the neighbourhood, totals do not.** A chain, a
hub or a broker position is a property of a neighbourhood and is invisible in
one account's rows; the subject's own totals stay strictly their own.

A test asserts no result type carries a `conclusion`, `verdict`, `guilt`,
`culpability`, `finding` or `suspicion` field, and that no observation's wording
contains accusatory vocabulary — the same technique used to keep event
comparison free of causal claims (ADR-0006).

## Consequences

- The capability is demonstrable without misrepresenting the data.
- A reviewer can tell at a glance which parts of an answer rest on the
  organiser's schema and which do not.
- If a real financial source is later integrated, the extension table is the
  seam it replaces, and every marker becomes a provenance change in one place.
