"""Financial pattern analysis over the synthetic transaction extension.

Two things are under test, and the second matters as much as the first:

* the arithmetic — chains, concentration, bursts, distribution and network
  position, on fixtures small enough to verify by hand; and
* the **language** — every one of these describes a shape in recorded
  transfers, and none of them may be phrased as a finding of wrongdoing or as
  a statement about a person's conduct. ADR-0005 and ADR-0006 make that a
  property of the code, so it is tested like one.
"""

from __future__ import annotations

import pytest

from ksp_cip.application.graph.financial import (
    CONCENTRATION_PERCENTILE,
    MIN_ACCOUNTS_FOR_CONCENTRATION,
    STRUCTURING_THRESHOLD,
    FinancialAnalyzer,
)


def txn(txn_id, source, target, amount, day, *, case_id=1,
        from_kind="entity", to_kind="entity"):
    return {
        "txn_id": txn_id,
        "case_master_id": case_id,
        "from_kind": from_kind, "from_ref": source, "from_label": f"{source} Ltd",
        "to_kind": to_kind, "to_ref": target, "to_label": f"{target} Ltd",
        "amount": float(amount), "currency": "INR",
        "txn_date": day, "channel": "NEFT", "is_extension": 1,
    }


@pytest.fixture
def analyzer():
    return FinancialAnalyzer()


class TestHopChains:
    def test_a_timely_onward_sequence_is_found(self, analyzer):
        rows = [
            txn("T1", "A", "B", 100_000, "2026-01-01"),
            txn("T2", "B", "C", 90_000, "2026-01-05"),
            txn("T3", "C", "D", 80_000, "2026-01-09"),
        ]
        chains = analyzer.hop_chains(rows)
        assert chains, "a three-hop sequence inside the window should be found"
        best = chains[0]
        assert best.hops == 3
        assert best.path == ["A Ltd", "B Ltd", "C Ltd", "D Ltd"]
        assert best.start_date == "2026-01-01" and best.end_date == "2026-01-09"

    def test_hops_outside_the_window_do_not_form_a_chain(self, analyzer):
        rows = [
            txn("T1", "A", "B", 100_000, "2026-01-01"),
            txn("T2", "B", "C", 90_000, "2026-06-01"),   # five months later
        ]
        assert analyzer.hop_chains(rows) == []

    def test_a_hop_must_post_date_the_one_before_it(self, analyzer):
        rows = [
            txn("T1", "A", "B", 100_000, "2026-01-10"),
            txn("T2", "B", "C", 90_000, "2026-01-02"),   # earlier: money cannot flow backwards
        ]
        assert analyzer.hop_chains(rows) == []

    def test_the_amount_ratio_is_a_ratio_not_a_retention_claim(self, analyzer):
        """A later hop carrying *more* is normal and must not read as an error."""
        rows = [
            txn("T1", "A", "B", 10_000, "2026-01-01"),
            txn("T2", "B", "C", 50_000, "2026-01-03"),
        ]
        chain = analyzer.hop_chains(rows)[0]
        assert chain.amount_ratio == 5.0

    def test_a_single_transfer_is_not_a_chain(self, analyzer):
        assert analyzer.hop_chains([txn("T1", "A", "B", 1000, "2026-01-01")]) == []


class TestConcentration:
    @staticmethod
    def _fan_in_rows():
        # Twelve distinct senders into one account, plus filler pairs so the
        # dataset is wide enough for a percentile to mean anything.
        rows = [txn(f"T{i}", f"S{i}", "HUB", 10_000, "2026-01-01") for i in range(12)]
        rows += [txn(f"F{i}", f"X{i}", f"Y{i}", 5_000, "2026-01-02") for i in range(6)]
        return rows

    def test_a_collection_point_is_reported_with_its_threshold(self, analyzer):
        results = analyzer.concentration(self._fan_in_rows())
        hub = next(r for r in results if r.ref == "HUB")
        assert hub.direction == "fan-in"
        assert hub.counterparty_count == 12
        # The cutoff must be published, not implied.
        assert hub.percentile == CONCENTRATION_PERCENTILE
        assert hub.threshold_degree >= 2

    def test_a_small_dataset_yields_no_concentration(self, analyzer):
        """A percentile over a handful of accounts describes the handful."""
        rows = [txn(f"T{i}", f"S{i}", "HUB", 1000, "2026-01-01") for i in range(2)]
        assert len(rows) < MIN_ACCOUNTS_FOR_CONCENTRATION
        assert analyzer.concentration(rows) == []

    def test_an_even_spread_reports_nothing(self, analyzer):
        """Every account with one counterparty is not concentration."""
        rows = [txn(f"T{i}", f"S{i}", f"D{i}", 1000, "2026-01-01") for i in range(12)]
        assert analyzer.concentration(rows) == []


