"""Pytest configuration for path setup."""

import os
import re
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
    # EVERY logger's level, not a list of the ones setup_logger happens to touch today.
    # It currently also pins aiortc and aioice (and, under --debug, openai and
    # websockets), and a fixture naming those four would go stale the moment a fifth
    # was added -- the deny-list shape CLAUDE.md records four defects against. Asking
    # the manager for what exists covers every present and future name at once.
    levels = {
        name: existing.level
        for name, existing in logging.root.manager.loggerDict.items()
        if isinstance(existing, logging.Logger)
    }
    try:
        yield
    finally:
        root.setLevel(level)
        if root.handlers != handlers:
            root.handlers[:] = handlers
        for name, existing in list(logging.root.manager.loggerDict.items()):
            if not isinstance(existing, logging.Logger):
                continue
            # A logger that did not exist before the test is reset to NOTSET rather
            # than left alone: aiortc and aioice are created BY the import that
            # configures them, so "restore only what existed" would miss exactly the
            # ones this is for.
            wanted = levels.get(name, logging.NOTSET)
            if existing.level != wanted:
                existing.setLevel(wanted)


# ------------------------------------------------------- the collection floor (D27)

# The floor lives in .stride.md and is read from there, never duplicated here. Two
# copies of a count is how the two drift and the check quietly stops meaning anything.
_REPOSITORY_ROOT = PROJECT_ROOT.parent
_FLOOR_PATTERN = re.compile(r"<!--\s*MINIMUM_COLLECTED_TESTS:\s*(\d+)\s*-->")


def _recorded_collection_floor() -> "int | None":
    """Read the collection floor from .stride.md, or None when there is nothing to read.

    None is a real answer and must not fail the suite: this package is installed onto
    robots without the development repository around it, and a missing .stride.md there
    is the normal case rather than a problem. scripts/check_test_floor.py is the half
    that refuses a MISSING floor, and it only ever runs in the repository.
    """
    stride_md = _REPOSITORY_ROOT / ".stride.md"
    if not stride_md.is_file():
        return None
    try:
        matches = _FLOOR_PATTERN.findall(stride_md.read_text(encoding="utf-8"))
    except OSError:
        return None
    return int(matches[0]) if len(matches) == 1 else None


def _is_whole_suite_run(config: "pytest.Config") -> bool:
    """Report whether this invocation was meant to collect the whole suite.

    The floor may only fire on a run that was TRYING to collect everything. A developer
    running one file, or narrowing with -k, has collected fewer tests on purpose, and a
    check that failed them would be noise they would rightly silence -- which would take
    the real guard with it.

    Measured, which is why the test is shaped this way: the gate invocation and the
    bypass produce IDENTICAL `config.args` (both `['reachy_language_tutor']`) and differ
    only in invocation params, so args alone cannot tell them apart -- but a single-file
    run passes a FILE and a -k run leaves `keyword` set. Directory-only args with no
    selection narrowing is exactly the shape of a full run.
    """
    if getattr(config.option, "keyword", "") or getattr(config.option, "markexpr", ""):
        return False
    if getattr(config.option, "deselect", None):
        return False
    # --lf registers with dest="lf", NOT "last_failed" -- verified against the installed
    # _pytest/cacheprovider.py. The first version of this line read `last_failed`, which
    # does not exist, so getattr returned its default and the exemption was dead code:
    # a developer's `--lf` run collects a handful of tests, was judged whole-suite, and
    # would have failed the floor. That is the shape of bug this whole task is about --
    # a guard that does not mean what it says -- so it is named rather than just fixed.
    if getattr(config.option, "lf", False) or getattr(config.option, "failedfirst", False):
        return False
    # --collect-only is deliberately NOT exempt. A collect-only run knows exactly how
    # many tests it found, which makes it the cheapest possible place to prove an
    # exclusion was applied -- and a collect-only run that finds too few is the same
    # signal as a full run that does.
    # A directory argument counts as whole-suite only when it CONTAINS the whole tests
    # tree. Any directory used to qualify, which failed a legitimate workflow: measured,
    # `pytest reachy_language_tutor/tests/tools -q` collects 91 and tripped the floor --
    # and the docstring right above calls that the dangerous case, because a check that
    # fails developers is one they silence along with the guard it protects.
    for argument in config.args:
        if "::" in argument:
            return False
        candidate = (Path(config.rootdir) / argument)
        if not candidate.is_dir():
            candidate = Path(argument)
        if not candidate.is_dir():
            return False
        if not TESTS_PATH.resolve().is_relative_to(candidate.resolve()):
            return False
    return True


