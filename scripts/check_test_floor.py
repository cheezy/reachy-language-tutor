"""Fail if the test suite collects fewer tests than the floor recorded in .stride.md.

WHY THIS IS A SCRIPT AND NOT A TEST. D21 established that a test file on disk must never
silently stop being collected, and tests/test_pytest_configuration.py enforces that by
comparing the collected set against the filesystem. But that guard shells out to its own
pytest subprocess with NO arguments, so it validates a clean default invocation rather
than the one actually running -- measured, before D27: running the suite with
`--ignore-glob='*test_rpc_control_surface.py'` dropped 38 tests and still reported green,
with the guard itself passing inside that very run.

A guard that lives inside the suite can be excluded along with it. This one lives outside,
is invoked by the after_doing hook in .stride.md, and answers a question the in-suite
guard structurally cannot: did a full collection produce at least as many tests as last
time somebody looked?

THE FLOOR IS A MINIMUM, NOT AN EQUALITY. Adding tests can never fail this. Removing one
deliberately means lowering the number in .stride.md, which is a visible line in a diff --
and that visibility is the whole point, because the failure being prevented is a SILENT
drop.

The number lives in exactly one place: .stride.md. Not here, not in pyproject, not in the
suite. Two copies of a count is how the two drift and the check quietly stops meaning
anything.
"""

from __future__ import annotations
import os
import re
import sys
import shlex
import subprocess
from pathlib import Path


FLOOR_PATTERN = re.compile(r"<!--\s*MINIMUM_COLLECTED_TESTS:\s*(\d+)\s*-->")


def read_floor(stride_md: Path) -> int | None:
    """Return the floor recorded in .stride.md, or None if it is not there.

    Counts only. This reads a single integer out of an HTML comment and nothing else --
    .stride.md is a committed file and must never become a place that carries test
    content, paths, or anything resembling a secret.
    """
    if not stride_md.is_file():
        return None
    matches = FLOOR_PATTERN.findall(stride_md.read_text(encoding="utf-8"))
    if len(matches) != 1:
        return None
    return int(matches[0])


def count_collected(package_directory: Path) -> int:
    """Ask pytest how many tests a full collection finds, from the package directory.

    No path argument, deliberately, for the reason test_pytest_configuration.py gives:
    a path on the command line overrides testpaths entirely, so passing one would hide a
    narrowed testpaths from this check as well.
    """
    # The floor is switched off for this nested run: this collection is how the floor is
    # MEASURED, so letting it inherit the floor would make it fail on the very breach it
    # exists to report, and the message would be the conftest hook's rather than this
    # script's clearer one.
    environment = {**os.environ, "STRIDE_TEST_FLOOR": "off"}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=package_directory,
        capture_output=True,
        text=True,
        env=environment,
    )
    if result.returncode not in (0, 5):
        print(
            f"collection failed ({result.returncode}):\n{result.stdout[-2000:]}\n{result.stderr[-1000:]}"
        )
        raise SystemExit(2)
    total = 0
    for line in result.stdout.splitlines():
        match = re.match(r"(\d+) tests? collected", line.strip())
        if match:
            total = int(match.group(1))
    if total == 0:
        total = sum(1 for line in result.stdout.splitlines() if "::" in line)
    return total


# The permitted SHAPE of the gate's pytest line: zero or more NAME=VALUE assignments,
# then the command. An allow-list of structure rather than of spelling, because three
# decoys defeated a check that only asked whether the token appeared somewhere on the
# line -- `echo STRIDE_TEST_FLOOR=enforce && python -m pytest ... -k 'not rpc'`,
# the same with `true`, and the token parked in a trailing `# comment`. Each satisfies
# a token search and sets nothing.
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# Shell operators that would make the LINE's exit status something other than pytest's.
_CONTROL_OPERATORS = ("&&", "||", ";", "|", "&", ">", "<", "`", "$(")

# What the gate is permitted to pass pytest. `-q` and the package directory are all it
# needs today; a new flag is a deliberate, visible edit here rather than a silent
# capability. Every previous version of this guard validated the ENVIRONMENT and left
# the arguments entirely unexamined, which is how `--noconftest` unloaded the in-suite
# half of the fix and `--collect-only` satisfied the floor while running nothing at all.
_PERMITTED_PYTEST_ARGUMENTS = frozenset({"-m", "pytest", "-q", "reachy_language_tutor"})

