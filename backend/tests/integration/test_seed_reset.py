"""`seed --reset` must actually reset, and must leave a usable platform behind.

Two defects this pins, both of which shipped:

1. **The truncation order was incomplete.** ``curated_ComplainantDetails`` is
   ``NOT NULL REFERENCES curated_CaseMaster`` and was missing from the delete
   list, so with foreign keys on (they are) every ``--reset`` died on
   "FOREIGN KEY constraint failed" before writing a single row.

2. **A half-finished seed is indistinguishable from a finished one.** The seed
   creates demo accounts, the event calendar and the socio-economic indicators
   *after* the intelligence refresh. A run interrupted in between leaves a
   database full of cases that nobody can log in to — which is exactly the
   state the checked-in ``backend/var/ksp_cip.db`` was found in. Asserting the
   tail of the pipeline, not just the case count, is what catches that.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.slow

RESET_CASES = 120
RESET_MONTHS = 18


@pytest.fixture(scope="module")
def reset_twice(tmp_path_factory):
    """Seed, then seed again with --reset over the top of the first run."""
    from conftest import make_settings
    from ksp_cip.interface.container import build_container

    settings = make_settings(tmp_path_factory.mktemp("cip-reset"))
    container = build_container(settings)
    first = container.seeder.run(target_cases=RESET_CASES, months=RESET_MONTHS)
    second = container.seeder.run(target_cases=RESET_CASES, months=RESET_MONTHS, reset=True)
    return {"container": container, "first": first, "second": second}


class TestResetCompletes:
    def test_reset_does_not_trip_a_foreign_key(self, reset_twice):
        assert reset_twice["second"]["generated_cases"] == RESET_CASES

    def test_reset_leaves_exactly_one_generation_of_cases(self, reset_twice):
        control = reset_twice["container"].control
        assert control.store_row_count("curated_CaseMaster") == RESET_CASES

    def test_reset_clears_the_child_tables_too(self, reset_twice):
        """One complainant per case: a stale generation would double this."""
        control = reset_twice["container"].control
        assert control.store_row_count("curated_ComplainantDetails") == RESET_CASES


class TestSeedFinishesItsTail:
    """Everything after the intelligence refresh, which is where a partial
    seed silently stops."""

    def test_demo_accounts_exist_so_the_platform_can_be_signed_into(self, reset_twice):
        from ksp_cip.application.pipeline import DEMO_PASSWORD, DEMO_USERS

        container = reset_twice["container"]
        for username, _display, _role, _district in DEMO_USERS:
            principal, token = container.identity_service.authenticate(username, DEMO_PASSWORD)
            assert principal.username == username
            assert token["access_token"]

    def test_the_event_calendar_is_populated(self, reset_twice):
        assert reset_twice["container"].events.count() > 0

    def test_socio_economic_indicators_are_populated(self, reset_twice):
        assert reset_twice["container"].socioeconomic_repository.count() > 0

    def test_the_seed_watermark_is_recorded(self, reset_twice):
        watermark = reset_twice["container"].control.watermark("seed")
        assert watermark
