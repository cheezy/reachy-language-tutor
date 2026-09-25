"""Turn a frame containing a face into a faceprint: numbers, and nothing else.

This is the only module in the package that carries a heavy dependency, and it is
guarded the way tools/play_emotion.py guards the emotion library: a module-level
AVAILABLE flag set by a try/except, so a robot without the library still boots and
still teaches -- it simply cannot recognise anybody, which is a degradation rather
than a failure.

The boundary is deliberate. Everything here speaks plain Python floats to the rest of
the app: numpy exists inside this file and dies at .tolist(). learners/store.py says
in as many words that numpy is not a declared dependency of the store layer and the
conversion belongs at the call site, so this is that call site.

NOTHING HERE WRITES A FRAME, A CROP OR AN EMBEDDING ANYWHERE. Not to disk, not to a
log, not temporarily. A frame of somebody's face is the one thing docs/plan.md
promises never to keep, and a debug line written "just for now" is how that promise
is broken without anyone editing the sentence.
"""

from __future__ import annotations
import hashlib
import logging
from typing import Any
from pathlib import Path
from dataclasses import dataclass


logger = logging.getLogger(__name__)

try:
    import cv2
    import numpy as np
    from huggingface_hub import hf_hub_download

    FACE_EMBEDDING_AVAILABLE = True
except Exception as exc:  # pragma: no cover - exercised only where cv2 is absent
    # The type, not the exception. play_emotion.py logs the exception itself here and
    # that is safe for ITS import; this module's failures can carry a filesystem path,
    # and three arms in one file that disagree about what may be logged is how the
    # no-PII rule erodes. ModuleNotFoundError is the actionable part anyway.
    logger.warning("Face embedding not available: %s", type(exc).__name__)
    FACE_EMBEDDING_AVAILABLE = False


# The model that produces a faceprint, and the exact revision of it.
#
# The revision is PINNED, and that is not ceremony. This repository already carries a
# defect (D30) for fetching the emotion library from the hub without one: an upstream
# force-push silently changes what a robot downloads, and for THIS model that would
# mean every stored faceprint was computed by a model nobody can name any more --
# comparing numbers that no longer mean the same thing. learners.faceprints stores the
# model id with every row precisely so that cannot happen quietly; pinning is the
# other half of that.
#
# The id is ASCII with no slash because the faceprints table refuses anything else:
# its embedding_model column permits A-Za-z0-9._- only, so the hub-style "opencv/..."
# repo name cannot be the stored identifier. The suffix matters too -- the same repo
# ships int8 variants whose numbers differ, so a faceprint from one is not comparable
# with a faceprint from the other, and the id has to say which produced it.
EMBEDDING_MODEL_ID = "opencv_sface_2021dec_fp32"

_DETECTOR_REPO = "opencv/face_detection_yunet"
_DETECTOR_FILE = "face_detection_yunet_2023mar.onnx"
_DETECTOR_REVISION = "3cc26e7f1014a5ee5d74a42acee58bafc9d0a310"

_RECOGNIZER_REPO = "opencv/face_recognition_sface"
_RECOGNIZER_FILE = "face_recognition_sface_2021dec.onnx"
_RECOGNIZER_REVISION = "3d7082438a6e4551e840c9b2bb60b71e8da4b524"

# The SHA-256 of each file's CONTENTS, checked before the file is handed to OpenCV.
#
# The revision pins which file the hub serves; it says nothing about the file that is
# actually on this robot. hf_hub_download returns whatever sits in the local cache, and
# measured: a cached detector blob replaced with a different ONNX model loaded without
# complaint, and every faceprint would then have been stamped with EMBEDDING_MODEL_ID
# regardless of what produced it. These digests make "the model this app names" a
# property of the bytes.
#
# HOW THESE WERE OBTAINED, so they can be re-derived rather than trusted: on
# 2026-09-24, `shasum -a 256` over the two files in the local huggingface cache at the
# pinned revisions (232,589 and 38,696,353 bytes), and separately HfApi.get_paths_info
# for the same repo, file and revision, whose LFS sha256 matched both exactly. Changing
# a revision above means re-measuring its digest here the same two ways.
_DETECTOR_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
_RECOGNIZER_SHA256 = "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"

# What this model is expected to produce. Checked against the loaded model rather than
# trusted: if the file behind the pin ever stops producing this, the loader refuses to
# come up instead of storing vectors of a shape the database bound was chosen for.
# 128 is inside the faceprints table's 1..1024 dimension CHECK, and float32 is exactly
# what that table stores.
EXPECTED_EMBEDDING_DIMENSION = 128

# The detector's working resolution. It is resized per frame anyway; this is only the
# size the model is constructed at.
_DETECTOR_INPUT_SIZE = (320, 320)

