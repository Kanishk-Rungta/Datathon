"""Read-only Catalyst reachability checks (Phase 0, P0-06).

Deliberately separate from ``test_deployment_smoke.py``, which writes and
deletes rows. This file only ever reads, so it is safe to point at a shared
Development project more casually — but it still fails closed rather than
inferring a target, per the same rule.

Gate (all required):

- ``KSPCIP_RUN_CATALYST_TESTS=1`` — explicit opt-in, distinct from the
  mutating suite's ``KSPCIP_SMOKE_ENABLED`` so the two cannot be confused.
- A Catalyst project id.
- OAuth credentials.
- ``KSPCIP_CATALYST_ENVIRONMENT`` names the intended Development environment
  (never ``Production``).
"""

from __future__ import annotations

import os

import pytest

RUN_ENABLED = os.environ.get("KSPCIP_RUN_CATALYST_TESTS") == "1"
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
    pytest.mark.deployment,
    pytest.mark.skipif(not RUN_ENABLED, reason="set KSPCIP_RUN_CATALYST_TESTS=1 to run Catalyst readiness checks"),
    pytest.mark.skipif(not PROJECT_ID, reason="KSPCIP_CATALYST_PROJECT_ID is not set"),
    pytest.mark.skipif(not HAS_CREDENTIALS, reason="Catalyst OAuth credentials are not set"),
    pytest.mark.skipif(
        ENVIRONMENT.lower() == "production",
        reason="refusing to point readiness checks at a Production Catalyst environment",
    ),
]


@pytest.fixture(scope="module")
def live_settings():
    from ksp_cip.config import Settings

    settings = Settings()
    return settings


class TestDataStoreReadOnlyReachability:
    def test_a_read_only_query_succeeds(self, live_settings):
        from ksp_cip.infrastructure.catalyst.datastore import CatalystDataStore

        store = CatalystDataStore(live_settings)
        rows = store.query("SELECT version FROM ctl_schema_version")
        assert isinstance(rows, list)

    def test_the_expected_curated_tables_are_reachable(self, live_settings):
        """A minimal shape check; the full manifest check lives in Phase 2."""
        from ksp_cip.infrastructure.catalyst.datastore import CatalystDataStore

        store = CatalystDataStore(live_settings)
        for table in ("curated_CaseMaster", "curated_District", "curated_Unit"):
            rows = store.query(f"SELECT COUNT(*) AS n FROM {table}")
            assert rows and "n" in rows[0], f"{table} did not respond to a count query"


class TestStratusReadOnlyReachability:
    def test_the_ingest_bucket_is_listable(self, live_settings):
        from ksp_cip.infrastructure.catalyst.stratus import StratusFileStore

        store = StratusFileStore(live_settings)
        # An empty or non-empty listing are both fine; the call must not raise.
        assert isinstance(store.list_keys(""), list)


class TestApplicationReadiness:
    def test_the_configured_settings_are_deployable(self, live_settings):
        """Whatever backends this environment has selected must pass the same
        startup validation ``build_container()`` enforces — checked directly
        here so a failure names the problem without needing a full build."""
        problems = live_settings.deployment_problems()
        assert not problems, "; ".join(problems)
