"""Attack the tool dispatch path the way a person talking to the robot would.

CLAUDE.md: "The app sets the current learner ID from recognition. Tools must never
accept a learner's identity from the conversation, so nobody can talk their way into
another person's profile."

The empty parameters_schema does not enforce that at runtime. `_safe_load_obj` parses
whatever the model emitted with no schema validation, and `_dispatch_tool_call` splats
it as `await tool(deps, **args)` -- so the model can put any key it likes into a tool's
kwargs. What holds the boundary is that tools read nothing out of them.

**Every registered tool is attacked, not only the ones that look like they read learner
data.** Discovery is used to decide which tools must be *observably* identity-dependent,
never to decide which get attacked -- a tool that reads a learner through a helper named
nothing in particular would escape any discovery rule, and there are only fifteen tools.

A second learner lives in the fixture with a name and real lesson results, mirror-imaged
against the seeded one -- she practises French where he practises Spanish -- so a leak
changes the name, the id, the language and the scores at once. Her whole stored history
is turned into forbidden tokens, so a leak of progress is caught as well as a leak of a
name. One test exists purely to prove a leak WOULD be visible.

Production passes ONE ToolDependencies to every call for the life of a session, so the
attack reuses a single deps object and asserts the identity on it is unchanged after
every dispatch. The dataclass now seals that field after construction, so a tool that
assigns it raises instead of repointing the session, and the dispatcher turns that into
an error dict the equality half notices. The end-state check stays, because the seal
only covers the attribute protocol: a write through deps.__dict__ goes around it and
does repoint every later turn, which is what the poisoning control below now uses to
prove that check can still fail.

What this does NOT catch, stated rather than implied:
  - A leak that is not driven by the injected keys -- an unconditional extra field --
    is identical in baseline and attacked. Only the forbidden-token check can see it,
    and only if the value appears literally.
  - A decomposed, transformed, truncated or repr-opaque value. Token matching is
    case-insensitive and covers each name part separately, but it is still matching.
  - Tools outside the shipped package: external file-backed tools from TOOLS_DIRECTORY,
    and RemoteMcpTool instances, which forward the model's kwargs verbatim to a remote
    server. Only their declared schemas are checked here. Milestone 5 moves learner data
    behind exactly that kind of call, and this suite does not follow it there.
  - A leak into a log rather than a return value. test_log_redaction.py guards that.
"""

import re
import ast
import sys
import json
import random
import inspect
import pkgutil
import textwrap
import importlib
import dataclasses
from types import ModuleType
from typing import Any
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from reachy_language_tutor import tools
from reachy_language_tutor.tools import core_tools
from reachy_language_tutor.learners import store

# For typing and subclassing ONLY. Never use these in an isinstance/issubclass check:
# another test file purges and re-imports the tools package, which would leave these
# names bound to dead classes and make such a check quietly return False. The `core`
# fixture below exists for the same reason.
from reachy_language_tutor.lesson_session import LessonSessionHolder, LessonSessionRefusedError
from reachy_language_tutor.tools.core_tools import Tool, ToolDependencies


PRIMARY_LEARNER = store.SEED_LEARNERS[0][0]
PRIMARY_NAME = store.SEED_LEARNERS[0][1]

# Nonsense on purpose: it cannot be a substring of any seeded content, so a match is
# unambiguous. Mirror-imaged against the seeded learner -- he practises Spanish, she
# practises French -- so a leak shows up in the language set as well as the name.
HOUSEMATE_ID = "housemate-w11"
HOUSEMATE_NAME = "Zerelda Quackenbush"
HOUSEMATE_LANGUAGE = "fr"

# Spellings a model might reach for, UNIONED with the identity-bearing field names on
# ToolDependencies itself. Deriving the second half matters more than the first: the
# likeliest key a violating tool reads is the one it can see on deps, and a hand list
# is exactly what forgets it. A new identity dep joins this payload automatically.
_IDENTITY_FIELD_NAMES = frozenset(
    field.name for field in dataclasses.fields(ToolDependencies) if "learner" in field.name or "user" in field.name
)
_IDENTITY_KEYS = _IDENTITY_FIELD_NAMES | {
    "learner_id",
    "learner",
    "learner_name",
    "name",
    "display_name",
    "user_id",
    "username",
    "person_id",
    "profile_id",
    "student_id",
    "speaker",
    "for_user",
    "who",
    "id",
    "email",
}
INJECTED_IDENTITY: dict[str, Any] = dict.fromkeys(sorted(_IDENTITY_KEYS), HOUSEMATE_ID)
INJECTED_IDENTITY["name"] = HOUSEMATE_NAME
INJECTED_IDENTITY["display_name"] = HOUSEMATE_NAME
INJECTED_IDENTITY["email"] = "zerelda@example.invalid"

# Every real entry point, and the list is CHECKED against the module rather than
# remembered -- see test_every_public_dispatch_entry_point_is_attacked below. D36
# added dispatch_tool_call_reporting_tool and made it the only one production calls;
# this tuple still named the other two, so the suite was attacking two paths the app
# no longer uses and none that it does. A hand-maintained list of what to attack is
# only ever as current as the last person to edit it.
#
# dispatch_tool_call_with_manager writes tool_manager into args before the same
# unvalidated splat, which is why the **kwargs catch-all must stay.
# dispatch_tool_call_reporting_tool does the same when a manager is passed, and
# returns (result, tool) rather than a bare dict.
ENTRY_POINTS = (
    "dispatch_tool_call",
    "dispatch_tool_call_with_manager",
    "dispatch_tool_call_reporting_tool",
)

# Non-degenerate on purpose. An empty string is rejected by remember, forget, camera
# and task_cancel at their first guard clause, so the attack would land on the input
# validator and never reach code that could read an identity -- a call that counts as
# attacked and is not.
_TYPE_DEFAULTS: dict[str, Any] = {
    "string": "w11",
    "integer": 1,
    "number": 1,
    "boolean": False,
    "array": [],
    "object": {},
}

# Tools whose baseline answer is an error dict, so the attack reaches their validator
# rather than their body. An entry here is a deliberate admission, not a silent gap.
#
# These seven refuse for want of a RUNTIME dependency a test harness does not supply --
# a camera, a real movement manager, a background tool manager, a go_to_sleep callback --
# not for want of a better argument. None of them reads learner data, and a test below
# asserts that: a waiver that ever covered a learner-reading tool would be hiding exactly
# what this suite exists to find.
ATTACKED_ONLY_AT_THE_VALIDATOR: frozenset[str] = frozenset(
    {"camera", "forget", "go_to_sleep", "move_head", "sweep_look", "task_cancel", "task_status"}
)

# Tools that do not answer consistently for the same input, so the equality half of
# the attack cannot be used against them. Same rule: explicit, never implicit.
NOT_SELF_CONSISTENT: frozenset[str] = frozenset()

# Joins the parts of one recorded attempt into a single forbidden token.
PAIRING_SEPARATOR = "|"

# Discovered learner-reading tools that cannot be shown to be identity-dependent.
# An entry here is a deliberate, commented admission that the tool is attacked but its
# answer does not vary by learner, so the differential half proves nothing for it.
NOT_DIFFERENTIALLY_OBSERVABLE: frozenset[str] = frozenset()


# --- Discovery, used for observability rather than for who gets attacked ----------------


