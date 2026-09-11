"""Tests for the get_profile tool: the shape it returns, and the boundary it holds.

CLAUDE.md: "Tools must never accept a learner's identity from the conversation, so
nobody can talk their way into another person's profile." This tool is the first one
to read learner data, so it is the first place that rule can actually be broken. The
identity tests here are not style checks -- they are the security boundary.

These call the tool directly, which pins it in isolation but SKIPS the dispatcher's
unvalidated **args splat. test_tool_identity_boundary.py attacks the real dispatch
path, discovers learner-reading tools rather than naming them, and is the guard that
actually holds the boundary.
"""

import inspect
import logging
import sqlite3
from typing import Any
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from reachy_language_tutor.tools import get_profile as module
from reachy_language_tutor.learners import store
from reachy_language_tutor.tools.core_tools import ToolDependencies
from reachy_language_tutor.tools.get_profile import GetProfile


SEEDED_LEARNER = store.SEED_LEARNERS[0][0]


@pytest.fixture
def instance(tmp_path: Path) -> Path:
    """A prepared learner database at a temporary instance path."""
    assert store.ensure_learner_database(tmp_path).ready is True
    return tmp_path


def _deps(**overrides: Any) -> ToolDependencies:
    """Dependencies with the two required fields stubbed, as the existing tests do."""
    return ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock(), **overrides)


async def _call(**overrides: Any) -> dict[str, Any]:
    """Invoke the tool against dependencies built from the given overrides."""
    return await GetProfile()(_deps(**overrides))


# --- The shape it returns -------------------------------------------------------------


@pytest.mark.asyncio
async def test_returns_the_display_name_and_practised_languages(instance: Path) -> None:
    """Reachy greets by name and needs to know what the learner has worked on."""
    result = await _call(current_learner_id=SEEDED_LEARNER, instance_path=instance)

    assert "error" not in result
    assert result["display_name"] == store.SEED_LEARNERS[0][1]
    assert [language["code"] for language in result["languages"]] == ["es"]
    spanish = result["languages"][0]
    assert spanish["name"] == "Spanish"
    assert spanish["attempts"] >= 1
    assert spanish["lessons_completed"] >= 1


@pytest.mark.asyncio
async def test_a_language_only_attempted_still_counts_as_practised(instance: Path) -> None:
    """Starting French and finishing nothing is not the same as never touching it."""
    french = store.get_progress(SEEDED_LEARNER, "fr", instance_path=instance)
    assert french is not None and french.next_lesson is not None
    assert (
        store.record_result(SEEDED_LEARNER, french.next_lesson.id, "partial", instance_path=instance).recorded is True
    )

    result = await _call(current_learner_id=SEEDED_LEARNER, instance_path=instance)

    languages = {language["code"]: language for language in result["languages"]}
    assert set(languages) == {"es", "fr"}
    assert languages["fr"]["attempts"] == 1
    assert languages["fr"]["lessons_completed"] == 0


@pytest.mark.asyncio
async def test_a_learner_with_no_history_gets_an_empty_language_list(instance: Path) -> None:
    """A brand-new learner has a name but nothing practised; that is not an error."""
    connection = store.connect(instance)
    with connection:
        connection.execute(
            "INSERT INTO learners (id, display_name, created_at) VALUES (?, ?, ?)", ("newcomer", "New", 1)
        )
    connection.close()

    result = await _call(current_learner_id="newcomer", instance_path=instance)

    assert result == {"display_name": "New", "languages": []}


# --- The identity boundary -------------------------------------------------------------


def test_the_tool_declares_no_parameters() -> None:
    """Anything declared here is something the model can fill in, so nothing may be."""
    schema = GetProfile.parameters_schema
    assert schema["properties"] == {}
    assert schema.get("required", []) == []


@pytest.mark.asyncio
async def test_a_learner_identity_in_kwargs_is_ignored_when_called_directly(instance: Path) -> None:
    """Asking for someone else by name must not return their data, tool in isolation.

    This calls the tool directly, so it does not cover the dispatcher's unvalidated
    **args splat -- test_tool_identity_boundary.py does that. Kept because it is a
    second, independent guard and costs nothing.
    """
    connection = store.connect(instance)
    with connection:
        connection.execute(
            "INSERT INTO learners (id, display_name, created_at) VALUES (?, ?, ?)", ("housemate", "Housemate", 1)
        )
    connection.close()

    result = await GetProfile()(
        _deps(current_learner_id=SEEDED_LEARNER, instance_path=instance),
        learner_id="housemate",
        name="Housemate",
        user_id="housemate",
    )

    assert result["display_name"] == store.SEED_LEARNERS[0][1]
    assert "Housemate" not in str(result)


def test_the_tool_accepts_no_named_parameter_beyond_its_dependencies() -> None:
    """The likeliest regression is a widened signature, not a kwargs read.

    The dispatcher splats the model's JSON straight in (`await tool(deps, **args)`)
    with no schema validation, so `parameters_schema` is advisory at runtime and the
    only thing standing between the model and a named `learner_id` argument is this
    signature. `**kwargs` itself must stay -- one dispatch path injects `tool_manager`
    -- so the check is on the named parameters, which is where an identity would land.
    """
    parameters = inspect.signature(GetProfile.__call__).parameters
    assert list(parameters) == ["self", "deps", "kwargs"]
    assert parameters["kwargs"].kind is inspect.Parameter.VAR_KEYWORD


