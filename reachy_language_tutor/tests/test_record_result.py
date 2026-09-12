"""Tests for the record_result tool: the write it makes, and the boundary it holds.

CLAUDE.md: "Tools must never accept a learner's identity from the conversation, so
nobody can talk their way into another person's profile." This is the first tool that
WRITES learner data, so it is the first place the boundary failing would corrupt a
record rather than merely disclose one.

Two of its shapes are load-bearing for tests/test_tool_identity_boundary.py, which
auto-discovers this tool and attacks the real dispatch path:

  * the lesson_id enum -- with a free string, _benign_args synthesises "w11", the
    store answers unknown_lesson, and both the steering test (via
    reached_only_validator) and the observability test (via inert) fail, with no
    waiver available for a learner-reading tool. Verified by removing it.
  * the absence of anything wall-clock from the success dict -- that suite calls the
    tool twice with identical arguments and requires identical answers.

These call the tool directly, which pins it in isolation but SKIPS the dispatcher's
unvalidated **args splat; the boundary suite is what holds the boundary for real.
"""

import ast
import inspect
import logging
from typing import Any
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from reachy_language_tutor.tools import record_result as module
from reachy_language_tutor.learners import OUTCOMES, RECORD_REASONS, store
from reachy_language_tutor.learners.models import RecordResultOutcome
from reachy_language_tutor.tools.core_tools import ToolDependencies
from reachy_language_tutor.tools.record_result import RecordResult


SEEDED_LEARNER = store.SEED_LEARNERS[0][0]
SPANISH_NEXT = "es-03-numbers"

IDENTITY_KEYS = (
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
)


@pytest.fixture
def instance(tmp_path: Path) -> Path:
    """Return a prepared learner database at a temporary instance path."""
    assert store.ensure_learner_database(tmp_path).ready is True
    return tmp_path


def _deps(**overrides: Any) -> ToolDependencies:
    """Dependencies with the two required fields stubbed, as the existing tests do."""
    return ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock(), **overrides)


async def _call(_kwargs: dict[str, Any] | None = None, **overrides: Any) -> dict[str, Any]:
    """Invoke the tool: _kwargs is what the model sent, overrides build the deps."""
    return await RecordResult()(_deps(**overrides), **(_kwargs or {}))


def _row_count(instance: Path) -> int:
    """How many attempts are stored, so a test can prove nothing was written."""
    connection = store.connect(instance)
    try:
        return int(connection.execute("SELECT COUNT(*) AS n FROM lesson_results").fetchone()["n"])
    finally:
        connection.close()


# --- The write it makes, and what it reports back -------------------------------------


@pytest.mark.asyncio
async def test_it_records_the_outcome_and_reports_the_new_standing(instance: Path) -> None:
    """The tutor needs to know the save landed, and where the learner now is."""
    result = await _call(
        {"lesson_id": SPANISH_NEXT, "outcome": "completed"},
        current_learner_id=SEEDED_LEARNER,
        instance_path=instance,
    )

    assert "error" not in result
    assert result["recorded"] is True
    assert result["lesson_id"] == SPANISH_NEXT
    assert result["outcome"] == "completed"
    assert result["language"] == "Spanish"
    assert result["completed_count"] == 3
    assert result["remaining_count"] == 3
    assert result["next_lesson"]["id"] == "es-04-ordering-food"


@pytest.mark.asyncio
async def test_the_store_is_what_decides_the_new_standing(instance: Path) -> None:
    """The database is the source of truth; the tool must not compute its own counts."""
    result = await _call(
        {"lesson_id": SPANISH_NEXT, "outcome": "completed"},
        current_learner_id=SEEDED_LEARNER,
        instance_path=instance,
    )
    progress = store.get_progress(SEEDED_LEARNER, "es", instance_path=instance)
    assert progress is not None

    assert result["completed_count"] == len(progress.completed)
    assert result["remaining_count"] == len(progress.remaining)
    assert result["next_lesson"]["id"] == progress.next_lesson.id


@pytest.mark.asyncio
async def test_recording_the_same_lesson_twice_returns_the_same_answer(instance: Path) -> None:
    """The local proxy for the boundary suite's stability assert.

    completed is built from a DISTINCT set of lesson ids, so a duplicate row collapses.
    If this ever fails, test_every_learner_tool_is_individually_observable fails too.
    """
    args = {"lesson_id": SPANISH_NEXT, "outcome": "completed"}
    first = await _call(args, current_learner_id=SEEDED_LEARNER, instance_path=instance)
    second = await _call(args, current_learner_id=SEEDED_LEARNER, instance_path=instance)

    assert first == second


