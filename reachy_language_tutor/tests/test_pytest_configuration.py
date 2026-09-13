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

import os
import re
import sys
import tempfile
import importlib.util
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
    environment = {**os.environ, "STRIDE_TEST_FLOOR": "off"}
        # STRIDE_TEST_FLOOR is set to 'off' deliberately. This nested collection exists to
        # find out WHICH file went missing; if it inherited the floor it would exit 1 on
        # the floor's own breach and this helper would abort with "collection run failed"
        # instead of naming the file -- the guard losing exactly the diagnostic it is for.
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=PROJECT_DIRECTORY,
        capture_output=True,
        text=True,
        env=environment,
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


# --------------------------------------------------- the collection floor (D27)

REPOSITORY_ROOT = PROJECT_DIRECTORY.parent

# A COMPLETE after_doing hook, because the guard now validates the whole block rather
# than one line found by substring. A fixture carrying only the gate line is refused for
# a reason the test using it is not about.
_TOOLCHAIN = str(Path(sys.executable).parent)

_HOOK_BLOCK = (
    "## after_doing\n\n```bash\n"
    "{toolchain}/ruff check reachy_language_tutor/src\n"
    "{gate}\n"
    "{toolchain}/python scripts/check_test_floor.py\n"
    "```\n"
)
STRIDE_MD = REPOSITORY_ROOT / ".stride.md"
FLOOR_PATTERN = re.compile(r"<!--\s*MINIMUM_COLLECTED_TESTS:\s*(\d+)\s*-->")


def _floor_from_stride_md() -> int:
    """Read the one authoritative floor, failing loudly if it is not exactly one."""
    if not STRIDE_MD.is_file():
        pytest.skip(".stride.md is absent; this is an installed copy, not the repository")
    matches = FLOOR_PATTERN.findall(STRIDE_MD.read_text(encoding="utf-8"))
    assert len(matches) == 1, (
        f"expected exactly one MINIMUM_COLLECTED_TESTS marker in .stride.md, found {len(matches)}. "
        "Two copies of a count is how the two drift and the check stops meaning anything."
    )
    return int(matches[0])


def test_the_collection_floor_is_recorded_exactly_once_and_is_not_above_reality() -> None:
    """The floor is a MINIMUM, and it has to be one that the real suite clears.

    Both directions matter. A floor recorded twice can drift. A floor set above the true
    count fails every run for a reason nobody can act on, and the fix for that would be
    to lower it -- which is the same motion as disabling it, so it must never be the
    state anyone finds the repository in.
    """
    floor = _floor_from_stride_md()
    collected = len(_node_ids_of_a_full_collection())

    assert floor > 0, "a floor of zero guards nothing"
    assert floor <= collected, (
        f"the recorded floor ({floor}) is above what a full collection actually finds "
        f"({collected}). Lower it in .stride.md, in the same change that removed the tests."
    )


def _node_ids_of_a_full_collection() -> set:
    """Every test node id a bare collection finds, asked of pytest."""
    environment = {**os.environ, "STRIDE_TEST_FLOOR": "off"}
        # STRIDE_TEST_FLOOR is set to 'off' deliberately. This nested collection exists to
        # find out WHICH file went missing; if it inherited the floor it would exit 1 on
        # the floor's own breach and this helper would abort with "collection run failed"
        # instead of naming the file -- the guard losing exactly the diagnostic it is for.
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=PROJECT_DIRECTORY,
        capture_output=True,
        text=True,
        env=environment,
    )
    if result.returncode not in (0, 5):
        pytest.fail(f"collection run failed ({result.returncode}):\n{result.stdout[-2000:]}")
    return {line.strip() for line in result.stdout.splitlines() if "::" in line}


