"""Does leaky=downstream on one tee branch keep the other branch alive?

Miniature of reachy_mini's media_server topology. Touches no camera:
videotestsrc is a synthetic pattern generator.

    videotestsrc ! tee -+- queue_webrtc ! fakesink   <- wedges mid-stream, standing
                        |                               in for the deadlocked webrtcsink
                        +- queue_ipc    ! fakesink   <- the camera IPC branch  

The webrtc branch runs normally for a while and THEN wedges, which is what the
real bug does (the deadlock happens during the first WebRTC session, ~10s in).
Blocking the very first buffer instead would stall preroll and prove nothing --
the pipeline would never reach PLAYING and both branches would stop for that
reason rather than because of the tee.
"""

import threading
import time

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst

Gst.init(None)

LET_THROUGH = 30  # ~1s at 30fps: long enough to reach PLAYING and settle


def run(leaky: str, seconds: float = 4.0) -> tuple[int, int]:
    pipeline = Gst.parse_launch(
        "videotestsrc is-live=true ! video/x-raw,width=320,height=240,framerate=30/1 "
        "! tee name=t "
        f"t. ! queue name=q_webrtc leaky={leaky} ! fakesink name=sink_webrtc sync=false "
        "t. ! queue name=q_ipc ! fakesink name=sink_ipc sync=false"
    )

    seen = {"webrtc": 0, "ipc": 0}
    wedged = threading.Event()
    release = threading.Event()

    def wedge(pad, info):
        seen["webrtc"] += 1
        if seen["webrtc"] <= LET_THROUGH:
            return Gst.PadProbeReturn.OK
        wedged.set()
        release.wait(timeout=30)  # bounded so teardown can complete
        return Gst.PadProbeReturn.OK

    def count(pad, info):
        seen["ipc"] += 1
        return Gst.PadProbeReturn.OK

    pipeline.get_by_name("sink_webrtc").get_static_pad("sink").add_probe(Gst.PadProbeType.BUFFER, wedge)
    pipeline.get_by_name("sink_ipc").get_static_pad("sink").add_probe(Gst.PadProbeType.BUFFER, count)

    pipeline.set_state(Gst.State.PLAYING)
    if pipeline.get_state(5 * Gst.SECOND)[0] != Gst.StateChangeReturn.SUCCESS:
        raise SystemExit("pipeline never reached PLAYING; the harness proves nothing")
    if not wedged.wait(timeout=10):
        raise SystemExit("the webrtc branch never wedged; the harness proves nothing")

    before = seen["ipc"]
    time.sleep(seconds)
    delivered = seen["ipc"] - before

    release.set()
    pipeline.set_state(Gst.State.NULL)
    return delivered, seen["webrtc"]


WINDOW = 4.0
EXPECTED = int(WINDOW * 30)  # the source runs at 30fps, so this is what a healthy branch delivers

print(f"(webrtc branch runs normally for {LET_THROUGH} buffers, then wedges for good)")
print(f"(a branch keeping up delivers about {EXPECTED} frames in {WINDOW:.0f}s)\n")
for setting in ("no", "downstream"):
    ipc, _ = run(setting, seconds=WINDOW)
    # A branch that delivers a handful of frames and then stops is NOT alive: that is
    # the queue's slack draining. Judge against the rate, not against zero.
    verdict = "keeps up" if ipc >= EXPECTED * 0.8 else "STALLS (queue slack only, then nothing)"
    print(
        f"leaky={setting:<11} frames reaching the IPC branch in {WINDOW:.0f}s AFTER the wedge: "
        f"{ipc:>4} of ~{EXPECTED}   {verdict}"
    )
