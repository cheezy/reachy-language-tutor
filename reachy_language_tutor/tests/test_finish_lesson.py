"""Tests for the finish_lesson tool: the write it makes, and the lesson it may not choose.

This is the only conversation-reachable writer to lesson_results, and it is the reason
W16 deleted the other one. Its whole point is that the lesson being recorded comes from
application state -- the session start_lesson pinned -- rather than from the
conversation. A write keyed by a model-supplied lesson id records what the model
believes happened, which is exactly the thing the architecture says it must not decide.

Ported from tests/test_record_result.py, which this file replaces. Three of that file's
tests were genuinely dropped because their subject no longer exists (the lesson_id
parameter, its catalog enum, and the benign-argument synthesis that depended on it);
everything else moved here, including the AST kwargs guard, the identity-key schema
sweep, the refusal-table pin against RECORD_REASONS and the log-redaction test.

Two shapes are load-bearing for tests/test_tool_identity_boundary.py, which
auto-discovers this tool and attacks the real dispatch path:

  * the outcome enum -- _benign_args synthesises enum[0], so a free string would have
    the store answer invalid_outcome and the attack would never reach the write;
  * the absence of anything wall-clock from the success dict -- that suite calls the
    tool twice with identical arguments and requires identical answers.

These call the tool directly, which pins it in isolation but SKIPS the dispatcher's
unvalidated **args splat; the boundary suite is what holds the boundary for real.
"""

import ast
import inspect
import logging
import sqlite3
from typing import Any
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Bound at module scope, which is what D28 made possible -- see tests/tools_module_graph.py.
from reachy_language_tutor.tools import core_tools
from reachy_language_tutor.tools import finish_lesson as module
from reachy_language_tutor.learners import OUTCOMES, RECORD_REASONS, store, get_progress, record_result
from reachy_language_tutor.lesson_session import LessonSessionHolder
from reachy_language_tutor.tools.core_tools import ToolDependencies
from reachy_language_tutor.tools.finish_lesson import FinishLesson


SEEDED_LEARNER = store.SEED_LEARNERS[0][0]
OTHER_LEARNER = "somebody-else"
# The seeded learner's actual next Spanish lesson, which is what this constant has
# always claimed to be. It moved when the six converted Cycles of the Spanish FAST
# took positions 1-6: es-03-numbers is now position 9 and sits behind six lessons
# nobody has attempted, so pinning it here would have made the
# partial-does-not-advance test below pass for the wrong reason -- the learner would
# be held back by the NEW lessons rather than by their own partial.
SPANISH_NEXT = "es-fast-01-getting-started-in-class"
SPANISH = "es"

# Identity-shaped keys, and LESSON-shaped keys beside them. The lesson half is new: as
# of W16 the lesson a result is recorded against is application state exactly as the
# learner is, so a lesson parameter would be the same breach one fact along.
FORBIDDEN_PARAMETERS = (
    "learner_id",
    "learner",
    "learner_name",
    "name",
    "display_name",
    "user_id",
    "username",
    "current_learner_id",
    "person_id",
    "profile_id",
    "student_id",
    "speaker",
    "who",
    "id",
    "email",
    "lesson_id",
    "lesson",
    "lesson_name",
    "lesson_title",
    "position",
)


@pytest.fixture
def instance(tmp_path: Path) -> Path:
    """Return a prepared learner database at a temporary instance path."""
    assert store.ensure_learner_database(tmp_path).ready is True
    return tmp_path


def _deps(**overrides: Any) -> ToolDependencies:
    """Build dependencies the way main.build_tool_dependencies does, fields stubbed."""
    overrides.setdefault("lesson_session", LessonSessionHolder(overrides.get("current_learner_id")))
    return ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock(), **overrides)


def _pinned_deps(instance: Path, lesson_id: str = SPANISH_NEXT, **overrides: Any) -> ToolDependencies:
    """Dependencies with a lesson already running, which is what production has here."""
    overrides.setdefault("current_learner_id", SEEDED_LEARNER)
    overrides.setdefault("instance_path", instance)
    deps = _deps(**overrides)
    deps.lesson_session.open(lesson_id=lesson_id, language_code=SPANISH)
    return deps


async def _call(_kwargs: dict[str, Any] | None = None, **overrides: Any) -> dict[str, Any]:
    """Invoke the tool: _kwargs is what the model sent, overrides build the deps."""
    return await FinishLesson()(_deps(**overrides), **(_kwargs or {}))


