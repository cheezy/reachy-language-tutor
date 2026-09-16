"""Tests for the start_lesson tool: what it pins, what it refuses, and what it says.

This is the tool that makes a lesson start from the record rather than from the
model's memory of the conversation. Two boundaries meet here and both are security
boundaries, not style: the learner comes from application state, and so does the
LESSON. A lesson parameter "so they can skip ahead" would be the same breach as a
learner parameter, one fact along, which is why the schema is pinned as an allow-list
of exactly one key.

These call the tool directly, which pins it in isolation but SKIPS the dispatcher's
unvalidated **args splat. test_tool_identity_boundary.py attacks the real dispatch
path, discovers learner-reading tools rather than naming them, and is the guard that
actually holds the boundary.
"""

import ast
import inspect
import logging
import sqlite3
from typing import Any
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from untaught_language import UNTAUGHT_NAME

from reachy_language_tutor.tools import start_lesson as module

# Bound at module scope, which is what D28 made possible -- see tests/tools_module_graph.py.
from reachy_language_tutor.learners import store, get_progress
from reachy_language_tutor.lesson_session import LessonSessionHolder
from reachy_language_tutor.tools.core_tools import ToolDependencies
from reachy_language_tutor.tools.start_lesson import StartLesson


SEEDED_LEARNER = store.SEED_LEARNERS[0][0]

# Identity-shaped keys that must never be declared as parameters, and the lesson keys
# that must not join them either. The assertion checks the schema's one-key allow-list;
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
)


@pytest.fixture
def instance(tmp_path: Path) -> Path:
    """Prepare a learner database at a temporary instance path."""
    assert store.ensure_learner_database(tmp_path).ready is True
    return tmp_path


def _deps(**overrides: Any) -> ToolDependencies:
    """Build dependencies the way main.build_tool_dependencies does, fields stubbed.

    The holder is bound to the learner the bundle names, because that is what
    production does -- a holder bound to nobody refuses to pin anything, and a test
    suite that quietly used one would exercise the refusal path and call it success.
    """
    overrides.setdefault("lesson_session", LessonSessionHolder(overrides.get("current_learner_id")))
    return ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock(), **overrides)


async def _call(_kwargs: dict[str, Any] | None = None, **overrides: Any) -> dict[str, Any]:
    """Invoke the tool: _kwargs is what the model sent, overrides build the deps."""
    return await StartLesson()(_deps(**overrides), **(_kwargs or {}))


def _finish_every_lesson(learner_id: str, language_code: str, instance: Path) -> None:
    """Record a completed attempt at every lesson in one language."""
    progress = get_progress(learner_id, language_code, instance_path=instance)
    assert progress is not None
    for lesson in progress.remaining:
        outcome = store.record_result(learner_id, lesson.id, "completed", instance_path=instance)
        assert outcome.recorded is True, outcome.reason


# --- The schema is an allow-list of one key -------------------------------------------


def test_the_schema_declares_only_a_language_parameter() -> None:
    """The language is the only thing the conversation may choose."""
    schema = StartLesson.parameters_schema

    assert set(schema["properties"]) == {"language"}
    assert schema["required"] == ["language"]


@pytest.mark.parametrize("forbidden", FORBIDDEN_PARAMETERS)
def test_no_identity_or_lesson_shaped_key_appears_in_the_schema(forbidden: str) -> None:
    """A lesson parameter is the same breach as a learner parameter, one fact along."""
    assert forbidden not in StartLesson.parameters_schema["properties"]


def test_the_declared_enum_matches_the_seeded_catalog_in_order() -> None:
    """Order is load-bearing: the boundary suite's _benign_args takes enum[0].

    Spanish must stay first. The two probe learners over in
    test_tool_identity_boundary.py are told apart by their Spanish history, so a first
    entry naming a language neither has practised makes both answers identical and
    turns the injection test vacuous.
    """
    declared = StartLesson.parameters_schema["properties"]["language"]["enum"]

    assert declared == [name for _, name in store.SEED_LANGUAGES]


def test_the_two_language_tools_offer_the_model_the_same_words() -> None:
    """Two tools contradicting each other about which languages exist is a bug.

    Pinned as equality rather than as two separate lists, so they cannot drift.
    """
    from reachy_language_tutor.tools.get_progress import GetProgress

    assert (
        StartLesson.parameters_schema["properties"]["language"]["enum"]
        == GetProgress.parameters_schema["properties"]["language"]["enum"]
    )


