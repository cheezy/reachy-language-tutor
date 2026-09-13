#!/usr/bin/env python3
"""Record what the Python environment and robot service look like, and diff two records.

WHY THIS EXISTS

Installing `reachy_mini_testbench` is worth doing -- it is an independent consumer of
the camera IPC socket, which makes it a real test of the leaky=downstream mitigation for
pollen-robotics/reachy_mini#1416 -- but its pyproject declares two things that can move
this environment underneath us:

  * `reachy-mini`, completely UNPINNED. This app pins >=1.10.0rc5 and the SDK has to
    match the desktop app's robot service; docs/SETUP.md records that an app install can
    silently provision a different one into a managed venv.
  * `opencv-python`, NOT the headless build. Both distributions provide `cv2`, so having
    both installed lets two of them fight over one import -- which is why this project's
    own pyproject.toml pins opencv-python-headless and explains at length that the
    distinction is load-bearing.

So: snapshot before, install, snapshot after, diff. If either moved, you know
immediately rather than three days later when something behaves oddly.

WHAT IT DOES AND DOES NOT SHOW

It records versions and which file `cv2` actually resolves to. It does NOT verify that
the robot service and the SDK are *compatible* -- only that they did or did not change.
A clean diff means nothing moved, not that everything is fine.

    python3 scripts/testbench_env_snapshot.py before.json
    # ... install the testbench ...
    python3 scripts/testbench_env_snapshot.py after.json
    python3 scripts/testbench_env_snapshot.py --diff before.json after.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# The distributions that can collide or drift. opencv-python and its headless twin are
# both here on purpose: the interesting state is which ones are present TOGETHER.
_WATCHED = (
    "reachy-mini",
    "reachy-mini-dances-library",
    "opencv-python",
    "opencv-python-headless",
    "cv2-enumerate-cameras",
    "sounddevice",
    "soundfile",
    "scipy",
    "psutil",
    "aiohttp",
    "numpy",
)

# Robot-service fields worth comparing. A selected list rather than the whole payload,
# which carries live state (temperatures, poses) that differs on every call and would
# make every diff noisy.
_DAEMON_FIELDS = (
    "robot_name",
    "state",
    "wireless_version",
    "desktop_app_daemon",
    "simulation_enabled",
    "mockup_sim_enabled",
    "no_media",
    "media_released",
    "camera_specs_name",
)

_DAEMON_STATUS_URL = "http://localhost:8000/api/daemon/status"


def _normalize(name: str) -> str:
    """PEP 503 name normalisation: `reachy_mini` and `reachy-mini` are one package."""
    return re.sub(r"[-_.]+", "-", name).lower()


# Variables that make a spawned interpreter report somebody else's packages. This is
# not hypothetical: the dev environment's python sets PYTHONPATH to its OWN
# site-packages, subprocess inherits it, and an apps_venv interpreter launched from
# there loaded the DEV env's pip and listed the DEV env's packages -- while reporting
# sys.prefix as apps_venv, so it looked right. The first version of this script
# confidently described the wrong environment. `env` in a shell does not show it,
# because the shell does not have it; only the python process does.
_ENV_THAT_LEAKS = ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "VIRTUAL_ENV_PROMPT", "PYTHONSTARTUP")


def _clean_env() -> dict[str, str]:
    """The current environment minus anything that redirects a child's import machinery."""
    return {k: v for k, v in os.environ.items() if k not in _ENV_THAT_LEAKS}


def _probe(python: str, code: str) -> Any:
    """Run a snippet IN the target interpreter and parse its JSON, with the env cleaned."""
    try:
        out = subprocess.run(
            [python, "-c", code],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
            env=_clean_env(),
        ).stdout
        return json.loads(out)
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        return {"__error__": type(exc).__name__}


def _installed_versions(python: str) -> dict[str, str]:
    """What the TARGET interpreter itself can see, for the distributions worth watching.

    importlib.metadata rather than `pip list`, because `-m pip` resolves through the
    inherited path and can load a different environment's pip entirely. This reads the
    target's own metadata through its own sys.path, so the answer belongs to the
    interpreter being named.
    """
    code = (
        "import json;from importlib import metadata;"
        "print(json.dumps({(d.metadata['Name'] or ''): d.version for d in metadata.distributions()}))"
    )
    found = _probe(python, code)
    if "__error__" in found:
        return {"__error__": found["__error__"]}

    # Normalise per PEP 503 before comparing: this SDK is distributed as "reachy_mini"
    # while its own pyproject asks for "reachy-mini", and a plain lowercase match
    # reported the installed package as absent -- measured, on the first run.
    installed = {_normalize(name): version for name, version in found.items() if name}
    return {name: installed.get(_normalize(name), "(absent)") for name in _WATCHED}


