"""Tests for the get_lesson_content tool: what it reads, what it refuses, what it hides.

This is the tool that lets the tutor teach a converted unit instead of improvising
around its title. It is also the first tool to carry lesson text into the conversation,
which makes two things load-bearing rather than tidy.

The first is the same boundary start_lesson and finish_lesson hold: the lesson comes
from the pinned session, never from the model. The schema is an allow-list of NO keys,
so there is nothing for an injected identity or an injected lesson to travel in.

The second is new here. Lesson text is data read out of a database, and a line of it
can perfectly well read like an instruction addressed to the tutor. The store's own
docstring says so. So this file pins what the tool hands over and what it holds back --
never a lesson id, never provenance, and never a line of any of it in a log.

These call the tool directly, which pins it in isolation but SKIPS the dispatcher's
unvalidated **args splat. test_tool_identity_boundary.py attacks the real dispatch
path and discovers this tool rather than naming it.
"""

import json
import logging
from typing import Any
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from reachy_language_tutor.tools import start_lesson, finish_lesson
from reachy_language_tutor.tools import get_lesson_content as module

# Bound at module scope, which is what D28 made possible -- see tests/tools_module_graph.py.
from reachy_language_tutor.learners import store
from reachy_language_tutor.lesson_session import LessonSessionHolder
from reachy_language_tutor.learners.models import Drill, LessonContent
from reachy_language_tutor.tools.core_tools import ToolDependencies
from reachy_language_tutor.tools.get_lesson_content import GetLessonContent


SEEDED_LEARNER = store.SEED_LEARNERS[0][0]

# A converted unit, which is the only kind that carries dialogue, notes and drills.
WITH_MATERIAL = "it-fast-01-what-time-is-it"
WITH_MATERIAL_LANGUAGE = "it"

# A lesson written for this app: a real row in the catalog with nothing under it. Not a
# degenerate case -- every lesson in five of the six seeded languages is one of these.
WITHOUT_MATERIAL = "es-01-greetings"
WITHOUT_MATERIAL_LANGUAGE = "es"

# Identity-shaped keys that must never be declared as parameters, and the lesson keys
# that must not join them either. The assertion checks the schema's empty allow-list;
# this list only makes a failure name what slipped in.
FORBIDDEN_PARAMETERS = (
    "learner_id",
    "learner",
    "name",
    "display_name",
    "user_id",
    "username",
    "person_id",
    "profile_id",
    "student_id",
    "speaker",
    "who",
    "id",
    "email",
    "lesson",
    "lesson_id",
    "position",
    "drill",
    "turn",
)


@pytest.fixture
def instance(tmp_path: Path) -> Path:
    """Prepare a learner database at a temporary instance path."""
    assert store.ensure_learner_database(tmp_path).ready is True
    return tmp_path


def _deps(pinned: tuple[str, str] | None = None, **overrides: Any) -> ToolDependencies:
    """Build dependencies the way main.build_tool_dependencies does, fields stubbed.

    The holder is bound to the learner the bundle names, because that is what production
    does -- a holder bound to nobody refuses to pin anything, and a suite that quietly
    used one would exercise the refusal path and call it success.
    """
    holder = LessonSessionHolder(overrides.get("current_learner_id"))
    if pinned is not None:
        holder.open(lesson_id=pinned[0], language_code=pinned[1])
    overrides.setdefault("lesson_session", holder)
    return ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock(), **overrides)


async def _call(pinned: tuple[str, str] | None = None, **overrides: Any) -> dict[str, Any]:
    """Invoke the tool with a lesson pinned, or with nothing pinned when None."""
    return await GetLessonContent()(_deps(pinned, **overrides))


# --- The schema is an allow-list of nothing at all ------------------------------------


def test_the_schema_declares_no_parameters_at_all() -> None:
    """There is nothing for the conversation to choose, so there is nothing to declare."""
    schema = GetLessonContent.parameters_schema

    assert schema["properties"] == {}
    assert schema["required"] == []


@pytest.mark.parametrize("forbidden", FORBIDDEN_PARAMETERS)
def test_no_identity_or_lesson_shaped_key_appears_in_the_schema(forbidden: str) -> None:
    """Named cases only sharpen a failure; the empty allow-list above is the guard."""
    assert forbidden not in GetLessonContent.parameters_schema["properties"]


