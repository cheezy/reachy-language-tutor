"""Guard the locked profile the app cannot start without.

config.py calls sys.exit(1) at import time if LOCKED_PROFILE has no profile.md,
so a mistake here takes the whole app down before it logs anything useful.
These tests fail loudly instead.

The guard below asks the PRODUCTION LOADER whether a declared name resolves, and
that is the whole point of this file. It used to ask a different question: it
scanned every module under ``tools/`` for any class carrying a ``name``
attribute, and accepted a declared name found anywhere in that set.

The loader's rule is narrower, and it is worth stating exactly because the
obvious summary of it is wrong. ``_load_enabled_tools`` imports
``reachy_language_tutor.tools.<declared name>`` -- the DECLARED name is the
module it looks for. (A name with no such module is not necessarily dead:
``_try_load_tool_classes`` falls back to ``config.TOOLS_DIRECTORY/<name>.py`` on
ModuleNotFoundError, so an external file can still supply it.) From the
module it does find, ``_tool_classes_from_module`` keeps every auto-registerable
``Tool`` subclass regardless of what each is called, and ``_build_tool_registry``
then keys them by whatever ``Tool.name`` each class carries. So the loader does
not match names; it matches a FILENAME and then trusts the attribute. A declared
name therefore resolves only when both halves line up, and a class whose ``name``
differs from its module registers under a key nobody declared.

Measured in this session, not inferred: a probe class named ``probe_misfiled_tool``
living in ``_probe_misfiled_module.py`` is accepted by the attribute scan and
refused by the loader -- ``misfiled_tool`` below manufactures exactly that case.

That gap mattered because an unresolvable name is not an error anyone sees. The
loader logs one warning, drops the tool and starts the app anyway, so the
conversation simply lacks a tool it was promised. This file is the only thing
standing between that and a release.
"""

import sys
import pkgutil
import tempfile
import importlib
from pathlib import Path

import pytest

# The canonical "does this module touch learner data" rule, reused rather than
# restated. A second copy of it here could drift from the boundary suite's, and a
# guard that means something slightly different from the one it mirrors is the
# defect this whole file exists to correct.
from test_tool_identity_boundary import _reads_learner_data

from reachy_language_tutor import tools, config
from reachy_language_tutor.profile_store import read_profile_from_directory


# The tools this app exists to expose. Named here so that dropping one from the
# profile fails with the name it dropped rather than as an arithmetic mismatch.
LEARNER_TOOLS = {"get_profile", "get_progress", "record_result"}

# The COMPLETE vocabulary a learner-reading tool may ask the model to fill in.
#
# Named as what is permitted, never as what is forbidden. CLAUDE.md records four
# defects in this repository caused by a deny-list that was only ever as complete
# as the last person to read it, and a list of identity-shaped words is exactly
# that shape: it admits student_id, household_member, for_whom and anything else
# nobody thought of. Everything named here is shared catalog vocabulary -- which
# language, which lesson, how it went. None of it identifies a person.
#
# Adding an entry is a decision about the security boundary, not housekeeping.
PERMITTED_LEARNER_TOOL_PARAMETERS = {"language", "lesson_id", "outcome"}


def _core_tools():
    """Import core_tools on every use rather than once at module scope.

    Still needed, but for a narrower reason than when it was written. D28 fixed
    test_external_loading.py, which no longer creates a second core_tools. Two files
    still do: test_tool_space_runtime.py and test_profile_load_resilience.py, whose
    reloads exist to re-bind monkeypatched dependencies rather than to refresh the
    registry, so tools_module_graph's in-place reset does not serve them. Until those
    two are converted, a module-scope binding here can still go stale.
    """
    from reachy_language_tutor.tools import core_tools

    return core_tools


def _locked_profile():
    name = config.LOCKED_PROFILE
    return read_profile_from_directory(name, config.DEFAULT_PROFILES_DIRECTORY / name)


def _registry(declared):
    """Build the tool registry the way the running app builds it."""
    core_tools = _core_tools()
    return core_tools._build_tool_registry(core_tools._load_enabled_tools(list(declared), []))


