"""The flow W10 exists to prove: the learner tools, one conversation, one database.

Every other test in this suite pins one tool in isolation. This one runs the
sequence a real lesson produces -- greet, choose a language, practise, save the
result, ask again -- through ``dispatch_tool_call``, the same entry point the
realtime session uses, with the model's arguments arriving as the JSON string it
actually emits.

Two things here are not reachable from a single-tool test:

* **The handoff.** It used to be an id: ``record_result`` was told which lesson to
  save using the id ``get_progress`` had reported. W16 removed that tool and that
  argument, so the handoff is now the pinned session -- ``start_lesson`` writes it,
  ``finish_lesson`` reads it, and no lesson id appears in the conversation at all.
  Nothing checks that those two agree about which lesson is running except a test
  that starts one and then finishes it.
* **A restart.** The acceptance criterion is that a saved result is still there
  after the app stops. A second call in the same process does reopen the file --
  ``store.py`` caches no connection -- but it shares an interpreter, an import
  graph and a page cache with the write, so it cannot speak to that criterion on
  its own. ``test_a_recorded_result_survives_a_restart`` therefore reads the
  database back from a genuinely separate process.

What this file deliberately does NOT do is claim the spoken session works. It
proves the tools are wired to each other and to the disk; whether the model
chooses to call them is a manual test, and it is recorded as one.
"""

import sys
import json
import subprocess
from typing import Any
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from untaught_language import UNTAUGHT_NAME

# Bound at module scope. That is the whole point of D28: until test_external_loading.py,
# test_tool_space_runtime.py and test_profile_load_resilience.py stopped re-importing the
# tools package, a binding made here went stale the moment one of them ran -- the re-import
# built a second Tool base class, and _load_enabled_tools filters with issubclass, so it
# matched nothing and the loader blamed the profile. This file used to carry a lookup helper
# that fetched the module per call to dodge exactly that. See tools_module_graph.py.
from reachy_language_tutor.tools import core_tools
from reachy_language_tutor.learners import store
from reachy_language_tutor.lesson_session import LessonSessionHolder
from reachy_language_tutor.tools.core_tools import ToolDependencies


SEEDED_LEARNER = store.SEED_LEARNERS[0][0]


@pytest.fixture
def instance(tmp_path: Path) -> Path:
    """Return a prepared learner database at a temporary instance path."""
    assert store.ensure_learner_database(tmp_path).ready is True
    return tmp_path


def _deps(instance: Path, learner_id: str | None = SEEDED_LEARNER) -> ToolDependencies:
    """Build the bundle the way main.build_tool_dependencies does, robot fields stubbed.

    The lesson-session holder is bound to the same learner the bundle names, because
    that is what production does -- a holder bound to nobody refuses every pin, so a
    flow test that left it defaulted would exercise start_lesson's refusal path and
    call it a working flow.
    """
    return ToolDependencies(
        reachy_mini=MagicMock(),
        movement_manager=MagicMock(),
        instance_path=instance,
        current_learner_id=learner_id,
        lesson_session=LessonSessionHolder(learner_id),
    )


async def _call(name: str, args: dict[str, Any], deps: ToolDependencies) -> dict[str, Any]:
    """Dispatch exactly as the realtime layer does: a name and a JSON string."""
    return await core_tools.dispatch_tool_call(name, json.dumps(args), deps)


def _teach_the_pinned_lesson(deps: Any, instance_path: Path) -> None:
    """Say the running lesson's own printed lines, as the tutor would out loud.

    finish_lesson will not write a completion for a lesson the app has no record of
    anybody teaching (D38), and in the running app that record is fed by the
    conversation handler as the tutor speaks. These tests drive the tools directly with
    no handler attached, so they stand in for it. Reads the pinned lesson through
    read_for rather than taking a lesson id, so no lesson id enters the test body --
    which is the property the flow test below exists to demonstrate.
    """
    session = deps.lesson_session.read_for(deps.current_learner_id)
    assert session is not None, "nothing is pinned, so there is no lesson to teach"
    content = store.get_lesson_content(session.lesson_id, instance_path=instance_path)
    assert content is not None
    for turn in content.turns:
        deps.lesson_session.note_spoken(deps.current_learner_id, turn.text)
        # The learner answering back. A completion needs both halves -- material said
        # AND somebody there to say it to -- so a helper that only spoke would be
        # simulating a tutor reciting at an empty chair.
        deps.lesson_session.note_learner_turn(deps.current_learner_id)
    for drill in content.drills:
        for line in (drill.target_text, drill.cue, drill.expected_response):
            if line:
                deps.lesson_session.note_spoken(deps.current_learner_id, line)


