"""Take somebody from "not known to this robot" to "has a faceprint", consent first.

CONSENT IS RECORDED BEFORE THE FACEPRINT IS COMPUTED, and the ordering is not a
convention this module observes -- it is the only order the pieces allow. There is no
public way to create a learner except `learners.record_consent`, which writes the
learner row and the consent row in one transaction, and the faceprint writer draws its
rows from the consents table, so a learner with no standing consent produces nothing to
insert. Put together: whatever moment this flow is interrupted at, what it leaves
behind is a person who agreed and has no faceprint, never a faceprint nobody agreed to.
`enrol` reads top to bottom in that order too, the way start_lesson.py pins a session
before it reports one started -- but the position is the second guarantee here, not the
only one.

ENROLMENT IS AN OPERATOR ACTION AND NOT A CONVERSATION TOOL. This project's central
boundary is that the model never supplies an identity: tools take no learner parameter,
and the permitted-parameter allow-list deliberately contains nothing identity-shaped. A
tool that could CREATE an identity is the same breach one step earlier, so nothing here
is registered as a tool, nothing in `tools/` imports this module, and tests assert both
by reading the import graph rather than by trusting this paragraph. It is not reachable
over `/rpc` either: that surface is LAN-reachable and cannot be authenticated, and the
project's own tests already refuse every writer on it.

WHO MAY CONSENT FOR A CHILD -- decided, and the decision is recorded rather than
assumed. Enrolment happens in person at the robot, and whoever runs it must state which
of two roles applies, every time, with no default: the person themselves, or an adult of
the household. For a child the answer is the second. What that does NOT establish is
written down here and in docs/learner-database.md instead of being left to be
discovered: nothing verifies that the person asserting adulthood is one, physical
presence at the robot is the whole control, which adult is deliberately not recorded
because they are not a learner here, and `learners` has no age field so the robot cannot
tell who is a child. The legal question is open and docs/plan.md already says to consult
a privacy lawyer before any public launch.

QUALITY MATTERS MORE THAN QUANTITY. A faceprint taken from one bad frame produces a
recogniser that fails for that person forever, and the person it fails for cannot debug
it. So this collects several frames, requires them to agree with each other by the same
floor the matcher will later judge them against, and stores the most representative one
rather than an average -- an averaged vector is not an embedding the model ever
produced, and nothing guarantees it sits anywhere sensible.

NOTHING HERE WRITES AN IMAGE ANYWHERE. Not a frame, not a crop, not a thumbnail, not to
a log, not temporarily while debugging. Frames are borrowed from a media handle this
module does not own, turned into numbers, and dropped.

ONE THING THIS DELIBERATELY DOES NOT DO: it does not check whether somebody is already
enrolled. Finding out would need a read across every learner in the household, which
the store's scoping rule refuses by design, and adding a cross-learner reader is a
security decision that deserves its own task rather than a line in this one. The
consequence is bounded and tested: enrolling the same person twice creates two learners,
and the matcher then answers `too_close_to_call` rather than guessing between them --
a refusal, which is the safe direction to fail in.
"""

from __future__ import annotations
import sys
import time
import uuid
import logging
from typing import Any
from dataclasses import dataclass

from reachy_language_tutor.learners import (
    CONSENT_GRANTED_BY,
    ConsentOutcome,
    forget_learner,
    record_consent,
    save_faceprint,
)
from reachy_language_tutor.faces.capture import capture_frame
from reachy_language_tutor.faces.matching import FACE_MATCH_SIMILARITY_FLOOR, faceprint_similarity
from reachy_language_tutor.faces.embedding import EMBEDDING_MODEL_ID, describe_face, warm_face_models


logger = logging.getLogger(__name__)


# What this app asks somebody to agree to, and the id it is stored under.
#
# The scope is a machine code shared with the store, which requires exactly this value
# before it will hold a faceprint. The id is versioned because the words below will be
# improved: a stored consent keeps the text it was given, so an old row goes on saying
# what that person was actually told. Bump the id when the words change.
CONSENT_SCOPE: str = "face_recognition"
CONSENT_STATEMENT_ID: str = "face_recognition.v3"

