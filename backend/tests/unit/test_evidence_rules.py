"""The evidence rule is the platform's core safety property.

If these tests pass, no factual statement can leave the composer without a
locator an officer can check, and no LLM rewrite can alter a figure.
"""

import pytest

from ksp_cip.application.services.evidence import (
    AnswerComposer,
    aggregate_evidence,
    claim,
    empty_result_evidence,
    merge_results,
)
from ksp_cip.domain.enums import AgentName, Intent, Provenance
from ksp_cip.domain.errors import EvidenceMissingError
from ksp_cip.domain.models import AgentResult


def result(claims, evidence=()):
    return AgentResult(
        agent=AgentName.DATA_RETRIEVAL,
        intent=Intent.LOOKUP_CASE,
        summary_claims=list(claims),
        evidence=list(evidence),
    )


class TestEvidenceEnforcement:
    def test_numeric_claim_without_evidence_is_refused(self):
        composer = AnswerComposer(None, enable_polish=False)
        with pytest.raises(EvidenceMissingError):
            composer.compose(result([claim("There were 42 thefts in Mysuru.")]))

    def test_inferred_claim_without_evidence_is_refused(self):
        composer = AnswerComposer(None, enable_polish=False)
        with pytest.raises(EvidenceMissingError):
            composer.compose(result([
                claim("These two people are associates.", provenance=Provenance.INFERRED)
            ]))

    def test_claim_citing_an_unpublished_locator_is_refused(self):
        composer = AnswerComposer(None, enable_polish=False)
        with pytest.raises(EvidenceMissingError):
            composer.compose(result([claim("There were 42 thefts.", ["AGG:ghost"])]))

    def test_qualitative_claim_needs_no_evidence(self):
        composer = AnswerComposer(None, enable_polish=False)
        answer = composer.compose(result([
            claim("Counts reflect registration practice as well as underlying crime.")
        ]))
        assert answer.answer_text

    def test_negative_result_still_carries_a_checkable_locator(self):
        """"Nothing found" is an assertion about a query, so it is evidenced too."""
        nothing = empty_result_evidence(key="early_warning:none", label="No area exceeded baseline")
        composer = AnswerComposer(None, enable_polish=False)
        answer = composer.compose(result(
            [claim("No area exceeded its own 12-month baseline.", [nothing],
                   provenance=Provenance.DETERMINISTIC_COMPUTATION)],
            [nothing],
        ))
        assert "AGG:early_warning:none" in answer.answer_text


class TestProvenanceMarkers:
    def test_inferred_claims_are_marked_in_the_rendered_text(self):
        evidence = aggregate_evidence(key="k", label="l", case_master_ids=[1])
        composer = AnswerComposer(None, enable_polish=False)
        answer = composer.compose(result(
            [claim("A and B share 3 FIRs.", [evidence], provenance=Provenance.INFERRED)],
            [evidence],
        ))
        assert "(inferred)" in answer.answer_text

    def test_synthetic_extension_claims_are_marked(self):
        evidence = aggregate_evidence(key="k", label="l", case_master_ids=[1])
        composer = AnswerComposer(None, enable_polish=False)
        answer = composer.compose(result(
            [claim("2 transfers totalling 40000 were recorded.", [evidence],
                   provenance=Provenance.SYNTHETIC_EXTENSION)],
            [evidence],
        ))
        assert "(synthetic extension)" in answer.answer_text

    def test_citations_appear_for_every_evidenced_claim(self):
        evidence = aggregate_evidence(key="trend:2026", label="l", case_master_ids=[1, 2])
        composer = AnswerComposer(None, enable_polish=False)
        answer = composer.compose(result([claim("There were 2 cases.", [evidence])], [evidence]))
        assert "[AGG:trend:2026]" in answer.answer_text


class TestRewriteVerification:
    """An LLM may only rephrase. These tests are the enforcement."""

    def _base(self):
        evidence = aggregate_evidence(key="trend:2026", label="l", case_master_ids=[1])
        return result([claim("There were 42 cases in Mysuru.", [evidence])], [evidence])

    def test_rewrite_that_changes_a_number_is_rejected(self):
        composer = AnswerComposer(None, enable_polish=False)
        original = composer._render(self._base())
        assert not composer.verify_rewrite(original, "There were 43 cases in Mysuru. [AGG:trend:2026]")

    def test_rewrite_that_drops_a_citation_is_rejected(self):
        composer = AnswerComposer(None, enable_polish=False)
        original = composer._render(self._base())
        assert not composer.verify_rewrite(original, "There were 42 cases in Mysuru.")

    def test_faithful_rephrasing_is_accepted(self):
        composer = AnswerComposer(None, enable_polish=False)
        original = composer._render(self._base())
        assert composer.verify_rewrite(original, "Mysuru recorded 42 cases. [AGG:trend:2026]")

    def test_rewrite_that_drops_an_inferred_marker_is_rejected(self):
        evidence = aggregate_evidence(key="e", label="l", case_master_ids=[1])
        composer = AnswerComposer(None, enable_polish=False)
        source = result([claim("A and B share 3 FIRs.", [evidence], provenance=Provenance.INFERRED)],
                        [evidence])
        original = composer._render(source)
        assert not composer.verify_rewrite(original, "A and B share 3 FIRs. [AGG:e]")


class TestMerge:
    def test_merging_preserves_all_evidence_and_claims(self):
        left_evidence = aggregate_evidence(key="a", label="a", case_master_ids=[1])
        right_evidence = aggregate_evidence(key="b", label="b", case_master_ids=[2])
        left = result([claim("First, 1 case.", [left_evidence])], [left_evidence])
        right = AgentResult(
            agent=AgentName.NETWORK_INTELLIGENCE, intent=Intent.NETWORK_QUERY,
            summary_claims=[claim("Second, 2 links.", [right_evidence])], evidence=[right_evidence],
        )
        merged = merge_results([left, right])
        assert len(merged.summary_claims) == 2
        assert {item.locator for item in merged.evidence} == {"AGG:a", "AGG:b"}