@pytest.mark.asyncio
async def test_the_whole_lesson_flow_runs_through_the_real_dispatch_path(instance: Path) -> None:
    """Greet, look up progress, record a result, and see the figures move."""
    deps = _deps(instance)

    profile = await _call("get_profile", {}, deps)
    assert "error" not in profile
    assert profile["display_name"]

    before = await _call("get_progress", {"language": "Spanish"}, deps)
    assert "error" not in before
    lesson = before["next_lesson"]
    assert lesson is not None, "the seeded learner has nothing left to practise"

    # The handoff, and the point of W16: no lesson id appears anywhere in this test
    # body after this line. start_lesson pins what the database chose, finish_lesson
    # records against the pin, and the conversation never names a lesson at all.
    started = await _call("start_lesson", {"language": "Spanish"}, deps)
    assert started["started"] is True
    assert started["lesson"]["title"] == lesson["title"]

    _teach_the_pinned_lesson(deps, instance)
    saved = await _call("finish_lesson", {"outcome": "completed"}, deps)
    assert saved["recorded"] is True
    assert saved["outcome"] == "completed", "a lesson that was taught must record as completed"
    assert saved["lesson_title"] == lesson["title"]

    after = await _call("get_progress", {"language": "Spanish"}, deps)
    assert after["completed_count"] == before["completed_count"] + 1
    assert after["remaining_count"] == before["remaining_count"] - 1
    assert after["last_completed"] == lesson["title"]
    # It moved on rather than offering the same lesson again.
    assert (after["next_lesson"] or {}).get("id") != lesson["id"]
    # And the session is clear, so the tutor cannot save the same lesson twice.
    assert deps.lesson_session.read_for(SEEDED_LEARNER) is None


