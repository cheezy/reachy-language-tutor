"""Ask who is in front of the robot, and refuse to guess.

This module is the identity boundary. Everything downstream trusts the id it
produces absolutely: the learner tools take no learner parameter precisely so that
this is the only place an identity can come from, which means a wrong answer here
hands one household member another's records and nothing further down will catch it.

A WRONG MATCH IS WORSE THAN NO MATCH, and that is the whole design. Every path that
is not a confident, calibrated identification answers nobody. The learner surface
then goes dead and says why, which is a bad afternoon for somebody; a confident wrong
answer is a child reading their sibling's records with nobody ever finding out.

WHY THIS FILE IS NOT IN faces/. The calibration tripwire in test_face_matching.py
deliberately skips every module inside that package, because the package defines the
flag. Putting the one consumer of `match_faceprint` in there would leave the check
passing vacuously -- evading it rather than satisfying it, which the task that
created this module names as worse than not having the check at all.

WHEN RECOGNITION RUNS, and the reasoning, because the obvious answers are both wrong.
Once per process is wrong for a household: the person at the robot changes during the
day. Continuously is a polling loop and an always-on camera on a small computer, and
the task forbids a thread, a timer and per-frame work. The answer taken here is
NEITHER: recognition runs once, in the calling thread, at the moment the app decides
who it is serving -- the construction of the tool dependencies. One frame is pulled,
compared, and dropped. There is no subscription, so there is no per-frame work, and
the import allow-list in tests/test_recognition_identity.py makes "no thread and no
timer" a property of the file rather than a promise in this paragraph -- named by
file because a reader who greps this one for it finds nothing and concludes the
control does not exist.

That leaves re-identification unsolved, and it is named here rather than left to be
discovered. The app has exactly one natural session boundary -- where the
conversation handler is rebuilt -- and the right long-term shape is a new dependency
bundle per session, which the seal on ToolDependencies turns into an asset: a new
identity means a new sealed bundle, and the previous lesson state is dropped with it.
That is not this change. What bounds the consequence today is the calibration gate
below: while the threshold is unmeasured no identity is ever set from recognition, so
there is no identity to go stale. Solving re-identification is a precondition for
flipping THRESHOLD_CALIBRATED, and it is recorded beside that flag.
"""

from __future__ import annotations
import os
import logging
from typing import Any
from dataclasses import dataclass

from reachy_language_tutor.faces import (
    CAPTURE_REASONS,
    EMBEDDING_REASONS,
    EMBEDDING_MODEL_ID,
    THRESHOLD_CALIBRATED,
    EnrolledFaceprint,
    capture_frame,
    describe_face,
    match_faceprint,
)
from reachy_language_tutor.learners import get_enrolled_faceprints


logger = logging.getLogger(__name__)


# The environment variable that names a learner outright, skipping recognition.
#
# Read here and only here, at startup, never at conversation time -- an identity the
# running conversation can influence is not an identity boundary. Deliberately NOT in
# config.py: that module is re-read by refresh_runtime_config_from_env(), and an
# identity a config refresh can move is one a running process can be made to change.
DEV_CURRENT_LEARNER_ENV = "REACHY_MINI_DEV_CURRENT_LEARNER_ID"


# Why the app is serving who it is serving. Machine codes, the same contract as
# CAPTURE_REASONS and MATCH_REASONS: a caller switches on these, and they are what
# the log line carries INSTEAD of a learner id.
RECOGNITION_DISPOSITIONS: tuple[str, ...] = (
    "identified",
    "override",
    "camera_disabled",
    "no_camera",
    "no_frame",
    "no_face",
    "several_faces",
    "not_confident",
    "frame_unreadable",
    "recognition_unavailable",
    "nobody_enrolled",
    "store_unreadable",
    "no_one_close_enough",
    "too_close_to_call",
    "not_recognised",
    "declined_uncalibrated",
)

# Upstream vocabularies mapped through verbatim, so a reason added to any of them
# fails the totality tests below rather than falling silently into the catch-all and
# telling an operator nothing.
_FROM_CAPTURE = {reason: reason for reason in CAPTURE_REASONS}
_FROM_EMBEDDING = {reason: reason for reason in EMBEDDING_REASONS}
# Spelled out rather than derived, because the mapping is not the identity: three of
# the matcher's reasons are internal faults that mean the same thing to an operator
# ("not recognised") while three are distinct situations worth telling them apart.
# A test asserts every MATCH_REASONS entry appears here, so a new one fails rather
# than silently taking the default.
_FROM_MATCH = {
    "nobody_enrolled": "nobody_enrolled",
    "unusable_vector": "not_recognised",
    "mixed_models": "not_recognised",
    "wrong_dimension": "not_recognised",
    "no_one_close_enough": "no_one_close_enough",
    "too_close_to_call": "too_close_to_call",
}


