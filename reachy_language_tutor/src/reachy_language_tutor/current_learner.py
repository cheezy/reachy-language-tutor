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

WHEN RECOGNITION CANNOT ANSWER, there is a configured fallback, and the shape of it
is the whole point. The obvious implementation -- the robot asks "who is practising?",
the person says a name, the model passes it to a tool -- is precisely the breach this
codebase is built to prevent, and no amount of confirmation makes a spoken name into
authentication. So the selection happens somewhere the conversation cannot reach: an
instance-local settings file, written by an operator with a shell on the device (the
`enrol --serve-when-unrecognised` command), read once at startup. The settings UI does
not write it -- nothing on /rpc can, and an earlier draft of this paragraph said it did.

WHY THAT IS OUT OF REACH, as a list of facts rather than an assurance. No tool takes
a learner id or a name; PERMITTED_TOOL_PARAMETERS is an allow-list with nothing
identity-shaped in it, and W16 shrank it rather than growing it. No tool takes a file
path. No module under tools/ imports this one or startup_settings, and the tool
import closure is checked by test. So the model has no route to the value, and what
it says cannot influence it -- which is the only security property the fallback
actually has.

THE SCOPE OF THAT, precisely, because a boundary described too widely is one somebody
later relies on where it does not hold: every one of those checks is a CI-time check
over the tools in THIS repository. Nothing validates a tool's parameters as it loads,
so a tool from config.TOOLS_DIRECTORY or a remote Space is outside all of it. The
claim is "no tool in this repository can reach the identity", not "no tool can".

WHAT IT IS WORTH, said plainly because it is weaker than recognition and must never
be mistaken for it. It is a configuration, not a check. It does not establish who is
present. In a one-person household it is exactly right and gives nothing away, since
there is no second profile to reach. In a household with more than one learner it is
WRONG BY CONSTRUCTION -- whoever sits down is served the configured person's lessons
and progress. The app cannot detect that in general, so such a household needs
recognition and this setting left unset. What it CAN detect it now acts on: when the
camera saw two faces, or a face it could compare and tie to somebody other than the
configured learner, the fallback is refused (_FALLBACK_MAY_APPLY). That is the honest
answer plan.md anticipated, written down rather than dressed up as something
stronger. _fall_back carries the rest.

IF THE PERSON BEING SERVED IS ERASED MID-SESSION, the decision is to do nothing to
the running process, and it is a decision rather than an omission. ToolDependencies
is sealed, so the id stays until the bundle is rebuilt -- and a stale id reaches
nobody. Measured against a real store after forget_learner_entirely: get_profile
answers None; record_result and save_faceprint both refuse with `unknown_learner`, so
nothing recreates the erased rows; and get_progress does NOT answer None -- it
answers the language's public course with completed=() and attempts=(), which is
byte-identical to what an id that never existed gets. That distinction is the one
that matters here and it was measured rather than assumed, twice, because the first
two attempts to write this paragraph both got it wrong: what comes back is the
catalogue every household shares, never another member's record. Learner ids are
UUIDs, so an erased id is never reissued and can never come to name somebody else.

The lesson surface therefore goes quiet for the rest of the process -- the right
failure for somebody who just asked to be forgotten -- and the id is gone at the next
session boundary. test_face_deletion.py pins each of those four answers, because the
decision rests on them and a silent regression would turn "reaches nobody" into
"reaches somebody".
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
    "configured_fallback",
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


# The recognition answers on which a configured fallback MAY be served. An ALLOW-LIST,
# decided one disposition at a time, and the principle is: the fallback applies only when
# recognition gathered NO evidence about who is in front of the robot. Where it gathered
# evidence that points somewhere else, or cannot tell people apart, serving the
# configured learner would be the confident wrong answer this module exists to refuse.
# A disposition added upstream is therefore refused until somebody decides here -- the
# totality test in test_identity_fallback.py fails until they do.
#
# Measured before this list existed: with two learners and the fallback set to one of
# them, recognition matching the OTHER, the camera seeing two faces, and the matcher
# calling it too close between the two all resolved to the fallback learner.
#
# PERMITTED -- nothing about identity was learned:
#   camera_disabled, no_camera, no_frame -- no image at all; a covered camera and a robot
#       started with --no-camera are the household this setting exists for.
#   frame_unreadable -- an image arrived and could not be decoded; nothing was seen.
#   recognition_unavailable -- the pipeline could not run (unwarmed model, an exception).
#   nobody_enrolled -- a face may have been seen, but there is no faceprint to compare
#       it with: the household that declined recognition, which is what this is for.
#   no_face -- the camera works and nobody is in shot. Recognition runs ONCE, at startup,
#       and a robot is usually switched on with nobody sitting in front of it, so
#       refusing here would leave a declined-recognition household served by nobody for
#       the whole session for no evidence at all. It says as little as a covered camera.
#   not_confident -- something face-shaped below the detector's floor; no identity was
#       computed, so it points at nobody. Same reasoning as no_face.
#
# REFUSED -- by omission, and each deliberately:
#   several_faces -- two people are in shot. Serving one person's records to a group is
#       exactly the harm, and the robot has just seen that it is a group.
#   too_close_to_call -- a face was compared and resembles more than one enrolled person.
#   no_one_close_enough -- a face was compared against the household and matched NOBODY
#       enrolled: positive evidence the person present is not an enrolled learner, so if
#       the fallback learner is enrolled it is not them.
#   not_recognised -- a face was captured but the comparison faulted (unusable vector,
#       mixed models, wrong dimension). Evidence a person is present, none that it is
#       the configured one; and faults are exactly where a wrong answer hides.
#   store_unreadable -- the robot cannot tell whether anybody else is enrolled, and the
#       resolver's own profile check would fail against the same store anyway.
#   declined_uncalibrated -- REFUSED here, and handled in _fall_back: recognition DID
#       match somebody and withheld it. The fallback is served only when that withheld
#       match IS the configured learner, because then both sources agree; a match to
#       anybody else is the clearest evidence there is that the fallback is wrong.
_FALLBACK_MAY_APPLY: frozenset[str] = frozenset(
    {
        "camera_disabled",
        "no_camera",
        "no_frame",
        "frame_unreadable",
        "recognition_unavailable",
        "nobody_enrolled",
        "no_face",
        "not_confident",
    }
)


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