def _cv2_identity(python: str) -> dict[str, str]:
    """Which cv2 actually wins the import, and where it lives.

    The version alone does not answer this: opencv-python and opencv-python-headless
    ship the SAME module name, so with both installed the answer is whichever landed in
    site-packages last. The file path is what distinguishes them.
    """
    code = (
        "import json\n"
        "try:\n"
        "    import cv2\n"
        "    print(json.dumps({'version': cv2.__version__, 'file': cv2.__file__}))\n"
        "except Exception as exc:\n"
        "    print(json.dumps({'version': '(absent)', 'file': type(exc).__name__}))\n"
    )
    return _probe(python, code)


def _daemon_status() -> dict[str, Any]:
    """The robot service's own account of itself, or why it could not be reached."""
    try:
        with urllib.request.urlopen(_DAEMON_STATUS_URL, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        return {"__unreachable__": type(exc).__name__}
    return {field: payload.get(field) for field in _DAEMON_FIELDS}


def snapshot(python: str) -> dict[str, Any]:
    """Everything worth comparing, in one object.

    `prefix` is recorded and checked rather than assumed: a snapshot that names one
    environment and describes another is worse than no snapshot, and that is exactly
    what this script did before _ENV_THAT_LEAKS existed.
    """
    installed = _installed_versions(python)
    prefix = _probe(python, "import sys,json;print(json.dumps({'prefix':sys.prefix}))")
    return {
        "python": python,
        "prefix": prefix.get("prefix", prefix.get("__error__")),
        "sdk_version": installed.get("reachy-mini"),
        "installed": installed,
        "cv2": _cv2_identity(python),
        "daemon": _daemon_status(),
    }


def _flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    if isinstance(obj, dict):
        for key, value in obj.items():
            flat.update(_flatten(value, f"{prefix}.{key}" if prefix else str(key)))
    else:
        flat[prefix] = obj
    return flat


def diff(before_path: Path, after_path: Path) -> int:
    """Print what moved. Exit non-zero if anything did, so CI or a shell can branch."""
    before = _flatten(json.loads(before_path.read_text(encoding="utf-8")))
    after = _flatten(json.loads(after_path.read_text(encoding="utf-8")))

    changed = [key for key in sorted(before | after) if before.get(key) != after.get(key)]
    if not changed:
        print("No change. Nothing in the watched set moved.")
        print("(That is not the same as 'everything is fine' -- see this file's docstring.)")
        return 0

    print(f"{len(changed)} value(s) CHANGED:\n")
    for key in changed:
        print(f"  {key}")
        print(f"      before: {before.get(key, '(absent)')}")
        print(f"      after:  {after.get(key, '(absent)')}")

    # The two that were predicted, called out by name so they are not lost in a list.
    if any(key.endswith("installed.reachy-mini") for key in changed):
        print("\n  !! reachy-mini moved. It must match the desktop app's robot service.")
    if any("opencv-python" in key for key in changed) or any("cv2." in key for key in changed):
        print("\n  !! the cv2 provider moved. Two distributions supply this import;")
        print("     see reachy_language_tutor/pyproject.toml for why headless is the one wanted.")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("output", nargs="?", type=Path, help="where to write the snapshot")
    parser.add_argument("--diff", nargs=2, type=Path, metavar=("BEFORE", "AFTER"))
    parser.add_argument(
        "--python",
        default=str(Path(sys.executable)),
        help="interpreter to inspect (default: the one running this script)",
    )
    args = parser.parse_args()

    if args.diff:
        return diff(*args.diff)
    if args.output is None:
        parser.error("give an output path, or --diff BEFORE AFTER")

    record = snapshot(args.python)
    args.output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"snapshot written: {args.output}")
    print(f"  reachy-mini            {record['installed'].get('reachy-mini')}")
    print(f"  opencv-python          {record['installed'].get('opencv-python')}")
    print(f"  opencv-python-headless {record['installed'].get('opencv-python-headless')}")
    print(f"  cv2 resolves to        {record['cv2'].get('file')}")
    print(f"  (environment inspected: {record['prefix']})")
    print(f"  daemon                 {record['daemon']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
