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
import logging
from typing import Any


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


def embed_face(image: Any) -> tuple[float, ...] | None:
    """Return the faceprint of the single face in this frame, or None.

    None means "no faceprint from this frame", and it covers every reason: the library
    is absent, the models are not loaded, there is no face, there is more than one
    face, or the detection was not confident enough. A caller that needs to tell those
    apart is asking the wrong layer -- what it can do about any of them is the same,
    which is to try another frame or ask who is practising.

    MORE THAN ONE FACE IS REFUSED, deliberately. Two people in frame has no single
    right answer, and picking the largest or the most central would silently decide
    which household member the robot is talking to on the strength of who leaned in.

    Returns plain floats. The numpy array never leaves this function.
    """
    if not FACE_EMBEDDING_AVAILABLE:
        return None
    models = loaded_face_models()
    if models is None:
        return None
    detector, recognizer = models

    try:
        height, width = int(image.shape[0]), int(image.shape[1])
        detector.setInputSize((width, height))
        _, faces = detector.detect(image)
        if faces is None or len(faces) != 1:
            return None
        face = faces[0]
        if float(face[-1]) < _DETECTION_CONFIDENCE_FLOOR:
            return None
        aligned = recognizer.alignCrop(image, face)
        feature = recognizer.feature(aligned)
        return tuple(float(value) for value in feature.flatten().tolist())
    except Exception as exc:
        # Never the frame, never the vector, never a path. The type alone is enough to
        # tell a maintainer which layer failed.
        logger.warning("Could not produce a faceprint: %s", type(exc).__name__)
        return None