def _row_count(instance: Path) -> int:
    """How many attempts are stored, so a test can prove nothing was written."""
    connection = store.connect(instance)
    try:
        return int(connection.execute("SELECT COUNT(*) AS n FROM lesson_results").fetchone()["n"])
    finally:
        connection.close()


def _attempts_for(instance: Path, lesson_id: str) -> list[Any]:
    """Every stored attempt at one lesson, oldest first, for this suite's learner.

    Sorted here because the store returns attempts newest-first, and a test asserting
    on "the one just written" wants the last element rather than the first. Sorting
    rather than reversing, so a future change of order upstream cannot quietly flip
    what these assertions mean.
    """
    progress = get_progress(SEEDED_LEARNER, SPANISH, instance_path=instance)
    assert progress is not None
    matching = [attempt for attempt in progress.attempts if attempt.lesson_id == lesson_id]
    return sorted(matching, key=lambda attempt: attempt.recorded_at)


# --- The schema is an allow-list, and the lesson is not in it -------------------------


def test_the_schema_declares_only_an_outcome_and_a_score() -> None:
    """How it went is the only thing the conversation may choose."""
    assert set(FinishLesson.parameters_schema["properties"]) == {"outcome", "score"}


def test_only_the_outcome_is_required() -> None:
    """A score is a judgement the tutor may not have; a missing one is not an error."""
    assert FinishLesson.parameters_schema["required"] == ["outcome"]


@pytest.mark.parametrize("forbidden", FORBIDDEN_PARAMETERS)
def test_no_identity_or_lesson_shaped_key_appears_in_the_schema(forbidden: str) -> None:
    """A lesson parameter is the same breach as a learner parameter, one fact along."""
    assert forbidden not in FinishLesson.parameters_schema["properties"]


def test_the_declared_outcome_enum_is_the_stores_vocabulary_in_order() -> None:
    """OUTCOMES itself, not a copy, so the enum and schema.sql's CHECK cannot drift."""
    assert FinishLesson.parameters_schema["properties"]["outcome"]["enum"] == list(OUTCOMES)


def test_neither_property_declares_a_default() -> None:
    """Keep both properties free of a default value.

    _benign_args reads `default` before `enum`, and a materialised default here would
    record a lesson nobody finished.
    """
    for spec in FinishLesson.parameters_schema["properties"].values():
        assert "default" not in spec


def test_the_declared_score_bounds_are_the_bounds_the_store_enforces(instance: Path) -> None:
    """The declared range is a hint to the model, so it must match the real rule.

    The dispatcher validates no schema, so these numbers enforce nothing by themselves.
    Asking the store what it really accepts is what stops them drifting into a lie.
    """
    spec = FinishLesson.parameters_schema["properties"]["score"]
    low, high = spec["minimum"], spec["maximum"]

    assert record_result(SEEDED_LEARNER, SPANISH_NEXT, "partial", score=low, instance_path=instance).recorded is True
    assert record_result(SEEDED_LEARNER, SPANISH_NEXT, "partial", score=high, instance_path=instance).recorded is True
    below = record_result(SEEDED_LEARNER, SPANISH_NEXT, "partial", score=low - 1, instance_path=instance)
    above = record_result(SEEDED_LEARNER, SPANISH_NEXT, "partial", score=high + 1, instance_path=instance)
    assert below.recorded is False and below.reason == "invalid_score"
    assert above.recorded is False and above.reason == "invalid_score"


# --- What it writes, and against which lesson -----------------------------------------


@pytest.mark.asyncio
async def test_it_records_the_lesson_the_app_pinned(instance: Path) -> None:
    """The whole point: the write names what ran, not what the conversation says ran."""
    deps = _pinned_deps(instance)

    result = await FinishLesson()(deps, outcome="completed")

    assert result["recorded"] is True
    assert result["outcome"] == "completed"
    # One row, because the pinned lesson is now Cycle 10 and the seeded learner has
    # never attempted it. The "partial" that used to head this list belonged to
    # es-03-numbers, which is a different lesson -- and it must still be sitting
    # there untouched, since this write names the pin and nothing else.
    assert [a.outcome for a in _attempts_for(instance, SPANISH_NEXT)] == ["completed"]
    assert [a.outcome for a in _attempts_for(instance, "es-03-numbers")] == ["partial"]


