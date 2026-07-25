"""Entity resolution: matching, thresholds and the no-silent-merge rule."""

from datetime import date

import pytest

from ksp_cip.application.graph import entity_resolution as er
from ksp_cip.domain.value_objects import normalize_person_name, split_name_initials


def record(accused_id, name, *, age=30, gender="M", case_id=None, district=1, year=2026):
    normalized = normalize_person_name(name)
    core, initials = split_name_initials(normalized)
    return er.AccusedRecord(
        accused_master_id=accused_id,
        name=name,
        normalized=normalized,
        core_name=core,
        initials=initials,
        phonetic=er.phonetic_key(name),
        age_year=age,
        gender_id=gender,
        case_master_id=case_id if case_id is not None else accused_id,
        crime_no=f"1{district:04d}0001{year}0000{accused_id}",
        registered_date=date(year, 6, 1),
        district_id=district,
        unit_id=10,
        crime_sub_head_id=201,
        gravity_offence_id=3,
    )


class TestSimilarityFunctions:
    def test_jaro_winkler_bounds(self):
        assert er.jaro_winkler("ramesh", "ramesh") == 1.0
        assert er.jaro_winkler("", "ramesh") == 0.0
        assert 0.8 < er.jaro_winkler("ramesh", "ramesha") < 1.0

    def test_transliteration_variants_share_a_phonetic_key(self):
        assert er.phonetic_key("Ramesh Gowda") == er.phonetic_key("Ramesha Gouda")
        assert er.phonetic_key("Shivakumar") == er.phonetic_key("Sivakumar")
        assert er.phonetic_key("Wasim Khan") == er.phonetic_key("Vasim Khan")

    def test_unrelated_names_do_not_collide(self):
        assert er.phonetic_key("Ramesh Gowda") != er.phonetic_key("Lakshmi Shetty")


class TestScoring:
    def test_weights_sum_to_one(self):
        """Published weights must be a true weighted average, or the score is not in [0, 1]."""
        assert sum(er.FEATURE_WEIGHTS.values()) == pytest.approx(1.0)

    def test_identical_records_score_at_the_top(self):
        resolver = er.EntityResolver()
        features = resolver.score_pair(record(1, "Ramesh Gowda"), record(2, "Ramesh Gowda"))
        assert features["score"] > 0.95

    def test_different_people_score_low(self):
        resolver = er.EntityResolver()
        features = resolver.score_pair(
            record(1, "Ramesh Gowda", gender="M"),
            record(2, "Lakshmi Shetty", gender="F", district=9),
        )
        assert features["score"] < 0.5

    def test_every_score_carries_its_features(self):
        resolver = er.EntityResolver()
        features = resolver.score_pair(record(1, "Ramesh Gowda"), record(2, "Ramesha Gouda"))
        for name in er.FEATURE_WEIGHTS:
            assert name in features, f"{name} missing from the explanation"


class TestDecisions:
    def test_high_scores_auto_link_and_middling_scores_queue_for_review(self):
        resolver = er.EntityResolver(tau_high=0.90, tau_low=0.72)
        records = [
            record(1, "Ramesh Gowda", age=30, year=2024),
            record(2, "Ramesh Gowda", age=32, year=2026),
            record(3, "Ramesha Gouda", age=45, district=7, year=2026),
        ]
        links, identities = resolver.resolve(records)
        decisions = {link.decision for link in links}
        assert "auto_link" in decisions
        assert all(link.score >= 0.72 for link in links)
        assert identities

    def test_below_tau_low_is_dropped_entirely(self):
        resolver = er.EntityResolver(tau_high=0.99, tau_low=0.98)
        links, _identities = resolver.resolve([record(1, "Ramesh Gowda"), record(2, "Lakshmi Shetty")])
        assert links == []

    def test_review_band_never_merges(self):
        """The critical safety property: only auto-links form identities."""
        resolver = er.EntityResolver(tau_high=0.999, tau_low=0.10)
        records = [record(1, "Ramesh Gowda"), record(2, "Ramesh Gowda")]
        links, identities = resolver.resolve(records)
        assert all(link.decision == "review" for link in links)
        assert len(identities) == 2, "review-band candidates must stay separate"

    def test_identities_retain_every_source_row(self):
        resolver = er.EntityResolver(tau_high=0.5, tau_low=0.4)
        records = [record(1, "Ramesh Gowda"), record(2, "Ramesh Gowda"), record(3, "Ramesh Gowda")]
        _links, identities = resolver.resolve(records)
        recovered = sorted(i for identity in identities for i in identity.source_ids)
        assert recovered == [1, 2, 3], "resolution must be reversible to its inputs"


class TestOffenderScoring:
    def test_single_case_people_are_not_scored(self):
        resolver = er.EntityResolver()
        records = [record(1, "Ramesh Gowda")]
        _links, identities = resolver.resolve(records)
        scores = er.score_offenders(identities, {r.accused_master_id: r for r in records})
        assert scores == []

    def test_score_is_bounded_and_fully_explained(self):
        resolver = er.EntityResolver(tau_high=0.5, tau_low=0.4)
        records = [record(1, "Ramesh Gowda", case_id=11), record(2, "Ramesh Gowda", case_id=22)]
        _links, identities = resolver.resolve(records)
        scores = er.score_offenders(identities, {r.accused_master_id: r for r in records},
                                    as_of=date(2026, 7, 25))
        assert scores
        entry = scores[0]
        assert 0 <= entry["score"] <= 100
        assert entry["components"]["items"]
        assert sum(item["weight"] for item in entry["components"]["items"]) == pytest.approx(
            entry["score"], abs=0.01
        )
