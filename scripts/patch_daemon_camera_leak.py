#!/usr/bin/env python3
"""Keep the camera IPC socket alive while the daemon's WebRTC branch is deadlocked.

WHAT IS BROKEN (upstream: pollen-robotics/reachy_mini#1416)

`webrtcsink` deadlocks in `on_remote_description_set` during the first WebRTC
session, roughly ten seconds after the daemon starts. That is a bug in
`gst-plugin-webrtc` (Rust) and cannot be fixed from here.

The part that CAN be fixed from here is the collateral damage. In
`reachy_mini/media/media_server.py` the camera IPC branch and the WebRTC branch
hang off the same `tee`, each behind a plain `queue`. A `tee` pushes to its
branches serially, so once the WebRTC branch stops draining, the whole `tee`
blocks -- and the camera IPC socket, which has nothing to do with WebRTC, goes
silent too. One broken feature becomes two.

WHAT THIS DOES

Sets `leaky=downstream` on the WebRTC branch's queue, so that when the branch
wedges the queue drops old buffers instead of blocking the `tee`. The dashboard
camera view stays broken -- that needs the upstream fix -- but the camera IPC
socket keeps delivering frames, which is what an on-device app reads.

Measured on a standalone pipeline reproducing this exact topology, over four
seconds after the WebRTC branch wedged: leaky=no delivered 18 frames to the IPC
branch and then starved; leaky=downstream delivered 119, which is full rate at
30fps.

WHICH INSTALL

The daemon runs from the desktop app's OWN managed virtualenv, not from any
project venv. Patching the wrong one changes nothing, so this defaults to the
app's and prints which file it touched.

The app replaces that virtualenv on update, so this patch does not survive one.
Re-run it after updating the desktop app, or when the camera goes quiet again.

Usage:
    python3 scripts/patch_daemon_camera_leak.py            # apply
    python3 scripts/patch_daemon_camera_leak.py --check    # report only
    python3 scripts/patch_daemon_camera_leak.py --revert   # restore the backup
    python3 scripts/patch_daemon_camera_leak.py --path /some/media_server.py
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

DEFAULT_MEDIA_SERVER = Path(
    "~/Library/Application Support/com.pollen-robotics.reachy-mini/.venv"
    "/lib/python3.12/site-packages/reachy_mini/media/media_server.py"
).expanduser()

# The anchor is the two lines that create and add the WebRTC branch queue. Matching
# on both, rather than on the factory call alone, is what stops this patching some
# other queue if the file is reorganised upstream.
ANCHOR = (
    '        queue_webrtc = Gst.ElementFactory.make("queue", "queue_webrtc")\n'
    "        pipeline.add(queue_webrtc)\n"
)

MARKER = "reachy_mini#1416"

INSERTION = (
    "        # PATCHED LOCALLY -- see pollen-robotics/reachy_mini#1416.\n"
    "        # webrtcsink deadlocks during the first session; behind a shared tee a\n"
    "        # non-leaky queue then blocks the tee and takes the camera IPC socket\n"
    "        # down with it. Leaking here contains the damage to WebRTC: the IPC\n"
    "        # branch keeps delivering frames at full rate.\n"
    "        # 2 == GST_QUEUE_LEAK_DOWNSTREAM (drop the oldest buffers).\n"
    '        queue_webrtc.set_property("leaky", 2)\n'
)


def report(path: Path) -> int:
    """Say whether the file exists and whether the patch is already in it."""
    if not path.exists():
        print(f"NOT FOUND: {path}")
        print("The desktop app may not be installed, or its venv may live elsewhere.")
        return 2
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        print(f"ALREADY PATCHED: {path}")
        return 0
    if ANCHOR not in text:
        print(f"ANCHOR NOT FOUND: {path}")
        print("The upstream file has changed shape. Re-read it before patching by hand.")
        return 3
    print(f"UNPATCHED, and the anchor is present: {path}")
    return 1


def apply(path: Path) -> int:
    """Insert the leaky property, keeping a .orig backup beside the file."""
    status = report(path)
    if status != 1:
        return 0 if status == 0 else status

    backup = path.with_suffix(path.suffix + ".orig")
    if not backup.exists():
        shutil.copy2(path, backup)
        print(f"backup written: {backup}")

    text = path.read_text(encoding="utf-8")
    if text.count(ANCHOR) != 1:
        print(f"REFUSING: the anchor appears {text.count(ANCHOR)} times, expected exactly 1")
        return 3
    path.write_text(text.replace(ANCHOR, ANCHOR + INSERTION), encoding="utf-8")

    # Read back rather than trust the write, and check it still parses.
    import ast

    written = path.read_text(encoding="utf-8")
    ast.parse(written)
    assert MARKER in written and 'set_property("leaky", 2)' in written
    print(f"PATCHED: {path}")
    print("\nRestart the Reachy Mini Control app for this to take effect.")
    return 0


def revert(path: Path) -> int:
    """Put the shipped file back."""
    backup = path.with_suffix(path.suffix + ".orig")
    if not backup.exists():
        print(f"no backup at {backup}; nothing to revert")
        return 2
    shutil.copy2(backup, path)
    print(f"REVERTED from {backup}")
    return 0


def main() -> int:
    """Parse arguments and run the requested action."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--path", type=Path, default=DEFAULT_MEDIA_SERVER, help="media_server.py to patch")
    parser.add_argument("--check", action="store_true", help="report only, change nothing")
    parser.add_argument("--revert", action="store_true", help="restore the .orig backup")
    args = parser.parse_args()

    if args.check:
        return 0 if report(args.path) in (0, 1) else report(args.path)
    if args.revert:
        return revert(args.path)
    return apply(args.path)


if __name__ == "__main__":
    sys.exit(main())