# The digest of the wording this id names, and WHEN A BUMP IS OWED, stated precisely
# because "bump the id when the words change" is the wrong rule during development --
# followed literally it would have produced a dozen versions nobody ever saw.
#
# The id exists so that "who agreed to face_recognition.v3?" has one answer. That
# question can only be asked about a consent row, so the bump is owed the moment the
# words change AFTER any real consent has been recorded under this id. Before that --
# while the wording is still being written, as it is now -- editing in place is
# correct, and this digest is what keeps such an edit visible rather than silent: the
# suite fails and whoever changed a sentence has to look at it and decide.
#
# That is not the rule v2 was edited under. There the rule was a comment, nothing
# enforced it, the words changed twice, and only a review noticed -- which is the
# whole reason this line exists rather than another sentence asking to be remembered.
# APPEND-ONLY. An entry that already exists is never edited -- a test refuses that --
# so the cheapest way to change a sentence is to add a key, which is exactly the
# incentive the rule wants. The previous single-digest form left the path of least
# resistance as "edit the sentence, paste the new digest", which is the habit that
# would be wrong the moment a household has actually consented.
CONSENT_STATEMENT_DIGESTS: dict[str, str] = {
    "face_recognition.v3": "e34bb750b8c4af6f819c28599e65ff744b3d05c77e61431fd10e389cb55a1e32",
}

