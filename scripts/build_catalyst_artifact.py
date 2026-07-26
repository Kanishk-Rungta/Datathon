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
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_KSP_CIP = REPO_ROOT / "backend" / "ksp_cip"
BOOTSTRAP_SRC = REPO_ROOT / "catalyst" / "_bootstrap.py"

TARGETS = {
    "api": {
        "source_dir": REPO_ROOT / "catalyst" / "appsail" / "api",
        "entrypoint": "server.py",
        "extra_files": ["app-config.json", "requirements.txt"],
    },
    "refresh": {
        "source_dir": REPO_ROOT / "catalyst" / "functions" / "cip_refresh",
        "entrypoint": "main.py",
        "extra_files": ["catalyst-config.json", "requirements.txt"],
    },
}

#: Never staged, regardless of what's in the source tree at build time.
EXCLUDE_DIR_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo"}


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
    ok = compileall.compile_dir(str(staging), quiet=1, force=True)
    if not ok:
        raise SystemExit("Self-containment check FAILED — one or more staged files do not compile.")


def build(target: str, output: Path, *, python_executable: str, check_only: bool) -> None:
    spec = TARGETS[target]
    source_dir: Path = spec["source_dir"]

    if not check_only:
        if output.exists():
            shutil.rmtree(output)
        output.mkdir(parents=True)

        copy_package(BACKEND_KSP_CIP, output / "ksp_cip")
        shutil.copy2(BOOTSTRAP_SRC, output / "_bootstrap.py")
        shutil.copy2(source_dir / spec["entrypoint"], output / spec["entrypoint"])
        for extra in spec["extra_files"]:
            src = source_dir / extra
            if src.exists():
                shutil.copy2(src, output / extra)

    verify_compiles(output)
    verify_self_contained(output, python_executable)

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
