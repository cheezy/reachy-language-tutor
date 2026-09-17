"""Guard the second trust anchor: which lesson is running, and whose it is.

test_current_learner.py guards who the app is serving. This file guards the same
property one fact along. If the lesson being practised could be named in the
conversation, a result could be recorded against a lesson nobody ran -- and, worse,
against a lesson somebody else ran. So the app holds it, the holder is bound at
construction to one learner, and it only hands a session back to that learner.

The tests are grouped by what they defend: the value and the holder, the open guard
(including a differential against the store the pinned values must survive), the
privacy properties (repr and logs are both channels a learner id can escape through),
and the wiring that puts the holder on the dependencies the tools receive.
"""

import ast
import time
import inspect
import logging
import traceback
import dataclasses
from typing import Any
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# The canonical "what writes a sealed field" rule, reused rather than restated. A
# second copy here would drift from the seal it mirrors, which is the defect this
# test exists to catch -- see test_locked_profile.py for the same import and reason.
from test_current_learner import _field_write_offenders

from reachy_language_tutor import main, lesson_session, current_learner
from reachy_language_tutor.tools import core_tools
from reachy_language_tutor.learners import store, get_lesson
from reachy_language_tutor.lesson_session import LessonSession, LessonSessionHolder, LessonSessionRefusedError
from reachy_language_tutor.tools.core_tools import ToolDependencies


MODULE_LOGGER = "reachy_language_tutor.lesson_session"

# Only these four spellings may appear on the module's logger. An allow-list, not a
# list of banned ones: logger.exception attaches exc_info implicitly, and a rendered
# traceback can carry a lesson or learner id while msg stays a constant.
PERMITTED_LOG_METHODS = {"debug", "info", "warning", "error"}

# Never a real name, and the two learners must be visibly different people.
LEARNER_A = "learner-a"
LEARNER_B = "learner-b"
LANGUAGE = "fr"
# A lesson the seeded catalog really has, so the differential test below asks the real
# consumer rather than a stand-in.
LESSON = "fr-01-greetings"

# Shapes the store accepts as a binding and can then never match, plus the wrong types.
# Used against every string argument, so the whole family is covered rather than the
# member that happens to be easiest to name.
UNUSABLE_IDS: tuple[Any, ...] = (
    "",
    "   ",
    f" {LESSON}",
    f"{LESSON} ",
    "fr 01 greetings",
    "fr-01\n",
    "fr-01\x00",
    None,
    0,
    b"x",
    object(),
)


@pytest.fixture
def instance(tmp_path: Path) -> Path:
    """Prepare a learner database at a temporary instance path."""
    assert store.ensure_learner_database(tmp_path).ready is True
    return tmp_path


def _deps(**overrides: Any) -> ToolDependencies:
    """Dependencies with the two required fields stubbed, as the existing tests do."""
    return ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock(), **overrides)


def _holder_for(learner_id: str | None = LEARNER_A) -> LessonSessionHolder:
    """Build a holder bound to a learner, the only kind that can pin anything."""
    return LessonSessionHolder(learner_id)


def _opened(holder: LessonSessionHolder, teachable_lines: tuple[str, ...] = ()) -> LessonSession:
    """Open the standard lesson, the starting point of most cases below."""
    return holder.open(lesson_id=LESSON, language_code=LANGUAGE, teachable_lines=teachable_lines)


def _log_surface(record: logging.LogRecord) -> str:
    """Everything a record carries that could later be rendered, not just the message.

    A local copy of the helper of the same name in test_current_learner.py, which
    carries the full derivation of why each channel is here: msg and args are formatted
    lazily and can reach a sink separately, a live exception rides in exc_info while
    msg stays a constant, extra= lands straight in record.__dict__, and the logger NAME
    is rendered by every "%(name)s" formatter. Duplicated rather than shared because
    importing across test modules would couple this file to a 970-line security suite;
    if you change one, change the other.
    """
    parts = [record.name, str(record.msg), str(record.args), record.getMessage()]
    if record.exc_info:
        parts.append("".join(traceback.format_exception(*record.exc_info)))
    if record.exc_text:
        parts.append(str(record.exc_text))
    if record.stack_info:
        parts.append(str(record.stack_info))
    standard = logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys()
    parts.extend(f"{key}={value}" for key, value in record.__dict__.items() if key not in standard)
    return " | ".join(parts)