def test_the_tool_module_never_reads_an_identity_from_kwargs() -> None:
    """A weak backstop, kept deliberately and worth being honest about.

    This catches the two obvious spellings and nothing else -- not `kwargs.pop`, not
    `dict(kwargs)`, not `locals()`, not `**kwargs` forwarded into a store call. The
    test that actually holds the boundary is the behavioural one above, which passes
    a housemate's id and asserts their data does not come back.
    """
    text = Path(module.__file__).read_text(encoding="utf-8")
    assert "kwargs.get" not in text, "get_profile must not read anything out of kwargs"
    assert "kwargs[" not in text, "get_profile must not read anything out of kwargs"


# --- Failure modes return an error dict, never an exception ----------------------------


@pytest.mark.asyncio
async def test_no_current_learner_returns_an_error_dict(instance: Path) -> None:
    """Unset identity must refuse rather than guess whose profile to read."""
    result = await _call(instance_path=instance)

    assert "error" in result
    assert "display_name" not in result


@pytest.mark.asyncio
async def test_an_unknown_learner_returns_an_error_dict(instance: Path) -> None:
    """A learner with no row is a fact about the person, not a fault in the robot."""
    result = await _call(current_learner_id="no-such-learner", instance_path=instance)

    assert "error" in result
    assert "profile saved" in result["error"]


@pytest.mark.asyncio
async def test_an_unreadable_store_says_so_rather_than_denying_the_person(tmp_path: Path) -> None:
    """Saying "I have no profile for you" when the database is broken is a falsehood."""
    result = await _call(current_learner_id=SEEDED_LEARNER, instance_path=tmp_path)

    assert "error" in result
    assert "cannot reach my records" in result["error"]


@pytest.mark.asyncio
async def test_a_store_failure_mid_call_does_not_raise_into_the_conversation(
    instance: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tool that raises breaks the voice loop; it must return an error dict instead."""

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(module, "get_profile", boom)
    monkeypatch.setattr(module, "store_is_available", lambda *a, **k: False)

    with pytest.raises(sqlite3.OperationalError):
        module.get_profile(SEEDED_LEARNER)  # the patch really is in force

    # The store's own readers absorb sqlite errors, which is why the tool does not
    # need its own try/except; this pins that the contract is the store's, not luck.
    assert store.get_profile("anyone", instance_path=Path("/nonexistent")) is None
    assert store.get_practised_languages("anyone", instance_path=Path("/nonexistent")) == ()


# --- It is actually reachable from the conversation --------------------------------------


def test_get_profile_is_listed_in_the_locked_profile() -> None:
    """A tool absent from default_tools is not available to the conversation at all."""
    from reachy_language_tutor import config
    from reachy_language_tutor.profile_store import read_profile_from_directory

    name = config.LOCKED_PROFILE
    profile = read_profile_from_directory(name, config.DEFAULT_PROFILES_DIRECTORY / name)
    assert "get_profile" in profile.default_tools


def test_the_tool_name_matches_its_module_filename() -> None:
    """The runtime loader imports tools.<name>, so a mismatch silently fails to load."""
    assert GetProfile.name == "get_profile"
    assert Path(module.__file__).stem == GetProfile.name


def test_the_runtime_loader_actually_registers_the_tool() -> None:
    """The loader imports tools.<name>, so only this proves the file is wired up.

    test_locked_profile.py scans every module under tools/, so it would pass even if
    the file were named something else -- it is the runtime path that would break.
    """
    from reachy_language_tutor.tools import core_tools

    registry = core_tools._build_tool_registry(core_tools._load_enabled_tools(["get_profile"], []))
    assert sorted(registry) == ["get_profile"]
    # Identity by module and class name, not isinstance: another test in this suite
    # reloads the tools modules, so the registry's class object is not always the one
    # imported at the top of this file even though it is the same class.
    loaded = type(registry["get_profile"])
    assert (loaded.__module__, loaded.__name__) == (GetProfile.__module__, GetProfile.__name__)
    # The spec is exactly what the model is shown, so assert the boundary there too.
    assert registry["get_profile"].spec()["parameters"]["properties"] == {}


def test_the_profile_persona_does_not_still_deny_having_profile_access() -> None:
    """Shipping the tool while the persona refuses to use it would deliver nothing."""
    from reachy_language_tutor import config

    text = (config.DEFAULT_PROFILES_DIRECTORY / config.LOCKED_PROFILE / "profile.md").read_text(encoding="utf-8")
    assert "do NOT yet have access to" not in text


@pytest.mark.asyncio
async def test_nothing_personal_is_logged(instance: Path, caplog: pytest.LogCaptureFixture) -> None:
    """~20 real households use this; names must not reach the log."""
    with caplog.at_level(logging.DEBUG):
        await _call(current_learner_id=SEEDED_LEARNER, instance_path=instance)

    messages = " ".join(record.getMessage() for record in caplog.records)
    assert SEEDED_LEARNER not in messages
    assert store.SEED_LEARNERS[0][1] not in messages
