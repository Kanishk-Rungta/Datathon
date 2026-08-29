"""The five agents against seeded data, through the supervisor."""

from datetime import date

import pytest

from ksp_cip.application.agents import TurnRequest
from ksp_cip.domain.enums import AgentName, Intent, Provenance

pytestmark = pytest.mark.slow


def ask(container, principal, text, session="test-session"):
    return container.supervisor.handle_turn(
        TurnRequest(principal=principal, session_id=session, text=text)
    )


class TestRoutingEndToEnd:
    @pytest.mark.parametrize("text,intent,agent", [
        ("Show me theft cases in Mysuru", Intent.LOOKUP_LOCATION, AgentName.DATA_RETRIEVAL),
        ("What is the crime trend this year?", Intent.TREND_QUERY, AgentName.CRIME_ANALYTICS),
        ("Is there a seasonal pattern to crime here?", Intent.SEASONAL_QUERY, AgentName.CRIME_ANALYTICS),
        ("Where are the hotspots?", Intent.HOTSPOT_QUERY, AgentName.CRIME_ANALYTICS),
        ("Any early warning alerts?", Intent.EARLY_WARNING, AgentName.CRIME_ANALYTICS),
        ("Who are the repeat offenders?", Intent.OFFENDER_PROFILE, AgentName.NETWORK_INTELLIGENCE),
        ("Break down complainants by occupation", Intent.DEMOGRAPHIC_INSIGHT, AgentName.CRIME_ANALYTICS),
    ])
    def test_questions_reach_the_right_agent(self, container, analyst, text, intent, agent):
        answer = ask(container, analyst, text)
        assert answer.intent is intent
        assert agent in answer.agents_used


class TestAnswerContract:
    def test_every_numeric_answer_carries_evidence(self, container, analyst):
        for text in ["Show me theft cases in Mysuru",
                     "What is the crime trend this year?",
                     "Who are the repeat offenders?"]:
            answer = ask(container, analyst, text)
            assert answer.evidence, f"no evidence for: {text}"
            for claim in answer.claims:
                if any(character.isdigit() for character in claim.text):
                    assert claim.evidence_locators, f"unevidenced numeric claim: {claim.text}"

    def test_every_cited_locator_is_published(self, container, analyst):
        answer = ask(container, analyst, "Where are the hotspots?")
        published = {item.locator for item in answer.evidence}
        for claim in answer.claims:
            assert set(claim.evidence_locators) <= published

    def test_every_answer_carries_a_computation_trace(self, container, analyst):
        answer = ask(container, analyst, "What is the crime trend this year?")
        assert answer.traces
        assert answer.traces[0].description

    def test_inferred_claims_are_marked(self, container, analyst):
        answer = ask(container, analyst, "Who are the repeat offenders?")
        inferred = [c for c in answer.claims if c.provenance is Provenance.INFERRED]
        for claim in inferred:
            assert claim.evidence_locators

    def test_an_unknown_place_is_reported_not_silently_dropped(self, container, analyst):
        """Answering statewide for "in Kathmandu" would answer a broader question."""
        answer = ask(container, analyst, "Show me cases in Kathmandu")
        text = answer.answer_text.lower()
        assert "kathmandu" in text
        assert "could not match" in text
        assert answer.needs_clarification

    def test_a_search_with_no_matches_says_so(self, container, analyst):
        answer = ask(container, analyst, "Show me murder cases in Kodagu registered in 1998")
        text = answer.answer_text.lower()
        assert any(phrase in text for phrase in
                   ["no fir", "not find", "no confident", "does not appear", "could not match"])


class TestConversationMemory:
    def test_a_follow_up_resolves_the_previous_case(self, container, analyst):
        session = "memory-session"
        first = ask(container, analyst, "Show me theft cases in Mysuru", session)
        assert first.evidence
        second = ask(container, analyst, "summarise that case", session)
        assert second.intent is Intent.INVESTIGATION_SUMMARY
        assert second.evidence

    def test_a_pronoun_after_an_offender_list_resolves_to_that_person(self, container, investigator):
        """"How is *he* connected?" must reach the person's ego network.

        The offender-profile result used to publish only identity_ids and
        case_master_ids, so no person name was pinned; the pronoun fell
        through to the case branch and answered with an out-of-scope FIR
        error instead of a link graph.
        """
        session = "offender-pronoun-session"
        first = ask(container, investigator, "Who are the repeat offenders?", session)
        assert first.intent is Intent.OFFENDER_PROFILE
        second = ask(container, investigator, "How is he connected to others?", session)
        assert second.intent is Intent.NETWORK_QUERY
        assert "not available within your authorized scope" not in second.answer_text
        assert second.evidence

    def test_the_transcript_is_persisted(self, container, analyst):
        session = "transcript-session"
        ask(container, analyst, "What is the crime trend this year?", session)
        turns = container.memory.transcript(session)
        assert len(turns) == 1
        assert turns[0]["user_text_english"]
        assert turns[0]["answer_text_english"]


class TestLanguage:
    def test_a_kannada_question_is_understood_and_answered(self, container, analyst):
        answer = ask(container, analyst, "ಮೈಸೂರು ಜಿಲ್ಲೆಯಲ್ಲಿ ಕಳ್ಳತನ ಪ್ರಕರಣಗಳು")
        assert str(answer.language) == "kn"
        assert answer.intent in {Intent.LOOKUP_LOCATION, Intent.LOOKUP_CASE}
        assert answer.answer_text_display

    def test_english_questions_are_untouched(self, container, analyst):
        answer = ask(container, analyst, "Where are the hotspots?")
        assert str(answer.language) == "en"
        assert answer.answer_text_display == answer.answer_text


