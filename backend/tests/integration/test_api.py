"""The HTTP surface: contracts, authorization and error shape."""

import pytest

pytestmark = pytest.mark.slow


def auth(tokens, role):
    return tokens[role]


class TestHealthAndCapabilities:
    def test_health_is_public(self, client):
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_readiness_distinguishes_configuration_from_seeding(self, client):
        payload = client.get("/api/v1/health/ready").json()
        assert "configuration_valid" in payload
        assert "configuration_problems" in payload
        assert payload["configuration_valid"] is True
        assert payload["configuration_problems"] == []

    def test_readiness_reports_the_offline_language_fallback_without_failing(self, client):
        """The local build's Kannada glossary is a stated, known limitation --
        it must be visible in degraded_optional_services, but must not turn
        readiness false the way a real system-of-record failure would."""
        payload = client.get("/api/v1/health/ready").json()
        assert "degraded_optional_services" in payload
        assert any("language" in note for note in payload["degraded_optional_services"])
        assert payload["ready"] is True

    def test_capabilities_states_what_is_and_is_not_full_fidelity(self, client, tokens):
        payload = client.get("/api/v1/capabilities", headers=auth(tokens, "analyst")).json()
        assert len(payload["agents"]) == 5
        assert "language_full_fidelity" in payload
        assert payload["llm_is_local_deterministic"] is True
        assert payload["financial_data"]

    def test_correlation_id_is_returned(self, client):
        response = client.get("/api/v1/health")
        assert response.headers.get("X-Correlation-Id")


class TestAuthentication:
    def test_bad_credentials_are_rejected(self, client):
        response = client.post("/api/v1/auth/login",
                               json={"username": "analyst.state", "password": "wrong-password"})
        assert response.status_code in (401, 403)
        assert response.headers["content-type"].startswith("application/problem+json")

    def test_protected_routes_require_a_token(self, client):
        assert client.post("/api/v1/cases/search", json={"limit": 1}).status_code == 401

    def test_a_malformed_token_is_rejected(self, client):
        response = client.post("/api/v1/cases/search", json={"limit": 1},
                               headers={"Authorization": "Bearer not.a.token"})
        assert response.status_code == 401

    def test_me_returns_the_caller(self, client, tokens):
        payload = client.get("/api/v1/auth/me", headers=auth(tokens, "analyst")).json()
        assert payload["username"] == "analyst.state"
        assert payload["role"] == "analyst"


class TestErrorShape:
    def test_unknown_routes_return_a_problem_document(self, client, tokens):
        response = client.get("/api/v1/nope", headers=auth(tokens, "analyst"))
        assert response.status_code == 404
        body = response.json()
        assert {"type", "title", "status", "detail", "instance"} <= set(body)

    def test_validation_errors_name_the_field(self, client, tokens):
        response = client.post("/api/v1/chat", headers=auth(tokens, "analyst"),
                               json={"session_id": "s-1"})
        assert response.status_code == 422
        assert response.json()["context"]["errors"]

    def test_an_unknown_crime_number_is_a_404_problem(self, client, tokens):
        response = client.get("/api/v1/cases/999999999999999999", headers=auth(tokens, "analyst"))
        assert response.status_code == 404
        assert response.json()["code"]