# --- The value and the holder ---------------------------------------------------------


def test_opening_a_lesson_records_the_lesson_the_language_and_the_learner_it_was_for() -> None:
    """All four facts, or a later read cannot tell whose lesson it is looking at."""
    session = _opened(_holder_for())

    assert session.lesson_id == LESSON
    assert session.language_code == LANGUAGE
    assert session.learner_id == LEARNER_A
    assert isinstance(session.opened_at, int)


def test_an_open_given_a_timestamp_records_that_timestamp() -> None:
    """Injectable, so a test pins a time rather than monkeypatching the clock."""
    session = _holder_for().open(lesson_id=LESSON, language_code=LANGUAGE, opened_at=1_700_000_000_123)

    assert session.opened_at == 1_700_000_000_123


def test_an_open_with_no_timestamp_stamps_the_current_time_in_the_unit_attempts_use() -> None:
    """Milliseconds. Seconds would be 1000x off the LessonAttempt this becomes."""
    before = int(time.time() * 1000)
    session = _opened(_holder_for())
    after = int(time.time() * 1000)

    assert before <= session.opened_at <= after


def test_a_session_is_returned_to_the_learner_it_was_opened_for() -> None:
    """The control: without this passing, every refusal below could be vacuous."""
    holder = _holder_for()
    session = _opened(holder)

    assert holder.read_for(LEARNER_A) == session


def test_a_session_opened_for_one_learner_is_not_returned_to_another() -> None:
    """The whole point of the module: a household shares one robot, not one lesson."""
    holder = _holder_for()
    _opened(holder)

    assert holder.read_for(LEARNER_B) is None


def test_reading_with_no_current_learner_returns_nothing_rather_than_the_running_lesson() -> None:
    """Nobody identified must mean nobody served, never "serve whoever is pinned"."""
    holder = _holder_for()
    _opened(holder)

    assert holder.read_for(None) is None


def test_reading_with_a_learner_id_that_is_not_a_string_returns_nothing() -> None:
    """An allow-list on the read: it must BE a string, and be the right one."""
    holder = _holder_for()
    _opened(holder)

    for impostor in (0, b"learner-a", object(), ["learner-a"]):
        assert holder.read_for(impostor) is None  # type: ignore[arg-type]


def test_reading_a_padded_learner_id_does_not_match_the_session_it_resembles() -> None:
    """Exact equality. Widening the match would buy nothing and sell a near miss."""
    holder = _holder_for()
    _opened(holder)

    assert holder.read_for(f" {LEARNER_A} ") is None


def test_a_fresh_holder_reports_nothing_running_rather_than_raising() -> None:
    """A tool asked to finish before anything started must answer, not end the turn."""
    assert _holder_for().read_for(LEARNER_A) is None


def test_clearing_leaves_nothing_running_for_the_learner_it_was_opened_for() -> None:
    """Finishing a lesson has to actually unpin it, or the next read finds a ghost."""
    holder = _holder_for()
    _opened(holder)

    holder.clear()

    assert holder.read_for(LEARNER_A) is None


def test_clearing_an_empty_holder_is_harmless_and_still_reports_nothing_running() -> None:
    """Idempotent: a second finish, or a finish after a restart, must not raise."""
    holder = _holder_for()

    holder.clear()
    holder.clear()

    assert holder.read_for(LEARNER_A) is None


def test_opening_a_second_lesson_replaces_the_first_rather_than_stacking() -> None:
    """The holder holds THE running lesson, singular. No queue, no stack."""
    holder = _holder_for()
    _opened(holder)

    second = holder.open(lesson_id="fr-02-introductions", language_code=LANGUAGE)

    assert holder.read_for(LEARNER_A) == second