def _reads_learner_data(source: str) -> bool:
    """Report whether a tool module touches learner data, by either of two signals.

    The union, not the intersection: a tool that reads deps.current_learner_id and
    hands it to a helper never imports the learners package. This decides which tools
    must be OBSERVABLY identity-dependent; it never decides which are attacked, because
    a rule like this can always be sidestepped by indirection.
    """
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("reachy_language_tutor.learners"):
            return True
        if isinstance(node, ast.Import) and any(
            a.name.startswith("reachy_language_tutor.learners") for a in node.names
        ):
            return True
        if isinstance(node, ast.Attribute) and node.attr in _IDENTITY_FIELD_NAMES:
            return True
    return False


def _tool_module_names() -> list[str]:
    """Return every module in the tools package, by name."""
    return [module.name for module in pkgutil.iter_modules(tools.__path__)]


def _module_source(name: str) -> str:
    """Read one tool module's source without importing it."""
    return (Path(tools.__path__[0]) / f"{name}.py").read_text(encoding="utf-8")


def _defining_module(tool: Tool) -> str:
    """Return the tools-package module name a registered tool was defined in."""
    return type(tool).__module__.rsplit(".", 1)[-1]


# --- Fixtures --------------------------------------------------------------------------


@pytest.fixture
def core() -> ModuleType:
    """Import core_tools per test rather than holding a module-level reference.

    Another test file purges every reachy_language_tutor.tools.* module from
    sys.modules and re-imports core_tools. A reference held across that purge goes
    stale, and then issubclass(GetProfile, Tool) compares a fresh class against a dead
    base, returns False, and the registry comes back EMPTY -- a silent vacuous pass.
    """
    return importlib.import_module("reachy_language_tutor.tools.core_tools")


@pytest.fixture
def registry(core: ModuleType) -> dict[str, Tool]:
    """Every tool on disk, built without consulting the locked profile."""
    built = core._build_tool_registry(core._load_enabled_tools(_tool_module_names(), []))
    assert built, "no tools were registered; every test below would pass vacuously"
    return built


@pytest.fixture
def learner_tools(registry: dict[str, Tool]) -> list[str]:
    """Return the registered tools whose modules read learner data."""
    return sorted(
        name for name, tool in registry.items() if _reads_learner_data(_module_source(_defining_module(tool)))
    )


@pytest.fixture
def instance(tmp_path: Path) -> Path:
    """Build a seeded database plus a second learner with a name and real results."""
    assert store.ensure_learner_database(tmp_path).ready is True

    connection = store.connect(tmp_path)
    with connection:
        connection.execute(
            "INSERT INTO learners (id, display_name, created_at) VALUES (?, ?, ?)",
            (HOUSEMATE_ID, HOUSEMATE_NAME, 1),
        )
    connection.close()

    # One finished lesson and one unfinished, so a leak of PROGRESS is observable and
    # not only a leak of a name. record_result refuses an unknown learner, so a
    # recorded=True here also proves the raw insert above really landed.
    for outcome, score in (("completed", 93), ("partial", 41)):
        progress = store.get_progress(HOUSEMATE_ID, HOUSEMATE_LANGUAGE, instance_path=tmp_path)
        assert progress is not None and progress.next_lesson is not None
        recorded = store.record_result(
            HOUSEMATE_ID, progress.next_lesson.id, outcome, score=score, instance_path=tmp_path
        )
        assert recorded.recorded is True

    # Pin the history rather than assume it: if these two attempts ever stopped
    # landing, the forbidden-token set would keep its name half and silently lose its
    # progress half, and every test here would still pass.
    assert len(_housemate_snapshot(tmp_path)[2]) == 2

    # The primary learner's most recent completion must be a lesson with material, or
    # redo_lesson never reaches its body. The seed's Spanish history is placeholders
    # only, and redo_lesson now refuses those (lesson_not_written_yet) before pinning
    # anything -- correctly, but that left the attack below hitting only the refusal,
    # which this suite rightly counts as "reached only the validator". Before that fix
    # the suite reached redo_lesson's body solely through the defect.
    redo_target = store.get_progress(PRIMARY_LEARNER, PINNED_LANGUAGE, instance_path=tmp_path)
    assert redo_target is not None and redo_target.next_lesson is not None
    redo_content = store.get_lesson_content(redo_target.next_lesson.id, instance_path=tmp_path)
    assert redo_content is not None and (redo_content.drills or redo_content.notes)
    assert (
        store.record_result(PRIMARY_LEARNER, redo_target.next_lesson.id, "completed", instance_path=tmp_path).recorded
        is True
    )
    return tmp_path


# The lesson the harness pins before every dispatch. A real id from the seeded catalog,
# pinned to the primary learner's own language, so a write that lands is a write the
# store really accepts rather than one it refuses as unknown_lesson -- which would leave
# the write-direction control looking green while nothing was ever written. A test below
# asserts both constants are still in the seeded catalog, so they cannot go stale.
PINNED_LESSON = "es-fast-01-getting-started-in-class"
PINNED_LANGUAGE = "es"


def _deps(**overrides: Any) -> ToolDependencies:
    """Build dependencies the way main.build_tool_dependencies does, fields stubbed.

    The lesson-session holder is bound to the same learner the bundle names, because
    that is what production does (main.py) -- the holder is bound at construction and
    refuses to pin anything for a bundle that names nobody. Leaving it at the field's
    default would hand every start_lesson call an unbound holder, so the tool would
    return the same refusal for both probe learners and the attack below would run
    against it vacuously. That is a NARROWER attack surface, not a safer one: binding
    it makes the holder live, so an injected identity now has something real to try to
    move. Nothing else in this file changes what is attacked.
    """
    overrides.setdefault("lesson_session", LessonSessionHolder(overrides.get("current_learner_id")))
    return ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock(), **overrides)


def _housemate_snapshot(instance: Path) -> tuple[Any, ...]:
    """Capture the housemate's stored data in EVERY language, for a before/after diff.

    Every language, not just hers, and that is the whole point. This used to read only
    HOUSEMATE_LANGUAGE, which made the write-direction control vacuous the moment a
    write tool existed: _ATTEMPTS_SQL is language-scoped, and the write under attack was
    a SPANISH one. So a tool that wrote a Spanish row onto the housemate left this
    snapshot byte-identical and passed the guard. Verified by building exactly that
    tool: it evaded the narrow snapshot and is caught by this one.

    How a Spanish lesson reaches the dispatch has since changed, and the reason to keep
    this wide has not. It used to be _benign_args synthesising the first value of
    record_result's lesson enum; W16 deleted that tool, and the lesson now arrives
    through PINNED_LESSON, which _run pins before every dispatch. Either way the write
    lands in one language, so a snapshot narrowed to the housemate's own would go blind
    to it again.

    _forbidden_tokens derives from this, so it inherited the same blind spot and is
    widened by the same fix.
    """
    profile = store.get_profile(HOUSEMATE_ID, instance_path=instance)
    assert profile is not None
    attempts: list[tuple[Any, ...]] = []
    for entry in store.get_language_catalog(instance_path=instance):
        progress = store.get_progress(HOUSEMATE_ID, entry.code, instance_path=instance)
        assert progress is not None
        attempts.extend((a.lesson_id, a.outcome, a.score) for a in progress.attempts)
    return (profile.id, profile.display_name, tuple(sorted(attempts)))