# THE WORDS THEMSELVES, WRITTEN DOWN ONCE, and this is the point of the constant: what
# a household member is told before they agree is not something a call site improvises.
#
# TWO RULES, not one, and the second was missing until a security review found what it
# cost. (a) A sentence must not be added here unless the same is true of it. (b) A
# thing this enrolment STORES must be named here. Rule (b) is the one Cadillac
# Fairview is cited for: the failure there was consent that was technically obtained
# while the notice understated what was collected. Saying "keep only those numbers"
# while the same transaction also wrote the person's name and, for a child, the fact
# that an adult consented for them, was that failure in miniature.
#
# Every claim here is one this app actually keeps, and each is checkable:
#   "does not keep the pictures"  -- nothing in this package writes an image anywhere,
#                                    and a test asserts the absence rather than trusting it.
#   "your name, today's date"     -- learners.display_name and consents.granted_at.
#   "that an adult agreed"        -- consents.granted_by, stored as a role and never
#                                    as the adult's own name.
#   "the numbers never leave"     -- the faceprints table is local; nothing uploads it,
#                                    and no tool returns a vector to the model.
# THIS PARAGRAPH IS A GENERAL CLAIM AND NOT A LIST, deliberately, and the reason is
# the rule CLAUDE.md states for guards applied one level up to prose. It WAS a list,
# and a list of what leaves is only ever as complete as the last person to enumerate
# it: four separate reviews each found one more item missing from it -- the name, the
# lesson progress, the camera image, then the raw microphone audio and the remembered
# facts. Naming the general truth ("what its microphone hears", "anything it has been
# asked to remember") closes the family the way an allow-list does, and a fifth sink
# added tomorrow is already covered by a sentence nobody has to remember to update.
#
#   "what its microphone hears"   -- huggingface_realtime.py base64-encodes EVERY
#                                    non-empty audio frame and appends it to the
#                                    realtime input buffer; turn detection is
#                                    server-side (ServerVad), which is why the raw
#                                    stream has to go up continuously. "what you say"
#                                    was the wrong claim twice over: it is audio and
#                                    not words, and it is everyone in the room rather
#                                    than the person who agreed.
#   "anything it has been asked to remember" -- prompts.py prepends
#                                    format_memory_for_prompt to the session
#                                    instructions, so up to 60 stored free-text facts
#                                    about the person are re-sent every session.
#   "sometimes it takes a picture" -- NOT "if you ask it to", and the honest state
#                                    of this one is worth writing down exactly,
#                                    because the first version of this comment got
#                                    it wrong in the other direction. tools/camera.py
#                                    exists and ships a JPEG to the endpoint whenever
#                                    it is enabled, and it has no confirmation gate,
#                                    so the person does not decide. But it is NOT in
#                                    the locked profile's default_tools today -- the
#                                    registry the app actually builds does not
#                                    contain it -- so no picture leaves right now.
#                                    The sentence is therefore an OVER-claim, which
#                                    is the safe direction for a consent notice: a
#                                    household is warned about egress that does not
#                                    yet happen, and the wording is already true the
#                                    day somebody enables the tool. The guard fires
#                                    on the tool EXISTING rather than on it being
#                                    enabled, deliberately and for the same reason.
#                                    The same holds for the remembered facts:
#                                    remember/forget are not registered either, which
#                                    is why "anything it HAS BEEN asked to remember"
#                                    is the right wording and must not be tightened.
#   "their decision and not this robot's" -- the earlier wording said the pictures
#                                    were "not kept, here or there". Here is
#                                    checkable and true; THERE is a claim about a
#                                    third party's retention that nothing in this
#                                    repository requests, configures or verifies, and
#                                    the rule is to never write a specific claim that
#                                    was not verified.
#   "it sends that picture"       -- tools/camera.py base64-encodes a live JPEG and
#                                    huggingface_realtime.py posts it to the hosted
#                                    endpoint as an input_image. The sanitizer strips
#                                    b64_im from the TEXT echo only, which is what
#                                    made this easy to miss. A paragraph that opened
#                                    "you should know this before you agree" and then
#                                    named only speech and the name, two paragraphs
#                                    after "it does not keep the pictures", left a
#                                    household to conclude no image of them ever
#                                    leaves the house. The opposite is true.
#   "how your lessons are going"  -- get_profile and get_progress return practised
#                                    languages, attempt counts and lesson progress to
#                                    the same endpoint.
#   "it sends the name it calls you by" -- TRUE, and it is here because the sentence
#                                    that used to stand in this place was false. The
#                                    notice said "nothing is sent anywhere and no copy
#                                    leaves it" of everything it had just listed, the
#                                    name included -- while tools/get_profile.py:109
#                                    returns display_name to the model, the realtime
#                                    sanitizer strips only the camera image, and the
#                                    result is json.dumps'd to a hosted endpoint. A
#                                    household would have agreed against a description
#                                    of the data flow this app does not honour, which
#                                    is the Cadillac Fairview shape exactly. It is not
#                                    yet the ENROLLED person's name that leaves, only
#                                    the hard-coded learner's, because recognition is
#                                    not wired to the current learner yet -- so this
#                                    was a promise that would have come true as a lie
#                                    the day W29 lands, with nothing failing.
#   "not a lock"                  -- there is no liveness check, and faces/__init__ says so.
#   "delete the numbers"          -- the `enrol --forget` command, which calls
#                                    delete_faceprint (it erases and checkpoints the
#                                    WAL). Before that command existed this sentence
#                                    promised something no code could do.
#   "the name and this agreement are kept" -- and this half is the SECOND correction
#                                    to the same paragraph. It first promised deletion
#                                    with no route at all; the route was then added and
#                                    the sentence still said "delete all of it", which
#                                    --forget does not do and main.py's own success
#                                    output contradicts two lines later. Keeping the
#                                    consent row is the right trade -- it is the record
#                                    of what somebody was told -- so the notice says so
#                                    rather than the code being changed to match a
#                                    sentence.
#
# NOTE FOR MILESTONE 5. The claim scoped to the NUMBERS is the one that must keep
# holding: docs/plan.md section 8 plans for names and progress to leave the home, and
# this notice now says the name already does. Faceprints leaving would be a different
# promise broken, so whoever moves them must bump CONSENT_STATEMENT_ID and re-ask
# anybody holding an earlier row -- the stored statement_text is the evidence of what
# each person was actually promised.
# A test asserts every table this flow writes is named here, so rule (b) is checked
# rather than remembered.
#
# Written for a person to understand rather than for a lawyer to approve: short lines,
# no defined terms, and it says what is kept before it says what is not.
CONSENT_STATEMENT: str = """This robot can learn to recognise your face, so it knows whose lessons to teach.

If you agree, it will take a few pictures with its camera and turn them into a list of numbers. It keeps the numbers. It does not keep the pictures, not even for a moment.

Along with the numbers it saves the name you are being enrolled under, today's date, these words you are reading now, and whether you agreed yourself or an adult agreed for you. It does not save the name of the adult.

The numbers never leave this robot. No copy of them is sent anywhere. They can tell you apart from other people in this household. They are not a lock, and they are not proof of who is in the room.

Other things do leave, and you should know this before you agree. While this robot is switched on, it sends what its microphone hears to a language service on the internet -- your voice, and the voice of anyone else in the room. It sends the name it calls you by, how your lessons are going, and anything it has been asked to remember about you. Anything else it looks up about you in order to teach you goes to that service too. Sometimes it takes a picture to see what is in front of it, and that goes to the same service too. This robot does not keep those pictures. What that company does with any of it is their decision and not this robot's.

You can say no now, or stop at any point before it finishes, and nothing is kept. If the robot cannot finish undoing it, it will say so on screen and tell whoever is running it how to remove you.

Afterwards you can ask whoever set this robot up to delete the numbers, and they will be deleted. They will need the learner id this robot shows them when you are enrolled, so ask them to keep it. The name and this agreement are kept, so there is always a record of what you were told and when.

If this is a child, an adult of the household has to agree for them, here, in person."""


