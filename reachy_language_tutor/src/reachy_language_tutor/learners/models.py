"""The learner data contract: what every implementation of the store returns.

Plain frozen dataclasses only -- no storage engine, no transport, and deliberately
no query text of any kind. A later hosted-backend implementation builds these same
types out of JSON, so callers never have to change when the storage moves.

Describe queries in prose here, never by quoting them: a verification step asserts
that query text lives only in store.py, and it reads comments and docstrings too.
"""

from __future__ import annotations
from dataclasses import dataclass
from collections.abc import Sequence


# The only outcomes an attempt may carry. Mirrored by a constraint in schema.sql;
# a test pins the two together so they cannot drift apart.
OUTCOMES: tuple[str, ...] = ("completed", "partial", "skipped")

# Why an attempt was not recorded. Machine codes, not prose: a caller switches on
# these, and the tutor turns them into something it can say out loud.
RECORD_REASONS: tuple[str, ...] = (
    "unknown_learner",
    "unknown_lesson",
    "invalid_outcome",
    "invalid_score",
    "invalid_recorded_at",
    "rejected_by_database",
    "storage_unavailable",
)

# Why a faceprint was not stored. Same shape and same purpose as RECORD_REASONS above,
# and deliberately a separate vocabulary: the two writers refuse for different reasons,
# and one list covering both would let a caller switch on a code its writer can never
# return. A test walks every published vocabulary against the reasons its writer can
# actually produce, so a code added here without a writer -- or a writer returning one
# that is not here -- fails rather than reaching a household.
FACEPRINT_REASONS: tuple[str, ...] = (
    "unknown_learner",
    "invalid_model",
    "invalid_vector",
    "no_consent",
    "rejected_by_database",
    "storage_unavailable",
)

# What a household member can be asked to agree to. An allow-list rather than free
# text on purpose: widening what a stored yes covers is a decision somebody should
# have to make deliberately, not a string a caller can invent. This is the second
# such decision, taken deliberately and recorded here.
#
# "local_profile" is agreeing to the robot keeping a learning record on this device
# and nothing more -- no face data, no camera. It exists because a household that
# declines face recognition could not otherwise be in the database AT ALL: the only
# way to create a learner is record_consent, and the only thing to consent to was
# face recognition, so declining meant having no profile and no way to use the app.
# That is the opposite of what an opt-in is for.
#
# The two scopes are not interchangeable and the database enforces it rather than a
# caller remembering: the faceprint insert selects from consents filtered to
# face_recognition, so somebody who agreed only to a learning record cannot be given
# a faceprint. Measured in tests/test_identity_fallback.py rather than asserted here.
#
# AND THERE IS NO UPGRADE PATH, which two earlier versions of this comment both got
# wrong -- the first inventing a remedy that does not exist, the second inventing a
# mechanism that does not fire. Measured, and this is what actually happens:
#
#   * record_consent is the only way to write a consent row, and it always inserts a
#     learner too. Passing an EXISTING learner_id is refused -- UNIQUE constraint
#     failed: learners.id, reported as rejected_by_database.
#   * Omitting the id does NOT fail. It succeeds with a fresh uuid4, creating a
#     SECOND household member with the same display name, silently. That is the
#     hazard worth writing down: the system does not refuse a duplicate person, it
#     makes one.
#   * Either way the original learner still holds no face_recognition row, so
#     save_faceprint keeps answering no_consent for them.
#
# So adding face recognition later means registering somebody again, and their
# history does not come with them. A real limitation, and the next person to need it
# should find it here rather than discover it.
CONSENT_SCOPES: tuple[str, ...] = ("face_recognition", "local_profile")

# THE WORDING FOR local_profile, and it is a separate notice rather than a reuse of
# the face one. Showing somebody the face-recognition notice and then storing their
# yes under a different scope would record that they agreed to something they were
# never shown -- and the stored statement_text is the evidence of what each person
# was actually promised, which makes that a false record rather than a shortcut.
#
# Deliberately short, because it covers deliberately little. It makes no promise
# about faces, cameras or recognition: this scope is the absence of those.
LOCAL_PROFILE_STATEMENT_ID: str = "local_profile.v1"

