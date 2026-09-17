"""Let a learner go back to a lesson their record says they have finished.

The other half of D38. The evidence gate in finish_lesson stops a lesson being written
off as completed when nobody taught it -- but it cannot help the learner whose record is
ALREADY wrong, and that learner existed before the gate did. The session that produced
this defect ended with them saying "I told you I had, but I really hadn't completed it"
and being told the lesson was already recorded, which was correct and left them stranded
past material they had never learned.

Nothing is deleted to do it. lesson_results is append-only on purpose -- a learner's
history is the record, not a mutable score -- so this appends. The catalog reads "this
lesson is finished" as "the LATEST result for it is a completion" (NEXT_LESSON_SQL), so
a later, honest row puts the lesson back on the path while every earlier row stays
exactly where it was.

What this tool must NOT become: a way for the conversation to name a lesson. It takes a
language, the same argument start_lesson already takes, and works out the lesson from
the learner's own history. There is no lesson parameter and no identity parameter here,
for the same reason there is none on any other learner tool.
"""

import logging
from typing import Any

from reachy_language_tutor.learners import (
    get_progress,
    get_lesson_content,
    get_language_catalog,
)
from reachy_language_tutor.lesson_session import LessonSessionRefusedError
from reachy_language_tutor.tools.core_tools import Tool, ToolDependencies
from reachy_language_tutor.tools.start_lesson import _lines_worth_hearing
from reachy_language_tutor.tools._language_choice import resolve_language


logger = logging.getLogger(__name__)

# One sentence per meaning, in the learner's register, stating no figure the store did
# not return -- the same shape as start_lesson's and finish_lesson's.
_REFUSALS: dict[str, str] = {
    "no_current_learner": "I do not know who I am talking to yet, so I cannot go back to a lesson.",
    "language_not_understood": "I did not catch which language that was, so I have not changed anything.",
    "records_unavailable": "I cannot reach my records right now, so I have not changed anything.",
    "language_not_taught": "I do not teach that language.",
    # Not a fault and not a refusal to help: there is simply nothing marked finished to
    # go back to, and saying so plainly is kinder than implying they did something wrong.
    "nothing_finished_yet": "You have not finished a lesson in that language yet, so there is none to go back to.",
    "could_not_reopen": "I could not reopen that lesson just now, so nothing has changed.",
}


def _refused(reason: str, **extra: Any) -> dict[str, Any]:
    """Build the answer for a reopen that did not happen, naming why in one code."""
    return {"reopened": False, "reason": reason, "error": _REFUSALS[reason], **extra}