# --- What it pins, and what it returns ------------------------------------------------


@pytest.mark.asyncio
async def test_it_returns_the_next_lessons_title_objective_and_position(instance: Path) -> None:
    """The tutor says what they are about to do and what it is for."""
    result = await _call({"language": "Spanish"}, current_learner_id=SEEDED_LEARNER, instance_path=instance)

    assert result["started"] is True
    assert result["language"] == "Spanish"
    assert result["language_code"] == "es"
    assert isinstance(result["lesson"]["position"], int)
    assert result["lesson"]["title"]
    assert result["lesson"]["objective"]
    assert result["remaining_after_this"] == result["remaining_count"] - 1
    assert result["total_lessons"] == result["completed_count"] + result["remaining_count"]


@pytest.mark.asyncio
async def test_the_lesson_it_pins_is_the_one_the_store_calls_next(instance: Path) -> None:
    """The whole point: the database chooses, not the caller and not the model."""
    deps = _deps(current_learner_id=SEEDED_LEARNER, instance_path=instance)
    expected = get_progress(SEEDED_LEARNER, "es", instance_path=instance)
    assert expected is not None and expected.next_lesson is not None

    result = await StartLesson()(deps, language="Spanish")

    pinned = deps.lesson_session.read_for(SEEDED_LEARNER)
    assert result["started"] is True
    assert pinned is not None
    assert pinned.lesson_id == expected.next_lesson.id
    assert pinned.language_code == "es"
    assert result["lesson"]["position"] == expected.next_lesson.position


@pytest.mark.asyncio
async def test_the_returned_lesson_carries_no_id_for_the_model_to_reuse(instance: Path) -> None:
    """Pinning it is what makes the id application state; handing it back undoes that."""
    result = await _call({"language": "Spanish"}, current_learner_id=SEEDED_LEARNER, instance_path=instance)

    assert "id" not in result["lesson"]
    assert "lesson_id" not in result


@pytest.mark.asyncio
async def test_a_learner_with_no_history_is_offered_the_first_lesson(instance: Path) -> None:
    """No history is not an error: it is somebody about to start at the beginning.

    Italian, not French. The seeded learner has no history in either, but French now
    refuses for want of written material, so asking it here would test that refusal
    rather than the fresh-start rule this test is named for.
    """
    result = await _call({"language": "Italian"}, current_learner_id=SEEDED_LEARNER, instance_path=instance)

    assert result["started"] is True
    assert result["lesson"]["position"] == 1
    assert result["completed_count"] == 0


@pytest.mark.asyncio
async def test_a_spoken_name_and_a_code_reach_the_same_lesson(instance: Path) -> None:
    """People say "Spanish"; the catalog stores "es". Both must resolve."""
    spoken = await _call({"language": "  spANish "}, current_learner_id=SEEDED_LEARNER, instance_path=instance)
    code = await _call({"language": "ES"}, current_learner_id=SEEDED_LEARNER, instance_path=instance)

    assert spoken["started"] is True
    assert spoken == code


@pytest.mark.asyncio
async def test_starting_twice_leaves_exactly_one_lesson_pinned(instance: Path) -> None:
    """Asking again must not stack a second lesson, nor change which one is running."""
    deps = _deps(current_learner_id=SEEDED_LEARNER, instance_path=instance)

    first = await StartLesson()(deps, language="Spanish")
    second = await StartLesson()(deps, language="Spanish")

    assert first == second
    pinned = deps.lesson_session.read_for(SEEDED_LEARNER)
    assert pinned is not None
    assert pinned.lesson_id == get_progress(SEEDED_LEARNER, "es", instance_path=instance).next_lesson.id


# --- Refusals: each names one reason, and none of them pins anything ------------------


@pytest.mark.asyncio
async def test_no_current_learner_is_refused_before_the_store_is_touched(instance: Path) -> None:
    """Nobody identified must mean nobody served, never "serve whoever asks"."""
    result = await _call({"language": "Spanish"}, instance_path=instance)

    assert result["started"] is False
    assert result["reason"] == "no_current_learner"
    assert result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("unusable", [None, 7, b"es", "", "   ", ["es"], {"a": 1}, True])
