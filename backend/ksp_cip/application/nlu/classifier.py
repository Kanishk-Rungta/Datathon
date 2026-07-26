"""Natural-language understanding: deterministic first, model second.

Routing is a rule engine over a fixed intent taxonomy (plan §6.2). This is a
deliberate inversion of the usual pattern and it buys three things:

* the router is unit-testable without a network or a model;
* routing does not drift when a provider changes a model version;
* the LLM is consulted only when the rules are genuinely unsure, and its
  answer is accepted only if it names a label in the taxonomy.

Slot extraction is likewise deterministic: dates by grammar, districts and
crime types by fuzzy match against master data (so "Mysore" resolves to the
"Mysuru" row), CrimeNos by the documented 18-digit format.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Sequence

from ...domain.enums import Intent
from ...domain.models import NLUResult, Slots
from ...domain.value_objects import CRIME_NO_RE
from ...infrastructure.db.repositories import ReferenceRepository

# Ordered: the first pattern group that matches with the highest weight wins.
INTENT_RULES: list[tuple[Intent, float, list[str]]] = [
    # Deliberately below the specific intents: "summarise the demographics"
    # is a demographic question that happens to use the word "summarise".
    (Intent.INVESTIGATION_SUMMARY, 0.90, [
        r"\b(summar(y|ise|ize)|brief(ing)?|timeline|what happened|next steps?|leads?|investigat(e|ion) plan)\b",
    ]),
    (Intent.SIMILAR_CASE, 1.0, [
        r"\b(similar|like this|comparable|resembl\w+|same (kind|type|pattern) of case|matching cases?)\b",
    ]),
    (Intent.NETWORK_QUERY, 1.0, [
        r"\b(network|gang|associates?|linked to|connect(ed|ion)s?|co[- ]?accused|ring|how are .+ (and|&) .+ (linked|connected|related))\b",
    ]),
    (Intent.FINANCIAL_LINK, 1.0, [
        r"\b(money|financial|transaction|payment|transfer|funds?|account|hawala|cash flow)\b",
    ]),
    (Intent.OFFENDER_PROFILE, 1.0, [
        r"\b(repeat offenders?|habitual|profile of|offender profile|risk (score|ranking)|most active (accused|offenders?))\b",
    ]),
    (Intent.HOTSPOT_QUERY, 1.0, [
        r"\b(hotspots?|hot spots?|concentrat\w+|cluster(s|ed|ing)?|where .*(most|highest)|crime map|heat ?map)\b",
    ]),
    (Intent.EARLY_WARNING, 1.0, [
        r"\b(early warning|alerts?|anomal\w+|spike|surge|unusual (rise|increase)|emerging)\b",
    ]),
    # Weighted above both HOTSPOT_QUERY (which describes *observed*
    # concentration) and FORECAST_QUERY (which projects counts over time).
    # "Where will crime concentrate next month" is a spatial question about the
    # future, and answering it with either today's hotspots or a statewide count
    # would answer a question nobody asked. Every pattern needs a forward-looking
    # word *and* a spatial one, so "where are the hotspots" stays with HOTSPOT.
    (Intent.SPATIOTEMPORAL_QUERY, 1.1, [
        r"\b(?:predicted|projected|forecast(?:ed)?|future|emerging|likely|expected)\s+"
        r"(?:crime\s+)?(?:hotspots?|hot spots?|clusters?|areas?|locations?|zones?)\b"
        r"|\bwhere\s+(?:will|would|is|are)\s+(?:crime|theft|cases?|incidents?|it)\s+"
        r"(?:be\s+)?(?:likely\s+)?(?:to\s+)?(?:concentrate|cluster|rise|increase|happen|occur|spike)\b"
        r"|\b(?:hotspots?|clusters?)\s+(?:for|in|over)\s+the\s+(?:next|coming)\b"
        r"|\bwhich\s+(?:areas?|locations?|zones?|grid cells?)\s+.{0,30}?(?:next|coming|future|likely)\b"
        r"|\bspatio[- ]?temporal\b|\bspatial (?:forecast|projection|risk)\b",
    ]),
    # Ahead of both SEASONAL_QUERY and TREND_QUERY: "what will next month look
    # like" is a question about the future, and answering it with a description
    # of the past would quietly substitute a different question. Weighted 1.05
    # so an explicitly forward-looking phrase wins a tie against them.
    (Intent.FORECAST_QUERY, 1.05, [
        r"\b(forecast|project(ion|ed)?|predict(ion|ed)?|"
        r"expect(ed)? (next|in the (coming|next))|"
        r"how many .{0,60}(next|coming) (month|quarter|year)|"
        r"(next|coming) (month|quarter|few months).{0,30}(expect|likely|estimate)|"
        r"what will .{0,40}(look like|happen)|"
        r"anticipat\w+|plan(ning)? ahead|resource(s)? (for|next))\b",
    ]),
    # Placed ahead of TREND_QUERY so a tie in match weight resolves toward the
    # more specific calendar-recurrence reading ("festival months", "seasonal
    # pattern") rather than a generic month-over-month trend.
    (Intent.SEASONAL_QUERY, 1.0, [
        r"\b(seasonal(ity)?|season|festival (month|season|period)s?|"
        r"same (month|period) (last|every|each) year|"
        r"time of year|recurs? every year|monsoon season)\b",
    ]),
    (Intent.TREND_QUERY, 1.0, [
        r"\b(trend|over time|month(ly)?|year on year|compared? (to|with) last|rising|falling|increase|decrease|pattern over)\b",
    ]),
    (Intent.SOCIOECONOMIC_QUERY, 1.05, [
        r"\b(socio[- ]?economic correlation|socio[- ]?economic factor|correlation with (literacy|poverty|unemployment|urbanization|income)|"
        r"literacy (vs|correlation)|unemployment (vs|correlation)|poverty (vs|correlation|impact)|"
        r"poverty headcount|urbanization (vs|correlation)|migrant population|per capita income)\b",
    ]),
    (Intent.DEMOGRAPHIC_INSIGHT, 1.0, [
        r"\b(demograph\w+|age group|occupation|socio[- ]?economic|gender breakdown|who (are|is) (the )?(victims?|complainants?))\b",
    ]),
    (Intent.LOOKUP_PERSON, 0.9, [
        r"\b(cases? (against|involving)|history of|record of|accused named|person named|what has .+ done)\b",
    ]),
    (Intent.LOOKUP_CASE, 0.9, [
        r"\b(fir|crime ?no|case ?no|crime number|case number|status of (this|the) case|show me case)\b",
    ]),
    (Intent.LOOKUP_LOCATION, 0.85, [
        r"\b(cases? in|firs? in|registered in|what happened in|crimes? in)\b",
    ]),
]

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

_RELATIVE_RE = re.compile(
    r"\b(?:last|past|previous|recent)\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten|twelve)?\s*"
    r"(day|days|week|weeks|month|months|quarter|quarters|year|years)\b",
    re.IGNORECASE,
)
_WORD_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
                 "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twelve": 12}
_YEAR_RE = re.compile(r"\b(20\d{2})\b")
_MONTH_YEAR_RE = re.compile(r"\b([A-Za-z]{3,9})\s+(20\d{2})\b")
_ISO_DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
_LIMIT_RE = re.compile(r"\b(?:top|first|latest|show me)\s+(\d{1,3})\b", re.IGNORECASE)
_NAMED_RE = re.compile(
    r"(?:named|name|accused|suspect|person|offender|about)\s+([A-Z][\w'.-]+(?:\s+[A-Z][\w'.-]+){0,3})"
)
_BY_NAME_RE = re.compile(r"\bby\s+([A-Z][\w'.-]+(?:\s+[A-Z][\w'.-]+){1,3})\b")
_QUOTED_RE = re.compile(r"[\"'\u201c]([^\"'\u201d]{3,60})[\"'\u201d]")
_SECTION_RE = re.compile(r"\b(?:section|u/s|under)\s*([0-9]{1,4}[A-Z]?)\b", re.IGNORECASE)
_CASE_ID_RE = re.compile(r"\bcase(?:master)?\s*id\s*[:#]?\s*(\d{1,9})\b", re.IGNORECASE)
#: A place named after a locative preposition. If it does not resolve to a
#: district or unit, the platform says so instead of widening the query.
_PLACE_PHRASE_RE = re.compile(
    r"\b(?:in|at|near|around|from|within)\s+([A-Z][a-zA-Z]{3,}(?:\s+[A-Z][a-zA-Z]{3,}){0,2})"
)

_STOPWORD_NAMES = {"the", "and", "with", "from", "this", "that", "cases", "case", "district", "police"}


@dataclass(slots=True)
class RuleMatch:
    intent: Intent
    weight: float
    matched: str


class NLUEngine:
    def __init__(self, reference: ReferenceRepository, llm: Any | None = None, *, today: date | None = None) -> None:
        self._reference = reference
        self._llm = llm
        self._today = today

    @property
    def today(self) -> date:
        return self._today or date.today()

    # ----------------------------------------------------------- classify
    def classify(self, text: str) -> NLUResult:
        slots = self.extract_slots(text)
        matches = self._rule_matches(text)
        if matches:
            best = max(matches, key=lambda m: m.weight)
            confidence = min(0.95, 0.6 + 0.1 * len(matches) + best.weight * 0.25)
            alternatives = [m.intent for m in matches if m.intent is not best.intent][:3]
            return NLUResult(intent=best.intent, slots=slots, confidence=round(confidence, 3),
                             method="rules", alternatives=alternatives)

        # No rule fired. Fall back to slot shape before reaching for a model.
        inferred = self._infer_from_slots(slots)
        if inferred is not None:
            return NLUResult(intent=inferred, slots=slots, confidence=0.55, method="rules")

        if self._llm is not None:
            label, confidence = self._llm.classify(
                system=self._router_prompt(),
                user_text=text,
                labels=[i.value for i in Intent],
                purpose="intent_routing",
            )
            if label:
                return NLUResult(intent=Intent(label), slots=slots, confidence=confidence, method="rules+llm")
        return NLUResult(intent=Intent.GENERAL_QA, slots=slots, confidence=0.4, method="rules")

    def _router_prompt(self) -> str:
        try:
            prompt, _version = self._llm.prompts.get("intent_router")  # type: ignore[union-attr]
            return prompt
        except Exception:  # noqa: BLE001 - registry is optional
            return "Classify the police query into exactly one intent label."

    @staticmethod
    def _rule_matches(text: str) -> list[RuleMatch]:
        matches: list[RuleMatch] = []
        for intent, weight, patterns in INTENT_RULES:
            for pattern in patterns:
                found = re.search(pattern, text, flags=re.IGNORECASE)
                if found:
                    matches.append(RuleMatch(intent=intent, weight=weight, matched=found.group(0)))
                    break
        return matches

    @staticmethod
    def _infer_from_slots(slots: Slots) -> Intent | None:
        if slots.crime_nos or slots.case_master_ids:
            return Intent.LOOKUP_CASE
        if slots.person_names:
            return Intent.LOOKUP_PERSON
        if slots.district_ids or slots.unit_ids:
            return Intent.LOOKUP_LOCATION
        return None

    # -------------------------------------------------------------- slots
    def extract_slots(self, text: str) -> Slots:
        slots = Slots(free_text=text.strip())

        slots.crime_nos = [m.group(0) for m in re.finditer(r"\b\d{18}\b", text) if CRIME_NO_RE.match(m.group(0))]
        slots.case_master_ids = [int(m.group(1)) for m in _CASE_ID_RE.finditer(text)]

        self._extract_dates(text, slots)
        self._extract_places(text, slots)
        self._extract_crime_types(text, slots)
        self._extract_status(text, slots)

        limit_match = _LIMIT_RE.search(text)
        if limit_match:
            slots.limit = max(1, min(200, int(limit_match.group(1))))

        slots.act_sections = [m.group(1).upper() for m in _SECTION_RE.finditer(text)]
        slots.person_names = self._extract_person_names(text, slots)
        return slots

    def _extract_dates(self, text: str, slots: Slots) -> None:
        iso_dates = [date.fromisoformat(m.group(1)) for m in _ISO_DATE_RE.finditer(text)]
        if len(iso_dates) >= 2:
            slots.date_from, slots.date_to = min(iso_dates), max(iso_dates)
            return
        if len(iso_dates) == 1:
            slots.date_from = slots.date_to = iso_dates[0]
            return

        month_year = _MONTH_YEAR_RE.search(text)
        if month_year and month_year.group(1).lower() in _MONTHS:
            month = _MONTHS[month_year.group(1).lower()]
            year = int(month_year.group(2))
            slots.date_from = date(year, month, 1)
            slots.date_to = (date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)) - timedelta(days=1)
            return

        relative = _RELATIVE_RE.search(text)
        if relative:
            raw_count = (relative.group(1) or "1").lower()
            count = _WORD_NUMBERS.get(raw_count, None)
            if count is None:
                count = int(raw_count) if raw_count.isdigit() else 1
            unit = relative.group(2).lower()
            days = {"day": 1, "days": 1, "week": 7, "weeks": 7, "month": 30, "months": 30,
                    "quarter": 91, "quarters": 91, "year": 365, "years": 365}[unit]
            span = count * days
            slots.relative_period_days = span
            slots.date_to = self.today
            slots.date_from = self.today - timedelta(days=span)
            return

        if re.search(r"\bthis year\b", text, re.IGNORECASE):
            slots.date_from = date(self.today.year, 1, 1)
            slots.date_to = self.today
            return
        if re.search(r"\blast year\b", text, re.IGNORECASE):
            slots.date_from = date(self.today.year - 1, 1, 1)
            slots.date_to = date(self.today.year - 1, 12, 31)
            return

        years = [int(m.group(1)) for m in _YEAR_RE.finditer(text)]
        if years:
            slots.date_from = date(min(years), 1, 1)
            slots.date_to = date(max(years), 12, 31)

    def _extract_places(self, text: str, slots: Slots) -> None:
        lowered = text.casefold()
        for district in self._reference.districts():
            name = str(district["DistrictName"]).casefold()
            if name and name in lowered:
                slots.district_ids.append(int(district["DistrictID"]))
                slots.district_names.append(str(district["DistrictName"]))
        if not slots.district_ids:
            # Try a fuzzy pass over capitalised tokens ("Mysore" → "Mysuru").
            for token in re.findall(r"\b[A-Z][a-z]{3,}\b", text):
                if token.casefold() in _STOPWORD_NAMES:
                    continue
                match = self._reference.resolve_district(token)
                if match:
                    slots.district_ids.append(int(match["DistrictID"]))
                    slots.district_names.append(str(match["DistrictName"]))
                    break
        for unit in self._reference.units():
            name = str(unit["UnitName"]).casefold()
            if name and name in lowered:
                slots.unit_ids.append(int(unit["UnitID"]))
                slots.unit_names.append(str(unit["UnitName"]))
        slots.district_ids = sorted(set(slots.district_ids))
        slots.unit_ids = sorted(set(slots.unit_ids))

        resolved_names = " ".join(slots.district_names + slots.unit_names).casefold()
        for match in _PLACE_PHRASE_RE.finditer(text):
            candidate = match.group(1).strip()
            if candidate.casefold() in resolved_names:
                continue
            if self._reference.resolve_district(candidate) or self._reference.resolve_unit(candidate):
                continue
            if candidate.casefold() in _STOPWORD_NAMES:
                continue
            slots.unresolved_terms.append(candidate)

    def _extract_crime_types(self, text: str, slots: Slots) -> None:
        lowered = text.casefold()
        sub_heads = self._reference.crime_sub_heads()
        for sub_head in sub_heads:
            name = str(sub_head["CrimeHeadName"]).casefold()
            if name and name in lowered:
                slots.crime_sub_head_ids.append(int(sub_head["CrimeSubHeadID"]))
                slots.crime_types.append(str(sub_head["CrimeHeadName"]))
        if not slots.crime_sub_head_ids:
            # "theft" is a family, not a row: House Theft, Motor Vehicle Theft
            # and Other Theft are all theft. Matching only the closest single
            # sub-head would silently answer a narrower question than the one
            # the officer asked, so every sub-head containing the keyword is
            # included.
            for keyword in ("theft", "burglary", "robbery", "murder", "assault", "cheating",
                            "fraud", "kidnapping", "snatching", "narcotic", "rioting",
                            "hurt", "dacoity", "forgery", "dowry", "gambling", "cyber"):
                if keyword not in lowered:
                    continue
                matched = [
                    row for row in sub_heads
                    if keyword in str(row["CrimeHeadName"]).casefold()
                ]
                if not matched:
                    fuzzy = self._reference.resolve_crime_sub_head(keyword)
                    matched = [fuzzy] if fuzzy else []
                for row in matched:
                    slots.crime_sub_head_ids.append(int(row["CrimeSubHeadID"]))
                    slots.crime_types.append(str(row["CrimeHeadName"]))
        slots.crime_sub_head_ids = sorted(set(slots.crime_sub_head_ids))
        slots.crime_types = list(dict.fromkeys(slots.crime_types))

    def _extract_status(self, text: str, slots: Slots) -> None:
        lowered = text.casefold()
        for status in self._reference.case_statuses():
            name = str(status["CaseStatusName"]).casefold()
            if name and name in lowered:
                slots.status_names.append(str(status["CaseStatusName"]))

    def _extract_person_names(self, text: str, slots: Slots) -> list[str]:
        names: list[str] = []
        for match in _QUOTED_RE.finditer(text):
            names.append(match.group(1).strip())
        for match in _NAMED_RE.finditer(text):
            candidate = match.group(1).strip()
            if candidate.casefold() in _STOPWORD_NAMES:
                continue
            if candidate in slots.district_names or candidate in slots.unit_names:
                continue
            names.append(candidate)
        for match in _BY_NAME_RE.finditer(text):
            candidate = match.group(1).strip()
            if candidate.casefold() in _STOPWORD_NAMES:
                continue
            if candidate in slots.district_names or candidate in slots.unit_names:
                continue
            names.append(candidate)
        deduped: list[str] = []
        for name in names:
            cleaned = re.sub(r"\s+", " ", name).strip(" .,")
            if cleaned and cleaned not in deduped and len(cleaned) >= 3:
                deduped.append(cleaned)
        return deduped[:3]


def default_date_window(slots: Slots, *, today: date, months: int = 12) -> tuple[date, date]:
    """Fill an unspecified window with a sensible default, stated in the trace."""
    if slots.date_from and slots.date_to:
        return slots.date_from, slots.date_to
    end = slots.date_to or today
    start = slots.date_from or (end - timedelta(days=months * 30))
    return start, end