def test_a_session_cannot_be_repointed_at_another_learner_after_it_is_opened() -> None:
    """Frozen: the binding between a lesson and a learner is the thing being trusted."""
    session = _opened(_holder_for())

    with pytest.raises(dataclasses.FrozenInstanceError):
        session.learner_id = LEARNER_B  # type: ignore[misc]


# --- The holder is bound to one learner, and open has no way to name another ----------


def test_open_takes_no_learner_argument_at_all() -> None:
    """The security consideration, pinned as a property of the signature.

    "Only start_lesson and finish_lesson move it, and neither takes an identity
    parameter." A tool cannot pass a conversation-supplied learner into a parameter
    that does not exist, so this holds without depending on the next author reading a
    comment. An allow-list, so a parameter added later has to be named here.
    """
    parameters = set(inspect.signature(LessonSessionHolder.open).parameters)

    # teachable_lines is the lesson's own printed text, not anybody's identity: it is
    # what the holder compares against what the tutor says, so that finish_lesson can
    # tell a lesson that happened from one that did not. The property this test guards
    # is unchanged -- there is still no learner parameter, and open() still pins only
    # for the learner the holder was built for.
    assert parameters == {"self", "lesson_id", "language_code", "opened_at", "teachable_lines"}
    assert not any("learner" in name for name in parameters), "open() must never take an identity"


def test_a_holder_bound_to_nobody_refuses_to_pin_a_lesson() -> None:
    """The "no current learner set at all" edge case: refuse, never guess."""
    holder = LessonSessionHolder()

    with pytest.raises(LessonSessionRefusedError):
        _opened(holder)

    assert holder.read_for(LEARNER_A) is None


@pytest.mark.parametrize("unusable", UNUSABLE_IDS)
def test_a_holder_bound_to_an_unusable_learner_holds_lessons_for_nobody(unusable: Any) -> None:
    """A holder that cannot name whose lessons it holds behaves as if it has none."""
    holder = LessonSessionHolder(unusable)

    with pytest.raises(LessonSessionRefusedError):
        _opened(holder)


def test_a_second_learners_holder_cannot_be_made_to_pin_against_the_first() -> None:
    """Two people, two holders: neither can reach the other's learner id."""
    holder_b = _holder_for(LEARNER_B)

    session = _opened(holder_b)

    assert session.learner_id == LEARNER_B
    assert holder_b.read_for(LEARNER_A) is None


# --- The open guard, and the store it has to agree with -------------------------------


@pytest.mark.parametrize("field_name", ["lesson_id", "language_code"])
@pytest.mark.parametrize("unusable", UNUSABLE_IDS)
def test_an_unusable_id_is_refused_and_leaves_the_running_lesson_untouched(field_name: str, unusable: Any) -> None:
    """Every shape, against both string arguments -- the class, not one member.

    Fail closed on the running lesson too: a rejected open must not have the side
    effect of unpinning whatever was legitimately running.
    """
    holder = _holder_for()
    running = _opened(holder)
    arguments: dict[str, Any] = {"lesson_id": LESSON, "language_code": LANGUAGE}
    arguments[field_name] = unusable

    with pytest.raises(ValueError) as excinfo:
        holder.open(**arguments)

    assert type(excinfo.value).__name__ == "LessonSessionRefusedError"
    assert holder.read_for(LEARNER_A) == running


@pytest.mark.parametrize("unusable", UNUSABLE_IDS)
def test_a_lesson_id_this_module_refuses_is_one_the_store_could_never_match(unusable: Any, instance: Path) -> None:
    """The differential: ask the real consumer rather than reason about it.

    D19's lesson is that a guard testing a narrower condition than its consumer lets a
    value through that the consumer then rejects, turning a refusal into a silent "no
    such lesson". This runs both sides against a real database: every shape refused
    here comes back as nothing from get_lesson, and the one shape accepted here comes
    back as a real row -- so the guard is neither narrower nor wider than what it
    protects.
    """
    holder = _holder_for()

    with pytest.raises(LessonSessionRefusedError):
        holder.open(lesson_id=unusable, language_code=LANGUAGE)

    assert get_lesson(unusable, instance_path=instance) is None


