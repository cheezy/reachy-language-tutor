"""Decide which enrolled household member a faceprint belongs to, or nobody.

Standard library only, on purpose. This module does the arithmetic that decides who
the robot thinks it is talking to, and keeping it free of numpy, cv2 and any model
means its tests need no camera, no database and no downloaded weights -- so the
threshold below can be exercised in both error directions on every run rather than
only when hardware is attached. `embedding.py` is where the heavy dependency lives,
and it converts to plain floats at its boundary so nothing crosses into here.

The decision is made ONCE, here. Callers get "this learner" or "nobody"; they never
get a score to threshold themselves, because a threshold applied in two places is a
threshold that will eventually differ between them.
"""

from __future__ import annotations
import math
import logging
from typing import Sequence
from dataclasses import dataclass


logger = logging.getLogger(__name__)


# Why a match was refused. Machine codes, like the store's vocabularies: a caller
# switches on these, and the tutor turns them into something it can say out loud.
MATCH_REASONS: tuple[str, ...] = (
    "nobody_enrolled",
    "unusable_vector",
    "mixed_models",
    "wrong_dimension",
    "no_one_close_enough",
    "too_close_to_call",
)

# The lowest cosine similarity that may count as the same person.
#
# THIS IS AN AUTHORIZATION CONTROL, not a tuning knob. Too low and one household
# member is served another's progress, which is the exact harm the identity boundary
# exists to prevent; too high and the tutor asks who you are more often than it should.
# The two errors are not symmetric -- a false accept discloses a child's records, a
# false reject costs one retry -- so this sits deliberately ABOVE the 0.363 that
# OpenCV Zoo publishes for this model, rather than at it.
#
# THE REFERENCE POINT, verified rather than repeated: at the pinned revision of the
# model repository, sface.py carries `self._threshold_cosine = 0.363`. That is the
# author's own operating point for general face verification. This sits above it
# because this is not general verification -- it decides whose language records a
# child is shown, and the cost of the two errors is not equal.
#
# NOT MEASURED IN-HOUSE, AND THAT IS THE HONEST STATE OF IT. There is no face data in
# this repository and none may be added: docs/learner-database.md's promise is that no
# image of anybody is stored, and a committed test photograph would break it on the
# first clone. scripts/calibrate_faceprints.py reads an operator's own directory
# OUTSIDE the repo, prints both error directions, and writes nothing. Until somebody
# runs it and records the table here, this number is a defensible default and is
# labelled as one. THRESHOLD_CALIBRATED below is that label, in a form a caller can
# read -- it is deliberately not a comment, because a comment cannot be checked.
#
# ONE FLOOR, WHATEVER THE HOUSEHOLD SIZE. There used to be two: 0.50 here, and a
# separate 0.65 "lone-member floor" applied when only one person was enrolled, on the
# reasoning that a second member adds the margin's protection. It does not -- not
# against the case this floor exists for. The margin asks "is another enrolled PERSON
# nearly as close?", which protects one member from being answered as another. A
# STRANGER has no real person in the household to be compared with, so an unrelated
# second member adds nothing, and yet enrolling one dropped the bar from 0.65 to 0.50.
# Measured on the two-floor version: a non-enrolled candidate at similarity 0.551 to
# member A was refused in a household of A alone and MATCHED AS A once an unrelated B
# (similarity -0.108) was enrolled. So the floor is the one control against a
# stranger in every household, and it carries the higher of the two former values,
# which is the direction to fail in while neither has been measured.
FACE_MATCH_SIMILARITY_FLOOR = 0.65

# How far ahead of the runner-up the best match must be before it is trusted.
#
# The floor alone is not enough, and siblings are why. Two people who look alike can
# both clear any absolute threshold, and then "best" is decided by noise -- lighting,
# angle, which frame the camera happened to give us. A margin turns that from a coin
# flip into a refusal, which is the direction to fail in: asking "who is practising?"
# is a mild cost, and answering with the wrong sibling is not.
FACE_MATCH_MARGIN = 0.10

