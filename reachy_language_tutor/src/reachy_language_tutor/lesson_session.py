"""The lesson that is running right now, and who it is running for.

Milestone 2 established that who the app is serving comes from application state and
never from the conversation: ToolDependencies seals current_learner_id, and no tool
accepts an identity parameter. The lesson being practised is the same kind of fact. If
the model can name the lesson, it can record a result against a lesson nobody ran --
a write path back in the model's hands, which is the boundary put back one layer up.

So the app holds the running lesson too. start_lesson pins it here; finish_lesson reads
it back. Neither takes a lesson id from the conversation, exactly as neither takes a
learner id -- and a holder takes no learner id either: it is BOUND to one learner when
the app builds it, beside the current_learner_id it was built from. open() therefore has
no identity parameter to pass one through, which is what makes "nothing reachable from
the conversation may write the holder for somebody else" a property of the code rather
than of the next author's discipline.

The distinction this module draws, and the reason it is a type rather than a variable:
a session is bound to the learner it was opened for. A household is several people
sharing one robot, and somebody sitting down after somebody else must not inherit the
previous person's lesson -- nor have their own result recorded against it. read_for is
the only way to read, and it will only hand a session back to the learner whose name is
on it. "Nothing is running" and "something is running for somebody else" are
deliberately the same answer: nothing.

Plain stdlib only, and nothing imported from this package. tools/core_tools.py imports
this module for real at module scope -- it has no `from __future__ import annotations`,
so its field annotations evaluate at import -- and an import back the other way would
be a cycle. Where a rule below matches one in learners/store.py, it is restated with
the store named rather than imported, because the store is private behind the learners
package boundary.
"""

from __future__ import annotations
import time
import logging
from dataclasses import dataclass


logger = logging.getLogger(__name__)

# What the column can represent at all. learners/store.py carries the same pair and is
# explicit that the bound is not optional: an out-of-range int passes isinstance and
# then raises OverflowError when SQLite binds it -- not sqlite3.Error, not ValueError,
# so nothing downstream catches it and it travels up through the conversation loop.
# Pinning a timestamp the store cannot store would move that raise to a later layer
# rather than prevent it.
_STORABLE_INT_MIN = -(2**63)
_STORABLE_INT_MAX = 2**63 - 1


class LessonSessionRefusedError(ValueError):
    """Raised when something tries to pin a lesson that is not fit to be pinned."""


@dataclass(frozen=True, repr=False)
class LessonSession:
    """One lesson a named learner has started and has not finished.

    Ids only. No display name, no lesson title: an id looks either of those up when
    something actually needs them, and carrying them here would drag personal data into
    every repr of every dependency bundle that holds the holder.
    """

    lesson_id: str
    language_code: str
    learner_id: str
    # Milliseconds since the epoch, the unit LessonAttempt.recorded_at uses -- a session
    # stamped in seconds would be 1000x off the attempt it eventually becomes.
    #
    # tools/record_result.py notes that nothing wall-clock may enter a tool's result
    # dict. A future finish_lesson reading this must not echo it to the model.
    opened_at: int

    def __repr__(self) -> str:
        """Describe the shape of this session and none of its values."""
        # A dataclass repr renders learner_id= and lesson_id=, and ToolDependencies is a
        # dataclass too, so repr(deps) would drag both out through the holder. One
        # logger.debug("%s", deps) one layer up is all it would take -- which is exactly
        # how a learner's name reached a log in D3. Shape, never value.
        return "LessonSession(running)"