def _configured_fallback(instance_path: Any) -> str | None:
    """Return the learner an operator configured to serve when recognition cannot.

    WHERE IT COMES FROM, AND WHY THAT IS THE SAFE PLACE. An instance-local settings
    file, written by an operator with a shell on the device (`enrol
    --serve-when-unrecognised`); no /rpc method and no settings-UI control writes it. The
    conversation cannot reach it: no tool takes a path, no tool takes an identity,
    PERMITTED_TOOL_PARAMETERS contains nothing identity-shaped, and no tool imports
    this module. The value is read once, at startup, in the resolver -- never at
    conversation time, because an identity a running conversation can influence is
    not an identity boundary.

    Never raises. An unreadable or malformed settings file must not brick a robot in
    somebody's home; it means no fallback, which serves nobody.
    """
    try:
        from reachy_language_tutor.startup_settings import read_startup_settings

        return read_startup_settings(instance_path).fallback_learner
    except Exception as exc:
        # The type only. This value IS a learner id.
        logger.warning("Could not read the configured fallback learner: %s", type(exc).__name__)
        return None


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
        outcome, withheld = _recognise(media=media, camera_enabled=camera_enabled, instance_path=instance_path)
        if outcome.learner_id is not None:
            return outcome
        return _fall_back(outcome, withheld_match=withheld, instance_path=instance_path)
    except Exception as exc:
        # THE CLAIM ABOVE, MADE TRUE. It said "never raises" and the function had no
        # try/except at all -- startup survived only because main's resolver wraps
        # the call, so the posture was the caller's property and not this one's, and
        # the test that appeared to cover it passed for the caller's reason. A second
        # caller written on the strength of that docstring would have inherited an
        # unguarded raise. The type only, never a value: this module handles a
        # person's identity.
        logger.warning("Recognition failed and the app is serving nobody: %s", type(exc).__name__)
        return _fall_back(
            RecognitionOutcome(None, "recognition_unavailable"), withheld_match=None, instance_path=instance_path
        )