@pytest.mark.asyncio
async def test_the_answer_carries_no_timestamp_score_or_row_id(instance: Path) -> None:
    """The regression that would silently break the boundary suite's equality check."""
    result = await _call(
        {"lesson_id": SPANISH_NEXT, "outcome": "completed"},
        current_learner_id=SEEDED_LEARNER,
        instance_path=instance,
    )

    # "id" is forbidden at the top level, where it could only mean the stored row's id.
    # Inside next_lesson it is the LESSON id -- catalog data, and stable across calls.
    forbidden = {"recorded_at", "score", "attempt", "attempts", "attempt_id", "row_id", "id"}
    assert forbidden.isdisjoint(result)
    assert (forbidden - {"id"}).isdisjoint(result["next_lesson"])
    # No value may be a millisecond epoch, however it is spelled.
    for value in result.values():
        assert not (isinstance(value, int) and not isinstance(value, bool) and value > 10**11)


@pytest.mark.asyncio
async def test_two_learners_get_different_answers_for_the_same_lesson(instance: Path) -> None:
    """The local proxy for the boundary suite's observability assert."""
    connection = store.connect(instance)
    connection.execute(
        "INSERT INTO learners (id, display_name, created_at) VALUES (?, ?, ?)",
        ("housemate-w11", "Zerelda Quackenbush", store.utc_now_ms()),
    )
    connection.commit()
    connection.close()

    args = {"lesson_id": SPANISH_NEXT, "outcome": "completed"}
    as_seeded = await _call(args, current_learner_id=SEEDED_LEARNER, instance_path=instance)
    as_housemate = await _call(args, current_learner_id="housemate-w11", instance_path=instance)

    assert as_seeded != as_housemate
    assert as_seeded["completed_count"] != as_housemate["completed_count"]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["partial", "skipped"])
async def test_a_partial_or_skipped_outcome_does_not_advance_the_next_lesson(instance: Path, outcome: str) -> None:
    """Only 'completed' finishes a lesson -- a database rule, not the model's opinion."""
    result = await _call(
        {"lesson_id": SPANISH_NEXT, "outcome": outcome},
        current_learner_id=SEEDED_LEARNER,
        instance_path=instance,
    )

    assert result["recorded"] is True
    assert result["next_lesson"]["id"] == SPANISH_NEXT


@pytest.mark.asyncio
async def test_a_subsequent_get_progress_reflects_the_recorded_result(instance: Path) -> None:
    """Acceptance criterion 4, exercised through the real get_progress tool."""
    from reachy_language_tutor.tools.get_progress import GetProgress

    before = await GetProgress()(_deps(current_learner_id=SEEDED_LEARNER, instance_path=instance), language="Spanish")
    await _call(
        {"lesson_id": SPANISH_NEXT, "outcome": "completed"},
        current_learner_id=SEEDED_LEARNER,
        instance_path=instance,
    )
    after = await GetProgress()(_deps(current_learner_id=SEEDED_LEARNER, instance_path=instance), language="Spanish")

    assert after["completed_count"] == before["completed_count"] + 1
    assert after["next_lesson"]["id"] != before["next_lesson"]["id"]