# THE SIBLING PROTECTION, extended with the scope rather than left behind. The face
# notice is pinned by digest in faces/enrollment.py because under its v2 the words
# changed twice and only a review noticed -- a stored statement_text is the evidence
# of what a person was promised, so an id naming two different texts cannot answer
# who agreed to what. This notice is stored on exactly the same terms and was
# initially given no such pin, which a review caught.
#
# APPEND-ONLY, like the other: while the wording is still being written, editing in
# place and updating the digest is correct, and this is what makes such an edit
# visible. Once a household has actually agreed under an id, add a NEW id instead.
LOCAL_PROFILE_STATEMENT_DIGESTS: dict[str, str] = {
    "local_profile.v1": "cc854bdbe08d35b5aa560a8fae47ba3e018b18117a9d685ed70e23a2c0819a07",
}

LOCAL_PROFILE_STATEMENT: str = """This robot can keep a record of your language learning on this device, so it knows which lessons you have done and what to teach next.

If you agree, it saves the name you are being registered under, today's date, these words you are reading now, and whether you agreed yourself or an adult agreed for you. It does not save the name of the adult.

It does NOT use the camera and does NOT keep any face data. This robot will not recognise you: whoever sets it up chooses who it is serving.

The record stays on this robot. You can ask whoever set this robot up to show you what is stored, or to delete all of it, and it is gone."""


# Who did the agreeing, as a ROLE and never a name. "an_adult_of_the_household" is the
# prototype's whole answer to parental consent: an adult was standing at the robot and
# said yes for a child. Which adult is deliberately not recorded -- they are not a
# learner here, and naming them would store personal data about somebody who was never
# asked. Nothing verifies the claim; physical presence at the robot is the control.
CONSENT_GRANTED_BY: tuple[str, ...] = ("the_person_themselves", "an_adult_of_the_household")

# How the yes was obtained, and this is the security claim rather than a description:
# an operator, in person, at this robot. There is no conversational path -- the model
# can no more create a consent than it can create a learner -- and no network path,
# because /rpc refuses every writer on it.
CONSENT_GRANTED_VIA: tuple[str, ...] = ("operator_at_the_robot",)

# Why a consent could not be recorded. Same shape as FACEPRINT_REASONS, and the same
# rule: a caller switches on these and never on a message.
CONSENT_REASONS: tuple[str, ...] = (
    "name_not_usable",
    "invalid_scope",
    "invalid_statement",
    "invalid_granted_by",
    "invalid_granted_via",
    "rejected_by_database",
    "storage_unavailable",
)

# The only kinds of drill a lesson may carry, and what each one is for:
#
#   repetition   -- the tutor says the target, the learner repeats it, and the gloss
#                   says what it means.
#   cue_response -- the tutor says the cue, the learner answers, and the expected
#                   response is the right answer. This is the checkable one.
#
# Mirrored by a constraint in schema.sql; a test pins the two together so they cannot
# drift apart, the same way OUTCOMES is pinned.
DRILL_KINDS: tuple[str, ...] = ("repetition", "cue_response")

# Where a lesson's content came from. "written_for_this_app" is original material and
# cites no page; "converted_from_course" came out of a published course and cites its
# module, unit and page, so a suspect line can be checked against the source. Mirrored
# by a constraint in schema.sql and pinned by a test, as DRILL_KINDS is.
LESSON_ORIGINS: tuple[str, ...] = ("written_for_this_app", "converted_from_course")


@dataclass(frozen=True)
class LearnerProfile:
    """One person who practises on this robot."""

    id: str
    display_name: str
    created_at: int


@dataclass(frozen=True)
class Lesson:
    """One lesson in a language's ordered catalog."""

    id: str
    language_code: str
    position: int
    title: str
    objective: str


@dataclass(frozen=True)
class LessonSource:
    """Where a lesson's content came from, precisely enough to go and check it.

    A lesson written for this app cites a course and nothing else, because it has no
    page to cite. One converted from a published course carries all four, so a line
    somebody doubts can be found on the page it was read off -- which is the whole
    reason this record exists, given that the sources are scans and the conversion is
    fallible. `origin` says which of the two a caller is holding; the fields that do
    not apply are None rather than an invented zero.
    """

    lesson_id: str
    origin: str
    course: str
    module: str | None
    unit: str | None
    page: int | None