def test_an_exclusion_on_the_command_line_makes_the_run_fail() -> None:
    """The measured bypass itself, with the argument shape the defect actually names.

    Before D27: `pytest reachy_language_tutor -q --ignore-glob='*test_rpc_control_surface.py'`
    dropped 38 tests, reported green, and the guard above passed inside that very run --
    because that guard shells out to its own argument-free collection and therefore
    validates a different invocation than the one running.

    An earlier version of this test substituted a single path argument for the
    --ignore-glob, to keep it fast. A review pointed out that this proves a weaker thing
    than the criterion asks for: the path argument exercises the override and the count,
    but not the argument shape a bypass would actually take. `--collect-only` gets the
    speed back honestly -- a collect-only run knows exactly how many tests it found, so
    the real exclusion can be applied and measured in about a second instead of fifteen.
    """
    floor = _floor_from_stride_md()
    environment = {**os.environ, "STRIDE_TEST_FLOOR": "enforce"}

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "reachy_language_tutor", "-q", "--collect-only",
         "-p", "no:cacheprovider", "--ignore-glob=*test_rpc_control_surface.py"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode != 0, (
        "the exact bypass this defect was filed for still exits 0 -- test files were "
        f"dropped and nothing noticed.\n{result.stdout[-2000:]}"
    )
    assert "FELL BELOW THE FLOOR" in result.stdout, (
        "the run failed, but not for the floor's reason; this test would be passing over "
        f"a different bug.\n{result.stdout[-2000:]}"
    )
    assert str(floor) in result.stdout, "the failure message should name the floor it applied"


def test_the_same_invocation_without_the_exclusion_passes() -> None:
    """The other half, or the test above passes for any reason at all.

    A regression proof that only ever shows the failing case cannot distinguish 'the
    exclusion was caught' from 'this command never works'.

    Deliberately WITHOUT the override: a gate run that only collects is now refused in
    its own right, because a run that executes nothing satisfies the floor and proves
    nothing. This takes the heuristic path instead -- a whole-suite collection, floor
    satisfied, no complaint -- which is the comparison this test needs.
    """
    environment = {key: value for key, value in os.environ.items() if key != "STRIDE_TEST_FLOOR"}

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "reachy_language_tutor", "-q", "--collect-only",
         "-p", "no:cacheprovider"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode == 0, f"a clean collection must pass the floor\n{result.stdout[-2000:]}"
    assert "FELL BELOW THE FLOOR" not in result.stdout


def test_the_floor_checker_actually_fails_when_the_floor_is_missing() -> None:
    """RUN the out-of-suite checker against a .stride.md with no floor, do not grep it.

    The first version of this test searched the script's SOURCE TEXT for the literal
    "return 1" near an "if floor is None:" split. That passes on the presence of a
    string: it would have kept passing if main() stopped being called, if the return
    value stopped being used, or if the branch became unreachable. A review named it,
    and it is the same defect this repository keeps paying for -- a check that does not
    mean what it says.

    So this executes the real script against a temporary repository layout.
    """
    checker = REPOSITORY_ROOT / "scripts" / "check_test_floor.py"
    assert checker.is_file(), "the out-of-suite floor checker is gone"

    with tempfile.TemporaryDirectory() as directory:
        fake_root = Path(directory)
        (fake_root / "scripts").mkdir()
        (fake_root / "scripts" / "check_test_floor.py").write_text(
            checker.read_text(encoding="utf-8"), encoding="utf-8"
        )
        # An after_doing section that demands enforcement, so the gate check passes and
        # the MISSING FLOOR is the only thing left to fail on -- otherwise this test
        # would go green on the wrong complaint.
        (fake_root / ".stride.md").write_text(
            _HOOK_BLOCK.format(toolchain=_TOOLCHAIN, gate=f"STRIDE_TEST_FLOOR=enforce {_TOOLCHAIN}/python -m pytest reachy_language_tutor -q"),
            encoding="utf-8",
        )
        (fake_root / "reachy_language_tutor").mkdir()

        result = subprocess.run(
            [sys.executable, str(fake_root / "scripts" / "check_test_floor.py")],
            capture_output=True,
            text=True,
        )

    assert result.returncode != 0, "a missing floor must FAIL, not silently disable the check"
    assert "MINIMUM_COLLECTED_TESTS" in result.stdout, (
        f"it failed, but not because the floor was missing:\n{result.stdout}\n{result.stderr}"
    )


def test_the_gate_assertion_lives_outside_the_suite() -> None:
    """The check that the gate still demands enforcement must not be deselectable.

    A review demonstrated the attack: drop STRIDE_TEST_FLOOR=enforce from the pytest
    line, add `-k 'not pytest_configuration'` to deselect whatever in here would have
    noticed, and delete the checker's own hook line. The -k is the part that works only
    while the assertion lives in the suite, so it does not live here any more -- it
    lives in scripts/check_test_floor.py, which a -k cannot reach.

    What remains in the suite is this: a statement that the assertion is out there.
    """
    checker = (REPOSITORY_ROOT / "scripts" / "check_test_floor.py").read_text(encoding="utf-8")

    assert "STRIDE_TEST_FLOOR=enforce" in checker, (
        "the out-of-suite checker no longer verifies that the gate demands enforcement; "
        "moving that assertion back into the suite makes it deselectable by the very "
        "invocation it polices"
    )
    assert "check_the_gate_still_demands_enforcement" in checker


