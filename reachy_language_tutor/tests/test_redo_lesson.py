"""The way back from a lesson a learner did not really finish (D38).

The evidence gate in finish_lesson stops the wrong record being written. These cases
are about the learner whose record is already wrong -- the one in the session that
found this defect, who said "I told you I had, but I really hadn't completed it" and
was told the lesson was already recorded.

The property under all of it: a lesson comes back onto the path WITHOUT anything being
deleted, and without this tool writing anything at all.
"""

import json
from typing import Any
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from reachy_language_tutor.tools import core_tools
from reachy_language_tutor.learners import store
from reachy_language_tutor.lesson_session import LessonSessionHolder
from reachy_language_tutor.tools.core_tools import ToolDependencies


SEEDED_LEARNER = store.SEED_LEARNERS[0][0]


@pytest.fixture
def instance(tmp_path: Path) -> Path:
    """Return a prepared learner database, as the app has at startup."""
    assert store.ensure_learner_database(tmp_path).ready is True
    return tmp_path


def _deps(instance: Path) -> ToolDependencies:
    return ToolDependencies(
        reachy_mini=MagicMock(),
        movement_manager=MagicMock(),
        instance_path=instance,
        current_learner_id=SEEDED_LEARNER,
        lesson_session=LessonSessionHolder(SEEDED_LEARNER),
    )