# How sure the detector must be that a region is a face before it is embedded. Below
# this the frame is treated as having no face, which is the safe direction: a bad
# detection produces a meaningless vector, and a meaningless vector compared against a
# household is exactly how somebody gets matched to a wall.
_DETECTION_CONFIDENCE_FLOOR = 0.9

_LOADED_MODELS: tuple[Any, Any] | None = None


# Why no faceprint came out of a frame. Machine codes, the same shape and the same
# contract as capture.py's CAPTURE_REASONS: a caller switches on these, and enrolment
# turns them into something an operator can act on.
#
# Kept apart because the remedy differs, which is the same test capture.py applies:
#   recognition_unavailable -- no library, or the models are not loaded. Never retry.
#   no_face                 -- nobody is in shot. Ask them to look at the robot.
#   several_faces           -- more than one person is in shot. Ask one to step out.
#   not_confident           -- something face-shaped, below the detector's floor.
#   frame_unreadable        -- the frame was not something the detector could read.
EMBEDDING_REASONS: tuple[str, ...] = (
    "recognition_unavailable",
    "no_face",
    "several_faces",
    "not_confident",
    "frame_unreadable",
)


# eq=False for the same load-bearing reason FrameCapture carries it, and the two types
# are deliberately identical in shape: a generated __eq__ would compare the vector
# elementwise, and frozen+eq would generate a __hash__ over a tuple of floats that
# reads as harmless right up until somebody puts one in a set. Identity is the honest
# answer for a value wrapping somebody's face, and __repr__ below is the same sweep --
# the generated one renders all 128 numbers into any log line or assertion diff.
@dataclass(frozen=True, eq=False)
class FaceEmbedding:
    """One faceprint, or the one reason there isn't one.

    Exactly one of `vector` and `reason` is ever set, and __post_init__ refuses any
    other combination -- so "an empty faceprint" is not a value this type can hold.
    `reason` is one of EMBEDDING_REASONS. There is no exception path, for the reason
    FrameCapture gives: the tutor has to keep talking whatever the camera is doing.
    """

    vector: tuple[float, ...] | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        """Refuse any combination that would let a caller mistake nothing for a face."""
        if self.vector is None and self.reason is None:
            raise ValueError("a FaceEmbedding carries either a vector or a reason, and this has neither")
        if self.vector is not None and self.reason is not None:
            raise ValueError("a FaceEmbedding carries either a vector or a reason, and this has both")
        if self.reason is not None and self.reason not in EMBEDDING_REASONS:
            # The TYPE, never the value -- FaceEmbedding(reason=<vector>) is a
            # plausible argument-order slip, and !r would put somebody's faceprint
            # into whatever logs or re-raises this message.
            raise ValueError(f"reason must be one of {EMBEDDING_REASONS}, not a {type(self.reason).__name__}")

    @property
    def usable(self) -> bool:
        """True when there is a faceprint to work with, so no caller needs the vocabulary."""
        return self.vector is not None

    def __repr__(self) -> str:
        """Describe the shape of the answer and never a number of it.

        THIS IS A PRIVACY CONTROL, not formatting, and it is the same one FrameCapture
        carries one layer up. A faceprint is biometric data about a person; the
        generated dataclass repr would render all of it into any log line, f-string or
        failing assertion that ever touched this object.
        """
        if self.vector is None:
            return f"FaceEmbedding(reason={self.reason!r})"
        return f"FaceEmbedding(vector=<{len(self.vector)} floats>)"


class FaceModelMismatch(RuntimeError):
    """A model file on disk is not the file this app pins, so it is not loaded."""


def _verify_model_file(path: str, expected_sha256: str) -> None:
    """Refuse a model file whose contents are not the pinned file's.

    Read whole, once per process, at start-up: 38 MB for the recognizer. The message
    names neither the path (a home directory) nor the digest it found; the class is
    what warm_face_models logs, and it says which failure this was.
    """
    if hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected_sha256:
        raise FaceModelMismatch("a face model file does not match its pinned SHA-256")