# The assignment NAMES the gate line may carry. Naming only the permitted VALUE of one
# variable left the set of names unbounded, which reopened the wrapper class the
# interpreter rule closed: an assignment reconfigures the interpreter as effectively as
# `env` does. Measured -- `STRIDE_TEST_FLOOR=enforce PYTHONPATH=<dir> python -m pytest ...`
# with a stub package named pytest on that path printed "1515 passed", ran nothing, and
# the checker accepted the line. PYTEST_ADDOPTS is the same hole.
_PERMITTED_ASSIGNMENTS = frozenset({"STRIDE_TEST_FLOOR"})


def check_the_gate_still_demands_enforcement(stride_md: Path) -> str | None:
    """Return a complaint if the after_doing hook stopped demanding the floor, else None.

    This lives OUT HERE, not in the suite, and that placement is load-bearing: an
    in-suite assertion can always be deselected by the very invocation it polices.

    EVERY executable line of the hook is checked against EVERY rule. Earlier versions
    applied the rules only to the line they had identified as the pytest line, which
    meant a line classified as ruff or as the floor checker escaped all of them -- and a
    review measured `ruff check src ; rm <a test file>` being accepted, needing nothing
    planted. The rules are uniform now because a rule that applies to one line is not a
    property of the hook.
    """
    if not stride_md.is_file():
        return None
    if stride_md.read_text(encoding="utf-8").count("## after_doing") > 1:
        return "there is more than one '## after_doing' section; this guard reads the first"

    _, _, after_doing = stride_md.read_text(encoding="utf-8").partition(
        "## after_doing"
    )
    after_doing, _, _ = after_doing.partition("\n## ")
    if not after_doing:
        return "the ## after_doing section has moved or gone from .stride.md"

    # Any fence, tagged or not. Counting only ```bash left an untagged second fence
    # unexamined, and whether the runner executes it is not this guard's call to make.
    if after_doing.count("```") > 2:
        return (
            "the after_doing hook has more than one fenced block; this guard reads the first, "
            "so lines in any other block would run unexamined."
        )
    _, _, fenced = after_doing.partition("```")
    block, _, _ = fenced.partition("```")
    block = block.partition("\n")[2] if block.startswith("bash") else block
    if not block.strip():
        return "the after_doing hook has no executable block"

    executable = [
        line
        for line in block.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not executable:
        return "the after_doing hook executes nothing"

    parsed: list[tuple[str, list[str], dict[str, str], int]] = []
    for line in executable:
        complaint_or_parse = _parse_hook_line(line)
        if isinstance(complaint_or_parse, str):
            return complaint_or_parse
        parsed.append((line, *complaint_or_parse))

    # The directory the hook's own tooling lives in, derived from the block rather than
    # hardcoded. FAIL CLOSED when it cannot be determined: an earlier version guarded
    # the pin with `and toolchains`, so a hook with no ruff line skipped it entirely and
    # accepted a planted interpreter -- the residual was one deletion away, not closed.
    # Anchored to the interpreter running THIS script, not merely to itself. Requiring
    # the hook's commands to agree with each other is not a pin: a hook whose every line
    # points at /tmp/fakebin is perfectly self-consistent, and a review measured exactly
    # that accepted, printing a full pass from a stub that ran nothing. In the real hook
    # this script is invoked by the venv's python, so sys.executable IS the toolchain,
    # and a hook pointing anywhere else fails closed.
    # NOT resolve(): a venv's bin/python is a symlink to the interpreter it was built
    # from, and resolving would compare the venv against uv's cpython and always differ.
    # The venv directory is the thing being pinned.
    expected = str(Path(sys.executable).parent)
    # $HOME and ~ are written literally in .stride.md and shlex does not expand them,
    # so expand here -- otherwise the real hook fails its own pin.
    wrong = sorted(
        {
            str(Path(os.path.expanduser(os.path.expandvars(tokens[index]))).parent)
            for _, tokens, _, index in parsed
        }
        - {expected}
    )
    if wrong:
        return (
            f"the hook runs commands from {wrong}, but this checker is running from {expected!r}.\n"
            "Every command must come from the same toolchain as the gate itself, or a stub planted "
            "elsewhere can stand in for the interpreter and report a full run having executed nothing."
        )

    pytest_lines = 0
    floor_lines = 0
    for line, tokens, effective, index in parsed:
        command = Path(tokens[index]).name
        rest = tokens[index + 1 :]
        if command.startswith("ruff"):
            continue
        if not command.startswith("python"):
            return (
                f"the hook runs a command this gate does not recognise: {command!r} in "
                f"{line.strip()!r}\nEvery executable line must be ruff, the pytest gate line, or "
                "scripts/check_test_floor.py."
            )
        if any(token.endswith("check_test_floor.py") for token in rest):
            floor_lines += 1
            continue

        pytest_lines += 1
        unexpected = [
            token for token in rest if token not in _PERMITTED_PYTEST_ARGUMENTS
        ]
        if unexpected:
            return (
                f"the gate's pytest line passes arguments this gate does not permit: {unexpected}\n"
                f"{line.strip()!r}\n"
                f"Every argument must be one of {sorted(_PERMITTED_PYTEST_ARGUMENTS)}. --noconftest "
                "unloads the in-suite half of this guard, --collect-only satisfies the floor while "
                "running nothing at all, and --ignore-glob / -k / --deselect drop tests outright."
            )
        if effective.get("STRIDE_TEST_FLOOR") != "enforce":
            return (
                "the gate's pytest invocation no longer demands the collection floor: "
                f"{line.strip()!r}\n"
                "STRIDE_TEST_FLOOR=enforce must be the EFFECTIVE value of the assignment prefix on "
                "the pytest command itself."
            )

    if pytest_lines != 1:
        return (
            f"the after_doing hook has {pytest_lines} pytest line(s); it must have exactly one, "
            "so there is one invocation whose exit status gates completion."
        )
    if floor_lines != 1:
        return (
            f"the after_doing hook has {floor_lines} check_test_floor.py line(s); it must have "
            "exactly one. Deleting it removes the half of this guard that survives an exclusion "
            "aimed at the suite, which is the whole reason it lives outside the suite."
        )
    return None


def _parse_hook_line(line: str) -> "str | tuple[list[str], dict[str, str], int]":
    """Parse one executable hook line, or return a complaint.

    Applies the rules that must hold of EVERY line: no shell control operator, and no
    assignment outside the permitted set. Returns the tokens, the effective environment
    and the index of the command, so the caller can apply per-command rules on top.
    """
    body = line.split("#", 1)[0]
    present = [operator for operator in _CONTROL_OPERATORS if operator in body]
    if present:
        return (
            f"a hook line carries shell operator(s) {present}: {line.strip()!r}\n"
            "Every line must be a single command, so its exit status is the command's. A trailing "
            "'; true' discards a failure, and '; rm ...' runs something this guard never sees."
        )
    try:
        tokens = shlex.split(body)
    except ValueError:
        return f"a hook line cannot be parsed as a command: {line.strip()!r}"
    if not tokens:
        return f"a hook line parses to nothing: {line.strip()!r}"

    effective: dict[str, str] = {}
    index = 0
    while index < len(tokens) and _ASSIGNMENT.match(tokens[index]):
        name, _, value = tokens[index].partition("=")
        if name not in _PERMITTED_ASSIGNMENTS:
            return (
                f"a hook line sets {name!r}, which it is not permitted to set: {line.strip()!r}\n"
                f"Only {sorted(_PERMITTED_ASSIGNMENTS)} may be assigned. PYTHONPATH can shadow "
                "pytest itself and PYTEST_ADDOPTS can carry any exclusion."
            )
        effective[name] = value
        index += 1
    if index >= len(tokens):
        return f"a hook line is only assignments and runs nothing: {line.strip()!r}"
    return tokens, effective, index


def main() -> int:
    """Compare a full collection against the recorded floor."""
    repository_root = Path(__file__).resolve().parent.parent
    stride_md = repository_root / ".stride.md"
    package_directory = repository_root / "reachy_language_tutor"

    complaint = check_the_gate_still_demands_enforcement(stride_md)
    if complaint is not None:
        print(complaint)
        return 1

    floor = read_floor(stride_md)
    if floor is None:
        print(
            "No MINIMUM_COLLECTED_TESTS floor found in .stride.md (or more than one).\n"
            "That marker is what stops a test file being dropped silently -- see D27.\n"
            "Restore a single line of the form:  <!-- MINIMUM_COLLECTED_TESTS: 1234 -->"
        )
        return 1

    collected = count_collected(package_directory)
    if collected < floor:
        print(
            f"TEST COLLECTION FELL BELOW THE FLOOR: {collected} collected, floor is {floor}.\n"
            f"{floor - collected} test(s) stopped being collected. That is D21's failure -- a file "
            "on disk that can no longer fail.\n"
            "If tests were removed deliberately, lower the floor in .stride.md in the same change, "
            "so the drop is visible in the diff."
        )
        return 1

    print(f"test collection floor OK: {collected} collected, floor {floor}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
