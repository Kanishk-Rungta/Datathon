#!/usr/bin/env python3
"""KSP-CIP — one entry point for everything you can run locally.

    python cip.py                 install, seed if needed, and serve
    python cip.py doctor          report what state this checkout is in
    python cip.py test            run the test suite
    python cip.py package         build the three Catalyst deployment artifacts

Why this file exists
--------------------
There were two front doors before this (``scripts/*.sh`` and ``scripts/*.ps1``)
and neither is something you can simply double-click on Windows: Explorer asks
which program should open a ``.sh``, PowerShell may refuse an unsigned ``.ps1``
under the default execution policy, and ``python3`` on a stock Windows PATH is
the Microsoft Store alias — a stub that prints an advert and exits. This file
removes all of that: if you can run Python at all, you can run the platform.

It is **stdlib-only and needs no virtualenv**, because its first job is to
build one. Nothing here imports ``ksp_cip``; it shells out to the interpreter
inside ``.venv`` for anything that needs the dependencies.

What it does *not* do
---------------------
It is not a second application factory, and Catalyst never runs it.
``ksp_cip.interface.api.main:get_app`` remains the only place the ASGI app is
constructed, and the deployed entrypoints stay ``catalyst/appsail/api/server.py``
and ``catalyst/functions/cip_refresh/main.py``. This file only *prepares* and
*launches* those same things locally, and builds the artifacts that get
deployed — so nothing you verify through it can diverge from what ships.
See ``python cip.py package`` and README §9.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
VENV = ROOT / ".venv"
IS_WINDOWS = os.name == "nt"
VENV_PYTHON = VENV / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")

MIN_PYTHON = (3, 11)

#: Kept in step with backend/pyproject.toml's `dependencies` (plus pytest).
#: httpx is a runtime dependency, not a test one: the hosted LLM providers and
#: the Bhashini adapter import it lazily, so its absence surfaces as a
#: ModuleNotFoundError on the first real call rather than at startup.
PACKAGES = [
    "fastapi", "uvicorn[standard]", "pydantic", "pydantic-settings",
    "python-multipart", "numpy", "networkx", "reportlab", "jinja2", "httpx",
    "pytest",
]

#: One importable name per package that a plain `pip install` would provide.
#: Checked instead of a marker file so an interrupted install is detected.
IMPORT_PROBE = (
    "import fastapi, uvicorn, pydantic, pydantic_settings, multipart, "
    "numpy, networkx, reportlab, jinja2, httpx, pytest"
)

DEFAULT_CASES = 4200
DEFAULT_MONTHS = 30

#: How to invoke this file, as the user actually invoked it. `cip.bat` sets
#: this so a Windows user who double-clicked is not told to type something
#: else; everyone else sees the portable form.
LAUNCHER = os.environ.get("CIP_LAUNCHER") or "python cip.py"

# Everything printed by this file is ASCII on purpose: a Windows console
# defaults to cp1252 and renders an em dash as a replacement character in the
# one message a first-time user actually reads.


# --------------------------------------------------------------------- output

def say(message: str = "") -> None:
    print(message, flush=True)


def step(message: str) -> None:
    say(f"==> {message}")


def detail(message: str) -> None:
    say(f"    {message}")


def die(message: str, *hints: str) -> "NoReturn":  # type: ignore[valid-type]
    say("")
    say(f"ERROR: {message}")
    for hint in hints:
        detail(hint)
    raise SystemExit(1)


# ----------------------------------------------------------------- interpreter

def usable_python() -> str:
    """Return a Python 3.11+ that actually runs.

    `shutil.which("python3")` succeeds on a stock Windows install by finding
    the Microsoft Store App Execution Alias, which is on PATH and is not an
    interpreter. Being on PATH and being usable are different things, so each
    candidate is executed before it is accepted.
    """
    if sys.version_info >= MIN_PYTHON:
        return sys.executable

    for name in ("python3", "python", "py"):
        found = shutil.which(name)
        if not found:
            continue
        probe = subprocess.run(
            [found, "-c", "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"],
            capture_output=True,
        )
        if probe.returncode == 0:
            return found

    die(
        f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required; "
        f"this is {sys.version.split()[0]} and no newer one was found on PATH.",
        "Install it from https://www.python.org/downloads/ and tick 'Add python.exe to PATH'.",
        "On Windows, 'python3' may be the Microsoft Store alias rather than a real install.",
    )


def run(command: list[str], *, cwd: Path | None = None, check: bool = True,
        quiet: bool = False) -> int:
    """Run a subprocess, streaming its output unless quiet."""
    result = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        capture_output=quiet,
        text=True,
    )
    if check and result.returncode != 0:
        if quiet and result.stderr:
            say(result.stderr.strip()[-2000:])
        die(f"Command failed ({result.returncode}): {' '.join(command)}")
    return result.returncode


# ------------------------------------------------------------------- ensure_*

def ensure_venv(*, quiet: bool = False) -> Path:
    """Create .venv if it is missing, and return its interpreter.

    An existing virtualenv is reused rather than recreated: re-running `venv`
    over a live one fails with a permission error if anything is holding
    python.exe open (a running server, an editor's language server), and
    re-running setup after a dependency change is the normal case.
    """
    if VENV_PYTHON.exists():
        if not quiet:
            detail(f"reusing {VENV.name}")
        return VENV_PYTHON

    base = usable_python()
    step(f"Creating a virtualenv at {VENV.name}")
    run([base, "-m", "venv", str(VENV)])
    if not VENV_PYTHON.exists():  # pragma: no cover - platform layout guard
        die(f"venv was created but {VENV_PYTHON} is missing.")
    return VENV_PYTHON


def dependencies_present(python: Path) -> bool:
    return subprocess.run([str(python), "-c", IMPORT_PROBE], capture_output=True).returncode == 0


def ensure_dependencies(python: Path, *, force: bool = False) -> None:
    if not force and dependencies_present(python):
        detail("dependencies already installed")
        return
    step("Installing Python dependencies (this takes a few minutes the first time)")
    run([str(python), "-m", "pip", "install", "--upgrade", "pip", "-q"], check=False)
    run([str(python), "-m", "pip", "install", "-q", *PACKAGES])
    if not dependencies_present(python):
        die("Dependencies installed but still cannot be imported.",
            f"Try: {python} -m pip install {' '.join(PACKAGES)}")


def console_is_built() -> bool:
    return (FRONTEND / "dist" / "index.html").is_file()


def ensure_console(*, force: bool = False) -> bool:
    """Build the React console. Returns True if it is available afterwards.

    Absence is not fatal: the API and every capability behind it work without
    the console, so a machine with no Node still gets a working platform and
    is told exactly what it is missing.
    """
    if console_is_built() and not force:
        detail("console already built")
        return True
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        detail("npm not found - skipping the console build.")
        detail("The API still runs; only the web UI is unavailable. Install Node 18+ to get it.")
        return False
    step("Building the console")
    run([npm, "install", "--no-fund", "--no-audit", "--silent"], cwd=FRONTEND)
    run([npm, "run", "build"], cwd=FRONTEND, quiet=True)
    if not console_is_built():
        die("npm run build finished but frontend/dist/index.html is missing.")
    detail("built to frontend/dist")
    return True


# ------------------------------------------------------------------- database

def database_path() -> Path:
    override = os.environ.get("KSPCIP_SQLITE_PATH")
    return Path(override).expanduser() if override else BACKEND / "var" / "ksp_cip.db"


def database_state() -> dict[str, int]:
    """Row counts that decide whether a seed is needed.

    Read with plain sqlite3 rather than through the application, so this works
    before the dependencies are installed.

    `users` is here because it is the count that actually matters and the one
    nothing else reports. A seed writes demo accounts, the event calendar and
    the socio-economic indicators *after* the intelligence refresh, so a run
    interrupted in between leaves a database full of cases that nobody can log
    in to -- which is exactly the state the checked-in database was found in.
    Judging "is this seeded?" by the case count alone reproduces that bug.
    """
    path = database_path()
    state = {"cases": -1, "users": -1, "socioeconomic": -1}
    if not path.exists():
        return state
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:  # pragma: no cover - unreadable file
        return state
    try:
        for key, table in (("cases", "curated_CaseMaster"),
                           ("users", "cip_user_account"),
                           ("socioeconomic", "ext_socioeconomic_indicator")):
            try:
                state[key] = int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            except sqlite3.Error:
                state[key] = -1
    finally:
        connection.close()
    return state


def needs_seed(state: dict[str, int]) -> str | None:
    """Return why a seed is needed, or None."""
    if state["cases"] <= 0:
        return "the database has no cases"
    if state["users"] <= 0:
        return "the database has no user accounts, so nobody can sign in"
    if state["socioeconomic"] <= 0:
        return "socio-economic indicators are missing"
    return None


def seed(python: Path, *, cases: int, months: int, reset: bool) -> None:
    step(f"Seeding {cases} synthetic FIRs over {months} months"
         + (" (reset: existing rows are deleted first)" if reset else ""))
    command = [str(python), "-m", "ksp_cip.cli", "seed",
               "--cases", str(cases), "--months", str(months)]
    if reset:
        command.append("--reset")
    run(command, cwd=BACKEND)


# ------------------------------------------------------------------- commands

def cmd_setup(args: argparse.Namespace) -> int:
    python = ensure_venv()
    ensure_dependencies(python, force=args.reinstall)
    ensure_console(force=args.rebuild_console)
    say()
    step("Ready.")
    detail(f"Next:  {LAUNCHER}         (seeds if needed, then serves)")
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    python = ensure_venv(quiet=True)
    ensure_dependencies(python)
    seed(python, cases=args.cases, months=args.months, reset=args.reset)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    python = ensure_venv(quiet=True)
    ensure_dependencies(python)
    if not args.no_console:
        ensure_console()

    state = database_state()
    reason = needs_seed(state)
    if reason:
        detail(f"Seeding because {reason}.")
        # A half-seeded database has rows in the tables the seed writes first,
        # and `--reset` is what makes the rerun deterministic rather than
        # layering a second generation on top of the first.
        seed(python, cases=args.cases, months=args.months, reset=state["cases"] > 0)

    say()
    step(f"http://{args.host}:{args.port}")
    detail("Sign in with any account on the login screen. Password: ChangeMe#2026")
    detail("API documentation: /api/v1/docs")
    detail("Ctrl-C to stop.")
    say()
    command = [
        str(python), "-m", "uvicorn", "ksp_cip.interface.api.main:get_app",
        "--factory", "--host", args.host, "--port", str(args.port),
    ]
    if args.reload:
        command.append("--reload")
    return run(command, cwd=BACKEND, check=False)


def cmd_dev(args: argparse.Namespace) -> int:
    """API with hot reload on :8000, Vite console on :5173 proxying to it."""
    python = ensure_venv(quiet=True)
    ensure_dependencies(python)
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        die("dev mode needs Node 18+ for the Vite server.",
            f"Without it, use: {LAUNCHER} run --reload")

    state = database_state()
    reason = needs_seed(state)
    if reason:
        detail(f"Seeding because {reason}.")
        seed(python, cases=args.cases, months=args.months, reset=state["cases"] > 0)

    api = subprocess.Popen(
        [str(python), "-m", "uvicorn", "ksp_cip.interface.api.main:get_app",
         "--factory", "--reload", "--port", "8000"],
        cwd=str(BACKEND),
    )
    try:
        step("API      http://127.0.0.1:8000")
        step("Console  http://127.0.0.1:5173   (Ctrl-C to stop both)")
        run([npm, "run", "dev"], cwd=FRONTEND, check=False)
    finally:
        api.terminate()
        try:
            api.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - stubborn child
            api.kill()
    return 0


def cmd_test(args: argparse.Namespace) -> int:
    python = ensure_venv(quiet=True)
    ensure_dependencies(python)
    return run([str(python), "-m", "pytest", *args.pytest_args], cwd=BACKEND, check=False)


def cmd_package(args: argparse.Namespace) -> int:
    """Build the three Catalyst deployment artifacts.

    Catalyst ships only the directory it is given, and none of the three
    entrypoints is self-contained in the checkout: the API and the refresh
    function need the `ksp_cip` package, and the console needs frontend/dist.
    Each staged artifact self-verifies -- the Python ones by importing their
    own module set with sys.path limited to the staging directory, the console
    by checking its build is inside it.
    """
    python = ensure_venv(quiet=True)
    ensure_dependencies(python)
    if not ensure_console():
        die("The console must be built before the cip-console artifact can be staged.",
            f"Install Node 18+, then re-run: {LAUNCHER} package")

    builder = ROOT / "scripts" / "build_catalyst_artifact.py"
    known = ("api", "refresh", "console")
    targets = args.targets or list(known)
    unknown = [t for t in targets if t not in known]
    if unknown:
        die(f"Unknown package target(s): {', '.join(unknown)}", f"Valid targets: {', '.join(known)}")
    for target in targets:
        step(f"Staging {target}")
        run([str(python), str(builder), "--target", target])

    say()
    step("Artifacts staged in dist/. Deploy from there, not from catalyst/.")
    detail("catalyst appsail:add --name cip-api     --stack python_3_11 --source dist/cip-api "
           '--command "python3 -u server.py" --port 9000')
    detail("catalyst appsail:add --name cip-console --stack node18      --source dist/cip-console "
           '--command "node server.js" --port 9000')
    detail("Full sequence and the provisioning order: README section 9.")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Report what state this checkout is in, and what to run next.

    Never fails on a missing optional piece -- the point is to say plainly what
    is and is not present, which is the opposite of a check that stops at the
    first problem.
    """
    say("KSP-CIP - checkout status")
    say("=" * 60)

    ok = True
    next_step = None
    running = sys.version.split()[0]
    good_python = sys.version_info >= MIN_PYTHON
    say(f"  Python (this one)   {running} {'OK' if good_python else 'TOO OLD'}")

    venv_ready = VENV_PYTHON.exists()
    say(f"  Virtualenv          {'present' if venv_ready else 'MISSING'}  ({VENV})")
    deps_ready = venv_ready and dependencies_present(VENV_PYTHON)
    say(f"  Dependencies        {'installed' if deps_ready else 'NOT INSTALLED'}")
    if not (venv_ready and deps_ready):
        ok = False
        next_step = f"{LAUNCHER} setup"


    npm = shutil.which("npm") or shutil.which("npm.cmd")
    say(f"  Node / npm          {'found' if npm else 'not found (console unavailable)'}")
    say(f"  Console build       {'present' if console_is_built() else 'not built'}")

    state = database_state()
    path = database_path()
    say(f"  Database            {path}")
    if state["cases"] < 0:
        say("                      NOT CREATED")
        ok = False
    else:
        say(f"                      {state['cases']} cases, {state['users']} user accounts, "
            f"{state['socioeconomic']} socio-economic rows")
    reason = needs_seed(state)
    if reason:
        say(f"  Seed                INCOMPLETE - {reason}")
        ok = False
        # Naming the *right* next command matters most here: a half-seeded
        # database is not fixed by re-running setup, and the seed steps that
        # write demo accounts skip when their table already has rows -- so the
        # fix is a reset, which `python cip.py` does on its own.
        next_step = next_step or f"{LAUNCHER}        (it will re-seed)"
    else:
        say("  Seed                complete")

    # Anything below here needs the application itself, so it is only reachable
    # once the dependencies are in place.
    if deps_ready:
        say("")
        say("  Effective configuration")
        probe = subprocess.run(
            [str(VENV_PYTHON), "-m", "ksp_cip.cli", "config"],
            cwd=str(BACKEND), capture_output=True, text=True,
        )
        if probe.returncode == 0:
            say("    deployable: yes (no configuration problems)")
        else:
            say("    deployable: NO")
            for line in probe.stdout.splitlines():
                if '"problems"' in line or line.strip().startswith('"KSPCIP'):
                    say(f"    {line.strip()}")
            ok = False
            next_step = "python -m ksp_cip.cli config   (from backend/, to see every problem)"

    say("")
    say("=" * 60)
    if ok:
        say(f"Everything is ready.   Run:  {LAUNCHER}")
    else:
        say(f"Not ready yet.         Run:  {next_step or LAUNCHER + ' setup'}")
    say("")
    say("Catalyst deployment is a separate path and is NOT checked here -- it")
    say(f"needs a live project. Build its artifacts with: {LAUNCHER} package")
    return 0 if ok else 1


# ---------------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cip.py",
        description="KSP-CIP - one entry point for everything you can run locally.",
        epilog=(
            "With no command at all, cip.py installs what is missing, seeds the "
            "database if it needs it, and serves the platform."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    def seed_options(target: argparse.ArgumentParser) -> None:
        target.add_argument("--cases", type=int, default=DEFAULT_CASES)
        target.add_argument("--months", type=int, default=DEFAULT_MONTHS)

    setup = sub.add_parser("setup", help="install dependencies and build the console")
    setup.add_argument("--reinstall", action="store_true", help="reinstall even if present")
    setup.add_argument("--rebuild-console", action="store_true", help="rebuild even if present")
    setup.set_defaults(func=cmd_setup)

    seeder = sub.add_parser("seed", help="generate the synthetic dataset")
    seed_options(seeder)
    seeder.add_argument("--reset", action="store_true", help="delete existing rows first")
    seeder.set_defaults(func=cmd_seed)

    server = sub.add_parser("run", help="serve the API and console (the default)")
    seed_options(server)
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8000)
    server.add_argument("--reload", action="store_true", help="restart on code changes")
    server.add_argument("--no-console", action="store_true", help="skip the console build")
    server.set_defaults(func=cmd_run)

    dev = sub.add_parser("dev", help="hot reload: API on :8000, Vite console on :5173")
    seed_options(dev)
    dev.set_defaults(func=cmd_dev)

    # `test` is declared for --help only. Its arguments are pytest's, and they
    # are intercepted in main() before argparse ever sees them -- a leading
    # `-m "not slow"` binds to the wrong parser otherwise, and pytest flags
    # must reach pytest unmangled. Same reason scripts/test.ps1 has no param().
    tests = sub.add_parser("test", help="run the test suite (extra args go to pytest)")
    tests.set_defaults(func=cmd_test, pytest_args=[])

    package = sub.add_parser("package", help="build the Catalyst deployment artifacts")
    package.add_argument("targets", nargs="*", metavar="TARGET",
                         help="api, refresh and/or console (default: all three)")
    package.set_defaults(func=cmd_package)

    doctor = sub.add_parser("doctor", help="report what state this checkout is in")
    doctor.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()

    # Hand pytest its own flags untouched. `cip.py test -m "not slow"` would
    # otherwise fail on argparse binding `-m` before the subparser runs.
    if argv and argv[0] == "test":
        namespace = argparse.Namespace(func=cmd_test, pytest_args=argv[1:])
        return int(cmd_test(namespace))

    # No command means "do the whole thing": the single-command path this file
    # exists to provide.
    if not argv or argv[0].startswith("-") and argv[0] not in ("-h", "--help"):
        argv = ["run", *argv]
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):  # pragma: no cover - argparse covers this
        parser.print_help()
        return 1
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        say("")
        say("Stopped.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