def test_a_lesson_id_this_module_pins_is_one_the_store_really_finds(instance: Path) -> None:
    """The other half of the differential, without which it would pass vacuously."""
    session = _opened(_holder_for())

    found = get_lesson(session.lesson_id, instance_path=instance)

    assert found is not None
    assert found.id == LESSON
    assert found.language_code == session.language_code


def test_an_uppercase_language_code_is_refused_because_the_catalog_stores_lowercase() -> None:
    """Uppercase binds cleanly and matches nothing, exactly as padding does."""
    holder = _holder_for()

    with pytest.raises(LessonSessionRefusedError):
        holder.open(lesson_id=LESSON, language_code="FR")


def test_a_boolean_timestamp_is_refused_the_way_the_store_refuses_one() -> None:
    """True is an int, so a bare isinstance check would pin it as a timestamp."""
    holder = _holder_for()

    with pytest.raises(LessonSessionRefusedError):
        holder.open(lesson_id=LESSON, language_code=LANGUAGE, opened_at=True)


def test_a_non_integer_timestamp_is_refused_rather_than_pinned() -> None:
    """A float or a string would reach a stored attempt and be wrong there instead."""
    holder = _holder_for()

    for unusable in (1.5, "1700000000123", b"1", object()):
        with pytest.raises(LessonSessionRefusedError):
            holder.open(lesson_id=LESSON, language_code=LANGUAGE, opened_at=unusable)


@pytest.mark.parametrize("out_of_range", [2**63, -(2**63) - 1, 2**70])
def test_a_timestamp_the_store_could_not_hold_is_refused_here_rather_than_later(out_of_range: int) -> None:
    """The sibling of the bool exclusion, and the one that would raise OverflowError.

    An out-of-range int passes isinstance and then raises at SQLite bind time -- not
    sqlite3.Error, not ValueError, so nothing downstream absorbs it. Pinning one would
    move that raise to the layer that can no longer explain it.
    """
    holder = _holder_for()

    with pytest.raises(LessonSessionRefusedError):
        holder.open(lesson_id=LESSON, language_code=LANGUAGE, opened_at=out_of_range)


def test_the_largest_timestamp_the_store_can_hold_is_still_accepted() -> None:
    """The bound is a bound, not an off-by-one that rejects a legal value."""
    session = _holder_for().open(lesson_id=LESSON, language_code=LANGUAGE, opened_at=2**63 - 1)

    assert session.opened_at == 2**63 - 1


def test_the_refusal_message_names_neither_the_lesson_nor_the_learner() -> None:
    """_dispatch_tool_call renders a tool's exception straight to the model."""
    holder = _holder_for()

    with pytest.raises(ValueError) as excinfo:
        holder.open(lesson_id="portuguese lesson three", language_code=LANGUAGE)

    assert type(excinfo.value).__name__ == "LessonSessionRefusedError"
    assert LEARNER_A not in str(excinfo.value)
    assert "portuguese lesson three" not in str(excinfo.value)


def test_a_padded_lesson_id_is_refused_rather_than_quietly_repaired() -> None:
    """Repairing it invisibly would leave the layer above no signal it sent rubbish.

    This is the store's own stated choice, and the reason this module does not strip:
    a caller that sent a padded id has a bug, and silently fixing it hides the bug
    while leaving the next malformed value -- one stripping cannot repair -- to fail.
    """
    holder = _holder_for()

    with pytest.raises(LessonSessionRefusedError):
        holder.open(lesson_id=f"  {LESSON}  ", language_code=LANGUAGE)


# --- The privacy properties -----------------------------------------------------------


# Every public reader of the holder, and the "nothing" each must answer to a learner
# the running lesson was not opened for. An ALLOW-LIST: a method absent from here fails
# the surface assertion below, so a new reader cannot be added without deciding what it
# answers a stranger.
_READERS_AND_THEIR_REFUSALS = {
    "read_for": None,
    "coverage_for": None,
    "worked_through_for": None,
}