def test_the_floor_is_parsed_the_same_way_everywhere_it_is_read() -> None:
    """One number, and one rule for reading it.

    The count itself lives only in .stride.md, but three files parse it: this one,
    conftest.py, and scripts/check_test_floor.py. They cannot import from each other --
    the script must not depend on the suite it polices, and the suite must not depend on
    a script that may be absent on an installed robot -- so the pattern is repeated. A
    repeated rule that drifts is a rule that means different things in different places,
    which is the defect class this whole task is about, so the copies are pinned equal.
    """
    pattern_literal = r"<!--\s*MINIMUM_COLLECTED_TESTS:\s*(\d+)\s*-->"
    readers = [
        Path(__file__),
        TESTS_DIRECTORY / "conftest.py",
        REPOSITORY_ROOT / "scripts" / "check_test_floor.py",
    ]

    for reader in readers:
        assert reader.is_file(), f"a reader of the floor has gone missing: {reader}"
        assert pattern_literal in reader.read_text(encoding="utf-8"), (
            f"{reader.name} parses the floor with a different pattern than its siblings; "
            "all three must agree on what the marker looks like"
        )


def test_a_near_miss_value_for_the_override_fails_loudly() -> None:
    """STRIDE_TEST_FLOOR has exactly one permitted value, and anything else is an error.

    A security review measured this end to end: the out-of-suite guard asked whether
    "STRIDE_TEST_FLOOR=enforce" appeared ANYWHERE in the gate line, while conftest asked
    whether the variable EQUALLED "enforce". So `STRIDE_TEST_FLOOR=enforced` satisfied
    the guard and defeated the consumer -- both after_doing lines exited 0, 47 tests were
    deselected, and the /rpc write-surface suite never ran behind a fully green gate.

    That is D19's shape, the rule this task's own patterns_to_follow cites: a guard must
    mean the same thing as the code it protects. Both halves now match exactly, and an
    unrecognised value fails rather than falling back to the heuristic it was meant to
    override -- because a silent fallback is indistinguishable from the attack.
    """
    environment = {**os.environ, "STRIDE_TEST_FLOOR": "enforced"}

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "reachy_language_tutor", "-q", "--collect-only",
         "-p", "no:cacheprovider"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode != 0, (
        "a near-miss value silently re-enabled the narrowed-run exemption; that is the "
        f"measured bypass.\n{result.stdout[-1500:]}"
    )
    assert "not 'enforce'" in result.stdout, (
        f"it failed, but not because the value was unrecognised.\n{result.stdout[-1500:]}"
    )