class TestAmountDistribution:
    def test_amounts_land_in_the_right_bands(self, analyzer):
        rows = [
            txn("T1", "A", "B", STRUCTURING_THRESHOLD * 0.10, "2026-01-01"),
            txn("T2", "A", "B", STRUCTURING_THRESHOLD * 0.50, "2026-01-01"),
            txn("T3", "A", "B", STRUCTURING_THRESHOLD * 0.90, "2026-01-01"),
            txn("T4", "A", "B", STRUCTURING_THRESHOLD * 2.00, "2026-01-01"),
        ]
        bands = {b.label: b.count for b in analyzer.amount_distribution(rows)}
        assert bands == {
            "below 25% of threshold": 1,
            "25–85% of threshold": 1,
            "just below threshold": 1,
            "at or above threshold": 1,
        }

    def test_every_transaction_is_counted_exactly_once(self, analyzer):
        rows = [txn(f"T{i}", "A", "B", 1_000 * (i + 1), "2026-01-01") for i in range(40)]
        bands = analyzer.amount_distribution(rows)
        assert sum(b.count for b in bands) == len(rows)

    def test_a_boundary_amount_falls_in_the_upper_band(self, analyzer):
        rows = [txn("T1", "A", "B", STRUCTURING_THRESHOLD, "2026-01-01")]
        bands = {b.label: b.count for b in analyzer.amount_distribution(rows)}
        assert bands["at or above threshold"] == 1
        assert bands["just below threshold"] == 0


class TestTemporalBursts:
    @staticmethod
    def _quiet_then_spike():
        rows = [txn(f"Q{i}", "ACC", f"P{i}", 5_000, day) for i, day in enumerate(
            ["2026-01-02", "2026-01-09", "2026-01-16", "2026-01-23", "2026-01-30"])]
        rows += [txn(f"B{i}", "ACC", f"Z{i}", 5_000, "2026-02-05") for i in range(9)]
        return rows

    def test_a_spike_against_a_quiet_history_is_found(self, analyzer):
        bursts = analyzer.temporal_bursts(self._quiet_then_spike())
        assert bursts, "nine transfers in a day against a weekly rhythm is a burst"
        assert bursts[0].ref == "ACC"
        assert bursts[0].day == "2026-02-05"
        assert bursts[0].txn_count == 9
        assert bursts[0].z_score >= 2.5

    def test_steady_activity_is_not_a_burst(self, analyzer):
        rows = [txn(f"S{i}", "ACC", f"P{i}", 5_000, f"2026-01-{i + 1:02d}") for i in range(28)]
        assert analyzer.temporal_bursts(rows) == []

    def test_an_account_without_enough_history_is_skipped(self, analyzer):
        """Two active days cannot establish what is normal for an account."""
        rows = [txn("T1", "ACC", "P", 5_000, "2026-01-01"),
                txn("T2", "ACC", "P", 5_000, "2026-01-02")]
        assert analyzer.temporal_bursts(rows) == []

    def test_each_account_is_compared_against_itself(self, analyzer):
        """A busy account must not make a quiet one look like a burst."""
        busy = [txn(f"H{i}", "BUSY", f"P{i}", 1_000, f"2026-01-{(i % 28) + 1:02d}") for i in range(60)]
        quiet = [txn(f"L{i}", "QUIET", f"P{i}", 1_000, day) for i, day in enumerate(
            ["2026-01-03", "2026-01-10", "2026-01-17", "2026-01-24", "2026-01-31"])]
        refs = {b.ref for b in analyzer.temporal_bursts(busy + quiet)}
        assert "QUIET" not in refs


class TestNetworkPosition:
    def test_a_broker_ranks_above_a_leaf(self, analyzer):
        # A—HUB—B—…: HUB sits on the routes between the outer accounts.
        rows = [
            txn("T1", "A", "HUB", 1_000, "2026-01-01"),
            txn("T2", "HUB", "B", 1_000, "2026-01-02"),
            txn("T3", "HUB", "C", 1_000, "2026-01-03"),
            txn("T4", "C", "D", 1_000, "2026-01-04"),
        ]
        positions = analyzer.network_positions(rows)
        assert positions[0].ref == "HUB"
        leaf = next(p for p in positions if p.ref == "A")
        assert positions[0].betweenness > leaf.betweenness

    def test_a_graph_too_small_to_have_a_position_returns_nothing(self, analyzer):
        assert analyzer.network_positions([txn("T1", "A", "B", 1_000, "2026-01-01")]) == []