def load_face_models() -> tuple[Any, Any]:
    """Load the detector and the recognizer, downloading them once if needed.

    Mirrors load_emotion_library: cached in a module global, so the download happens
    at most once per process. Raises rather than returning a sentinel, because the
    only caller that should reach it is warm_face_models below, which is where the
    failure is turned into a reported False.
    """
    global _LOADED_MODELS
    if _LOADED_MODELS is None:
        detector_path = hf_hub_download(_DETECTOR_REPO, _DETECTOR_FILE, revision=_DETECTOR_REVISION)
        recognizer_path = hf_hub_download(_RECOGNIZER_REPO, _RECOGNIZER_FILE, revision=_RECOGNIZER_REVISION)
        _verify_model_file(detector_path, _DETECTOR_SHA256)
        _verify_model_file(recognizer_path, _RECOGNIZER_SHA256)
        detector = cv2.FaceDetectorYN.create(detector_path, "", _DETECTOR_INPUT_SIZE)
        recognizer = cv2.FaceRecognizerSF.create(recognizer_path, "")

        # The dimension is read from the model, not asserted about it. A pinned file
        # that no longer produces what the database bound was chosen for is a reason
        # to stay unavailable, not a reason to store a differently-shaped vector.
        probe = np.zeros((112, 112, 3), dtype=np.uint8)
        produced = int(recognizer.feature(probe).shape[1])
        if produced != EXPECTED_EMBEDDING_DIMENSION:
            raise RuntimeError(f"face model produced dimension {produced}, expected {EXPECTED_EMBEDDING_DIMENSION}")
        _LOADED_MODELS = (detector, recognizer)
    return _LOADED_MODELS


def loaded_face_models() -> tuple[Any, Any] | None:
    """Return the loaded models without loading them, or None if none are loaded.

    Lets a caller ask whether recognition is ready without paying a download, which is
    what the conversation loop needs: it must never block a turn on a network fetch.
    """
    return _LOADED_MODELS


def warm_face_models() -> bool:
    """Load the models at startup, reporting rather than raising.

    Called once during start-up so the first person to stand in front of the robot
    does not wait for a download. A robot with no network answers False here and
    keeps teaching -- it simply cannot recognise anybody, and the tutor asks who is
    practising instead.
    """
    if not FACE_EMBEDDING_AVAILABLE:
        return False
    try:
        load_face_models()
    except Exception as exc:
        # The shape of the failure, never a path that might name a person's home
        # directory, and never a frame.
        logger.warning("Could not load the face models: %s", type(exc).__name__)
        return False
    return True


def describe_face(image: Any) -> FaceEmbedding:
    """Turn this frame into a faceprint, or say which of five things stopped it.

    The one implementation; embed_face is a thin wrapper over it. Enrolment needs the
    reason because an operator's remedy differs per cause -- "nobody is in shot" and
    "two people are in shot" are different instructions to give a person standing at
    the robot -- while the tutor's voice loop does not, and keeps the narrower call.
    Splitting the reason out rather than adding a second detection call site is what
    keeps there being exactly one place a frame is looked at.

    MORE THAN ONE FACE IS REFUSED, deliberately and unchanged. Two people in frame has
    no single right answer, and picking the largest or the most central would silently
    decide which household member the robot is talking to on the strength of who
    leaned in.

    Returns plain floats. The numpy array never leaves this function.
    """
    if not FACE_EMBEDDING_AVAILABLE:
        return FaceEmbedding(reason="recognition_unavailable")
    models = loaded_face_models()
    if models is None:
        return FaceEmbedding(reason="recognition_unavailable")
    detector, recognizer = models

    try:
        height, width = int(image.shape[0]), int(image.shape[1])
        detector.setInputSize((width, height))
        # THE FIRST RETURN VALUE IS A SUCCESS FLAG, NOT A FACE COUNT. Measured against
        # the pinned YuNet model: on a blank 240x320 frame detect() returns
        # (1, None) -- retval 1, and no faces. So `if retval == 0` reads "one face"
        # off an empty frame, and len() raises on the None. The count comes from the
        # array, and the array being None IS the zero.
        _, faces = detector.detect(image)
        found = 0 if faces is None else len(faces)
        if found == 0:
            return FaceEmbedding(reason="no_face")
        if found > 1:
            return FaceEmbedding(reason="several_faces")
        face = faces[0]
        if float(face[-1]) < _DETECTION_CONFIDENCE_FLOOR:
            return FaceEmbedding(reason="not_confident")
        aligned = recognizer.alignCrop(image, face)
        produced = recognizer.feature(aligned)
        return FaceEmbedding(vector=tuple(float(value) for value in produced.flatten().tolist()))
    except Exception as exc:
        # Never the frame, never the vector, never a path. The type alone is enough to
        # tell a maintainer which layer failed.
        logger.warning("Could not produce a faceprint: %s", type(exc).__name__)
        return FaceEmbedding(reason="frame_unreadable")


def embed_face(image: Any) -> tuple[float, ...] | None:
    """Return the faceprint of the single face in this frame, or None.

    The narrow form, and the contract every existing caller already has: None means
    "no faceprint from this frame" and covers every reason -- the library is absent,
    the models are not loaded, there is no face, there is more than one face, the
    detection was not confident enough, or the frame could not be read. A caller that
    needs to tell those apart calls describe_face; a caller on the voice loop does
    not, because what it can do about any of them is the same.

    Returns plain floats. The numpy array never leaves describe_face.
    """
    return describe_face(image).vector
