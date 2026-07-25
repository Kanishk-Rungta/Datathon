# ADR-0006: Every published figure is arithmetic, and shows its working

**Status:** Accepted · **Date:** 2026-07-25

## Context

Trends, hotspots, early-warning signals, sociological breakdowns and the
Investigation Priority Indicator all inform decisions about people.

## Decision

Each is computed in `application/analytics` from `GROUP BY` results, in pure
functions with unit tests, and each returns a `ComputationTrace` describing the
query, the parameters and the formula.

Two design rules follow from the subject matter:

**Scores describe records, never predict people.** The repeat-offender score
summarises recorded case count, offence variety, recency, gravity escalation
and network position, each with a published weight, and is labelled as history
rather than risk. The architecture's exclusion of individual predictive risk
scoring is honoured.

**Statistical signals are prompts, not conclusions.** An early-warning alert
says a district × crime-type combination is running above *its own* 12-month
baseline by a z-score, and carries the explanation. Hotspot output states its
grid size and warns that a cell boundary can split one real concentration.
Sociological output carries a mandatory caveat that counts are of recorded
complaints, not rates against population.

The generator plants known signals and records them in a manifest; integration
tests assert the analytics recover them. Without that loop, analytics over
synthetic data would demonstrate nothing.

## Consequences

- Numbers are reproducible from the trace alone.
- A supervisor can disagree with the arithmetic rather than with a black box.
- The z-score floor prevents a flat baseline producing an infinite score; the
  rolling baseline excludes the observation, so a spike cannot inflate the
  history it is measured against.