@pytest.mark.asyncio
async def test_a_failed_read_back_does_not_turn_into_a_failed_write(
    instance: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The write landed, so it must be reported as landed even if the read-back fails.

    The degraded branch of _standing. Nothing else pins the decision its docstring
    states, and the inverse -- reporting recorded=False while the row sits in
    lesson_results -- is exactly the task's "do not let a failed write look like a
    success" pitfall run backwards: the tutor would re-teach a lesson the database
    already counts as done, forever.
    """
    before = _row_count(instance)
    monkeypatch.setattr(module, "get_lesson", lambda *a, **k: None)

    result = await _call(
        {"lesson_id": SPANISH_NEXT, "outcome": "completed"},
        current_learner_id=SEEDED_LEARNER,
        instance_path=instance,
    )

    # Reported as saved, because it WAS saved.
    assert result["recorded"] is True
    assert "error" not in result
    assert _row_count(instance) == before + 1
    # And honest about what it could not read back, rather than guessing a figure.
    assert result["language"] is None
    assert result["lesson_title"] is None
    assert result["completed_count"] is None
    assert result["remaining_count"] is None
    assert result["next_lesson"] is None


@pytest.mark.asyncio
async def test_an_unreadable_progress_read_back_also_reports_the_write_as_landed(
    instance: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second way the read-back degrades: the lesson resolves, progress does not."""
    before = _row_count(instance)
    monkeypatch.setattr(module, "get_progress", lambda *a, **k: None)

    result = await _call(
        {"lesson_id": SPANISH_NEXT, "outcome": "completed"},
        current_learner_id=SEEDED_LEARNER,
        instance_path=instance,
    )

    assert result["recorded"] is True
    assert _row_count(instance) == before + 1
    assert result["completed_count"] is None


# --- The identity boundary ------------------------------------------------------------


def test_the_schema_declares_only_a_lesson_and_an_outcome() -> None:
    """An allow-list of exactly two keys; anything else is something the model fills in."""
    schema = RecordResult.parameters_schema

    assert set(schema["properties"]) == {"lesson_id", "outcome"}
    assert schema["required"] == ["lesson_id", "outcome"]


def test_no_identity_shaped_key_can_appear_in_the_schema() -> None:
    """The security boundary from CLAUDE.md, asserted where it would first be broken."""
    declared = set(RecordResult.parameters_schema["properties"])
    exposed = set(RecordResult().spec()["parameters"]["properties"])

    for key in IDENTITY_KEYS:
        assert key not in declared
        assert key not in exposed


@pytest.mark.asyncio
async def test_a_learner_identity_in_kwargs_is_ignored_when_called_directly(instance: Path) -> None:
    """A write tool getting this wrong corrupts another person's record, not just leaks it."""
    connection = store.connect(instance)
    connection.execute(
        "INSERT INTO learners (id, display_name, created_at) VALUES (?, ?, ?)",
        ("housemate-w11", "Zerelda Quackenbush", store.utc_now_ms()),
    )
    connection.commit()
    connection.close()

    poisoned = await _call(
        {
            "lesson_id": SPANISH_NEXT,
            "outcome": "completed",
            "learner_id": "housemate-w11",
            "name": "Zerelda Quackenbush",
            "current_learner_id": "housemate-w11",
        },
        current_learner_id=SEEDED_LEARNER,
        instance_path=instance,
    )

    assert poisoned["recorded"] is True
    # The row landed on the deps learner, and the housemate gained nothing.
    housemate = store.get_progress("housemate-w11", "es", instance_path=instance)
    assert housemate is not None and len(housemate.completed) == 0
    seeded = store.get_progress(SEEDED_LEARNER, "es", instance_path=instance)
    assert seeded is not None and len(seeded.completed) == 3


def test_the_tool_accepts_no_named_parameter_beyond_its_dependencies() -> None:
    """A named parameter would be a second way in; kwargs keeps the surface two keys wide."""
    parameters = inspect.signature(RecordResult.__call__).parameters

    assert list(parameters) == ["self", "deps", "kwargs"]
    assert parameters["kwargs"].kind is inspect.Parameter.VAR_KEYWORD


def test_the_module_mentions_kwargs_only_to_read_the_lesson_and_outcome_keys() -> None:
    """The allow-list is the access SHAPE, not a list of spellings to refuse.

    Same guard as test_get_progress.py, widened to the two keys this tool legitimately
    reads. It permits one access form and refuses every other mention of the name --
    kwargs.pop, kwargs.items, dict(kwargs), setdefault, aliasing and **kwargs
    forwarding all fail it.

    The bound, stated rather than overclaimed: this reads the module's SYNTAX, so
    string-mediated dynamic reads alongside the permitted calls (locals()["kwargs"],
    eval, sys._getframe) are not ast.Name mentions and pass it.
    tests/test_tool_identity_boundary.py is the behavioural control that catches an
    identity read however it was spelled.
    """
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}

    mentions = [node for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id == "kwargs"]
    assert mentions, "the tool must actually read its arguments, or this test proves nothing"

    read: set[str] = set()
    for mention in mentions:
        attribute = parents.get(mention)
        assert isinstance(attribute, ast.Attribute) and attribute.attr == "get", (
            "kwargs may only be read via .get(); found another access form"
        )
        call = parents.get(attribute)
        assert isinstance(call, ast.Call) and call.func is attribute
        assert len(call.args) == 1
        assert isinstance(call.args[0], ast.Constant)
        read.add(str(call.args[0].value))

    assert read == {"lesson_id", "outcome"}


@pytest.mark.asyncio
async def test_a_notes_argument_is_ignored_and_never_stored(instance: Path) -> None:
    """Ignore a notes argument entirely, rather than storing or echoing it.

    The task declared a notes field; there is no column for it, and it would be
    free-text commentary about a child. Nothing reads it, so it is ignored
    structurally rather than by policy.
    """
    commentary = "Ana was distracted and kept fidgeting today"

    result = await _call(
        {"lesson_id": SPANISH_NEXT, "outcome": "completed", "notes": commentary},
        current_learner_id=SEEDED_LEARNER,
        instance_path=instance,
    )

    assert result["recorded"] is True
    assert commentary not in str(result)
    assert "notes" not in RecordResult.parameters_schema["properties"]


