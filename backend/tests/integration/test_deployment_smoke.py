"""Live Catalyst smoke tests — skipped unless explicitly pointed at a project.

Gate 0 of ``implementationv2.md`` asks for a collection that "must never run
against an unspecified project". That is enforced here by three conditions,
all of which must hold before a single call is made:

1. ``KSPCIP_SMOKE_ENABLED=1`` — an explicit opt-in, so no CI run stumbles in.
2. A Catalyst project id and OAuth credentials are present.
3. ``KSPCIP_CATALYST_ENVIRONMENT`` is *not* ``Production``.

Condition 3 is the important one. These tests write and delete rows. Pointing
them at a production project would mutate real records, so the suite refuses to
run there even if someone sets the opt-in flag.
"""

from __future__ import annotations

import os
import uuid

import pytest

SMOKE_ENABLED = os.environ.get("KSPCIP_SMOKE_ENABLED") == "1"
PROJECT_ID = os.environ.get("KSPCIP_CATALYST_PROJECT_ID")
ENVIRONMENT = os.environ.get("KSPCIP_CATALYST_ENVIRONMENT", "Development")
HAS_CREDENTIALS = all(
    os.environ.get(name)
    for name in (
        "KSPCIP_CATALYST_OAUTH_CLIENT_ID",
        "KSPCIP_CATALYST_OAUTH_CLIENT_SECRET",
        "KSPCIP_CATALYST_OAUTH_REFRESH_TOKEN",
    )
)

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not SMOKE_ENABLED, reason="set KSPCIP_SMOKE_ENABLED=1 to run live Catalyst smoke tests"),
    pytest.mark.skipif(not PROJECT_ID, reason="KSPCIP_CATALYST_PROJECT_ID is not set"),
    pytest.mark.skipif(not HAS_CREDENTIALS, reason="Catalyst OAuth credentials are not set"),
    pytest.mark.skipif(
        ENVIRONMENT.lower() == "production",
        reason="refusing to run mutating smoke tests against a Production Catalyst environment",
    ),
]


@pytest.fixture(scope="module")
def live_settings():
    from ksp_cip.config import Settings
    from ksp_cip.config.settings import DataStoreBackend, FileStoreBackend

    settings = Settings(
        datastore_backend=DataStoreBackend.CATALYST,
        filestore_backend=FileStoreBackend.CATALYST,
    )
    problems = settings.deployment_problems()
    assert not problems, "configuration is not deployable: " + "; ".join(problems)
    return settings


@pytest.fixture(scope="module")
def live_store(live_settings):
    from ksp_cip.infrastructure.catalyst.datastore import CatalystDataStore

    return CatalystDataStore(live_settings)


class TestDataStoreReachability:
    def test_a_trivial_query_returns(self, live_store):
        rows = live_store.query("SELECT ROWID FROM cip_kv LIMIT 0, 1")
        assert isinstance(rows, list)

    def test_the_schema_version_table_exists(self, live_store):
        rows = live_store.query("SELECT version FROM ctl_schema_version")
        assert isinstance(rows, list), "ctl_schema_version is missing; provision the schema first"


class TestUpsertAgainstLiveZCQL:
    """The read-then-write path the adapter emulates for ``ON CONFLICT``."""

    def test_an_upsert_writes_then_updates_rather_than_duplicating(self, live_store):
        namespace = "agent_scratchpad"
        key = f"smoke-{uuid.uuid4().hex[:12]}"
        statement = (
            "INSERT INTO cip_kv (namespace, kv_key, value_json, expires_at, updated_at)"
            " VALUES (:ns, :k, :v, :e, :u)"
            " ON CONFLICT (namespace, kv_key) DO UPDATE SET value_json = excluded.value_json"
        )
        try:
            live_store.execute(statement, {"ns": namespace, "k": key, "v": '{"n":1}',
                                           "e": None, "u": "2026-01-01T00:00:00+00:00"})
            live_store.execute(statement, {"ns": namespace, "k": key, "v": '{"n":2}',
                                           "e": None, "u": "2026-01-01T00:00:00+00:00"})
            rows = live_store.query(
                "SELECT value_json FROM cip_kv WHERE namespace = :ns AND kv_key = :k",
                {"ns": namespace, "k": key},
            )
            assert len(rows) == 1, "the upsert duplicated instead of updating"
            assert '"n":2' in rows[0]["value_json"].replace(" ", "")
        finally:
            live_store.execute(
                "DELETE FROM cip_kv WHERE namespace = :ns AND kv_key = :k",
                {"ns": namespace, "k": key},
            )


class TestStratusRoundTrip:
    def test_an_object_writes_and_reads_back(self, live_settings):
        """Write and read by exact key — the only path the application uses."""
        from ksp_cip.infrastructure.catalyst.stratus import StratusFileStore

        store = StratusFileStore(live_settings)
        key = f"smoke/{uuid.uuid4().hex[:12]}.txt"
        payload = b"ksp-cip smoke test"

        store.write_bytes(key, payload, "text/plain")
        assert store.read_bytes(key) == payload
        assert store.exists(key)

    def test_listing_is_refused_with_a_reason(self, live_settings):
        """Stratus publishes no list-objects REST endpoint.

        The bucket origin answers a prefixed GET with a bare 405 and every
        baas-hosted shape with INVALID_URL_PATTERN, so the adapter refuses
        rather than surfacing a transport error the caller cannot act on.
        """
        from ksp_cip.domain.errors import ProviderError
        from ksp_cip.infrastructure.catalyst.stratus import StratusFileStore

        store = StratusFileStore(live_settings)
        with pytest.raises(ProviderError, match="no documented list-objects"):
            store.list_keys("smoke/")

    def test_export_urls_are_not_public_object_links(self, live_settings):
        """``url_for`` must route through the authorising application path."""
        from ksp_cip.infrastructure.catalyst.stratus import StratusFileStore

        url = StratusFileStore(live_settings).url_for("exports/u1/report.pdf")
        assert url.startswith("/api/v1/files/")
        assert "http" not in url