def _unresolvable(declared):
    """Return declared names the production loader cannot turn into a registered tool."""
    registry = _registry(declared)
    return [name for name in declared if name not in registry]


def _scanned_tool_names() -> set:
    """Recompute the set the guard used to accept: any class anywhere with a name."""
    scanned = set()
    for found in pkgutil.iter_modules(tools.__path__):
        loaded = importlib.import_module(f"reachy_language_tutor.tools.{found.name}")
        for obj in vars(loaded).values():
            name = getattr(obj, "name", None)
            if isinstance(obj, type) and isinstance(name, str):
                scanned.add(name)
    return scanned


@pytest.fixture()
def misfiled_tool():
    """Create a real, loadable Tool whose module filename does NOT match its name.

    Without an example like this the difference between the two rules is invisible:
    every tool shipped today satisfies both, so a guard reverted to the attribute
    scan would stay green.

    The module goes in a temporary directory appended to ``tools.__path__`` rather
    than into the package on disk. ``pkgutil.iter_modules`` walks that same
    ``__path__``, so the scan still sees it and ``importlib`` still imports it --
    but nothing is ever written into the version-controlled source tree, so an
    abnormal termination cannot leave a stray module behind for a later commit to
    pick up.
    """
    name = "probe_misfiled_tool"
    with tempfile.TemporaryDirectory() as directory:
        (Path(directory) / "_probe_misfiled_module.py").write_text(
            "from typing import Any\n"
            "from reachy_language_tutor.tools.core_tools import Tool, ToolDependencies\n\n\n"
            "class ProbeMisfiled(Tool):\n"
            f"    name = {name!r}\n"
            '    description = "probe"\n'
            '    parameters_schema: dict[str, Any] = {"type": "object", "properties": {}}\n\n'
            "    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:\n"
            "        return {}\n",
            encoding="utf-8",
        )
        original = list(tools.__path__)
        tools.__path__.append(directory)
        importlib.invalidate_caches()
        try:
            yield name
        finally:
            tools.__path__[:] = original
            sys.modules.pop("reachy_language_tutor.tools._probe_misfiled_module", None)
            core_tools = _core_tools()
            core_tools._LOADED_TOOL_CLASS_CACHE.clear()
            core_tools._LOADED_REMOTE_TOOL_CACHE.clear()
            importlib.invalidate_caches()


def _learner_reading_tools() -> set:
    """Discover which offered tools touch learner data, rather than naming them.

    A guard that checks three hard-coded names says nothing about the fourth
    learner tool somebody adds later, which is the case it most needs to cover.

    The module is resolved from each registered INSTANCE rather than by assembling
    ``reachy_language_tutor.tools.<name>``. That dotted path is only correct for
    packaged tools: a file-backed external tool registers under
    ``reachy_language_tutor._external_tools.<name>``, so guessing the path would
    find nothing, read an empty source, and quietly conclude the tool does not read
    learner data -- a security guard failing open. A tool whose source cannot be
    read at all is treated as learner-reading, so the unknown case fails closed.
    """
    discovered = set()
    for name, tool in _core_tools().get_tools().items():
        module = sys.modules.get(type(tool).__module__)
        path = getattr(module, "__file__", None)
        if path is None:
            discovered.add(name)
            continue
        try:
            source = Path(path).read_text(encoding="utf-8")
        except OSError:
            discovered.add(name)
            continue
        if _reads_learner_data(source):
            discovered.add(name)
    return discovered


def test_locked_profile_is_set() -> None:
    """The app ships locked to one profile; end users must not switch personalities."""
    assert config.LOCKED_PROFILE == "_reachy_language_tutor_locked"


def test_locked_profile_parses() -> None:
    """profile.md must be present and valid, or the app exits at startup."""
    profile = _locked_profile()
    assert profile.instructions.strip(), "profile has no system prompt"


def test_every_declared_tool_exists() -> None:
    """A declared name the loader cannot resolve is dropped with only a warning."""
    declared = _locked_profile().default_tools
    assert declared, "the locked profile declares no tools at all"
    assert _unresolvable(declared) == []


