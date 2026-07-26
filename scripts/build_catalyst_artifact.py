#!/usr/bin/env python3
"""Build a self-contained Catalyst deployment artifact.

Implements P1-03 of ``implementationv2-phases-0-2.md``. The problem this
solves: the original entrypoints computed a path to a repository-level
``backend`` folder. That is valid when run from a checkout, but it is not
proof that Catalyst's deployment upload includes that folder — "source":
"appsail/api" in ``catalyst.json`` only names the directory Catalyst zips and
ships; it says nothing about the rest of the repository.

This script builds a staging directory per artifact that contains everything
the entrypoint needs and nothing that reaches back into the checkout, then
verifies that by importing the staged package with a sys.path that contains
*only* the staging directory — not the repository.

Usage:
    python scripts/build_catalyst_artifact.py --target api
    python scripts/build_catalyst_artifact.py --target refresh --output dist/cip-refresh
    python scripts/build_catalyst_artifact.py --target api --output dist/cip-api --check-only
"""

from __future__ import annotations

import argparse
import compileall
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_KSP_CIP = REPO_ROOT / "backend" / "ksp_cip"
BOOTSTRAP_SRC = REPO_ROOT / "catalyst" / "_bootstrap.py"

FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"

TARGETS = {
    "api": {
        "language": "python",
        "source_dir": REPO_ROOT / "catalyst" / "appsail" / "api",
        "entrypoint": "server.py",
        "extra_files": ["app-config.json", "requirements.txt"],
        # Imported by name before any application code runs. Missing vendored
        # copies of these are what made the first live deploy crash.
        "vendor_required": ("uvicorn", "fastapi", "pydantic", "pydantic_settings"),
    },
    "refresh": {
        "language": "python",
        "source_dir": REPO_ROOT / "catalyst" / "functions" / "cip_refresh",
        "entrypoint": "main.py",
        "extra_files": ["catalyst-config.json", "requirements.txt"],
        # Deliberately no fastapi/uvicorn: this function never imports
        # ksp_cip.interface.api, which is why its requirements.txt is shorter
        # than the API's.
        "vendor_required": ("pydantic", "pydantic_settings", "numpy", "networkx"),
    },
    "console": {
        # Node, not Python: no ksp_cip package, no bootstrap, no compileall/
        # import self-containment check -- see verify_self_contained's guard.
        # What it needs staged instead is the built React bundle, since
        # server.js's own repo-relative fallback (`../../../frontend/dist`)
        # only resolves when running out of a full checkout, not a deployed
        # AppSail source zip. See server.js's STAGED_DIST/CHECKOUT_DIST split.
        "language": "node",
        "source_dir": REPO_ROOT / "catalyst" / "appsail" / "console",
        "entrypoint": "server.js",
        "extra_files": ["app-config.json", "package.json"],
    },
}

#: Never staged, regardless of what's in the source tree at build time.
EXCLUDE_DIR_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo"}

#: The runtime the artifact will actually execute on, which is not the machine
#: this script runs on. AppSail's `python_3_11` stack is CPython 3.11 on
#: x86-64 Linux; building on Windows without pinning these would vendor
#: win_amd64 wheels that cannot load there.
VENDOR_PYTHON_VERSION = "3.11"
VENDOR_PLATFORM = "manylinux2014_x86_64"


def copy_package(src: Path, dst: Path) -> None:
    def ignore(_dir: str, names: list[str]) -> set[str]:
        return {
            name for name in names
            if name in EXCLUDE_DIR_NAMES or Path(name).suffix in EXCLUDE_SUFFIXES
        }

    shutil.copytree(src, dst, ignore=ignore)


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def build_manifest(staging: Path) -> dict[str, object]:
    files = sorted(p for p in staging.rglob("*") if p.is_file())
    return {
        "file_count": len(files),
        "total_bytes": sum(p.stat().st_size for p in files),
        "files": {
            str(p.relative_to(staging)).replace("\\", "/"): {
                "sha256": hash_file(p),
                "bytes": p.stat().st_size,
            }
            for p in files
        },
    }


def verify_self_contained(staging: Path, python_executable: str) -> None:
    """Import ``ksp_cip`` with sys.path containing only the staging dir.

    ``-I`` (isolated mode) additionally strips the environment's own
    ``PYTHONPATH`` and user site-packages, so a pass here is not an accident
    of the calling shell's environment.
    """
    # `-I` already strips PYTHONPATH, user site-packages, and cwd-on-path, so
    # the standard library is still reachable through the base interpreter's
    # own entries -- only the staging directory needs adding, at the front,
    # so nothing else on this specific path could satisfy the import instead.
    script = (
        "import sys; "
        f"sys.path.insert(0, {str(staging)!r}); "
        "import ksp_cip; "
        "import ksp_cip.interface.container; "
        "import ksp_cip.interface.api.main; "
        "print('OK: ksp_cip imports with sys.path limited to the staging directory')"
    )
    result = subprocess.run(
        [python_executable, "-I", "-c", script],
        capture_output=True, text=True, cwd=str(staging),
    )
    if result.returncode != 0:
        raise SystemExit(
            "Self-containment check FAILED — the staged artifact could not import "
            "ksp_cip using only its own directory:\n" + result.stderr
        )
    print(result.stdout.strip())