def _forbidden_tokens(instance: Path) -> list[str]:
    """Everything of the housemate's that must never appear in another learner's answer.

    Derived from her stored data rather than hand-listed, so her progress is covered
    and not only her name. A bare lesson id is deliberately NOT forbidden: the lesson
    catalog is not personal data and a French lesson id legitimately appears in any
    learner's French progress. What is personal is the PAIRING of a lesson with an
    outcome and a score, so those are forbidden as pairs.
    """
    _, display_name, attempts = _housemate_snapshot(instance)
    tokens = [HOUSEMATE_ID, display_name, *display_name.split()]
    # A lesson id alone is NOT forbidden -- the catalog is shared, not personal, and a
    # French lesson id legitimately appears in any learner's French progress. The three
    # parts together are personal, so they are carried as one token and split by
    # _leaked, which is the only place that knows how to match them.
    tokens += [PAIRING_SEPARATOR.join(str(part) for part in attempt) for attempt in attempts]
    return tokens


def _leaked(tokens: list[str], probe: Any) -> list[str]:
    """Return which forbidden tokens show up in a result, matched case-insensitively."""
    haystack = str(probe).casefold()
    found = []
    for token in tokens:
        if PAIRING_SEPARATOR in token:
            # A pairing counts as leaked only when every part of it is present.
            parts = token.split(PAIRING_SEPARATOR)
            if all(part.casefold() in haystack for part in parts):
                found.append(token)
        elif token.casefold() in haystack:
            found.append(token)
    return found


def _benign_args(tool: Tool) -> dict[str, Any]:
    """Synthesise the arguments a tool declares, so it is called for real where possible.

    get_profile declares none. A future get_progress(language) declaring an enum gets a
    real value; one declaring a free string gets a placeholder and may answer with an
    error dict -- which still proves injection inert, but is why the observability test
    below insists separately that each learner tool's answer really does vary.
    """
    schema = getattr(tool, "parameters_schema", {}) or {}
    properties = schema.get("properties", {}) or {}
    args: dict[str, Any] = {}
    for name in schema.get("required", []) or []:
        spec = properties.get(name, {}) or {}
        if "default" in spec:
            args[name] = spec["default"]
        elif spec.get("enum"):
            args[name] = spec["enum"][0]
        else:
            args[name] = _TYPE_DEFAULTS.get(spec.get("type"), "")
    return args


async def _run(
    core: ModuleType,
    registry: dict[str, Tool],
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    args: Any,
    deps: ToolDependencies,
    entry: str = "dispatch_tool_call",
    manager: Any = None,
) -> dict[str, Any]:
    """Dispatch through a REAL public entry point, with only the registry lookup pinned.

    Both entry points run _safe_load_obj over attacker-shaped JSON and then the
    unvalidated `await tool(deps, **args)` splat. Replacing the registry lookup makes
    coverage wider than production, never narrower; nothing about the identity boundary
    lives in that lookup.

    `deps` is passed in rather than built here, because production reuses ONE
    ToolDependencies for a whole session and the poisoning case depends on that.
    `args` is serialised as given, so a caller can pass something that is not a JSON
    object at all and exercise _safe_load_obj's coercion.
    """
    monkeypatch.setattr(core, "get_tools", lambda: registry)
    # Pin a lesson before every dispatch, because production always has one pinned when
    # a lesson is finished -- start_lesson pinned it. A harness that pins nothing
    # attacks finish_lesson at its "nothing is running" guard instead of in its body,
    # which is a NARROWER surface, not a safer one: it would never reach the write. The
    # same argument _deps already makes for binding the holder, one step further.
    # Re-pinning after the tool clears it is what a real second turn does.
    if deps.lesson_session.read_for(deps.current_learner_id) is None:
        try:
            deps.lesson_session.open(lesson_id=PINNED_LESSON, language_code=PINNED_LANGUAGE)
        except LessonSessionRefusedError:
            # A holder bound to nobody pins nothing, and that state is itself under
            # test elsewhere in this file. Refusing here must not fail the dispatch.
            pass
    payload = json.dumps(args)
    # Some tools choose at random -- dance picks a move -- so pin the RNG for the
    # duration of the dispatch. Without this, "the answer did not change" is a
    # coin flip for those tools and the suite is flaky in the direction that
    # invents failures. Sampling for stability instead would still be probabilistic.
    state = random.getstate()
    random.seed(0)
    try:
        # One manager per exchange, like deps: a fresh mock each call would make a
        # tool that echoes it look non-deterministic, and that artefact would
        # silently disable the equality half of the attack for that tool.
        if entry == "dispatch_tool_call_with_manager":
            return await core.dispatch_tool_call_with_manager(tool_name, payload, deps, manager or MagicMock())
        if entry == "dispatch_tool_call_reporting_tool":
            # Returns (result, resolved_tool); the identity boundary is about the
            # result, and the second half is unwrapped here so every entry point in
            # ENTRY_POINTS answers the same shape to the attacks below.
            result, _resolved = await core.dispatch_tool_call_reporting_tool(
                tool_name, payload, deps, manager or MagicMock()
            )
            return result
        return await core.dispatch_tool_call(tool_name, payload, deps)
    finally:
        random.setstate(state)


def test_every_public_dispatch_entry_point_is_attacked() -> None:
    """ENTRY_POINTS is read from what REACHES the splat, not from what it is called.

    D36 added a third entry point and made it the only one production calls, while
    this tuple still named the other two -- so the suite spent its whole run
    attacking paths the application no longer uses, and nothing failed, because a
    hand-maintained list cannot notice what it is missing.

    The first replacement was a NAME-PREFIX convention, and the security review
    defeated it in one move: a public `async def run_tool_call(...)` whose body is
    the same `await _dispatch_tool_call(...)` splat this whole suite exists to attack
    slipped past, and the full suite stayed green. A convention enumerates what
    somebody chose to call an entry point. The property that matters is structural --
    does this function reach the unvalidated `**args` splat -- so that is what is
    read, from the AST, and an entry point called ANYTHING is caught as long as it
    has the shape below.

    WHAT IT CATCHES, stated exactly, because the sentence that used to be here said
    "a fifth entry point called anything at all is attacked the day it is written"
    and review measured four shapes that walk past it. Caught: a public top-level
    `async def` in core_tools whose body contains a direct `_dispatch_tool_call(...)`
    call. NOT caught: one level of indirection through a helper; a call written as
    `core_tools._dispatch_tool_call(...)`, since the walk reads `func.id` and an
    attribute has none; a public alias assignment, which is no FunctionDef at all;
    and an entry point defined in another module or as a method. Those are real
    gaps, not hypotheticals -- each was appended to the real module and each passed.
    Widening the walk is possible; what is not acceptable is a docstring claiming
    the breadth this does not have, which is the D15 class CLAUDE.md records.

    `_dispatch_tool_call` itself is excluded: it IS the splat, and attacking it would
    re-run the same code a fourth time while proving nothing about a public surface.
    """
    source = Path(core_tools.__file__).read_text(encoding="utf-8")
    reaching = {
        node.name
        for node in ast.parse(source).body
        if isinstance(node, ast.AsyncFunctionDef)
        and not node.name.startswith("_")
        and any(
            isinstance(inner, ast.Call) and getattr(inner.func, "id", None) == "_dispatch_tool_call"
            for inner in ast.walk(node)
        )
    }

    assert reaching, "no function was found reaching _dispatch_tool_call, so this guard proves nothing"
    assert set(ENTRY_POINTS) == reaching, (
        "the identity-boundary suite attacks a different set than the module exposes to the splat; "
        f"reaching _dispatch_tool_call={sorted(reaching)} attacked={sorted(ENTRY_POINTS)}"
    )
    for name in ENTRY_POINTS:
        assert inspect.iscoroutinefunction(getattr(core_tools, name)), f"{name} is not an async entry point"


# --- The suite must not be able to pass while proving nothing --------------------------