class LessonSessionHolder:
    """The one mutable cell holding whichever lesson is running, owned by the app.

    Bound at construction to the learner it holds lessons for, and never afterwards.
    main.build_tool_dependencies builds it from the same resolved id it seals into
    ToolDependencies.current_learner_id, so the two cannot disagree; a holder built
    without one holds lessons for nobody and refuses to pin anything, which is the
    right answer when recognition has not identified anyone.

    Not a module-level global: it rides on ToolDependencies the way current_learner_id
    does, so a test can build one per case and two instances of the app cannot see each
    other's lesson.

    The public surface is open, read_for and clear, and deliberately nothing else. There
    must be no spelling that returns a session without naming a learner -- an ambient
    read is the shape that lets one household member's lesson be served to the next.
    """

    def __init__(self, learner_id: str | None = None) -> None:
        """Hold lessons for this learner, or for nobody when none is usable."""
        # Anything unusable becomes nobody rather than an error, and for the same
        # reason read_for answers "nothing" two ways: a holder that cannot name whose
        # lessons it holds must behave exactly like one that has nobody to hold them
        # for. Both refuse to pin, and neither guesses.
        self._learner_id: str | None = learner_id if _is_storable_id(learner_id) else None
        self._session: LessonSession | None = None

    def open(self, lesson_id: str, language_code: str, opened_at: int | None = None) -> LessonSession:
        """Pin a lesson as the one running, replacing any earlier one.

        There is deliberately no learner parameter. The learner is whoever this holder
        was built for, so no caller -- and therefore nothing the caller read out of a
        conversation -- can pin a lesson against a different household member.

        Last open wins: when somebody starts another lesson, the previous one is gone
        rather than lingering where a later read could find it.

        Raises LessonSessionRefusedError rather than returning None, because a caller
        must be able to tell "refused" from "nothing running" -- confusing the two is
        the failure this module exists to prevent. A refused open leaves whatever was
        already running untouched.
        """
        learner_id = self._learner_id
        if learner_id is None:
            raise LessonSessionRefusedError(_REFUSAL)

        # An allow-list: each id must BE a string the store can both accept and match,
        # and the timestamp must BE an integer the store can represent. Naming the
        # shapes that are permitted covers the whole family, which a list of rejected
        # shapes never does -- see CLAUDE.md, where inverting a deny-list is the fix
        # that closed four separate defects.
        if not _is_storable_id(lesson_id) or not _is_catalog_code(language_code):
            raise LessonSessionRefusedError(_REFUSAL)
        if opened_at is not None and not _is_storable_timestamp(opened_at):
            raise LessonSessionRefusedError(_REFUSAL)

        session = LessonSession(
            lesson_id=lesson_id,
            language_code=language_code,
            learner_id=learner_id,
            opened_at=_now_ms() if opened_at is None else opened_at,
        )
        self._session = session
        logger.debug("Pinned the running lesson for the learner this holder was built for")
        return session

    def read_for(self, learner_id: str | None) -> LessonSession | None:
        """Return the running lesson if it was opened for this learner, else nothing.

        Never raises. A tool asked to finish a lesson before anything started must be
        able to answer "nothing is running" without ending the turn.

        Exact equality, not a normalised comparison: current_learner_id comes from
        main.resolve_current_learner_id, which returns a seeded id or None and never a
        padded one, so widening the match would buy nothing and sell a near miss.
        """
        session = self._session
        if session is None:
            return None
        if not isinstance(learner_id, str) or learner_id != session.learner_id:
            # Worth an operator's attention: this is somebody being asked about, or
            # asking about, a lesson that is not theirs. The shape is the whole story;
            # the two ids are precisely what must not reach a log.
            logger.warning("Refused to hand the running lesson to a learner it was not opened for")
            return None
        return session

    def clear(self) -> None:
        """Forget whatever was running, and stay harmless when nothing was."""
        self._session = None
        logger.debug("Forgot the running lesson")

    def __repr__(self) -> str:
        """Describe whether a lesson is running, and nothing about which or whose."""
        return f"LessonSessionHolder({'running' if self._session is not None else 'empty'})"


# One constant, interpolating nothing. core_tools._dispatch_tool_call renders a tool's
# exception into {"error": f"{type(e).__name__}: {e}"} and hands that to the model, so a
# lesson id or a learner id in this text would be a value echoed straight to the LLM.
_REFUSAL = (
    "A lesson session needs a learner this holder was built for, a lesson and a lowercase language code -- "
    "each a string the store can store and match, with no padding, whitespace or control characters -- and "
    "an optional whole-number timestamp in range. Nothing was pinned and any lesson already running is "
    "untouched. The lesson being practised is application state, decided by the app rather than named in "
    "the conversation, which is what stops a result being recorded against a lesson nobody ran."
)


def _is_storable_id(value: object) -> bool:
    """Say whether this could name a row the store will later find again.

    The property, not a proxy for it. learners/store.py refuses a lesson id that is
    padded or carries whitespace or a control character, because such a value binds
    cleanly and then matches nothing -- coming back as a silent "no such lesson" that a
    caller cannot tell from a genuine absence. A guard must mean the same thing as the
    code it protects (D19): pinning something the store will later reject would move
    the failure to the layer that can no longer explain it.

    Refused rather than repaired, which is also the store's stated choice: stripping a
    caller's value invisibly leaves the layer above no signal that what it sent was
    malformed.
    """
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and not any(character.isspace() or ord(character) < 0x20 for character in value)
    )


def _is_catalog_code(value: object) -> bool:
    """Say whether this could name a language row in the catalog.

    Everything an id must be, and lowercase besides: the catalog's CHECK stores only
    lowercase codes, so "ES" binds cleanly and matches nothing exactly as " es" does.

    The catalog's 2-to-8 length bound is deliberately NOT restated here. It is a
    store-private constant, and a number copied across a package boundary is a number
    that drifts; the code is validated again by the store when it is actually used to
    look something up. What is duplicated here is the shape class -- padding, control
    characters, case -- which is a property rather than a figure.
    """
    return _is_storable_id(value) and isinstance(value, str) and value == value.lower()


def _is_storable_timestamp(value: object) -> bool:
    """Say whether this is a whole number the store could actually hold.

    bool is a subclass of int, so isinstance(True, int) is True and a bare isinstance
    check would pin True as a timestamp. learners/store.py makes this same exclusion,
    alongside the 64-bit bound above, and both halves belong together: fixing one and
    leaving its sibling is the single most repeated defect on this board.
    """
    return isinstance(value, int) and not isinstance(value, bool) and _STORABLE_INT_MIN <= value <= _STORABLE_INT_MAX


def _now_ms() -> int:
    """Return the current time in the unit recorded attempts use: epoch milliseconds."""
    # learners/store.utc_now_ms is int(time.time() * 1000), and is store-private behind
    # the learners package boundary -- so this matches it rather than importing it.
    return int(time.time() * 1000)
