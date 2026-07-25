"""Catalyst AdvancedIO entry point for the API.

This file contains no application logic on purpose. It imports the same ASGI
application that `uvicorn` serves locally, so a behaviour that works in
development cannot diverge in deployment through a second implementation.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault("KSPCIP_DATASTORE_BACKEND", "catalyst")
os.environ.setdefault("KSPCIP_ENVIRONMENT", "catalyst")

from ksp_cip.interface.api.main import get_app  # noqa: E402

app = get_app()


def handler(context, basicio):  # pragma: no cover - Catalyst runtime shim
    """AdvancedIO handler. Catalyst supplies an ASGI-compatible bridge."""
    return app
