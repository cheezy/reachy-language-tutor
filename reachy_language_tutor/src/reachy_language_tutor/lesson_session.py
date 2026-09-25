"""The lesson that is running right now, and who it is running for.

Milestone 2 established that who the app is serving comes from application state and
never from the conversation: ToolDependencies seals current_learner_id, and no tool
accepts an identity parameter. The lesson being practised is the same kind of fact. If
the model can name the lesson, it can record a result against a lesson nobody ran --
a write path back in the model's hands, which is the boundary put back one layer up.

So the app holds the running lesson too. start_lesson (or redo_lesson) pins it here; finish_lesson reads
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
import re
import time
import logging
from typing import Iterable
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

# The fraction of a lesson's own lines that must have been said out loud before the app
# will believe the lesson was worked through.
#
# This is a FLOOR AGAINST A LESSON THAT DID NOT HAPPEN, not a standard of mastery. The
# case it exists for was measured: a learner failed the first of fifteen turns five
# times and then said "we're done, I completed it", and the lesson was recorded
# completed on that sentence alone. Note what does NOT distinguish that session --
# it ran two minutes and three seconds across twenty conversation turns, so a gate on
# elapsed time or on how much was said would have passed it. Only coverage of the
# material tells the two apart.
#
# Set low on purpose. Blocking a real completion is a worse failure than admitting a
# thin one (the task naming this defect says so outright), so a third of the lines is
# enough to believe a lesson happened, and no claim is made that a third is enough to
# have learned it.
_WORKED_THROUGH_FRACTION = 1 / 3

# And the learner has to have been there. Coverage alone is the tutor's own output, so
# a tutor that recites a lesson at a silent child would clear it -- which is the very
# thing the security note on this defect warned about: an outcome a conversation can
# talk its way around is the same defect wearing a gate. A lesson is a conversation, so
# "worked through" needs both halves: enough of the material said, and the person it
# was said to answering back.
#
# Low, and lower than the line floor, because a quiet learner is still a learner and
# blocking a real completion is the worse failure. It exists to separate a practice
# from a broadcast, not to grade how talkative somebody was.
_LEARNER_TURNS_PER_LINE_FLOOR = 1 / 3
_MIN_LEARNER_TURNS = 3

# What SURVIVES the reduction to a comparable form: letters, digits, and the spaces
# between them. Everything else becomes a space.
#
# Stated as what is permitted rather than as a list of punctuation to strip, which is
# the rule CLAUDE.md records getting wrong four separate times in this repository. A
# deny-list here is only as complete as the last person to read it: guillemets, an
# ordinal indicator, a slash or an ampersand in some future lesson would normalise one
# way on the printed side and another on the spoken side, silently under-count coverage,
# and push a real completion towards the downgrade this task calls the worse failure.
# str.isalnum() is the allow-list, and it covers accented letters by construction.
#
# Accents are therefore KEPT -- they are letters. A tutor that says "manha" for "manhã"
# has not said the line, and in a course typed off page images precisely to preserve
# those marks, that is the distinction worth enforcing rather than folding away.


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
    # tools/finish_lesson.py notes that nothing wall-clock may enter a tool's result
    # dict -- the boundary suite calls a tool twice and demands identical answers -- so
    # a caller reading this must not echo it to the model.
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
        # What the running lesson is made of, and which of it has been said out loud.
        # Held on the HOLDER rather than on the session because LessonSession is frozen
        # -- and because this is the mutable cell by design, so there is exactly one
        # place a lesson's state lives. Both are replaced by open() and emptied by
        # clear(), so coverage can never outlive the lesson it describes and be read
        # against the next one.
        self._teachable: tuple[str, ...] = ()
        # The same lines, longest first -- the order note_spoken consumes them in. Kept
        # beside _teachable rather than instead of it so coverage_for and the floor keep
        # reading one plain tuple.
        self._longest_first: tuple[str, ...] = ()
        self._spoken: set[str] = set()
        self._learner_turns: int = 0

    def open(
        self,
        lesson_id: str,
        language_code: str,
        opened_at: int | None = None,
        teachable_lines: Iterable[str] | None = None,
    ) -> LessonSession:
        """Pin a lesson as the one running, replacing any earlier one.

        There is deliberately no learner parameter. The learner is whoever this holder
        was built for, so no caller -- and therefore nothing the caller read out of a
        conversation -- can pin a lesson against a different household member.

        Last open wins: when somebody starts ANOTHER lesson, the previous one is gone
        rather than lingering where a later read could find it. Opening the lesson that
        is ALREADY running is not another lesson, and it changes nothing: the running
        session and everything noted against it stand. Without that, a model calling
        start_lesson a second time mid-lesson erased every line already taught, and a
        lesson worked through end to end was then downgraded to partial on finishing.

        teachable_lines has two meanings and they are deliberately not the same:

        * None -- the caller supplied no lines, so coverage cannot be measured and
          worked_through_for answers None. This is the fallback for a caller that does
          not know the lesson's content; no tool in this package takes it, and a test
          scans the package to keep it that way.
        * Anything else -- the caller DID read the lesson, and these are its lines. If
          none survives normalisation the lesson has nothing to teach, and pinning it
          would make its completion unmeasurable -- so the pin is refused. That is the
          rule start_lesson's gate applies, stated here once so that every tool that
          pins a lesson inherits it rather than each remembering to (redo_lesson did
          not, and reopened an empty lesson whose completion then went unchecked).

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
        if not _is_lesson_id(lesson_id) or not _is_catalog_code(language_code):
            raise LessonSessionRefusedError(_REFUSAL)
        if opened_at is not None and not _is_storable_timestamp(opened_at):
            raise LessonSessionRefusedError(_REFUSAL)

        teachable: tuple[str, ...] = ()
        if teachable_lines is not None:
            teachable = tuple(dict.fromkeys(filter(None, (_for_matching(line) for line in teachable_lines))))
            if not teachable:
                raise LessonSessionRefusedError(_REFUSAL)

        running = self._session
        if running is not None and running.lesson_id == lesson_id and running.language_code == language_code:
            # The same lesson, for the same learner (the holder is bound to one). Keep
            # it, and keep what has been said in it. Deliberately checked AFTER the
            # argument checks, so a malformed re-open is still refused rather than
            # waved through on the strength of matching something already pinned.
            logger.debug("Kept the running lesson, which was opened again")
            return running

        session = LessonSession(
            lesson_id=lesson_id,
            language_code=language_code,
            learner_id=learner_id,
            opened_at=_now_ms() if opened_at is None else opened_at,
        )
        self._session = session
        # The lines this lesson is made of, normalised once here so every later
        # comparison is a set lookup rather than a scan. A caller that passes none
        # leaves coverage unmeasurable, which worked_through_for reports as "unknown"
        # rather than as "not worked through" -- see that method for why the difference
        # has to survive.
        self._teachable = teachable
        self._longest_first = tuple(sorted(teachable, key=len, reverse=True))
        self._spoken = set()
        self._learner_turns = 0
        # One constant and no interpolation, like every other log line in this module.
        # A count looks harmless, but the rule here is absolute precisely so that nobody
        # has to adjudicate which values are safe -- a test parses this file and enforces
        # it. The counts an operator needs are logged by finish_lesson instead.
        logger.debug("Pinned the running lesson and the lines to watch for")
        return session

    def note_spoken(self, learner_id: str | None, text: object) -> None:
        """Record which of the running lesson's own lines appear in something said.

        Called for every final assistant transcript, so it is on the conversation's hot
        path and does nothing but set membership. It NEVER stores, returns or logs the
        text it is handed: what survives this call is a count of which printed lines
        have been reached, and that is the whole point -- the evidence a lesson really
        happened has to be app state rather than the model's own account of itself,
        and it has to be shape rather than words to stay inside the logging rule.

        Takes the learner for the same reason read_for does. Marking coverage for
        whoever happens to be holding the robot would let one household member's
        practice count towards another's lesson.
        """
        if not self._teachable or not isinstance(text, str):
            return
        session = self._session
        if session is None or not isinstance(learner_id, str) or learner_id != session.learner_id:
            return
        spoken = _for_matching(text)
        if not spoken:
            return
        # Padded on both sides so a match has to fall on word boundaries. Unpadded
        # containment marked "uno" as said inside "un desayuno".
        #
        # And each spoken word is evidence for ONE line, never several. Word boundaries
        # alone did not stop one utterance ticking off many lines, because drills are
        # drawn from the dialogue: saying one Portuguese dialogue turn also "said" up to
        # eight single-word drills lifted out of it, and two turns of a twelve-turn
        # lesson cleared the floor. So lines are matched longest first, and the words a
        # match used are taken out of the utterance before shorter lines are looked for.
        # A drill lifted from a turn therefore counts only when it is said on its own --
        # which is what drilling it is. Already-spoken lines consume their words too,
        # or repeating a turn would tick off the drills inside it the second time.
        remaining = f" {spoken} "
        for line in self._longest_first:
            padded_line = f" {line} "
            if padded_line in remaining:
                self._spoken.add(line)
            # "|" can never be part of a line: _for_matching keeps only letters, digits
            # and single spaces, so the gap it leaves matches nothing. One occurrence at
            # a time, because two back-to-back occurrences share the space between them
            # and a single replace() pass would leave the second one standing.
            while padded_line in remaining:
                remaining = remaining.replace(padded_line, " | ", 1)

    def note_learner_turn(self, learner_id: str | None) -> None:
        """Record that the learner said something while this lesson was running.

        The count, never the words: nothing about what they said is passed in, because
        nothing about it is needed. Coverage says the material was presented; this says
        somebody was there to receive it, and a completion needs both.
        """
        session = self._session
        if session is None or not isinstance(learner_id, str) or learner_id != session.learner_id:
            return
        # Only once some of the lesson has actually been said. Counted from the moment
        # the lesson was pinned, this certified that somebody spoke at SOME point rather
        # than that they took part in anything: four remarks before a word of the lesson
        # was taught, followed by the tutor reciting it, cleared the gate.
        #
        # What this does NOT do, and it should be said rather than implied: it cannot
        # tell a reply from a refusal. A learner who says "no" thirty times still counts
        # as present. Distinguishing those is a judgement about meaning, and a judgement
        # about meaning made by the model is the thing this whole gate exists to stop
        # relying on. What is claimed here is narrow -- somebody was in the room and
        # answering while the lesson was being taught -- and that is all.
        if not self._spoken:
            return
        self._learner_turns += 1

    def worked_through_for(self, learner_id: str | None) -> bool | None:
        """Say whether enough of the running lesson has been said out loud.

        Three answers, and the third is the one that matters. True and False are
        verdicts; None means "this cannot be measured" -- no lesson running, not this
        learner's, or a lesson pinned with no lines to watch for. A caller must not read
        None as False: refusing to record a completion because the app forgot to supply
        the lines would punish a learner for a wiring mistake. Absence of evidence is
        reported as absence of evidence.
        """
        session = self._session
        if session is None or not isinstance(learner_id, str) or learner_id != session.learner_id:
            return None
        if not self._teachable:
            return None
        line_floor = max(1, round(len(self._teachable) * _WORKED_THROUGH_FRACTION))
        turn_floor = max(_MIN_LEARNER_TURNS, round(line_floor * _LEARNER_TURNS_PER_LINE_FLOOR))
        return len(self._spoken) >= line_floor and self._learner_turns >= turn_floor

    def coverage_for(self, learner_id: str | None) -> tuple[int, int, int] | None:
        """Return (lines said, lines in the lesson, learner turns), or None.

        Counts only, never the lines themselves and never a word the learner said:
        this feeds a log line and a tool result, and both are places a learner's words
        must never reach.
        """
        session = self._session
        if session is None or not isinstance(learner_id, str) or learner_id != session.learner_id:
            return None
        if not self._teachable:
            return None
        return (len(self._spoken), len(self._teachable), self._learner_turns)

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
        # Coverage goes with it. Left behind, it would be read against the NEXT lesson
        # and count somebody's finished work towards a lesson they have not started --
        # which is the same class of fault as handing one learner's session to another.
        self._teachable = ()
        self._longest_first = ()
        self._spoken = set()
        self._learner_turns = 0
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
    "an optional whole-number timestamp in range -- and, when the lesson's lines are given, at least one line to "
    "teach from. Nothing was pinned and any lesson already running is untouched. The lesson being practised is application state, decided by the app rather than named in "
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