# How many agreeing frames a faceprint is built from, and how many attempts it may make
# to get them. Five rather than one because a single frame can catch a blink, a turn or
# a shadow, and the person it then fails for has no way to find out why. Twenty
# attempts because a frame can be missed for reasons that are nobody's fault -- a read
# can simply be early, which capture.py measures at length.
_ENROLMENT_FRAMES = 5
_ENROLMENT_ATTEMPTS = 20

# A pause between attempts, so five frames sample five moments rather than five reads
# of one. It lives here and not in capture.py deliberately: that module imports no
# `time` at all, which is how "it starts no timer" stays a property of the file rather
# than a promise in a comment. This is an operator command, not the voice loop, so a
# second of wall clock costs nobody a turn.
_SECONDS_BETWEEN_ATTEMPTS = 0.15

# Why an enrolment did not finish. Machine codes, same contract as CAPTURE_REASONS and
# EMBEDDING_REASONS -- and the two upstream vocabularies are reused verbatim as keys in
# _REFUSALS below rather than re-spelled, so a new reason upstream fails the pin test in
# this package rather than falling silently into a generic sentence.
ENROLMENT_REASONS: tuple[str, ...] = (
    "name_not_usable",
    "consent_role_not_understood",
    "consent_not_recorded",
    "not_enough_frames",
    "frames_disagree",
    "faceprint_not_stored",
    "rollback_failed",
    "records_unavailable",
)

# One sentence per meaning, the shape both lesson tools use.
#
# REGISTER NOTE, because this is a deliberate departure from those tools: these are read
# by an adult at a console, not spoken to a learner, so they may name a remedy. "Two
# people are in shot" is an instruction the operator can act on; the tutor's refusals
# cannot talk like that because the person hearing them is mid-lesson.
_REFUSALS: dict[str, str] = {
    "name_not_usable": "That is not a name this robot can store. Use the name they go by, in their own script.",
    "consent_role_not_understood": (
        "Say who is giving permission: the person themselves, or an adult of the household."
    ),
    "camera_disabled": "The camera is switched off, so nobody can be enrolled. Restart without --no-camera.",
    "no_camera": "This robot has no camera available, so nobody can be enrolled.",
    "no_frame": "The camera is not producing frames. Check the robot's camera and try again.",
    "recognition_unavailable": "The face models are not loaded, so no faceprint can be made.",
    "no_face": "Nobody is in shot. Ask them to look at the robot, and try again.",
    "several_faces": "More than one person is in shot. Ask everybody else to step out of view.",
    "not_confident": "The camera can see a face but not clearly enough. Try better light, or move closer.",
    "frame_unreadable": "The camera produced something the face models could not read.",
    "not_enough_frames": "Not enough clear views of one face. Ask them to hold still, facing the robot.",
    "frames_disagree": (
        "The views did not agree with each other well enough to be one person. "
        "Check nobody else moved through shot, and try again."
    ),
    "consent_not_recorded": "The agreement could not be saved, so nothing else was done.",
    "faceprint_not_stored": "The agreement was saved but the faceprint was not, so this person has no faceprint.",
    "rollback_failed": (
        "This person was not enrolled, AND the record created for them could not be removed. "
        "They are in the database with an agreement and no faceprint. "
        "Remove them with: enrol --remove <the learner id printed below>."
    ),
    "records_unavailable": "This robot cannot reach its records right now.",
}