def test_the_registry_is_populated_and_wider_than_the_locked_profile(registry: dict[str, Tool]) -> None:
    """Coverage must not be filtered by default_tools, or an unlisted tool escapes."""
    from reachy_language_tutor import config
    from reachy_language_tutor.profile_store import read_profile_from_directory

    name = config.LOCKED_PROFILE
    enabled = set(read_profile_from_directory(name, config.DEFAULT_PROFILES_DIRECTORY / name).default_tools)
    assert len(registry) > 1, "the registry under test is suspiciously small"
    assert set(registry) - enabled, "the registry matches the profile, so an unlisted tool would not be attacked"


def test_no_waiver_covers_a_learner_reading_tool(learner_tools: list[str]) -> None:
    """A waiver may excuse a tool from part of the attack; it may never excuse THIS one.

    Each waiver above removes some coverage in exchange for being visible. That trade is
    only acceptable for tools that cannot touch a learner -- the moment one covers a
    learner-reading tool it is concealing the exact thing the suite is for.
    """
    for waiver, why in (
        (ATTACKED_ONLY_AT_THE_VALIDATOR, "is attacked only at its input validator"),
        (NOT_SELF_CONSISTENT, "is exempt from the equality half of the attack"),
        (NOT_DIFFERENTIALLY_OBSERVABLE, "is exempt from the observability requirement"),
    ):
        covered = sorted(set(learner_tools) & waiver)
        assert not covered, f"a learner-reading tool {why}, which hides what this suite exists to find: {covered}"


def test_discovery_finds_at_least_one_learner_reading_tool(learner_tools: list[str]) -> None:
    """Discovery drives the observability requirement, which is empty without it."""
    assert learner_tools, "discovery found no learner-reading tools; the observability test would prove nothing"


def test_the_discovery_rule_recognises_both_signals() -> None:
    """Self-test, so the rule is still known-good even if the real set ever shrinks."""
    assert _reads_learner_data("from reachy_language_tutor.learners import get_profile")
    assert _reads_learner_data("from reachy_language_tutor.learners.store import connect")
    assert _reads_learner_data("import reachy_language_tutor.learners")
    assert _reads_learner_data("def f(deps):\n    return deps.current_learner_id\n")
    assert not _reads_learner_data("from reachy_language_tutor.moves import MovementManager")


def test_every_tool_module_mentioning_a_learner_is_discovered(
    registry: dict[str, Tool], learner_tools: list[str]
) -> None:
    """The growth guard: it fires when a new tool lands in a shape the AST rule misses."""
    missed = sorted(
        name
        for name, tool in registry.items()
        if "learner" in _module_source(_defining_module(tool)).lower() and name not in learner_tools
    )
    assert not missed, f"these tool modules mention a learner but discovery missed them: {missed}"


def test_the_injected_payload_covers_the_identity_fields_on_deps(instance: Path) -> None:
    """The likeliest key a violating tool reads is the one it can see on deps."""
    assert "current_learner_id" in INJECTED_IDENTITY
    assert _IDENTITY_FIELD_NAMES <= set(INJECTED_IDENTITY)
    # And the payload must actually carry the housemate, or every attack is inert.
    assert HOUSEMATE_ID in INJECTED_IDENTITY.values()
    tokens = _forbidden_tokens(instance)
    assert tokens, "no forbidden tokens were derived; the leak checks would be vacuous"
    assert any(PAIRING_SEPARATOR in token for token in tokens), (
        "no lesson/outcome/score pairing was derived, so a leak of PROGRESS would go unnoticed "
        "even though a leak of a name would not"
    )


