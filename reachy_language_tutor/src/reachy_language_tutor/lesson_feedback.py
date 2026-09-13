"""Reactions tied to lesson events the app knows about, rather than ones the model narrates.

The locked profile already asks the model to react during a lesson, and that covers the
conversation. It cannot cover the things the model does not know: whether the result was
actually written, and whether that was the last lesson in a language. Movement driven by
the model is movement driven by something that has not seen the database. This module is
the other half -- a few reactions tied to events the app can vouch for, so the encouraging
nod happens when the result is really saved.

**This module cannot see who the learner is or what they are practising, and that is
structural rather than a matter of care.** `react_to_lesson_event` takes a member of a
closed enum and a movement sink, and nothing else. It is never handed `ToolDependencies`,
which carries the learner id; it is never handed a lesson, a title or an objective. There
is nothing here to leak because there is nothing here to leak from.

It adds no thread, no timer and no polling loop. Choosing a reaction is a dictionary
lookup, and `MovementManager.queue_move` hands the move to the worker thread that is
already running. The robot's onboard computer is small, and this runs on it.
"""

import logging
from enum import Enum
from typing import TYPE_CHECKING, Any

from reachy_language_tutor.tools import play_emotion


if TYPE_CHECKING:
    from reachy_language_tutor.dance_emotion_moves import EmotionQueueMove


logger = logging.getLogger(__name__)

# The same guard play_emotion.py carries, and for the same reason: the emotion library is
# an optional dependency, and a robot without it must still teach and still record. It is
# deliberately a separate guard rather than a read of play_emotion's, so the two modules
# fail independently.
try:
    from reachy_language_tutor.dance_emotion_moves import EmotionQueueMove

    EMOTION_AVAILABLE = True
except Exception as e:  # pragma: no cover - exercised by monkeypatching the flag
    logger.warning(f"Emotion library not available: {e}")
    EMOTION_AVAILABLE = False


class LessonEvent(Enum):
    """Every lesson event this app reacts to. Closed, and closed on purpose.

    The whole permitted vocabulary, named as what IS an event rather than as a list of
    things that are not one. A learner id, a lesson title and a stray string are all
    equally not members, so none of them can be mistaken for one -- which is the
    difference between a rule that covers the cases somebody thought of and a rule that
    covers the family. CLAUDE.md records four defects from getting that the other way
    round.
    """

    LESSON_STARTED = "lesson_started"
    RESULT_RECORDED_COMPLETE = "result_recorded_complete"
    RESULT_RECORDED_SET_ASIDE = "result_recorded_set_aside"
    LANGUAGE_FINISHED = "language_finished"


# One intent per event, every one of them a word play_emotion already knows. A test pins
# this against EMOTION_INTENTS, so inventing a name here fails in the suite rather than
# resolving to nothing on a robot.
EVENT_INTENTS: dict[LessonEvent, str] = {
    # "I am with you now" -- the same feeling the profile already asks for while
    # listening. Greeting somebody the robot greeted two minutes ago would be worse.
    LessonEvent.LESSON_STARTED: "attentive",
    # The nod, and the only one tied to a write that really happened.
    LessonEvent.RESULT_RECORDED_COMPLETE: "success",
    # Acknowledgement, not consolation. A robot looking hurt because a child stopped
    # early is the wrong thing in somebody's home; this says "got it, it is saved".
    LessonEvent.RESULT_RECORDED_SET_ASIDE: "yes_understanding",
    # The only big one, and it can happen at most once per language per learner.
    LessonEvent.LANGUAGE_FINISHED: "excited",
}

# Which event a recorded outcome is, keyed by the store's own vocabulary. A test pins the
# keys against OUTCOMES, so a new outcome word upstream fails here rather than quietly
# collecting the set-aside reaction.
_OUTCOME_EVENTS: dict[str, LessonEvent] = {
    "completed": LessonEvent.RESULT_RECORDED_COMPLETE,
    "partial": LessonEvent.RESULT_RECORDED_SET_ASIDE,
    "skipped": LessonEvent.RESULT_RECORDED_SET_ASIDE,
}


def event_for_recorded_result(outcome: object, *, more_lessons_remain: bool | None) -> LessonEvent | None:
    """Name the event a written result is, or nothing when it is not one this app reacts to.

    `outcome` is the store's own word -- "completed", "partial" or "skipped" -- which says
    how the practice went and nothing about who did it.

    `more_lessons_remain` is THREE-valued, and that is the point of it. True and False are
    answers; None means the app could not find out, which happens when the read-back after
    a write fails. None must not be read as False: celebrating a finished language because
    a lookup broke would tell a learner they are done with a language they are not.
    """
    if not isinstance(outcome, str):
        return None
    event = _OUTCOME_EVENTS.get(outcome)
    if event is LessonEvent.RESULT_RECORDED_COMPLETE and more_lessons_remain is False:
        # `is False` and never a falsy test: None is the unknown, and it must fall through
        # to the ordinary reaction rather than to this one.
        return LessonEvent.LANGUAGE_FINISHED
    return event


def react_to_lesson_event(event: object, *, movement_manager: Any) -> str | None:
    """Queue one small move for one lesson event, and return the move queued, or nothing.

    Never raises. A reaction is decoration on something that already happened: a lesson
    that started, or a result the database already holds. Letting a movement fault undo
    either of those would trade something that matters for something that does not.
    """
    if not isinstance(event, LessonEvent):
        # An allow-list of one type. Anything else -- a raw outcome word, a lesson title,
        # None, a dict -- is simply not an event, so there is nothing to react to.
        return None
    if not EMOTION_AVAILABLE:
        return None

    # Asked, never loaded. Building the library downloads a dataset, and this runs inside
    # the voice loop; a cold process does without a reaction rather than stalling a
    # conversation. See load_emotion_library's pair in play_emotion.py.
    library = play_emotion.loaded_emotion_library()
    if library is None:
        logger.debug("lesson_feedback: no emotion library loaded yet, so no reaction")
        return None

    intent = EVENT_INTENTS.get(event)
    if intent is None:
        return None

    try:
        available = library.list_moves()
        move_name = play_emotion.resolve_emotion_name(intent, available)
        if not move_name:
            # Deliberately NOT play_emotion's random fallback. A reaction nobody chose is
            # worse than stillness here: the model asked for an emotion, the app asked for
            # a specific piece of feedback, and a random one would not be that feedback.
            logger.warning("lesson_feedback: %s did not resolve to an available move", intent)
            return None
        move: EmotionQueueMove = EmotionQueueMove(move_name, library)
        movement_manager.queue_move(move)
    except Exception:
        # The lesson is already started or already saved. Only the reaction is lost, and
        # the one place that is decided is here, so neither tool can forget it.
        logger.exception("lesson_feedback: could not queue a reaction")
        return None

    # The event name and the move: both safe constants. Never the learner, never the
    # lesson -- this module is not given either, and must not start logging as if it were.
    logger.info("lesson_feedback: queued %s for %s", move_name, event.value)
    return move_name
