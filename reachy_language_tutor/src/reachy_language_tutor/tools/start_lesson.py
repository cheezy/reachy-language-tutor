import logging
from typing import Any

from reachy_language_tutor.learners import get_progress, get_lesson_content, get_language_catalog
from reachy_language_tutor.lesson_session import LessonSessionRefusedError
from reachy_language_tutor.lesson_feedback import LessonEvent, react_to_lesson_event
from reachy_language_tutor.tools.core_tools import Tool, ToolDependencies
from reachy_language_tutor.tools._language_choice import resolve_language
from reachy_language_tutor.tools.get_lesson_content import lesson_has_nothing_to_teach


logger = logging.getLogger(__name__)

# One sentence per meaning, and never a sentence shared between two meanings. The
# tutor says these out loud, so they are the learner's register rather than the
# developer's -- and none of them states a figure, because a figure the store did not
# return is a figure this tool invented. Same shape as _REFUSALS in finish_lesson.py.
_REFUSALS: dict[str, str] = {
    "no_current_learner": "I do not know who I am talking to yet, so I cannot start a lesson.",
    # The value itself is never quoted back: it is unbounded text the model chose.
    "language_not_understood": "I did not catch which language that was, so I have not started anything.",
    # A fault in the robot, deliberately worded so it can never be heard as a fact
    # about the person or about which languages are taught.
    "records_unavailable": "I cannot reach my records right now, so I cannot start a lesson.",
    # Byte-identical to get_progress's, so the two tools do not contradict each other
    # about the same fact in the same conversation.
    "language_not_taught": "I do not teach that language.",
    "no_lessons_yet": "I do not have any lessons for that language yet, so there is nothing to start.",
    # DISTINCT from no_lessons_yet above and from all_lessons_finished below, because
    # all three are empty answers that mean opposite things to the person listening:
    # nothing planned, nothing written, and everything done. This one is "the plan
    # exists and the material does not", which is the state three of five languages
    # are in and the only one where starting anyway would have the tutor improvise.
    # NOT "no_material": get_lesson_content already publishes that code for a RUNNING
    # lesson with nothing written. When this comment was written that code carried the
    # opposite guidance -- work from the objective -- which D33 removed; the two still
    # answer different questions ("should this lesson start" against "what is in the
    # lesson that did"), and two codes one suffix apart, handed to the same model,
    # meaning different things, is the distinctness this vocabulary is supposed to have.
    "lesson_not_written_yet": (
        "I have that lesson in the plan but nothing written to teach from, so I would only be making it up."
    ),
    # Deliberately does NOT say "nothing is running": a refused open leaves whatever
    # was already pinned untouched, so that sentence could be false.
    "could_not_start": "I could not start that lesson just now, so I have not started it.",
}


def _refused(reason: str, **extra: Any) -> dict[str, Any]:
    """Build the answer for a start that did not happen, naming why in one code."""
    return {"started": False, "reason": reason, "error": _REFUSALS[reason], **extra}