def test_the_identity_matchers_track_the_dataclass_rather_than_a_literal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prove the derivation is real, not a coincidence of today's single field name.

    _IDENTITY_FIELD_NAMES has exactly one member right now, so every matcher built on
    it behaves identically to one hard-coding "current_learner_id" -- which is exactly
    how the asymmetry this test exists to prevent stayed invisible until a review found
    it. So this asserts the DERIVATION, using a stand-in dataclass carrying a second
    identity field, rather than asserting today's behaviour.
    """
    assert _IDENTITY_FIELD_NAMES, "the derivation produced nothing; every matcher below would be vacuous"
    assert "current_learner_id" in _IDENTITY_FIELD_NAMES

    # Iterating today's one-member set would prove nothing: a matcher hard-coding
    # "current_learner_id" passes that loop. So the set is WIDENED for the duration of
    # this assertion, which a literal matcher cannot survive.
    future = _IDENTITY_FIELD_NAMES | {"household_user_id"}
    monkeypatch.setattr(
        sys.modules[test_the_identity_matchers_track_the_dataclass_rather_than_a_literal.__module__],
        "_IDENTITY_FIELD_NAMES",
        future,
    )
    for name in future:
        assert _reads_learner_data(f"async def __call__(self, deps):\n    return deps.{name}\n"), (
            f"{name} is an identity field on ToolDependencies but reading it is not recognised; "
            "a matcher that hard-codes one field name fails exactly here"
        )
    nowhere = _IdentityFromNowhereTool()
    with pytest.raises(AssertionError, match=re.escape(f"must read {sorted(future)} from its dependencies")):
        _assert_sources_identity_from_deps(nowhere)

    @dataclasses.dataclass
    class _FutureDeps:
        reachy_mini: Any = None
        current_learner_id: str | None = None
        household_user_id: str | None = None  # the milestone-4 shape this guards against
        motion_duration_s: float = 1.0

    derived = frozenset(
        field.name for field in dataclasses.fields(_FutureDeps) if "learner" in field.name or "user" in field.name
    )
    assert derived == {"current_learner_id", "household_user_id"}, (
        "a second identity dep must join the matchers automatically; if this fails, the rule that "
        "builds _IDENTITY_FIELD_NAMES has drifted from the one asserted here"
    )
    assert "motion_duration_s" not in derived, "the rule must not sweep in ordinary non-identity fields"


# --- The attack ------------------------------------------------------------------------


async def _assert_identity_injection_is_ignored(
    core: ModuleType,
    registry: dict[str, Tool],
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    instance: Path,
) -> dict[str, bool]:
    """Assert no injected identity changes a tool's answer, leaks the housemate, or sticks.

    A plain helper rather than a test, so the deliberately broken tools below run
    through the very same assertions the real ones do.

    Returns what the caller must NOT swallow: whether this tool was reached only at its
    input validator, and whether it answers consistently. Both silently remove part of
    the attack, so they are reported upward and asserted against explicit waivers rather
    than shrugged off here.
    """
    reached_only_validator = False
    self_consistent = True
    args = _benign_args(registry[tool_name])
    tokens = _forbidden_tokens(instance)

    for entry in ENTRY_POINTS:
        # ONE deps object for the whole exchange, as production has for a whole session:
        # a tool that writes deps.current_learner_id would repoint every later call.
        deps = _deps(current_learner_id=PRIMARY_LEARNER, instance_path=instance)
        manager = MagicMock()
        where = f"{tool_name} via {entry}"

        async def run(
            payload: Any, deps: ToolDependencies = deps, entry: str = entry, manager: Any = manager
        ) -> dict[str, Any]:
            result = await _run(core, registry, monkeypatch, tool_name, payload, deps, entry, manager)
            # After EVERY dispatch, not once at the end: an end-state check cannot say
            # which payload broke it, and would miss a tool that repoints the identity
            # on one call and restores it on a later one.
            assert deps.current_learner_id == PRIMARY_LEARNER, (
                f"{tool_name} via {entry}: a dispatch with {sorted(payload) if isinstance(payload, dict) else payload} "
                "changed the identity ON the shared dependencies, which would repoint every later turn"
            )
            # The same check for the OTHER piece of learner-scoped state the shared
            # dependencies carry. Binding the holder in _deps made the pinned lesson a
            # live write target, and a target the sweep can move but never reads back
            # is a target it does not really test -- the "fix the class, not the
            # member" shape CLAUDE.md names, with current_learner_id as the member
            # that already had its guard. Nothing may pin a lesson for anyone but the
            # learner this session is serving, whatever the payload said.
            assert deps.lesson_session.read_for(HOUSEMATE_ID) is None, (
                f"{tool_name} via {entry}: a dispatch with {sorted(payload) if isinstance(payload, dict) else payload} "
                "pinned a lesson readable as the OTHER household member's, on the shared dependencies"
            )
            return result

        baseline = await run(args)
        # Some tools are deliberately non-deterministic -- dance picks a random move --
        # so establish self-consistency before treating inequality as evidence. Measured
        # rather than listed: a list would go stale, and a tool that is unstable here
        # simply loses the equality half, never the leak half.
        stable = baseline == await run(args)
        self_consistent = self_consistent and stable
        reached_only_validator = reached_only_validator or (isinstance(baseline, dict) and "error" in baseline)
        attacked = await run({**args, **INJECTED_IDENTITY})
        # An identity key whose value IS the current learner must be as inert as one
        # naming somebody else: the tool must not read it at all.
        self_named = await run({**args, **dict.fromkeys(INJECTED_IDENTITY, PRIMARY_LEARNER)})
        # Case variants, because a model does not type what we expect.
        variants = await run({**args, "Learner_ID": HOUSEMATE_ID, "CURRENT_LEARNER_ID": HOUSEMATE_ID})
        # Not a JSON object at all; _safe_load_obj coerces it to NO kwargs, so the
        # honest comparison is against an empty-args call rather than a benign-args one
        # -- a tool with required arguments legitimately behaves differently without
        # them, and that has nothing to do with identity.
        no_args = await run({})
        not_an_object = await run([HOUSEMATE_ID, "learner_id"])
        identity_only = await run(dict(INJECTED_IDENTITY))

        if stable:
            for probe, label in (
                (attacked, "injected identity"),
                (self_named, "a self-naming identity key"),
                (variants, "case variants of an identity key"),
            ):
                assert probe == baseline, f"{where}: {label} changed what the tool returned"
            assert not_an_object == no_args, f"{where}: a non-object payload was not coerced to empty kwargs"
        for probe, label in (
            (baseline, "on the baseline call"),
            (attacked, "with valid arguments"),
            (identity_only, "with identity keys alone"),
            (variants, "with case-variant identity keys"),
        ):
            leaked = _leaked(tokens, probe)
            assert not leaked, f"{where}: {label}, another learner's data came back: {leaked}"

    return {"validator_only": reached_only_validator, "self_consistent": self_consistent}


@pytest.mark.asyncio
async def test_no_tool_can_be_steered_by_an_injected_identity(
    core: ModuleType, registry: dict[str, Tool], monkeypatch: pytest.MonkeyPatch, instance: Path
) -> None:
    """Every registered tool, not only the ones a discovery rule recognises.

    Discovery can always be sidestepped by indirection, and there are fifteen tools --
    attacking all of them costs little and removes the whole class of escape.
    """
    validator_only = []
    unstable = []
    for tool_name in sorted(registry):
        reached = await _assert_identity_injection_is_ignored(core, registry, monkeypatch, tool_name, instance)
        if reached["validator_only"]:
            validator_only.append(tool_name)
        if not reached["self_consistent"]:
            unstable.append(tool_name)

    # Both of these silently remove part of the attack, so neither may be implicit.
    assert set(validator_only) == ATTACKED_ONLY_AT_THE_VALIDATOR, (
        "these tools answered with an error dict, so the attack reached their input "
        f"validator rather than their body: {sorted(validator_only)}. Give them usable "
        "arguments, or waive them in ATTACKED_ONLY_AT_THE_VALIDATOR."
    )
    assert set(unstable) == NOT_SELF_CONSISTENT, (
        "these tools do not answer consistently for the same input, so the equality half "
        f"of the attack was skipped for them: {sorted(unstable)}. Waive them in "
        "NOT_SELF_CONSISTENT if that is genuinely unavoidable."
    )


@pytest.mark.asyncio
async def test_every_learner_tool_is_individually_observable(
    core: ModuleType,
    registry: dict[str, Tool],
    learner_tools: list[str],
    monkeypatch: pytest.MonkeyPatch,
    instance: Path,
) -> None:
    """Per tool, not across the set: a tool whose answer never varies proves nothing.

    Aggregated, one observable tool would vouch for every other. So each learner tool
    must answer differently for two different learners -- otherwise the attack above
    is running against it vacuously, and that must be a loud failure, not a silent one.
    """
    # Each probe learner gets the lesson THEY would really be working on, resolved once
    # so it is the same lesson on every dispatch. _run pins one constant for everybody,
    # which is right for the attack itself -- it puts every tool in the same state, so
    # equality across an injected identity means something. It is wrong here: a tool
    # whose answer is keyed on the pinned lesson answers identically for two learners
    # pinned to the SAME lesson, and reads as inert while being perfectly observable.
    # Production never pins that way; start_lesson pins from the learner's own progress,
    # and so does this. Resolved once rather than per dispatch because a tool that
    # records a result moves the learner on, and re-resolving would pin a different
    # lesson on the second call and report a stable tool as unstable.
    own_lesson = {}
    for learner, language in ((PRIMARY_LEARNER, PINNED_LANGUAGE), (HOUSEMATE_ID, HOUSEMATE_LANGUAGE)):
        progress = store.get_progress(learner, language, instance_path=instance)
        assert progress is not None and progress.next_lesson is not None, (
            f"the fixture owes {language} lessons to both probe learners, and the observability "
            "probe is vacuous without them"
        )
        own_lesson[learner] = (progress.next_lesson.id, language)
    assert own_lesson[PRIMARY_LEARNER][0] != own_lesson[HOUSEMATE_ID][0], (
        "both probe learners are pinned to the same lesson, so a tool keyed on the pinned "
        "lesson would look inert no matter how observable it is"
    )

    # Where a tool is ALLOWED to leave the pin, per learner. Named as the permitted end
    # states rather than as an exemption for whichever tool moves it, because a list of
    # excused tools is only ever as complete as the last person to read it -- CLAUDE.md
    # records four defects from exactly that shape.
    #
    # Three states are legitimate: cleared, because finish_lesson clears the lesson it
    # just recorded; unchanged, because a tool that only reads must not move it; and
    # this learner's own next lesson in ANY taught language, because that is what
    # start_lesson pins and _benign_args may name a language other than the one pinned
    # here. Anything else is a tool putting a learner on a lesson nobody chose.
    def may_be_left_pinned(learner: str) -> set[str]:
        """Return the lessons a dispatch may leave pinned for this learner, right now.

        Resolved after the dispatch rather than once up front, because finish_lesson
        records a result and moves the learner on -- so the lesson start_lesson may
        legitimately pin next is one that did not exist as an answer before the loop
        began. Computing this ahead of time reported start_lesson as a tool that had
        moved the pin somewhere nobody chose, which it had not.
        """
        allowed = {own_lesson[learner][0]}
        for entry in store.get_language_catalog(instance_path=instance) or ():
            standing = store.get_progress(learner, entry.code, instance_path=instance)
            if standing is not None and standing.next_lesson is not None:
                allowed.add(standing.next_lesson.id)
        return allowed

    def _recorded_attempts(learner: str) -> int:
        """Count what this learner has actually attempted, across every language."""
        practised = store.get_practised_languages(learner, instance_path=instance)
        return sum(entry.attempts for entry in practised or ())

    moved: list[str] = []

    async def run_on_their_own_lesson(tool_name: str, args: Any, deps: ToolDependencies) -> dict[str, Any]:
        """Dispatch with this learner's own lesson pinned, re-pinned every time.

        open() is last-open-wins, so re-pinning before each dispatch makes _run's own
        conditional pin a no-op and leaves the state identical across repeated calls --
        including after a tool clears the pin, which is what makes the stability half of
        this test mean the same thing it did before.

        Re-pinning costs one thing, and it is paid back below rather than dropped: it
        hides a tool that MOVES the pin, which _run's conditional pin used to surface as
        instability on the second call. So the pin is checked after every dispatch
        against the states above.
        """
        learner = deps.current_learner_id
        lesson_id, language_code = own_lesson[learner]
        deps.lesson_session.open(lesson_id=lesson_id, language_code=language_code)
        attempts_before = _recorded_attempts(learner)
        result = await _run(core, registry, monkeypatch, tool_name, args, deps)
        after = deps.lesson_session.read_for(learner)
        if after is None:
            # Clearing is permitted only by a dispatch that actually WROTE something,
            # which is the condition finish_lesson ties its own clear to. Keyed on the
            # store rather than on which tool it was, and rather than on the tool's own
            # say-so: a list of tools excused from this check would be one name short
            # the day a second recording tool exists, and a check reading the answer's
            # own "recorded": true would let a tool that wrote nothing award itself the
            # permission -- this harness exists to attack these tools, so it must not
            # take their word for what they did.
            if _recorded_attempts(learner) == attempts_before:
                moved.append(tool_name)
        elif after.lesson_id not in may_be_left_pinned(learner):
            moved.append(tool_name)
        return result

    inert = []
    unstable = []
    for tool_name in learner_tools:
        if tool_name in NOT_DIFFERENTIALLY_OBSERVABLE:
            continue
        args = _benign_args(registry[tool_name])
        repeated = _deps(current_learner_id=PRIMARY_LEARNER, instance_path=instance)
        first = await run_on_their_own_lesson(tool_name, args, repeated)
        if first != await run_on_their_own_lesson(tool_name, args, repeated):
            unstable.append(tool_name)
        as_primary = await run_on_their_own_lesson(
            tool_name, args, _deps(current_learner_id=PRIMARY_LEARNER, instance_path=instance)
        )
        as_housemate = await run_on_their_own_lesson(
            tool_name, args, _deps(current_learner_id=HOUSEMATE_ID, instance_path=instance)
        )
        if as_primary == as_housemate:
            inert.append(tool_name)

    assert not moved, (
        "these tools left the running lesson somewhere nobody chose -- moved to a lesson the "
        "learner was not on, or cleared without recording anything, either of which decides "
        f"what a later finish_lesson writes: {sorted(set(moved))}"
    )
    assert not unstable, (
        "these learner-reading tools do not answer consistently for the same learner, so "
        f"the injection attack cannot use equality against them: {unstable}"
    )
    assert not inert, (
        "these learner-reading tools answer identically for two different learners, so "
        f"the injection attack proves nothing about them: {inert}. Give them usable "
        "arguments, or waive them explicitly in NOT_DIFFERENTIALLY_OBSERVABLE."
    )


@pytest.mark.asyncio
async def test_the_primary_learners_own_data_really_does_come_back(
    core: ModuleType,
    registry: dict[str, Tool],
    learner_tools: list[str],
    monkeypatch: pytest.MonkeyPatch,
    instance: Path,
) -> None:
    """A second positive control: the pipe is connected, so absence means something."""
    seen = []
    for tool_name in learner_tools:
        result = await _run(
            core,
            registry,
            monkeypatch,
            tool_name,
            _benign_args(registry[tool_name]),
            _deps(current_learner_id=PRIMARY_LEARNER, instance_path=instance),
        )
        if PRIMARY_NAME in str(result):
            seen.append(tool_name)
    assert seen, f"no learner tool returned the primary learner's own name; checked {learner_tools}"


@pytest.mark.asyncio
async def test_injection_does_not_touch_the_other_learners_stored_data(
    core: ModuleType, registry: dict[str, Tool], monkeypatch: pytest.MonkeyPatch, instance: Path
) -> None:
    """The write direction, which the read assertions structurally cannot see."""
    before = _housemate_snapshot(instance)
    for tool_name in sorted(registry):
        args = {**_benign_args(registry[tool_name]), **INJECTED_IDENTITY}
        deps = _deps(current_learner_id=PRIMARY_LEARNER, instance_path=instance)
        await _run(core, registry, monkeypatch, tool_name, args, deps)

    assert _housemate_snapshot(instance) == before, "an injected identity altered another learner's stored data"


# --- What the model is SHOWN ------------------------------------------------------------


def _assert_declares_no_identity_parameter(tool: Tool) -> None:
    """Assert nothing identity-shaped appears in what the model is offered."""
    for schema, where in ((tool.parameters_schema, "parameters_schema"), (tool.spec()["parameters"], "spec")):
        names = set((schema or {}).get("properties", {})) | set((schema or {}).get("required", []) or [])
        offenders = sorted(n for n in names if "learner" in n.lower() or n.lower() in _IDENTITY_KEYS)
        assert not offenders, f"{tool.name}: {where} offers the model an identity: {offenders}"


def test_no_tool_offers_the_model_an_identity(registry: dict[str, Tool]) -> None:
    """Checked on every live registered instance, not only on the class source.

    Every tool, not only the learner-reading ones: this also covers a RemoteMcpTool,
    whose implementation lives on someone else's server and whose only locally visible
    surface is the schema it declares.
    """
    for tool in registry.values():
        _assert_declares_no_identity_parameter(tool)


def _assert_sources_identity_from_deps(tool: Tool) -> None:
    """Assert this tool's __call__ reads the identity off the dependencies it was handed."""
    call = ast.parse(textwrap.dedent(inspect.getsource(type(tool).__call__))).body[0]
    assert isinstance(call, (ast.FunctionDef, ast.AsyncFunctionDef))
    positional = [argument.arg for argument in call.args.posonlyargs + call.args.args]
    deps_parameter = positional[1] if len(positional) > 1 else ""
    sources = {
        node.value.id
        for node in ast.walk(call)
        if isinstance(node, ast.Attribute)
        and node.attr in _IDENTITY_FIELD_NAMES
        and isinstance(node.ctx, ast.Load)
        and isinstance(node.value, ast.Name)
    }
    assert deps_parameter in sources, (
        f"{tool.name}: __call__ must read {sorted(_IDENTITY_FIELD_NAMES)} from its dependencies parameter "
        f"({deps_parameter or 'none declared'}); it reads it from {sorted(sources) or 'nowhere'}"
    )