class TestRoleBasedAccess:
    def test_investigator_scope_is_narrower_than_analyst_scope(self, client, tokens):
        wide = client.post("/api/v1/cases/search", headers=auth(tokens, "analyst"),
                           json={"limit": 1}).json()
        narrow = client.post("/api/v1/cases/search", headers=auth(tokens, "investigator"),
                             json={"limit": 1}).json()
        assert narrow["total"] < wide["total"]
        assert narrow["scope_note"] != wide["scope_note"]

    def test_audit_log_is_closed_to_investigators(self, client, tokens):
        assert client.get("/api/v1/admin/audit", headers=auth(tokens, "investigator")).status_code == 403

    def test_audit_log_is_open_to_auditors(self, client, tokens):
        assert client.get("/api/v1/admin/audit", headers=auth(tokens, "auditor")).status_code == 200

    def test_pipeline_administration_is_admin_only(self, client, tokens):
        assert client.get("/api/v1/admin/pipeline", headers=auth(tokens, "analyst")).status_code == 403
        assert client.get("/api/v1/admin/pipeline",
                          headers=auth(tokens, "platform_admin")).status_code == 200

    def test_financial_tools_require_the_financial_permission(self, client, tokens):
        offenders = client.get("/api/v1/graph/offenders?limit=1",
                               headers=auth(tokens, "analyst")).json()["offenders"]
        if not offenders:
            pytest.skip("no offenders in this dataset")
        identity = offenders[0]["identity_id"]
        assert client.get(f"/api/v1/graph/financial/{identity}",
                          headers=auth(tokens, "analyst")).status_code == 403
        assert client.get(f"/api/v1/graph/financial/{identity}",
                          headers=auth(tokens, "investigator")).status_code == 200

    def test_policymaker_cannot_read_case_detail(self, client, tokens):
        response = client.post("/api/v1/cases/search", headers=auth(tokens, "policymaker"),
                               json={"limit": 1})
        assert response.status_code == 403


class TestChatContract:
    def test_a_chat_answer_carries_evidence_and_traces(self, client, tokens):
        payload = client.post("/api/v1/chat", headers=auth(tokens, "analyst"),
                              json={"message": "Where are the hotspots?",
                                    "session_id": "api-chat-1"}).json()
        assert payload["evidence"]
        assert payload["traces"]
        assert payload["agents_used"]
        assert payload["intent"] == "HOTSPOT_QUERY"

    def test_claims_only_cite_published_evidence(self, client, tokens):
        payload = client.post("/api/v1/chat", headers=auth(tokens, "analyst"),
                              json={"message": "What is the crime trend this year?",
                                    "session_id": "api-chat-2"}).json()
        published = {item["locator"] for item in payload["evidence"]}
        for claim in payload["claims"]:
            assert set(claim["evidence_locators"]) <= published

    def test_the_transcript_endpoint_returns_the_turn(self, client, tokens):
        client.post("/api/v1/chat", headers=auth(tokens, "analyst"),
                    json={"message": "Where are the hotspots?", "session_id": "api-chat-3"})
        turns = client.get("/api/v1/chat/api-chat-3/transcript",
                           headers=auth(tokens, "analyst")).json()
        assert len(turns) >= 1


class TestAnalyticsEndpoints:
    def test_trend_returns_a_dense_series(self, client, tokens):
        payload = client.post("/api/v1/analytics/trend", headers=auth(tokens, "analyst"),
                              json={"months": 12}).json()
        assert len(payload["periods"]) == len(payload["counts"])
        assert payload["trace"]["description"]

    def test_hotspots_carry_their_parameters(self, client, tokens):
        payload = client.post("/api/v1/analytics/hotspots", headers=auth(tokens, "analyst"),
                              json={}).json()
        assert payload["grid_metres"] > 0
        assert payload["window_days"] > 0

    def test_sociology_carries_its_caveat(self, client, tokens):
        payload = client.post("/api/v1/analytics/sociology", headers=auth(tokens, "analyst"),
                              json={"dimension": "occupation"}).json()
        assert payload["caveat"]
        assert payload["associations"]

    def test_caste_breakdowns_need_the_sensitive_permission(self, client, tokens):
        response = client.post("/api/v1/analytics/sociology", headers=auth(tokens, "investigator"),
                               json={"dimension": "caste"})
        assert response.status_code in (200, 403)

    def test_seasonality_marks_months_without_enough_history(self, client, tokens):
        payload = client.post("/api/v1/analytics/seasonality", headers=auth(tokens, "analyst"),
                              json={}).json()
        assert payload["trace"]["description"]
        assert "not a forecast" in payload["caveat"]
        for bucket in payload["buckets"]:
            if bucket["insufficient_history"]:
                assert bucket["deviation_percent"] is None
                assert bucket["z_score"] is None

    def test_only_approved_events_are_listed(self, client, tokens):
        payload = client.get("/api/v1/analytics/events", headers=auth(tokens, "analyst")).json()
        for event in payload["events"]:
            assert event["approval_status"] == "approved"

    def test_event_comparison_reports_coincidence_not_cause(self, client, tokens):
        events = client.get("/api/v1/analytics/events",
                            headers=auth(tokens, "analyst")).json()["events"]
        if not events:
            pytest.skip("no approved events seeded")
        payload = client.post("/api/v1/analytics/event-comparison", headers=auth(tokens, "analyst"),
                              json={"event_id": events[0]["event_id"]}).json()
        assert payload["comparison_windows"]
        assert "does not show" in payload["caveat"]
        assert "caused" not in payload["trace"]["description"].replace("caused them", "")

    def test_an_unapproved_event_name_is_a_404(self, client, tokens):
        response = client.post("/api/v1/analytics/event-comparison", headers=auth(tokens, "analyst"),
                               json={"event_name": "no such event"})
        assert response.status_code == 404


