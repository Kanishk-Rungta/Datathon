"""Catalyst AppSail entrypoint for the API.

Runs the same ASGI application `uvicorn` serves locally — `get_app()` is the
only application factory anywhere in this codebase, so a behaviour that works
in development cannot diverge in deployment through a second implementation.

This file replaces a prior Advanced I/O function whose handler
(`def handler(context, basicio): return app`) returned the ASGI app object to
a runtime that has no documented way to invoke one. See
`docs/deployment/catalyst-runtime.md` for what was checked and why AppSail —
not Advanced I/O — is the target here.

AppSail's only two requirements for a Python service, per Zoho's own examples
(Flask/Bottle/Tornado): bind `0.0.0.0`, and read the port from
`X_ZOHO_CATALYST_LISTEN_PORT` rather than hardcoding one.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
# `_bootstrap.py` sits alongside this file once staged (see
# scripts/build_catalyst_artifact.py); in the repo checkout it lives two
# levels up at `catalyst/_bootstrap.py`. Try the staged layout first.
sys.path.insert(0, str(_HERE))
try:
    from _bootstrap import bootstrap
except ImportError:
    sys.path.insert(0, str(_HERE.parents[1]))
    from _bootstrap import bootstrap

bootstrap(__file__)

import uvicorn  # noqa: E402

from ksp_cip.interface.api.main import get_app  # noqa: E402

app = get_app()

if __name__ == "__main__":
    port = int(os.environ.get("X_ZOHO_CATALYST_LISTEN_PORT", 9000))
    uvicorn.run(app, host="0.0.0.0", port=port, log_config=None)