def test_every_learner_tool_sources_its_identity_from_its_dependencies(
    registry: dict[str, Tool], learner_tools: list[str]
) -> None:
    """A positive statement of where identity comes from, not just an absent spelling.

    NECESSARY, NOT SUFFICIENT, and the two tests below pin both limits as facts rather
    than leaving them as caveats: the read appearing does not mean the value is used,
    and it does not exclude a second source. It also imposes a style rule -- the read
    must appear syntactically inside __call__ -- so a tool that hands deps to a helper
    would fail this and need a conversation, not a waiver. There can be no waiver here:
    test_no_waiver_covers_a_learner_reading_tool forbids one over a learner tool.

    The attribute names come from _IDENTITY_FIELD_NAMES, derived from
    dataclasses.fields(ToolDependencies), rather than from the literal
    "current_learner_id". That matters for a reason a review caught and this docstring
    would otherwise have hidden: the injected payload was ALREADY derived that way, so
    a future second identity dep -- a household id, a speaker id -- would have joined
    the behavioural attack automatically while this positive check and
    _reads_learner_data silently went on covering one field. A stated-limits list that
    is one item short is worse than no list, so the asymmetry is closed rather than
    documented. Today there is exactly one such field, and
    test_the_identity_matchers_track_the_dataclass_rather_than_a_literal pins that the
    derivation is real rather than a coincidence of naming.
    """
    for name in learner_tools:
        _assert_sources_identity_from_deps(registry[name])