def test_the_holder_offers_no_way_to_read_a_session_without_naming_a_learner() -> None:
    """A bare .session would be an ambient read, and so would a reader taking no learner.

    Two halves, because the name list alone stopped being enough once the holder grew
    readers. The surface is still pinned -- but every member of it that can see session
    state must also NAME the learner it is answering for, in its signature, and hand a
    stranger nothing. A reader that skipped that is the shape which serves one household
    member's lesson to the next.
    """
    import inspect

    holder = _holder_for()
    surface = {name for name in dir(holder) if not name.startswith("_")}

    assert surface == {"open", "clear", "note_spoken", "note_learner_turn", *_READERS_AND_THEIR_REFUSALS}

    # open() is the writer and is bound to the holder's own learner; clear() destroys
    # rather than reveals. Everything else touches session state on somebody's behalf.
    for name in surface - {"open", "clear"}:
        parameters = set(inspect.signature(getattr(holder, name)).parameters)
        assert "learner_id" in parameters, (
            f"{name} touches session state without naming a learner, which is an ambient read"
        )


def test_a_stranger_reads_nothing_and_marks_no_coverage_on_somebody_elses_lesson() -> None:
    """The signature check above is structural; this is the behaviour it stands for.

    Parametrised over every reader by name, so a reader added to the allow-list without
    the refusal wired up fails here rather than shipping.
    """
    holder = _holder_for()
    _opened(holder, teachable_lines=("uma frase",))

    for name, nothing in _READERS_AND_THEIR_REFUSALS.items():
        assert getattr(holder, name)(LEARNER_B) is nothing, f"{name} answered a learner it was not opened for"

    # And a stranger's words cannot count towards the lesson somebody else is taking,
    # on either side of it: not as a line covered, not as a turn taken.
    holder.note_spoken(LEARNER_B, "uma frase")
    holder.note_learner_turn(LEARNER_B)
    assert holder.coverage_for(LEARNER_A) == (0, 1, 0), "a stranger's speech was counted towards the lesson"


def test_the_sessions_repr_carries_neither_the_learner_id_nor_the_lesson_id() -> None:
    """A dataclass repr would render both, and repr is a log channel like any other."""
    session = _opened(_holder_for())

    assert LEARNER_A not in repr(session)
    assert LESSON not in repr(session)


def test_the_holders_repr_carries_neither_the_learner_id_nor_the_lesson_id() -> None:
    """Shape only: whether something is running, never which lesson or whose."""
    holder = _holder_for()
    _opened(holder)

    assert LEARNER_A not in repr(holder)
    assert LESSON not in repr(holder)
    assert "running" in repr(holder)
    assert "empty" in repr(_holder_for())


def test_the_dependency_bundles_repr_carries_neither_the_learner_id_nor_the_lesson_id() -> None:
    """ToolDependencies is a dataclass, so its repr drags the holder out with it.

    This is D3's shape caught one layer up before it happened: a single
    logger.debug("%s", deps) is all it would take.
    """
    deps = _deps(lesson_session=_holder_for())
    _opened(deps.lesson_session)

    assert "LessonSessionHolder(" in repr(deps), "the holder is not in the repr, so this pin would be vacuous"
    assert LEARNER_A not in repr(deps)
    assert LESSON not in repr(deps)


