"""Follow-up context: a continuation carries the previous question's frame.

"Show me theft cases in Mysuru" → "What about Bengaluru instead" should stay a
breakdown of *theft*, in a new place — not be re-read from the fragment alone,
which would lose the crime type and (worse) treat "Bengaluru" as an accused.
``resolve_coreference`` is pure over its arguments, so it is tested directly.
"""

from __future__ import annotations

from ksp_cip.application.services.memory import MemoryService, SessionContext
from ksp_cip.domain.enums import Intent
from ksp_cip.domain.models import Slots


def resolver() -> MemoryService:
    # resolve_coreference touches no I/O dependency, so None is fine here.
    return MemoryService(conversations=None, kv=None)  # type: ignore[arg-type]


def context_after_theft_in_mysuru() -> SessionContext:
    return SessionContext(
        session_id="s", user_id="u",
        pinned_district_ids=[2922],
        pinned_crime_sub_head_ids=[201, 202, 203],
        pinned_crime_types=["House Theft", "Motor Vehicle Theft", "Other Theft"],
        last_intent=Intent.LOOKUP_LOCATION,
    )


class TestContinuation:
    def test_a_continuation_carries_the_crime_frame(self):
        slots, notes, override = resolver().resolve_coreference(
            "What about Bengaluru instead", Slots(), context_after_theft_in_mysuru()
        )
        assert slots.crime_sub_head_ids == [201, 202, 203]
        assert override is Intent.LOOKUP_LOCATION
        assert any("crime type" in note.lower() for note in notes)

    def test_a_continuation_does_not_overwrite_an_explicit_new_crime(self):
        """"what about murder" changes the crime — the pin must not clobber it."""
        explicit = Slots(crime_sub_head_ids=[101], crime_types=["Murder"])
        slots, _notes, _override = resolver().resolve_coreference(
            "What about murder instead", explicit, context_after_theft_in_mysuru()
        )
        assert slots.crime_sub_head_ids == [101]

    def test_a_fresh_question_is_not_treated_as_a_continuation(self):
        slots, _notes, override = resolver().resolve_coreference(
            "Show me hotspots", Slots(), context_after_theft_in_mysuru()
        )
        assert override is None
        assert slots.crime_sub_head_ids == []

    def test_no_override_without_a_previous_intent(self):
        slots, _notes, override = resolver().resolve_coreference(
            "What about Bengaluru", Slots(), SessionContext(session_id="s", user_id="u")
        )
        assert override is None
