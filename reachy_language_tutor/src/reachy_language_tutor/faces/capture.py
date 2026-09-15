"""Ask for one frame, right now, and get one clear answer back.

Recognition needs a frame at a moment -- when somebody arrives, when enrolment asks
for one. It does not need a stream. So this module has no thread, no timer, no queue
and no loop that runs when nobody is asking: `capture_frame` does its work inside the
call a caller made, and nothing it starts outlives the return. That is also why the
retry budget below is counted in ATTEMPTS rather than seconds -- `time` is not
imported, so "no timer" is a property of the file rather than a promise in a comment.

WHO OWNS THE CAMERA. Not this module. It opens nothing and closes nothing; it borrows
a media handle somebody else already has. The daemon owns the physical camera, this
app reaches it through a `ReachyMini` built in `main.py` (or handed in by the daemon),
and whoever built it releases it in their own shutdown `finally`, where
`robot.media.close()` is called. There are two such owners now rather than one --
`run` for the app, and the enrolment command, which builds its own robot because it
is invoked from a shell and not from the running app -- and a test derives that set
from `main.py` rather than counting it, so a third owner is held to the same rule
without this sentence needing to be right about the number. Closing it from here
would be actively wrong
rather than merely out of scope: `MediaManager.close()` closes the audio device too,
and that audio device is the tutor's voice -- a frame grab that muted the lesson would
be a strange way to recognise somebody.

WHY A FRAME CAN BE MISSING WHEN THE CAMERA IS FINE. `get_frame()` waits up to 20ms for
the next sample; the camera runs at 30fps, so a new frame arrives every ~33ms. A read
issued straight after a successful one therefore misses BY CONSTRUCTION and answers
`None`. Measured against the desktop app's mockup simulation on 2026-09-14: 3 of 10
back-to-back reads returned a frame, while 6 of 6 spaced 100ms apart did, and 18 of 18
across spacings of 100ms or more.
So a bare `None` from the SDK means one of three different things, and only one of
them is "there is no camera". Telling them apart is this module's whole job -- a
caller told "no camera" because it asked twice quickly would go looking for hardware
that is sitting there working. `_FRAME_ATTEMPTS` covers one frame interval about three
times over, which costs a caller at most ~100ms and only when no frame is arriving
at all.

A `no_frame` answer that persists is not something to fix here. It means the daemon's
pipeline has wedged -- docs/SETUP.md records that failure and the rule that nothing in
this repository is on that path.

NOTHING HERE WRITES A FRAME ANYWHERE. Not to disk, not to a log, not into an error
message, not temporarily while debugging. `FrameCapture` carries its own `__repr__`
for exactly that reason: the one a dataclass writes for you renders the array, which
would put a household's pixels into any log line, f-string or pytest assertion diff
that ever touched the object.
"""

from __future__ import annotations
import logging
from typing import Any
from dataclasses import dataclass


logger = logging.getLogger(__name__)


# Why no frame came back. Machine codes, the same shape as matching.py's MATCH_REASONS:
# a caller switches on these, and the tutor turns them into something it can say.
#
# The three are kept apart because the remedy differs, and merging them is a decision a
# caller should have to make rather than inherit:
#   camera_disabled -- somebody passed --no-camera. Never retry; there is nothing wrong.
#   no_camera       -- the backend has no camera at all. Never retry.
#   no_frame        -- the camera is there and no sample arrived. Retrying later is fine.
CAPTURE_REASONS: tuple[str, ...] = (
    "camera_disabled",
    "no_camera",
    "no_frame",
)

# How many consecutive reads one request may make before it gives up.
#
# Re-derive it rather than tuning it: get_frame() waits 20ms for a sample and the camera
# produces one every ~33ms (30fps), so a single read can miss simply by being early.
# Five reads span ~100ms, about three frame intervals. There is no sleep between them --
# the wait already happens inside get_frame(), which is the SDK's own shape for "block
# briefly for the next sample" rather than something invented here.
_FRAME_ATTEMPTS = 5

# Distinguishes "this object says its camera is None" from "this object does not talk
# about cameras at all". Only the first is an absent camera; the second is some other
# media object, and it gets to answer for itself by being asked for a frame.
_UNSTATED = object()


