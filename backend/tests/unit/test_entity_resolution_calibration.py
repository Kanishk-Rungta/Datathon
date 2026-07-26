"""Entity-resolution calibration (implementationv2 §10.2).

The existing ER tests check that individual scoring functions behave. This
file asks the different question a deployment actually needs answered: **at the
published thresholds, how often is the resolver right, and how often does it
send work to a human?**

The labelled set is synthetic and deliberately built around the failure modes
the module claims to handle — Kannada↔English transliteration variance,
dropped initials, patronymic ordering, age drift between case years — plus
near-miss negatives that a naive matcher would wrongly join.

`τ_high` is a *precision* gate: a false auto-link silently fuses two people's
histories and is the expensive error. `τ_low` is a *recall* gate: below it the
pair is dropped without a human ever seeing it. The tests below assert each
threshold against the error it exists to prevent, and print a per-threshold
table so a reviewer can see the trade-off rather than trusting a single number.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pytest

from ksp_cip.application.graph.entity_resolution import EntityResolver

TAU_HIGH = 0.90
TAU_LOW = 0.72


@dataclass(frozen=True)
class LabelledPair:
    """One evaluation pair with ground truth and why it is in the set."""

    label: str
    same_person: bool
    left: dict
    right: dict
    pattern: str


def _row(accused_id: int, name: str, *, age: int | None = 34, gender: str | None = "M",
         year: int = 2025, district: int | None = 2922, case_id: int | None = None) -> dict:
    return {
        "AccusedMasterID": accused_id,
        "AccusedName": name,
        "AgeYear": age,
        "GenderID": gender,
        "CaseMasterID": case_id if case_id is not None else 1000 + accused_id,
        "CrimeNo": f"1292200500{year}0000{accused_id}",
        "CrimeRegisteredDate": date(year, 6, 1).isoformat(),
        "DistrictID": district,
        "PoliceStationID": 2050,
        "CrimeMinorHeadID": 201,
        "GravityOffenceID": 3,
    }


#: The labelled evaluation set. Positives are the same human recorded twice;
#: negatives are distinct humans a careless matcher could fuse.
LABELLED_PAIRS: list[LabelledPair] = [
    # ---------------------------------------------------------- positives
    LabelledPair(
        "transliteration-sh-s", True,
        _row(1, "Shivakumar Gowda"), _row(2, "Sivakumar Gowda", year=2026),
        "Kannada sh/s transliteration variance",
    ),
    LabelledPair(
        "transliteration-w-u", True,
        _row(3, "Ramesh Gowda"), _row(4, "Ramesh Gouda", year=2026),
        "Gowda/Gouda glide spelling",
    ),
    LabelledPair(
        "dropped-initial", True,
        _row(5, "K. Ramesh Gowda"), _row(6, "Ramesh Gowda", year=2026),
        "initial present in one record only",
    ),
    LabelledPair(
        "transliteration-v-w", True,
        _row(7, "Vishwanath Reddy"), _row(8, "Vishvanath Reddy", year=2026),
        "internal v/w alternation",
    ),
    LabelledPair(
        "age-drift-across-years", True,
        _row(9, "Manjunath Shetty", age=30, year=2023),
        _row(10, "Manjunath Shetty", age=33, year=2026),
        "age advances consistently with the gap between case years",
    ),
    LabelledPair(
        "honorific-prefix", True,
        _row(11, "Sri Basavaraj Patil"), _row(12, "Basavaraj Patil", year=2026),
        "honorific stripped",
    ),
    LabelledPair(
        "cross-district-same-person", True,
        _row(13, "Nagaraj Hegde", district=2922),
        _row(14, "Nagaraj Hegde", district=2904, year=2026),
        "same name, different district — geo is a weak prior, not a veto",
    ),
    # ---------------------------------------------------------- negatives
    LabelledPair(
        "different-surname", False,
        _row(20, "Ramesh Gowda"), _row(21, "Ramesh Shetty", year=2026),
        "shared given name only",
    ),
    LabelledPair(
        "gender-mismatch", False,
        _row(22, "Anil Kumar", gender="M"), _row(23, "Anila Kumari", gender="F", year=2026),
        "near-identical name, incompatible gender — must be vetoed",
    ),
    LabelledPair(
        "age-incompatible", False,
        _row(24, "Suresh Rao", age=25, year=2025),
        _row(25, "Suresh Rao", age=58, year=2026),
        "same name, biologically incompatible ages",
    ),
    LabelledPair(
        "common-name-different-people", False,
        _row(26, "Manjunath Kumar", age=24, year=2024),
        _row(27, "Manjunatha Kumara", age=47, year=2026),
        "common name family, incompatible ages",
    ),
    LabelledPair(
        "conflicting-initials", False,
        _row(28, "K. Prakash Naik"), _row(29, "M. Prakash Naik", year=2026),
        "different initials on an otherwise identical name",
    ),
]


@dataclass
class CalibrationRow:
    threshold: float
    true_positive: int
    false_positive: int
    false_negative: int
    true_negative: int

    @property
    def precision(self) -> float:
        denominator = self.true_positive + self.false_positive
        return self.true_positive / denominator if denominator else 1.0

    @property
    def recall(self) -> float:
        denominator = self.true_positive + self.false_negative
        return self.true_positive / denominator if denominator else 1.0

    @property
    def false_link_rate(self) -> float:
        denominator = self.false_positive + self.true_negative
        return self.false_positive / denominator if denominator else 0.0


def score_for(pair: LabelledPair, resolver: EntityResolver) -> float:
    left, right = resolver.to_records([pair.left, pair.right])
    return resolver.score_pair(left, right)["score"]


def calibrate(threshold: float, scores: dict[str, float]) -> CalibrationRow:
    row = CalibrationRow(threshold, 0, 0, 0, 0)
    for pair in LABELLED_PAIRS:
        linked = scores[pair.label] >= threshold
        if pair.same_person and linked:
            row.true_positive += 1
        elif pair.same_person and not linked:
            row.false_negative += 1
        elif not pair.same_person and linked:
            row.false_positive += 1
        else:
            row.true_negative += 1
    return row


@pytest.fixture(scope="module")
def resolver() -> EntityResolver:
    return EntityResolver(tau_high=TAU_HIGH, tau_low=TAU_LOW)


@pytest.fixture(scope="module")
def scores(resolver) -> dict[str, float]:
    return {pair.label: score_for(pair, resolver) for pair in LABELLED_PAIRS}


class TestThresholdCalibration:
    def test_tau_high_makes_no_false_auto_links(self, scores):
        """The expensive error: silently fusing two people's histories."""
        row = calibrate(TAU_HIGH, scores)
        offenders = [
            pair.label for pair in LABELLED_PAIRS
            if not pair.same_person and scores[pair.label] >= TAU_HIGH
        ]
        assert row.false_positive == 0, f"auto-linked distinct people: {offenders}"
        assert row.precision == 1.0

    def test_tau_low_does_not_silently_drop_true_matches(self, scores):
        """Below tau_low a pair is discarded without a human ever seeing it."""
        dropped = [
            pair.label for pair in LABELLED_PAIRS
            if pair.same_person and scores[pair.label] < TAU_LOW
        ]
        assert not dropped, f"true matches dropped below review: {dropped}"

    def test_the_review_band_is_small_enough_to_be_worked(self, scores):
        """A review queue nobody can clear is the same as no review at all."""
        in_band = [
            pair.label for pair in LABELLED_PAIRS
            if TAU_LOW <= scores[pair.label] < TAU_HIGH
        ]
        assert len(in_band) <= len(LABELLED_PAIRS) // 2, f"review band too wide: {in_band}"

    def test_gender_veto_holds_regardless_of_name_similarity(self, scores):
        assert scores["gender-mismatch"] == 0.0

    def test_raising_the_threshold_never_adds_a_false_link(self, scores):
        """The invariant that actually holds as the threshold rises.

        Precision is deliberately *not* asserted to be monotonic: raising a
        threshold can drop a true positive while keeping a false one, so
        precision may legitimately fall. False-link rate cannot rise, because
        a higher bar can only remove links.
        """
        rates = [calibrate(t / 100, scores).false_link_rate for t in range(60, 100, 5)]
        assert rates == sorted(rates, reverse=True), (
            "a higher threshold produced more false links: " + str(rates)
        )

    def test_conflicting_initials_go_to_review_not_auto_link(self, scores):
        """Regression guard for the defect this suite found.

        "K. Prakash Naik" vs "M. Prakash Naik" scored 0.94 on name similarity
        alone and auto-linked. It must land in the review band instead.
        """
        assert TAU_LOW <= scores["conflicting-initials"] < TAU_HIGH

    def test_calibration_table_is_reportable(self, scores, capsys):
        """Emit the table a reviewer signs off on when changing a threshold."""
        lines = ["", f"{'tau':>6} {'prec':>6} {'recall':>7} {'false-link':>11}"]
        for step in range(60, 100, 5):
            row = calibrate(step / 100, scores)
            lines.append(
                f"{row.threshold:>6.2f} {row.precision:>6.2f} {row.recall:>7.2f} "
                f"{row.false_link_rate:>11.2f}"
            )
        print("\n".join(lines))
        captured = capsys.readouterr()
        assert "tau" in captured.out


