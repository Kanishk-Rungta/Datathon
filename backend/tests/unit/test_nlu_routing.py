"""Deterministic routing: the same question must always reach the same agent."""

from datetime import date

import pytest

from ksp_cip.application.agents import AGENT_ROSTER, FOLLOW_UP_ROUTING, INTENT_ROUTING
from ksp_cip.application.nlu import NLUEngine
from ksp_cip.domain.enums import AgentName, Intent


class FakeReference:
    """Minimal master data, so routing tests do not need a database."""

    def districts(self):
        return [{"DistrictID": 2922, "DistrictName": "Mysuru"},
                {"DistrictID": 2904, "DistrictName": "Bengaluru City"}]

    def units(self):
        return [{"UnitID": 2050, "UnitName": "Mysuru East Police Station"}]

    def crime_sub_heads(self):
        return [
            {"CrimeSubHeadID": 201, "CrimeHeadName": "House Theft"},
            {"CrimeSubHeadID": 202, "CrimeHeadName": "Motor Vehicle Theft"},
            {"CrimeSubHeadID": 203, "CrimeHeadName": "Other Theft"},
            {"CrimeSubHeadID": 101, "CrimeHeadName": "Murder"},
        ]

    def case_statuses(self):
        return [{"CaseStatusID": 2, "CaseStatusName": "Charge Sheeted"}]

    def resolve_district(self, name):
        lowered = name.casefold()
        if lowered.startswith("mysor") or lowered.startswith("mysur"):
            return {"DistrictID": 2922, "DistrictName": "Mysuru"}
        return None

    def resolve_crime_sub_head(self, name):
        for row in self.crime_sub_heads():
            if name.casefold() in row["CrimeHeadName"].casefold():
                return row
        return None


@pytest.fixture
def engine():
    return NLUEngine(FakeReference(), llm=None, today=date(2026, 7, 25))


class TestAgentRoster:
    def test_there_are_exactly_five_agents(self):
        assert len(AGENT_ROSTER) == 5
        assert set(AGENT_ROSTER) == {
            AgentName.SUPERVISOR, AgentName.DATA_RETRIEVAL, AgentName.CRIME_ANALYTICS,
            AgentName.NETWORK_INTELLIGENCE, AgentName.INVESTIGATION_SUPPORT,
        }

    def test_every_intent_routes_to_a_specialist(self):
        assert set(INTENT_ROUTING) == set(Intent)
        assert AgentName.SUPERVISOR not in INTENT_ROUTING.values()

    def test_follow_up_targets_are_real_agents(self):
        assert set(FOLLOW_UP_ROUTING.values()) <= set(AGENT_ROSTER)


class TestIntentClassification:
    @pytest.mark.parametrize("text,expected", [
        ("Show me the crime trend over the last year", Intent.TREND_QUERY),
        ("Where are the hotspots right now?", Intent.HOTSPOT_QUERY),
        ("Any early warning alerts?", Intent.EARLY_WARNING),
        ("Who are the repeat offenders?", Intent.OFFENDER_PROFILE),
        ("How is Ramesh connected to Suresh?", Intent.NETWORK_QUERY),
        ("Trace the money transfers", Intent.FINANCIAL_LINK),
        ("Find cases similar to this one", Intent.SIMILAR_CASE),
        ("Give me a briefing on this case", Intent.INVESTIGATION_SUMMARY),
        ("Break down complainants by occupation", Intent.DEMOGRAPHIC_INSIGHT),
    ])
    def test_representative_questions_route_correctly(self, engine, text, expected):
        assert engine.classify(text).intent is expected

    def test_classification_is_deterministic(self, engine):
        text = "Show me theft trends in Mysuru"
        first = engine.classify(text)
        for _ in range(5):
            assert engine.classify(text).intent is first.intent

    def test_summarise_the_demographics_is_not_a_case_briefing(self, engine):
        """A specific intent must beat the generic word "summarise"."""
        assert engine.classify("Summarise the demographics by occupation").intent is (
            Intent.DEMOGRAPHIC_INSIGHT
        )

    def test_unknown_questions_fall_back_without_a_model(self, engine):
        assert engine.classify("what is the weather like").intent is Intent.GENERAL_QA


class TestSlotExtraction:
    def test_district_is_extracted(self, engine):
        assert 2922 in engine.extract_slots("cases in Mysuru").district_ids

    def test_misspelt_district_is_resolved(self, engine):
        assert 2922 in engine.extract_slots("cases in Mysore").district_ids

    def test_theft_expands_to_every_theft_sub_head(self, engine):
        """"Theft" is a family; answering with one sub-head would be a narrower question."""
        ids = engine.extract_slots("theft cases in Mysuru").crime_sub_head_ids
        assert set(ids) == {201, 202, 203}

    def test_crime_no_is_recognised(self, engine):
        assert engine.extract_slots("open FIR 104430006202600001").crime_nos == ["104430006202600001"]

    def test_relative_period_is_resolved_against_today(self, engine):
        slots = engine.extract_slots("cases in the last 3 months")
        assert slots.date_to == date(2026, 7, 25)
        assert slots.relative_period_days == 90

    def test_explicit_year_is_honoured(self, engine):
        slots = engine.extract_slots("cases registered in 2025")
        assert slots.date_from == date(2025, 1, 1)
        assert slots.date_to == date(2025, 12, 31)

    def test_this_year_is_year_to_date(self, engine):
        slots = engine.extract_slots("how many cases this year")
        assert slots.date_from == date(2026, 1, 1)
        assert slots.date_to == date(2026, 7, 25)

    def test_limit_is_extracted(self, engine):
        assert engine.extract_slots("show me top 5 districts").limit == 5

    def test_quoted_person_name_is_extracted(self, engine):
        assert "Ramesh Gowda" in engine.extract_slots('cases against "Ramesh Gowda"').person_names
