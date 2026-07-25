"""Entity resolution over accused records (architecture §8.3).

Indian police name data resolves badly with naive matching: transliteration
variance, patronymics, no DOB (only ``AgeYear``), no national identifier in
this schema. The pipeline implemented here is the one the architecture
prescribes, at hackathon scale:

1. **Normalize** — NFC, honorific stripping, alias splitting, casefold.
2. **Block** — candidates only within (phonetic key, district-or-adjacent,
   age band adjusted for the gap between case years, gender).
3. **Score** — Jaro-Winkler + token-set ratio + character-trigram cosine +
   age/gender/geo compatibility priors, combined with fixed, published weights.
4. **Decide** — ``≥ τ_high`` auto-links; between ``τ_low`` and ``τ_high`` goes
   to an analyst review queue; below is dropped. **Nothing is ever merged
   irreversibly**: identities are connected components over auto-links, and
   every source row id is retained.
5. **Explain** — every link stores its full feature vector and score.

Deliberately excluded: any individual risk score for a *person's future
behaviour*. The offender score computed here summarises recorded history only,
and every component is shown.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from typing import Any, Iterable, Sequence

from ...domain.value_objects import normalize_person_name, split_name_initials
from ..analytics import stats

VOWELS = set("aeiou")
_NON_ALPHA = re.compile(r"[^a-z\u0C80-\u0CFF ]+")

# Published feature weights. Changing these changes published scores, so they
# live in one place and are asserted by tests.
FEATURE_WEIGHTS = {
    "name_jaro_winkler": 0.32,
    "name_token_set": 0.22,
    "name_trigram_cosine": 0.20,
    "initial_compatibility": 0.06,
    "age_compatibility": 0.14,
    "geo_compatibility": 0.03,
    "gender_compatibility": 0.03,
}

#: Gender is a *veto*, not a weight: a male and a female record are not the
#: same person regardless of how alike the names are. Applying it as a
#: multiplier rather than an addend prevents a near-identical name from
#: dragging an incompatible pair over the threshold.
GENDER_VETO = True


@dataclass(slots=True)
class AccusedRecord:
    accused_master_id: int
    name: str
    normalized: str
    core_name: str
    initials: list[str]
    phonetic: str
    age_year: int | None
    gender_id: str | None
    case_master_id: int
    crime_no: str
    registered_date: date | None
    district_id: int | None
    unit_id: int | None
    crime_sub_head_id: int | None
    gravity_offence_id: int | None


@dataclass(slots=True)
class CandidateLink:
    link_id: str
    left_accused_id: int
    right_accused_id: int
    score: float
    decision: str
    features: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Identity:
    identity_id: str
    canonical_name: str
    normalized_name: str
    phonetic_key: str
    age_estimate: int | None
    gender_id: str | None
    district_ids: list[int]
    unit_ids: list[int]
    source_ids: list[int]
    case_ids: list[int]
    crime_nos: list[str]


# ------------------------------------------------------------- normalization


def phonetic_key(name: str) -> str:
    """Indic-aware consonant-skeleton key used for blocking.

    Not a full double-metaphone: it collapses the specific confusions that
    dominate Kannada↔English transliteration (s/sh, v/w, b/bh aspirates,
    doubled consonants, terminal vowels) which is what blocking needs.
    """
    text = unicodedata.normalize("NFKD", normalize_person_name(name))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = _NON_ALPHA.sub("", text.lower())
    if not text:
        return ""
    replacements = (
        ("sh", "s"), ("ch", "c"), ("ph", "f"), ("th", "t"), ("dh", "d"),
        ("bh", "b"), ("gh", "g"), ("kh", "k"), ("jh", "j"),
        ("z", "j"), ("q", "k"), ("x", "ks"),
    )
    for source, target in replacements:
        text = text.replace(source, target)
    # 'w' behaves in two different ways in Kannada transliteration. Word-initially
    # it alternates with 'v' (Wasim / Vasim), so it is folded to 'v'. Inside a
    # word it is usually a glide spelling of a vowel (Gowda / Gouda,
    # Vishwanath / Vishvanath), so it is dropped. Treating both the same way is
    # what made "Gowda" and "Gouda" land in different blocks.
    text = " ".join(
        ("v" + token[1:].replace("w", "")) if token.startswith("w") else token.replace("w", "")
        for token in text.split()
    )
    keys: list[str] = []
    for token in text.split():
        skeleton: list[str] = []
        previous = ""
        for index, char in enumerate(token):
            if char in VOWELS and index > 0:
                continue
            if char == previous:
                continue
            skeleton.append(char)
            previous = char
        keys.append("".join(skeleton)[:6])
    keys.sort()
    return "-".join(k for k in keys if k)


def trigrams(text: str) -> set[str]:
    padded = f"  {text} "
    return {padded[i:i + 3] for i in range(len(padded) - 2)}


def trigram_cosine(left: str, right: str) -> float:
    a, b = trigrams(left), trigrams(right)
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    return intersection / ((len(a) ** 0.5) * (len(b) ** 0.5))


def jaro_winkler(left: str, right: str) -> float:
    if left == right:
        return 1.0
    if not left or not right:
        return 0.0
    match_window = max(len(left), len(right)) // 2 - 1
    if match_window < 0:
        match_window = 0
    left_flags = [False] * len(left)
    right_flags = [False] * len(right)
    matches = 0
    for i, char in enumerate(left):
        start = max(0, i - match_window)
        end = min(i + match_window + 1, len(right))
        for j in range(start, end):
            if right_flags[j] or right[j] != char:
                continue
            left_flags[i] = right_flags[j] = True
            matches += 1
            break
    if matches == 0:
        return 0.0
    transpositions = 0
    k = 0
    for i, flagged in enumerate(left_flags):
        if not flagged:
            continue
        while not right_flags[k]:
            k += 1
        if left[i] != right[k]:
            transpositions += 1
        k += 1
    transpositions //= 2
    jaro = (matches / len(left) + matches / len(right) + (matches - transpositions) / matches) / 3
    prefix = 0
    for a, b in zip(left[:4], right[:4]):
        if a != b:
            break
        prefix += 1
    return jaro + prefix * 0.1 * (1 - jaro)


def token_set_ratio(left: str, right: str) -> float:
    a, b = set(left.split()), set(right.split())
    if not a or not b:
        return 0.0
    common = a & b
    if common and (a - common or b - common):
        return max(
            SequenceMatcher(None, " ".join(sorted(common)), " ".join(sorted(a))).ratio(),
            SequenceMatcher(None, " ".join(sorted(common)), " ".join(sorted(b))).ratio(),
        )
    return len(common) / len(a | b) if not common else 1.0 if a == b else len(common) / len(a | b)


# --------------------------------------------------------------- pipeline


class EntityResolver:
    def __init__(self, *, tau_high: float = 0.90, tau_low: float = 0.72, age_band: int = 3) -> None:
        self._tau_high = tau_high
        self._tau_low = tau_low
        self._age_band = age_band

    # -------------------------------------------------------------- inputs
    @staticmethod
    def to_records(rows: Iterable[dict[str, Any]]) -> list[AccusedRecord]:
        records: list[AccusedRecord] = []
        for row in rows:
            name = str(row.get("AccusedName") or "").strip()
            if not name:
                continue
            registered = row.get("CrimeRegisteredDate")
            core, initials = split_name_initials(name)
            records.append(
                AccusedRecord(
                    accused_master_id=int(row["AccusedMasterID"]),
                    name=name,
                    normalized=normalize_person_name(name),
                    core_name=core,
                    initials=initials,
                    phonetic=phonetic_key(core),
                    age_year=row.get("AgeYear"),
                    gender_id=str(row["GenderID"]) if row.get("GenderID") is not None else None,
                    case_master_id=int(row["CaseMasterID"]),
                    crime_no=str(row.get("CrimeNo") or ""),
                    registered_date=date.fromisoformat(str(registered)[:10]) if registered else None,
                    district_id=row.get("DistrictID"),
                    unit_id=row.get("PoliceStationID"),
                    crime_sub_head_id=row.get("CrimeMinorHeadID"),
                    gravity_offence_id=row.get("GravityOffenceID"),
                )
            )
        return records

    # ------------------------------------------------------------ blocking
    def blocks(self, records: Sequence[AccusedRecord]) -> dict[str, list[AccusedRecord]]:
        """Candidate generation.

        Blocking keys are built from the *core* name (initials removed), which
        is what makes "K. Ramesh Gowda" and "Ramesh Gowda" land in the same
        bucket. Two keys are used — a phonetic skeleton and the surname — so a
        misspelt first name does not lose the pair.
        """
        buckets: dict[str, list[AccusedRecord]] = defaultdict(list)
        for record in records:
            if record.phonetic:
                buckets[f"p:{record.phonetic}"].append(record)
            tokens = record.core_name.split()
            if tokens:
                buckets[f"s:{tokens[-1]}"].append(record)
        return {key: items for key, items in buckets.items() if 1 < len(items) <= 400}

    # ------------------------------------------------------------- scoring
    def score_pair(self, left: AccusedRecord, right: AccusedRecord) -> dict[str, float]:
        # Names are compared on their core form; initials are scored separately.
        name_jw = jaro_winkler(left.core_name, right.core_name)
        name_ts = token_set_ratio(left.core_name, right.core_name)
        name_tc = trigram_cosine(left.core_name, right.core_name)

        left_initials, right_initials = set(left.initials), set(right.initials)
        if not left_initials or not right_initials:
            # One record simply omits the initials — common and uninformative.
            initial_compat = 0.7
        elif left_initials & right_initials:
            initial_compat = 1.0
        else:
            initial_compat = 0.0

        age_compat = 0.5
        if left.age_year and right.age_year and left.registered_date and right.registered_date:
            year_gap = abs(left.registered_date.year - right.registered_date.year)
            expected = abs((left.age_year - right.age_year)) - year_gap
            age_compat = 1.0 if abs(expected) <= self._age_band else max(0.0, 1.0 - abs(expected) / 12.0)
        elif left.age_year and right.age_year:
            age_compat = 1.0 if abs(left.age_year - right.age_year) <= self._age_band else 0.3

        geo_compat = 1.0 if left.district_id == right.district_id else 0.45
        genders = {left.gender_id, right.gender_id} - {None, ""}
        gender_compat = 0.0 if len(genders) > 1 else 1.0

        features = {
            "name_jaro_winkler": round(name_jw, 4),
            "name_token_set": round(name_ts, 4),
            "name_trigram_cosine": round(name_tc, 4),
            "initial_compatibility": round(initial_compat, 4),
            "age_compatibility": round(age_compat, 4),
            "geo_compatibility": round(geo_compat, 4),
            "gender_compatibility": round(gender_compat, 4),
        }
        score = sum(FEATURE_WEIGHTS[key] * value for key, value in features.items())
        if GENDER_VETO and gender_compat == 0.0:
            score = 0.0
            features["vetoed_by"] = "gender"
        features["score"] = round(score, 4)
        return features

    def resolve(self, records: Sequence[AccusedRecord]) -> tuple[list[CandidateLink], list[Identity]]:
        links: list[CandidateLink] = []
        seen: set[tuple[int, int]] = set()
        for block in self.blocks(records).values():
            for i, left in enumerate(block):
                for right in block[i + 1:]:
                    if left.accused_master_id == right.accused_master_id:
                        continue
                    key = tuple(sorted((left.accused_master_id, right.accused_master_id)))
                    if key in seen:
                        continue
                    seen.add(key)
                    features = self.score_pair(left, right)
                    score = features["score"]
                    if score < self._tau_low:
                        continue
                    decision = "auto_link" if score >= self._tau_high else "review"
                    links.append(
                        CandidateLink(
                            link_id=hashlib.blake2s(f"{key[0]}:{key[1]}".encode(), digest_size=8).hexdigest(),
                            left_accused_id=key[0],
                            right_accused_id=key[1],
                            score=round(score, 4),
                            decision=decision,
                            features=features,
                        )
                    )
        identities = self.build_identities(records, [link for link in links if link.decision == "auto_link"])
        return links, identities

    # ---------------------------------------------------------- identities
    @staticmethod
    def build_identities(records: Sequence[AccusedRecord], auto_links: Sequence[CandidateLink]) -> list[Identity]:
        """Connected components over auto-links. Reversible by construction."""
        parent: dict[int, int] = {record.accused_master_id: record.accused_master_id for record in records}

        def find(node: int) -> int:
            while parent[node] != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        def union(a: int, b: int) -> None:
            root_a, root_b = find(a), find(b)
            if root_a != root_b:
                parent[max(root_a, root_b)] = min(root_a, root_b)

        for link in auto_links:
            if link.left_accused_id in parent and link.right_accused_id in parent:
                union(link.left_accused_id, link.right_accused_id)

        clusters: dict[int, list[AccusedRecord]] = defaultdict(list)
        for record in records:
            clusters[find(record.accused_master_id)].append(record)

        identities: list[Identity] = []
        for root, members in clusters.items():
            members.sort(key=lambda r: (r.registered_date or date.min, r.accused_master_id))
            canonical = max((m.name for m in members), key=len)
            ages = [m.age_year for m in members if m.age_year]
            identities.append(
                Identity(
                    identity_id=f"P{root:08d}",
                    canonical_name=canonical,
                    normalized_name=normalize_person_name(canonical),
                    phonetic_key=phonetic_key(split_name_initials(canonical)[0]),
                    age_estimate=int(round(stats.mean([float(a) for a in ages]))) if ages else None,
                    gender_id=next((m.gender_id for m in members if m.gender_id), None),
                    district_ids=sorted({m.district_id for m in members if m.district_id is not None}),
                    unit_ids=sorted({m.unit_id for m in members if m.unit_id is not None}),
                    source_ids=sorted({m.accused_master_id for m in members}),
                    case_ids=sorted({m.case_master_id for m in members}),
                    crime_nos=sorted({m.crime_no for m in members if m.crime_no}),
                )
            )
        return identities


# ------------------------------------------------------- offender scoring


HEINOUS_GRAVITY_IDS = {1}
OFFENDER_SCORE_BANDS: tuple[tuple[float, str], ...] = ((70.0, "high"), (45.0, "medium"), (0.0, "low"))


def score_offenders(
    identities: Sequence[Identity],
    records_by_accused: dict[int, AccusedRecord],
    *,
    centrality: dict[str, float] | None = None,
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    """Transparent, rule-based repeat-offender score over *recorded history*.

    This is explicitly not a prediction about a person. Each component is a
    count or a recency measured from the record, and every weight is returned
    alongside the score (plan §6.7, architecture §9.3's exclusion of
    individual risk scoring).
    """
    as_of = as_of or date.today()
    centrality = centrality or {}
    results: list[dict[str, Any]] = []
    for identity in identities:
        members = [records_by_accused[a] for a in identity.source_ids if a in records_by_accused]
        if len(identity.case_ids) < 2:
            continue
        dates = [m.registered_date for m in members if m.registered_date]
        latest = max(dates) if dates else None
        recency_days = (as_of - latest).days if latest else None
        sub_heads = {m.crime_sub_head_id for m in members if m.crime_sub_head_id is not None}

        ordered = sorted((m for m in members if m.registered_date), key=lambda m: m.registered_date)  # type: ignore[arg-type]
        escalation = 0.0
        if len(ordered) >= 2:
            first_gravity = ordered[0].gravity_offence_id or 99
            last_gravity = ordered[-1].gravity_offence_id or 99
            # Lower GravityOffenceID means more serious in the seeded master.
            escalation = 1.0 if last_gravity < first_gravity else 0.0

        node_centrality = centrality.get(f"person:{identity.identity_id}", 0.0)

        case_component = min(40.0, len(identity.case_ids) * 8.0)
        variety_component = min(15.0, len(sub_heads) * 5.0)
        recency_component = 0.0
        if recency_days is not None:
            recency_component = 20.0 if recency_days <= 180 else 12.0 if recency_days <= 365 else 4.0
        escalation_component = 10.0 * escalation
        centrality_component = min(15.0, node_centrality * 100.0)

        components = [
            {"name": "recorded cases", "value": len(identity.case_ids), "weight": round(case_component, 2),
             "rationale": "8 points per linked case, capped at 40."},
            {"name": "distinct crime sub-heads", "value": len(sub_heads), "weight": round(variety_component, 2),
             "rationale": "5 points per distinct sub-head, capped at 15."},
            {"name": "days since most recent case", "value": recency_days,
             "weight": round(recency_component, 2), "rationale": "20 within 6 months, 12 within a year, else 4."},
            {"name": "gravity escalation across cases", "value": bool(escalation),
             "weight": round(escalation_component, 2),
             "rationale": "10 points if the most recent offence is graver than the first."},
            {"name": "network centrality", "value": round(node_centrality, 4),
             "weight": round(centrality_component, 2),
             "rationale": "Degree centrality × 100, capped at 15."},
        ]
        score = sum(item["weight"] for item in components)
        results.append(
            {
                "identity_id": identity.identity_id,
                "canonical_name": identity.canonical_name,
                "case_count": len(identity.case_ids),
                "distinct_crime_heads": len(sub_heads),
                "recency_days": recency_days,
                "gravity_escalation": escalation,
                "network_centrality": round(node_centrality, 4),
                "score": round(min(score, 100.0), 2),
                "band": stats.band_for(score, OFFENDER_SCORE_BANDS),
                "components": {
                    "items": components,
                    "formula": "score = Σ component weights, capped at 100",
                    "max_possible": 100.0,
                },
                "case_ids": identity.case_ids,
                "district_ids": identity.district_ids,
                "unit_ids": identity.unit_ids,
            }
        )
    results.sort(key=lambda item: item["score"], reverse=True)
    return results