def _fall_back(
    answered_nobody: RecognitionOutcome, *, withheld_match: str | None, instance_path: Any
) -> RecognitionOutcome:
    """Serve the configured learner when recognition learned nothing, or pass the answer on.

    WHICH "NOBODY" ANSWERS QUALIFY is _FALLBACK_MAY_APPLY, an allow-list with a reason
    written beside every entry. `withheld_match` is the learner a declined_uncalibrated
    match named, carried here beside the outcome and never on it, so it cannot reach the
    outcome's repr, a log line, or a caller -- it is compared and dropped.

    ONE SEAM FOR EVERY NOBODY. Recognition has a dozen ways to answer nobody -- no
    camera, no face, several faces, a store it could not read, an exception -- and a
    household whose camera is covered deserves the same answer as one whose robot is
    in shadow. Putting this above _recognise rather than inside it means a branch
    added there is decided here by construction rather than by remembering -- and
    "decided" now means refused unless _FALLBACK_MAY_APPLY names it.

    WHAT THIS IS WORTH, STATED PLAINLY BECAUSE IT IS WEAKER THAN WHAT IT REPLACES.
    It is a configuration, not authentication, and no confirmation step could make it
    one. It does not establish who is in front of the robot; it says who the operator
    decided to serve when the robot cannot tell. In a one-person household that is
    exactly right and costs nothing, because there is no other profile to reach. In a
    household with more than one learner it is WRONG by construction: whoever sits
    down is served the configured person's lessons and progress. The app cannot detect
    that in general -- a covered camera looks the same in every home -- but where the
    camera DID see two people, or a face it could tie to somebody else, it refuses.
    Such a household still needs recognition, and this setting left unset.

    WHAT IT DOES NOT PROTECT AGAINST, so nobody mistakes the disposition for a check:
    it does not prove presence, it does not prove consent at the moment of use, and
    it does not distinguish two members of the same family. It protects exactly one
    thing -- that the identity did not come from anything anybody SAID -- because the
    value is a file on disk and no tool takes a path or an identity.

    AND THE PRECISION MATTERS IN A PARAGRAPH ABOUT NOT OVERCLAIMING. That holds for
    every tool in this repository, checked over the whole tool import closure by
    test. It is a CI-time check over merged code, not a runtime one: the allow-list
    lives in the test suite, nothing validates a tool's parameters as it loads, and a
    tool loaded from config.TOOLS_DIRECTORY or a remote Space is outside what any of
    it has seen. So the honest form is "no tool in this repository can reach it",
    which is what was meant and not what an earlier draft said.

    The one thing this never does is invent an identity: with nothing configured, the
    original answer passes through untouched, id and disposition both.
    """
    # NOT GATED ON THRESHOLD_CALIBRATED, and the first version of this function was.
    # The reasoning that put it there sounds right and is wrong. That gate exists
    # because an unmeasured threshold cannot be trusted to tell two people APART, so
    # it withholds a match that might be the wrong person. This path makes no match:
    # it serves whoever an operator named, and no threshold participates. Gating it
    # would have made the feature dead on arrival -- the flag is false today -- and
    # the household this exists for is precisely the one that declined face
    # recognition and will never flip it. Requiring a face-matching measurement
    # before a household with no faces can use the robot is the opposite of the
    # point.
    #
    # What stands in for the gate is that nothing here is automatic: unset, this
    # returns the original answer and the app serves nobody. Somebody with the device
    # has to name a learner, and is told at that moment what it does not protect.
    configured = _configured_fallback(instance_path)
    if configured is None:
        return answered_nobody

    permitted = answered_nobody.disposition in _FALLBACK_MAY_APPLY or (
        answered_nobody.disposition == "declined_uncalibrated" and withheld_match == configured
    )
    if not permitted:
        # WARNING, and it names the disposition and nobody. An operator who configured a
        # fallback and sees the robot serve nobody deserves to be told it was refused on
        # evidence, not that the setting failed to load.
        logger.warning(
            "Recognition answered nobody (%s) with evidence that the configured fallback may "
            "not be who is present, so the fallback was not served and the app is serving nobody.",
            answered_nobody.disposition,
        )
        return answered_nobody

    # WARNING, not INFO, and it says which path won. An operator reading a log must
    # never mistake a configured fallback for a recognition. The disposition carries
    # it too, so the distinction survives into anything that switches on the outcome
    # rather than living only in prose. No id: this line is about a person.
    logger.warning(
        "Recognition answered nobody (%s), so the app is serving the learner configured "
        "as the fallback. This is a setting, not a recognition: it does not establish "
        "who is present.",
        answered_nobody.disposition,
    )
    return RecognitionOutcome(configured, "configured_fallback")


def _recognise(
    *, media: Any | None, camera_enabled: bool, instance_path: Any
) -> tuple[RecognitionOutcome, str | None]:
    """Run the pipeline. Wrapped by recognise_current_learner, which absorbs.

    Returns the outcome and, beside it, the learner a match named when the calibration
    gate withheld it -- None on every other path. A pair rather than a field on the
    outcome, deliberately: RecognitionOutcome reaches logs and callers, and an id that is
    not being served has no business travelling with it. Only _fall_back reads it.
    """
    override = _development_override()
    if override is not None:
        logger.warning(
            "A development override is setting who the app is serving; recognition was not consulted. "
            "Unset %s to use recognition.",
            DEV_CURRENT_LEARNER_ENV,
        )
        return RecognitionOutcome(override, "override"), None

    shot = capture_frame(media, camera_enabled=camera_enabled)
    if not shot.usable:
        return RecognitionOutcome(None, _FROM_CAPTURE.get(shot.reason, "not_recognised")), None

    described = describe_face(shot.frame)
    if not described.usable:
        return RecognitionOutcome(None, _FROM_EMBEDDING.get(described.reason, "not_recognised")), None

    household = get_enrolled_faceprints(instance_path=instance_path)
    if household is None:
        return RecognitionOutcome(None, "store_unreadable"), None
    if not household:
        return RecognitionOutcome(None, "nobody_enrolled"), None

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
        return RecognitionOutcome(None, _FROM_MATCH.get(outcome.reason, "not_recognised")), None

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
        return RecognitionOutcome(None, "declined_uncalibrated"), outcome.learner_id

    return RecognitionOutcome(outcome.learner_id, "identified"), None
