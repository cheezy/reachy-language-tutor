"""Guard the trust anchor: who the app thinks it is serving.

CLAUDE.md: "The app sets the current learner ID from recognition. Tools must never
accept a learner's identity from the conversation, so nobody can talk their way into
another person's profile." ToolDependencies is what makes that enforceable -- the LLM
is shown a tool's parameters_schema and never the dependencies behind it.

Two of these tests are source-level tripwires rather than runtime protection, and it
is worth being honest about what they do not catch. ToolDependencies is a plain
mutable dataclass, so nothing stops a write at runtime. The guards below read the
tools package's own source and miss: indirect mutation (setattr with a computed name,
vars(deps)[...] = ..., deps.__dict__.update(...)), writes from modules outside the
tools package that hold a deps reference -- conversation_handler.py and
huggingface_realtime.py both do -- and a tool that takes an identity under an
unrelated spelling ("who", "person") and passes it to a reader. The real protection is
architectural; these fail the suite when the obvious mistake is made.
"""

import ast
import logging
import pkgutil
import importlib
import dataclasses
from typing import Any
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from reachy_language_tutor import main, tools
from reachy_language_tutor.learners import store
from reachy_language_tutor.tools.core_tools import ToolDependencies


FIELD = "current_learner_id"
IDENTITY_PARAMETERS = {"user_id", "profile_id", "person_id"}


@pytest.fixture
def instance(tmp_path: Path) -> Path:
    """A prepared learner database at a temporary instance path."""
    assert store.ensure_learner_database(tmp_path).ready is True
    return tmp_path


def _deps(**overrides: Any) -> ToolDependencies:
    """Dependencies with the two required fields stubbed, as the existing tests do."""
    return ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock(), **overrides)


def _tool_module_paths() -> list[Path]:
    """Every module in the tools package, as source files to parse.

    iter_modules does not enumerate __init__.py, so it is added explicitly -- it is
    inside the package and could write the field like any other module there.
    """
    paths = [Path(module.module_finder.path) / f"{module.name}.py" for module in pkgutil.iter_modules(tools.__path__)]
    paths += [Path(root) / "__init__.py" for root in tools.__path__]
    assert paths, "found no tool modules to check; the guard would pass vacuously"
    return paths


# --- The field exists and means what it says ---------------------------------------


def test_tool_dependencies_carries_the_current_learner_id() -> None:
    """Tools need somewhere trustworthy to read identity from; this is that place."""
    fields = {f.name: f for f in dataclasses.fields(ToolDependencies)}
    assert FIELD in fields
    assert fields[FIELD].type == str | None
    assert fields[FIELD].default is None


def test_the_field_is_unset_by_default() -> None:
    """Unset must mean nobody, never a real learner -- a wrong identity is the harm."""
    assert _deps().current_learner_id is None


# --- Startup populates it ------------------------------------------------------------


def test_resolving_the_current_learner_returns_the_seeded_learner(instance: Path) -> None:
    """The hard-coded learner resolves against a prepared database."""
    resolved = main.resolve_current_learner_id(instance, logging.getLogger(__name__))
    assert resolved == main.HARDCODED_CURRENT_LEARNER_ID


def test_app_startup_builds_dependencies_carrying_a_valid_seeded_learner(instance: Path) -> None:
    """The wiring end to end: what startup builds names a learner that really exists."""
    from reachy_language_tutor.learners import get_profile

    deps = main.build_tool_dependencies(
        robot=MagicMock(),
        movement_manager=MagicMock(),
        instance_path=instance,
        camera_enabled=False,
        logger=logging.getLogger(__name__),
    )
    assert deps.current_learner_id == store.SEED_LEARNERS[0][0]
    # Populated is not enough; it has to be somebody.
    assert get_profile(deps.current_learner_id, instance_path=instance) is not None


# --- Nothing in the tools package writes it ------------------------------------------