@pytest.mark.asyncio
async def test_a_lesson_id_in_kwargs_does_not_change_which_lesson_is_recorded(instance: Path) -> None:
    """The pitfall, pinned: no fallback, and no override either."""
    deps = _pinned_deps(instance)

    result = await FinishLesson()(deps, outcome="completed", lesson_id="es-06-shopping", lesson="es-06-shopping")

    assert result["recorded"] is True
    assert _attempts_for(instance, "es-06-shopping") == []
    assert [a.outcome for a in _attempts_for(instance, SPANISH_NEXT)][-1] == "completed"


@pytest.mark.asyncio
async def test_an_identity_in_kwargs_is_ignored_when_called_directly(instance: Path) -> None:
    """The injected id belongs to nobody; the write must land on the seeded learner."""
    deps = _pinned_deps(instance)

    result = await FinishLesson()(deps, outcome="completed", learner_id=OTHER_LEARNER, user_id=OTHER_LEARNER)

    assert result["recorded"] is True
    assert [a.learner_id for a in _attempts_for(instance, SPANISH_NEXT)] == [SEEDED_LEARNER]


@pytest.mark.asyncio
async def test_the_answer_carries_no_lesson_id_for_the_model_to_reuse(instance: Path) -> None:
    """No tool accepts a lesson id any more, so handing one back only reopens it."""
    deps = _pinned_deps(instance)

    result = await FinishLesson()(deps, outcome="partial")

    assert "lesson_id" not in result
    assert SPANISH_NEXT not in str(result)
    assert result["next_lesson"] is None or "id" not in result["next_lesson"]


@pytest.mark.asyncio
async def test_the_answer_carries_nothing_wall_clock_or_row_shaped(instance: Path) -> None:
    """The boundary suite calls twice with identical arguments and demands equality."""
    deps = _pinned_deps(instance)

    result = await FinishLesson()(deps, outcome="completed")

    for absent in ("recorded_at", "attempt_id", "row_id", "id", "attempts", "already_recorded", "score"):
        assert absent not in result


@pytest.mark.asyncio
async def test_an_optional_score_reaches_the_stored_attempt(instance: Path) -> None:
    """A score the tutor judged is the learner's data and has to arrive intact."""
    deps = _pinned_deps(instance)

    await FinishLesson()(deps, outcome="completed", score=88)

    assert _attempts_for(instance, SPANISH_NEXT)[-1].score == 88


@pytest.mark.asyncio
async def test_omitting_the_score_stores_no_score(instance: Path) -> None:
    """Not judging is not the same as judging zero."""
    deps = _pinned_deps(instance)

    await FinishLesson()(deps, outcome="completed")

    assert _attempts_for(instance, SPANISH_NEXT)[-1].score is None


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", OUTCOMES)
async def test_every_outcome_in_the_vocabulary_is_accepted(outcome: str, instance: Path) -> None:
    """The declared enum and the accepted set are the same set, proved per word."""
    deps = _pinned_deps(instance)

    result = await FinishLesson()(deps, outcome=outcome)

    assert result["recorded"] is True


@pytest.mark.asyncio
async def test_a_partial_outcome_does_not_advance_the_next_lesson(instance: Path) -> None:
    """The database decides what counts as done, and partial does not."""
    deps = _pinned_deps(instance)

    result = await FinishLesson()(deps, outcome="partial")

    assert result["next_lesson"] is not None
    assert result["next_lesson"]["title"] == "Getting started"


@pytest.mark.asyncio
async def test_a_subsequent_get_progress_reflects_the_recorded_result(instance: Path) -> None:
    """A write nobody can read back is not a write."""
    deps = _pinned_deps(instance)
    before = get_progress(SEEDED_LEARNER, SPANISH, instance_path=instance)
    assert before is not None

    await FinishLesson()(deps, outcome="completed")

    after = get_progress(SEEDED_LEARNER, SPANISH, instance_path=instance)
    assert after is not None
    assert len(after.completed) == len(before.completed) + 1
    assert after.next_lesson is not None and after.next_lesson.id != SPANISH_NEXT


# --- The session lifecycle: cleared on a write, never otherwise -----------------------


@pytest.mark.asyncio
async def test_with_no_lesson_pinned_it_returns_an_error_dict_and_writes_nothing(instance: Path) -> None:
    """The fallback for "nothing is running" is to say so, never to guess a lesson."""
    before = _row_count(instance)

    result = await _call({"outcome": "completed"}, current_learner_id=SEEDED_LEARNER, instance_path=instance)

    assert result["recorded"] is False
    assert result["reason"] == "no_lesson_running"
    assert result["error"]
    assert _row_count(instance) == before


