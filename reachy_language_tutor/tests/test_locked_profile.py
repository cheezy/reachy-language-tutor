"""Guard the locked profile the app cannot start without.

config.py calls sys.exit(1) at import time if LOCKED_PROFILE has no profile.md,
so a mistake here takes the whole app down before it logs anything useful.
These tests fail loudly instead.
"""

import pkgutil
import importlib

from reachy_language_tutor import config, tools
from reachy_language_tutor.profile_store import read_profile_from_directory


def _locked_profile():
    name = config.LOCKED_PROFILE
    return read_profile_from_directory(name, config.DEFAULT_PROFILES_DIRECTORY / name)


def test_locked_profile_is_set() -> None:
    """The app ships locked to one profile; end users must not switch personalities."""
    assert config.LOCKED_PROFILE == "_reachy_language_tutor_locked"


def test_locked_profile_parses() -> None:
    """profile.md must be present and valid, or the app exits at startup."""
    profile = _locked_profile()
    assert profile.instructions.strip(), "profile has no system prompt"


def test_every_declared_tool_exists() -> None:
    """A tool name with no matching Tool subclass breaks the conversation at runtime."""
    known = set()
    for module in pkgutil.iter_modules(tools.__path__):
        loaded = importlib.import_module(f"reachy_language_tutor.tools.{module.name}")
        for obj in vars(loaded).values():
            name = getattr(obj, "name", None)
            if isinstance(obj, type) and isinstance(name, str):
                known.add(name)

    unknown = [t for t in _locked_profile().default_tools if t not in known]
    assert not unknown, f"profile declares unknown tools: {unknown}"