def test_the_gate_line_check_matches_a_whole_token_not_a_substring() -> None:
    """The out-of-suite guard must reject a near-miss on the gate line too.

    Both halves of one rule, pinned to agree. Checked by calling the checker's own
    function rather than re-implementing its logic here -- a second implementation is
    how two copies of a rule start meaning different things.
    """
    checker_path = REPOSITORY_ROOT / "scripts" / "check_test_floor.py"
    spec = importlib.util.spec_from_file_location("_check_test_floor", checker_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    with tempfile.TemporaryDirectory() as directory:
        stride_md = Path(directory) / ".stride.md"

        stride_md.write_text(
            _HOOK_BLOCK.format(toolchain=_TOOLCHAIN, gate=f"STRIDE_TEST_FLOOR=enforced {_TOOLCHAIN}/python -m pytest reachy_language_tutor -q"),
            encoding="utf-8",
        )
        complaint = module.check_the_gate_still_demands_enforcement(stride_md)
        assert complaint is not None, "'enforced' must be rejected: it is not the permitted value"
        assert "assignment prefix" in complaint, (
            f"the complaint should name what the line must actually look like: {complaint!r}"
        )

        stride_md.write_text(
            _HOOK_BLOCK.format(toolchain=_TOOLCHAIN, gate=f"STRIDE_TEST_FLOOR=enforce {_TOOLCHAIN}/python -m pytest reachy_language_tutor -q"),
            encoding="utf-8",
        )
        assert module.check_the_gate_still_demands_enforcement(stride_md) is None, (
            "the permitted value must be accepted, or the guard blocks its own gate"
        )

        # The token must be an assignment PREFIX on the pytest command, not merely
        # present on the line. Each decoy below satisfies a token search and sets
        # nothing, and each was measured defeating the token-only version of this guard.
        decoys = [
            f"echo STRIDE_TEST_FLOOR=enforce && {_TOOLCHAIN}/python -m pytest reachy_language_tutor -q -k 'not rpc'",
            f"true STRIDE_TEST_FLOOR=enforce && {_TOOLCHAIN}/python -m pytest reachy_language_tutor -q",
            f"{_TOOLCHAIN}/python -m pytest reachy_language_tutor -q # STRIDE_TEST_FLOOR=enforce",
            f"STRIDE_TEST_FLOOR=ENFORCE {_TOOLCHAIN}/python -m pytest reachy_language_tutor -q",
            # 'off' is the nested-collection opt-out, and it must never be what the GATE
            # hands pytest -- that would disable the floor on the one run that matters.
            f"STRIDE_TEST_FLOOR=off {_TOOLCHAIN}/python -m pytest reachy_language_tutor -q",
            # The critical a review measured: the permitted token IS present, and the
            # shell hands pytest the LAST assignment, so pytest receives 'off'. Asking
            # whether the token appears is a different question from asking what the
            # command actually receives, and the gap between them was a green run with
            # the /rpc suite never collected.
            f"STRIDE_TEST_FLOOR=enforce STRIDE_TEST_FLOOR=off {_TOOLCHAIN}/python -m pytest reachy_language_tutor -q",
            # `env` carries its own assignments, so it can override the prefix.
            f"STRIDE_TEST_FLOOR=enforce env STRIDE_TEST_FLOOR=off {_TOOLCHAIN}/python -m pytest reachy_language_tutor -q",
            # A chained second pytest command: validating only the first left this one
            # running with the floor disabled, and the line reads as if it were demanded.
            f"STRIDE_TEST_FLOOR=enforce {_TOOLCHAIN}/python -m pytest reachy_language_tutor -q && STRIDE_TEST_FLOOR=off {_TOOLCHAIN}/python -m pytest reachy_language_tutor -q",
            # Every wrapper between the assignments and the interpreter, including ones
            # that would have worked. The guard is deliberately STRICTER than the shell
            # here: `nice python` really does deliver "enforce", and is still refused,
            # because a rule about a shape the guard can reason about beats a list of
            # wrappers it has to keep up with. Refusing too much fails the build loudly;
            # accepting too much is a green gate with tests dropped.
            f"STRIDE_TEST_FLOOR=enforce env {_TOOLCHAIN}/python -m pytest reachy_language_tutor -q",
            f"STRIDE_TEST_FLOOR=enforce nice {_TOOLCHAIN}/python -m pytest reachy_language_tutor -q",
            f"STRIDE_TEST_FLOOR=enforce exec env STRIDE_TEST_FLOOR=off {_TOOLCHAIN}/python -m pytest reachy_language_tutor -q",
            f"STRIDE_TEST_FLOOR=enforce sh -c f'STRIDE_TEST_FLOOR=off {_TOOLCHAIN}/python -m pytest reachy_language_tutor -q'",
        ]
        for decoy in decoys:
            stride_md.write_text(
                _HOOK_BLOCK.format(toolchain=_TOOLCHAIN, gate=decoy), encoding="utf-8"
            )
            assert module.check_the_gate_still_demands_enforcement(stride_md) is not None, (
                f"this line sets nothing but satisfied the guard: {decoy!r}"
            )

        # And the legitimate shapes must still pass, or the guard blocks its own gate.
        # Even a benign-looking assignment is refused, because the set of permitted
        # NAMES has to be bounded: PYTHONPATH can shadow pytest itself and PYTEST_ADDOPTS
        # can carry any exclusion, and a rule that admits "harmless" names has to decide
        # which those are. Adding PYTHONHASHSEED would be a deliberate, visible edit.
        stride_md.write_text(
            _HOOK_BLOCK.format(
                toolchain=_TOOLCHAIN,
                gate=f"PYTHONHASHSEED=0 STRIDE_TEST_FLOOR=enforce {_TOOLCHAIN}/python -m pytest reachy_language_tutor -q",
            ),
            encoding="utf-8",
        )
        complaint = module.check_the_gate_still_demands_enforcement(stride_md)
        assert complaint is not None and "PYTHONHASHSEED" in complaint, (
            "an assignment name outside the permitted set must be refused by name"
        )

        for permitted in [
            # Repeated, but ending on the permitted value -- the shell hands pytest
            # 'enforce', so this is correct and must not be refused for looking odd.
            f"STRIDE_TEST_FLOOR=off STRIDE_TEST_FLOOR=enforce {_TOOLCHAIN}/python -m pytest reachy_language_tutor -q",
        ]:
            stride_md.write_text(
                _HOOK_BLOCK.format(toolchain=_TOOLCHAIN, gate=permitted), encoding="utf-8"
            )
            assert module.check_the_gate_still_demands_enforcement(stride_md) is None, (
                f"this line does hand pytest 'enforce' and must be accepted: {permitted!r}"
            )


def test_a_developer_running_a_test_subdirectory_is_not_failed_by_the_floor() -> None:
    """The floor must never fire on a run that narrowed on purpose.

    Measured before the fix: `pytest reachy_language_tutor/tests/tools -q` collected 91
    and exited 1 on the floor, because any directory argument was judged whole-suite.
    conftest.py's own docstring calls this the dangerous case -- a guard that fails
    developers is one they silence, taking the real check with it.

    A directory now counts as whole-suite only when it CONTAINS the tests tree.
    """
    subdirectory = TESTS_DIRECTORY / "tools"
    if not subdirectory.is_dir():
        pytest.skip("no tests/tools subdirectory in this checkout")

    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(subdirectory), "-q", "-p", "no:cacheprovider"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        env={key: value for key, value in os.environ.items() if key != "STRIDE_TEST_FLOOR"},
    )

    assert "FELL BELOW THE FLOOR" not in result.stdout, (
        f"a subdirectory run was failed by the floor:\n{result.stdout[-1500:]}"
    )
    assert result.returncode == 0, f"the subdirectory run should pass\n{result.stdout[-1500:]}"