class StartLesson(Tool):
    """Begin the lesson the database says comes next in one language."""

    name = "start_lesson"
    description = (
        "Start the next lesson in a language: name the language the person wants to practise and this picks the "
        "lesson for them, tells you its title and what it is for, and remembers that it is the one running. You "
        "cannot choose WHICH lesson they do -- the database decides that from what they have already finished -- "
        "and you must not ask anyone for a name or an id in order to call it. Read 'started': when it is false, "
        "'reason' says why. A reason of 'all_lessons_finished' is good news, not a failure -- say they have "
        "finished everything you have in that language. A reason of 'lesson_not_written_yet' is different and "
        "is not good news: that lesson is in the plan with nothing written in it, so say plainly that you "
        "cannot teach it yet, name the languages in 'languages_with_material' as the ones you can, and do "
        "not offer to make one up. Never invent a lesson, a title or a figure."
    )
    # One property, and nothing identity-shaped may ever join it -- not a learner, and
    # not a lesson either. The learner's identity comes from application state, and the
    # lesson comes from the database; anything declared here is something the model
    # fills in, which is something a person talking to the robot can influence. A
    # lesson parameter "so they can skip ahead" is the same boundary breach as a
    # learner parameter, one fact along.
    #
    # The enum is the same list, in the same order, as get_progress.py's -- see the
    # comment block there for why the order is load-bearing rather than cosmetic
    # (test_tool_identity_boundary's _benign_args takes enum[0], and the two probe
    # learners are told apart by their Spanish history). A test pins both to the seeded
    # catalog so they cannot drift apart or go stale.
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "language": {
                "type": "string",
                "enum": ["Spanish", "French", "German", "Italian", "Portuguese"],
                "description": "The language to start the next lesson in, e.g. 'Spanish'.",
            }
        },
        "required": ["language"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Pin the next lesson in one language and describe it, or say why not."""
        # Only "language" is ever read out of kwargs. Reading an identity or a lesson
        # out of it is exactly the boundary this tool exists to hold, and a test parses
        # this module to assert that no other key is taken from it.
        learner_id = deps.current_learner_id
        if learner_id is None:
            logger.warning("start_lesson: no current learner is set")
            return _refused("no_current_learner")

        spoken = kwargs.get("language")
        if not isinstance(spoken, str) or not spoken.strip():
            # Refused before the store is touched. A bad argument costs no database
            # work, which is what keeps the voice loop responsive on the Wireless
            # model's onboard computer.
            logger.warning("start_lesson: the language argument was missing or not a usable string")
            return _refused("language_not_understood")

        catalog = get_language_catalog(instance_path=deps.instance_path)
        if not catalog:
            # Unreadable and empty-but-present are deliberately the same answer:
            # neither supports a claim about which languages are taught.
            logger.error("start_lesson: the language catalog could not be read")
            return _refused("records_unavailable")

        matched = resolve_language(catalog, spoken)
        if matched is None:
            logger.warning("start_lesson: the requested language is not in the catalog")
            return _refused(
                "language_not_taught",
                languages_taught=[entry.name for entry in catalog],
                # Split the same way get_progress splits it. Listing five names flat
                # said the robot could teach five languages when it can teach two, and
                # this is the answer a learner gets at the moment they were refused.
                languages_with_material=[entry.name for entry in catalog if entry.has_material],
                languages_without_material_yet=[entry.name for entry in catalog if not entry.has_material],
            )

        progress = get_progress(learner_id, matched.code, instance_path=deps.instance_path)
        if progress is None:
            # The catalog row existed a moment ago, so this is not an absence: either
            # the store failed mid-call or deps carries an id no learner could have.
            # Both are faults in the robot, and neither is said as a fact about a
            # language. A distinct log line keeps one prefix per meaning.
            logger.error("start_lesson: the store did not answer for a language the catalog resolved")
            return _refused("records_unavailable")

        completed_count = len(progress.completed)
        remaining_count = len(progress.remaining)

        if progress.next_lesson is None:
            # Two different silences, and telling them apart matters to the learner.
            # A language with lessons, all of them done, is an achievement. A language
            # the catalog lists but has no lessons for is a gap in the robot's data,
            # and calling that "you have finished them all" would be a lie about them.
            if completed_count:
                logger.info(
                    "Tool call: start_lesson completed=%d remaining=%d",
                    completed_count,
                    remaining_count,
                )
                # remaining_count, not a literal 0. It IS zero here -- the store builds
                # next_lesson as remaining[0] if remaining else None -- but that is an
                # invariant two modules away, and a figure the tutor speaks should be
                # one the store returned rather than one this branch knows.
                return {
                    "started": False,
                    "reason": "all_lessons_finished",
                    "message": "You have finished every lesson I have in that language.",
                    "language": progress.language_name,
                    "language_code": progress.language_code,
                    "completed_count": completed_count,
                    "remaining_count": remaining_count,
                    "total_lessons": completed_count + remaining_count,
                }
            logger.error("start_lesson: the catalog lists a language the store has no lessons for")
            return _refused("no_lessons_yet")

        lesson = progress.next_lesson

        # LAST of the four empty answers, and gated on the LESSON rather than on the
        # language. The language-level test was wrong in both directions and the tests
        # found each one: placed before the counts it turned `no_lessons_yet` into this
        # code for a language with no lessons, and placed before the next_lesson branch
        # it turned `all_lessons_finished` into this code for someone who had finished
        # an unwritten course.
        #
        # Worse, a per-LANGUAGE gate cannot catch the case that is reachable today with
        # no legacy data at all. Italian and Spanish each have six converted units in
        # front of six empty placeholders, so the language has material and lesson
        # seven does not: start_lesson would open it and get_lesson_content then said
        # "we can work from what it is for" -- the improvised lesson this task exists
        # to prevent, invited by the tool one call later. D33 has since rewritten that
        # message, so the invitation is gone and this gate is the remaining reason the
        # lesson never opens. Asking about the lesson that would actually start covers
        # both shapes with one rule.
        content = get_lesson_content(lesson.id, instance_path=deps.instance_path)
        if content is None:
            # FAIL CLOSED. `None` means the store named three different things -- an id
            # it will not accept, no such lesson, or a store it could not read -- and
            # the first two are caught downstream by the session pin. The third was
            # not: with an unreadable database at this moment the gate waved the lesson
            # through, get_lesson_content then said "we can work from what it is for",
            # and finish_lesson wrote the improvised lesson down as completed. A guard
            # whose permitted set contains "whatever this was" is not a guard.
            logger.error("start_lesson: could not read the lesson's content, so it was not started")
            return _refused("could_not_start")
        if lesson_has_nothing_to_teach(content):
            logger.info("start_lesson: refused a lesson with nothing written in it")
            return _refused(
                "lesson_not_written_yet",
                language=matched.name,
                language_code=matched.code,
                languages_with_material=[entry.name for entry in catalog if entry.has_material],
            )

        try:
            # Pinned BEFORE anything is returned, so a failed pin can never be
            # reported as a lesson that started. The narrow except is deliberate: a
            # bare one would swallow a programming error into a soothing sentence.
            deps.lesson_session.open(lesson_id=lesson.id, language_code=progress.language_code)
        except LessonSessionRefusedError as exc:
            # The class name, never the message: _REFUSAL over in lesson_session is
            # developer-register prose about holders, and this is spoken aloud.
            logger.error("start_lesson: the lesson session refused the pin: %s", type(exc).__name__)
            return _refused("could_not_start")

        # Counts and the one lesson, never the list of titles. No lesson id either:
        # pinning it is what makes the id application state, so handing it back would
        # put the thing this tool exists to decide into the model's hands again.
        logger.info(
            "Tool call: start_lesson completed=%d remaining=%d",
            completed_count,
            remaining_count,
        )
        # Below the pin, and below every refusal above it: a lesson that did not start
        # must not be nodded at. The policy is handed an event and the movement sink, and
        # deliberately not `deps` -- deps carries the learner id, and choosing a reaction
        # is not allowed to see who is practising.
        react_to_lesson_event(LessonEvent.LESSON_STARTED, movement_manager=deps.movement_manager)
        return {
            "started": True,
            "language": progress.language_name,
            "language_code": progress.language_code,
            "lesson": {
                "position": lesson.position,
                "title": lesson.title,
                "objective": lesson.objective,
            },
            "completed_count": completed_count,
            "remaining_count": remaining_count,
            "remaining_after_this": remaining_count - 1,
            "total_lessons": completed_count + remaining_count,
        }
