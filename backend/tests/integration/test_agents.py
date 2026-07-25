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