# The store's permitted shape for a lesson id and a language code, restated rather
# than imported (see the module docstring). Restating is how this drifted once: the
# store inverted its rule to this allow-list and the copy here kept the old deny-list,
# so a zero-width space could be pinned here and refused at finish_lesson instead.
# test_lesson_session.py now runs both rules over every seeded id and a set of hostile
# values and fails if they disagree, so a change on either side cannot go unnoticed.
_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_CATALOG_CODE_LENGTHS = range(2, 9)


def _is_lesson_id(value: object) -> bool:
    """Say whether the store would accept this as a lesson id (learners/store.py, _cannot_name_a_lesson)."""
    return isinstance(value, str) and _SLUG.fullmatch(value) is not None


def _is_catalog_code(value: object) -> bool:
    """Say whether the store would accept this as a language code (learners/store.py, _cannot_be_a_catalog_code)."""
    return isinstance(value, str) and len(value) in _CATALOG_CODE_LENGTHS and _SLUG.fullmatch(value) is not None


def _is_storable_timestamp(value: object) -> bool:
    """Say whether this is a whole number the store could actually hold.

    bool is a subclass of int, so isinstance(True, int) is True and a bare isinstance
    check would pin True as a timestamp. learners/store.py makes this same exclusion,
    alongside the 64-bit bound above, and both halves belong together: fixing one and
    leaving its sibling is the single most repeated defect on this board.
    """
    return isinstance(value, int) and not isinstance(value, bool) and _STORABLE_INT_MIN <= value <= _STORABLE_INT_MAX


def _for_matching(value: object) -> str:
    """Reduce a line to the form two spellings of the same sentence share.

    Case and punctuation vary freely between a printed line and a spoken one; letters
    do not. Accents are letters here and are kept -- folding them would make "manha"
    match "manhã" and quietly retire the one property a course typed off page images
    exists to protect.

    Returns "" for anything that is not a usable string, and every caller treats "" as
    "nothing to compare", so a non-string can never be counted as coverage.
    """
    if not isinstance(value, str):
        return ""
    kept = "".join(character if character.isalnum() or character.isspace() else " " for character in value)
    return " ".join(kept.casefold().split())


def _now_ms() -> int:
    """Return the current time in the unit recorded attempts use: epoch milliseconds."""
    # learners/store.utc_now_ms is int(time.time() * 1000), and is store-private behind
    # the learners package boundary -- so this matches it rather than importing it.
    return int(time.time() * 1000)