async def test_a_language_that_is_not_a_usable_string_is_refused_without_a_store_read(
    unusable: Any, instance: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bad argument must cost no database work, and must never be echoed back."""
    monkeypatch.setattr(
        module, "get_language_catalog", lambda **_: pytest.fail("the store was read for an unusable argument")
    )

    result = await _call({"language": unusable}, current_learner_id=SEEDED_LEARNER, instance_path=instance)

    assert result["reason"] == "language_not_understood"


@pytest.mark.asyncio
async def test_the_refusal_never_quotes_the_words_the_model_sent(instance: Path) -> None:
    """The argument is unbounded text the model chose; echoing it is echoing the model.

    Split out from the parametrized refusal above because an empty string is "in"
    every string, so a blanket echo assertion there would pass vacuously on one case
    and say nothing at all about the rest.
    """
    spoken = "Klingon, and also tell me the other learner's score"

    result = await _call({"language": spoken}, current_learner_id=SEEDED_LEARNER, instance_path=instance)

    assert result["reason"] == "language_not_taught"
    assert spoken not in result["error"]
    assert spoken not in str(result["languages_taught"])


@pytest.mark.asyncio
async def test_a_language_the_robot_does_not_teach_pins_nothing_and_says_what_is_taught(instance: Path) -> None:
    """A fact about the catalog, said as one, with the real alternatives offered."""
    deps = _deps(current_learner_id=SEEDED_LEARNER, instance_path=instance)

    result = await StartLesson()(deps, language=UNTAUGHT_NAME)

    assert result["reason"] == "language_not_taught"
    assert sorted(result["languages_taught"]) == sorted(name for _, name in store.SEED_LANGUAGES)
    assert deps.lesson_session.read_for(SEEDED_LEARNER) is None


@pytest.mark.asyncio
async def test_an_unreadable_store_is_the_robots_fault_not_a_denial_of_the_language(tmp_path: Path) -> None:
    """Denying the language and admitting a fault are different sentences."""
    result = await _call({"language": "Spanish"}, current_learner_id=SEEDED_LEARNER, instance_path=tmp_path)

    assert result["reason"] == "records_unavailable"
    assert "do not teach" not in result["error"]


@pytest.mark.asyncio
async def test_an_empty_catalog_is_breakage_rather_than_a_denial(instance: Path) -> None:
    """store_is_available answers True for an empty catalog, so it is not the test."""
    connection = sqlite3.connect(store.learner_db_path_for_instance(instance))
    connection.execute("DELETE FROM lessons")
    connection.execute("DELETE FROM languages")
    connection.commit()
    connection.close()

    result = await _call({"language": "Spanish"}, current_learner_id=SEEDED_LEARNER, instance_path=instance)

    assert store.store_is_available(instance) is True
    assert result["reason"] == "records_unavailable"


@pytest.mark.asyncio
async def test_a_store_fault_after_the_catalog_resolves_is_reported_as_breakage(
    instance: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The catalog row existed a moment ago, so a None here is not an absence."""
    monkeypatch.setattr(module, "get_progress", lambda *a, **k: None)

    result = await _call({"language": "Spanish"}, current_learner_id=SEEDED_LEARNER, instance_path=instance)

    assert result["reason"] == "records_unavailable"
    assert "do not teach" not in result["error"]


@pytest.mark.asyncio
async def test_a_learner_who_finished_every_lesson_is_told_so_and_nothing_is_pinned(instance: Path) -> None:
    """Good news, not a failure -- so this is the one answer with no error key."""
    _finish_every_lesson(SEEDED_LEARNER, "es", instance)
    deps = _deps(current_learner_id=SEEDED_LEARNER, instance_path=instance)

    result = await StartLesson()(deps, language="Spanish")

    assert result["started"] is False
    assert result["reason"] == "all_lessons_finished"
    assert "error" not in result
    assert result["remaining_count"] == 0
    assert result["completed_count"] == result["total_lessons"] > 0
    assert deps.lesson_session.read_for(SEEDED_LEARNER) is None


@pytest.mark.asyncio
async def test_a_catalog_language_with_no_lessons_says_so_rather_than_finished(instance: Path) -> None:
    """Telling somebody they finished a course that has no lessons is a lie about them."""
    connection = sqlite3.connect(store.learner_db_path_for_instance(instance))
    connection.execute("INSERT INTO languages (code, name) VALUES (?, ?)", ("nl", "Dutch"))
    connection.commit()
    connection.close()

    result = await _call({"language": "Dutch"}, current_learner_id=SEEDED_LEARNER, instance_path=instance)

    assert result["reason"] == "no_lessons_yet"
    assert result["reason"] != "all_lessons_finished"


@pytest.mark.asyncio
async def test_a_holder_bound_to_nobody_refuses_and_the_tool_says_so(instance: Path) -> None:
    """The pin is load-bearing: a lesson that was not pinned did not start."""
    deps = _deps(current_learner_id=SEEDED_LEARNER, instance_path=instance, lesson_session=LessonSessionHolder())

    result = await StartLesson()(deps, language="Spanish")

    assert result["started"] is False
    assert result["reason"] == "could_not_start"


@pytest.mark.asyncio
async def test_a_lesson_id_the_session_cannot_pin_is_reported_as_could_not_start(instance: Path) -> None:
    """Reachable from real data: lessons.id carries no shape CHECK, the holder does."""
    connection = sqlite3.connect(store.learner_db_path_for_instance(instance))
    connection.execute("INSERT INTO languages (code, name) VALUES (?, ?)", ("nl", "Dutch"))
    connection.execute(
        "INSERT INTO lessons (id, language_code, position, title, objective) VALUES (?, ?, ?, ?, ?)",
        ("nl 01 groeten", "nl", 1, "Greetings", "Say hello."),
    )
    # One dialogue turn, so the language HAS material. Without it start_lesson refuses
    # with lesson_not_written_yet and never reaches the pin, and this test is about the pin.
    connection.execute(
        "INSERT INTO lesson_dialogue_turns (lesson_id, position, speaker, text) VALUES (?, ?, ?, ?)",
        ("nl 01 groeten", 1, "Usted", "Goedendag."),
    )
    connection.commit()
    connection.close()
    deps = _deps(current_learner_id=SEEDED_LEARNER, instance_path=instance)

    result = await StartLesson()(deps, language="Dutch")

    assert result["reason"] == "could_not_start"
    assert deps.lesson_session.read_for(SEEDED_LEARNER) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("unusable", [None, 7, b"es", "", "   ", ["es"], {"a": 1}, True])
async def test_it_never_raises_for_any_argument_shape(unusable: Any, instance: Path) -> None:
    """An exception here is rendered to the model by _dispatch_tool_call, not handled."""
    result = await _call({"language": unusable}, current_learner_id=SEEDED_LEARNER, instance_path=instance)

    assert result["started"] is False


# --- The identity boundary, held in this tool's own source ----------------------------


@pytest.mark.asyncio
async def test_an_identity_in_kwargs_is_ignored_when_called_directly(instance: Path) -> None:
    """The injected id belongs to nobody; the answer must be the seeded learner's."""
    honest = await _call({"language": "Spanish"}, current_learner_id=SEEDED_LEARNER, instance_path=instance)

    attacked = await _call(
        {"language": "Spanish", "learner_id": "somebody-else", "user_id": "somebody-else"},
        current_learner_id=SEEDED_LEARNER,
        instance_path=instance,
    )

    assert attacked == honest


def test_the_tool_takes_no_named_parameter_beyond_its_dependencies() -> None:
    """Anything named in the signature is something the dispatcher's splat can fill."""
    parameters = inspect.signature(StartLesson.__call__).parameters

    assert list(parameters) == ["self", "deps", "kwargs"]
    assert parameters["kwargs"].kind is inspect.Parameter.VAR_KEYWORD


def test_the_module_mentions_kwargs_only_to_read_the_language_key() -> None:
    """The allow-list is the access SHAPE, not a list of spellings to refuse.

    Ported from test_get_progress.py, whose docstring carries the full derivation:
    kwargs.pop, kwargs.items, dict(kwargs)[...], a rebinding alias and forwarding
    **kwargs to a helper all read an identity while passing a two-pattern deny-list.
    So permit one thing -- the call kwargs.get("language") -- and refuse every other
    mention of the name.

    Same stated bound as the original: this reads SYNTAX, so a string-mediated dynamic
    read is not an ast.Name mention and passes. test_tool_identity_boundary.py is the
    primary control and catches an identity read behaviourally however it was spelled.
    """
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}

    mentions = [node for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id == "kwargs"]
    assert mentions, "the tool must actually read its argument, or this test proves nothing"

    for mention in mentions:
        attribute = parents.get(mention)
        assert isinstance(attribute, ast.Attribute) and attribute.attr == "get", (
            "kwargs may only be read via .get(); found another access form"
        )
        call = parents.get(attribute)
        assert isinstance(call, ast.Call) and call.func is attribute
        assert len(call.args) == 1
        assert isinstance(call.args[0], ast.Constant) and call.args[0].value == "language", (
            "the only key this module may take from kwargs is 'language'"
        )