class TestPatternCoverage:
    @pytest.mark.parametrize(
        "label", [pair.label for pair in LABELLED_PAIRS if pair.same_person]
    )
    def test_each_positive_pattern_scores_above_review(self, label, scores):
        """Named so a failure says which transliteration pattern regressed."""
        assert scores[label] >= TAU_LOW

    @pytest.mark.parametrize(
        "label", [pair.label for pair in LABELLED_PAIRS if not pair.same_person]
    )
    def test_each_negative_pattern_stays_below_auto_link(self, label, scores):
        assert scores[label] < TAU_HIGH

    def test_every_pair_documents_why_it_is_in_the_set(self):
        for pair in LABELLED_PAIRS:
            assert pair.pattern, f"{pair.label} has no stated rationale"


class TestDecisionsAreReversible:
    def test_resolution_links_rather_than_merges(self, resolver):
        """Every source row id survives; nothing is destructively merged."""
        rows = [_row(1, "Shivakumar Gowda"), _row(2, "Sivakumar Gowda", year=2026)]
        records = resolver.to_records(rows)
        _links, identities = resolver.resolve(records)
        retained = {rid for identity in identities for rid in identity.source_ids}
        assert retained == {1, 2}

    def test_a_review_band_pair_does_not_form_an_identity(self, resolver):
        """Only auto-links build identities; review pairs wait for a human."""
        rows = [_row(1, "Ramesh Gowda"), _row(2, "Ramesh Shetty", year=2026)]
        records = resolver.to_records(rows)
        _links, identities = resolver.resolve(records)
        assert len(identities) == 2