def test_no_tool_module_writes_to_the_current_learner_field() -> None:
    """A tool that could set identity would let the conversation choose whose data it reads."""
    offenders: list[str] = []
    for path in _tool_module_paths():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                targets = [node.target]
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "setattr":
                # A computed attribute name defeats a constant check, so flag every
                # setattr on deps and let a legitimate future use be reviewed.
                first = node.args[0] if node.args else None
                named = node.args[1] if len(node.args) > 1 else None
                if isinstance(first, ast.Name) and first.id == "deps":
                    offenders.append(f"{path.name} line {node.lineno}: setattr on deps")
                elif isinstance(named, ast.Constant) and named.value == FIELD:
                    offenders.append(f"{path.name} line {node.lineno}: setattr of {FIELD}")
                continue
            for target in targets:
                if isinstance(target, ast.Attribute) and target.attr == FIELD:
                    offenders.append(f"{path.name} line {target.lineno}: assigns {FIELD}")

    assert not offenders, "the tools package must never write the current learner: " + "; ".join(offenders)


def test_no_tool_accepts_a_learner_identity_parameter() -> None:
    """Identity must arrive from app state, not from something the LLM can fill in."""
    offenders: list[str] = []
    for module in pkgutil.iter_modules(tools.__path__):
        loaded = importlib.import_module(f"reachy_language_tutor.tools.{module.name}")
        for obj in vars(loaded).values():
            schema = getattr(obj, "parameters_schema", None)
            if not isinstance(obj, type) or not isinstance(schema, dict):
                continue
            names = set(schema.get("properties", {})) | set(schema.get("required", []) or [])
            for name in names:
                if "learner" in name.lower() or name in IDENTITY_PARAMETERS:
                    offenders.append(f"{getattr(obj, 'name', obj.__name__)}.{name}")

    assert not offenders, "tools must not take a learner identity as an argument: " + "; ".join(offenders)


# --- Hard-coded in exactly one place --------------------------------------------------


def test_the_hardcoded_learner_id_matches_the_seeded_learner() -> None:
    """The literal is duplicated on purpose; this is what keeps the copy honest."""
    assert main.HARDCODED_CURRENT_LEARNER_ID == store.SEED_LEARNERS[0][0]


def test_the_learner_id_is_hardcoded_in_exactly_one_place() -> None:
    """Milestone 4 must change one line, so make "one line" a checked fact."""
    package = Path(main.__file__).resolve().parent
    seed_data = package / "learners" / "store.py"

    hits = [
        f"{path.relative_to(package)} x{path.read_text(encoding='utf-8').count(main.HARDCODED_CURRENT_LEARNER_ID)}"
        for path in sorted(package.rglob("*.py"))
        if path != seed_data and main.HARDCODED_CURRENT_LEARNER_ID in path.read_text(encoding="utf-8")
    ]
    assert hits == ["main.py x1"], f"the learner id should be chosen in one place only, found: {hits}"


# --- Both failure modes leave it unset ------------------------------------------------


def test_an_unknown_learner_leaves_the_field_unset(
    instance: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A configured learner who is not in the database serves nobody, loudly."""
    monkeypatch.setattr(main, "HARDCODED_CURRENT_LEARNER_ID", "no-such-learner")
    with caplog.at_level(logging.ERROR, logger=__name__):
        resolved = main.resolve_current_learner_id(instance, logging.getLogger(__name__))

    assert resolved is None
    messages = [record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR]
    assert any("not in the learner database" in message for message in messages), messages
    # Personal data does not belong in a log line; today's id is a placeholder, but
    # milestone 4 replaces it with a recognized person.
    assert not any("no-such-learner" in message for message in messages), messages


def test_an_unreadable_store_leaves_the_field_unset(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A broken database must not be reported as "I do not know you"."""
    with caplog.at_level(logging.ERROR, logger=__name__):
        resolved = main.resolve_current_learner_id(tmp_path, logging.getLogger(__name__))

    assert resolved is None
    messages = [record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR]
    assert any("unreadable" in message for message in messages), messages


def test_startup_dependencies_are_unset_when_the_store_is_missing(tmp_path: Path) -> None:
    """The robot still starts; it is the learner surface that goes dead, not the app."""
    deps = main.build_tool_dependencies(
        robot=MagicMock(),
        movement_manager=MagicMock(),
        instance_path=tmp_path,
        camera_enabled=False,
        logger=logging.getLogger(__name__),
    )
    assert deps.current_learner_id is None
