"""Tests for the get_progress tool: the shape it returns, and the boundary it holds.

CLAUDE.md: "Tools must never accept a learner's identity from the conversation, so
nobody can talk their way into another person's profile." This tool declares a
parameter, which get_profile does not, so it is the first learner-reading tool where
the model fills something in -- and the first place a wrong parameter could be added.
The identity tests here are not style checks; they are the security boundary.

These call the tool directly, which pins it in isolation but SKIPS the dispatcher's
unvalidated **args splat. test_tool_identity_boundary.py attacks the real dispatch
path, discovers learner-reading tools rather than naming them, and is the guard that
actually holds the boundary.

One test here exists for a bug that is invisible from the tool's own source. The
store's get_progress takes a catalog CODE and answers a SILENT None for a language it
does not teach -- but "spanish" is a well-formed code that simply matches no row, so
passing a spoken name down would have the tutor deny teaching a language it teaches.
That is why the tool resolves against the catalog first, and why
test_an_unreadable_store_says_so_rather_than_denying_the_language pins it.
"""

import ast
import inspect
import logging
from typing import Any
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from reachy_language_tutor.tools import get_progress as module
from reachy_language_tutor.learners import store
from reachy_language_tutor.tools.core_tools import ToolDependencies
from reachy_language_tutor.tools.get_progress import GetProgress


SEEDED_LEARNER = store.SEED_LEARNERS[0][0]

# Identity-shaped keys that must never be declared as parameters. Named positively as
# the set that is forbidden, because the schema is an allow-list of exactly one key
# and the assertion below checks that allow-list rather than this list.
IDENTITY_KEYS = (
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
)


@pytest.fixture
def instance(tmp_path: Path) -> Path:
    """A prepared learner database at a temporary instance path."""
    assert store.ensure_learner_database(tmp_path).ready is True
    return tmp_path


def _deps(**overrides: Any) -> ToolDependencies:
    """Dependencies with the two required fields stubbed, as the existing tests do."""
    return ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock(), **overrides)


async def _call(_kwargs: dict[str, Any] | None = None, **overrides: Any) -> dict[str, Any]:
    """Invoke the tool: _kwargs is what the model sent, overrides build the deps."""
    return await GetProgress()(_deps(**overrides), **(_kwargs or {}))


# --- The shape it returns -------------------------------------------------------------


@pytest.mark.asyncio
async def test_it_returns_counts_and_the_next_lesson_for_the_seeded_learner(instance: Path) -> None:
    """The tutor says what is done, what is left, and what comes next."""
    result = await _call({"language": "Spanish"}, current_learner_id=SEEDED_LEARNER, instance_path=instance)

    assert "error" not in result
    assert result["language"] == "Spanish"
    assert result["language_code"] == "es"
    assert result["completed_count"] == 2
    assert result["remaining_count"] == 4
    assert result["total_lessons"] == 6
    assert result["next_lesson"]["position"] == 3


@pytest.mark.asyncio
async def test_the_next_lesson_is_the_one_the_store_says_it_is(instance: Path) -> None:
    """The database decides what is next; the tool must not compute its own answer."""
    expected = store.get_progress(SEEDED_LEARNER, "es", instance_path=instance)
    assert expected is not None and expected.next_lesson is not None

    result = await _call({"language": "Spanish"}, current_learner_id=SEEDED_LEARNER, instance_path=instance)

    assert result["next_lesson"]["id"] == expected.next_lesson.id
    assert result["completed_count"] == len(expected.completed)
    assert result["remaining_count"] == len(expected.remaining)


@pytest.mark.asyncio
async def test_it_summarises_the_lesson_sets_rather_than_listing_them(instance: Path) -> None:
    """A spoken tutor says "two of six", never a six-item list."""
    result = await _call({"language": "Spanish"}, current_learner_id=SEEDED_LEARNER, instance_path=instance)

    assert isinstance(result["completed_count"], int)
    assert isinstance(result["remaining_count"], int)
    # next_lesson is the one row the tutor teaches from; nothing else may be a list of
    # lessons. languages_taught cannot appear here -- it is the not-taught branch only.
    for key, value in result.items():
        assert key == "next_lesson" or not isinstance(value, (list, tuple)), f"{key} enumerates rows"