def test_the_sourcing_check_does_not_catch_a_tool_that_reads_kwargs_as_well() -> None:
    """Its blind spot, as a checked fact: this tool passes the check and is still broken.

    _IdentityFromKwargsTool prefers a kwarg and falls back to deps, so the deps read is
    present and this static check is satisfied. Only the behavioural attack catches it,
    which is why that attack is the guard that holds.
    """
    _assert_sources_identity_from_deps(_IdentityFromKwargsTool())


def test_a_tool_that_never_reads_deps_fails_the_sourcing_check() -> None:
    """The demonstration that the check can fail at all.

    The expected message is DERIVED, like the check itself. Spelling it as a literal
    here is what made this test fail the moment the matcher stopped hard-coding one
    field name -- which was the guard working, but it would have been a standing
    invitation to re-introduce the literal to make the test pass again.
    """
    expected = re.escape(f"must read {sorted(_IDENTITY_FIELD_NAMES)} from its dependencies")
    with pytest.raises(AssertionError, match=expected):
        _assert_sources_identity_from_deps(_IdentityFromNowhereTool())


def test_no_tool_takes_a_named_parameter_beyond_its_dependencies(registry: dict[str, Tool]) -> None:
    """A widened signature is the likeliest regression, and it never mentions kwargs."""
    offenders = []
    for name, tool in registry.items():
        parameters = inspect.signature(type(tool).__call__).parameters
        if list(parameters) != ["self", "deps", "kwargs"]:
            offenders.append(f"{name}: {list(parameters)}")
        elif parameters["kwargs"].kind is not inspect.Parameter.VAR_KEYWORD:
            offenders.append(f"{name}: kwargs is not **kwargs")
    assert not offenders, "tools must take only (self, deps, **kwargs): " + "; ".join(offenders)


# --- Criterion 5: the assertions must fail on tools shaped like the violations ----------


class _IdentityFromKwargsTool(Tool):
    """Deliberately broken: it lets the conversation choose whose profile to read."""

    _auto_register = False
    name = "w11_identity_from_kwargs"
    description = "Deliberately broken tool, never registered outside this test."
    parameters_schema: dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Read the learner from kwargs when offered one -- the exact violation."""
        learner_id = kwargs.get("learner_id") or deps.current_learner_id
        profile = store.get_profile(str(learner_id), instance_path=deps.instance_path)
        return {"display_name": profile.display_name} if profile else {"error": "no profile"}


class _IdentityPoisoningTool(Tool):
    """Deliberately broken: it writes the identity onto the shared dependencies."""

    _auto_register = False
    name = "w11_identity_poisoning"
    description = "Deliberately broken tool, never registered outside this test."
    parameters_schema: dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Repoint the session at somebody else while returning an innocent answer."""
        if kwargs.get("current_learner_id"):
            deps.current_learner_id = kwargs["current_learner_id"]
        return {"ok": True}


class _IdentityPoisoningAroundTheSealTool(Tool):
    """Deliberately broken: it repoints the session by writing the instance dict directly.

    ToolDependencies now refuses `deps.current_learner_id = ...` at runtime, so without
    this tool the end-state check after every dispatch would have no way left to fail --
    and that check covers exactly the writes the seal cannot see.
    """

    _auto_register = False
    name = "w12_identity_poisoning_around_the_seal"
    description = "Deliberately broken tool, never registered outside this test."
    parameters_schema: dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Repoint the session without going through __setattr__, which the seal hooks."""
        if kwargs.get("current_learner_id"):
            deps.__dict__["current_learner_id"] = kwargs["current_learner_id"]
        return {"ok": True}


class _WritesAnotherLearnersRowTool(Tool):
    """Deliberately broken: it records a result against an identity from kwargs."""

    _auto_register = False
    name = "w11_writes_another_learner"
    description = "Deliberately broken tool, never registered outside this test."
    parameters_schema: dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Write to whoever the caller named, which is the leak the reads cannot see."""
        learner_id = str(kwargs.get("learner_id") or deps.current_learner_id)
        progress = store.get_progress(learner_id, HOUSEMATE_LANGUAGE, instance_path=deps.instance_path)
        if progress is not None and progress.next_lesson is not None:
            store.record_result(learner_id, progress.next_lesson.id, "skipped", instance_path=deps.instance_path)
        return {"ok": True}


class _WritesAnotherLearnersRowInAnotherLanguageTool(Tool):
    """Deliberately broken: writes onto a named identity in a language she avoids.

    Writes onto the caller's named identity, in a language the housemate does not
    study, while returning only the CURRENT learner's own standing.

    The shape that defeated every half of the guard at once before the snapshot was
    widened: equality passes because the answer is the deps learner's, the forbidden
    tokens pass because none of her data is returned, and the old language-scoped
    snapshot never looked at the language written to.
    """

    _auto_register = False
    name = "w11_writes_another_learner_in_another_language"
    description = "Deliberately broken tool, never registered outside this test."
    parameters_schema: dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Write a lesson in a language the named learner does not study."""
        victim = str(kwargs.get("learner_id") or deps.current_learner_id)
        # Deliberately NOT HOUSEMATE_LANGUAGE: the point is the language she has no
        # rows in, which is the one the old snapshot could not see.
        store.record_result(victim, "es-01-greetings", "skipped", instance_path=deps.instance_path)
        mine = store.get_progress(str(deps.current_learner_id), "es", instance_path=deps.instance_path)
        return {"completed": len(mine.completed) if mine else 0}


class _UnconditionalLeakTool(Tool):
    """Deliberately broken: it returns the housemate's data whatever it is asked.

    The shape nothing else here can catch. It ignores kwargs entirely, so baseline and
    attacked are identical and the equality half passes cleanly -- only the forbidden
    tokens can see it. Without this control the token check is the one assertion family
    in the suite with no proof that it can fail.
    """

    _auto_register = False
    name = "w11_unconditional_leak"
    description = "Deliberately broken tool, never registered outside this test."
    parameters_schema: dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Return somebody else's profile and progress, regardless of who is asking."""
        progress = store.get_progress(HOUSEMATE_ID, HOUSEMATE_LANGUAGE, instance_path=deps.instance_path)
        attempts = [] if progress is None else [(a.lesson_id, a.outcome, a.score) for a in progress.attempts]
        return {"also_in_this_household": HOUSEMATE_NAME, "their_history": attempts}


