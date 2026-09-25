"""Tests for lesson_feedback: which events earn a move, and what a reaction may never see.

Two things are being held here, and only one of them is about movement.

The first is the honest-signal rule the task exists for: a reaction fires below the write,
never beside it. A nod that happens when the tutor THINKS a result was saved is worse than
no nod, because it tells a learner something the database did not.

The second is a boundary. This module is the first thing in the app whose whole job is to
react to what a learner just did, which is exactly the shape that ends up holding a name
"so the reaction can be warmer". It is built so it cannot: its entry point takes a member
of a closed enum and a movement sink, and a test below pins that signature and the module's
whole import list rather than trusting a future edit to remember.
"""

import ast
import inspect
import logging
from typing import Any
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from reachy_language_tutor import lesson_feedback
from reachy_language_tutor.tools import play_emotion
from reachy_language_tutor.learners import store
from reachy_language_tutor.lesson_session import LessonSessionHolder
from reachy_language_tutor.learners.models import OUTCOMES
from reachy_language_tutor.lesson_feedback import (
    EVENT_INTENTS,
    LessonEvent,
    react_to_lesson_event,
    event_for_recorded_result,
)
from reachy_language_tutor.tools.core_tools import ToolDependencies
from reachy_language_tutor.tools.play_emotion import EMOTION_INTENTS
from reachy_language_tutor.tools.start_lesson import StartLesson
from reachy_language_tutor.tools.finish_lesson import FinishLesson


SEEDED_LEARNER = store.SEED_LEARNERS[0][0]
SPANISH_NEXT = "es-03-numbers"
SPANISH = "es"

# Every move the four intents can resolve to, so a fake library can offer them all and the
# resolution is the real one rather than a stub.
AVAILABLE_MOVES = ["attentive1", "attentive2", "success1", "success2", "understanding2", "dance2", "dance3"]


class FakeRecordedMoves:
    """A library that offers moves without downloading a dataset."""

    def __init__(self, moves: list[str] | None = None) -> None:
        """Offer the given moves, or every move the policy's four intents can reach."""
        self._moves = AVAILABLE_MOVES if moves is None else moves

    def list_moves(self) -> list[str]:
        """Return the moves this fake offers."""
        return list(self._moves)


class FakeEmotionQueueMove:
    """Stands in for the real move so nothing reaches a robot."""

    def __init__(self, emotion_name: str, recorded_moves: Any) -> None:
        """Record what would have been played, without reaching a robot."""
        self.emotion_name = emotion_name
        self.recorded_moves = recorded_moves