@dataclass(frozen=True)
class DialogueTurn:
    """One turn of a lesson's dialogue: who speaks, when, and what they say.

    `speaker` is a label out of the source material and never a learner -- a dialogue
    is the same for every household.
    """

    position: int
    speaker: str
    text: str


@dataclass(frozen=True)
class UsageNote:
    """One numbered note on the dialogue, in English.

    The number is the source's own, which is what somebody checking against the page
    needs; it is also the order.
    """

    number: int
    text: str


@dataclass(frozen=True)
class Drill:
    """One drill, of one kind, at one place in the lesson.

    Which fields carry a value follows from `kind`, and the database refuses any other
    combination:

    * a repetition drill fills `target_text` and `english_gloss` -- the term to say and
      what it means, kept apart because the tutor does different things with them;
    * a cue-response drill fills `cue` and `expected_response` -- what the learner
      hears and the answer that is right, which is what makes this kind checkable
      rather than only sayable.
    """

    position: int
    kind: str
    target_text: str | None
    english_gloss: str | None
    cue: str | None
    expected_response: str | None


@dataclass(frozen=True)
class LessonContent:
    """Everything a lesson is made of, gathered in one value.

    Composed rather than fetched piece by piece, for the same reason LanguageProgress
    is: a hosted backend answers this with one request, and the robot's link is not
    free.

    Every part is optional and empty is a real answer. The seeded catalog carries no
    content at all yet, so a lesson with nothing but a title reads back as this value
    with empty tuples -- not as an error, and not as a missing lesson.
    """

    lesson: Lesson
    source: LessonSource | None
    dialogue_title: str | None
    turns: tuple[DialogueTurn, ...]
    notes: tuple[UsageNote, ...]
    drills: tuple[Drill, ...]


@dataclass(frozen=True)
class LessonAttempt:
    """One recorded attempt at a lesson."""

    learner_id: str
    lesson_id: str
    outcome: str
    score: int | None
    recorded_at: int


@dataclass(frozen=True)
class PractisedLanguage:
    """One language a learner has actually worked on, with how far they have got.

    Distinguishes "started but has finished nothing" from "never touched" -- the tutor
    says something different about each, and a learner who has only partial attempts
    still belongs in the list.
    """

    code: str
    name: str
    attempts: int
    completed: int


@dataclass(frozen=True)
class CatalogLanguage:
    """One language this robot teaches, independent of any learner.

    Shared reference data, not personal data: the same tuple comes back for everyone.
    A caller holding the whole catalog can say "I do not teach that" from evidence --
    seeing the list and not finding the language in it -- rather than inferring it
    from a lookup that answers None for three different reasons.

    `has_material` carries the second fact a caller needs and could not previously
    get: whether any lesson in this language has anything written in it. A language
    can be taught, have a full syllabus of lessons, and still have nothing to teach
    from -- which was true of four of the five languages here and stays true of three
    of them. Offering such a language beside one that has content states something
    false, so the distinction travels with the language rather than being rediscovered
    by whoever lists it.

    It is DERIVED from the content tables on every read, never stored and never
    configured. A conversion landing makes it flip on its own, with no code change and
    nothing to remember to update -- which is the only version of this that survives
    conversions arriving one at a time.
    """

    code: str
    name: str
    has_material: bool


