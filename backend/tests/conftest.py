"""Shared fixtures.

One seeded platform is built per test session and shared, because seeding is
the expensive part and the tests are read-only against it. Tests that mutate
state (identity review decisions, admin actions) use their own container.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# Point Settings at a file that does not exist, BEFORE ksp_cip is imported.
# pydantic-settings resolves `env_file` when the settings class is created, so
# this has to happen above the import below.
#
# Without it the suite reads whatever `.env` the developer happens to have at
# the repo root, and tests that assert on *missing* configuration silently
# invert: "catalyst backend without a project id is refused" passes on a clean
# machine and fails the moment someone adds real credentials. Tests must
# describe the code, not the machine they run on.
os.environ["KSPCIP_ENV_FILE"] = str(BACKEND_ROOT / "tests" / ".env.absent")

# Makes the evaluation harness importable as ``evals.harness`` from any test
# module. The tests tree is intentionally not a package (pytest imports test
# files as top-level modules), so the shared helper is reached this way.
TESTS_ROOT = Path(__file__).resolve().parent
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))

from ksp_cip.config import Settings  # noqa: E402
from ksp_cip.interface.container import build_container  # noqa: E402

SEED_CASES = 700
SEED_MONTHS = 24


def make_settings(tmp_path: Path, **overrides) -> Settings:
    return Settings(
        sqlite_path=tmp_path / "cip.db",
        filestore_root=tmp_path / "filestore",
        log_json=False,
        log_level="ERROR",
        synthetic_seed=20260725,
        **overrides,
    )


@pytest.fixture(scope="session")
def seeded(tmp_path_factory):
    """A fully seeded platform: container, seed summary and manifest."""
    tmp_path = tmp_path_factory.mktemp("cip-session")
    settings = make_settings(tmp_path)
    container = build_container(settings)
    summary = container.seeder.run(target_cases=SEED_CASES, months=SEED_MONTHS)
    container.warm()
    return {"container": container, "summary": summary, "manifest": summary["manifest"],
            "settings": settings}


@pytest.fixture(scope="session")
def container(seeded):
    return seeded["container"]


@pytest.fixture(scope="session")
def manifest(seeded):
    return seeded["manifest"]


@pytest.fixture(scope="session")
def client(seeded):
    from fastapi.testclient import TestClient
    from ksp_cip.interface.api.main import create_app

    return TestClient(create_app(seeded["settings"], seeded["container"]))


@pytest.fixture(scope="session")
def tokens(client):
    """Bearer headers for each demo role."""
    from ksp_cip.application.pipeline import DEMO_PASSWORD, DEMO_USERS

    headers = {}
    for username, _display, role, _district in DEMO_USERS:
        response = client.post("/api/v1/auth/login",
                               json={"username": username, "password": DEMO_PASSWORD})
        assert response.status_code == 200, response.text
        headers[str(role)] = {"Authorization": f"Bearer {response.json()['access_token']}"}
    return headers


@pytest.fixture
def fresh_container(tmp_path):
    """An unseeded container for tests that need to mutate or seed themselves."""
    return build_container(make_settings(tmp_path))


@pytest.fixture(scope="session")
def analyst(container):
    principal, _token = container.identity_service.authenticate("analyst.state", "ChangeMe#2026")
    return principal


@pytest.fixture(scope="session")
def investigator(container):
    principal, _token = container.identity_service.authenticate("io.bengaluru", "ChangeMe#2026")
    return principal