@pytest.mark.asyncio
async def test_a_lesson_pinned_for_another_learner_is_refused_and_writes_nothing(instance: Path) -> None:
    """Somebody else's lesson must not be recorded against whoever is present."""
    before = _row_count(instance)
    smuggled = LessonSessionHolder(OTHER_LEARNER)
    smuggled.open(lesson_id=SPANISH_NEXT, language_code=SPANISH)
    deps = _deps(current_learner_id=SEEDED_LEARNER, instance_path=instance, lesson_session=smuggled)

    result = await FinishLesson()(deps, outcome="completed")

    assert result["reason"] == "no_lesson_running"
    assert _row_count(instance) == before


@pytest.mark.asyncio
async def test_finishing_does_not_clear_a_lesson_pinned_for_someone_else(instance: Path) -> None:
    """Otherwise anyone could wipe another person's lesson by saying they were done."""
    smuggled = LessonSessionHolder(OTHER_LEARNER)
    smuggled.open(lesson_id=SPANISH_NEXT, language_code=SPANISH)
    deps = _deps(current_learner_id=SEEDED_LEARNER, instance_path=instance, lesson_session=smuggled)

    await FinishLesson()(deps, outcome="completed")

    assert smuggled.read_for(OTHER_LEARNER) is not None


@pytest.mark.asyncio
async def test_a_successful_record_clears_the_session(instance: Path) -> None:
    """Nothing is running once it has been saved, which is what stops a double write."""
    deps = _pinned_deps(instance)

    await FinishLesson()(deps, outcome="completed")

    assert deps.lesson_session.read_for(SEEDED_LEARNER) is None


@pytest.mark.asyncio
async def test_an_immediate_second_call_writes_nothing_and_says_nothing_is_running(instance: Path) -> None:
    """Repeating the call must not record the same lesson twice."""
    deps = _pinned_deps(instance)

    first = await FinishLesson()(deps, outcome="completed")
    after_first = _row_count(instance)
    second = await FinishLesson()(deps, outcome="completed")

    assert first["recorded"] is True
    assert second["recorded"] is False
    assert second["reason"] == "no_lesson_running"
    assert _row_count(instance) == after_first


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [{"outcome": "completed", "score": 101}, {"outcome": "nonsense"}])
async def test_a_refused_write_leaves_the_lesson_still_pinned(kwargs: dict[str, Any], instance: Path) -> None:
    """A learner who tried should not lose the lesson because the write was refused."""
    deps = _pinned_deps(instance)

    result = await FinishLesson()(deps, **kwargs)

    assert result["recorded"] is False
    pinned = deps.lesson_session.read_for(SEEDED_LEARNER)
    assert pinned is not None and pinned.lesson_id == SPANISH_NEXT


@pytest.mark.asyncio
async def test_a_storage_failure_leaves_the_lesson_still_pinned(instance: Path, tmp_path: Path) -> None:
    """The same rule when the fault is the robot's rather than the argument's."""
    deps = _pinned_deps(instance)
    deps.instance_path = tmp_path / "nowhere"

    result = await FinishLesson()(deps, outcome="completed")

    assert result["recorded"] is False
    assert deps.lesson_session.read_for(SEEDED_LEARNER) is not None


@pytest.mark.asyncio
async def test_a_lesson_deleted_from_the_catalog_is_refused_and_stays_pinned(instance: Path) -> None:
    """Clear only on a write that happened -- a rule with one exception is the one that breaks.

    The learner is left with a pin that will refuse until the next start_lesson
    replaces it, which open() does by last-open-wins. That is the recoverable half of
    the trade-off, and it is deliberate.
    """
    deps = _pinned_deps(instance)
    connection = sqlite3.connect(store.learner_db_path_for_instance(instance))
    connection.execute("DELETE FROM lesson_results WHERE lesson_id = ?", (SPANISH_NEXT,))
    connection.execute("DELETE FROM lessons WHERE id = ?", (SPANISH_NEXT,))
    connection.commit()
    connection.close()

    result = await FinishLesson()(deps, outcome="completed")

    assert result["reason"] == "unknown_lesson"
    assert deps.lesson_session.read_for(SEEDED_LEARNER) is not None


# --- Refusals: one code per meaning, and none of them reads like success --------------


