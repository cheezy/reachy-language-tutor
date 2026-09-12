"""The suite must collect every test file on disk -- no file may be excluded wholesale.

D21 removed seven ``--ignore-glob`` entries from pyproject's ``addopts``. They had been
added for a defensible reason (31 upstream profile-switching tests fail by design here)
but the instrument was file-level while the cause was test-level, so they also discarded
69 passing tests -- and silently swallowed anything added to those files afterwards. That
is not hypothetical: tests for a .env line-injection fix were written in
``test_console.py``, passed when run directly, and were never collected by
``pytest reachy_language_tutor -q``. Nothing failed. The total just did not move.

This file is what stops that coming back.

The guard asserts what is PERMITTED -- every ``test_*.py`` under ``tests/`` is collected --
rather than forbidding the one spelling that caused it. Checking for the string
``--ignore-glob`` would have been a deny-list, and this repository has paid four times for
deny-lists (D11 twice, D19, D20): ``--ignore``, ``--deselect``, a ``collect_ignore`` list
in ``conftest.py``, ``norecursedirs``, or narrowing ``testpaths`` would each reintroduce
the identical defect while passing a check written against that string. Comparing the
collected set to the files on disk catches every one of them, including the ones nobody
has thought of yet -- but only because both halves of the comparison ask pytest rather
than assume. The collection below runs with no path argument, which is the difference
between asking what the configuration does and telling it what to collect; and the
file list comes from pytest's own ``python_files`` setting. Earlier drafts got each of
those wrong in turn -- an explicit path that would have missed a narrowed ``testpaths``,
then a hard-coded ``test_*.py`` glob that would have missed a ``*_test.py`` file -- while
this docstring claimed the coverage both times. That is the failure mode to watch for
here: the guard is only ever as good as its weaker half, and prose does not fix it.
"""

import sys
import subprocess
from pathlib import Path

import pytest


TESTS_DIRECTORY = Path(__file__).parent.resolve()
PROJECT_DIRECTORY = TESTS_DIRECTORY.parent


def _files_pytest_actually_collects() -> set[str]:
    """Return the test file names pytest collects, asked of pytest rather than inferred.

    A subprocess, because the answer has to come from a real collection run under the
    real configuration -- reading pyproject here would only re-state what this test is
    supposed to be checking, and would miss every exclusion mechanism that does not live
    in pyproject at all.
    """
    # Deliberately NO path argument. A path on the command line overrides ``testpaths``
    # entirely, so a guard that passed one would collect everything no matter how
    # ``testpaths`` had been narrowed -- it would have been blind to one of the very
    # mechanisms this file claims to catch. Running bare, from the project directory, is
    # what makes the answer reflect the configuration actually in force.
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=PROJECT_DIRECTORY,
        capture_output=True,
        text=True,
    )
    if result.returncode not in (0, 5):
        pytest.fail(f"collection run failed ({result.returncode}):\n{result.stdout[-3000:]}\n{result.stderr[-2000:]}")

    collected = set()
    for line in result.stdout.splitlines():
        node, _, _ = line.partition("::")
        if node.endswith(".py"):
            # Node ids are relative to the rootdir, which is PROJECT_DIRECTORY. Compare
            # whole relative paths rather than bare names so a file in tests/tools/ is
            # covered too -- a name-only comparison would let a whole subdirectory be
            # excluded without this noticing.
            collected.add(Path(node).as_posix())
    return collected


def test_every_test_file_on_disk_is_collected_by_the_suite(pytestconfig: pytest.Config) -> None:
    """A test file that exists but is never collected is a test that cannot fail.

    This is the whole defect D21 fixed, stated as an assertion. It deliberately compares
    against the filesystem rather than a hard-coded list: a list would need maintaining,
    and the day somebody forgot to update it is the day this guard stops guarding.

    "What counts as a test file" is the UNION of pytest's ``python_files`` setting and the
    two default patterns written here, and the union is the point. Globbing ``test_*.py``
    alone missed ``*_test.py``, which pytest also collects. Taking ``python_files`` alone
    fixed that and opened a worse hole: the guard then meant whatever the collector meant,
    so narrowing that setting hid files from both halves at once and this file still passed.
    The literals are a floor configuration cannot lower; ``getini`` only ever adds to it.
    """
    # The union, not the ini value. Deriving the patterns from ``python_files`` alone
    # made the guard adopt the collector's own definition of a test file, so narrowing
    # that setting narrowed BOTH halves of the comparison identically and the guard went
    # blind -- demonstrated with `python_files = ["test_[!c]*.py", ...]`, which un-collects
    # test_console.py entirely while this file still reports 2 passed. The literals are a
    # floor a configuration change cannot lower; ``getini`` can only add to it.
    patterns = set(pytestconfig.getini("python_files")) | {"test_*.py", "*_test.py"}
    on_disk = {
        path.relative_to(PROJECT_DIRECTORY).as_posix()
        for pattern in patterns
        for path in TESTS_DIRECTORY.rglob(pattern)
    }
    assert on_disk, "no test files found -- this guard is not looking where it thinks it is"

    collected = _files_pytest_actually_collects()

    never_collected = sorted(on_disk - collected)
    assert not never_collected, (
        "these test files exist but the suite never collects them, so nothing in them can "
        f"ever fail: {never_collected}. Do not silence a failing test by excluding its FILE -- "
        "mark the failing test itself, the way tests/profile_lock.py does."
    )


def test_pyproject_declares_no_file_level_test_exclusion() -> None:
    """A fast, legible signal for the spelling that actually caused D21.

    Strictly redundant: the collection comparison above catches this and every other
    mechanism. It is here because it names the specific mistake in the specific file
    where somebody would be about to make it again, and because it fails in milliseconds
    with a message that points at the line. It is NOT the guard -- do not add the next
    spelling to it when one turns up; that is how a deny-list grows and keeps losing.
    """
    pyproject = (PROJECT_DIRECTORY / "pyproject.toml").read_text()
    _, _, pytest_section = pyproject.partition("[tool.pytest.ini_options]")
    pytest_section, _, _ = pytest_section.partition("\n[tool.")
    assert pytest_section, "the [tool.pytest.ini_options] section has moved or gone"

    for line in pytest_section.splitlines():
        if line.lstrip().startswith("#"):
            continue
        assert "ignore-glob" not in line and "ignore_glob" not in line, (
            f"a file-level test exclusion is back in pyproject.toml: {line.strip()!r}. "
            "That is D21. Mark the failing tests instead."
        )