# The only vector length this matcher will compare.
#
# Not a tidiness check. Cosine similarity on a ONE-element vector is always exactly
# +1 or -1, so a 1-dimensional row clears any floor unconditionally and the threshold
# stops meaning anything at all -- measured, not reasoned about. Two elements are
# barely better. The faceprints table permits dimension 1..1024, so a short row is
# storable by a buggy or poisoned enrolment; this is the matcher refusing to be the
# place that turns one into a match.
#
# Stated here rather than imported from embedding.py, because this module must stay
# free of the heavy dependency -- that is what lets its tests run with no model.
EXPECTED_MATCH_DIMENSION = 128

# Whether the floor and the margin above have been measured against real faces. False, today.
#
# This exists because "the threshold is unvalidated" is a fact a reviewer can read in a
# comment and a running robot cannot. A caller that is about to act on a recognition --
# setting the current learner, and thereby choosing whose records are shown -- can ask
# this and decide whether it trusts an uncalibrated control, rather than discovering
# the state from a code comment nobody reads at runtime.
#
# Flipping it is not a code change on its own: it means running
# scripts/calibrate_faceprints.py against a real set, recording the table beside
# FACE_MATCH_SIMILARITY_FLOOR, and only then setting this True. Setting it True without
# that is the exact "tune until it looks fine" failure the task's pitfalls name.
#
# SHIPPING UNCALIBRATED WAS AN EXPLICIT DECISION, not an oversight. A security review
# raised the unmeasured threshold as an open authorization risk during W26; the project
# owner accepted it and assigned the consequence to W29, the task that wires recognition
# into choosing whose profile the tutor teaches from. So W29 is where this flag has to
# stop being decorative: a caller that reads False and switches profiles anyway has
# taken the risk without deciding to. The test in tests/test_face_matching.py that
# refuses any caller of match_faceprint which never mentions this name is the mechanism
# that makes W29 confront it rather than inherit it silently.
#
# BEFORE YOU SET THIS True, TWO THINGS ARE OWED, not one.
#
#   1. MEASURE IT. Run scripts/calibrate_faceprints.py against a directory of real
#      faces outside this repository and record the table it prints beside the two
#      constants above. Both error directions, not just the false rejects.
#
#   2. DECIDE WHAT HAPPENS WHEN THE PERSON CHANGES MID-SESSION. This one is easy to
#      miss because nothing fails without it today. W29 wired recognition into
#      choosing the current learner and left re-identification unsolved, and what
#      bounds that is THIS FLAG: while it is False no identity is ever set from a
#      face, so there is no identity that can go stale. Flipping it removes that
#      bound in the same motion as it enables matching. ToolDependencies is sealed
#      after construction, so a mid-session change is not expressible today at all --
#      the shape that works is a new bundle per session, at the boundary where the
#      conversation handler is rebuilt. current_learner.py's module docstring carries
#      the reasoning; this line is here so that whoever flips the flag reads it.
THRESHOLD_CALIBRATED = False


@dataclass(frozen=True)
class MatchOutcome:
    """Who the robot believes it is looking at, or nobody, and why.

    `learner_id` is set only when `matched` is True. `reason` is one of MATCH_REASONS
    only when `matched` is False. There is deliberately no score on this type: the
    decision is made here, once, and a caller handed a number would eventually apply
    its own threshold to it.
    """

    matched: bool
    learner_id: str | None = None
    reason: str | None = None

    def __repr__(self) -> str:
        """Say whether somebody was matched and why not, never who.

        A PRIVACY CONTROL, the same one FaceEmbedding, FrameCapture and
        RecognitionOutcome carry: the generated repr renders the learner id into any
        log line, f-string or assertion diff that touches this object.
        """
        held = "none" if self.learner_id is None else "<set>"
        return f"MatchOutcome(matched={self.matched!r}, learner_id={held}, reason={self.reason!r})"