def pytest_sessionfinish(session, exitstatus):  # noqa: ANN001, ANN201
    """Fail a whole-suite run that collected fewer tests than .stride.md records.

    This is the half of D27's fix that has to live inside the suite, and the reason is
    structural: an exclusion passed on the COMMAND LINE is invisible to any check that
    runs its own separate collection, because that collection does not carry the
    offending argument. Only the session that actually ran knows what it actually
    collected.

    It does not replace tests/test_pytest_configuration.py, which compares the collected
    set against the filesystem and names WHICH file went missing. This only knows that
    the total fell, which is a blunter signal -- and a blunter signal that cannot be
    argued out of is exactly what the task asked for.
    """
    # The gate sets STRIDE_TEST_FLOOR=enforce, which overrides every exemption below.
    # Without it the heuristic is the only thing deciding, and the heuristic has to let
    # -k and single-file runs through or it would break every developer -- which would
    # make it noise, and noise gets silenced along with the guard. So the invocation that
    # actually gates completion says so explicitly rather than hoping it looks whole.
    # An allow-list of what the variable may SAY, and anything else is an error rather
    # than a silent fallback. Measured, which is why it is written this way: the
    # out-of-suite guard checked `"STRIDE_TEST_FLOOR=enforce" in line` while this
    # compared `== "enforce"`, so `STRIDE_TEST_FLOOR=enforced` satisfied the guard and
    # defeated this -- both after_doing lines exited 0 with 47 tests deselected and the
    # /rpc suite never run. That is D19's shape: a guard that does not mean the same
    # thing as the code it protects. A value we do not recognise now fails loudly, so a
    # typo can never quietly re-enable the exemption it was meant to override.
    # Two permitted values, named. "enforce" is what the gate demands; "off" is what a
    # NESTED collection passes -- the guards in test_pytest_configuration.py and
    # check_test_floor.py shell out to their own `--collect-only` runs, and those runs
    # are how the floor is MEASURED. Without an opt-out they enforce it on themselves
    # via the whole-suite heuristic, so a real breach made them abort with "collection
    # run failed" instead of naming which file went missing -- the guard losing exactly
    # the diagnostic it exists for. "off" is safe on the gate line because the
    # out-of-suite checker requires the literal token STRIDE_TEST_FLOOR=enforce there.
    requested = os.environ.get("STRIDE_TEST_FLOOR")
    if requested == "off":
        return
    if requested is not None and requested != "enforce":
        print(
            f"\nSTRIDE_TEST_FLOOR is set to {requested!r}, which is not 'enforce' or 'off'.\n"
            "That variable has exactly two permitted values. A near-miss silently re-enables the\n"
            "narrowed-run exemption, which is how a gate reports green with tests deselected."
        )
        session.exitstatus = 1
        return

    demanded = requested == "enforce"
    if not demanded and not _is_whole_suite_run(session.config):
        return
    floor = _recorded_collection_floor()
    if floor is None:
        return
    collected = int(getattr(session, "testscollected", 0))
    if collected >= floor:
        # The floor is satisfied -- but a gate run that COLLECTED enough and EXECUTED
        # nothing is not a gate. session.testscollected is populated during collection,
        # so `--collect-only` clears the floor at full count while running zero tests,
        # and a review measured that reading greener than a normal run. The out-of-suite
        # checker refuses --collect-only on the gate line; this is the half that catches
        # it if the line is ever invoked another way. Only under 'enforce': the nested
        # measuring collections pass 'off' and never reach here.
        if demanded and getattr(session.config.option, "collectonly", False):
            print(
                "\nTHE GATE RAN NOTHING: this invocation demanded the collection floor and then "
                "only collected.\nA run that executes no tests satisfies the floor and proves "
                "nothing. Drop --collect-only from the gate."
            )
            session.exitstatus = 1
        return

    print(
        f"\nTEST COLLECTION FELL BELOW THE FLOOR: {collected} collected, floor is {floor}.\n"
        f"{floor - collected} test(s) were not collected by THIS invocation. An exclusion on the\n"
        "pytest command line is the usual cause, and it is invisible to a guard that runs its own\n"
        "collection -- see D27. If tests were removed deliberately, lower the floor in .stride.md\n"
        "in the same change so the drop is visible in the diff."
    )
    session.exitstatus = 1