@dataclass(frozen=True)
class EnrolmentOutcome:
    """What happened, and who it happened to.

    `learner_id` is set whenever a learner row was created -- which includes some
    failures, because consent is recorded first and a capture can fail after it. When
    `enrolled` is False and `learner_id` is set, the learner was rolled back; the field
    is still returned so a caller can say which enrolment it was talking about.
    """

    enrolled: bool
    reason: str | None = None
    error: str | None = None
    learner_id: str | None = None


def _refused(reason: str, learner_id: str | None = None) -> EnrolmentOutcome:
    """Build the answer for an enrolment that did not happen, naming why in one code."""
    return EnrolmentOutcome(
        enrolled=False,
        reason=reason,
        error=_REFUSALS.get(reason, _REFUSALS["records_unavailable"]),
        learner_id=learner_id,
    )


def consent_statement() -> str:
    """Return the exact words a person must be shown before they are asked to agree.

    A function rather than the bare constant so that every surface asks the same
    question of the same source, and so a test can assert what an operator actually saw
    was this and not something a call site composed.
    """
    return CONSENT_STATEMENT


def record_consent_for_enrolment(
    display_name: str,
    *,
    granted_by: str,
    learner_id: str | None = None,
    instance_path: Any = None,
) -> ConsentOutcome:
    """Phase one: create the person and record what they agreed to. No camera involved.

    Separated from the capture on purpose. It is what makes "interrupted after consent"
    a state a test can produce directly rather than by simulating a failure, and it is
    the half that must survive the other half failing.

    `granted_by` has no default, and that is the whole of this app's answer to who may
    consent for a child: the question is asked every time rather than inherited.
    """
    return record_consent(
        display_name,
        scope=CONSENT_SCOPE,
        statement_id=CONSENT_STATEMENT_ID,
        statement_text=CONSENT_STATEMENT,
        granted_by=granted_by,
        granted_via="operator_at_the_robot",
        learner_id=learner_id,
        instance_path=instance_path,
    )


def _gather_faceprints(media: Any, *, camera_enabled: bool) -> tuple[list[tuple[float, ...]], str | None]:
    """Collect several faceprints of one face, or say what stopped it.

    Returns the prints and None, or whatever was collected and the reason the last
    attempt failed -- the LAST reason rather than the first, because an operator acting
    on it wants to know what is wrong now.
    """
    prints: list[tuple[float, ...]] = []
    last_reason: str | None = None

    for attempt in range(_ENROLMENT_ATTEMPTS):
        if len(prints) >= _ENROLMENT_FRAMES:
            break
        if attempt:
            time.sleep(_SECONDS_BETWEEN_ATTEMPTS)

        shot = capture_frame(media, camera_enabled=camera_enabled)
        if not shot.usable:
            last_reason = shot.reason
            # Neither of these gets better by asking again: the operator turned the
            # camera off, or there is no camera to ask. Retrying twenty times would
            # just make the command slow before saying the same thing.
            if shot.reason in ("camera_disabled", "no_camera"):
                break
            continue

        described = describe_face(shot.frame)
        if not described.usable:
            last_reason = described.reason
            if described.reason == "recognition_unavailable":
                break
            continue

        prints.append(described.vector)
        last_reason = None

    return prints, last_reason


def _medoid(prints: list[tuple[float, ...]]) -> tuple[float, ...] | None:
    """Return the print most like all the others, or None if they do not agree.

    THE MEDOID, NEVER A MEAN. An averaged vector is not an embedding the model ever
    produced, and nothing says the average of several faces of one person lands
    anywhere the model would put that person; the medoid is a real observation of a
    real moment, chosen for being the least eccentric one.

    Agreement is judged by the floor the MATCHER will later use, which is the point
    rather than a convenience: if a person's own frames cannot clear that floor against
    each other, the print stored from them will not clear it against them either, and
    the failure would show up as a household member the robot never recognises.
    """
    scores: list[tuple[float, tuple[float, ...]]] = []
    for index, candidate in enumerate(prints):
        others = [other for position, other in enumerate(prints) if position != index]
        similarities = [faceprint_similarity(candidate, other) for other in others]
        if any(value is None or value < FACE_MATCH_SIMILARITY_FLOOR for value in similarities):
            return None
        scores.append((sum(value for value in similarities if value is not None), candidate))
    if not scores:
        return None
    return max(scores, key=lambda scored: scored[0])[1]