@pytest.mark.asyncio
async def test_no_score_or_attempt_history_is_returned(instance: Path) -> None:
    """Scores are the most sensitive field and speaking a short answer does not need them."""
    result = await _call({"language": "Spanish"}, current_learner_id=SEEDED_LEARNER, instance_path=instance)

    assert "attempts" not in result
    progress = store.get_progress(SEEDED_LEARNER, "es", instance_path=instance)
    assert progress is not None and progress.attempts, "seed data must carry attempts for this to test anything"
    rendered = str(result)
    for attempt in progress.attempts:
        if attempt.score is not None:
            assert str(attempt.score) not in rendered


@pytest.mark.asyncio
async def test_a_catalog_code_is_accepted_as_well_as_a_name(instance: Path) -> None:
    """The model is hinted with names, but a code must not be refused as unknown."""
    by_name = await _call({"language": "Spanish"}, current_learner_id=SEEDED_LEARNER, instance_path=instance)
    by_code = await _call({"language": "es"}, current_learner_id=SEEDED_LEARNER, instance_path=instance)

    assert by_code == by_name


@pytest.mark.asyncio
async def test_the_language_is_matched_case_insensitively_and_trimmed(instance: Path) -> None:
    """The bug this design exists to prevent.

    store.get_progress takes a catalog code: "spanish" is a well-formed code that
    matches no row, and its None is SILENT, which by that function's own contract
    means "not taught here". Passing a spoken name straight down would have the robot
    deny teaching Spanish. Reverting the catalog resolution fails this test.
    """
    result = await _call({"language": "  spANish "}, current_learner_id=SEEDED_LEARNER, instance_path=instance)

    assert "error" not in result, "a spoken language name must not read as an unknown language"
    assert result["language_code"] == "es"


@pytest.mark.asyncio
async def test_a_learner_with_no_history_gets_the_first_lesson_as_next(instance: Path) -> None:
    """Never practised is not an error: it is a fresh start, and the tutor offers lesson one."""
    result = await _call({"language": "French"}, current_learner_id=SEEDED_LEARNER, instance_path=instance)

    assert "error" not in result
    assert result["completed_count"] == 0
    assert result["last_completed"] is None
    assert result["next_lesson"]["position"] == 1


@pytest.mark.asyncio
async def test_an_unknown_learner_gets_a_fresh_start_rather_than_an_error(instance: Path) -> None:
    """The lesson catalog is not personal data, so there is nothing to withhold."""
    result = await _call({"language": "Spanish"}, current_learner_id="nobody-here", instance_path=instance)

    assert "error" not in result
    assert result["completed_count"] == 0
    assert result["next_lesson"]["position"] == 1


@pytest.mark.asyncio
async def test_a_finished_language_reports_no_next_lesson(instance: Path) -> None:
    """Every lesson done is a real state, and next_lesson is None rather than missing."""
    progress = store.get_progress(SEEDED_LEARNER, "es", instance_path=instance)
    assert progress is not None
    for lesson in progress.remaining:
        assert store.record_result(SEEDED_LEARNER, lesson.id, "completed", instance_path=instance).recorded

    result = await _call({"language": "Spanish"}, current_learner_id=SEEDED_LEARNER, instance_path=instance)

    assert result["remaining_count"] == 0
    assert result["next_lesson"] is None
    assert result["completed_count"] == result["total_lessons"]


# --- The identity boundary ------------------------------------------------------------


def test_the_schema_declares_only_a_language_parameter() -> None:
    """An allow-list of exactly one key: anything else is something the model can fill in."""
    schema = GetProgress.parameters_schema

    assert set(schema["properties"]) == {"language"}
    assert schema["required"] == ["language"]


def test_no_identity_shaped_key_can_appear_in_the_schema() -> None:
    """The security boundary from CLAUDE.md, asserted where it would first be broken."""
    declared = set(GetProgress.parameters_schema["properties"])

    for key in IDENTITY_KEYS:
        assert key not in declared


@pytest.mark.asyncio
async def test_a_learner_identity_in_kwargs_is_ignored_when_called_directly(instance: Path) -> None:
    """The dispatcher splats the model's JSON unvalidated, so extra keys really do arrive."""
    clean = await _call({"language": "Spanish"}, current_learner_id=SEEDED_LEARNER, instance_path=instance)
    poisoned = await _call(
        {
            "language": "Spanish",
            "learner_id": "housemate",
            "name": "Someone Else",
            "user_id": "housemate",
        },
        current_learner_id=SEEDED_LEARNER,
        instance_path=instance,
    )

    assert poisoned == clean


