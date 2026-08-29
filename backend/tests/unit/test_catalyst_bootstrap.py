"""`catalyst/_bootstrap.py` — the one file every deployed entrypoint runs first.

It has no import site inside `ksp_cip`, so nothing else in the suite would
notice it breaking. Each test here pins a defect that actually shipped:

* ``KSPCIP_ENVIRONMENT=catalyst`` — not a member of the ``Environment`` enum,
  so ``Settings()`` raised before the app could start. "Catalyst" is a hosting
  platform, not a deployment environment.
* Defaulting the data store to Catalyst while leaving the file store on
  ``local`` — a combination ``Settings.deployment_problems()`` rejects by
  design, which made every first deploy fail at startup.
* Resolving ``ksp_cip`` from only one of the two layouts (staged artifact vs
  repo checkout) — the defect P1-03 exists to catch.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BOOTSTRAP_PATH = REPO_ROOT / "catalyst" / "_bootstrap.py"

ENV_VARS = (
    "KSPCIP_ENVIRONMENT",
    "KSPCIP_CATALYST_ENVIRONMENT",
    "KSPCIP_DATASTORE_BACKEND",
    "KSPCIP_FILESTORE_BACKEND",
)


@pytest.fixture(scope="module")
def bootstrap_module():
    spec = importlib.util.spec_from_file_location("cip_bootstrap_under_test", BOOTSTRAP_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def clean_env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sys, "path", list(sys.path))
    return monkeypatch


class TestLayoutResolution:
    def test_the_repo_checkout_layout_resolves(self, bootstrap_module):
        """Running an entrypoint straight out of `catalyst/` in a checkout."""
        found = bootstrap_module.locate_backend_root(REPO_ROOT / "catalyst" / "appsail" / "api")
        assert (found / "ksp_cip").is_dir()
        assert found == REPO_ROOT / "backend"

    def test_the_staged_artifact_layout_wins_over_the_checkout(self, bootstrap_module, tmp_path):
        """A staged artifact carries `ksp_cip` as a sibling; that must be used
        even when a `backend/ksp_cip` also exists further up."""
        staged = tmp_path / "backend" / "cip-api"
        (staged / "ksp_cip").mkdir(parents=True)
        (tmp_path / "backend" / "ksp_cip").mkdir(parents=True)
        assert bootstrap_module.locate_backend_root(staged) == staged

    def test_an_unresolvable_layout_fails_loudly(self, bootstrap_module, tmp_path):
        with pytest.raises(RuntimeError, match="ksp_cip"):
            bootstrap_module.locate_backend_root(tmp_path)


class TestEnvironmentDefaults:
    def test_it_never_sets_an_environment_the_settings_enum_rejects(
        self, bootstrap_module, clean_env
    ):
        from ksp_cip.config.settings import Environment

        bootstrap_module.bootstrap(str(BOOTSTRAP_PATH))
        import os

        assert Environment(os.environ["KSPCIP_ENVIRONMENT"]) is Environment.DEVELOPMENT

    def test_a_catalyst_production_environment_maps_to_production(
        self, bootstrap_module, clean_env
    ):
        import os

        clean_env.setenv("KSPCIP_CATALYST_ENVIRONMENT", "Production")
        bootstrap_module.bootstrap(str(BOOTSTRAP_PATH))
        assert os.environ["KSPCIP_ENVIRONMENT"] == "production"

    def test_an_explicit_environment_is_never_overridden(self, bootstrap_module, clean_env):
        import os

        clean_env.setenv("KSPCIP_ENVIRONMENT", "staging")
        clean_env.setenv("KSPCIP_CATALYST_ENVIRONMENT", "Production")
        bootstrap_module.bootstrap(str(BOOTSTRAP_PATH))
        assert os.environ["KSPCIP_ENVIRONMENT"] == "staging"

    def test_the_defaults_it_sets_are_a_deployable_combination(
        self, bootstrap_module, clean_env
    ):
        """Data store and file store are not independent once either is
        Catalyst. Whatever bootstrap defaults must pass the same validator the
        container runs, given only the credentials a deployer supplies."""
        from ksp_cip.config import Settings

        bootstrap_module.bootstrap(str(BOOTSTRAP_PATH))
        clean_env.setenv("KSPCIP_CATALYST_PROJECT_ID", "test-project")
        for name in ("CLIENT_ID", "CLIENT_SECRET", "REFRESH_TOKEN"):
            clean_env.setenv(f"KSPCIP_CATALYST_OAUTH_{name}", "test-value")
        clean_env.setenv("KSPCIP_JWT_SECRET", "not-the-placeholder")

        problems = Settings(_env_file=None).deployment_problems()
        assert problems == []

    def test_an_explicit_file_store_choice_survives(self, bootstrap_module, clean_env):
        import os

        clean_env.setenv("KSPCIP_FILESTORE_BACKEND", "local")
        bootstrap_module.bootstrap(str(BOOTSTRAP_PATH))
        assert os.environ["KSPCIP_FILESTORE_BACKEND"] == "local"