# --- Reachability and the record ------------------------------------------------------


def test_start_lesson_is_listed_in_the_locked_profile() -> None:
    """A tool absent from the profile cannot be called at all."""
    from reachy_language_tutor import config
    from reachy_language_tutor.profile_store import read_profile_from_directory

    name = config.LOCKED_PROFILE
    assert name is not None
    profile = read_profile_from_directory(name, config.DEFAULT_PROFILES_DIRECTORY / name)

    assert "start_lesson" in profile.default_tools


def test_the_tool_name_matches_its_module_filename() -> None:
    """The loader resolves packaged tools by that correspondence."""
    assert Path(module.__file__).stem == StartLesson.name


@pytest.mark.asyncio
async def test_nothing_personal_reaches_a_log(instance: Path, caplog: pytest.LogCaptureFixture) -> None:
    """The learner id, and the lesson's title and id, are all values -- log the shape.

    The lesson title is here deliberately: it is not the learner's own data, but it
    says what they are working on, and lesson_session.py made the same decision one
    layer down. Asserting only on the learner id would let a title through.
    """
    expected = get_progress(SEEDED_LEARNER, "es", instance_path=instance)
    assert expected is not None and expected.next_lesson is not None

    with caplog.at_level(logging.DEBUG, logger=module.__name__):
        result = await _call({"language": "Spanish"}, current_learner_id=SEEDED_LEARNER, instance_path=instance)

    assert result["started"] is True
    records = [record for record in caplog.records if record.name == module.__name__]
    assert records, "the tool logged nothing, so this pin would pass vacuously"
    for record in records:
        rendered = f"{record.name} | {record.msg} | {record.args} | {record.getMessage()}"
        assert SEEDED_LEARNER not in rendered, rendered
        assert expected.next_lesson.id not in rendered, rendered
        assert expected.next_lesson.title not in rendered, rendered