def test_no_log_record_from_a_full_lesson_cycle_carries_the_learner_or_the_lesson_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Open, a mismatched read and a clear, checked across the WHOLE record."""
    holder = _holder_for()

    with caplog.at_level(logging.DEBUG, logger=MODULE_LOGGER):
        _opened(holder)
        holder.read_for(LEARNER_B)
        holder.clear()

    records = [record for record in caplog.records if record.name == MODULE_LOGGER]
    assert records, "the module logged nothing, so this pin would pass vacuously"
    for record in records:
        surface = _log_surface(record)
        assert LEARNER_A not in surface, surface
        assert LEARNER_B not in surface, surface
        assert LESSON not in surface, surface


def test_every_logging_call_in_the_module_is_a_permitted_method_with_one_constant() -> None:
    """An allow-list on the log surface, so a future "... %s", lesson_id fails here.

    The METHOD is allow-listed as well as the arguments. logger.exception("constant")
    has one constant argument and no keywords, and would pass a guard that only looked
    at those -- while implicitly attaching exc_info, whose rendered traceback can carry
    a lesson or a learner id.

    Reads the source rather than exercising the calls: a line that only runs on a path
    no test reaches would still ship the id.
    """
    tree = ast.parse(Path(lesson_session.__file__).read_text())
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "logger"
    ]

    assert calls, "found no logging calls to check; the guard would pass vacuously"
    for call in calls:
        assert isinstance(call.func, ast.Attribute)
        assert call.func.attr in PERMITTED_LOG_METHODS, ast.dump(call)
        assert not call.keywords, ast.dump(call)
        assert len(call.args) == 1, ast.dump(call)
        assert isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str), ast.dump(call)


# --- The wiring -----------------------------------------------------------------------


def test_tool_dependencies_carries_the_lesson_session_holder() -> None:
    """Tools reach the running lesson the same way they reach the current learner."""
    fields = {f.name: f for f in dataclasses.fields(ToolDependencies)}

    assert "lesson_session" in fields
    assert fields["lesson_session"].type is LessonSessionHolder
    assert fields["lesson_session"].default_factory is LessonSessionHolder


def test_dependencies_built_without_naming_a_holder_still_have_one_bound_to_nobody() -> None:
    """Keep the fallback path fail-closed as well as non-null.

    default_factory means no tool needs an "is there a holder" branch, and the holder
    it produces is bound to nobody, so that path pins nothing.
    """
    deps = _deps()

    assert deps.lesson_session.read_for(LEARNER_A) is None
    with pytest.raises(LessonSessionRefusedError):
        _opened(deps.lesson_session)


def test_dependencies_built_by_startup_carry_a_holder_bound_to_the_current_learner(
    instance: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wiring end to end: the holder and the seal name the same person.

    A robot that has just booted is not in the middle of anybody's lesson, and the
    lesson it can pin belongs to the learner the dependencies are sealed to.

    Run under the development override, because the agreement between the holder and
    the seal is what this test is about and a holder bound to nobody pins nothing --
    it would still pass, and prove nothing. Nothing recognises a face in a test run.
    """
    monkeypatch.setenv(current_learner.DEV_CURRENT_LEARNER_ENV, store.SEED_LEARNERS[0][0])

    deps = main.build_tool_dependencies(
        robot=MagicMock(),
        movement_manager=MagicMock(),
        instance_path=instance,
        camera_enabled=False,
        logger=logging.getLogger(__name__),
    )

    assert isinstance(deps.lesson_session, LessonSessionHolder)
    assert deps.lesson_session.read_for(deps.current_learner_id) is None

    session = deps.lesson_session.open(lesson_id=LESSON, language_code=LANGUAGE)

    assert session.learner_id == deps.current_learner_id == store.SEED_LEARNERS[0][0]


def test_the_identity_seal_still_refuses_a_write_on_dependencies_that_carry_a_holder(instance: Path) -> None:
    """Adding a field must not have disarmed the seal this app's boundary rests on."""
    deps = main.build_tool_dependencies(
        robot=MagicMock(),
        movement_manager=MagicMock(),
        instance_path=instance,
        camera_enabled=False,
        logger=logging.getLogger(__name__),
    )
    seeded = deps.current_learner_id

    with pytest.raises(AttributeError) as excinfo:
        deps.current_learner_id = LEARNER_B

    assert type(excinfo.value).__name__ == "CurrentLearnerIsReadOnlyError"
    assert deps.current_learner_id == seeded