# --- Failure modes return an error dict, never an exception ---------------------------


@pytest.mark.asyncio
async def test_no_current_learner_returns_an_error_dict_and_writes_nothing(instance: Path) -> None:
    """Nobody recognised yet is a fact about the robot's state, not a lesson result."""
    before = _row_count(instance)

    result = await _call(
        {"lesson_id": SPANISH_NEXT, "outcome": "completed"}, current_learner_id=None, instance_path=instance
    )

    assert result["recorded"] is False
    assert "error" in result
    assert _row_count(instance) == before


@pytest.mark.asyncio
async def test_an_unknown_lesson_returns_an_error_dict_and_writes_nothing(instance: Path) -> None:
    """Acceptance criterion 5, with the row count proving the 'writes nothing' half."""
    before = _row_count(instance)

    result = await _call(
        {"lesson_id": "es-99-nonexistent", "outcome": "completed"},
        current_learner_id=SEEDED_LEARNER,
        instance_path=instance,
    )

    assert result["recorded"] is False
    assert "error" in result
    assert _row_count(instance) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [None, "", "abandoned", "COMPLETED", 7, ["completed"], "done"])
async def test_an_unknown_outcome_is_refused_without_touching_the_store(
    instance: Path, outcome: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acceptance criterion 2, and the responsiveness pitfall.

    'abandoned' is in the list on purpose: the task specified it, and the database's
    CHECK rejects it, so it must be refused here rather than sent down to fail.
    """

    def _fail(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("the store was called for an outcome it could never accept")

    monkeypatch.setattr(module, "record_result", _fail)

    result = await _call(
        {"lesson_id": SPANISH_NEXT, "outcome": outcome},
        current_learner_id=SEEDED_LEARNER,
        instance_path=instance,
    )

    assert result["recorded"] is False
    assert "error" in result


@pytest.mark.asyncio
async def test_a_missing_lesson_is_refused_without_touching_the_store(
    instance: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bad argument must cost no database work on the weak onboard computer."""

    def _fail(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("the store was called for a lesson id that could never match")

    monkeypatch.setattr(module, "record_result", _fail)

    result = await _call({"outcome": "completed"}, current_learner_id=SEEDED_LEARNER, instance_path=instance)

    assert result["recorded"] is False
    assert "error" in result


@pytest.mark.asyncio
async def test_an_unknown_learner_in_deps_is_reported_not_silently_succeeded(instance: Path) -> None:
    """An id no learner has must not look like a saved result."""
    result = await _call(
        {"lesson_id": SPANISH_NEXT, "outcome": "completed"},
        current_learner_id="nobody-here",
        instance_path=instance,
    )

    assert result["recorded"] is False
    assert "error" in result


@pytest.mark.asyncio
async def test_an_unreadable_store_says_so_and_does_not_claim_success(tmp_path: Path) -> None:
    """Breakage must never read as a save."""
    result = await _call(
        {"lesson_id": SPANISH_NEXT, "outcome": "completed"},
        current_learner_id=SEEDED_LEARNER,
        instance_path=tmp_path / "nonexistent",
    )

    assert result["recorded"] is False
    assert "error" in result


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", RECORD_REASONS)
async def test_every_refusal_reason_has_its_own_answer_and_never_looks_like_success(
    instance: Path, reason: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed write must not look like a success -- for every reason the store has."""
    monkeypatch.setattr(
        module, "record_result", lambda *a, **k: RecordResultOutcome(recorded=False, reason=reason, attempt=None)
    )

    result = await _call(
        {"lesson_id": SPANISH_NEXT, "outcome": "completed"},
        current_learner_id=SEEDED_LEARNER,
        instance_path=instance,
    )

    assert result["recorded"] is False
    assert len(result["error"].strip()) > 20
    assert "completed_count" not in result


def test_the_refusal_table_covers_every_reason_the_store_can_return() -> None:
    """A new reason upstream must fail loudly here, not fall into a generic string."""
    assert set(module._REFUSALS) == set(RECORD_REASONS)


@pytest.mark.asyncio
@pytest.mark.parametrize("lesson_id", [None, 7, b"es", "", "   ", ["es-01-greetings"], {"a": 1}, True])
async def test_it_never_raises_for_any_argument_shape(instance: Path, lesson_id: Any) -> None:
    """A tool raising into the conversation loop breaks the turn; it returns a dict."""
    result = await _call(
        {"lesson_id": lesson_id, "outcome": "completed"},
        current_learner_id=SEEDED_LEARNER,
        instance_path=instance,
    )

    assert isinstance(result, dict)
    assert "error" in result
    assert result["recorded"] is False


# --- It is actually reachable from the conversation --------------------------------------


def test_record_result_is_listed_in_the_locked_profile() -> None:
    """A tool absent from default_tools is not available to the conversation at all."""
    from reachy_language_tutor import config
    from reachy_language_tutor.profile_store import read_profile_from_directory

    name = config.LOCKED_PROFILE
    profile = read_profile_from_directory(name, config.DEFAULT_PROFILES_DIRECTORY / name)
    assert "record_result" in profile.default_tools


def test_the_tool_name_matches_its_module_filename() -> None:
    """The runtime loader imports tools.<name>, so a mismatch silently fails to load."""
    assert RecordResult.name == "record_result"
    assert Path(module.__file__).stem == RecordResult.name


def test_the_runtime_loader_actually_registers_the_tool() -> None:
    """The loader imports tools.<name>, so only this proves the file is wired up."""
    from reachy_language_tutor.tools import core_tools

    registry = core_tools._build_tool_registry(core_tools._load_enabled_tools(["record_result"], []))
    assert sorted(registry) == ["record_result"]
    loaded = type(registry["record_result"])
    assert (loaded.__module__, loaded.__name__) == (RecordResult.__module__, RecordResult.__name__)
    assert set(registry["record_result"].spec()["parameters"]["properties"]) == {"lesson_id", "outcome"}


def test_the_declared_lesson_enum_matches_the_seeded_catalog() -> None:
    """A static hint can go stale; this is what keeps it honest against the seed data."""
    declared = RecordResult.parameters_schema["properties"]["lesson_id"]["enum"]

    assert sorted(declared) == sorted(lesson[0] for lesson in store.SEED_LESSONS)


def test_the_declared_outcome_enum_matches_the_stores_vocabulary() -> None:
    """Constraining the model to a word the database rejects is worse than free text."""
    declared = RecordResult.parameters_schema["properties"]["outcome"]["enum"]

    assert tuple(declared) == OUTCOMES
    assert "abandoned" not in declared


def test_the_benign_arguments_the_boundary_suite_synthesises_name_a_real_lesson() -> None:
    """Why the enums may not be reordered away or freed -- stated where it would be edited.

    tests/test_tool_identity_boundary.py's _benign_args takes enum[0] for a required
    property. If that stops naming a real lesson and a real outcome, the tool answers
    with an error dict and two tests over there fail with no waiver available.
    """
    properties = RecordResult.parameters_schema["properties"]
    lesson_id = properties["lesson_id"]["enum"][0]
    outcome = properties["outcome"]["enum"][0]

    assert lesson_id in {lesson[0] for lesson in store.SEED_LESSONS}
    assert outcome in OUTCOMES
    assert "default" not in properties["lesson_id"], "a default would be used before the enum"
    assert "default" not in properties["outcome"], "a default would be used before the enum"


@pytest.mark.asyncio
async def test_the_profile_persona_tells_the_tutor_to_save_results() -> None:
    """Shipping the tool while the persona never calls it would deliver nothing."""
    from reachy_language_tutor import config

    text = (config.DEFAULT_PROFILES_DIRECTORY / config.LOCKED_PROFILE / "profile.md").read_text(encoding="utf-8")
    assert "record_result" in text


@pytest.mark.asyncio
async def test_nothing_personal_is_logged(instance: Path, caplog: pytest.LogCaptureFixture) -> None:
    """~20 real households use this; names, ids and lesson data must not reach the log.

    The lesson id matters as much as the name here: test_log_redaction.py already pins
    that a lesson id must not appear in a log line even inside an error message.
    """
    with caplog.at_level(logging.DEBUG):
        await _call(
            {"lesson_id": SPANISH_NEXT, "outcome": "completed"},
            current_learner_id=SEEDED_LEARNER,
            instance_path=instance,
        )
        await _call(
            {"lesson_id": "es-99-nonexistent", "outcome": "completed"},
            current_learner_id=SEEDED_LEARNER,
            instance_path=instance,
        )

    messages = " ".join(record.getMessage() for record in caplog.records)
    assert SEEDED_LEARNER not in messages
    assert store.SEED_LEARNERS[0][1] not in messages
    assert SPANISH_NEXT not in messages
    assert "es-99-nonexistent" not in messages
    for lesson in store.SEED_LESSONS:
        assert lesson[3] not in messages