class RedoLesson(Tool):
    """Put the most recently finished lesson in one language back on the learner's path."""

    name = "redo_lesson"
    description = (
        "Go back to the last lesson the person finished in a language, when they want to do it again or tell "
        "you it was marked finished when it was not. Name the language they asked about. You cannot choose "
        "WHICH lesson this is -- it is the last one their records show they finished -- and you must not ask "
        "anyone for a name or an id in order to call it. It reopens that lesson and starts it, so teach it "
        "from the beginning. Read 'reopened': when it is false, 'reason' says why, and a reason of "
        "'nothing_finished_yet' simply means there is no finished lesson to go back to. Never tell someone a "
        "lesson has been reopened when it has not, and never invent a lesson or a title."
    )
    # One property, and nothing identity-shaped or lesson-shaped may ever join it. A
    # lesson id here would hand back the write path this whole boundary exists to keep
    # out of the conversation -- see lesson_session.py.
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "language": {
                "type": "string",
                "enum": ["Spanish", "French", "German", "Italian", "Portuguese"],
                "description": "The language whose last finished lesson should be reopened, e.g. 'Spanish'.",
            }
        },
        "required": ["language"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Reopen the learner's most recently finished lesson in one language."""
        # Only "language" is ever read out of kwargs, exactly as in start_lesson.
        learner_id = deps.current_learner_id
        if learner_id is None:
            logger.warning("redo_lesson: no current learner is set")
            return _refused("no_current_learner")

        spoken = kwargs.get("language")
        if not isinstance(spoken, str) or not spoken.strip():
            logger.warning("redo_lesson: the language argument was missing or not a usable string")
            return _refused("language_not_understood")

        catalog = get_language_catalog(instance_path=deps.instance_path)
        if not catalog:
            logger.error("redo_lesson: the language catalog could not be read")
            return _refused("records_unavailable")

        matched = resolve_language(catalog, spoken)
        if matched is None:
            logger.warning("redo_lesson: the requested language is not in the catalog")
            return _refused("language_not_taught")

        progress = get_progress(learner_id, matched.code, instance_path=deps.instance_path)
        if progress is None:
            logger.error("redo_lesson: the store did not answer for a language the catalog resolved")
            return _refused("records_unavailable")

        # THE LESSON COMES FROM THEIR OWN HISTORY, never from the conversation.
        # progress.attempts is ordered newest first by the store, so the first
        # completion in it is the most recent one -- no new query, and in particular no
        # query that takes a lesson id from anywhere near the model.
        # Scoped to the lessons the catalog CURRENTLY calls finished, not to every
        # lesson that was ever completed. The two stopped being the same thing when
        # "finished" became "the latest result is a completion": after one redo cycle
        # ends in a partial, the most recent completed ATTEMPT names a lesson that is
        # no longer finished, and looking that up in progress.completed found nothing
        # -- so the tool answered "I could not reopen that" for ever after a single
        # use. The route back has to survive being used twice.
        still_finished = {entry.id: entry for entry in progress.completed}
        finished = next(
            (
                attempt
                for attempt in progress.attempts
                if attempt.outcome == "completed" and attempt.lesson_id in still_finished
            ),
            None,
        )
        if finished is None:
            logger.info("redo_lesson: nothing is marked finished in this language, so there is nothing to reopen")
            return _refused("nothing_finished_yet", language=matched.name, language_code=matched.code)
        lesson_id = finished.lesson_id

        lesson = still_finished.get(lesson_id)
        content = get_lesson_content(lesson_id, instance_path=deps.instance_path)
        if lesson is None or content is None:
            # The attempt names a lesson the catalog no longer describes. Nothing is
            # written: reopening a lesson that cannot then be taught would strand the
            # learner a second way.
            logger.error("redo_lesson: the finished lesson could not be read back, so nothing was reopened")
            return _refused("could_not_reopen")

        # THIS TOOL WRITES NOTHING, and that is a deliberate second thought rather
        # than an omission. The first version appended a "partial" row here to stop the
        # catalog calling the lesson finished. Three things were wrong with it: it made
        # a second conversation-reachable writer of lesson_results where the whole
        # design has exactly one, it consumed its own precondition so calling it twice
        # gave two different answers, and it wrote a claim about a lesson the learner
        # had not yet done anything about.
        #
        # None of that is needed. The catalog reads "finished" as "the LATEST result is
        # a completion" (NEXT_LESSON_SQL), so the row that supersedes the old completion
        # is simply the one finish_lesson writes when this reopened attempt ends -- an
        # honest record of what actually happened, written by the one tool that has ever
        # been allowed to write. Until then the lesson stays as it was, which is correct:
        # a learner who asks to redo a lesson and then wanders off has not redone it.
        try:
            deps.lesson_session.open(
                lesson_id=lesson_id,
                language_code=matched.code,
                teachable_lines=_lines_worth_hearing(content),
            )
        except LessonSessionRefusedError as exc:
            # Nothing to roll back -- this tool writes nothing, so a failed pin leaves
            # the record exactly as it was and the lesson still finished. Saying
            # "nothing has changed" is therefore the literal truth here.
            logger.error("redo_lesson: reopened the lesson but the session refused the pin: %s", type(exc).__name__)
            return _refused("could_not_reopen")

        logger.info("Tool call: redo_lesson reopened=1 language=%s", matched.code)
        return {
            "reopened": True,
            "language": matched.name,
            "language_code": matched.code,
            # The title, never the id -- the same rule every other lesson tool follows.
            "lesson": {"title": lesson.title, "objective": lesson.objective},
        }
