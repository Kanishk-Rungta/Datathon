"""Case (fact) data generation.

The generator's purpose is not volume; it is *structure*. Analytics that
detect nothing on flat random data prove nothing, so the following signals are
planted deliberately and recorded in the manifest so tests can assert they were
found again:

* **Seasonality** — property crime peaks in the festival months.
* **A rising trend** in one district and crime type, steep enough for the
  early-warning z-score to fire.
* **Hotspots** — three tight geographic clusters, each far above the ambient
  rate for its grid cell.
* **A network ring** — a set of people who repeatedly appear as co-accused
  across districts, with transliteration variants of their names so that entity
  resolution has to earn the link.
* **Repeat offenders** — a population with a realistic long tail of
  multi-case involvement.

Everything is generated from a single seeded ``random.Random``; nothing depends
on wall-clock time except the anchor date, which is passed in explicitly.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Sequence

from ....domain.value_objects import CrimeNo
from .masters import (
    CRIME_TAXONOMY,
    KARNATAKA_DISTRICTS,
    SECTION_MAP,
    MasterData,
    name_variants_of,
    person_name,
)

MONTHLY_SEASONALITY = {
    1: 0.95, 2: 0.92, 3: 1.00, 4: 1.05, 5: 1.08, 6: 0.98,
    7: 0.95, 8: 1.02, 9: 1.10, 10: 1.22, 11: 1.18, 12: 1.06,
}

BRIEF_FACTS_TEMPLATES: dict[str, list[str]] = {
    "theft": [
        "The complainant reported that unknown persons removed {item} from {place} at around {time} "
        "on {day}. The property is valued at approximately Rs. {value}. No one was injured.",
        "On {day} the complainant found the lock of {place} broken and {item} missing. Neighbours "
        "reported seeing two unidentified persons near the premises around {time}.",
    ],
    "vehicle": [
        "The complainant parked a two-wheeler bearing registration KA-{reg} outside {place} on {day} "
        "at about {time}. On returning, the vehicle was not found at the spot.",
        "A four-wheeler parked near {place} was found missing on the morning of {day}. The complainant "
        "states the vehicle was locked and no spare key was left inside.",
    ],
    "hurt": [
        "A dispute over {reason} at {place} on {day} led to an altercation in which the complainant "
        "sustained injuries. The complainant was treated at the government hospital.",
        "The complainant states that the accused abused and assaulted him at {place} around {time} "
        "on {day} following an argument over {reason}.",
    ],
    "cheating": [
        "The complainant states that the accused induced payment of Rs. {value} on the promise of "
        "{promise} and thereafter failed to fulfil the commitment or return the amount.",
        "Between {day} and later dates, the accused collected Rs. {value} from the complainant "
        "towards {promise}. The amount was neither returned nor accounted for.",
    ],
    "cyber": [
        "The complainant received a call from an unknown number on {day} at about {time}. The caller "
        "posed as a bank official and obtained one-time passwords, following which Rs. {value} was "
        "debited from the account in several transactions.",
    ],
    "narcotics": [
        "During a routine check near {place} on {day} at about {time}, the accused was found in "
        "possession of contraband. The material was seized in the presence of panch witnesses.",
    ],
    "public_order": [
        "A group assembled near {place} on {day} at about {time} and caused obstruction and damage "
        "to property. Police intervened and dispersed the gathering.",
    ],
    "women": [
        "The complainant states that she was subjected to harassment at {place} on {day}. The matter "
        "was reported at the police station on the same day.",
        "The complainant reports continued ill-treatment at the matrimonial home. The complaint was "
        "recorded on {day} and the statement of the complainant taken.",
    ],
    "default": [
        "The complainant reported an incident at {place} on {day} at about {time}. The matter has "
        "been registered and taken up for investigation.",
    ],
}

SUB_HEAD_TEMPLATE_KEY = {
    101: "hurt", 102: "hurt", 103: "hurt", 104: "hurt", 105: "default",
    201: "theft", 202: "vehicle", 203: "theft", 204: "theft", 205: "theft",
    206: "theft", 207: "theft",
    301: "women", 302: "women", 303: "women",
    401: "cheating", 402: "cheating", 403: "cheating", 404: "cyber",
    501: "public_order", 502: "public_order", 503: "hurt",
    601: "narcotics", 602: "narcotics", 603: "public_order", 604: "narcotics",
}

PLACES = [
    "the complainant's residence", "a commercial complex", "the bus stand", "a petrol station",
    "the vegetable market", "a residential layout", "the railway station area", "a school compound",
    "an apartment parking area", "the main road junction", "a bank premises", "a temple street",
]
ITEMS = [
    "gold ornaments weighing about 40 grams", "a laptop computer", "cash kept in an almirah",
    "two mobile phones", "household electronic items", "silver articles", "a bicycle",
]
REASONS = ["a monetary transaction", "a parking dispute", "an old enmity", "a land boundary",
           "a family matter", "an argument at a shop"]
PROMISES = ["a job placement", "supply of building material", "a property registration",
            "returns on an investment", "a bank loan approval"]


@dataclass(slots=True)
class GeneratedCase:
    case: dict[str, Any]
    complainants: list[dict[str, Any]] = field(default_factory=list)
    victims: list[dict[str, Any]] = field(default_factory=list)
    accused: list[dict[str, Any]] = field(default_factory=list)
    act_sections: list[dict[str, Any]] = field(default_factory=list)
    arrests: list[dict[str, Any]] = field(default_factory=list)
    chargesheets: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class GenerationManifest:
    """What was planted, so tests can assert it is found again."""

    case_count: int = 0
    seed: int = 0
    anchor_date: str = ""
    hotspots: list[dict[str, Any]] = field(default_factory=list)
    surge: dict[str, Any] = field(default_factory=dict)
    ring: dict[str, Any] = field(default_factory=dict)
    repeat_offenders: list[dict[str, Any]] = field(default_factory=list)
    district_case_counts: dict[str, int] = field(default_factory=dict)
    #: canonical name -> spellings actually written into FIRs, so entity
    #: resolution can be scored against ground truth.
    planted_identities: dict[str, list[str]] = field(default_factory=dict)
    #: The account and day deliberately given a spike of transfers, so the
    #: financial burst analysis can be scored against ground truth the same way.
    financial_burst: dict[str, Any] = field(default_factory=dict)


class CaseGenerator:
    def __init__(self, masters: MasterData, rng: random.Random, *, anchor: date, months: int = 30) -> None:
        self._masters = masters
        self._rng = rng
        self._anchor = anchor
        self._months = months
        self._stations_by_district: dict[int, list[dict[str, Any]]] = {}
        for unit in masters.units:
            if unit["TypeID"] == 6 and unit["DistrictID"] is not None:
                self._stations_by_district.setdefault(int(unit["DistrictID"]), []).append(unit)
        self._employees_by_unit: dict[int, list[dict[str, Any]]] = {}
        for employee in masters.employees:
            self._employees_by_unit.setdefault(int(employee["UnitID"]), []).append(employee)
        self._courts_by_district: dict[int, list[dict[str, Any]]] = {}
        for court in masters.courts:
            self._courts_by_district.setdefault(int(court["DistrictID"]), []).append(court)
        self._district_geo = {
            int(row["DistrictID"]): (row["cip_latitude"], row["cip_longitude"], row["cip_weight"])
            for row in masters.districts
        }
        self._sub_heads = [
            (sub_id, name, share, gravity, head_id)
            for head_id, _group, subs in CRIME_TAXONOMY
            for sub_id, name, share, gravity in subs
        ]
        self._serials: dict[tuple[int, int, int], int] = {}
        self._next_ids = {"case": 1, "complainant": 1, "victim": 1, "accused": 1,
                          "arrest": 1, "chargesheet": 1}

    # ---------------------------------------------------------------- API
    def generate(self, target_cases: int) -> tuple[list[GeneratedCase], GenerationManifest]:
        manifest = GenerationManifest(seed=self._rng.random().__hash__() & 0xFFFF,
                                      anchor_date=self._anchor.isoformat())
        cases: list[GeneratedCase] = []

        ring_members = self._build_ring()
        manifest.ring = {
            "members": [member["canonical"] for member in ring_members],
            "variants": {m["canonical"]: m["variants"] for m in ring_members},
            "case_count": 0,
        }
        hotspot_specs = self._choose_hotspots()
        surge_spec = self._choose_surge()
        manifest.surge = dict(surge_spec)

        repeat_pool = self._build_repeat_pool(max(40, target_cases // 30))

        # Shares are tuned so that planted structure is detectable without any
        # individual appearing in an implausible number of FIRs: a ring member
        # shows up in roughly 10-20 cases, which is what a real recidivist looks
        # like in a 30-month window.
        base_count = int(target_cases * 0.85)
        for _ in range(base_count):
            cases.append(self._make_case(repeat_pool=repeat_pool))

        hotspot_share = int(target_cases * 0.075)
        per_hotspot = max(1, hotspot_share // max(1, len(hotspot_specs)))
        for spec in hotspot_specs:
            planted = 0
            for _ in range(per_hotspot):
                cases.append(self._make_case(repeat_pool=repeat_pool, hotspot=spec))
                planted += 1
            spec["planted_cases"] = planted
        manifest.hotspots = hotspot_specs

        surge_share = int(target_cases * 0.06)  # enough to clear a 2-sigma threshold
        for _ in range(surge_share):
            cases.append(self._make_case(repeat_pool=repeat_pool, surge=surge_spec))

        ring_share = target_cases - len(cases)
        for _ in range(max(0, ring_share)):
            cases.append(self._make_case(repeat_pool=repeat_pool, ring=ring_members))
        manifest.ring["case_count"] = max(0, ring_share)

        counts: dict[str, int] = {}
        district_names = {int(row["DistrictID"]): row["DistrictName"] for row in self._masters.districts}
        for generated in cases:
            unit_id = generated.case["PoliceStationID"]
            district_id = next(
                (int(u["DistrictID"]) for u in self._masters.units if int(u["UnitID"]) == unit_id), None
            )
            if district_id:
                name = district_names.get(district_id, str(district_id))
                counts[name] = counts.get(name, 0) + 1
        manifest.district_case_counts = counts
        manifest.case_count = len(cases)

        # Report the planted repeat population by canonical identity, not by
        # raw string: the whole point is that one person appears under several
        # spellings, so counting strings would understate it.
        variant_to_canonical = {
            variant: member["canonical"]
            for member in list(repeat_pool) + list(ring_members)
            for variant in member["variants"]
        }
        offender_counts: dict[str, int] = {}
        for generated in cases:
            for accused in generated.accused:
                canonical = variant_to_canonical.get(accused["AccusedName"])
                if canonical:
                    offender_counts[canonical] = offender_counts.get(canonical, 0) + 1
        manifest.repeat_offenders = [
            {"name": name, "cases": count}
            for name, count in sorted(offender_counts.items(), key=lambda kv: kv[1], reverse=True)[:15]
            if count > 1
        ]
        manifest.planted_identities = {
            member["canonical"]: member["variants"]
            for member in list(repeat_pool) + list(ring_members)
        }
        return cases, manifest

    # ------------------------------------------------------------ planting
    def _build_ring(self) -> list[dict[str, Any]]:
        """A small set of people who recur together, each spelled several ways.

        This is the entity-resolution test case: the same person is written
        differently at different stations, and only correct resolution reveals
        that the six of them keep appearing on each other's FIRs.
        """
        members: list[dict[str, Any]] = []
        for _ in range(10):
            canonical, gender = person_name(self._rng, allow_variant=False)
            members.append({
                "canonical": canonical,
                "variants": name_variants_of(self._rng, canonical),
                "gender": gender,
                "age": self._rng.randint(22, 46),
            })
        return members

    def _build_repeat_pool(self, size: int) -> list[dict[str, Any]]:
        """People deliberately given multi-case histories, with spelling variance."""
        pool: list[dict[str, Any]] = []
        for _ in range(size):
            canonical, gender = person_name(self._rng, allow_variant=False)
            pool.append({
                "canonical": canonical,
                "variants": name_variants_of(self._rng, canonical),
                "gender": gender,
                "age": self._rng.randint(19, 55),
            })
        return pool

    def _choose_hotspots(self) -> list[dict[str, Any]]:
        candidates = [row for row in self._masters.districts if row["cip_weight"] >= 1.0]
        chosen = self._rng.sample(candidates, k=min(3, len(candidates)))
        specs = []
        for district in chosen:
            stations = self._stations_by_district.get(int(district["DistrictID"]), [])
            if not stations:
                continue
            station = self._rng.choice(stations)
            specs.append({
                "district_id": int(district["DistrictID"]),
                "district_name": district["DistrictName"],
                "unit_id": int(station["UnitID"]),
                "centre_lat": round(district["cip_latitude"] + self._rng.uniform(-0.03, 0.03), 6),
                "centre_lon": round(district["cip_longitude"] + self._rng.uniform(-0.03, 0.03), 6),
                "radius_metres": 450,
                "crime_sub_head_id": self._rng.choice([201, 202, 203, 207]),
                "planted_cases": 0,
            })
        return specs

    def _choose_surge(self) -> dict[str, Any]:
        district = self._rng.choice([row for row in self._masters.districts if row["cip_weight"] >= 1.0])
        return {
            "district_id": int(district["DistrictID"]),
            "district_name": district["DistrictName"],
            "crime_sub_head_id": 404,  # online financial fraud
            "crime_sub_head_name": "Online Financial Fraud",
            "window_days": 45,
        }

    # -------------------------------------------------------- case factory
    def _make_case(
        self,
        *,
        repeat_pool: Sequence[dict[str, Any]],
        hotspot: dict[str, Any] | None = None,
        surge: dict[str, Any] | None = None,
        ring: Sequence[dict[str, Any]] | None = None,
    ) -> GeneratedCase:
        rng = self._rng

        if hotspot:
            district_id = hotspot["district_id"]
            unit = next(u for u in self._masters.units if int(u["UnitID"]) == hotspot["unit_id"])
            sub_head_id = hotspot["crime_sub_head_id"]
            # Hotspot cases sit inside the detection window on purpose: a
            # concentration spread evenly over 30 months is not a hotspot, and
            # planting one that the 90-day detector cannot see would make the
            # validation loop meaningless.
            registered = self._anchor - timedelta(days=self._rng.randint(0, 85))
            latitude, longitude = self._jitter(hotspot["centre_lat"], hotspot["centre_lon"],
                                               hotspot["radius_metres"])
        elif surge:
            district_id = surge["district_id"]
            unit = rng.choice(self._stations_by_district[district_id])
            sub_head_id = surge["crime_sub_head_id"]
            registered = self._anchor - timedelta(days=rng.randint(0, surge["window_days"]))
            latitude, longitude = self._district_point(district_id)
        else:
            district_id = self._weighted_district()
            unit = rng.choice(self._stations_by_district[district_id])
            sub_head_id = self._weighted_sub_head(registered_month=None)
            registered = self._random_date()
            latitude, longitude = self._district_point(district_id)

        sub_head = next(s for s in self._sub_heads if s[0] == sub_head_id)
        gravity_id = sub_head[3]
        head_id = sub_head[4]

        category_code = 1 if rng.random() > 0.06 else rng.choice([3, 4, 8])
        serial_key = (int(unit["UnitID"]), category_code, registered.year)
        self._serials[serial_key] = self._serials.get(serial_key, 0) + 1
        crime_no = CrimeNo.build(
            category_code=category_code,
            district_id=district_id,
            station_id=int(unit["UnitID"]),
            year=registered.year,
            serial=self._serials[serial_key],
        )

        case_id = self._take("case")
        incident_from = datetime.combine(
            registered - timedelta(days=rng.choice([0, 0, 0, 1, 1, 2, 4])),
            self._incident_time(sub_head_id),
        )
        incident_to = incident_from + timedelta(hours=rng.choice([0, 1, 2, 6]))
        info_received = incident_to + timedelta(hours=rng.choice([1, 2, 3, 8, 20]))
        status_id, chargesheet = self._status_for(registered)
        courts = self._courts_by_district.get(district_id, [])
        officers = self._employees_by_unit.get(int(unit["UnitID"]), [])
        officer = rng.choice(officers) if officers else None

        brief_facts = self._brief_facts(sub_head_id, registered, rng)
        case_row = {
            "CaseMasterID": case_id,
            "CrimeNo": crime_no.raw,
            "CaseNo": crime_no.case_no,
            "CrimeRegisteredDate": registered.isoformat(),
            "PolicePersonID": int(officer["EmployeeID"]) if officer else None,
            "PoliceStationID": int(unit["UnitID"]),
            "CaseCategoryID": category_code,
            "GravityOffenceID": gravity_id,
            "CrimeMajorHeadID": head_id,
            "CrimeMinorHeadID": sub_head_id,
            "CaseStatusID": status_id,
            "CourtID": int(rng.choice(courts)["CourtID"]) if courts and status_id >= 2 else None,
            "IncidentFromDate": incident_from.isoformat(sep=" ", timespec="seconds"),
            "IncidentToDate": incident_to.isoformat(sep=" ", timespec="seconds"),
            "InfoReceivedPSDate": info_received.isoformat(sep=" ", timespec="seconds"),
            "latitude": latitude,
            "longitude": longitude,
            "BriefFacts": brief_facts,
            "cip_brief_facts_kn": None,
            "cip_dq_flags": None,
        }

        generated = GeneratedCase(case=case_row)
        self._add_complainant(generated, case_id, rng)
        self._add_victims(generated, case_id, sub_head_id, rng)
        self._add_accused(generated, case_id, sub_head_id, rng, repeat_pool=repeat_pool, ring=ring)
        self._add_act_sections(generated, case_id, sub_head_id)
        self._add_arrests(generated, case_id, unit, district_id, officer, courts, registered, rng)
        if chargesheet:
            self._add_chargesheet(generated, case_id, registered, officer, rng)
        return generated

    # ------------------------------------------------------------ children
    def _add_complainant(self, generated: GeneratedCase, case_id: int, rng: random.Random) -> None:
        name, gender = person_name(rng)
        generated.complainants.append({
            "ComplainantID": self._take("complainant"),
            "CaseMasterID": case_id,
            "ComplainantName": name,
            "AgeYear": rng.randint(19, 68),
            "OccupationID": rng.choices(
                [1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13],
                weights=[16, 14, 8, 16, 12, 7, 9, 6, 4, 5, 8, 5], k=1)[0],
            "ReligionID": rng.choices([1, 2, 3, 4, 5, 6, 7], weights=[70, 14, 5, 4, 2, 1, 4], k=1)[0],
            "CasteID": rng.choices([1, 2, 3, 4, 5], weights=[26, 38, 20, 8, 8], k=1)[0],
            "GenderID": 2 if gender == "F" else 1,
        })

    def _add_victims(
        self, generated: GeneratedCase, case_id: int, sub_head_id: int, rng: random.Random
    ) -> None:
        if sub_head_id in (601, 602, 603, 604):
            return
        count = 1 if rng.random() < 0.86 else rng.randint(2, 3)
        for _ in range(count):
            gender_hint = "F" if sub_head_id in (301, 302, 303) else None
            name, gender = person_name(rng, gender=gender_hint)
            generated.victims.append({
                "VictimMasterID": self._take("victim"),
                "CaseMasterID": case_id,
                "VictimName": name,
                "AgeYear": rng.randint(6, 78),
                "GenderID": gender,
                "VictimPolice": "1" if rng.random() < 0.012 else "0",
            })

    def _add_accused(
        self,
        generated: GeneratedCase,
        case_id: int,
        sub_head_id: int,
        rng: random.Random,
        *,
        repeat_pool: Sequence[dict[str, Any]],
        ring: Sequence[dict[str, Any]] | None,
    ) -> None:
        if ring:
            members = rng.sample(list(ring), k=rng.randint(2, min(4, len(ring))))
            for index, member in enumerate(members, start=1):
                generated.accused.append({
                    "AccusedMasterID": self._take("accused"),
                    "CaseMasterID": case_id,
                    "AccusedName": rng.choice(member["variants"]),
                    "AgeYear": member["age"] + rng.randint(0, 2),
                    "GenderID": member["gender"],
                    "PersonID": f"A{index}",
                })
            return

        if rng.random() < 0.22:
            return  # unknown accused, a real and common case

        count = 1 if rng.random() < 0.72 else rng.randint(2, 4)
        for index in range(1, count + 1):
            if repeat_pool and rng.random() < 0.18:
                member = rng.choice(list(repeat_pool))
                name = rng.choice(member["variants"])
                gender = member["gender"]
                age = member["age"] + rng.randint(0, 2)
            else:
                name, gender = person_name(rng)
                age = rng.randint(18, 58)
            generated.accused.append({
                "AccusedMasterID": self._take("accused"),
                "CaseMasterID": case_id,
                "AccusedName": name,
                "AgeYear": age,
                "GenderID": gender,
                "PersonID": f"A{index}",
            })

    def _add_act_sections(self, generated: GeneratedCase, case_id: int, sub_head_id: int) -> None:
        entries = SECTION_MAP.get(sub_head_id, [])
        for order, (act_code, section_code, _description) in enumerate(entries, start=1):
            generated.act_sections.append({
                "CaseMasterID": case_id,
                "ActID": act_code,
                "SectionID": section_code,
                "ActOrderID": order,
                "SectionOrderID": order,
            })

    def _add_arrests(
        self,
        generated: GeneratedCase,
        case_id: int,
        unit: dict[str, Any],
        district_id: int,
        officer: dict[str, Any] | None,
        courts: list[dict[str, Any]],
        registered: date,
        rng: random.Random,
    ) -> None:
        if not generated.accused or rng.random() > 0.46:
            return
        for accused in generated.accused:
            if rng.random() > 0.62:
                continue
            arrest_date = registered + timedelta(days=rng.randint(0, 120))
            if arrest_date > self._anchor:
                continue
            generated.arrests.append({
                "ArrestSurrenderID": self._take("arrest"),
                "CaseMasterID": case_id,
                "ArrestSurrenderTypeID": 1 if rng.random() < 0.82 else 2,
                "ArrestSurrenderDate": arrest_date.isoformat(),
                "ArrestSurrenderStateId": 29,
                "ArrestSurrenderDistrictId": district_id,
                "PoliceStationID": int(unit["UnitID"]),
                "IOID": int(officer["EmployeeID"]) if officer else None,
                "CourtID": int(rng.choice(courts)["CourtID"]) if courts else None,
                "AccusedMasterID": accused["AccusedMasterID"],
                "IsAccused": 1,
                "IsComplainantAccused": 0,
            })

    def _add_chargesheet(
        self, generated: GeneratedCase, case_id: int, registered: date,
        officer: dict[str, Any] | None, rng: random.Random,
    ) -> None:
        filed = registered + timedelta(days=rng.randint(30, 240))
        if filed > self._anchor:
            return
        generated.chargesheets.append({
            "CSID": self._take("chargesheet"),
            "CaseMasterID": case_id,
            "csdate": filed.isoformat(),
            "cstype": rng.choices(["A", "B", "C"], weights=[80, 8, 12], k=1)[0],
            "PolicePersonID": int(officer["EmployeeID"]) if officer else None,
        })

    # ------------------------------------------------------------- helpers
    def _take(self, key: str) -> int:
        value = self._next_ids[key]
        self._next_ids[key] = value + 1
        return value

    def _weighted_district(self) -> int:
        ids = list(self._district_geo.keys())
        weights = [self._district_geo[i][2] for i in ids]
        return self._rng.choices(ids, weights=weights, k=1)[0]

    def _weighted_sub_head(self, *, registered_month: int | None) -> int:
        ids = [s[0] for s in self._sub_heads]
        weights = [s[2] for s in self._sub_heads]
        return self._rng.choices(ids, weights=weights, k=1)[0]

    def _random_date(self, *, recent_bias: float = 0.0) -> date:
        span_days = int(self._months * 30.44)
        for _ in range(12):
            offset = self._rng.randint(0, span_days)
            if recent_bias and self._rng.random() < recent_bias:
                offset = self._rng.randint(0, max(1, span_days // 4))
            candidate = self._anchor - timedelta(days=offset)
            if self._rng.random() < MONTHLY_SEASONALITY[candidate.month] / 1.25:
                return candidate
        return self._anchor - timedelta(days=self._rng.randint(0, span_days))

    def _district_point(self, district_id: int) -> tuple[float, float]:
        latitude, longitude, _weight = self._district_geo[district_id]
        return (
            round(latitude + self._rng.gauss(0, 0.06), 6),
            round(longitude + self._rng.gauss(0, 0.06), 6),
        )

    def _jitter(self, latitude: float, longitude: float, radius_metres: float) -> tuple[float, float]:
        angle = self._rng.uniform(0, 2 * math.pi)
        distance = self._rng.uniform(0, radius_metres)
        delta_lat = (distance * math.cos(angle)) / 111_320.0
        delta_lon = (distance * math.sin(angle)) / (111_320.0 * math.cos(math.radians(latitude)))
        return round(latitude + delta_lat, 6), round(longitude + delta_lon, 6)

    def _incident_time(self, sub_head_id: int) -> Any:
        from datetime import time as time_type

        if sub_head_id in (201, 204):
            hour = self._rng.choice([0, 1, 2, 3, 22, 23])
        elif sub_head_id in (202, 207):
            hour = self._rng.choice([18, 19, 20, 21, 22])
        elif sub_head_id in (404, 401):
            hour = self._rng.randint(9, 19)
        else:
            hour = self._rng.randint(6, 23)
        return time_type(hour, self._rng.choice([0, 15, 30, 45]))

    def _status_for(self, registered: date) -> tuple[int, bool]:
        age_days = (self._anchor - registered).days
        rng = self._rng
        if age_days < 45:
            return (1, False)
        if age_days < 150:
            return (rng.choices([1, 2, 6], weights=[58, 34, 8], k=1)[0], rng.random() < 0.34)
        outcome = rng.choices([1, 2, 3, 4, 5, 6, 7], weights=[18, 24, 26, 10, 6, 12, 4], k=1)[0]
        return (outcome, outcome in (2, 3, 4, 5))

    def _brief_facts(self, sub_head_id: int, registered: date, rng: random.Random) -> str:
        key = SUB_HEAD_TEMPLATE_KEY.get(sub_head_id, "default")
        template = rng.choice(BRIEF_FACTS_TEMPLATES.get(key, BRIEF_FACTS_TEMPLATES["default"]))
        return template.format(
            item=rng.choice(ITEMS),
            place=rng.choice(PLACES),
            time=f"{rng.randint(1, 12)}.{rng.choice(['00', '15', '30', '45'])} "
                 f"{rng.choice(['a.m.', 'p.m.'])}",
            day=registered.strftime("%d.%m.%Y"),
            value=f"{rng.randint(5, 850) * 1000:,}",
            reg=f"{rng.randint(1, 70):02d}-{rng.choice('ABCDEFGHJKLMNPQRSTUVWXYZ')}"
                f"{rng.choice('ABCDEFGHJKLMNPQRSTUVWXYZ')}-{rng.randint(1000, 9999)}",
            reason=rng.choice(REASONS),
            promise=rng.choice(PROMISES),
        )
