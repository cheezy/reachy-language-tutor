"""Learner profiles, the lesson catalog, and recorded lesson results.

This package is the boundary. Import the learner interface from here, never from the
storage module underneath it -- what `__all__` names is the whole vocabulary a caller
needs, and it is what a future hosted backend has to provide.
"""

from reachy_language_tutor.learners.store import (
    get_lesson,
    get_profile,
    get_progress,
    get_faceprint,
    record_result,
    save_faceprint,
    delete_faceprint,
    get_lesson_content,
    store_is_available,
    get_language_catalog,
    get_practised_languages,
)
from reachy_language_tutor.learners.models import (
    OUTCOMES,
    DRILL_KINDS,
    LESSON_ORIGINS,
    RECORD_REASONS,
    FACEPRINT_REASONS,
    Drill,
    Lesson,
    Faceprint,
    UsageNote,
    DialogueTurn,
    LessonSource,
    LessonAttempt,
    LessonContent,
    LearnerProfile,
    CatalogLanguage,
    LanguageProgress,
    PractisedLanguage,
    RecordResultOutcome,
    SaveFaceprintOutcome,
)


__all__ = [
    "CatalogLanguage",
    "DRILL_KINDS",
    "DialogueTurn",
    "Drill",
    "FACEPRINT_REASONS",
    "Faceprint",
    "LESSON_ORIGINS",
    "LanguageProgress",
    "LearnerProfile",
    "Lesson",
    "LessonAttempt",
    "LessonContent",
    "LessonSource",
    "OUTCOMES",
    "PractisedLanguage",
    "RECORD_REASONS",
    "RecordResultOutcome",
    "SaveFaceprintOutcome",
    "UsageNote",
    "delete_faceprint",
    "get_faceprint",
    "get_language_catalog",
    "get_lesson",
    "get_lesson_content",
    "get_practised_languages",
    "get_profile",
    "get_progress",
    "record_result",
    "save_faceprint",
    "store_is_available",
]
