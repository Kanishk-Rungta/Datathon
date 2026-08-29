"""Shared entrypoint bootstrap for every Catalyst-hosted component.

Both `catalyst/appsail/api/server.py` and `catalyst/functions/cip_refresh/main.py`
import this before touching `ksp_cip`, so a fix here reaches every entrypoint
at once rather than being copy-pasted and drifting.

**Two layouts, one file.** In the repo checkout, this file sits at
`catalyst/_bootstrap.py` and `ksp_cip` is two levels away at `backend/ksp_cip`.
In a built deployment artifact (see `scripts/build_catalyst_artifact.py`),
this file is copied to sit *directly next to* the entrypoint and `ksp_cip`, as
siblings, because P1-03 requires the artifact to be self-contained rather than
reaching back into the developer's working tree. ``locate_ksp_cip`` checks the
staged (sibling) layout first and falls back to the repo-relative layout only
so this same source file works unmodified for local testing.

Two runtime defects are also corrected here, found while implementing Phase 1:

1. **The old entrypoints set `KSPCIP_ENVIRONMENT=catalyst`.** That is not a
   member of the application's ``Environment`` enum (`local`, `development`,
   `staging`, `production`) — constructing ``Settings()`` would raise a
   validation error before the app even started. "Catalyst" is a *hosting
   platform*, not a deployment *environment*; the two are different axes and
   conflating them was the bug. This module derives a valid environment from
   ``KSPCIP_CATALYST_ENVIRONMENT`` (``Development``/``Production``, which
   Catalyst already asks a deployer to name) instead of inventing a new enum
   value the settings model does not know about.
2. Only a *default* is set, via ``setdefault``, on every variable — an
   operator who has explicitly exported ``KSPCIP_ENVIRONMENT`` (e.g. to
   ``staging``) is never overridden.
3. **The file store is defaulted alongside the data store.** Setting only
   ``KSPCIP_DATASTORE_BACKEND=catalyst`` produces a combination the settings
   validator rejects by design, so every first deploy died at startup on a
   configuration error rather than on anything the deployer did.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def locate_backend_root(entrypoint_dir: Path) -> Path:
    """Return the directory that contains an importable ``ksp_cip`` package.

    Checked in order: a ``ksp_cip`` sibling of the entrypoint (the staged
    artifact layout), then the repo-relative ``backend/`` (the checkout
    layout, for running entrypoints directly out of ``catalyst/`` during
    development).
    """
    staged = entrypoint_dir / "ksp_cip"
    if staged.is_dir():
        return entrypoint_dir

    # Walk up looking for a `backend/ksp_cip` — robust to an entrypoint that
    # moves a directory level without this file needing an update.
    for ancestor in [entrypoint_dir, *entrypoint_dir.parents]:
        candidate = ancestor / "backend"
        if (candidate / "ksp_cip").is_dir():
            return candidate

    raise RuntimeError(
        f"Could not locate an importable 'ksp_cip' package from {entrypoint_dir}. "
        "Expected either a 'ksp_cip' sibling (staged artifact) or a 'backend/ksp_cip' "
        "ancestor (repo checkout)."
    )


def bootstrap(entrypoint_file: str) -> None:
    entrypoint_dir = Path(entrypoint_file).resolve().parent
    backend_root = locate_backend_root(entrypoint_dir)
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))

    os.environ.setdefault("KSPCIP_DATASTORE_BACKEND", "catalyst")
    # The two are a pair, not independent, once the data store is Catalyst:
    # `Settings.deployment_problems()` refuses `datastore=catalyst` with
    # `filestore=local` outright, because an export written to a function's
    # own disk is gone at the next cold start and the audit row would then
    # cite a file nobody can fetch. Defaulting only the data store therefore
    # guaranteed that the *first* deploy of every component failed at startup
    # on a configuration error the deployer had no way to anticipate.
    # `setdefault` again: an operator who has stated a filestore keeps it.
    os.environ.setdefault("KSPCIP_FILESTORE_BACKEND", "catalyst")

    catalyst_env = os.environ.get("KSPCIP_CATALYST_ENVIRONMENT", "Development")
    inferred = "production" if catalyst_env.strip().lower() == "production" else "development"
    os.environ.setdefault("KSPCIP_ENVIRONMENT", inferred)