@pytest.mark.asyncio
async def test_no_current_learner_is_refused_before_the_session_is_read(instance: Path) -> None:
    """Nobody identified must mean nobody served."""
    result = await _call({"outcome": "completed"}, instance_path=instance)

    assert result["reason"] == "no_current_learner"


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [None, "", "abandoned", "COMPLETED", 7, ["completed"], True])
async def test_an_outcome_outside_the_vocabulary_is_refused_without_touching_the_store(
    outcome: Any, instance: Path
) -> None:
    """An allow-list against the store's own words, checked before any database work."""
    deps = _pinned_deps(instance)
    before = _row_count(instance)

    result = await FinishLesson()(deps, outcome=outcome)

    assert result["reason"] == "invalid_outcome"
    assert _row_count(instance) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("score", [-1, 101, "90", 90.5, True, []])
async def test_a_score_the_store_will_not_take_is_refused_with_its_own_reason_code(score: Any, instance: Path) -> None:
    """The tool checks no score itself; the store's rule is the only rule."""
    deps = _pinned_deps(instance)

    result = await FinishLesson()(deps, outcome="completed", score=score)

    assert result["recorded"] is False
    assert result["reason"] == "invalid_score"


@pytest.mark.asyncio
async def test_an_unreadable_store_says_so_and_does_not_claim_success(instance: Path, tmp_path: Path) -> None:
    """A fault in the robot, said as one, and never as a saved lesson."""
    deps = _pinned_deps(instance)
    deps.instance_path = tmp_path / "nowhere"

    result = await FinishLesson()(deps, outcome="completed")

    assert result["recorded"] is False
    assert "saved" in result["error"] or "records" in result["error"]


def test_the_refusal_table_covers_every_reason_the_store_can_return() -> None:
    """A new reason upstream must fail here, not fall into a generic sentence."""
    assert set(RECORD_REASONS) <= set(module._REFUSALS)


def test_every_refusal_says_nothing_was_saved_rather_than_reading_like_success() -> None:
    """The worst outcome this tool has is a refusal the tutor hears as a success."""
    for reason, sentence in module._REFUSALS.items():
        lowered = sentence.lower()
        # An allow-list of the ways a refusal may end: it either says nothing was
        # saved, or -- for the one reason where there was nothing to save in the first
        # place -- says that. Anything else is a sentence a tutor could read aloud as
        # a success, which is the worst outcome this tool has.
        permitted = ("not saved", "not recorded", "nothing was saved", "nothing for me to save")
        assert any(phrase in lowered for phrase in permitted), f"{reason}: {sentence}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs", [{}, {"outcome": None}, {"outcome": object()}, {"outcome": "completed", "score": object()}]
)
async def test_it_never_raises_for_any_argument_shape(kwargs: dict[str, Any], instance: Path) -> None:
    """An exception here is rendered to the model by _dispatch_tool_call, not handled."""
    deps = _pinned_deps(instance)

    result = await FinishLesson()(deps, **kwargs)

    assert result["recorded"] is False


# --- The boundary, held in this tool's own source ------------------------------------


def test_the_tool_takes_no_named_parameter_beyond_its_dependencies() -> None:
    """Anything named in the signature is something the dispatcher's splat can fill."""
    parameters = inspect.signature(FinishLesson.__call__).parameters

    assert list(parameters) == ["self", "deps", "kwargs"]
    assert parameters["kwargs"].kind is inspect.Parameter.VAR_KEYWORD


def test_the_module_mentions_kwargs_only_to_read_the_outcome_and_the_score() -> None:
    """The allow-list is the access SHAPE, not a list of spellings to refuse.

    Ported from test_record_result.py, whose docstring carries the full derivation:
    kwargs.pop, kwargs.items, dict(kwargs)[...], an alias and forwarding **kwargs to a
    helper all read an identity while passing a two-pattern deny-list. So permit one
    thing -- kwargs.get(<a permitted key>) -- and refuse every other mention.

    Same stated bound as the original: this reads SYNTAX, so a string-mediated dynamic
    read is not an ast.Name mention and passes. test_tool_identity_boundary.py is the
    primary control and catches an identity read behaviourally however it was spelled.
    """
    permitted = {"outcome", "score"}
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}

    mentions = [node for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id == "kwargs"]
    assert mentions, "the tool must actually read its arguments, or this test proves nothing"

    for mention in mentions:
        attribute = parents.get(mention)
        assert isinstance(attribute, ast.Attribute) and attribute.attr == "get", (
            "kwargs may only be read via .get(); found another access form"
        )
        call = parents.get(attribute)
        assert isinstance(call, ast.Call) and call.func is attribute
        assert len(call.args) == 1
        key = call.args[0]
        assert isinstance(key, ast.Constant) and key.value in permitted, (
            f"the only keys this module may take from kwargs are {sorted(permitted)}"
        )