async def _call(deps: ToolDependencies, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    answer = await core_tools.dispatch_tool_call(name, json.dumps(args or {}), deps)
    return json.loads(answer) if isinstance(answer, str) else answer


def _teach(deps: ToolDependencies, instance: Path) -> None:
    """Say the pinned lesson's own lines, standing in for the conversation handler.

    The dialogue AND the drills, each said on its own, which is what the profile tells
    the tutor to do. Dialogue alone used to be enough only because a spoken turn also
    ticked off every drill lifted out of it; each spoken word now counts towards one
    line, so a drill has to be drilled to count.
    """
    session = deps.lesson_session.read_for(SEEDED_LEARNER)
    assert session is not None
    content = store.get_lesson_content(session.lesson_id, instance_path=instance)
    assert content is not None
    said = [turn.text for turn in content.turns] + [drill.target_text or drill.cue for drill in content.drills]
    for line in said:
        deps.lesson_session.note_spoken(SEEDED_LEARNER, line)
        deps.lesson_session.note_learner_turn(SEEDED_LEARNER)


def _rows(instance: Path, lesson_id: str) -> list[str]:
    connection = store.connect(instance)
    try:
        return [
            str(row["outcome"])
            for row in connection.execute(
                "SELECT outcome FROM lesson_results WHERE learner_id = ? AND lesson_id = ? ORDER BY recorded_at, id",
                (SEEDED_LEARNER, lesson_id),
            )
        ]
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_a_wrongly_completed_lesson_can_be_taken_again_and_comes_back(instance: Path) -> None:
    """The whole defect, end to end through the real dispatch path.

    Finish a lesson properly, decide it was wrong, go back to it, get part way, and the
    catalog offers it again -- with every earlier row still on disk.
    """
    deps = _deps(instance)
    started = await _call(deps, "start_lesson", {"language": "Italian"})
    lesson_title = started["lesson"]["title"]
    _teach(deps, instance)
    assert (await _call(deps, "finish_lesson", {"outcome": "completed"}))["outcome"] == "completed"

    moved_on = await _call(deps, "get_progress", {"language": "Italian"})
    assert moved_on["next_lesson"]["title"] != lesson_title, "it did not move on, so there is nothing to undo"

    reopened = await _call(deps, "redo_lesson", {"language": "Italian"})

    assert reopened["reopened"] is True
    assert reopened["lesson"]["title"] == lesson_title, "it reopened a lesson other than the one just finished"
    # Reopening alone writes nothing: the lesson is pinned and the record is untouched
    # until the learner's second attempt actually ends.
    lesson_id = deps.lesson_session.read_for(SEEDED_LEARNER).lesson_id
    assert _rows(instance, lesson_id) == ["completed"], "redo_lesson wrote to the results table"

    # Now end the second attempt honestly. THIS is what supersedes the completion.
    saved = await _call(deps, "finish_lesson", {"outcome": "partial"})
    assert saved["recorded"] is True

    back = await _call(deps, "get_progress", {"language": "Italian"})
    assert back["next_lesson"]["title"] == lesson_title, "the lesson did not come back onto the path"
    # Nothing was deleted to achieve that. The history is the record.
    assert _rows(instance, lesson_id) == ["completed", "partial"]


@pytest.mark.asyncio
async def test_it_reopens_nothing_when_the_learner_has_finished_nothing(instance: Path) -> None:
    """An empty answer with its own reason, not an error and not a guess."""
    deps = _deps(instance)

    answer = await _call(deps, "redo_lesson", {"language": "Italian"})

    assert answer["reopened"] is False
    assert answer["reason"] == "nothing_finished_yet"
    assert deps.lesson_session.read_for(SEEDED_LEARNER) is None, "it pinned a lesson it said it had not reopened"


@pytest.mark.asyncio
async def test_the_lesson_it_reopens_can_be_taught_because_its_lines_were_pinned(instance: Path) -> None:
    """A reopened lesson has to be measurable, or the gate goes inert for exactly it.

    Reopening pins the lesson the same way start_lesson does, lines included. Without
    that, the second attempt could be recorded completed with nothing taught -- which is
    the defect this tool exists beside.
    """
    deps = _deps(instance)
    await _call(deps, "start_lesson", {"language": "Italian"})
    _teach(deps, instance)
    await _call(deps, "finish_lesson", {"outcome": "completed"})

    await _call(deps, "redo_lesson", {"language": "Italian"})

    assert deps.lesson_session.worked_through_for(SEEDED_LEARNER) is False, (
        "a reopened lesson reports its coverage as unmeasurable, so a completion would pass unchecked"
    )
    saved = await _call(deps, "finish_lesson", {"outcome": "completed"})
    assert saved["outcome"] == "partial", "a reopened lesson was completed again without being taught"
    assert saved["not_completed_because"] == "too_little_of_the_lesson_was_practised"


@pytest.mark.asyncio
async def test_neither_a_lesson_nor_an_identity_in_the_arguments_changes_what_it_reopens(instance: Path) -> None:
    """The boundary every learner tool holds: the conversation names neither."""
    deps = _deps(instance)
    await _call(deps, "start_lesson", {"language": "Italian"})
    _teach(deps, instance)
    await _call(deps, "finish_lesson", {"outcome": "completed"})
    honest = await _call(deps, "redo_lesson", {"language": "Italian"})

    attacked = await _call(
        deps,
        "redo_lesson",
        {
            "language": "Italian",
            "lesson_id": "it-fast-06-phone-call-about-a-flat",
            "learner_id": "somebody-else",
            "user_id": "somebody-else",
        },
    )

    assert attacked == honest, "an injected lesson or identity changed the answer"


@pytest.mark.asyncio
async def test_the_route_back_survives_being_used_twice(instance: Path) -> None:
    """A way back that works once is not a way back.

    The first version of this tool found its lesson among every attempt ever recorded
    as completed, then looked that lesson up among the ones the catalog CURRENTLY calls
    finished. Those two stopped being the same question when "finished" became
    latest-wins: once a redo cycle ended in a partial, the most recent completed
    attempt named a lesson that was no longer finished, the lookup found nothing, and
    the tool answered "I could not reopen that lesson just now" for ever afterwards.
    """
    deps = _deps(instance)

    # Finish two lessons, so there is still something finished after the first redo.
    for _ in range(2):
        await _call(deps, "start_lesson", {"language": "Italian"})
        _teach(deps, instance)
        assert (await _call(deps, "finish_lesson", {"outcome": "completed"}))["outcome"] == "completed"

    # First redo, ended honestly as a partial: lesson two is no longer finished.
    first = await _call(deps, "redo_lesson", {"language": "Italian"})
    assert first["reopened"] is True
    second_lesson = first["lesson"]["title"]
    await _call(deps, "finish_lesson", {"outcome": "partial"})

    # Lesson one is still genuinely finished, so there is still a way back to it.
    again = await _call(deps, "redo_lesson", {"language": "Italian"})

    assert again["reopened"] is True, f"the route back jammed after one use: {again.get('reason')}"
    assert again["lesson"]["title"] != second_lesson, (
        "it reopened the lesson that is no longer finished rather than the one that is"
    )


def test_finish_lesson_is_still_the_only_tool_that_writes_a_result() -> None:
    """The invariant this tool was nearly the counter-example to, enforced not asserted.

    Three places in the codebase now SAY that finish_lesson is the only
    conversation-reachable writer of lesson_results -- this module's docstring,
    docs/lesson-flow.md, and test_finish_lesson.py's header -- and until this case
    nothing checked it. The first version of redo_lesson did write, which is exactly how
    an invariant kept only in prose stops being true; test_face_matching.py records the
    same lesson being learned for faceprint writers.

    Derived by parsing every module in the tools package, so a new tool is caught on the
    day it is added rather than when somebody rereads a docstring.
    """
    import ast as _ast

    from reachy_language_tutor import tools as tools_package

    writers = []
    for path in sorted(Path(tools_package.__path__[0]).glob("*.py")):
        tree = _ast.parse(path.read_text(encoding="utf-8"))
        imports_writer = any(
            isinstance(node, _ast.ImportFrom)
            and (node.module or "").startswith("reachy_language_tutor.learners")
            and any(alias.name == "record_result" for alias in node.names)
            for node in _ast.walk(tree)
        )
        calls_writer = any(
            isinstance(node, _ast.Call)
            and (
                (isinstance(node.func, _ast.Name) and node.func.id == "record_result")
                or (isinstance(node.func, _ast.Attribute) and node.func.attr == "record_result")
            )
            for node in _ast.walk(tree)
        )
        if imports_writer or calls_writer:
            writers.append(path.name)

    assert writers == ["finish_lesson.py"], (
        f"{writers} can write a lesson result. Exactly one conversation-reachable writer is the "
        "design: the lesson a result names comes from the pinned session, and a second writer is a "
        "second place that boundary has to be got right. If a new writer is genuinely wanted, that "
        "is a decision about the security boundary rather than housekeeping."
    )


@pytest.mark.asyncio
async def test_a_last_finished_lesson_with_nothing_written_in_it_is_not_reopened(instance: Path) -> None:
    """The measured defect: the seeded learner finished two Spanish placeholders.

    Before the fix this reopened 'Introducing yourself' -- a title and an objective with
    no dialogue, notes or drills -- pinned it with no lines, and a "completed" was then
    written for it with nothing taught, because an unmeasurable lesson is not downgraded.
    start_lesson refuses such a lesson with lesson_not_written_yet; this now does too.
    """
    deps = _deps(instance)
    placeholders = ["es-01-greetings", "es-02-introductions"]
    before = {lesson_id: _rows(instance, lesson_id) for lesson_id in placeholders}
    assert before == {"es-01-greetings": ["completed"], "es-02-introductions": ["completed"]}, (
        "the seed no longer gives this learner finished placeholders, so this proves nothing"
    )

    answer = await _call(deps, "redo_lesson", {"language": "Spanish"})

    assert answer["reopened"] is False
    assert answer["reason"] == "lesson_not_written_yet"
    assert set(answer["languages_with_material"]) == {"French", "Italian", "Portuguese", "Spanish"}
    assert deps.lesson_session.read_for(SEEDED_LEARNER) is None, "an empty lesson was pinned anyway"
    # And so there is nothing for a "completed" to be written against.
    saved = await _call(deps, "finish_lesson", {"outcome": "completed"})
    assert saved["reason"] == "no_lesson_running"
    assert {lesson_id: _rows(instance, lesson_id) for lesson_id in placeholders} == before


@pytest.mark.asyncio
async def test_a_reopened_lesson_opened_again_keeps_what_was_already_taught(instance: Path) -> None:
    """The idempotent re-open reaches redo_lesson too: asking twice is not starting over."""
    deps = _deps(instance)
    await _call(deps, "start_lesson", {"language": "Italian"})
    _teach(deps, instance)
    await _call(deps, "finish_lesson", {"outcome": "completed"})
    await _call(deps, "redo_lesson", {"language": "Italian"})
    _teach(deps, instance)
    taught = deps.lesson_session.coverage_for(SEEDED_LEARNER)

    await _call(deps, "redo_lesson", {"language": "Italian"})

    assert deps.lesson_session.coverage_for(SEEDED_LEARNER) == taught
    assert (await _call(deps, "finish_lesson", {"outcome": "completed"}))["outcome"] == "completed"
