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

Production passes ONE ToolDependencies to every call for the life of a session, and it
is a mutable dataclass. So the attack reuses a single deps object and asserts the
identity on it is unchanged afterwards: a tool that writes `deps.current_learner_id`
would repoint every later turn, which is the most damaging shape of this violation and
is invisible to a suite that builds fresh deps per call.

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

import ast
import json
import random
import inspect
import pkgutil
import importlib
import dataclasses
from types import ModuleType
from typing import Any
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from reachy_language_tutor import tools
from reachy_language_tutor.learners import store

# For typing and subclassing ONLY. Never use these in an isinstance/issubclass check:
# another test file purges and re-imports the tools package, which would leave these
# names bound to dead classes and make such a check quietly return False. The `core`
# fixture below exists for the same reason.
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

# Both real entry points. dispatch_tool_call_with_manager writes tool_manager into args
# before the same unvalidated splat, which is why the **kwargs catch-all must stay.
ENTRY_POINTS = ("dispatch_tool_call", "dispatch_tool_call_with_manager")

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
        if isinstance(node, ast.Attribute) and node.attr == "current_learner_id":
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
    return tmp_path


def _deps(**overrides: Any) -> ToolDependencies:
    """Build dependencies with the two required fields stubbed, as the other suites do."""
    return ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock(), **overrides)


def _housemate_snapshot(instance: Path) -> tuple[Any, ...]:
    """Capture the housemate's stored data, for a before/after comparison."""
    profile = store.get_profile(HOUSEMATE_ID, instance_path=instance)
    progress = store.get_progress(HOUSEMATE_ID, HOUSEMATE_LANGUAGE, instance_path=instance)
    assert profile is not None and progress is not None
    return (
        profile.id,
        profile.display_name,
        tuple(sorted((a.lesson_id, a.outcome, a.score) for a in progress.attempts)),
    )


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
    payload = json.dumps(args)
    # Some tools choose at random -- dance picks a move -- so pin the RNG for the
    # duration of the dispatch. Without this, "the answer did not change" is a
    # coin flip for those tools and the suite is flaky in the direction that
    # invents failures. Sampling for stability instead would still be probabilistic.
    state = random.getstate()
    random.seed(0)
    try:
        if entry == "dispatch_tool_call_with_manager":
            # One manager per exchange, like deps: a fresh mock each call would make a
            # tool that echoes it look non-deterministic, and that artefact would
            # silently disable the equality half of the attack for that tool.
            return await core.dispatch_tool_call_with_manager(tool_name, payload, deps, manager or MagicMock())
        return await core.dispatch_tool_call(tool_name, payload, deps)
    finally:
        random.setstate(state)


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
    inert = []
    unstable = []
    for tool_name in learner_tools:
        if tool_name in NOT_DIFFERENTIALLY_OBSERVABLE:
            continue
        args = _benign_args(registry[tool_name])
        repeated = _deps(current_learner_id=PRIMARY_LEARNER, instance_path=instance)
        first = await _run(core, registry, monkeypatch, tool_name, args, repeated)
        if first != await _run(core, registry, monkeypatch, tool_name, args, repeated):
            unstable.append(tool_name)
        as_primary = await _run(
            core,
            registry,
            monkeypatch,
            tool_name,
            args,
            _deps(current_learner_id=PRIMARY_LEARNER, instance_path=instance),
        )
        as_housemate = await _run(
            core,
            registry,
            monkeypatch,
            tool_name,
            args,
            _deps(current_learner_id=HOUSEMATE_ID, instance_path=instance),
        )
        if as_primary == as_housemate:
            inert.append(tool_name)

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
async def test_a_tool_that_poisons_the_shared_identity_fails_the_attack(
    core: ModuleType, monkeypatch: pytest.MonkeyPatch, instance: Path
) -> None:
    """The cross-turn takeover: it returns an innocent answer and repoints the session."""
    broken = core._build_tool_registry([], extra_tools=[_IdentityPoisoningTool()])

    with pytest.raises(AssertionError, match="changed the identity ON the shared dependencies"):
        await _assert_identity_injection_is_ignored(core, broken, monkeypatch, "w11_identity_poisoning", instance)


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
