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
    "rejected_by_database",
    "storage_unavailable",
)


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
class LessonAttempt:
    """One recorded attempt at a lesson."""

    learner_id: str
    lesson_id: str
    outcome: str
    score: int | None
    recorded_at: int


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