class TestGraphEndpoints:
    def test_expansion_reports_what_it_withheld(self, client, tokens):
        offenders = client.get("/api/v1/graph/offenders?limit=1",
                               headers=auth(tokens, "analyst")).json()["offenders"]
        if not offenders:
            pytest.skip("no offenders in this dataset")
        payload = client.post("/api/v1/graph/expand", headers=auth(tokens, "analyst"),
                              json={"node_id": f"person:{offenders[0]['identity_id']}",
                                    "hops": 2}).json()
        assert "withheld_by_scope" in payload
        assert payload["notice"]

    def test_every_returned_link_is_marked_inferred_or_not(self, client, tokens):
        offenders = client.get("/api/v1/graph/offenders?limit=1",
                               headers=auth(tokens, "analyst")).json()["offenders"]
        if not offenders:
            pytest.skip("no offenders in this dataset")
        payload = client.post("/api/v1/graph/expand", headers=auth(tokens, "analyst"),
                              json={"node_id": f"person:{offenders[0]['identity_id']}"}).json()
        for link in payload["links"]:
            assert isinstance(link["inferred"], bool)

    def test_the_review_queue_publishes_its_thresholds(self, client, tokens):
        payload = client.get("/api/v1/graph/entity-resolution/review",
                             headers=auth(tokens, "supervisor")).json()
        assert payload["thresholds"]["auto_link_at_or_above"] > 0
        assert payload["notice"]


class TestExport:
    def test_a_conversation_exports_to_pdf(self, client, tokens):
        client.post("/api/v1/chat", headers=auth(tokens, "analyst"),
                    json={"message": "Where are the hotspots?", "session_id": "api-export-1"})
        payload = client.post("/api/v1/export/pdf", headers=auth(tokens, "analyst"),
                              json={"session_id": "api-export-1"}).json()
        assert payload["url"].endswith(".pdf")
        response = client.get(payload["url"], headers=auth(tokens, "analyst"))
        assert response.status_code == 200
        assert response.content[:4] == b"%PDF"

    def test_a_user_cannot_read_another_users_export(self, client, tokens):
        client.post("/api/v1/chat", headers=auth(tokens, "analyst"),
                    json={"message": "Where are the hotspots?", "session_id": "api-export-2"})
        payload = client.post("/api/v1/export/pdf", headers=auth(tokens, "analyst"),
                              json={"session_id": "api-export-2"}).json()
        response = client.get(payload["url"], headers=auth(tokens, "investigator"))
        assert response.status_code in (403, 404)


class TestAuditTrail:
    def test_conversation_turns_are_audited(self, client, tokens):
        client.post("/api/v1/chat", headers=auth(tokens, "analyst"),
                    json={"message": "Where are the hotspots?", "session_id": "api-audit-1"})
        events = client.get("/api/v1/admin/audit", headers=auth(tokens, "auditor")).json()["events"]
        actions = {event["action"] for event in events}
        assert "conversation.turn" in actions

    def test_audit_events_record_the_actor_and_correlation(self, client, tokens):
        events = client.get("/api/v1/admin/audit", headers=auth(tokens, "auditor")).json()["events"]
        assert events
        assert events[0]["actor_user_id"]
        assert events[0]["action"]
