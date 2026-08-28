"""The payload-type contract between the agents and the console.

``StructuredPayload.payload_type`` is a closed ``Literal``. That is deliberate
— a typo should fail at the agent rather than render as a blank panel — but it
has a sharp edge: a type the agents emit and the ``Literal`` rejects raises
``ValidationError`` and turns the whole answer into a 500.

That is not hypothetical. ``socioeconomic_correlation`` was added to the agent
and to ``PayloadView.jsx`` but not to the ``Literal``, so every
``SOCIOECONOMIC_QUERY`` turn crashed. These tests read the agents and the
React renderer as source data and hold all three in step.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

import pytest

from ksp_cip.domain.models import StructuredPayload

BACKEND_ROOT = Path(__file__).resolve().parents[2]
AGENTS_DIR = BACKEND_ROOT / "ksp_cip" / "application" / "agents"
PAYLOAD_VIEW = BACKEND_ROOT.parent / "frontend" / "src" / "components" / "PayloadView.jsx"

ALLOWED = set(get_args(StructuredPayload.model_fields["payload_type"].annotation))


def emitted_payload_types() -> set[str]:
    """Every literal payload_type the five agents construct."""
    pattern = re.compile(r'payload_type\s*=\s*"([a-z_]+)"')
    found: set[str] = set()
    for path in AGENTS_DIR.glob("*.py"):
        found.update(pattern.findall(path.read_text(encoding="utf-8")))
    return found


def rendered_payload_types() -> set[str]:
    """Every payload_type ``PayloadView.jsx`` has a case for."""
    if not PAYLOAD_VIEW.exists():  # pragma: no cover - frontend not checked out
        pytest.skip("frontend/src not present")
    pattern = re.compile(r"case\s+'([a-z_]+)'")
    return set(pattern.findall(PAYLOAD_VIEW.read_text(encoding="utf-8")))


class TestPayloadTypeContract:
    def test_every_type_an_agent_emits_is_accepted_by_the_model(self):
        """The regression itself: emitting an unlisted type raises, not degrades."""
        unlisted = emitted_payload_types() - ALLOWED
        assert not unlisted, (
            f"agents emit payload types the StructuredPayload Literal rejects: {sorted(unlisted)}. "
            "Every such answer raises ValidationError and returns 500."
        )

    def test_every_type_an_agent_emits_actually_constructs(self):
        for payload_type in sorted(emitted_payload_types()):
            StructuredPayload(payload_type=payload_type, title="t", data={})

    def test_every_type_an_agent_emits_has_a_renderer(self):
        """A payload the console cannot draw is a blank panel for the officer."""
        missing = emitted_payload_types() - rendered_payload_types()
        assert not missing, (
            f"agents emit payload types PayloadView.jsx has no case for: {sorted(missing)}"
        )

    def test_an_unknown_type_is_still_refused(self):
        """Widening the Literal must not turn it into a free-text field."""
        with pytest.raises(Exception):
            StructuredPayload(payload_type="not_a_real_renderer", title="t", data={})

    def test_the_default_is_the_inert_type(self):
        assert StructuredPayload().payload_type == "none"