class TestScopeEnforcementInAgents:
    def test_an_investigator_counts_fewer_cases_than_an_analyst(self, container, analyst, investigator):
        """Both see "all recent cases" — but "all" means different things."""
        wide = ask(container, analyst, "Show me all recent cases", "scope-a")
        narrow = ask(container, investigator, "Show me all recent cases", "scope-b")
        wide_total = wide.payload.data.get("total")
        narrow_total = narrow.payload.data.get("total")
        if wide_total is None or narrow_total is None:
            pytest.skip("neither answer reported a total")
        assert narrow_total < wide_total

    def test_evidence_never_cites_a_case_outside_scope(self, container, investigator):
        answer = ask(container, investigator, "Show me all recent cases", "scope-c")
        scope = investigator.scope
        for item in answer.evidence:
            for case_id in item.case_master_ids[:20]:
                rows = container.store.query(
                    "SELECT PoliceStationID FROM curated_CaseMaster WHERE CaseMasterID = :id",
                    {"id": case_id},
                )
                if rows:
                    assert scope.allows(rows[0]["PoliceStationID"])


class TestInvestigationSupport:
    def test_a_briefing_assembles_the_case_record(self, container, analyst):
        row = container.store.query(
            "SELECT CrimeNo FROM curated_CaseMaster ORDER BY CaseMasterID LIMIT 1", {}
        )[0]
        answer = ask(container, analyst, f"Give me a briefing on FIR {row['CrimeNo']}")
        assert answer.intent is Intent.INVESTIGATION_SUMMARY
        assert answer.payload.payload_type == "timeline"
        assert answer.payload.data["events"]
        assert row["CrimeNo"] in answer.answer_text
        assert "None §None" not in answer.answer_text

    def test_the_priority_indicator_is_fully_decomposed(self, container, analyst):
        row = container.store.query(
            "SELECT CrimeNo FROM curated_CaseMaster ORDER BY CaseMasterID LIMIT 1", {}
        )[0]
        answer = ask(container, analyst, f"Brief me on {row['CrimeNo']}")
        priority = answer.payload.data.get("priority")
        if priority:
            items = priority["components"]["items"]
            assert items
            assert all("rationale" in item for item in items)


class TestSeasonalAnalysis:
    def test_seasonal_query_is_evidence_bound(self, container, analyst):
        """Whichever branch fires (enough history or honestly not enough),
        every numeric claim must still carry a locator (Gate 5, §9.1)."""
        answer = ask(container, analyst, "Is there a seasonal pattern to crime in Karnataka?")
        assert answer.intent is Intent.SEASONAL_QUERY
        assert AgentName.CRIME_ANALYTICS in answer.agents_used
        assert answer.traces
        for claim in answer.claims:
            if any(character.isdigit() for character in claim.text):
                assert claim.evidence_locators, f"unevidenced numeric claim: {claim.text}"

    def test_seasonal_query_disclaims_forecasting(self, container, analyst):
        """It compares history, and must say so plainly rather than imply a
        prediction of what a future occurrence of that month will look like."""
        answer = ask(container, analyst, "What is the seasonal trend for theft?")
        assert answer.intent is Intent.SEASONAL_QUERY
        assert "not a forecast" in answer.answer_text.lower()


class TestForecastScope:
    """An aggregate forecast must survive a conversation that mentioned a person.

    The refusal gate on ``_forecast`` used to read ``pinned_person_names`` as
    well as the current turn's slots. A pin lasts the whole session, so once
    an officer had asked about repeat offenders, every later planning question
    in that conversation -- "forecast crime for the next three months", which
    names nobody -- came back as "this platform does not forecast whether a
    particular person will offend". Refusing the prohibited question is the
    point; refusing the permitted one because of an earlier turn is a defect.
    """

    def test_an_aggregate_forecast_after_a_person_turn_still_forecasts(self, container, analyst):
        session = "forecast-after-person"
        offenders = ask(container, analyst, "Who are the repeat offenders?", session)
        assert offenders.intent is Intent.OFFENDER_PROFILE

        answer = ask(container, analyst, "Forecast crime for the next three months", session)
        assert answer.intent is Intent.FORECAST_QUERY
        assert "does not forecast whether a particular person" not in answer.answer_text
        assert answer.payload.payload_type == "forecast"

    def test_a_person_follow_up_in_the_same_session_is_still_refused(self, container, analyst):
        """The legitimate refusal path: the pronoun resolves to the pinned name
        through MemoryService, so it lands in the current turn's slots."""
        session = "forecast-person-followup"
        ask(container, analyst, "Who are the repeat offenders?", session)
        answer = ask(container, analyst, "Will he reoffend next year?", session)
        assert "does not forecast whether a particular person" in answer.answer_text

    def test_a_named_individual_forecast_is_refused_outright(self, container, analyst):
        answer = ask(container, analyst, "Predict who will commit a crime next month",
                     "forecast-named")
        assert "does not forecast whether a particular person" in answer.answer_text


class TestSociologySubjectSelector:
    def test_victim_subject_with_unsupported_dimension_is_substituted_not_dropped(self, container, analyst):
        """The organiser's Victim table has no occupation column. The agent
        must say so and substitute gender, rather than silently ignoring the
        "victim" framing or raising an error (Gate 5, §9.3)."""
        answer = ask(container, analyst, "Break down victims by occupation")
        assert answer.intent is Intent.DEMOGRAPHIC_INSIGHT
        text = answer.answer_text.lower()
        assert "victim" in text
        assert "gender instead" in text

    def test_victim_subject_with_a_supported_dimension_needs_no_substitution(self, container, analyst):
        answer = ask(container, analyst, "Give me a gender breakdown of victims")
        assert answer.intent is Intent.DEMOGRAPHIC_INSIGHT
        assert "gender instead" not in answer.answer_text.lower()
        assert "victim" in answer.answer_text.lower()
