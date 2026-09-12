"""Pytest configuration for path setup."""

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[1].resolve()
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

# This directory too, so `import profile_lock` works from the test modules that carry the
# profile-lock skipif. Pytest's default "prepend" import mode already puts it here because
# there is no __init__.py, but that is a side effect rather than a promise: adding an
# __init__.py, or running with --import-mode=importlib, would take it away and break six
# test files at once. conftest.py is imported before any test module, so stating it here
# makes the import hold under any import mode.
TESTS_PATH = Path(__file__).parent.resolve()
if str(TESTS_PATH) not in sys.path:
    sys.path.insert(0, str(TESTS_PATH))


# Make tests reproducible by ignoring machine-specific profile/tool env config.
# Without this, importing config during test collection can pick up a developer's
# local .env and fail before tests run.
os.environ["REACHY_MINI_SKIP_DOTENV"] = "1"
os.environ.pop("REACHY_MINI_CUSTOM_PROFILE", None)
os.environ.pop("REACHY_MINI_EXTERNAL_PROFILES_DIRECTORY", None)
os.environ.pop("REACHY_MINI_EXTERNAL_TOOLS_DIRECTORY", None)


import logging  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_root_logging():
    """Put the root logger back the way pytest left it, after every test.

    The app configures logging with ``logging.basicConfig(..., force=True)``
    (``utils.setup_logger``, reached from ``main()``). ``force=True`` does not merely
    set a level: it CLOSES AND REMOVES every existing root handler and installs its
    own. Any test that drives a code path calling it therefore reconfigures logging
    for the whole session, and nothing puts it back.

    Measured, not theorised. Running ``tests/test_tool_spaces.py`` -- which invokes
    ``main()`` for the tool-space CLI subcommands -- and then probing the root logger
    showed level 30 -> 20 and pytest's own handlers replaced::

        before: level=30 handlers=[_LiveLoggingNullHandler, _FileHandler, LogCaptureHandler, ...]
        after:  level=20 handlers=[StreamHandler <stderr>, LogCaptureHandler, ...]

    The visible consequence was three failures in reverse file order:
    ``test_learner_schema.py::test_an_empty_record_reads_as_empty_without_a_warning``
    asserts ``caplog.text == ""`` and, with the root level left at INFO, picked up an
    INFO record from ``ensure_learner_database`` that the default order never showed.
    An order-dependent failure that blames the wrong file, which is the same shape of
    defect as D28 and just as corrosive: the tests it breaks assert a no-PII property,
    and a suite that fails for unrelated reasons is one somebody eventually weakens.

    This lives in ``conftest.py`` rather than in ``test_tool_spaces.py`` deliberately.
    Today that is the only file reaching ``basicConfig``, but the hazard belongs to
    *any* test exercising a code path that configures logging, and fixing the one file
    that happens to do it now is the "fix the member, not the class" mistake CLAUDE.md
    names as this board's most repeated defect.

    Restoring the handler LIST matters as much as the level: ``force=True`` closed
    pytest's live-logging and report-capture handlers, so a later test relying on them
    would be reading a logging stack pytest no longer owns.
    """
    root = logging.getLogger()
    level = root.level
    handlers = list(root.handlers)
    try:
        yield
    finally:
        root.setLevel(level)
        if root.handlers != handlers:
            root.handlers[:] = handlers