# --- What a running lesson gives the tutor --------------------------------------------


@pytest.mark.asyncio
async def test_it_returns_the_running_lessons_dialogue_notes_and_drills(instance: Path) -> None:
    """The material the tutor teaches from, compared against the database, not a copy."""
    expected = store.get_lesson_content(WITH_MATERIAL, instance_path=instance)
    assert expected is not None and expected.turns and expected.notes and expected.drills

    result = await _call(
        (WITH_MATERIAL, WITH_MATERIAL_LANGUAGE), current_learner_id=SEEDED_LEARNER, instance_path=instance
    )

    assert result["have_content"] is True
    assert len(result["dialogue"]) == len(expected.turns), "the dialogue came back a different length"
    assert len(result["notes"]) == len(expected.notes)
    assert len(result["drills"]) == len(expected.drills)
    assert result["dialogue"][0]["speaker"] == expected.turns[0].speaker
    assert result["dialogue"][0]["text"] == expected.turns[0].text
    assert result["notes"][0]["text"] == expected.notes[0].text


@pytest.mark.asyncio
async def test_a_cue_response_drill_keeps_its_cue_and_its_answer_apart(instance: Path) -> None:
    """Merge the two and the tutor reads the answer out with the question."""
    result = await _call(
        (WITH_MATERIAL, WITH_MATERIAL_LANGUAGE), current_learner_id=SEEDED_LEARNER, instance_path=instance
    )

    askable = [drill for drill in result["drills"] if drill["kind"] == "cue_response"]
    assert askable, "this unit shipped cue-response drills and none came through"
    for drill in askable:
        # .get rather than [], so a drill rendered with the other kind's fields fails
        # saying which drill lost what, rather than raising KeyError out of the harness.
        assert drill.get("cue"), f"drill {drill['position']} has no cue to say: {sorted(drill)}"
        assert drill.get("expected_response"), f"drill {drill['position']} has no answer to check: {sorted(drill)}"
        assert drill["cue"] != drill["expected_response"], f"drill {drill['position']} answers itself"
        assert "target_text" not in drill, f"drill {drill['position']} carries a repetition field it never fills"


@pytest.mark.asyncio
async def test_a_repetition_drill_carries_its_target_and_its_gloss(instance: Path) -> None:
    """The line to say and what it means are different jobs, so they stay separate."""
    result = await _call(
        (WITH_MATERIAL, WITH_MATERIAL_LANGUAGE), current_learner_id=SEEDED_LEARNER, instance_path=instance
    )

    repeats = [drill for drill in result["drills"] if drill["kind"] == "repetition"]
    assert repeats, "this unit shipped repetition drills and none came through"
    for drill in repeats:
        assert drill.get("target_text"), f"drill {drill['position']} has no line to repeat: {sorted(drill)}"
        assert drill.get("english_gloss"), f"drill {drill['position']} has no gloss: {sorted(drill)}"
        assert "cue" not in drill, f"drill {drill['position']} carries a cue-response field it never fills"


@pytest.mark.asyncio
async def test_the_returned_lesson_carries_no_id_for_the_model_to_reuse(instance: Path) -> None:
    """Pinning the lesson is what makes its id application state; handing it back undoes that."""
    result = await _call(
        (WITH_MATERIAL, WITH_MATERIAL_LANGUAGE), current_learner_id=SEEDED_LEARNER, instance_path=instance
    )

    assert "id" not in result["lesson"]
    assert WITH_MATERIAL not in json.dumps(result), "the lesson id reached the model somewhere in the answer"


@pytest.mark.asyncio
async def test_where_the_lesson_came_from_stays_out_of_what_the_tutor_can_recite(instance: Path) -> None:
    """Provenance exists so a person can check the scan, not so the robot can cite a page.

    W24 records the course, module, unit and printed page of every converted lesson.
    That is for somebody auditing the database. A tutor that can see it will say it out
    loud in the middle of a lesson, so it is not carried here.
    """
    expected = store.get_lesson_content(WITH_MATERIAL, instance_path=instance)
    assert expected is not None and expected.source is not None, "this unit records its provenance"

    result = await _call(
        (WITH_MATERIAL, WITH_MATERIAL_LANGUAGE), current_learner_id=SEEDED_LEARNER, instance_path=instance
    )

    rendered = json.dumps(result)
    assert "source" not in result
    assert expected.source.course not in rendered
    assert str(expected.source.page) not in [note["text"] for note in result["notes"]]