def test_the_lesson_recorded_is_read_from_the_session_and_never_from_kwargs() -> None:
    """The positional lesson argument to the store must come from the pinned session."""
    source = Path(module.__file__).read_text(encoding="utf-8")

    assert "session.lesson_id" in source
    assert 'kwargs.get("lesson_id")' not in source
    assert 'kwargs.get("lesson")' not in source


# --- Reachability and the record ------------------------------------------------------


def test_finish_lesson_is_listed_in_the_locked_profile() -> None:
    """A tool absent from the profile cannot be called at all."""
    from reachy_language_tutor import config
    from reachy_language_tutor.profile_store import read_profile_from_directory

    name = config.LOCKED_PROFILE
    assert name is not None
    profile = read_profile_from_directory(name, config.DEFAULT_PROFILES_DIRECTORY / name)

    assert "finish_lesson" in profile.default_tools
    assert "record_result" not in profile.default_tools


def test_record_result_is_no_longer_a_tool_at_all() -> None:
    """Exactly one conversation-reachable writer, enforced rather than asserted.

    The profile is a markdown data file, so "not in default_tools" is one edit away
    from false with no code review on the security property. The module is gone, so
    there is nothing for such an edit to re-enable.
    """
    registry = core_tools._build_tool_registry(core_tools._load_enabled_tools(["finish_lesson"], []))

    assert "record_result" not in registry
    assert not (Path(module.__file__).parent / "record_result.py").exists()


def test_the_tool_name_matches_its_module_filename() -> None:
    """The loader resolves packaged tools by that correspondence."""
    assert Path(module.__file__).stem == FinishLesson.name


@pytest.mark.asyncio
async def test_nothing_personal_reaches_a_log(instance: Path, caplog: pytest.LogCaptureFixture) -> None:
    """The learner id, the display name, the lesson id and every lesson title.

    The outcome beside a learner id is the pairing the security consideration names, so
    the learner id is checked on every record rather than only on the success line.
    """
    deps = _pinned_deps(instance)
    profile = store.get_profile(SEEDED_LEARNER, instance_path=instance)
    assert profile is not None
    titles = [title for _, _, _, title, _ in store.SEED_LESSONS]

    with caplog.at_level(logging.DEBUG, logger=module.__name__):
        result = await FinishLesson()(deps, outcome="completed")

    assert result["recorded"] is True
    records = [record for record in caplog.records if record.name == module.__name__]
    assert records, "the tool logged nothing, so this pin would pass vacuously"
    for record in records:
        rendered = f"{record.name} | {record.msg} | {record.args} | {record.getMessage()}"
        assert SEEDED_LEARNER not in rendered, rendered
        assert profile.display_name not in rendered, rendered
        assert SPANISH_NEXT not in rendered, rendered
        for title in titles:
            assert title not in rendered, rendered


# --- The evidence gate (D38) ----------------------------------------------------------
#
# A learner who could not say the first of a lesson's fifteen turns said "we're done, I
# completed it", and the lesson was written off on that sentence. CLAUDE.md says the
# model does not decide what is completed; until this gate, it did.
#
# Note what does NOT separate that session from a real one: it ran two minutes across
# twenty conversation turns. Only coverage of the lesson's own lines tells them apart,
# which is why these cases are written in terms of lines said rather than time or talk.


def _pinned_with_lines(instance: Path, lines: tuple[str, ...], **overrides: Any) -> ToolDependencies:
    """Dependencies with a lesson running whose coverage can actually be measured."""
    overrides.setdefault("current_learner_id", SEEDED_LEARNER)
    overrides.setdefault("instance_path", instance)
    deps = _deps(**overrides)
    deps.lesson_session.open(lesson_id=SPANISH_NEXT, language_code=SPANISH, teachable_lines=lines)
    return deps


_SIX_LINES = ("uno", "dos", "tres", "cuatro", "cinco", "seis")


@pytest.mark.asyncio
async def test_a_completion_is_downgraded_when_almost_none_of_the_lesson_was_said(instance: Path) -> None:
    """The D38 session itself: one line reached, completion claimed."""
    deps = _pinned_with_lines(instance, _SIX_LINES)
    deps.lesson_session.note_spoken(SEEDED_LEARNER, "empecemos: uno")

    result = await FinishLesson()(deps, outcome="completed")

    # Recorded, not refused -- a refusal would lose the work the learner did do and
    # leave the tutor arguing with them, which this fix is explicitly not allowed to do.
    assert result["recorded"] is True
    assert result["outcome"] == "partial", "a lesson nobody taught was written down as completed"
    assert result["not_completed_because"] == "too_little_of_the_lesson_was_practised"
    assert [a.outcome for a in _attempts_for(instance, SPANISH_NEXT)] == ["partial"]