@dataclass(frozen=True)
class EnrolledFaceprint:
    """One household member's stored faceprint, as the matcher needs it.

    Deliberately not the store's Faceprint: this module must not import the learner
    package, so that its tests need no database. A caller builds these from whatever
    the store returned.
    """

    learner_id: str
    embedding_model: str
    dimension: int
    vector: tuple[float, ...]

    def __repr__(self) -> str:
        """Describe the row's shape, never whose it is or a number of it.

        The generated repr printed all 128 floats and the learner id -- 1392
        characters of one person's biometric data -- into anything that rendered it.
        The model id and dimension are machine codes and carry nobody.
        """
        length = len(self.vector) if isinstance(self.vector, (tuple, list)) else None
        return (
            f"EnrolledFaceprint(learner_id=<set>, embedding_model={self.embedding_model!r}, "
            f"dimension={self.dimension!r}, vector=<{length} floats>)"
        )


def _is_usable(vector: object, dimension: int | None = None) -> bool:
    """Report whether this value is a vector this module may compare.

    An allow-list of what a vector may BE: a sequence of at least one real, finite
    float, of the expected length when one is known. Everything else -- a str, a bool
    (which is an int and would compare as 1.0), a NaN that silently makes every
    comparison false, an infinity -- is refused rather than quietly compared.
    """
    if isinstance(vector, (str, bytes, bytearray)) or not isinstance(vector, Sequence):
        return False
    if not vector:
        return False
    if dimension is not None and len(vector) != dimension:
        return False
    for value in vector:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        if not math.isfinite(value):
            return False
    return True


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float | None:
    """Cosine similarity of two equal-length vectors, or None if it is undefined.

    math.fsum rather than sum, because a 128-element dot product accumulates rounding
    that a threshold comparison then depends on. A zero-magnitude vector has no
    direction and therefore no similarity to anything -- None, never 0.0, which would
    read as "maximally dissimilar" and quietly pass the refusal path.

    EACH VECTOR IS SCALED BY ITS OWN LARGEST ELEMENT FIRST. Cosine does not change
    under scaling, so this costs no meaning, and it is what keeps the arithmetic
    inside the range a float can hold. Unscaled, a vector of large FINITE values --
    which _is_usable admits -- made math.fsum raise ("-inf + inf in fsum",
    "intermediate overflow in fsum") and made this return NaN, in a module whose entry
    point promises never to raise and whose published similarity promises None rather
    than a non-number. Measured with elements of 1e200 and 1e154. After scaling every
    element is in [-1, 1], so no product, square or 128-element sum can overflow.

    And the result is returned only when it is a finite number -- the one shape a
    floor test can safely compare. Anything else is None.
    """
    if len(left) != len(right):
        return None
    left_scale = max((abs(a) for a in left), default=0.0)
    right_scale = max((abs(b) for b in right), default=0.0)
    if not (0.0 < left_scale < math.inf and 0.0 < right_scale < math.inf):
        return None
    left = [a / left_scale for a in left]
    right = [b / right_scale for b in right]
    dot = math.fsum(a * b for a, b in zip(left, right))
    left_magnitude = math.sqrt(math.fsum(a * a for a in left))
    right_magnitude = math.sqrt(math.fsum(b * b for b in right))
    similarity = dot / (left_magnitude * right_magnitude)
    return similarity if math.isfinite(similarity) else None


def faceprint_similarity(left: Sequence[float], right: Sequence[float]) -> float | None:
    """How alike two faceprints are, or None when the question has no answer.

    The published form of this module's own comparison, so that enrolment can ask
    whether a person's frames agree with each other without reaching past the package
    boundary into a private helper -- the boundary whose violation
    scripts/calibrate_faceprints.py records as the defect in its own first version.

    It is the SAME comparison match_faceprint uses, not a second one written to look
    like it. That matters: enrolment checks a person's frames against each other using
    the floor this module publishes, so a print that would not clear the floor against
    its own owner is refused at enrolment rather than discovered later by a household
    member the robot cannot recognise.

    None when the comparison is undefined -- different lengths, a zero-magnitude
    vector, or anything that is not a vector of finite real numbers. Never 0.0 for
    those, which would read as "maximally dissimilar" and quietly pass a floor test.
    """
    if not _is_usable(left) or not _is_usable(right):
        return None
    return _cosine_similarity(left, right)


