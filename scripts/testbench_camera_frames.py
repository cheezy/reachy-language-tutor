#!/usr/bin/env python3
"""Count frames the testbench actually delivers, so patched-vs-unpatched is a number.

WHY THIS IS THE RIGHT CONSUMER TO MEASURE

`reachy_mini_testbench` reaches the camera through `reachy_mini.media.get_frame()`.
Traced through the installed SDK: `MediaBackend.DEFAULT` is an alias for `LOCAL`, LOCAL
calls `_init_camera` ("LOCAL IPC reader"), and that builds a GStreamer pipeline with
`unixfdsrc` on `CAMERA_SOCKET_PATH`. That is the camera IPC socket -- the same branch of
the daemon's `tee` that goes silent when `queue_webrtc` wedges, and the same one
`leaky=downstream` keeps alive (see scripts/patch_daemon_camera_leak.py).

So this measures a real consumer written by somebody else, which is a stronger claim
than the synthetic topology in scripts/tee_leak_proof.py.

WHY COUNTING MATTERS MORE THAN LOOKING

The testbench's `/api/camera/stream` handler is:

    while True:
        frame = ...get_frame()
        if frame is not None:
            yield <jpeg part>
        time.sleep(0.033)

When the camera is starved it yields NOTHING and keeps looping. The HTTP response stays
open and simply produces no bytes -- so a wedged camera looks like a slow stream, not an
error, and a person watching a blank panel cannot tell "broken" from "still loading".
The SDK compounds this: on the LOCAL path a camera that fails to initialise is caught
and logged ("Camera init failed, continuing without camera"), so nothing raises.

A count over a fixed window separates those. Judge against the RATE, never against zero:
a handful of frames is a queue draining its slack, not a working camera.

    python3 scripts/testbench_camera_frames.py --seconds 8

WHAT THIS DOES NOT SHOW

Nothing about WebRTC. The dashboard camera view is a different branch of the same `tee`
and stays broken with the mitigation applied; this only ever speaks for the IPC half.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request

# The generator sleeps 0.033s per iteration, so the stream cannot exceed ~30 fps however
# fast the camera is. A branch that is keeping up sits near the camera's own rate.
_HANDLER_CEILING_FPS = 30.0

# The verdict is RELATIVE to a measured healthy baseline, not to a number picked in
# advance. Measured on this machine, a healthy mockup-sim stream runs at 3.2-3.5 fps --
# far below the handler's 30 fps ceiling, because the rate is set by the source and the
# JPEG encode, not by the loop. A hardcoded floor of 3.0 would have sat 13% under that,
# so a healthy run and a degraded one would differ by less than the noise, and the
# verdict would flip on which sample you happened to take.
#
# So: pass --baseline with the healthy figure, and judge against a fraction of it. The
# same discipline as scripts/tee_leak_proof.py, which judges against the source rate
# rather than against zero.
_DELIVERING_FRACTION = 0.5  # at least half the baseline is still plainly alive
_STALLED_FRACTION = 0.1  # below a tenth is slack draining, not a camera

# One complete multipart part header, as the handler writes it. Matching the whole header
# rather than the bare "--frame" boundary, because two bytes of JPEG payload could
# coincide with a short boundary and inflate the count.
_PART_HEADER = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"


def probe_capture(base: str, timeout: float = 8.0) -> str:
    """Ask for a single frame first: a fast, unambiguous yes/no before the timed run."""
    url = f"{base}/api/camera/capture"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        size = len(payload.get("image", ""))
        return f"one frame returned ({size} base64 chars)"
    except urllib.error.HTTPError as exc:
        # 503 is the handler's own "no camera frame available after timeout" -- the
        # starved case, stated plainly rather than inferred from silence.
        return f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:120]}"
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return f"{type(exc).__name__} (is the testbench running on this port?)"


def count_frames(base: str, seconds: float) -> tuple[int, float, bool]:
    """Read the MJPEG stream for `seconds` and count complete parts.

    Returns (frames, elapsed, connected). `connected` matters: a stream that never
    opened is a testbench that is not running, which is a DIFFERENT fact from a stream
    that opened and delivered nothing. Reporting both as "no frames" would make the
    measurement unable to distinguish "not started" from the symptom it exists to find
    -- and a verdict that means something other than what it says is the bug this whole
    exercise has been about.
    """
    url = f"{base}/api/camera/stream"
    frames = 0
    tail = b""
    connected = False
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=max(5.0, seconds + 5.0)) as response:
            connected = True
            started = time.perf_counter()
            while time.perf_counter() - started < seconds:
                chunk = response.read(65536)
                if not chunk:
                    break
                buffer = tail + chunk
                frames += buffer.count(_PART_HEADER)
                # Keep enough tail that a header split across two reads is not lost.
                tail = buffer[-len(_PART_HEADER) :]
    except urllib.error.HTTPError as exc:
        # The handler's own 503 -- it answered, so it IS running.
        connected = True
        print(f"  stream refused: HTTP {exc.code} {exc.read().decode('utf-8', 'replace')[:120]}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # Once connected, a timeout with zero frames IS the symptom. Before connecting,
        # it just means nothing is listening.
        print(f"  stream ended: {type(exc).__name__}")
    return frames, time.perf_counter() - started, connected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="http://localhost:8042", help="testbench base URL")
    parser.add_argument("--seconds", type=float, default=8.0, help="how long to count for")
    parser.add_argument("--label", default="", help="a note for the output, e.g. 'unpatched'")
    parser.add_argument(
        "--baseline",
        type=float,
        default=0.0,
        help="healthy fps to judge against, from a known-good run (without it, no verdict is given)",
    )
    args = parser.parse_args()

    if args.label:
        print(f"=== {args.label} ===")
    print(f"single-frame probe: {probe_capture(args.base)}")

    print(f"counting frames from {args.base}/api/camera/stream for {args.seconds:.0f}s ...")
    frames, elapsed, connected = count_frames(args.base, args.seconds)
    fps = frames / elapsed if elapsed > 0 else 0.0

    if not connected:
        print("\n  NOT MEASURED: nothing answered on that port, so the testbench is not running.")
        print("  This says nothing about the camera. Start the testbench and run again.")
        return 2

    if frames == 0:
        verdict = "SILENT: the stream opened and delivered nothing, which is the symptom"
    elif args.baseline <= 0:
        verdict = f"{fps:.1f} fps, no --baseline given, so no verdict -- measure a healthy run first"
    elif fps >= args.baseline * _DELIVERING_FRACTION:
        verdict = f"DELIVERING ({fps / args.baseline:.0%} of the {args.baseline:.1f} fps baseline)"
    elif fps <= args.baseline * _STALLED_FRACTION:
        verdict = f"STALLED ({fps / args.baseline:.0%} of baseline -- slack draining, not a camera)"
    else:
        verdict = f"DEGRADED ({fps / args.baseline:.0%} of baseline -- neither healthy nor wedged)"

    print(
        f"\n  {frames} frames in {elapsed:.1f}s = {fps:.1f} fps "
        f"(handler ceiling ~{_HANDLER_CEILING_FPS:.0f} fps)\n  {verdict}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