# ------------------------------------------------- a language with nothing written in it
#
# The catalog advertises five languages and, when this was written, two of them had
# anything to teach from. Offering the other three beside Italian and Spanish told a
# learner something false, and starting one handed the model a title and an objective
# and asked it to teach from them -- which is how a child gets a lesson nobody reviewed.
#
# `lesson_not_written_yet` is the fourth empty answer, and the four mean different things to
# the person listening: I do not teach that (language_not_taught), I have no lessons
# planned (no_lessons_yet), I have the plan but nothing written (lesson_not_written_yet), and
# you have done them all (all_lessons_finished). Collapsing any pair loses the meaning.


def _language_with_lessons_but_no_material(instance: Path, code: str = "nl", name: str = "Dutch") -> None:
    """A catalogued language with a full syllabus and not a word written in it."""
    connection = sqlite3.connect(store.learner_db_path_for_instance(instance))
    try:
        connection.execute("INSERT INTO languages (code, name) VALUES (?, ?)", (code, name))
        for position in (1, 2):
            connection.execute(
                "INSERT INTO lessons (id, language_code, position, title, objective) VALUES (?, ?, ?, ?, ?)",
                (f"{code}-0{position}-placeholder", code, position, f"Lesson {position}", "An objective, and nothing else."),
            )
        connection.commit()
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_a_language_with_lessons_but_no_material_is_refused_distinctly(instance: Path) -> None:
    """The whole point: a plan is not something to teach from."""
    _language_with_lessons_but_no_material(instance)

    result = await _call({"language": "Dutch"}, current_learner_id=SEEDED_LEARNER, instance_path=instance)

    assert result["started"] is False
    assert result["reason"] == "lesson_not_written_yet"
    # Each of the other three would be a different, wrong thing to tell the learner.
    assert result["reason"] != "no_lessons_yet", "it has lessons; what it lacks is their content"
    assert result["reason"] != "all_lessons_finished", "they have finished nothing"
    assert result["reason"] != "language_not_taught", "it IS taught, and that is the problem"
    # And the answer names what CAN be taught, so the refusal is useful rather than bare.
    assert set(result["languages_with_material"]) == {"Italian", "Portuguese", "Spanish"}


