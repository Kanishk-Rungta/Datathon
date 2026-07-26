"""The Kannada ASR benchmark's own correctness.

The benchmark cannot be *run* here — that needs audio and real model weights.
Its scoring can be, and must be: a benchmark whose metric is wrong is worse
than none, because it produces a number people trust.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.evals.kannada_asr.harness import (
    CRITICAL_TERM_RECALL_TARGET,
    Fixture,
    load_corpus,
    score,
    summarise,
    word_error_rate,
)


class TestCorpus:
    def test_the_corpus_loads_and_is_not_trivial(self):
        corpus = load_corpus()
        assert len(corpus) >= 20
        assert all(f.reference and f.critical_terms for f in corpus)

    def test_ids_are_unique(self):
        ids = [f.id for f in load_corpus()]
        assert len(ids) == len(set(ids))

    def test_every_fixture_is_actually_kannada(self):
        """A Latin-script "Kannada" fixture would measure nothing."""
        for fixture in load_corpus():
            assert any("ಀ" <= ch <= "೿" for ch in fixture.reference), fixture.id

    def test_critical_terms_appear_in_their_own_reference(self):
        """A term that is not in the sentence can never be recalled from it."""
        for fixture in load_corpus():
            for term in fixture.critical_terms:
                assert term in fixture.reference, f"{fixture.id}: {term!r} not in reference"

    def test_the_vocabulary_the_platform_routes_on_is_covered(self):
        tags = {tag for fixture in load_corpus() for tag in fixture.tags}
        # These are the categories a wrong transcript would actually break.
        assert {"crime_term", "district", "legal_term", "proper_noun"} <= tags


class TestWordErrorRate:
    def test_an_exact_match_scores_zero(self):
        assert word_error_rate("ಕಳ್ಳತನ ಪ್ರಕರಣ", "ಕಳ್ಳತನ ಪ್ರಕರಣ") == 0.0

    def test_a_substitution_is_counted(self):
        assert word_error_rate("ಕಳ್ಳತನ ಪ್ರಕರಣ", "ಕೊಲೆ ಪ್ರಕರಣ") == pytest.approx(0.5)

    def test_a_dropped_word_is_counted(self):
        assert word_error_rate("ಕಳ್ಳತನ ಪ್ರಕರಣ", "ಕಳ್ಳತನ") == pytest.approx(0.5)

    def test_an_empty_hypothesis_is_total_error(self):
        assert word_error_rate("ಕಳ್ಳತನ ಪ್ರಕರಣ", "") == 1.0

    def test_punctuation_does_not_count_as_error(self):
        assert word_error_rate("ಕಳ್ಳತನ ಪ್ರಕರಣ", "ಕಳ್ಳತನ, ಪ್ರಕರಣ.") == 0.0


class TestCriticalTermRecall:
    fixture = Fixture(
        id="t1", reference="ಮೈಸೂರು ಜಿಲ್ಲೆಯಲ್ಲಿ ಕಳ್ಳತನ",
        gloss="theft in Mysuru", critical_terms=["ಮೈಸೂರು", "ಕಳ್ಳತನ"], tags=["crime_term"],
    )

    def test_all_terms_present_is_full_recall(self):
        assert score(self.fixture, "ಮೈಸೂರು ಜಿಲ್ಲೆಯಲ್ಲಿ ಕಳ್ಳತನ").term_recall == 1.0

    def test_a_dropped_crime_term_is_caught(self):
        outcome = score(self.fixture, "ಮೈಸೂರು ಜಿಲ್ಲೆಯಲ್ಲಿ")
        assert outcome.term_recall == 0.5
        assert "ಕಳ್ಳತನ" in outcome.terms_missed

    def test_a_fluent_transcript_that_loses_the_terms_still_fails(self):
        """The whole point: low WER with the key terms gone is not success."""
        outcome = score(self.fixture, "ಮೈಸೂರು ಜಿಲ್ಲೆಯಲ್ಲಿ ಕೊಲೆ")
        assert outcome.word_error_rate < 0.5, "only one word differs"
        assert outcome.term_recall == 0.5, "but the crime term was lost"


class TestSummary:
    def test_the_summary_reports_against_the_target(self):
        corpus = load_corpus()[:5]
        perfect = [score(f, f.reference) for f in corpus]
        report = summarise(perfect)
        assert report["critical_term_recall"] == 1.0
        assert report["meets_target"] is True
        assert report["target"] == CRITICAL_TERM_RECALL_TARGET

    def test_a_failing_run_is_reported_as_failing(self):
        corpus = load_corpus()[:5]
        deaf = [score(f, "") for f in corpus]
        report = summarise(deaf)
        assert report["critical_term_recall"] == 0.0
        assert report["meets_target"] is False
        assert report["worst"], "a failing run must name what was missed"

    def test_recall_is_broken_down_by_vocabulary_kind(self):
        """"Which words does it lose" is the actionable question."""
        report = summarise([score(f, f.reference) for f in load_corpus()])
        assert "crime_term" in report["recall_by_tag"]
        assert "proper_noun" in report["recall_by_tag"]

    def test_an_empty_run_does_not_claim_success(self):
        assert summarise([])["fixtures"] == 0