@pytest.mark.asyncio
async def test_a_completion_stands_when_the_lesson_really_was_taught(instance: Path) -> None:
    """The other direction, which matters as much: the gate must not block a real one.

    Without this case the gate could refuse every completion and still look correct.
    """
    deps = _pinned_with_lines(instance, _SIX_LINES)
    for line in _SIX_LINES:
        deps.lesson_session.note_spoken(SEEDED_LEARNER, f"repite conmigo: {line}")
        # And the learner answering. Coverage on its own is the tutor's own output, so
        # a completion needs the person it was said to as well.
        deps.lesson_session.note_learner_turn(SEEDED_LEARNER)

    result = await FinishLesson()(deps, outcome="completed")

    assert result["outcome"] == "completed"
    assert "not_completed_because" not in result
    assert [a.outcome for a in _attempts_for(instance, SPANISH_NEXT)] == ["completed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", sorted(set(OUTCOMES) - {"completed"}))
async def test_the_gate_touches_no_outcome_but_completed(instance: Path, outcome: str) -> None:
    """Only 'completed' removes a lesson from a learner's path, so only it is gated."""
    deps = _pinned_with_lines(instance, _SIX_LINES)

    result = await FinishLesson()(deps, outcome=outcome)

    assert result["outcome"] == outcome
    assert "not_completed_because" not in result


@pytest.mark.asyncio
async def test_a_lesson_whose_coverage_cannot_be_measured_is_not_presumed_untaught(instance: Path) -> None:
    """Absence of evidence is reported as absence of evidence, not as a verdict.

    A lesson pinned with no lines to watch for cannot be measured. Reading that as
    "not worked through" would punish a learner for a wiring mistake somewhere above
    them, so the completion stands. start_lesson always supplies the lines -- the case
    below pins that -- so this is a fallback rather than a live path.
    """
    deps = _pinned_deps(instance)

    result = await FinishLesson()(deps, outcome="completed")

    assert result["outcome"] == "completed"
    assert "not_completed_because" not in result