def test_the_tool_accepts_no_named_parameter_beyond_its_dependencies() -> None:
    """A named parameter would be a second way in; kwargs keeps the surface one key wide."""
    parameters = inspect.signature(GetProgress.__call__).parameters

    assert list(parameters) == ["self", "deps", "kwargs"]
    assert parameters["kwargs"].kind is inspect.Parameter.VAR_KEYWORD


def test_the_module_mentions_kwargs_only_to_read_the_language_key() -> None:
    """The allow-list is the access SHAPE, not a list of spellings to refuse.

    An earlier version of this test matched two node patterns -- kwargs.get("literal")
    and kwargs["literal"] -- and its docstring claimed that made it an allow-list. It
    did not. kwargs.pop("learner_id"), kwargs.items(), dict(kwargs)["learner_id"],
    kwargs.setdefault(...), a composed key, an alias `kw = kwargs`, and forwarding
    **kwargs to a helper all read an identity while passing it. That is the deny-list
    shape CLAUDE.md records as costing four review rounds, written as an allow-list.

    So permit one thing and refuse everything else: the name `kwargs` may appear in
    this module only as the call kwargs.get("language"). Any other mention of that
    name -- any method, any subscript, any binding, any forwarding -- fails.

    The bound, stated rather than overclaimed: this reads the module's SYNTAX, so it
    only sees access that names `kwargs`. String-mediated dynamic reads alongside the
    permitted call -- locals()["kwargs"], eval(...), sys._getframe().f_locals -- are
    not ast.Name mentions and pass it; all three were executed against this guard to
    confirm that. Closing them statically is not worth it, because
    tests/test_tool_identity_boundary.py is the primary control and catches an
    identity read behaviourally however it was spelled. This is defence in depth
    over the spellings a maintainer would plausibly reach for, not a proof.
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


# --- Failure modes return an error dict, never an exception ---------------------------


@pytest.mark.asyncio
async def test_no_current_learner_returns_an_error_dict(instance: Path) -> None:
    """Nobody recognised yet is a fact about the robot's state, not a lookup failure."""
    result = await _call({"language": "Spanish"}, current_learner_id=None, instance_path=instance)

    assert "error" in result
    assert "completed_count" not in result


@pytest.mark.asyncio
async def test_a_missing_language_returns_an_error_dict(instance: Path) -> None:
    """The model can omit a required argument; that must not raise into the conversation."""
    result = await _call({}, current_learner_id=SEEDED_LEARNER, instance_path=instance)

    assert "error" in result