def verify_compiles(staging: Path) -> None:
    # `vendor/` is excluded on purpose: those are third-party wheels built for
    # CPython 3.11, compiled by this script's interpreter (often a different
    # version). Compiling them here proves nothing about our code, writes
    # throwaway .pyc bulk into the artifact, and fails on any file the build
    # interpreter's parser rejects.
    ok = compileall.compile_dir(
        str(staging), quiet=1, force=True, rx=re.compile(r"[/\\]vendor[/\\]")
    )
    if not ok:
        raise SystemExit("Self-containment check FAILED — one or more staged files do not compile.")


def vendor_dependencies(requirements: Path, target: Path, python_executable: str) -> None:
    """Install requirements.txt into ``target`` as Linux/CPython-3.11 wheels.

    AppSail ships the source directory as-is and does **not** run
    `pip install -r requirements.txt` server-side (confirmed the hard way:
    the first live deploy crashed with `ModuleNotFoundError: No module named
    'uvicorn'`). Dependencies therefore have to be in the artifact.

    ``--only-binary=:all:`` is deliberate rather than convenient: with a
    cross-platform ``--platform`` pin, pip cannot build an sdist for the
    target, so allowing sdists would silently produce a package built for the
    *build* machine. Failing loudly on a package with no manylinux wheel is
    the correct outcome.
    """
    if not requirements.exists():
        return
    print(f"Vendoring {requirements.name} -> {target} "
          f"(py{VENDOR_PYTHON_VERSION}, {VENDOR_PLATFORM})")
    result = subprocess.run(
        [
            python_executable, "-m", "pip", "install",
            "-r", str(requirements),
            "-t", str(target),
            "--platform", VENDOR_PLATFORM,
            "--python-version", VENDOR_PYTHON_VERSION,
            "--only-binary=:all:",
            # pip otherwise byte-compiles with the *build* interpreter, which
            # is not the 3.11 the artifact runs on -- those .pyc files are
            # both useless there and pure upload weight.
            "--no-compile",
            "--quiet",
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise SystemExit(
            "Dependency vendoring FAILED — could not resolve every requirement "
            f"as a {VENDOR_PLATFORM} / CPython {VENDOR_PYTHON_VERSION} wheel:\n"
            + result.stderr
        )


def verify_vendored(staging: Path, required_top_level: tuple[str, ...]) -> None:
    """Check the vendored tree by inspection, not by importing it.

    These are Linux/CPython-3.11 wheels; the build machine is generally
    neither, so importing them here would fail for reasons that say nothing
    about the artifact. Existence and wheel-tag checks are what is actually
    verifiable at build time — the real import happens on AppSail.
    """
    vendor = staging / "vendor"
    if not vendor.is_dir():
        raise SystemExit(f"Vendor check FAILED — {vendor} does not exist.")

    missing = [
        name for name in required_top_level
        if not (vendor / name).is_dir() and not (vendor / f"{name}.py").exists()
    ]
    if missing:
        raise SystemExit(
            f"Vendor check FAILED — {missing} not present in {vendor}. "
            "The artifact would crash at import time on AppSail."
        )

    # A win_amd64/macosx binary here means the --platform pin silently did not
    # apply, which produces a module that exists but cannot load on AppSail --
    # a failure mode that looks like success until deployment.
    wrong_platform = sorted({
        so.name for so in vendor.rglob("*.so")
        if "linux" not in so.name and so.name.count(".") > 1
    } | {p.name for p in vendor.rglob("*.pyd")})
    if wrong_platform:
        raise SystemExit(
            "Vendor check FAILED — non-Linux binary extension(s) found: "
            f"{wrong_platform[:5]}. Expected {VENDOR_PLATFORM} wheels only."
        )

    total = sum(p.stat().st_size for p in vendor.rglob("*") if p.is_file())
    print(f"OK: vendored dependencies present ({total / 1_048_576:.1f} MiB, "
          f"{VENDOR_PLATFORM}/py{VENDOR_PYTHON_VERSION})")


#: Operator-supplied deployment settings, merged into the staged
#: app-config.json at build time. Gitignored: it holds OAuth credentials.
DEPLOY_ENV_FILE = REPO_ROOT / "catalyst" / "deploy.env"


def inject_deploy_secrets(app_config_path: Path) -> None:
    """Merge ``catalyst/deploy.env`` into the staged app-config's env_variables.

    `catalyst deploy` pushes whatever `env_variables` the artifact declares,
    which means values set by hand in the Catalyst console are overwritten on
    the next deploy. Rather than ask an operator to re-enter secrets after
    every release, they live in one gitignored file on the deployment machine
    and are injected here -- so the artifact is the single source of truth and
    the committed app-config.json never contains a credential.
    """
    if not DEPLOY_ENV_FILE.exists():
        print(f"note: {DEPLOY_ENV_FILE.name} not found -- staging app-config.json unchanged. "
              "Catalyst-backed deployment needs it; see docs/deployment/v3-phase-d3-runtime.md.")
        return

    overrides: dict[str, str] = {}
    # utf-8-sig, not utf-8: PowerShell's `Out-File -Encoding utf8` writes a
    # BOM, which would otherwise make the first key `﻿KSPCIP_...` -- a
    # different key that silently fails to override anything.
    for raw in DEPLOY_ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        overrides[key.strip()] = value.strip().strip('"').strip("'")

    config = json.loads(app_config_path.read_text(encoding="utf-8"))
    env = dict(config.get("env_variables") or {})
    env.update(overrides)
    config["env_variables"] = env
    app_config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    # Names only -- printing a value here would defeat the point of the file.
    print(f"Injected {len(overrides)} deployment variable(s) from {DEPLOY_ENV_FILE.name}: "
          f"{', '.join(sorted(overrides))}")


SECRET_PATTERNS = ("api_key", "secret", "refresh_token", "oauth")


def verify_console_bundle(staging: Path) -> None:
    index_html = staging / "dist" / "index.html"
    if not index_html.exists():
        raise SystemExit(f"Self-containment check FAILED — {index_html} is missing.")
    hits: list[str] = []
    for path in (staging / "dist").rglob("*"):
        if path.is_file() and path.suffix in {".js", ".css", ".html"}:
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
            # React ships its own internal marker literally named this; it is
            # not a secret, just an oddly-chosen symbol name upstream.
            text = text.replace("__secret_internals_do_not_use_or_you_will_be_fired", "")
            hits.extend(pattern for pattern in SECRET_PATTERNS if pattern in text)
    if hits:
        raise SystemExit(
            "Self-containment check FAILED — suspicious pattern(s) "
            f"{sorted(set(hits))} found in the built console bundle."
        )
    print("OK: console bundle has dist/index.html and no secret-like patterns")


def build(target: str, output: Path, *, python_executable: str, check_only: bool) -> None:
    spec = TARGETS[target]
    source_dir: Path = spec["source_dir"]
    is_python = spec.get("language", "python") == "python"

    if not check_only:
        if output.exists():
            shutil.rmtree(output)
        output.mkdir(parents=True)

        if is_python:
            copy_package(BACKEND_KSP_CIP, output / "ksp_cip")
            shutil.copy2(BOOTSTRAP_SRC, output / "_bootstrap.py")
            vendor_dependencies(
                source_dir / "requirements.txt", output / "vendor", python_executable
            )
        else:
            if not FRONTEND_DIST.exists():
                raise SystemExit(
                    f"{FRONTEND_DIST} does not exist -- run `npm ci && npm run build` "
                    "in frontend/ before staging the console artifact."
                )
            copy_package(FRONTEND_DIST, output / "dist")
        shutil.copy2(source_dir / spec["entrypoint"], output / spec["entrypoint"])
        for extra in spec["extra_files"]:
            src = source_dir / extra
            if src.exists():
                shutil.copy2(src, output / extra)

        if (output / "app-config.json").exists():
            inject_deploy_secrets(output / "app-config.json")

    if is_python:
        verify_compiles(output)
        verify_self_contained(output, python_executable)
        verify_vendored(output, spec["vendor_required"])
    else:
        verify_console_bundle(output)

    manifest = build_manifest(output)
    manifest_path = output.parent / f"{output.name}.manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Staged '{target}' at {output} — {manifest['file_count']} files, "
          f"{manifest['total_bytes']:,} bytes. Manifest: {manifest_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", choices=sorted(TARGETS), required=True)
    parser.add_argument("--output", type=Path, default=None,
                        help="Staging directory (default: dist/cip-<target>)")
    parser.add_argument("--python", default=sys.executable,
                        help="Python executable used for the self-containment check")
    parser.add_argument("--check-only", action="store_true",
                        help="Re-verify an already-built staging directory without re-copying")
    args = parser.parse_args()

    output = args.output or (REPO_ROOT / "dist" / f"cip-{args.target}")
    build(args.target, output, python_executable=args.python, check_only=args.check_only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