def test_the_holder_reference_is_sealed_the_way_the_learner_it_is_bound_to_is() -> None:
    """The holder is mutable; the reference to it is not. That is the task's own words.

    Round 1 of this work left the reference writable, arguing that read_for made the
    seal unnecessary. A security review disproved that by running it: read_for proves
    the LEARNER dimension only, so a holder swapped for one bound to the same learner
    but carrying a lesson nobody started reads back clean -- the exact harm this module
    exists to prevent. The seal is what closes the lesson dimension.
    """
    deps = _deps(current_learner_id=LEARNER_A)

    with pytest.raises(AttributeError) as excinfo:
        deps.lesson_session = _holder_for()

    assert type(excinfo.value).__name__ == "RunningLessonIsReadOnlyError"


def test_the_holder_reference_cannot_be_deleted_either() -> None:
    """Deletion is the same attribute protocol and has to be refused with it."""
    deps = _deps(current_learner_id=LEARNER_A)

    with pytest.raises(AttributeError) as excinfo:
        del deps.lesson_session

    assert type(excinfo.value).__name__ == "RunningLessonIsReadOnlyError"


def test_the_refusal_to_swap_the_holder_names_neither_a_learner_nor_a_lesson() -> None:
    """_dispatch_tool_call renders a tool's exception straight to the model."""
    deps = _deps(current_learner_id=LEARNER_A)

    with pytest.raises(AttributeError) as excinfo:
        deps.lesson_session = _holder_for()

    assert LEARNER_A not in str(excinfo.value)
    assert LESSON not in str(excinfo.value)


def test_both_sealed_dependencies_refuse_through_one_shared_base() -> None:
    """A caller that wants to catch "startup fixed this" can, without naming both."""
    deps = _deps(current_learner_id=LEARNER_A)

    with pytest.raises(core_tools.SealedDependencyError):
        deps.lesson_session = _holder_for()
    with pytest.raises(core_tools.SealedDependencyError):
        deps.current_learner_id = LEARNER_B


def test_a_holder_bound_to_one_learner_still_refuses_the_other_if_it_ever_gets_in() -> None:
    """Belt and braces: the read guard stands on its own, seal or no seal.

    object.__setattr__ reaches the instance dict without passing through the seal --
    the same documented gap current_learner_id has -- so the read-side property is
    still the thing that must hold if a holder does get swapped in.
    """
    deps = _deps(current_learner_id=LEARNER_B)
    smuggled = _holder_for(LEARNER_A)
    _opened(smuggled)

    object.__setattr__(deps, "lesson_session", smuggled)

    assert deps.lesson_session.read_for(deps.current_learner_id) is None


def test_the_package_wide_scan_really_covers_the_holder_and_not_just_the_learner() -> None:
    """The static half, proved against the real scan rather than a second copy of it.

    The seal refuses an ordinary attribute write; it cannot refuse an
    object.__setattr__ or an instance-dict write, which is why the learner id carries a
    source scan as well as a runtime guard. Its sibling needs the same, and the way to
    get it is to make the one scan derive its field list from _SEALED_ATTRIBUTES rather
    than to write a second scan here that would drift from it -- a security review
    found exactly that drift in an earlier version of this file, where a hand-rolled
    scan missed every spelling the real one catches.

    So this runs the canonical rule over synthetic sources, one per shape that goes
    around the attribute protocol, and asserts each is flagged.
    """
    smuggles = [
        'object.__setattr__(bundle, "lesson_session", evil)',
        'setattr(bundle, "lesson_session", evil)',
        'deps.__dict__["lesson_session"] = evil',
        "bundle.lesson_session = evil",
        'delattr(bundle, "lesson_session")',
        "del bundle.lesson_session",
    ]

    for source in smuggles:
        assert _field_write_offenders(source, "synthetic"), source


def test_the_package_wide_scan_is_not_simply_flagging_everything() -> None:
    """Without this, the pin above would pass on a rule that returned True always."""
    assert _field_write_offenders("bundle.camera_enabled = True", "synthetic") == []
    assert _field_write_offenders("holder.open(lesson_id=x, language_code=y)", "synthetic") == []


def test_the_one_permitted_writer_really_does_write_it() -> None:
    """Without this, a package that wired up nothing would look equally clean."""
    source = Path(main.__file__).read_text()

    assert "lesson_session=LessonSessionHolder(current_learner_id)" in source