@pytest.mark.asyncio
async def test_a_non_string_language_is_refused_without_touching_the_store(
    instance: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The voice loop must stay responsive: a bad argument costs no database work."""

    def _fail(**_: Any) -> None:
        raise AssertionError("the store was read for an argument that could never match")

    monkeypatch.setattr(module, "get_language_catalog", _fail)

    result = await _call({"language": 7}, current_learner_id=SEEDED_LEARNER, instance_path=instance)

    assert "error" in result


@pytest.mark.asyncio
async def test_an_unknown_language_returns_a_clear_error_and_says_what_is_taught(instance: Path) -> None:
    """Saying which languages exist lets the turn recover in one exchange."""
    result = await _call({"language": "German"}, current_learner_id=SEEDED_LEARNER, instance_path=instance)

    assert "error" in result
    assert sorted(result["languages_taught"]) == ["French", "Spanish"]


@pytest.mark.asyncio
async def test_an_unknown_language_is_not_an_empty_success(instance: Path) -> None:
    """An empty success would have the tutor report zero progress in a real language."""
    result = await _call({"language": "German"}, current_learner_id=SEEDED_LEARNER, instance_path=instance)

    assert "completed_count" not in result
    assert "next_lesson" not in result


@pytest.mark.asyncio
async def test_an_unreadable_store_says_so_rather_than_denying_the_language(tmp_path: Path) -> None:
    """Breakage must never be spoken as absence.

    This is the regression test for the whole design: the tutor telling a learner it
    does not teach Spanish, because the store was unreadable, is the failure the
    catalog lookup exists to prevent.
    """
    result = await _call(
        {"language": "Spanish"}, current_learner_id=SEEDED_LEARNER, instance_path=tmp_path / "nonexistent"
    )

    assert "error" in result
    assert "do not teach" not in result["error"]
    assert "languages_taught" not in result


@pytest.mark.asyncio
async def test_an_empty_catalog_is_reported_as_breakage_not_as_not_taught(instance: Path) -> None:
    """store_is_available answers True for an empty catalog, so it cannot be the test."""
    connection = store.connect(instance)
    connection.execute("DELETE FROM languages")
    connection.commit()
    connection.close()
    assert store.store_is_available(instance) is True

    result = await _call({"language": "Spanish"}, current_learner_id=SEEDED_LEARNER, instance_path=instance)

    assert "error" in result
    assert "do not teach" not in result["error"]


@pytest.mark.asyncio
async def test_a_store_failure_after_the_catalog_resolves_does_not_raise(
    instance: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The catalog row existed a moment ago, so a None here is a fault, not an absence."""
    monkeypatch.setattr(module, "get_progress", lambda *args, **kwargs: None)

    result = await _call({"language": "Spanish"}, current_learner_id=SEEDED_LEARNER, instance_path=instance)

    assert "error" in result
    assert "do not teach" not in result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("language", [None, 7, b"es", "", "   ", ["es"], {"a": 1}, True])
async def test_it_never_raises_for_any_argument_shape(instance: Path, language: Any) -> None:
    """A tool raising into the conversation loop breaks the turn; it returns a dict instead."""
    result = await _call({"language": language}, current_learner_id=SEEDED_LEARNER, instance_path=instance)

    assert isinstance(result, dict)
    assert "error" in result


# --- It is actually reachable from the conversation --------------------------------------


def test_get_progress_is_listed_in_the_locked_profile() -> None:
    """A tool absent from default_tools is not available to the conversation at all."""
    from reachy_language_tutor import config
    from reachy_language_tutor.profile_store import read_profile_from_directory

    name = config.LOCKED_PROFILE
    profile = read_profile_from_directory(name, config.DEFAULT_PROFILES_DIRECTORY / name)
    assert "get_progress" in profile.default_tools


def test_the_tool_name_matches_its_module_filename() -> None:
    """The runtime loader imports tools.<name>, so a mismatch silently fails to load."""
    assert GetProgress.name == "get_progress"
    assert Path(module.__file__).stem == GetProgress.name


def test_the_runtime_loader_actually_registers_the_tool() -> None:
    """The loader imports tools.<name>, so only this proves the file is wired up."""
    from reachy_language_tutor.tools import core_tools

    registry = core_tools._build_tool_registry(core_tools._load_enabled_tools(["get_progress"], []))
    assert sorted(registry) == ["get_progress"]
    # Identity by module and class name, not isinstance: another test in this suite
    # reloads the tools modules, so the registry's class object is not always the one
    # imported at the top of this file even though it is the same class.
    loaded = type(registry["get_progress"])
    assert (loaded.__module__, loaded.__name__) == (GetProgress.__module__, GetProgress.__name__)
    # The spec is exactly what the model is shown, so assert the boundary there too.
    assert set(registry["get_progress"].spec()["parameters"]["properties"]) == {"language"}


def test_the_declared_enum_matches_the_seeded_catalog() -> None:
    """A static hint can go stale; this is what keeps it honest against the seed data."""
    declared = GetProgress.parameters_schema["properties"]["language"]["enum"]

    assert sorted(declared) == sorted(name for _, name in store.SEED_LANGUAGES)


def test_the_profile_persona_no_longer_denies_progress_lookup() -> None:
    """Shipping the tool while the persona refuses to use it would deliver nothing."""
    from reachy_language_tutor import config

    text = (config.DEFAULT_PROFILES_DIRECTORY / config.LOCKED_PROFILE / "profile.md").read_text(encoding="utf-8")
    assert "cannot look that up yet" not in text
    assert "arrive in a later version" not in text


@pytest.mark.asyncio
async def test_nothing_personal_is_logged(instance: Path, caplog: pytest.LogCaptureFixture) -> None:
    """~20 real households use this; names, ids and progress must not reach the log."""
    with caplog.at_level(logging.DEBUG):
        await _call({"language": "Spanish"}, current_learner_id=SEEDED_LEARNER, instance_path=instance)

    messages = " ".join(record.getMessage() for record in caplog.records)
    assert SEEDED_LEARNER not in messages
    assert store.SEED_LEARNERS[0][1] not in messages
    # Progress is learner data too: lesson titles and scores are as personal as a name.
    progress = store.get_progress(SEEDED_LEARNER, "es", instance_path=instance)
    assert progress is not None
    for lesson in progress.completed:
        assert lesson.title not in messages