def match_faceprint(
    candidate: Sequence[float],
    enrolled: Sequence[EnrolledFaceprint],
    *,
    embedding_model: str,
) -> MatchOutcome:
    """Say which enrolled member this faceprint belongs to, or that it is nobody.

    Never raises. "Nobody" is a first-class answer and the common one: a visiting
    friend, a delivery, a face on a television, or simply a bad frame all resolve here
    rather than to the nearest enrolled member.

    Every row must come from the same embedding model as the candidate. A household
    part-way through a model upgrade is refused WHOLE rather than matched against the
    rows that happen to agree -- skipping the mismatched rows would leave a look-alike
    as the best remaining candidate and hand them somebody else's records, which is
    the failure this refusal exists to prevent.
    """
    if not enrolled:
        return MatchOutcome(matched=False, reason="nobody_enrolled")
    if not _is_usable(candidate):
        return MatchOutcome(matched=False, reason="unusable_vector")
    if len(candidate) != EXPECTED_MATCH_DIMENSION:
        logger.warning(
            "Refusing a candidate of the wrong length: dimension %d, expected %d",
            len(candidate),
            EXPECTED_MATCH_DIMENSION,
        )
        return MatchOutcome(matched=False, reason="wrong_dimension")

    for row in enrolled:
        # Two different faults, two different reasons. They were one code and one log
        # line, which sent a maintainer looking for a model upgrade whenever a
        # dimension simply disagreed. Shapes and counts only -- never a learner id,
        # never a vector.
        if row.embedding_model != embedding_model:
            logger.warning(
                "Refusing to match across embedding models: %d enrolled row(s)",
                len(enrolled),
            )
            return MatchOutcome(matched=False, reason="mixed_models")
        if row.dimension != len(candidate) or row.dimension != EXPECTED_MATCH_DIMENSION:
            logger.warning(
                "Refusing to match vectors of different lengths: candidate dimension %d, %d enrolled row(s)",
                len(candidate),
                len(enrolled),
            )
            return MatchOutcome(matched=False, reason="wrong_dimension")
        if not _is_usable(row.vector, row.dimension):
            return MatchOutcome(matched=False, reason="unusable_vector")

    # Best score PER PERSON, not per row. The margin asks "is another PERSON nearly as
    # close?", and comparing the top two rows answers a different question: two rows
    # belonging to one learner sit a margin apart from each other and refused that
    # learner outright. The faceprints table's primary key makes one row per learner
    # the norm, so this is defensive rather than reachable today -- but the margin now
    # means what its comment says it means.
    best_per_learner: dict[str, float] = {}
    for row in enrolled:
        similarity = _cosine_similarity(candidate, row.vector)
        if similarity is None:
            return MatchOutcome(matched=False, reason="unusable_vector")
        previous = best_per_learner.get(row.learner_id)
        if previous is None or similarity > previous:
            best_per_learner[row.learner_id] = similarity

    scored = sorted(((score, learner) for learner, score in best_per_learner.items()), reverse=True)
    best_similarity, best_learner = scored[0]

    # One floor, whoever else is enrolled -- see FACE_MATCH_SIMILARITY_FLOOR for the
    # measurement that removed the lone-member floor. The margin below is ADDITIONAL
    # protection between enrolled people, never a reason to lower this one.
    floor = FACE_MATCH_SIMILARITY_FLOOR

    # Written positively -- `not (x >= floor)` rather than `x < floor` -- so it fails
    # CLOSED on NaN. Every comparison against NaN is false, so the obvious spelling
    # falls THROUGH to a match: a NaN similarity would be answered as the person.
    # _is_usable should make that unreachable, but a validator that reads a value once
    # and arithmetic that reads it again are two reads, and the safe direction costs
    # nothing.
    if not (best_similarity >= floor):
        return MatchOutcome(matched=False, reason="no_one_close_enough")

    if len(scored) > 1:
        runner_up = scored[1][0]
        # Positive for the same reason as the floor above.
        if not (best_similarity - runner_up >= FACE_MATCH_MARGIN):
            # The sibling case. Both may be over the floor; that is exactly when the
            # answer must be "I am not sure" rather than whichever won by noise.
            return MatchOutcome(matched=False, reason="too_close_to_call")

    return MatchOutcome(matched=True, learner_id=best_learner)