def test_the_guard_fails_on_a_name_the_loader_cannot_resolve() -> None:
    """The negative control: without this, a guard that always returned [] would pass."""
    assert _unresolvable(["get_profile", "definitely_not_a_tool"]) == ["definitely_not_a_tool"]


def test_the_guard_asks_the_loader_rather_than_scanning_for_classes(misfiled_tool) -> None:
    """Pin the rule itself, so nobody reverts this file to the attribute scan.

    The two rules agree on every tool shipped today, so the fixture manufactures the
    one case that separates them. If _unresolvable ever goes back to scanning for
    classes, the second assertion below is what says so.
    """
    assert misfiled_tool in _scanned_tool_names(), "the old attribute scan would have accepted it"
    assert _unresolvable([misfiled_tool]) == [misfiled_tool], "the guard accepted a tool that cannot load"


def test_the_loader_drops_an_unresolvable_tool_without_failing_startup(misfiled_tool) -> None:
    """Why the guard has to be right: nothing downstream of it complains.

    The app starts, the model is simply never offered the tool, and the only trace
    is one warning line. That is what makes this file the last line of defence.
    """
    assert sorted(_registry(["get_profile", misfiled_tool])) == ["get_profile"]


def test_each_declared_tool_lives_in_the_file_named_after_it() -> None:
    """Pin the convention the codebase keeps, which is stricter than the loader's rule.

    The loader would happily register a class under a name that differs from its
    module; it just would not answer to the DECLARED name. Keeping the two equal is
    what makes "the tool is in tools/<name>.py" a safe thing for a reader to assume.
    """
    registry = _registry(_locked_profile().default_tools)
    assert registry, "no tool resolved at all"
    for name, tool in registry.items():
        module = sys.modules[type(tool).__module__]
        assert Path(module.__file__).stem == name


def test_the_three_learner_tools_reach_the_conversation() -> None:
    """The task this app exists for: a tool absent here cannot be called at all."""
    assert LEARNER_TOOLS <= set(_registry(_locked_profile().default_tools))


def test_the_learner_tools_are_offered_to_the_realtime_session() -> None:
    """get_tool_specs is what the realtime session is actually handed.

    _run_realtime_session logs these names and puts them in session.update, so this
    is the seam between "declared in a file" and "callable by the model".
    """
    assert LEARNER_TOOLS <= {spec["name"] for spec in _core_tools().get_tool_specs()}


def test_no_learner_tool_asks_the_model_for_anything_outside_the_catalog_vocabulary() -> None:
    """The identity boundary, stated as an allow-list over discovered tools.

    Identity comes from application state; a declared parameter is something the
    model fills in, which is something a person talking to the robot can influence.
    Asserted on the specs the SESSION receives, over tools DISCOVERED by the same
    rule the boundary suite uses -- so a fourth learner tool added later is covered
    without anyone having to remember this rule exists.
    """
    specs = {spec["name"]: spec for spec in _core_tools().get_tool_specs()}
    discovered = _learner_reading_tools()
    assert discovered, "discovery found no learner-reading tool among the offered specs"
    assert LEARNER_TOOLS <= discovered, "a known learner tool was not discovered"

    for name in sorted(discovered):
        declared = set(specs[name]["parameters"].get("properties", {}))
        outside = declared - PERMITTED_LEARNER_TOOL_PARAMETERS
        assert not outside, f"{name} declares {sorted(outside)}, outside the permitted vocabulary"


def test_the_prompt_tells_the_tutor_never_to_ask_who_it_is_talking_to() -> None:
    """A security consideration W10 names, pinned positively rather than by absence.

    The three tools refuse an identity in their arguments, but nothing stopped the
    PROMPT from telling the model to ask a person for a name and work it in some
    other way. Absence tests cannot catch that -- only a positive requirement can.

    Whitespace is collapsed first so that reflowing the paragraph, which changes no
    meaning, cannot fail this. Rewording it still does, which is the point.
    """
    text = " ".join(_locked_profile().instructions.split())
    assert "never ask someone for a name or an id" in text
    assert "Call it rather than asking who they are" in text