def split_catalog_by_material(
    catalog: Sequence[CatalogLanguage],
) -> tuple[list[str], list[str]]:
    """Split a catalog into the language NAMES that can be taught and those that cannot.

    One definition, because there were six. Every tool that tells the model which
    languages are ready wrote `[entry.name for entry in catalog if entry.has_material]`
    out by hand -- get_progress, start_lesson twice, get_lesson_content, and now
    get_profile -- and the review of D33 named that as the drift surface it is: they
    agree today by copy, so changing what "ready" means reaches one of them and leaves
    the others stating the opposite about the same language. The field names the tools
    return are the two halves of this pair, so the names travel with the derivation.

    Order is the catalog's own, which `get_language_catalog` sorts by name in SQL, so
    what the tutor reads aloud is stable rather than whatever the rows happened to be.

    An empty catalog yields two empty lists, and that is NOT a claim that no language
    has material: `get_language_catalog` returns empty both for a store it could not
    read and for a catalog with no rows, and its contract is that a caller must treat
    those the same, because neither supports telling a person which languages are
    taught. Splitting nothing cannot invent that distinction -- each caller still has
    to decide what to do with an empty catalog, and the callers that can refuse, do.
    """
    with_material = [entry.name for entry in catalog if entry.has_material]
    without_material = [entry.name for entry in catalog if not entry.has_material]
    return with_material, without_material


@dataclass(frozen=True)
class LanguageProgress:
    """A learner's standing in one language: what is done, what is left, what is next.

    Composed as a single value rather than several lookups, because a hosted backend
    answers this with one request and the robot's link is not free.
    """

    learner_id: str
    language_code: str
    language_name: str
    completed: tuple[Lesson, ...]
    remaining: tuple[Lesson, ...]
    next_lesson: Lesson | None
    attempts: tuple[LessonAttempt, ...]


@dataclass(frozen=True)
class RecordResultOutcome:
    """What happened when an attempt was recorded.

    Returned rather than raised, so that a storage failure has somewhere to land that
    already exists when this moves behind the network.
    """

    recorded: bool
    reason: str | None = None
    attempt: LessonAttempt | None = None


@dataclass(frozen=True)
class Faceprint:
    """One person's face, as numbers and nothing else.

    Numeric face data only: no image, no crop, no thumbnail, and no path to a file on
    disk. docs/plan.md makes that promise to the households this runs in, and the shape
    of this type is part of how it is kept -- there is no field here that could carry a
    picture, and the store has no entry point that accepts bytes.

    The vector means nothing without the model that produced it. Numbers from one
    embedding model are not comparable with numbers from another, so `embedding_model`
    travels with them and a reader that ignores it will happily match the wrong person.
    `dimension` is how many numbers there are, and it always equals len(vector).
    """

    learner_id: str
    embedding_model: str
    dimension: int
    vector: tuple[float, ...]
    created_at: int


@dataclass(frozen=True)
class SaveFaceprintOutcome:
    """What happened when a faceprint was stored.

    Returned rather than raised, for the reason RecordResultOutcome gives: a storage
    failure needs somewhere to land that already exists when this moves behind the
    network. `reason` is one of FACEPRINT_REASONS when `saved` is False, and None when
    it is True.
    """

    saved: bool
    reason: str | None = None
    faceprint: Faceprint | None = None


@dataclass(frozen=True)
class ConsentRecord:
    """One act of permission, in a form a person can read back later.

    Who, to what, and when -- which is what the household is owed and what an auditor
    would ask for. `statement_text` is the wording as it stood on the day, stored
    rather than looked up: an id alone would let a later edit to the constant in
    faces/enrollment.py silently rewrite what somebody was told, and the record would
    then answer a question about today instead of about that day.

    `granted_by` is a role from CONSENT_GRANTED_BY, never a second person's name.
    `withdrawn_at` is None while the permission stands; the faceprint writer refuses
    once it is set.
    """

    id: int
    learner_id: str
    scope: str
    statement_id: str
    statement_text: str
    granted_by: str
    granted_via: str
    granted_at: int
    withdrawn_at: int | None = None


@dataclass(frozen=True)
class ConsentOutcome:
    """What happened when consent was recorded, and the learner it created.

    Returned rather than raised, like every other outcome here. `reason` is one of
    CONSENT_REASONS when `recorded` is False, and None when it is True.

    `learner_id` is set only on success, and it is the whole handle the caller gets:
    enrolment needs it to store a faceprint, and there is no other public way to
    obtain one for a person who did not exist a moment ago.
    """

    recorded: bool
    reason: str | None = None
    learner_id: str | None = None
    consent: ConsentRecord | None = None
