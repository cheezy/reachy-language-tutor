"""Learner profiles, the lesson catalog, and recorded lesson results.

This package is the boundary. Import the learner interface from here, never from the
storage module underneath it -- what `__all__` names is the whole vocabulary a caller
needs, and it is what a future hosted backend has to provide.
"""

from reachy_language_tutor.learners.store import get_profile, get_progress, record_result, store_is_available
from reachy_language_tutor.learners.models import (
    OUTCOMES,
    RECORD_REASONS,
    Lesson,
    LessonAttempt,
    LearnerProfile,
    LanguageProgress,
    RecordResultOutcome,
)


__all__ = [
    "LanguageProgress",
    "LearnerProfile",
    "LessonAttempt",
    "Lesson",
    "OUTCOMES",
    "RECORD_REASONS",
    "RecordResultOutcome",
    "get_profile",
    "get_progress",
    "record_result",
    "store_is_available",
]