class TestSubjectTotals:
    """Regression: totals must cover every source row an identity resolves to.

    Entity resolution merges several ``curated_Accused`` rows into one person.
    Matching a transfer against only the first of them counted it toward the
    transaction *count* while contributing nothing to the totals, so a person
    with real money movement was reported as "1 transaction … ₹0 received and
    ₹0 sent".
    """

    def test_a_transfer_on_a_secondary_source_row_still_counts(self, analyzer):
        rows = [txn("T1", "351", "E1", 693_000, "2026-01-01", from_kind="accused")]
        summary = analyzer.summarize(
            subject_ref="130", subject_refs=["130", "136", "308", "351"],
            subject_label="Y. Mustafa Patil", transactions=rows,
        )
        assert summary.total_sent == 693_000
        assert summary.counterparties, "the counterparty must appear in the table"

    def test_matching_only_the_display_ref_would_have_missed_it(self, analyzer):
        rows = [txn("T1", "351", "E1", 693_000, "2026-01-01", from_kind="accused")]
        summary = analyzer.summarize(
            subject_ref="130", subject_label="Y. Mustafa Patil", transactions=rows,
        )
        # This is the old behaviour, kept explicit so the bug cannot return
        # unnoticed: without the full ref set, the money is invisible.
        assert summary.total_sent == 0.0

    def test_a_transfer_between_two_of_the_subjects_own_rows_is_not_double_counted(self, analyzer):
        rows = [txn("T1", "130", "351", 50_000, "2026-01-01",
                    from_kind="accused", to_kind="accused")]
        summary = analyzer.summarize(
            subject_ref="130", subject_refs=["130", "351"],
            subject_label="Same Person", transactions=rows,
        )
        assert summary.total_sent == 0.0
        assert summary.total_received == 0.0


class TestStructuralAnalysesUseTheNeighbourhood:
    def test_the_neighbourhood_drives_structure_while_totals_stay_the_subjects(self, analyzer):
        own = [txn("T1", "SUB", "HUB", 10_000, "2026-01-01")]
        neighbourhood = own + [
            txn("N1", "HUB", "B", 9_000, "2026-01-03"),
            txn("N2", "B", "C", 8_000, "2026-01-06"),
        ]
        summary = analyzer.summarize(
            subject_ref="SUB", subject_refs=["SUB"], subject_label="Subject",
            transactions=own, network_transactions=neighbourhood,
        )
        assert summary.total_sent == 10_000, "totals describe the subject only"
        assert summary.chains, "structure is visible only across the neighbourhood"


class TestLanguageIsObservationalNotAccusatory:
    """These outputs describe records. They must never read as verdicts."""

    ACCUSATORY = [
        "laundering", "launder", "guilty", "criminal", "fraudulent", "illegal",
        "proves", "proven", "confirms that", "scheme", "conspiracy",
    ]

    def test_pattern_wording_stays_observational(self, analyzer):
        rows = [txn(f"T{i}", "A", "B", STRUCTURING_THRESHOLD * 0.9, "2026-01-01") for i in range(5)]
        for pattern in analyzer.detect_patterns(rows):
            blob = f"{pattern['pattern']} {pattern['observation']} {pattern['caveat']}".lower()
            for word in self.ACCUSATORY:
                assert word not in blob, f"{word!r} appeared in a financial observation"

    def test_the_structuring_observation_still_refuses_to_call_it_structuring(self, analyzer):
        rows = [txn(f"T{i}", "A", "B", STRUCTURING_THRESHOLD * 0.9, "2026-01-01") for i in range(5)]
        near = next(p for p in analyzer.detect_patterns(rows) if "threshold" in p["pattern"])
        assert "not a finding of structuring" in near["caveat"]

    def test_no_result_type_carries_a_conclusion_field(self, analyzer):
        """A field named for a verdict invites one; there must not be one."""
        rows = [
            txn("T1", "A", "HUB", 1_000, "2026-01-01"),
            txn("T2", "HUB", "B", 1_000, "2026-01-02"),
            txn("T3", "HUB", "C", 1_000, "2026-01-03"),
        ]
        summary = analyzer.summarize(
            subject_ref="A", subject_refs=["A"], subject_label="A", transactions=rows,
        )
        forbidden = {"conclusion", "verdict", "guilt", "culpability", "finding", "suspicion"}
        for item in (*summary.chains, *summary.concentrations, *summary.positions):
            assert not forbidden & set(getattr(item, "__slots__", ()))