@pytest.mark.asyncio
async def test_a_language_with_no_lessons_at_all_still_says_no_lessons_yet(instance: Path) -> None:
    """The distinction this nearly lost: no syllabus is not the same as an empty one.

    A language with no lessons also has no material, so an earlier version of the
    check fired first and turned `no_lessons_yet` into `lesson_not_written_yet`. The suite
    caught it. The ordering is load-bearing and this pins it.
    """
    connection = sqlite3.connect(store.learner_db_path_for_instance(instance))
    connection.execute("INSERT INTO languages (code, name) VALUES (?, ?)", ("da", "Danish"))
    connection.commit()
    connection.close()

    result = await _call({"language": "Danish"}, current_learner_id=SEEDED_LEARNER, instance_path=instance)

    assert result["reason"] == "no_lessons_yet"


@pytest.mark.asyncio
async def test_a_language_gaining_material_starts_working_with_no_code_change(instance: Path) -> None:
    """The property that makes this survive conversions landing one at a time.

    Nothing here edits a list of which languages are ready. A single dialogue turn is
    written into one lesson, and the same call that refused a moment ago now starts
    it -- because the signal is derived from the content tables on every read.
    """
    _language_with_lessons_but_no_material(instance)
    before = await _call({"language": "Dutch"}, current_learner_id=SEEDED_LEARNER, instance_path=instance)
    assert before["reason"] == "lesson_not_written_yet", "the precondition, or this proves nothing"

    connection = sqlite3.connect(store.learner_db_path_for_instance(instance))
    connection.execute(
        "INSERT INTO lesson_dialogue_turns (lesson_id, position, speaker, text) VALUES (?, ?, ?, ?)",
        ("nl-01-placeholder", 1, "Usted", "Goedendag."),
    )
    connection.commit()
    connection.close()

    after = await _call({"language": "Dutch"}, current_learner_id=SEEDED_LEARNER, instance_path=instance)

    assert after["started"] is True, "one content row is all it should take"
    assert after["lesson"]["position"] == 1


@pytest.mark.asyncio
async def test_a_language_with_material_in_only_some_lessons_is_not_refused(instance: Path) -> None:
    """Italian and Spanish today: six converted units and six placeholders each.

    The test is ANY, not ALL. A language whose first lessons are written and whose
    later ones are not is a normal language part way through conversion, and refusing
    it would take away the material that does exist.
    """
    result = await _call({"language": "Italian"}, current_learner_id=SEEDED_LEARNER, instance_path=instance)

    assert result["started"] is True
    assert "reason" not in result, "a started lesson carries no refusal reason"
    assert result["lesson"]["position"] == 1


def test_the_availability_signal_comes_from_the_database_and_not_from_the_call(instance: Path) -> None:
    """The security consideration: the conversation must not choose what is offered.

    start_lesson declares one property, `language`, and the catalog read takes no
    argument at all -- so there is no route by which anything the model says can make
    a language look ready. This asserts the shape rather than trusting it.
    """
    catalog_call = inspect.signature(store.get_language_catalog)
    assert list(catalog_call.parameters) == ["instance_path"], (
        "the catalog read gained an argument, which is a route for the conversation to influence it"
    )

    declared = module.StartLesson.parameters_schema["properties"]
    assert set(declared) == {"language"}, "start_lesson gained a property the model can fill in"

    # And has_material is not something a caller can pass: it is computed in SQL.
    source = Path(store.__file__).read_text(encoding="utf-8")
    assert "AS has_material" in source, "has_material is no longer derived in the catalog query"


@pytest.mark.asyncio
async def test_finishing_an_unwritten_language_says_finished_not_no_material(instance: Path) -> None:
    """The mirror of the collision above, and the one review had to find for me.

    Moving the has_material check after the lesson counts fixed `no_lessons_yet` and
    broke `all_lessons_finished`: a learner who had completed every lesson of a
    language with no written material was told "I have nothing written" rather than
    "you have finished them all". One collision fixed, its mirror opened -- the
    fix-the-member-not-the-class shape, inside a single function.

    The check now sits last, so it fires only when a lesson would otherwise start.
    """
    _language_with_lessons_but_no_material(instance)
    connection = sqlite3.connect(store.learner_db_path_for_instance(instance))
    for lesson_id in ("nl-01-placeholder", "nl-02-placeholder"):
        connection.execute(
            "INSERT INTO lesson_results (learner_id, lesson_id, outcome, score, recorded_at)"
            " VALUES (?, ?, 'completed', 90, 1)",
            (SEEDED_LEARNER, lesson_id),
        )
    connection.commit()
    connection.close()

    result = await _call({"language": "Dutch"}, current_learner_id=SEEDED_LEARNER, instance_path=instance)

    assert result["reason"] == "all_lessons_finished", "finishing a course is news, not a shortage"
    assert result["reason"] != "lesson_not_written_yet"


