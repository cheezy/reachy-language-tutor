"""The learner data contract: what every implementation of the store returns.

Plain frozen dataclasses only -- no storage engine, no transport, and deliberately
no query text of any kind. A later hosted-backend implementation builds these same
types out of JSON, so callers never have to change when the storage moves.

Describe queries in prose here, never by quoting them: a verification step asserts
that query text lives only in store.py, and it reads comments and docstrings too.
"""

from __future__ import annotations
from dataclasses import dataclass


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