@pytest.mark.asyncio
async def test_reading_the_lesson_leaves_the_pin_exactly_as_it_was(instance: Path) -> None:
    """Reading is all this tool does: start_lesson opens the pin and finish_lesson drops it."""
    deps = _deps((WITH_MATERIAL, WITH_MATERIAL_LANGUAGE), current_learner_id=SEEDED_LEARNER, instance_path=instance)
    before = deps.lesson_session.read_for(SEEDED_LEARNER)
    assert before is not None

    await GetLessonContent()(deps)

    assert deps.lesson_session.read_for(SEEDED_LEARNER) == before, "the tool moved a pin it only had to read"


# --- What it refuses, and in which order ----------------------------------------------


@pytest.mark.asyncio
async def test_no_current_learner_is_refused_before_the_session_is_read(instance: Path) -> None:
    """Without a learner there is no session to read, so that guard has to come first.

    A lesson really is running -- for somebody -- and the bundle names nobody. Reverse
    the two guards and read_for(None) answers None, so this comes back as "no lesson is
    running", which would be a lie about a lesson that is.
    """
    holder = LessonSessionHolder(SEEDED_LEARNER)
    holder.open(lesson_id=WITH_MATERIAL, language_code=WITH_MATERIAL_LANGUAGE)
    deps = ToolDependencies(
        reachy_mini=MagicMock(),
        movement_manager=MagicMock(),
        current_learner_id=None,
        instance_path=instance,
        lesson_session=holder,
    )

    result = await GetLessonContent()(deps)

    assert result["have_content"] is False
    assert result["reason"] == "no_current_learner"