def capture_faceprint(
    learner_id: str,
    media: Any,
    *,
    camera_enabled: bool,
    instance_path: Any = None,
) -> EnrolmentOutcome:
    """Phase two: look at the person and store their faceprint. Consent must already exist.

    It does not verify that it does, and that is deliberate rather than an omission:
    the store refuses a faceprint for a learner with no standing face_recognition
    consent row -- any consent row is NOT enough, and the scope is what the statement
    filters on -- so a check here
    would be a second opinion about a decision that has already been made somewhere it
    cannot be bypassed. If consent is missing this returns `faceprint_not_stored`,
    which is the truth -- nothing was stored.
    """
    # Load the models here rather than relying on start-up having done it. Enrolment
    # is an operator command, not the voice loop: it can afford the first fetch, and
    # the alternative is telling an operator "the face models are not loaded" when the
    # fix is one they cannot see. warm_face_models reports rather than raises, so a
    # robot with no network still gets a sentence it can act on.
    if not warm_face_models():
        return _refused("recognition_unavailable", learner_id)

    prints, reason = _gather_faceprints(media, camera_enabled=camera_enabled)
    if len(prints) < _ENROLMENT_FRAMES:
        # The count and the reason, never the vectors and never the name.
        logger.info(
            "Enrolment gathered %d of %d views before stopping",
            len(prints),
            _ENROLMENT_FRAMES,
        )
        return _refused(reason or "not_enough_frames", learner_id)

    representative = _medoid(prints)
    if representative is None:
        return _refused("frames_disagree", learner_id)

    stored = save_faceprint(learner_id, EMBEDDING_MODEL_ID, representative, instance_path=instance_path)
    if not stored.saved:
        # The store has already logged its own reason, in its own safe form.
        return _refused("faceprint_not_stored", learner_id)

    return EnrolmentOutcome(enrolled=True, learner_id=learner_id)


def _roll_back_left_something(learner_id: str, instance_path: Any) -> bool:
    """Undo the learner an unfinished enrolment created, and say whether it took.

    One function rather than two copies, because there are two ways an enrolment can
    fail after consent -- a refusal it returns, and an interruption it raises -- and
    the rollback has to be identical on both. Writing it twice is how the second one
    drifts.

    Two fixed sentences rather than the count, because the count is the less useful
    half: what a maintainer needs to know is whether the rollback took. Neither line
    carries a name or an id.

    RETURNS whether a row was LEFT BEHIND, and the caller must say so, because a log
    line is the wrong place for this fact to stop. A row left behind means somebody
    is in the database having agreed to something, with no faceprint and no way to
    find out -- and the only person who can do anything about that is the operator
    standing at the robot, who reads the terminal and not the log.

    Named for the answer rather than for the action, because there are THREE
    outcomes and only one of them is worth interrupting an operator about:
      1 -- the row went. Nothing to report.
      0 -- there was no row. That is the interrupt-before-the-commit case, and it is
           a success, not a failure: nothing was written, so nothing is left.
      None -- the store could not be read, so nothing can be promised either way.
    Collapsing 0 and None, as an earlier version did by testing `== 1`, made every
    interrupt that landed before the write print a warning about a row that did not
    exist -- alarming an operator about nothing, which is its own kind of wrong.
    """
    removed = forget_learner(learner_id, instance_path=instance_path)
    if removed == 1:
        logger.info("Enrolment did not finish; the learner it created was rolled back")
        return False
    if removed == 0:
        logger.debug("Enrolment did not finish; there was no learner row to roll back")
        return False
    logger.warning("Enrolment did not finish and the learner it created could not be rolled back")
    return True


