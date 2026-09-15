"""Face recognition: pixels to numbers, and numbers to one household member or nobody.

This package is the boundary. Import from here, never from the modules underneath.

It owns no hardware, and until enrolment arrived it touched no database either. It
now reaches one on exactly one path -- `enrollment.py`, which writes a person's
agreement and then their faceprint, in that order and no other -- and that path writes
numbers and never pixels. Everything else here is still handed what it needs: `embedding.py` is handed a frame by
whatever owns the camera, and `matching.py` is handed rows by whatever owns the store.
`capture.py` is the one module that goes near a camera, and it borrows a handle rather
than opening one -- it starts nothing, closes nothing, and keeps no frame. That
separation is what lets the threshold be exercised in both error directions on every
test run, with no hardware and no downloaded weights.

WHAT IS STORED, AND WHAT IS NOT. A faceprint is 128 float32 numbers. No image, no
crop, no thumbnail and no path to one is written by anything in this package, at any
point, including temporarily.

NOBODY IS ENROLLED WITHOUT AGREEING FIRST, and `enrollment.py` is not where that is
enforced -- it is enforced beneath it, by a store that publishes no way to create a
learner except alongside their consent and a faceprint INSERT that draws its rows from
the consents table. Enrolment is an operator action performed in person, never a
conversation tool: a tool that could create an identity is the same boundary breach as
one that accepts an identity, one step earlier.

**THERE IS NO LIVENESS CHECK, AND THAT IS A DECISION RATHER THAN AN OVERSIGHT.** A
photograph held up to the camera, or a face on a television or a phone screen, is
treated as that person if the detector accepts it. Nobody should read this package as
proving who is physically present.

What bounds the consequence is what recognition is USED for here: it selects which
learner profile the tutor teaches from. The worst case is that somebody sees another
household member's language progress and practises as them -- a real privacy failure
inside one home, and the reason the threshold and margin in `matching.py` are treated
as an authorization control. It is not a lock, it does not guard money or messages,
and it must never be extended to something that does without a liveness check
arriving first.
"""

from reachy_language_tutor.faces.capture import (
    CAPTURE_REASONS,
    FrameCapture,
    capture_frame,
)
from reachy_language_tutor.faces.matching import (
    MATCH_REASONS,
    FACE_MATCH_MARGIN,
    THRESHOLD_CALIBRATED,
    EXPECTED_MATCH_DIMENSION,
    FACE_MATCH_SIMILARITY_FLOOR,
    FACE_MATCH_LONE_MEMBER_FLOOR,
    MatchOutcome,
    EnrolledFaceprint,
    match_faceprint,
    faceprint_similarity,
)
from reachy_language_tutor.faces.embedding import (
    EMBEDDING_REASONS,
    EMBEDDING_MODEL_ID,
    FACE_EMBEDDING_AVAILABLE,
    EXPECTED_EMBEDDING_DIMENSION,
    FaceEmbedding,
    embed_face,
    describe_face,
    warm_face_models,
    loaded_face_models,
)
from reachy_language_tutor.faces.enrollment import (
    CONSENT_SCOPE,
    CONSENT_STATEMENT,
    ENROLMENT_REASONS,
    CONSENT_STATEMENT_ID,
    EnrolmentOutcome,
    enrol,
    capture_faceprint,
    consent_statement,
    record_consent_for_enrolment,
)


__all__ = [
    "CAPTURE_REASONS",
    "CONSENT_SCOPE",
    "CONSENT_STATEMENT",
    "CONSENT_STATEMENT_ID",
    "EMBEDDING_REASONS",
    "ENROLMENT_REASONS",
    "EnrolmentOutcome",
    "FaceEmbedding",
    "EMBEDDING_MODEL_ID",
    "EXPECTED_EMBEDDING_DIMENSION",
    "EnrolledFaceprint",
    "FACE_EMBEDDING_AVAILABLE",
    "EXPECTED_MATCH_DIMENSION",
    "FACE_MATCH_LONE_MEMBER_FLOOR",
    "FACE_MATCH_MARGIN",
    "FACE_MATCH_SIMILARITY_FLOOR",
    "MATCH_REASONS",
    "THRESHOLD_CALIBRATED",
    "FrameCapture",
    "MatchOutcome",
    "capture_faceprint",
    "capture_frame",
    "consent_statement",
    "describe_face",
    "embed_face",
    "enrol",
    "faceprint_similarity",
    "record_consent_for_enrolment",
    "loaded_face_models",
    "match_faceprint",
    "warm_face_models",
]
