"""The mark that records why an upstream test cannot pass while this app locks its profile.

These seven test files come from the upstream conversation app and test profile
SWITCHING: creating, editing, deleting, applying and persisting a chosen personality.
This app sets ``config.LOCKED_PROFILE``, which deliberately disables all of that, so
those tests fail by design rather than by regression.

They used to be silenced a whole file at a time, with seven ``--ignore-glob`` entries in
pyproject's ``addopts``. That threw away the 69 PASSING tests in the same files, and --
worse -- meant a test added to one of those files afterwards was never collected at all.
A test for a .env line-injection fix was written in ``test_console.py``, passed when run
directly, and was silently absent from the suite; the suite total simply did not move.
See D21, and the docstring of ``test_backend_config_env_injection.py``.

So the refusal is marked here, on the tests that actually fail, and it is CONDITIONED on
the real cause rather than asserted. If the app ever stops locking its profile,
``LOCKED_PROFILE`` becomes ``None``, the condition goes false, and all of these run again
without anybody having to remember this file exists.

Four mechanisms produce the same by-design failure, which is why the symptoms differ:

* ``personality_routes.py`` and ``tool_space_routes.py`` raise ``profile_locked`` before
  doing anything else -- the visible string in most of these failures.
* ``startup_settings.py`` skips applying a saved profile, so a round-trip assertion sees
  the settings it wrote come back empty.
* ``console.py``'s ``_persist_personality`` returns early, so nothing is written and a
  plain ``assert False`` is all the test can report.
* ``config.py`` resolves ``REACHY_MINI_CUSTOM_PROFILE`` from ``LOCKED_PROFILE`` first, so
  ``Config.__init__`` raises about the locked profile before it reaches the
  name-collision branch the test wanted to exercise.

Not every failure in these files belongs here. ``test_profile_paths.py``'s Windows
wheel-path test fails for a reason of its own and is marked in place, with its own
explanation -- do not reach for this mark without reading why the test fails.
"""

import pytest

import reachy_language_tutor.config as config_mod


# Read through the module rather than copying the literal: the point is that this
# reverses itself when config.py changes, and a copied string would not.
REQUIRES_PROFILE_SWITCHING = pytest.mark.skipif(
    config_mod.LOCKED_PROFILE is not None,
    reason="this app locks its profile (config.LOCKED_PROFILE), which disables profile switching by design",
)