def enrol(
    display_name: str,
    media: Any,
    *,
    granted_by: str,
    camera_enabled: bool = True,
    instance_path: Any = None,
) -> EnrolmentOutcome:
    """Run the whole flow: agree first, then look, then store. Refusable at every step.

    THE BODY IS THE ORDERING. Consent is recorded before a camera is touched, and the
    faceprint is stored last -- no flag, no `if consented:`, just position, the way
    start_lesson pins a session before it reports one started. The database enforces
    the same order underneath, so this reads as a statement of intent rather than as
    the only thing standing between a household and an unconsented faceprint.

    A capture that fails after consent removes the learner it created. Leaving them
    would mean somebody is in the database, having agreed to something, with no
    faceprint and no way to know -- which is a worse state than not having started.
    """
    if granted_by not in CONSENT_GRANTED_BY:
        # Refused here as well as in the store, because this is the argument that
        # carries the child question and a caller should meet it at the surface it
        # called rather than as a storage-layer reason code.
        #
        # Its own code, and not name_not_usable: this module's refusals exist to name
        # a remedy an operator can act on, and telling somebody their NAME is wrong
        # when the role argument is what they mis-typed points them at the wrong
        # argument. That is worse than a generic message, not better.
        return _refused("consent_role_not_understood")

    # The consent call is INSIDE the guarded region, not above it. It used to sit
    # outside, and a review reproduced the window against a real database: an
    # interrupt landing after record_consent's COMMIT and before the try began left a
    # learner row and a consent row behind, printed nothing, and named no id -- so
    # both halves of the notice's "stop at any point and nothing is kept" were false,
    # and the remedy it points at was unreachable. Narrow, and not narrow enough to
    # leave in a sentence made to a household.
    # The id is minted HERE, before the try, and handed down. That is what closes the
    # last of the interrupt window: putting record_consent inside the try was not
    # enough, because the rollback still needed `agreed` to be BOUND, and an
    # interrupt landing after the COMMIT but before that assignment -- in the
    # connection close, or building the outcome object -- left a committed learner
    # and consent row that nothing could name. Reproduced against a real database
    # before this change. With the id known up front, every failure after this point
    # can undo the row whether or not the call ever returned.
    learner_id = uuid.uuid4().hex
    try:
        agreed = record_consent_for_enrolment(
            display_name, granted_by=granted_by, learner_id=learner_id, instance_path=instance_path
        )
        if not agreed.recorded or agreed.learner_id is None:
            return _refused("name_not_usable" if agreed.reason == "name_not_usable" else "consent_not_recorded")

        outcome = capture_faceprint(
            agreed.learner_id,
            media,
            camera_enabled=camera_enabled,
            instance_path=instance_path,
        )
    except BaseException:
        # BaseException, not Exception, and this is the sentence it defends:
        # CONSENT_STATEMENT promises "you can stop at any point before it finishes,
        # and nothing is kept". The capture is real wall-clock time -- up to twenty
        # attempts with a pause between them -- and the way a person actually stops it
        # is the operator pressing Ctrl-C, which raises KeyboardInterrupt and is NOT
        # an Exception. Without this the promise was false for exactly the scenario it
        # describes: the learner row and the consent row would stay behind.
        # REPORTED HERE TOO, and the earlier version of this comment was wrong about
        # why it need not be. It claimed there was nowhere to print because the
        # interrupt was on its way up -- but stderr is open inside this block, and
        # Ctrl-C is precisely the case where the operator is watching the terminal,
        # because they are the one who just pressed it. Leaving this silent meant the
        # path the household is MOST likely to take -- somebody changing their mind
        # mid-capture, which is the scenario this handler exists to honour -- was the
        # one path where a failed rollback reached only a log nobody reads.
        # forget_learner answers 0 for a row that was never written, which is the
        # "interrupted before the commit" case and needs no report -- there is
        # nothing to tell anybody about. Only a row that EXISTS and would not go is
        # worth the operator's attention.
        if _roll_back_left_something(learner_id, instance_path):
            print(_REFUSALS["rollback_failed"], file=sys.stderr)
            print(f"  learner id: {learner_id}", file=sys.stderr)
        raise

    if not outcome.enrolled and _roll_back_left_something(learner_id, instance_path):
        # The refusal the operator was about to be shown is no longer the whole truth:
        # somebody is in the database who agreed to something and has no faceprint.
        # That has to reach the terminal, because the operator is the only one who
        # can act on it.
        return _refused("rollback_failed", learner_id)
    return outcome