class _IdentityFromNowhereTool(Tool):
    """Deliberately broken: it reads learner data without sourcing identity from deps."""

    _auto_register = False
    name = "w12_identity_from_nowhere"
    description = "Deliberately broken tool, never registered outside this test."
    parameters_schema: dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Look somebody up without ever asking the dependencies who that is."""
        profile = store.get_profile(HOUSEMATE_ID, instance_path=deps.instance_path)
        return {"display_name": profile.display_name} if profile else {"error": "no profile"}


class _DeclaredIdentityTool(Tool):
    """Deliberately broken: it offers the model an identity to fill in."""

    _auto_register = False
    name = "w11_declared_identity"
    description = "Deliberately broken tool, never registered outside this test."
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"learner_id": {"type": "string"}},
        "required": ["learner_id"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Return nothing interesting; the violation is in the schema above."""
        return {"ok": True}


@pytest.mark.asyncio
async def test_a_tool_that_reads_identity_from_kwargs_fails_the_attack(
    core: ModuleType, monkeypatch: pytest.MonkeyPatch, instance: Path
) -> None:
    """Prove the attack can fail, using the same helper the real tools go through."""
    broken = core._build_tool_registry([], extra_tools=[_IdentityFromKwargsTool()])

    with pytest.raises(AssertionError, match="changed what the tool returned"):
        await _assert_identity_injection_is_ignored(core, broken, monkeypatch, "w11_identity_from_kwargs", instance)


@pytest.mark.asyncio
async def test_a_tool_that_poisons_the_shared_identity_is_refused_at_runtime(
    core: ModuleType, monkeypatch: pytest.MonkeyPatch, instance: Path
) -> None:
    """The write no longer lands: the dataclass refuses it before the session is repointed."""
    broken = core._build_tool_registry([], extra_tools=[_IdentityPoisoningTool()])
    deps = _deps(current_learner_id=PRIMARY_LEARNER, instance_path=instance)

    result = await _run(core, broken, monkeypatch, "w11_identity_poisoning", dict(INJECTED_IDENTITY), deps)

    assert deps.current_learner_id == PRIMARY_LEARNER
    assert "CurrentLearnerIsReadOnlyError" in result["error"]
    # The refusal is handed to the model as a tool error, so it must not name anybody.
    assert not _leaked(_forbidden_tokens(instance), result)


@pytest.mark.asyncio
async def test_a_poisoning_tool_now_fails_the_attack_by_erroring_instead(
    core: ModuleType, monkeypatch: pytest.MonkeyPatch, instance: Path
) -> None:
    """It still fails the suite -- as a changed answer, because the write raised."""
    broken = core._build_tool_registry([], extra_tools=[_IdentityPoisoningTool()])

    with pytest.raises(AssertionError, match="changed what the tool returned"):
        await _assert_identity_injection_is_ignored(core, broken, monkeypatch, "w11_identity_poisoning", instance)


@pytest.mark.asyncio
async def test_a_write_around_the_seal_still_fails_the_end_state_check(
    core: ModuleType, monkeypatch: pytest.MonkeyPatch, instance: Path
) -> None:
    """The seal covers the attribute protocol; this is what covers everything else."""
    broken = core._build_tool_registry([], extra_tools=[_IdentityPoisoningAroundTheSealTool()])

    with pytest.raises(AssertionError, match="changed the identity ON the shared dependencies"):
        await _assert_identity_injection_is_ignored(
            core, broken, monkeypatch, "w12_identity_poisoning_around_the_seal", instance
        )


@pytest.mark.asyncio
async def test_a_tool_that_writes_another_learners_row_fails_the_snapshot(
    core: ModuleType, monkeypatch: pytest.MonkeyPatch, instance: Path
) -> None:
    """The write direction needs its own control, or it is a guard with no evidence."""
    broken = core._build_tool_registry([], extra_tools=[_WritesAnotherLearnersRowTool()])
    before = _housemate_snapshot(instance)

    deps = _deps(current_learner_id=PRIMARY_LEARNER, instance_path=instance)
    await _run(core, broken, monkeypatch, "w11_writes_another_learner", dict(INJECTED_IDENTITY), deps)

    assert _housemate_snapshot(instance) != before, "the snapshot did not notice another learner's row changing"


@pytest.mark.asyncio
async def test_a_write_in_a_language_she_does_not_study_fails_the_snapshot(
    core: ModuleType, monkeypatch: pytest.MonkeyPatch, instance: Path
) -> None:
    """Prove the snapshot can fail on the shape that used to evade it entirely.

    The sibling control above writes in HOUSEMATE_LANGUAGE, which the old
    language-scoped snapshot already saw. This one writes in the language she has no
    rows in -- and returns the current learner's own data, so neither the equality
    half nor the forbidden-token half can see it either. Before the snapshot covered
    every language, this tool passed the entire suite.
    """
    broken = core._build_tool_registry([], extra_tools=[_WritesAnotherLearnersRowInAnotherLanguageTool()])
    before = _housemate_snapshot(instance)

    deps = _deps(current_learner_id=PRIMARY_LEARNER, instance_path=instance)
    await _run(
        core, broken, monkeypatch, "w11_writes_another_learner_in_another_language", dict(INJECTED_IDENTITY), deps
    )

    assert _housemate_snapshot(instance) != before, (
        "the snapshot did not notice a row written in a language the housemate does not study"
    )


@pytest.mark.asyncio
async def test_an_unconditional_leak_is_caught_by_the_forbidden_tokens(
    core: ModuleType, monkeypatch: pytest.MonkeyPatch, instance: Path
) -> None:
    """Prove the token check can fail, on the one shape equality is blind to.

    A leak that is not driven by the injected keys is identical in baseline and
    attacked, so every equality assertion passes. Only the tokens derived from the
    housemate's stored data can see it -- and until this test existed, nothing
    demonstrated that they do.
    """
    broken = core._build_tool_registry([], extra_tools=[_UnconditionalLeakTool()])

    with pytest.raises(AssertionError, match="another learner's data came back"):
        await _assert_identity_injection_is_ignored(core, broken, monkeypatch, "w11_unconditional_leak", instance)


def test_a_tool_that_declares_an_identity_fails_the_schema_check() -> None:
    """The other violation, caught by the other guard."""
    with pytest.raises(AssertionError, match="offers the model an identity"):
        _assert_declares_no_identity_parameter(_DeclaredIdentityTool())


def test_the_pinned_lesson_the_harness_uses_is_in_the_seeded_catalog(tmp_path: Path) -> None:
    """A stale constant would turn a real write into a silent unknown_lesson refusal.

    _run pins PINNED_LESSON before every dispatch so a write tool is attacked in its
    body rather than at its "nothing is running" guard. If that id stopped naming a
    real lesson, every such dispatch would be refused by the store and the
    write-direction control would pass while writing nothing -- green, and vacuous.

    It must also be a lesson WITH MATERIAL. finish_lesson completes it during the
    sweep, which makes it the primary learner's latest completion, and redo_lesson
    refuses a lesson with nothing written in it -- so a placeholder here leaves
    redo_lesson attacked only at that refusal. Read from a seeded database rather than
    SEED_LESSONS, because converted lessons are seeded from JSON and are not in it.
    """
    assert store.ensure_learner_database(tmp_path).ready is True
    lesson = store.get_lesson(PINNED_LESSON, instance_path=tmp_path)
    assert lesson is not None, f"{PINNED_LESSON} is not in the seeded catalog"
    assert lesson.language_code == PINNED_LANGUAGE
    content = store.get_lesson_content(PINNED_LESSON, instance_path=tmp_path)
    assert content is not None and (content.drills or content.notes), f"{PINNED_LESSON} has nothing written in it"