@pytest.mark.asyncio
async def test_the_two_tools_describe_the_catalog_the_same_way(instance: Path) -> None:
    """Sibling drift, caught in review: one tool split the catalog and the other did not.

    get_progress learned to answer with languages_with_material and
    languages_without_material_yet; start_lesson kept returning a flat list of all
    five names. Both answer the same question in the same conversation, so a learner
    could be told the robot teaches five languages by one tool and two by the other.
    """
    from reachy_language_tutor.tools.get_progress import GetProgress

    deps = _deps(current_learner_id=SEEDED_LEARNER, instance_path=instance)
    started = await StartLesson()(deps, language=UNTAUGHT_NAME)
    progressed = await GetProgress()(deps, language=UNTAUGHT_NAME)

    for answer in (started, progressed):
        assert set(answer["languages_with_material"]) == {"Italian", "Portuguese", "Spanish"}
        assert set(answer["languages_without_material_yet"]) == {"French", "German"}


@pytest.mark.asyncio
async def test_an_unwritten_lesson_in_a_written_language_is_refused_too(instance: Path) -> None:
    """The wall a per-LANGUAGE gate could not see, reachable today with no legacy data.

    Italian and Spanish each ship six converted units in front of six empty
    placeholders. The language has material, so a language-level check waves it
    through -- and lesson seven is a title and an objective with nothing behind it.
    start_lesson would open it and get_lesson_content would answer "we can work from
    what it is for", which is the improvised lesson this task exists to prevent,
    invited by the next tool call.

    Gating on the lesson that would actually start catches this and the
    whole-language case with one rule.
    """
    connection = sqlite3.connect(store.learner_db_path_for_instance(instance))
    converted = [
        row[0]
        for row in connection.execute(
            "SELECT id FROM lessons WHERE language_code = 'it' AND position <= 6 ORDER BY position"
        )
    ]
    assert len(converted) == 6, "the six converted Italian units, or this test is about something else"
    for lesson_id in converted:
        connection.execute(
            "INSERT INTO lesson_results (learner_id, lesson_id, outcome, score, recorded_at)"
            " VALUES (?, ?, 'completed', 90, 1)",
            (SEEDED_LEARNER, lesson_id),
        )
    connection.commit()
    connection.close()

    result = await _call({"language": "Italian"}, current_learner_id=SEEDED_LEARNER, instance_path=instance)

    assert result["started"] is False, "lesson seven has nothing written in it"
    assert result["reason"] == "lesson_not_written_yet"
    # And it is still honest about the language, which DOES have material.
    assert set(result["languages_with_material"]) == {"Italian", "Portuguese", "Spanish"}


@pytest.mark.asyncio
async def test_a_lesson_whose_content_cannot_be_read_is_not_started(instance: Path) -> None:
    """The gate must fail CLOSED, and this is the branch that let it fail open.

    `get_lesson_content` answers None for three different reasons, and one of them is
    a store it could not read. The first version of this gate permitted None, so with
    an unreadable database the lesson started, get_lesson_content then told the model
    "we can work from what it is for", and finish_lesson wrote the improvised lesson
    down as completed -- the exact behaviour this task exists to prevent, reached by
    the guard meant to prevent it.

    No test distinguished the two versions, which is how it survived review round 1.
    """
    deps = _deps(current_learner_id=SEEDED_LEARNER, instance_path=instance)

    def unreadable(*_args: Any, **_kwargs: Any) -> None:
        return None

    original = module.get_lesson_content
    module.get_lesson_content = unreadable
    try:
        result = await StartLesson()(deps, language="Italian")
    finally:
        module.get_lesson_content = original

    assert result["started"] is False, "an unreadable lesson must not start"
    assert result["reason"] == "could_not_start"
    assert deps.lesson_session.read_for(SEEDED_LEARNER) is None, "and nothing was pinned"