@dataclass(frozen=True)
class RecognitionOutcome:
    """Who the app should serve, and why that is the answer.

    `learner_id` is None on every path but a confident, calibrated identification and
    an explicit development override. `disposition` is one of
    RECOGNITION_DISPOSITIONS and is what reaches a log -- never the id.
    """

    learner_id: str | None
    disposition: str

    def __post_init__(self) -> None:
        """Refuse a disposition no caller could switch on."""
        if self.learner_id is not None and not (isinstance(self.learner_id, str) and self.learner_id):
            # The sibling check. disposition was validated and learner_id was not,
            # so RecognitionOutcome(12345, "identified") constructed fine -- the
            # D5 -> D10 -> D14 shape this board keeps paying for, caught before it
            # shipped. The TYPE only: a learner id is personal data.
            raise ValueError(f"learner_id must be None or a non-empty string, not a {type(self.learner_id).__name__}")
        if self.disposition not in RECOGNITION_DISPOSITIONS:
            # The VALUE, not its type. This is a membership failure, so the likely
            # mistake is a disposition string nobody published -- and reporting
            # "not a str" for it describes the wrong thing entirely. A disposition
            # is a machine code rather than personal data, which is exactly why it
            # may be quoted here where a learner id never could be.
            raise ValueError(f"disposition must be one of {RECOGNITION_DISPOSITIONS}, not {self.disposition!r}")

    def __repr__(self) -> str:
        """Say whether there is an identity, never which one.

        A PRIVACY CONTROL and not formatting, the same one FrameCapture and
        FaceEmbedding carry: the generated dataclass repr renders the learner id, and
        this object reaches log lines, f-strings and assertion diffs.
        """
        held = "none" if self.learner_id is None else "<set>"
        return f"RecognitionOutcome(disposition={self.disposition!r}, learner_id={held})"


def _development_override() -> str | None:
    """Return the learner id an operator named in the environment, or None.

    Whitespace-only is unset, so a stray `export VAR=` does not silently switch
    recognition off. The value is never logged; the variable's NAME is a source
    constant and discloses nothing.
    """
    named = (os.getenv(DEV_CURRENT_LEARNER_ENV) or "").strip()
    return named or None


def recognise_current_learner(
    *,
    media: Any | None,
    camera_enabled: bool,
    instance_path: Any = None,
) -> RecognitionOutcome:
    """Decide who is in front of the robot, or say why nobody can be named.

    Never raises: the caller is on the startup path and a corrupt database or a
    missing camera in somebody's home must not brick the robot.

    THE ORDER IS THE DESIGN. The override is first, so it is deterministic on a desk
    with no face in front of it. The calibration gate is LAST, so it withholds an
    identification that was really made rather than skipping the work -- which is
    what keeps the answer recognition-derived, and what makes flipping the flag
    change the outcome for the same input rather than switching a feature on.
    """
    try:
        return _recognise(media=media, camera_enabled=camera_enabled, instance_path=instance_path)
    except Exception as exc:
        # THE CLAIM ABOVE, MADE TRUE. It said "never raises" and the function had no
        # try/except at all -- startup survived only because main's resolver wraps
        # the call, so the posture was the caller's property and not this one's, and
        # the test that appeared to cover it passed for the caller's reason. A second
        # caller written on the strength of that docstring would have inherited an
        # unguarded raise. The type only, never a value: this module handles a
        # person's identity.
        logger.warning("Recognition failed and the app is serving nobody: %s", type(exc).__name__)
        return RecognitionOutcome(None, "recognition_unavailable")


def _recognise(*, media: Any | None, camera_enabled: bool, instance_path: Any) -> RecognitionOutcome:
    """Run the pipeline. Wrapped by recognise_current_learner, which absorbs."""
    override = _development_override()
    if override is not None:
        logger.warning(
            "A development override is setting who the app is serving; recognition was not consulted. "
            "Unset %s to use recognition.",
            DEV_CURRENT_LEARNER_ENV,
        )
        return RecognitionOutcome(override, "override")

    shot = capture_frame(media, camera_enabled=camera_enabled)
    if not shot.usable:
        return RecognitionOutcome(None, _FROM_CAPTURE.get(shot.reason, "not_recognised"))

    described = describe_face(shot.frame)
    if not described.usable:
        return RecognitionOutcome(None, _FROM_EMBEDDING.get(described.reason, "not_recognised"))

    household = get_enrolled_faceprints(instance_path=instance_path)
    if household is None:
        return RecognitionOutcome(None, "store_unreadable")
    if not household:
        return RecognitionOutcome(None, "nobody_enrolled")

    outcome = match_faceprint(
        described.vector,
        tuple(
            EnrolledFaceprint(
                learner_id=row.learner_id,
                embedding_model=row.embedding_model,
                dimension=row.dimension,
                vector=row.vector,
            )
            for row in household
        ),
        embedding_model=EMBEDDING_MODEL_ID,
    )
    if not outcome.matched or not outcome.learner_id:
        # `not outcome.learner_id`, not `is None`. Naming one member of the falsy
        # class let MatchOutcome(matched=True, learner_id="") through as identified
        # with an empty id -- measured. One character closes the family, which is
        # the same inversion this repository has paid for four times.
        return RecognitionOutcome(None, _FROM_MATCH.get(outcome.reason, "not_recognised"))

    if not THRESHOLD_CALIBRATED:
        # THE GATE, and it is deliberately below the match rather than above it.
        #
        # The threshold this identification rests on -- the similarity floor, the
        # margin and the lone-member floor -- has never been measured against real
        # faces. A security review said so, and the decision was recorded and handed
        # to this task rather than fixed. Acting on it would make an unmeasured
        # control into a live authorization decision, and the failure it permits is
        # the exact harm this boundary exists to prevent.
        #
        # So a real identification is made and then WITHHELD. No id, and a warning
        # that names the disposition and nobody. Measure the threshold with
        # scripts/calibrate_faceprints.py, record the table beside the constants, and
        # flip the flag -- at which point this same input resolves to this same
        # person, which is what the paired tests assert.
        logger.warning(
            "Recognition identified somebody, and the app is serving nobody: the face-matching "
            "threshold has never been measured, so acting on it would be an unmeasured "
            "authorization decision. See faces.THRESHOLD_CALIBRATED."
        )
        return RecognitionOutcome(None, "declined_uncalibrated")

    return RecognitionOutcome(outcome.learner_id, "identified")