@pytest.mark.asyncio
async def test_a_recorded_result_survives_a_restart(instance: Path) -> None:
    """The criterion in the task's own words, answered by a separate process.

    The reader below is a fresh interpreter. It imports the tool from scratch and
    points it at the same directory on disk, so a result it can see is one that
    outlived the process that wrote it.
    """
    deps = _deps(instance)
    # Italian, not French: French now refuses for want of written material, and this
    # test is about a recorded result outliving the process that wrote it.
    lesson = (await _call("get_progress", {"language": "Italian"}, deps))["next_lesson"]
    assert lesson is not None

    started = await _call("start_lesson", {"language": "Italian"}, deps)
    assert started["started"] is True
    _teach_the_pinned_lesson(deps, instance)
    saved = await _call("finish_lesson", {"outcome": "completed"}, deps)
    assert saved["recorded"] is True
    assert saved["outcome"] == "completed"
    completed_before_restart = saved["completed_count"]

    reader = """
import asyncio, json, sys
from unittest.mock import MagicMock
from reachy_language_tutor.tools import core_tools
from reachy_language_tutor.tools.core_tools import ToolDependencies

deps = ToolDependencies(
    reachy_mini=MagicMock(), movement_manager=MagicMock(),
    instance_path=sys.argv[1], current_learner_id=sys.argv[2],
)
answer = asyncio.run(core_tools.dispatch_tool_call("get_progress", json.dumps({"language": "Italian"}), deps))
print("RESULT:" + json.dumps(answer))
"""
    finished = subprocess.run(
        [sys.executable, "-c", reader, str(instance), SEEDED_LEARNER],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert finished.returncode == 0, finished.stderr[-2000:]
    line = next(ln for ln in finished.stdout.splitlines() if ln.startswith("RESULT:"))
    after_restart = json.loads(line[len("RESULT:") :])

    assert "error" not in after_restart
    assert after_restart["completed_count"] == completed_before_restart
    assert after_restart["last_completed"] == lesson["title"]


@pytest.mark.asyncio
async def test_a_language_the_learner_has_never_practised_starts_at_the_first_lesson(
    instance: Path,
) -> None:
    """Edge case the task names. Never-practised is a real answer, not an error."""
    deps = _deps(instance, learner_id=store.SEED_LEARNERS[-1][0])

    answer = await _call("get_progress", {"language": "French"}, deps)

    assert "error" not in answer
    assert answer["completed_count"] == 0
    assert answer["last_completed"] is None
    assert answer["next_lesson"]["position"] == 1


@pytest.mark.asyncio
async def test_a_tool_that_fails_mid_conversation_says_so_rather_than_inventing(
    tmp_path: Path,
) -> None:
    """Edge case the task names, asserted on all three tools rather than one.

    An unreadable store is the failure the tutor is most likely to meet, and the
    pitfall it must not answer with a guess. Swept across the set because fixing
    one member and leaving its siblings is this repository's most repeated defect.
    """
    missing = tmp_path / "no-database-here"
    missing.mkdir()
    deps = _deps(missing)

    calls = {
        "get_profile": {},
        "get_progress": {"language": "Spanish"},
        "start_lesson": {"language": "Spanish"},
        "finish_lesson": {"outcome": "completed"},
    }
    # finish_lesson needs a lesson already running, or it would answer
    # "nothing is running" and this test would pass for a reason other than the one it
    # names. start_lesson cannot pin one against an unreadable store, so the pin is
    # made directly -- which is also what a real conversation has: the store went
    # unreadable BETWEEN starting the lesson and finishing it.
    deps.lesson_session.open(lesson_id="es-01-greetings", language_code="es")

    for name, args in calls.items():
        answer = await _call(name, args, deps)
        assert "error" in answer, f"{name} reported no failure against an unreadable store"
        assert answer["error"].strip(), f"{name} returned an empty error"
        if name == "finish_lesson":
            assert answer["recorded"] is False, "a failed write must never look like a success"
            assert answer["reason"] == "storage_unavailable", (
                "the write failed for the reason this test names, not because nothing was running"
            )


@pytest.mark.asyncio
async def test_start_lesson_and_get_progress_agree_on_which_lesson_is_next(instance: Path) -> None:
    """Two tools, one database: they must not tell the learner different things.

    The handoff that matters here is not an id -- start_lesson deliberately returns
    none -- but the POSITION and the counts, which are what the tutor says out loud.
    If these diverged, the learner would hear "you are on lesson three" from one and
    "four" from the other in the same conversation.
    """
    deps = _deps(instance)

    progress = await _call("get_progress", {"language": "Spanish"}, deps)
    started = await _call("start_lesson", {"language": "Spanish"}, deps)

    assert "error" not in progress
    assert started["started"] is True
    assert started["lesson"]["position"] == progress["next_lesson"]["position"]
    assert started["lesson"]["title"] == progress["next_lesson"]["title"]
    assert started["remaining_count"] == progress["remaining_count"]
    assert started["completed_count"] == progress["completed_count"]
    # And the thing the pin is for: what the app believes is running is the lesson the
    # store named, not one the model was told about and could repeat back differently.
    pinned = deps.lesson_session.read_for(SEEDED_LEARNER)
    assert pinned is not None
    assert pinned.lesson_id == progress["next_lesson"]["id"]


@pytest.mark.asyncio
async def test_calling_start_lesson_twice_leaves_exactly_one_lesson_pinned(instance: Path) -> None:
    """Asking again must not stack a lesson, nor quietly advance to the next one.

    Through the real dispatch path, because the JSON round trip is where a second
    call could differ from the first.
    """
    deps = _deps(instance)

    first = await _call("start_lesson", {"language": "Spanish"}, deps)
    second = await _call("start_lesson", {"language": "Spanish"}, deps)

    assert first["started"] is True
    assert first == second
    expected = store.get_progress(SEEDED_LEARNER, "es", instance_path=instance)
    assert expected is not None and expected.next_lesson is not None
    pinned = deps.lesson_session.read_for(SEEDED_LEARNER)
    assert pinned is not None
    assert pinned.lesson_id == expected.next_lesson.id


@pytest.mark.asyncio
async def test_the_empty_language_signal_survives_the_real_dispatch_path(instance: Path) -> None:
    """The structured signal has to reach the model, not just exist in the store.

    Everything between the catalog query and the model is JSON going through
    `dispatch_tool_call`, and a field that is computed correctly and then dropped on
    the way out is the same to a learner as one that was never computed. So this asks
    both tools the way the realtime layer does -- a name and a JSON string -- and
    checks the answer a model would actually receive.

    French, German and Portuguese carry a full syllabus and nothing written in it;
    Italian and Spanish carry six converted units each. A learner must not be offered
    those two sets as if they were the same thing.
    """
    deps = _deps(instance)

    empty = await _call("start_lesson", {"language": "French"}, deps)
    assert empty["started"] is False
    assert empty["reason"] == "lesson_not_written_yet"
    assert set(empty["languages_with_material"]) == {"Italian", "Portuguese", "Spanish"}
    # Nothing was pinned, so the tutor cannot then record a lesson it never started.
    assert deps.lesson_session.read_for(SEEDED_LEARNER) is None

    ready = await _call("start_lesson", {"language": "Italian"}, deps)
    assert ready["started"] is True, "a language with material is unaffected"

    progress = await _call("get_progress", {"language": "French"}, deps)
    assert progress["has_material"] is False
    italian = await _call("get_progress", {"language": "Italian"}, deps)
    assert italian["has_material"] is True

    # And the not-taught answer splits the catalog rather than listing five names as
    # if the robot could teach five languages.
    unknown = await _call("get_progress", {"language": UNTAUGHT_NAME}, deps)
    assert set(unknown["languages_with_material"]) == {"Italian", "Portuguese", "Spanish"}
    assert set(unknown["languages_without_material_yet"]) == {"French", "German"}
