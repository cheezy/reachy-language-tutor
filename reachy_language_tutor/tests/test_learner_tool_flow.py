"""The flow W10 exists to prove: three tools, one conversation, one database.

Every other test in this suite pins one tool in isolation. This one runs the
sequence a real lesson produces -- greet, choose a language, practise, save the
result, ask again -- through ``dispatch_tool_call``, the same entry point the
realtime session uses, with the model's arguments arriving as the JSON string it
actually emits.

Two things here are not reachable from a single-tool test:

* **The handoff.** ``record_result`` is told which lesson to save using the id
  ``get_progress`` reported. Nothing checks that those two agree on the shape of a
  lesson id except a test that carries one from the first call into the second.
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

from reachy_language_tutor.learners import store
from reachy_language_tutor.tools.core_tools import ToolDependencies


def _core_tools():
    """Look core_tools up per call rather than binding it at module scope.

    Still needed, but for a narrower reason than when it was written. D28 fixed
    test_external_loading.py, which no longer creates a second core_tools. Two files
    still do: test_tool_space_runtime.py and test_profile_load_resilience.py, whose
    reloads exist to re-bind monkeypatched dependencies rather than to refresh the
    registry, so tools_module_graph's in-place reset does not serve them. Until those
    two are converted, a module-scope binding here can still go stale.
    """
    from reachy_language_tutor.tools import core_tools

    return core_tools


SEEDED_LEARNER = store.SEED_LEARNERS[0][0]


@pytest.fixture
def instance(tmp_path: Path) -> Path:
    """Return a prepared learner database at a temporary instance path."""
    assert store.ensure_learner_database(tmp_path).ready is True
    return tmp_path


def _deps(instance: Path, learner_id: str | None = SEEDED_LEARNER) -> ToolDependencies:
    return ToolDependencies(
        reachy_mini=MagicMock(),
        movement_manager=MagicMock(),
        instance_path=instance,
        current_learner_id=learner_id,
    )


async def _call(name: str, args: dict[str, Any], deps: ToolDependencies) -> dict[str, Any]:
    """Dispatch exactly as the realtime layer does: a name and a JSON string."""
    return await _core_tools().dispatch_tool_call(name, json.dumps(args), deps)


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

    # The handoff: the id the tutor was just given is the id it saves against.
    saved = await _call("record_result", {"lesson_id": lesson["id"], "outcome": "completed"}, deps)
    assert saved["recorded"] is True

    after = await _call("get_progress", {"language": "Spanish"}, deps)
    assert after["completed_count"] == before["completed_count"] + 1
    assert after["remaining_count"] == before["remaining_count"] - 1
    assert after["last_completed"] == lesson["title"]
    # It moved on rather than offering the same lesson again.
    assert (after["next_lesson"] or {}).get("id") != lesson["id"]


@pytest.mark.asyncio
async def test_a_recorded_result_survives_a_restart(instance: Path) -> None:
    """The criterion in the task's own words, answered by a separate process.

    The reader below is a fresh interpreter. It imports the tool from scratch and
    points it at the same directory on disk, so a result it can see is one that
    outlived the process that wrote it.
    """
    deps = _deps(instance)
    lesson = (await _call("get_progress", {"language": "French"}, deps))["next_lesson"]
    assert lesson is not None

    saved = await _call("record_result", {"lesson_id": lesson["id"], "outcome": "completed"}, deps)
    assert saved["recorded"] is True
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
answer = asyncio.run(core_tools.dispatch_tool_call("get_progress", json.dumps({"language": "French"}), deps))
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
        "record_result": {"lesson_id": "es-01-greetings", "outcome": "completed"},
    }
    for name, args in calls.items():
        answer = await _call(name, args, deps)
        assert "error" in answer, f"{name} reported no failure against an unreadable store"
        assert answer["error"].strip(), f"{name} returned an empty error"
        if name == "record_result":
            assert answer["recorded"] is False, "a failed write must never look like a success"