def test_the_guards_idea_of_the_environment_matches_what_a_shell_delivers() -> None:
    """Differential: the guard's computed environment vs. what a shell ACTUALLY hands over.

    The decoy lists elsewhere pin spellings, and spellings grew one entry per review
    round -- `echo`, a repeated assignment, `env`, `exec env`, `env -u`. This pins the
    RULE instead: for each candidate line, run it with a shim standing in for the
    interpreter, see what STRIDE_TEST_FLOOR is really delivered, and require the guard's
    own computation to agree.

    It exercises _parse_hook_line rather than the whole-hook entry point, and that is
    deliberate. The first version went through check_the_gate_still_demands_enforcement,
    where the toolchain pin refuses any command outside the venv -- so every shim line
    was rejected before the delivered value was ever consulted and the assertion was
    vacuous. A review proved it by reverting the round-3 CRITICAL this test exists to
    close and finding it still green. Testing the property directly is what makes it
    able to fail.
    """
    checker_path = REPOSITORY_ROOT / "scripts" / "check_test_floor.py"
    spec = importlib.util.spec_from_file_location("_check_test_floor_diff", checker_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    templates = [
        "STRIDE_TEST_FLOOR=enforce {py}",
        "STRIDE_TEST_FLOOR=off STRIDE_TEST_FLOOR=enforce {py}",
        "STRIDE_TEST_FLOOR=enforce STRIDE_TEST_FLOOR=off {py}",
        "STRIDE_TEST_FLOOR=enforced {py}",
        "STRIDE_TEST_FLOOR= {py}",
        "{py}",
    ]

    with tempfile.TemporaryDirectory() as directory:
        shim = Path(directory) / "python"
        shim.write_text('#!/bin/sh\nprintf "%s" "${STRIDE_TEST_FLOOR-<unset>}"\n', encoding="utf-8")
        shim.chmod(0o755)
        clean = {key: value for key, value in os.environ.items() if key != "STRIDE_TEST_FLOOR"}

        disagreements: list[str] = []
        for template in templates:
            line = template.format(py=shim)
            parsed = module._parse_hook_line(line)
            assert not isinstance(parsed, str), f"the guard refused to parse {line!r}: {parsed}"
            _, effective, _ = parsed
            computed = effective.get("STRIDE_TEST_FLOOR", "<unset>")

            delivered = subprocess.run(
                ["bash", "-c", line], capture_output=True, text=True, env=clean
            ).stdout.strip()

            if computed != delivered:
                disagreements.append(f"{template}: guard computed {computed!r}, shell delivered {delivered!r}")

        assert disagreements == [], (
            "the guard's model of the environment differs from what a shell actually hands the "
            "command; every gap between those two has been a bypass: " + str(disagreements)
        )


def test_the_whole_after_doing_hook_is_an_allow_list_of_permitted_commands() -> None:
    """Not just the gate LINE's shape -- which lines count as the gate, too.

    The shape rules were right and guarded the wrong thing: line SELECTION was still a
    substring match. A review measured it end to end -- a `#`-commented decoy satisfied
    "the hook still runs pytest", while the line that actually ran, spelled `-mpytest`,
    was never examined, and 38 /rpc tests were deselected behind two green hook lines.

    So every executable line of the bash block must be one of a named set: a ruff
    invocation, the one pytest gate line, or the floor checker. Anything else is refused
    whatever it spells, and comment-only lines are discarded rather than accepted.
    """
    checker_path = REPOSITORY_ROOT / "scripts" / "check_test_floor.py"
    spec = importlib.util.spec_from_file_location("_check_test_floor_hook", checker_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    python = str(Path(sys.executable).parent / "python")
    ruff = f"{Path(sys.executable).parent / 'ruff'} check reachy_language_tutor/src"
    gate = f"STRIDE_TEST_FLOOR=enforce {python} -m pytest reachy_language_tutor -q"
    floor = f"{python} scripts/check_test_floor.py"

    def hook(*lines: str) -> str:
        body = "\n".join(lines)
        return f"## after_doing\n\n```bash\n{body}\n```\n"

    permitted = [(hook(ruff, gate, floor), "the real hook")]
    refused = [
        (hook(ruff, f"# {gate}",
              f"STRIDE_TEST_FLOOR=off {python} -mpytest reachy_language_tutor -q -k 'not rpc'",
              floor), "a comment decoy with the real line spelled -mpytest"),
        (hook(ruff, f"STRIDE_TEST_FLOOR=enforce PYTHONPATH=/tmp/x {python} -m pytest reachy_language_tutor -q",
              floor), "PYTHONPATH, which can shadow pytest itself"),
        (hook(ruff, f"STRIDE_TEST_FLOOR=enforce PYTEST_ADDOPTS=--noconftest {python} -m pytest reachy_language_tutor -q",
              floor), "PYTEST_ADDOPTS, which can carry any exclusion"),
        (hook(ruff, gate), "the floor checker's own line deleted"),
        (hook(ruff, gate, floor, floor), "the floor checker duplicated"),
        (hook(ruff, gate, gate, floor), "two pytest lines, so which one gates?"),
        (hook(ruff, floor), "no pytest line at all"),
        (hook(ruff, gate, floor, f"{python} -c 'pass'"), "an extra unrecognised command"),
        # The interpreter is pinned to the hook's own toolchain, not merely named
        # "python". A basename check accepts any executable called python at any path,
        # and a stub there reports a full run having executed nothing -- the same class
        # as the PYTHONPATH hole, with the shadow moved from the environment to the path.
        (hook(ruff, "STRIDE_TEST_FLOOR=enforce /tmp/fakebin/python -m pytest reachy_language_tutor -q",
              floor), "an interpreter planted outside the hook's own toolchain"),
        # Only the first fence is parsed, so a second one would run unexamined.
        (hook(ruff, gate, floor) + "\n```bash\necho second\n```\n", "a second bash block"),
    ]

    for content, label in permitted:
        stride_md = _write_temporary_stride_md(content)
        assert module.check_the_gate_still_demands_enforcement(stride_md) is None, (
            f"the guard refuses its own hook: {label}"
        )

    for content, label in refused:
        stride_md = _write_temporary_stride_md(content)
        assert module.check_the_gate_still_demands_enforcement(stride_md) is not None, (
            f"this hook would run the suite with tests dropped and was accepted: {label}"
        )


def _write_temporary_stride_md(content: str) -> Path:
    """Write a throwaway .stride.md and return its path, kept alive for the test."""
    directory = tempfile.mkdtemp()
    path = Path(directory) / ".stride.md"
    path.write_text(content, encoding="utf-8")
    return path