@pytest.mark.asyncio
async def test_the_downgrade_logs_counts_and_never_a_word_of_the_lesson(
    instance: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The evidence is shape; the lesson's words are nobody's business on disk."""
    deps = _pinned_with_lines(instance, _SIX_LINES)
    deps.lesson_session.note_spoken(SEEDED_LEARNER, "uno")

    with caplog.at_level(logging.INFO):
        await FinishLesson()(deps, outcome="completed")

    logged = "\n".join(f"{record.getMessage()} {record.args}" for record in caplog.records)
    assert "downgraded" in logged, "the downgrade has to be visible to an operator at all"
    for line in _SIX_LINES:
        assert line not in logged, f"the lesson line {line!r} reached the log"
    assert SEEDED_LEARNER not in logged


@pytest.mark.asyncio
async def test_reciting_the_whole_lesson_at_a_silent_learner_is_not_a_completion(instance: Path) -> None:
    """The gate's own bypass, closed.

    Coverage is built from what the TUTOR says, so on its own it is the model's output
    grading the model's work: a tutor that recited every line at a child who never
    spoke would clear it. That is the listed security consideration verbatim -- an
    outcome a conversation can talk its way around is the same defect wearing a gate.
    """
    deps = _pinned_with_lines(instance, _SIX_LINES)
    for line in _SIX_LINES:
        deps.lesson_session.note_spoken(SEEDED_LEARNER, line)
    assert deps.lesson_session.coverage_for(SEEDED_LEARNER) == (6, 6, 0), "every line said, nobody there"

    result = await FinishLesson()(deps, outcome="completed")

    assert result["outcome"] == "partial"
    assert result["not_completed_because"] == "too_little_of_the_lesson_was_practised"


@pytest.mark.asyncio
async def test_every_shipped_lesson_that_can_start_can_also_be_measured(instance: Path) -> None:
    """The two gates have to mean the same thing, or the evidence gate goes inert.

    lesson_has_nothing_to_teach decides whether a lesson may START; _lines_worth_hearing
    decides whether it can be MEASURED. While the first counted notes and the second did
    not, a notes-only lesson could start and then record a completion with nothing
    taught -- the D19 family, where a guard does not mean the same thing as the code it
    protects.

    Asserted over every lesson in the shipped catalog rather than the converted ones, so
    a future lesson of an unusual shape fails here rather than silently disabling the
    gate for itself.
    """
    from reachy_language_tutor.learners import get_progress, get_language_catalog
    from reachy_language_tutor.tools.start_lesson import _lines_worth_hearing
    from reachy_language_tutor.tools.get_lesson_content import lesson_has_nothing_to_teach

    checked = 0
    for language in get_language_catalog(instance_path=instance):
        progress = get_progress(SEEDED_LEARNER, language.code, instance_path=instance)
        assert progress is not None
        for lesson in progress.completed + progress.remaining:
            content = store.get_lesson_content(lesson.id, instance_path=instance)
            assert content is not None
            if lesson_has_nothing_to_teach(content):
                continue  # start_lesson refuses it, so it never reaches the gate
            checked += 1
            assert _lines_worth_hearing(content), (
                f"{lesson.id} is allowed to start but has no measurable lines, so a completion "
                "for it would never be checked"
            )
    assert checked, "no startable lesson was examined, so this proves nothing"


def test_a_notes_only_lesson_can_be_measured_as_well_as_started() -> None:
    """The two gates, compared on the shape that separated them.

    The catalog case above passes today whether or not notes are counted, because no
    shipped lesson is notes-only -- so on its own it is a test that cannot fail for the
    reason it names. This one constructs that shape directly, which is what makes the
    pair honest: remove notes from _lines_worth_hearing and this fails immediately.
    """
    from reachy_language_tutor.learners.models import UsageNote, LessonContent
    from reachy_language_tutor.tools.start_lesson import _lines_worth_hearing
    from reachy_language_tutor.tools.get_lesson_content import lesson_has_nothing_to_teach

    notes_only = LessonContent(
        lesson=store.get_lesson(SPANISH_NEXT, instance_path=None) or MagicMock(),
        source=None,
        dialogue_title=None,
        turns=(),
        notes=(UsageNote(number=1, text="El artículo cambia con el sustantivo."),),
        drills=(),
    )

    assert not lesson_has_nothing_to_teach(notes_only), "the start gate would refuse this, so there is no gap"
    assert _lines_worth_hearing(notes_only), (
        "a lesson the start gate lets through has nothing to measure, so the evidence gate goes inert for exactly it"
    )


@pytest.mark.asyncio
async def test_talking_before_the_lesson_starts_is_not_taking_part_in_it(instance: Path) -> None:
    """Participation has to be participation IN the lesson, not merely nearby.

    Counted from the moment the lesson was pinned, the learner half certified only that
    somebody spoke at some point: a few remarks first, then the tutor reciting the whole
    lesson to itself, and the gate opened. Turns are now counted only once some of the
    material has actually been said, so they are replies to teaching rather than
    chatter that happened to precede it.
    """
    deps = _pinned_with_lines(instance, _SIX_LINES)
    for _ in range(6):
        deps.lesson_session.note_learner_turn(SEEDED_LEARNER)
    assert deps.lesson_session.coverage_for(SEEDED_LEARNER) == (0, 6, 0), "chatter before teaching was counted"

    for line in _SIX_LINES:
        deps.lesson_session.note_spoken(SEEDED_LEARNER, line)

    result = await FinishLesson()(deps, outcome="completed")

    assert result["outcome"] == "partial"
    assert result["not_completed_because"] == "too_little_of_the_lesson_was_practised"


def test_matching_keeps_letters_and_digits_and_drops_everything_else() -> None:
    """The allow-list, exercised on punctuation no shipped lesson happens to use yet.

    The point of naming what is permitted rather than what is stripped is that the next
    lesson's guillemets or ampersand are handled by the rule instead of by whoever last
    edited a list of characters. Accents stay, because they are letters and because a
    course typed off page images exists to preserve exactly them.
    """
    from reachy_language_tutor.lesson_session import _for_matching

    assert _for_matching("«Bom dia» — 3.º andar & co.") == "bom dia 3 º andar co"
    assert _for_matching("Portaria! Às suas ordens!") == "portaria às suas ordens"
    # The whole reason accents are not folded away.
    assert _for_matching("manhã") != _for_matching("manha")
    assert _for_matching("¿Qué es esto?") == "qué es esto"
    assert _for_matching("Não, só isso.") == "não só isso"
    # Inverted punctuation goes, the accent stays -- so an unaccented rendering of the
    # same words is NOT the same line.
    assert _for_matching("¿Qué es esto?") != _for_matching("Que es esto?")