@pytest.mark.asyncio
async def test_no_lesson_running_is_refused_before_the_store_is_touched(
    instance: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read with nothing running must cost no database work on the voice path."""
    reads: list[str] = []
    monkeypatch.setattr(module, "get_lesson_content", lambda lesson_id, **kw: reads.append(lesson_id))

    result = await _call(None, current_learner_id=SEEDED_LEARNER, instance_path=instance)

    assert result["reason"] == "no_lesson_running"
    assert reads == [], "the store was read for a learner with no lesson running"


@pytest.mark.asyncio
async def test_a_lesson_that_vanished_is_told_apart_from_an_unreadable_store(instance: Path, tmp_path: Path) -> None:
    """One is a fact about the lesson and the other is a fault in the robot.

    Saying "I cannot find that lesson" when the database is simply unreachable would
    tell a learner their lesson is gone when it is not -- the same mistake get_profile
    splits its two silences to avoid.
    """
    gone = await _call(("es-99-no-such-lesson", "es"), current_learner_id=SEEDED_LEARNER, instance_path=instance)
    broken = await _call(
        (WITH_MATERIAL, WITH_MATERIAL_LANGUAGE), current_learner_id=SEEDED_LEARNER, instance_path=tmp_path / "empty"
    )

    assert gone["reason"] == "lesson_gone"
    assert broken["reason"] == "records_unavailable"
    assert gone["error"] != broken["error"], "the two silences are said the same way"


@pytest.mark.asyncio
async def test_a_lesson_with_no_material_is_not_reported_as_a_failure(instance: Path) -> None:
    """Most lessons in the catalog have no dialogue, notes or drills, and are still lessons.

    Shaped as news rather than an error, the way start_lesson treats "you have finished
    them all" -- and with the lesson still named, so the tutor has its objective to work
    from instead of nothing.
    """
    result = await _call(
        (WITHOUT_MATERIAL, WITHOUT_MATERIAL_LANGUAGE), current_learner_id=SEEDED_LEARNER, instance_path=instance
    )

    assert result["have_content"] is False
    assert result["reason"] == "no_material"
    assert "error" not in result, "a lesson written without drills is not a fault"
    assert result["message"]
    assert result["lesson"]["title"], "the tutor is left with nothing to work from"


def test_the_renderer_knows_every_drill_kind_the_store_can_store() -> None:
    """A kind the store accepts and this tool does not know is a drill the tutor never sees.

    Pinned against the store's own vocabulary rather than a copy, so adding a third kind
    of drill fails here -- at the one place that decides which fields reach the model --
    instead of silently shipping a kind nobody rendered.
    """
    from reachy_language_tutor.learners.models import DRILL_KINDS

    assert set(module._DRILL_FIELDS) == set(DRILL_KINDS), (
        "the drill renderer and the store disagree about which kinds of drill exist: "
        f"{sorted(set(module._DRILL_FIELDS) ^ set(DRILL_KINDS))}"
    )
    for kind, fields in module._DRILL_FIELDS.items():
        assert fields, f"{kind} renders no fields, so the tutor gets a drill with nothing in it"


def test_every_refusal_sentence_says_one_thing_only() -> None:
    """Two reasons sharing a sentence is two meanings a learner cannot tell apart."""
    sentences = list(module._REFUSALS.values())

    assert len(set(sentences)) == len(sentences), f"a sentence is shared between reasons: {sentences}"


def test_no_refusal_repeats_a_sibling_tools_sentence() -> None:
    """The same reason name costs each tool something different, so each says its own.

    "no_lesson_running" means nothing was saved in finish_lesson and nothing can be read
    here. A learner who hears the saving sentence when nothing was being saved is told
    something untrue about their own records.
    """
    shared = set(module._REFUSALS.values()) & (
        set(finish_lesson._REFUSALS.values()) | set(start_lesson._REFUSALS.values())
    )

    assert not shared, f"this tool speaks a sibling tool's sentence: {sorted(shared)}"


# --- Nothing from the lesson reaches disk ---------------------------------------------


@pytest.mark.asyncio
async def test_nothing_from_the_lesson_reaches_a_log(instance: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Counts are safe; the id, the title and every line of the material are not.

    The dialogue is the strongest case in the repository for this rule: it is the
    nearest thing the app has to a transcript, and CLAUDE.md's log rule exists because
    a learner's words reached disk once already.
    """
    expected = store.get_lesson_content(WITH_MATERIAL, instance_path=instance)
    assert expected is not None and expected.turns

    with caplog.at_level(logging.DEBUG, logger=module.__name__):
        result = await _call(
            (WITH_MATERIAL, WITH_MATERIAL_LANGUAGE), current_learner_id=SEEDED_LEARNER, instance_path=instance
        )

    assert result["have_content"] is True
    records = [record for record in caplog.records if record.name == module.__name__]
    assert records, "the tool logged nothing, so this pin would pass vacuously"
    for record in records:
        rendered = f"{record.name} | {record.msg} | {record.args} | {record.getMessage()}"
        assert SEEDED_LEARNER not in rendered, rendered
        assert WITH_MATERIAL not in rendered, rendered
        assert expected.lesson.title not in rendered, rendered
        assert expected.lesson.objective not in rendered, rendered
        # Every field that carries lesson text, not the two the first draft happened to
        # check. Drill cues and expected responses are target-language lines of exactly
        # the kind this rule protects, and CLAUDE.md names the unswept sibling as this
        # board's most common defect.
        for turn in expected.turns:
            assert turn.text not in rendered, rendered
            assert turn.speaker not in rendered, rendered
        for note in expected.notes:
            assert note.text not in rendered, rendered
        for drill in expected.drills:
            for value in (drill.target_text, drill.english_gloss, drill.cue, drill.expected_response):
                assert value is None or value not in rendered, rendered
        if expected.dialogue_title:
            assert expected.dialogue_title not in rendered, rendered


@pytest.mark.asyncio
async def test_two_learners_with_the_same_lesson_pinned_read_the_same_material(instance: Path) -> None:
    """Lesson content is shared reference data: it must not vary with who is asking.

    The other half of the boundary. Everywhere else in this suite the concern is that a
    tool answers for the wrong person; here it is that lesson text is not personal at
    all, so a difference between two learners would mean something leaked into it.
    """
    connection = store.connect(instance)
    with connection:
        connection.execute(
            "INSERT INTO learners (id, display_name, created_at) VALUES (?, ?, ?)",
            ("housemate-w18", "Housemate", 1),
        )
    connection.close()

    mine = await _call(
        (WITH_MATERIAL, WITH_MATERIAL_LANGUAGE), current_learner_id=SEEDED_LEARNER, instance_path=instance
    )
    theirs = await _call(
        (WITH_MATERIAL, WITH_MATERIAL_LANGUAGE), current_learner_id="housemate-w18", instance_path=instance
    )

    assert mine == theirs


# --- A drill kind the renderer does not know ------------------------------------------
#
# _DRILL_FIELDS is pinned against the store's DRILL_KINDS, so this branch is unreachable
# through the database as it stands today. That is exactly why it is exercised here by
# narrowing the map instead: a branch nobody has run is a branch nobody has checked, and
# this repository's rule is to execute it rather than reason about it. The pin above is
# what stops the branch mattering in production; these two are what make it correct if
# it ever does.


@pytest.mark.asyncio
async def test_a_drill_kind_the_renderer_does_not_know_is_dropped_and_said_so(
    instance: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Dropped from what the tutor gets, counted out of the log, and named at error level."""
    expected = store.get_lesson_content(WITH_MATERIAL, instance_path=instance)
    assert expected is not None
    cue_response = [drill for drill in expected.drills if drill.kind == "cue_response"]
    assert cue_response, "this unit is the fixture for a dropped kind and has none to drop"

    monkeypatch.setitem(module._DRILL_FIELDS, "cue_response", ())
    monkeypatch.delitem(module._DRILL_FIELDS, "cue_response")

    with caplog.at_level(logging.DEBUG, logger=module.__name__):
        result = await _call(
            (WITH_MATERIAL, WITH_MATERIAL_LANGUAGE), current_learner_id=SEEDED_LEARNER, instance_path=instance
        )

    kept = len(expected.drills) - len(cue_response)
    assert len(result["drills"]) == kept, "a drill of an unknown kind reached the tutor anyway"
    assert all(drill["kind"] == "repetition" for drill in result["drills"])

    rendered = " | ".join(record.getMessage() for record in caplog.records if record.name == module.__name__)
    # The count the tutor actually received, not the store's -- a log claiming drills the
    # tutor never saw is the specific defect this fix exists to prevent.
    assert f"drills={kept}" in rendered, rendered
    assert f"drills={len(expected.drills)}" not in rendered, rendered
    # The kind is a safe constant from the store's vocabulary; the drill's text is not.
    assert "cue_response" in rendered, rendered
    for drill in cue_response:
        assert drill.cue not in rendered, rendered
        assert drill.expected_response not in rendered, rendered


@pytest.mark.asyncio
async def test_a_lesson_whose_every_drill_was_dropped_reads_as_having_no_material(
    instance: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """no_material asks what reached the tutor, not what the store happens to hold.

    Before this, a lesson whose only content was drills of an unrenderable kind came back
    have_content True with three empty lists -- the tutor told it had material, and handed
    none. The lesson is built here rather than seeded because the database's own CHECK
    refuses a kind outside DRILL_KINDS, which is the guard that keeps this hypothetical.
    """
    real = store.get_lesson_content(WITH_MATERIAL, instance_path=instance)
    assert real is not None
    nothing_renderable = LessonContent(
        lesson=real.lesson,
        source=None,
        dialogue_title=None,
        turns=(),
        notes=(),
        drills=(
            Drill(
                position=1, kind="sung_round", target_text=None, english_gloss=None, cue=None, expected_response=None
            ),
        ),
    )
    monkeypatch.setattr(module, "get_lesson_content", lambda *a, **k: nothing_renderable)

    result = await _call(
        (WITH_MATERIAL, WITH_MATERIAL_LANGUAGE), current_learner_id=SEEDED_LEARNER, instance_path=instance
    )

    assert result["have_content"] is False, (
        "the tutor was told it had material after every drill was dropped: "
        f"{ {k: v for k, v in result.items() if k != 'lesson'} }"
    )
    assert result["reason"] == "no_material"
    assert "error" not in result
    assert result["lesson"]["title"] == real.lesson.title, "the tutor is left with nothing to work from"