@pytest.fixture
def loaded(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Put the policy in the state a warm robot is in, and hand back the movement sink."""
    monkeypatch.setattr(lesson_feedback, "EMOTION_AVAILABLE", True)
    monkeypatch.setattr(lesson_feedback, "EmotionQueueMove", FakeEmotionQueueMove)
    monkeypatch.setattr(play_emotion, "loaded_emotion_library", lambda: FakeRecordedMoves())
    return MagicMock()


@pytest.fixture
def instance(tmp_path: Path) -> Path:
    """Return a prepared learner database at a temporary instance path."""
    assert store.ensure_learner_database(tmp_path).ready is True
    return tmp_path


def _deps(**overrides: Any) -> ToolDependencies:
    """Build dependencies the way main.build_tool_dependencies does, fields stubbed."""
    overrides.setdefault("lesson_session", LessonSessionHolder(overrides.get("current_learner_id")))
    overrides.setdefault("movement_manager", MagicMock())
    return ToolDependencies(reachy_mini=MagicMock(), **overrides)


def _pinned_deps(instance: Path, lesson_id: str = SPANISH_NEXT, **overrides: Any) -> ToolDependencies:
    """Dependencies with a lesson already running, which is what production has here."""
    overrides.setdefault("current_learner_id", SEEDED_LEARNER)
    overrides.setdefault("instance_path", instance)
    deps = _deps(**overrides)
    deps.lesson_session.open(lesson_id=lesson_id, language_code=SPANISH)
    return deps


def _queued(movement_manager: MagicMock) -> list[str]:
    """Return the names of the moves handed to queue_move, in order."""
    return [call.args[0].emotion_name for call in movement_manager.queue_move.call_args_list]


# --- The vocabulary is somebody else's, and stays somebody else's ----------------------


def test_every_intent_the_policy_names_is_one_play_emotion_already_knows() -> None:
    """Inventing an emotion name resolves to nothing on a robot and to silence here."""
    for event, intent in EVENT_INTENTS.items():
        assert intent in EMOTION_INTENTS, f"{event.value} maps to {intent!r}, which play_emotion does not know"


def test_the_policy_names_an_intent_for_every_event_it_can_be_handed() -> None:
    """An event with no intent is an event that silently does nothing."""
    assert set(EVENT_INTENTS) == set(LessonEvent), (
        f"events and intents disagree: {sorted(e.value for e in set(EVENT_INTENTS) ^ set(LessonEvent))}"
    )


def test_the_outcome_table_covers_the_stores_whole_vocabulary() -> None:
    """A new outcome word upstream must fail here rather than collect a wrong reaction."""
    assert set(lesson_feedback._OUTCOME_EVENTS) == set(OUTCOMES), (
        "the policy and the store disagree about how a lesson can go: "
        f"{sorted(set(lesson_feedback._OUTCOME_EVENTS) ^ set(OUTCOMES))}"
    )


@pytest.mark.parametrize("event", list(LessonEvent))
def test_every_event_the_policy_handles_resolves_to_a_move_the_library_offers(
    event: LessonEvent, loaded: MagicMock
) -> None:
    """An intent that resolves to nothing is an event the robot answers with stillness."""
    queued = react_to_lesson_event(event, movement_manager=loaded)

    assert queued is not None, f"{event.value} resolved to no available move"
    assert _queued(loaded) == [queued]


# --- Which event a written result is --------------------------------------------------


def test_a_completed_result_and_a_skipped_result_produce_different_reactions(loaded: MagicMock) -> None:
    """Finishing and setting aside are different things and must not feel the same."""
    done = react_to_lesson_event(
        event_for_recorded_result("completed", more_lessons_remain=True), movement_manager=loaded
    )
    skipped = react_to_lesson_event(
        event_for_recorded_result("skipped", more_lessons_remain=True), movement_manager=loaded
    )

    assert done != skipped, f"both a completion and a skip queued {done!r}"


def test_finishing_the_last_lesson_in_a_language_reacts_differently_from_an_ordinary_one() -> None:
    """The one big reaction, and it can only happen when nothing is left."""
    last = event_for_recorded_result("completed", more_lessons_remain=False)
    ordinary = event_for_recorded_result("completed", more_lessons_remain=True)

    assert last is LessonEvent.LANGUAGE_FINISHED
    assert ordinary is LessonEvent.RESULT_RECORDED_COMPLETE


def test_a_standing_the_store_could_not_read_back_is_not_celebrated_as_a_finished_language() -> None:
    """The footgun this policy is built around, asserted rather than commented.

    finish_lesson's read-back returns an all-None dict when the lookup after the write
    fails, and its next_lesson is None in exactly the same way a finished language's is.
    Read as False, that fault becomes a robot telling a child they have finished Spanish.
    """
    assert event_for_recorded_result("completed", more_lessons_remain=None) is (
        LessonEvent.RESULT_RECORDED_COMPLETE
    ), "an unreadable standing was read as a finished language"


def test_a_set_aside_result_never_produces_the_language_finished_reaction() -> None:
    """Skipping the last lesson does not finish a language, and must not dance about it."""
    for outcome in ("partial", "skipped"):
        assert event_for_recorded_result(outcome, more_lessons_remain=False) is (
            LessonEvent.RESULT_RECORDED_SET_ASIDE
        ), f"{outcome} with nothing remaining was treated as finishing the language"


def test_an_outcome_the_store_would_refuse_is_not_an_event() -> None:
    """A word the store does not know cannot describe something that was written."""
    assert event_for_recorded_result("abandoned", more_lessons_remain=True) is None
    assert event_for_recorded_result(None, more_lessons_remain=True) is None


# --- What the policy may be handed, and what it may touch -----------------------------


def test_the_policy_takes_only_an_event_and_a_movement_manager() -> None:
    """A `deps` parameter would hand this module the learner id, which is the whole point."""
    parameters = inspect.signature(react_to_lesson_event).parameters

    assert list(parameters) == ["event", "movement_manager"]
    assert parameters["movement_manager"].kind is inspect.Parameter.KEYWORD_ONLY
    assert not [p for p in parameters.values() if p.kind in (p.VAR_KEYWORD, p.VAR_POSITIONAL)], (
        "a splat parameter would let a caller smuggle a learner in"
    )


@pytest.mark.parametrize(
    "not_an_event",
    ["completed", "lesson_started", None, {"event": "lesson_started"}, SEEDED_LEARNER, "What time is it?"],
)
def test_a_thing_that_is_not_an_event_is_not_something_the_policy_reacts_to(
    not_an_event: object, loaded: MagicMock
) -> None:
    """An allow-list of one type, so a learner id and a lesson title are equally not events."""
    assert react_to_lesson_event(not_an_event, movement_manager=loaded) is None
    assert loaded.queue_move.call_count == 0


def test_the_policy_may_import_only_what_it_needs_to_choose_and_queue_a_move() -> None:
    """An allow-list of imports, which closes two families at once.

    It keeps the learner out -- core_tools and learners.store are the modules that would
    bring one -- and it keeps a thread, a timer or a polling loop out, which is the
    acceptance criterion about adding no per-frame work. Named as what is permitted, so a
    module nobody thought of is refused by default.
    """
    permitted = {
        "logging",
        "enum",
        "typing",
        # The package itself, because `from reachy_language_tutor.tools import x` names it.
        # Permitting it does not permit its modules: each imported NAME is recorded as its
        # own dotted path below and checked against this list separately, so
        # `from reachy_language_tutor.tools import core_tools` is still refused.
        "reachy_language_tutor.tools",
        "reachy_language_tutor.tools.play_emotion",
        "reachy_language_tutor.dance_emotion_moves",
        # Added deliberately when the exception this module logs was routed through
        # log_safe and where. logging_safety imports sqlite3 and traceback and nothing of
        # this package: no learner, no thread, no timer.
        "reachy_language_tutor.logging_safety",
        "reachy_language_tutor.logging_safety.log_safe",
        "reachy_language_tutor.logging_safety.where",
    }
    source = Path(lesson_feedback.__file__).read_text(encoding="utf-8")

    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            # Both granularities, so importing a MODULE out of a permitted package is
            # still visible. Names that are ordinary attributes rather than submodules
            # (Enum, Any) simply never appear in the permitted list and are covered by
            # their package already being there.
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)
    # Attribute imports out of the standard-library modules already permitted above.
    imported -= {"enum.Enum", "typing.TYPE_CHECKING", "typing.Any"}
    imported -= {"reachy_language_tutor.dance_emotion_moves.EmotionQueueMove"}

    assert imported <= permitted, f"lesson_feedback imports something outside its allow-list: {imported - permitted}"


def test_the_move_is_handed_to_queue_move_and_to_nothing_else(loaded: MagicMock) -> None:
    """queue_move is the single entry point, and this policy never clears or interrupts.

    A reaction that cleared the queue would cancel whatever the tutor was already doing,
    which is the opposite of sparing.
    """
    react_to_lesson_event(LessonEvent.LESSON_STARTED, movement_manager=loaded)

    assert [name for name, _, _ in loaded.mock_calls] == ["queue_move"]


# --- Degrading rather than breaking ---------------------------------------------------


def test_with_the_emotion_library_unavailable_the_policy_queues_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A robot without the optional dependency still teaches and still records."""
    monkeypatch.setattr(lesson_feedback, "EMOTION_AVAILABLE", False)
    movement_manager = MagicMock()

    assert react_to_lesson_event(LessonEvent.RESULT_RECORDED_COMPLETE, movement_manager=movement_manager) is None
    assert movement_manager.queue_move.call_count == 0


def test_with_no_library_loaded_yet_the_policy_queues_nothing_rather_than_downloading_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Building the library downloads a dataset, and this runs inside the voice loop.

    A cold process does without a reaction. Stalling a conversation on a download would be
    the per-frame work the task forbids, wearing a different hat.
    """

    def never(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the lesson path must never construct the emotion library")

    monkeypatch.setattr(lesson_feedback, "EMOTION_AVAILABLE", True)
    monkeypatch.setattr(play_emotion, "RecordedMoves", never)
    monkeypatch.setattr(play_emotion, "_LOADED_LIBRARY", None)
    movement_manager = MagicMock()

    assert react_to_lesson_event(LessonEvent.LESSON_STARTED, movement_manager=movement_manager) is None
    assert movement_manager.queue_move.call_count == 0


def test_an_intent_that_resolves_to_nothing_is_answered_with_stillness(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not play_emotion's random fallback: a reaction nobody chose is not feedback."""
    monkeypatch.setattr(lesson_feedback, "EMOTION_AVAILABLE", True)
    monkeypatch.setattr(lesson_feedback, "EmotionQueueMove", FakeEmotionQueueMove)
    monkeypatch.setattr(play_emotion, "loaded_emotion_library", lambda: FakeRecordedMoves(["nothing_like_it"]))
    movement_manager = MagicMock()

    assert react_to_lesson_event(LessonEvent.LANGUAGE_FINISHED, movement_manager=movement_manager) is None
    assert movement_manager.queue_move.call_count == 0


def test_a_movement_manager_that_raises_still_lets_the_reaction_return(loaded: MagicMock) -> None:
    """The narrow contract the two tools rely on, held in one place so neither can forget."""
    loaded.queue_move.side_effect = RuntimeError("robot busy")

    assert react_to_lesson_event(LessonEvent.LESSON_STARTED, movement_manager=loaded) is None


# --- The two call sites: below the thing that really happened -------------------------


@pytest.mark.asyncio
async def test_finish_lesson_queues_one_move_for_a_result_the_store_really_wrote(
    instance: Path, loaded: MagicMock
) -> None:
    """The honest signal: the nod is tied to the write, not to the tutor's belief about it."""
    deps = _pinned_deps(instance, movement_manager=loaded)

    result = await FinishLesson()(deps, outcome="completed", score=90)

    assert result["recorded"] is True
    assert len(_queued(loaded)) == 1, f"expected one reaction, got {_queued(loaded)}"


@pytest.mark.asyncio
async def test_finish_lesson_queues_nothing_when_no_lesson_was_running(instance: Path, loaded: MagicMock) -> None:
    """Nothing was written, so there is nothing to be pleased about."""
    deps = _deps(current_learner_id=SEEDED_LEARNER, instance_path=instance, movement_manager=loaded)

    result = await FinishLesson()(deps, outcome="completed")

    assert result["recorded"] is False
    assert loaded.queue_move.call_count == 0


@pytest.mark.asyncio
async def test_finish_lesson_queues_nothing_when_the_store_refused_the_write(
    instance: Path, loaded: MagicMock, tmp_path: Path
) -> None:
    """A refused write with a lesson still pinned -- the case a flag would get wrong.

    The session IS running here, so the tool gets past its own guards and is refused by
    the store. That is the branch where a reaction placed a few lines too high would fire
    on a lesson nobody saved.
    """
    deps = _pinned_deps(instance, movement_manager=loaded, instance_path=tmp_path / "no-database-here")

    result = await FinishLesson()(deps, outcome="completed")

    assert result["recorded"] is False
    assert result["reason"] == "storage_unavailable"
    assert loaded.queue_move.call_count == 0, f"a refused write was celebrated with {_queued(loaded)}"


@pytest.mark.asyncio
async def test_finish_lesson_queues_nothing_for_an_outcome_the_store_would_refuse(
    instance: Path, loaded: MagicMock
) -> None:
    """Refused before the store is reached, and equally not something to react to."""
    deps = _pinned_deps(instance, movement_manager=loaded)

    result = await FinishLesson()(deps, outcome="brilliantly")

    assert result["recorded"] is False
    assert loaded.queue_move.call_count == 0


@pytest.mark.asyncio
async def test_start_lesson_queues_one_move_only_when_a_lesson_was_really_pinned(
    instance: Path, loaded: MagicMock
) -> None:
    """A lesson that did not start must not be nodded at."""
    started = await StartLesson()(
        _deps(current_learner_id=SEEDED_LEARNER, instance_path=instance, movement_manager=loaded),
        language="Spanish",
    )
    assert started["started"] is True
    assert len(_queued(loaded)) == 1

    refused_manager = MagicMock()
    refused = await StartLesson()(
        _deps(current_learner_id=SEEDED_LEARNER, instance_path=instance, movement_manager=refused_manager),
        language="Klingon",
    )
    assert refused["started"] is False
    assert refused_manager.queue_move.call_count == 0

    nobody = MagicMock()
    unknown = await StartLesson()(
        _deps(current_learner_id=None, instance_path=instance, movement_manager=nobody), language="Spanish"
    )
    assert unknown["started"] is False
    assert nobody.queue_move.call_count == 0


@pytest.mark.asyncio
async def test_two_results_in_quick_succession_queue_two_moves_and_clear_nothing(
    instance: Path, loaded: MagicMock
) -> None:
    """Moves stack behind one another; a reaction never cancels what is already playing."""
    first = _pinned_deps(instance, movement_manager=loaded)
    assert (await FinishLesson()(first, outcome="completed"))["recorded"] is True

    second = _pinned_deps(instance, lesson_id="es-04-ordering-food", movement_manager=loaded)
    assert (await FinishLesson()(second, outcome="skipped"))["recorded"] is True

    assert len(_queued(loaded)) == 2
    assert [name for name, _, _ in loaded.mock_calls] == ["queue_move", "queue_move"]


@pytest.mark.asyncio
async def test_a_movement_failure_does_not_undo_a_lesson_write_that_already_succeeded(
    instance: Path, loaded: MagicMock
) -> None:
    """The pitfall, measured against the database rather than against the return value.

    A row that exists while the tutor says it does not is the worst outcome available
    here: the learner repeats a lesson the database already counted.
    """
    loaded.queue_move.side_effect = RuntimeError("robot busy")
    deps = _pinned_deps(instance, movement_manager=loaded)

    result = await FinishLesson()(deps, outcome="completed", score=75)

    assert result["recorded"] is True
    progress = store.get_progress(SEEDED_LEARNER, SPANISH, instance_path=instance)
    assert SPANISH_NEXT in [lesson.id for lesson in progress.completed], "the write did not survive"


@pytest.mark.asyncio
async def test_nothing_about_the_learner_or_the_lesson_reaches_a_log_from_the_reaction(
    instance: Path, loaded: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    """A new logger is a new way for a name to reach disk, and it needs its own pin.

    The existing redaction tests filter on their own module's logger name, so they would
    not have seen a leak from this one. That is D3's shape exactly -- the rule obeyed in
    one module and broken in the one above it.
    """
    profile = store.get_profile(SEEDED_LEARNER, instance_path=instance)
    lesson = store.get_lesson(SPANISH_NEXT, instance_path=instance)
    assert profile is not None and lesson is not None

    with caplog.at_level(logging.DEBUG, logger=lesson_feedback.__name__):
        result = await FinishLesson()(_pinned_deps(instance, movement_manager=loaded), outcome="completed")

    assert result["recorded"] is True
    records = [record for record in caplog.records if record.name == lesson_feedback.__name__]
    assert records, "the reaction logged nothing, so this pin would pass vacuously"
    for record in records:
        rendered = f"{record.name} | {record.msg} | {record.args} | {record.getMessage()}"
        assert SEEDED_LEARNER not in rendered, rendered
        assert profile.display_name not in rendered, rendered
        assert SPANISH_NEXT not in rendered, rendered
        assert lesson.title not in rendered, rendered
        assert lesson.objective not in rendered, rendered


# --- Something has to pay for the library, and startup is where ----------------------
#
# The policy asks for the library and never builds one, which is right on the voice path
# and useless on its own: without a warm-up nothing else builds it either until the model
# happens to play an emotion, so a session's first reactions are dropped -- starting with
# the lesson-start nod, which is always the first. W17's review raised exactly that.


def test_warming_the_library_loads_it_once_so_the_first_reaction_is_not_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After the warm-up the read-only accessor answers, which is what the policy asks."""
    built: list[str] = []

    def fake_recorded_moves(dataset: str) -> FakeRecordedMoves:
        built.append(dataset)
        return FakeRecordedMoves()

    monkeypatch.setattr(play_emotion, "EMOTION_AVAILABLE", True)
    monkeypatch.setattr(play_emotion, "RecordedMoves", fake_recorded_moves)
    monkeypatch.setattr(play_emotion, "_LOADED_LIBRARY", None)

    assert play_emotion.loaded_emotion_library() is None, "cold, so a reaction would be dropped"
    assert play_emotion.warm_emotion_library() is True
    assert play_emotion.loaded_emotion_library() is not None, "the warm-up did not make the library askable"

    assert play_emotion.warm_emotion_library() is True
    assert len(built) == 1, f"the dataset was fetched more than once: {built}"


def test_a_library_that_cannot_be_fetched_does_not_stop_a_robot_teaching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A robot with no dataset still runs lessons; only the movement is lost."""

    def explode(dataset: str) -> Any:
        raise RuntimeError("no network in this house")

    monkeypatch.setattr(play_emotion, "EMOTION_AVAILABLE", True)
    monkeypatch.setattr(play_emotion, "RecordedMoves", explode)
    monkeypatch.setattr(play_emotion, "_LOADED_LIBRARY", None)

    assert play_emotion.warm_emotion_library() is False
    assert play_emotion.loaded_emotion_library() is None


def test_the_warm_up_does_nothing_when_the_emotion_library_is_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The optional dependency is optional at startup too."""

    def never(dataset: str) -> Any:
        raise AssertionError("a robot without the emotion library must not try to load one")

    monkeypatch.setattr(play_emotion, "EMOTION_AVAILABLE", False)
    monkeypatch.setattr(play_emotion, "RecordedMoves", never)
    monkeypatch.setattr(play_emotion, "_LOADED_LIBRARY", None)

    assert play_emotion.warm_emotion_library() is False


def test_the_app_warms_the_emotion_library_while_it_is_starting_up() -> None:
    """The wiring, pinned: a helper nobody calls leaves every first reaction silent.

    Read as a syntax tree rather than as text, because the two things worth pinning are
    both structural: that the call is on the startup path -- inside `run`, beside the
    learner-database preparation, rather than merely present somewhere in the file -- and
    that it is wrapped in a real try/except, so a robot that cannot fetch the dataset
    still starts. An earlier version of this test asserted a COMMENT saying so, which
    would have passed with the guard deleted.

    Asserted against the source rather than by running `run`, which needs a robot.
    """
    from reachy_language_tutor import main

    tree = ast.parse(Path(main.__file__).read_text(encoding="utf-8"))
    startup = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "run" and "ensure_learner_database" in ast.dump(node)
        ),
        None,
    )
    assert startup is not None, "could not find the startup path that prepares the learner database"

    def _calls_warm_up(node: ast.AST) -> bool:
        return any(
            isinstance(call.func, ast.Name) and call.func.id == "warm_emotion_library"
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
        )

    assert _calls_warm_up(startup), "nothing warms the emotion library on the startup path"

    guarded = [
        node
        for node in ast.walk(startup)
        if isinstance(node, ast.Try) and node.handlers and any(_calls_warm_up(body) for body in node.body)
    ]
    assert guarded, "the startup warm-up is not inside a try/except, so a failed fetch would stop the app"


@pytest.mark.asyncio
async def test_a_warmed_robot_really_reacts_to_a_recorded_result(
    instance: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole chain, with nothing stubbed between the warm-up and the queued move.

    Every other test here hands the policy a library by patching the accessor it calls.
    That proves the policy and leaves the join untested, which is precisely where the
    review found the defect: the accessor answered None forever because nothing warmed it.
    So this one warms the library the way startup does and then asks whether a real
    finish_lesson actually moves the robot.
    """
    monkeypatch.setattr(play_emotion, "EMOTION_AVAILABLE", True)
    monkeypatch.setattr(play_emotion, "RecordedMoves", lambda dataset: FakeRecordedMoves())
    monkeypatch.setattr(play_emotion, "_LOADED_LIBRARY", None)
    monkeypatch.setattr(lesson_feedback, "EMOTION_AVAILABLE", True)
    monkeypatch.setattr(lesson_feedback, "EmotionQueueMove", FakeEmotionQueueMove)

    movement_manager = MagicMock()
    cold = await FinishLesson()(_pinned_deps(instance, movement_manager=movement_manager), outcome="completed")
    assert cold["recorded"] is True
    assert movement_manager.queue_move.call_count == 0, "a cold robot must not stall on a download"

    assert play_emotion.warm_emotion_library() is True

    warmed_manager = MagicMock()
    warmed = await FinishLesson()(
        _pinned_deps(instance, lesson_id="es-04-ordering-food", movement_manager=warmed_manager),
        outcome="completed",
    )

    assert warmed["recorded"] is True
    assert _queued(warmed_manager) == ["success1"], "a warmed robot still did not react to a recorded result"
