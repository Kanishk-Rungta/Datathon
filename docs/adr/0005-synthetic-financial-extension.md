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

## Consequences

- The capability is demonstrable without misrepresenting the data.
- A reviewer can tell at a glance which parts of an answer rest on the
  organiser's schema and which do not.
- If a real financial source is later integrated, the extension table is the
  seam it replaces, and every marker becomes a provenance change in one place.
