"""`cip.py` is the one entry point a new user runs, so it has to hold together.

It is not application code — it installs, seeds and launches — but three of its
properties are load-bearing and easy to break silently:

1. **It must import and run on a bare interpreter.** Its whole purpose is to
   build the virtualenv, so it cannot need anything from it. A stray
   `import httpx` at the top would make the first command a new user types fail
   with the exact error the file exists to prevent.
2. **It must not become a second application factory.** `get_app()` is the only
   place the ASGI app is constructed, because Catalyst runs
   `catalyst/appsail/api/server.py` and never runs this file — anything
   configured here and not there is a behaviour that works locally and
   silently differs in deployment.
3. **Its "is this seeded?" check must look at the user accounts**, not just the
   case count. A seed interrupted after the intelligence refresh leaves cases
   with no accounts, which is the state the checked-in database shipped in and
   which nothing else reports.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
LAUNCHER = REPO_ROOT / "cip.py"


@pytest.fixture(scope="module")
def cip():
    spec = importlib.util.spec_from_file_location("cip_under_test", LAUNCHER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TestRunsWithoutTheVirtualenv:
    def test_it_imports_using_only_the_standard_library(self):
        """`-I` strips PYTHONPATH and user site-packages; -S skips site-packages
        entirely, so anything pip installed is unreachable for this check."""
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-c",
             f"import importlib.util as u; "
             f"s = u.spec_from_file_location('cip', {str(LAUNCHER)!r}); "
             f"m = u.module_from_spec(s); s.loader.exec_module(m); "
             f"print(sorted(c for c in dir(m) if c.startswith('cmd_')))"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "cmd_run" in result.stdout

    def test_help_works_from_a_bare_interpreter(self):
        result = subprocess.run(
            [sys.executable, "-I", str(LAUNCHER), "--help"],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, result.stderr
        for command in ("setup", "seed", "run", "dev", "test", "package", "doctor"):
            assert command in result.stdout


class TestItIsNotASecondApplicationFactory:
    def test_it_never_imports_the_application(self):
        source = LAUNCHER.read_text(encoding="utf-8")
        code = "\n".join(
            line for line in source.splitlines()
            if not line.lstrip().startswith("#")
        )
        for forbidden in ("import ksp_cip", "from ksp_cip", "create_app", "FastAPI("):
            assert forbidden not in code, (
                f"cip.py references {forbidden!r}. It launches the application as a "
                "subprocess and must not construct or import it: get_app() is the only "
                "application factory, and Catalyst runs server.py, never this file."
            )

    def test_it_launches_the_same_factory_the_deployment_does(self, cip):
        source = LAUNCHER.read_text(encoding="utf-8")
        factory = "ksp_cip.interface.api.main:get_app"
        assert factory in source
        appsail = (REPO_ROOT / "catalyst" / "appsail" / "api" / "server.py").read_text(encoding="utf-8")
        assert "from ksp_cip.interface.api.main import get_app" in appsail


class TestSeedCompletenessCheck:
    @pytest.mark.parametrize("state,expected_reason", [
        ({"cases": -1, "users": -1, "socioeconomic": -1}, "no cases"),
        ({"cases": 0, "users": 0, "socioeconomic": 0}, "no cases"),
        ({"cases": 4200, "users": 0, "socioeconomic": 31}, "no user accounts"),
        ({"cases": 4200, "users": 6, "socioeconomic": 0}, "socio-economic"),
    ])
    def test_an_incomplete_seed_is_detected(self, cip, state, expected_reason):
        reason = cip.needs_seed(state)
        assert reason is not None
        assert expected_reason in reason

    def test_a_complete_seed_needs_nothing(self, cip):
        assert cip.needs_seed({"cases": 4200, "users": 6, "socioeconomic": 31}) is None

    def test_a_missing_database_reads_as_missing_rather_than_raising(self, cip, tmp_path, monkeypatch):
        monkeypatch.setenv("KSPCIP_SQLITE_PATH", str(tmp_path / "absent.db"))
        assert cip.database_state() == {"cases": -1, "users": -1, "socioeconomic": -1}

    def test_a_file_that_is_not_a_database_does_not_crash_the_launcher(
        self, cip, tmp_path, monkeypatch
    ):
        junk = tmp_path / "not-a-db.db"
        junk.write_bytes(b"this is not a sqlite file")
        monkeypatch.setenv("KSPCIP_SQLITE_PATH", str(junk))
        assert cip.needs_seed(cip.database_state()) is not None


class TestPackageListMatchesTheProject:
    def test_every_runtime_dependency_is_installed_by_the_launcher(self, cip):
        """A dependency added to pyproject.toml but not here produces a
        virtualenv that cannot run the thing it was built for."""
        pyproject = (REPO_ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8")
        block = pyproject.split("dependencies = [", 1)[1].split("]", 1)[0]
        declared = {
            line.strip().strip('",').split(">=")[0].split("[")[0].strip()
            for line in block.splitlines()
            if line.strip().startswith('"')
        }
        installed = {name.split(">=")[0].split("[")[0] for name in cip.PACKAGES}
        missing = declared - installed
        assert not missing, f"cip.py does not install: {sorted(missing)}"
