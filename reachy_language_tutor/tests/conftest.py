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