# eq=False is deliberate and load-bearing. The generated __eq__ compares the fields
# elementwise, so `a == b` on two frame-bearing answers hits numpy's "truth value of an
# array with more than one element is ambiguous", and frozen+eq would generate a
# __hash__ that raises "unhashable type: ndarray". Both would be exceptions raised on
# the voice path by ordinary Python -- `shot in seen`, a dict key, an assertEqual -- in
# a module whose whole contract is that a caller on that path never gets one. Without
# eq, both fall back to identity, which is the honest answer for a value wrapping a
# buffer. This is the same sweep as the __repr__ override below: the generated
# dataclass methods that are wrong for an array-bearing type are __repr__, __eq__ and
# __hash__, and all three are handled here rather than only the one that was noticed.
@dataclass(frozen=True, eq=False)
class FrameCapture:
    """One frame, or the one reason there isn't one.

    Exactly one of `frame` and `reason` is ever set, and `__post_init__` refuses any
    other combination -- so "an empty frame" is not a value this type can hold, rather
    than a case every caller has to remember to check for. `reason` is one of
    CAPTURE_REASONS. There is deliberately no exception path: a caller on the voice loop
    cannot afford one, and the tutor has to keep talking whatever the camera is doing.
    """

    frame: Any | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        """Refuse any combination that would let a caller mistake nothing for a frame."""
        if self.frame is None and self.reason is None:
            raise ValueError("a FrameCapture carries either a frame or a reason, and this has neither")
        if self.frame is not None and self.reason is not None:
            raise ValueError("a FrameCapture carries either a frame or a reason, and this has both")
        if self.reason is not None and self.reason not in CAPTURE_REASONS:
            # The TYPE, never the value. `reason` is annotated str|None but nothing
            # enforces it, and FrameCapture(reason=<frame>) is a plausible argument-order
            # slip -- with !r that message would carry the array's own repr, which is
            # pixels of somebody's face, into whatever logs or re-raises it. The
            # vocabulary is still named, so the message stays actionable.
            raise ValueError(f"reason must be one of {CAPTURE_REASONS}, not a {type(self.reason).__name__}")

    @property
    def usable(self) -> bool:
        """True when there is a frame to work with, so no caller needs the vocabulary."""
        return self.frame is not None

    def __repr__(self) -> str:
        """Describe the shape of the answer and never a pixel of it.

        THIS IS A PRIVACY CONTROL, not formatting. The generated dataclass repr renders
        the whole array, so any log line, f-string or failing assertion that touched this
        object would write somebody's face into a file. `getattr` rather than `.shape` so
        that a frame which is not an array cannot leak through the fallback either.
        """
        if self.frame is None:
            return f"FrameCapture(reason={self.reason!r})"
        shape = getattr(self.frame, "shape", None)
        dtype = getattr(self.frame, "dtype", None)
        return f"FrameCapture(frame=<{shape} {dtype}>)"


def capture_frame(media: Any, *, camera_enabled: bool) -> FrameCapture:
    """Ask for a single frame now, and answer why not if there isn't one.

    `media` is the handle this app already holds -- `deps.reachy_mini.media`. It is taken
    duck-typed and never imported, which keeps the SDK out of this module's import list,
    keeps it importable on a machine that has no robot, and makes the no-camera paths
    exercisable with an ordinary stand-in rather than hardware.

    The frame is returned exactly as the SDK handed it over, the same object, so there is
    no conversion at the call site and nothing here needs to know what a pixel is.
    """
    # Before anything is touched, and deliberately first: an operator who passed
    # --no-camera has said no, and asking the media layer anyway would be both wasted
    # work and a different answer than the one they asked for.
    if not camera_enabled:
        logger.debug("capture_frame: refused, reason=camera_disabled")
        return FrameCapture(reason="camera_disabled")

    if media is None:
        logger.debug("capture_frame: refused, reason=no_camera")
        return FrameCapture(reason="no_camera")

    try:
        # The real no-camera signal, and the reason this module exists. MediaManager sets
        # `camera` to None when the backend is no_media or when the camera failed to come
        # up; that is a lasting fact about the machine. A None FRAME is not -- it is
        # usually just an early read. Asking here also spares the SDK five
        # "Camera is not initialized." warnings per request on a robot that has none.
        if getattr(media, "camera", _UNSTATED) is None:
            logger.debug("capture_frame: refused, reason=no_camera")
            return FrameCapture(reason="no_camera")

        for _ in range(_FRAME_ATTEMPTS):
            frame = media.get_frame()
            if frame is not None:
                logger.debug("capture_frame: frame")
                return FrameCapture(frame=frame)
    except Exception as exc:
        # The class name only, never the message: a backend's message can carry a device
        # path, and this is the one module where the thing being handled is somebody's
        # face. embedding.py logs failures the same way, for the same reason. Catching
        # broadly is deliberate here rather than lazy -- criterion 3 says no exception
        # reaches the caller, and a camera backend can raise almost anything.
        logger.debug("capture_frame: refused, reason=no_frame after %s", type(exc).__name__)
        return FrameCapture(reason="no_frame")

    logger.debug("capture_frame: refused, reason=no_frame")
    return FrameCapture(reason="no_frame")
