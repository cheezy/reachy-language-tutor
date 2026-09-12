import logging
from typing import Any

from reachy_language_tutor.learners import OUTCOMES, get_lesson, get_progress, record_result
from reachy_language_tutor.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


# One answer per reason a write can be refused. The first seven are RECORD_REASONS
# verbatim, so a new reason upstream fails a pin test here rather than falling into a
# generic string and telling a learner something vague. The last two are this tool's
# own, for the refusals that happen before the store is reached.
#
# Every one of them says nothing was saved. A refusal that reads like a success is the
# worst outcome this tool has: the tutor would move on, and the lesson would be
# repeated forever because the database never heard about it.
_REFUSALS: dict[str, str] = {
    "unknown_learner": "I cannot find your records, so I have not saved that.",
    "unknown_lesson": "I do not know that lesson any more, so I have not saved anything.",
    "invalid_outcome": "I did not understand how that lesson went, so I have not saved it.",
    "invalid_score": "I could not save that just now, so it is not recorded.",
    # Unreachable from this tool, which never passes recorded_at -- and kept anyway, so
    # that a future caller which does pass one cannot fall into the generic string.
    "invalid_recorded_at": "I could not save that just now, so it is not recorded.",
    "rejected_by_database": "I could not save that just now, so it is not recorded.",
    "storage_unavailable": "I cannot reach my records right now, so nothing was saved.",
    "no_current_learner": "I do not know who I am talking to yet, so I have not saved anything.",
    "no_lesson_running": "I do not have a lesson running, so there is nothing for me to save.",
}

_UNKNOWN_REFUSAL = "I could not save that just now, so it is not recorded."


def _refused(reason: str) -> dict[str, Any]:
    """Build the answer for a write that did not happen, naming why in one code."""
    return {"recorded": False, "reason": reason, "error": _REFUSALS.get(reason, _UNKNOWN_REFUSAL)}


class FinishLesson(Tool):
    """Record how the running lesson went, against the lesson the app pinned."""

    name = "finish_lesson"
    description = (
        "Save how the lesson that is running went, once the person has finished practising it. Say only whether "
        "they completed it, got part way through it, or skipped it, and a score out of a hundred if you judged "
        "one. You cannot choose WHICH lesson is saved or whose it is: it saves the lesson start_lesson began, "
        "for the person you are talking to. If it says no lesson is running, say so rather than guessing which "
        "one they meant. Save how it went and nothing about the person, never a remark of your own, and never "
        "tell someone a lesson is saved when it is not."
    )
    # Two properties, and nothing identity-shaped or lesson-shaped may ever join them.
    # The learner comes from application state and the lesson comes from the pinned
    # session; anything declared here is something the model fills in, which is
    # something a person talking to the robot can influence. A lesson id "as a fallback
    # when nothing is pinned" is exactly the boundary this tool exists to close -- the
    # fallback is to say nothing is running.
    #
    # The enum is OUTCOMES itself rather than a copy, so the words offered to the model
    # and the CHECK constraint in schema.sql cannot drift apart.
    #
    # The score bounds are the store's own, and they do NOT duplicate its rule:
    # _dispatch_tool_call performs no schema validation at all, so these are a hint to
    # the model in the same class as the enum. This tool checks no score in code -- the
    # value goes to the store untouched and store.record_result's invalid_score branch
    # is the only rule. A test pins the declared bounds against what the store really
    # accepts, so they cannot drift either.
    #
    # Neither property declares a "default". _benign_args over in
    # test_tool_identity_boundary.py reads `default` before `enum`, and a provider that
    # materialised schema defaults would record a lesson nobody finished.
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "outcome": {
                "type": "string",
                "enum": list(OUTCOMES),
                "description": (
                    "How the lesson went: 'completed' if they worked through it, 'partial' if they got part "
                    "way through, 'skipped' if they gave up on it or moved on without doing it."
                ),
            },
            "score": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100,
                "description": "Optional. How well it went, out of a hundred. Leave it out rather than guessing.",
            },
        },
        "required": ["outcome"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Record the running lesson's outcome, or return an error dict saying why not."""
        # Only "outcome" and "score" are ever read out of kwargs -- never a lesson and
        # never an identity, which is the whole point of this tool existing. A test
        # parses this module to assert no other key is taken from it.
        #
        # This read must stay here, inside __call__: test_tool_identity_boundary.py's
        # _assert_sources_identity_from_deps looks for it syntactically in this function.
        learner_id = deps.current_learner_id
        if learner_id is None:
            logger.warning("finish_lesson: no current learner is set")
            return _refused("no_current_learner")

        # The lesson comes from here and nowhere else. read_for answers None both when
        # nothing is pinned and when what is pinned belongs to somebody else, and that
        # is deliberate -- lesson_session.py's docstring says the two are the same
        # answer. This tool therefore cannot tell them apart and must not try: reaching
        # past read_for to find out would defeat the guard it is standing behind. One
        # reason code covers both, and the write is refused for the structural reason
        # that there is no lesson id to write.
        session = deps.lesson_session.read_for(learner_id)
        if session is None:
            # Nothing is cleared here, deliberately. Clearing on this path would let
            # anyone wipe a lesson pinned for somebody else just by saying they were
            # done -- a denial of service any conversation could trigger.
            logger.warning("finish_lesson: no lesson is running for the current learner")
            return _refused("no_lesson_running")

        outcome = kwargs.get("outcome")
        if outcome not in OUTCOMES:
            # Checked against the store's own vocabulary object rather than a copy, and
            # reusing the store's own reason name: one rule, one code, two evaluation
            # sites. The second saves a database open on a word the model guessed.
            logger.warning("finish_lesson: the outcome argument was missing or not in the store's vocabulary")
            return _refused("invalid_outcome")

        # Passed through untouched. The store refuses a bad score with its own
        # invalid_score code, and re-implementing that rule here would put it in two
        # places -- which is the pitfall this task names.
        score = kwargs.get("score")

        result = record_result(
            learner_id,
            session.lesson_id,
            outcome,
            score=score,
            instance_path=deps.instance_path,
        )
        if not result.recorded:
            # Reason codes are safe constants; the lesson id is not. The session STAYS
            # pinned: a learner who tried should not lose the lesson because storage
            # hiccuped. That holds even for unknown_lesson, where the lesson is gone for
            # good -- a rule with one exception is the shape that breaks, and the next
            # start_lesson replaces the pin anyway, since open() is last-open-wins.
            logger.warning("finish_lesson: the store refused, reason=%s", result.reason)
            return _refused(result.reason or "")

        # Cleared here and only here, tied to a write that really happened, and BEFORE
        # the read-back below: a failed read must not leave a recorded lesson still
        # pinned and recordable a second time.
        deps.lesson_session.clear()

        standing = self._standing(learner_id, session.lesson_id, deps)
        logger.info(
            "Tool call: finish_lesson recorded=1 completed=%s remaining=%s",
            standing["completed_count"],
            standing["remaining_count"],
        )
        # No lesson id, and none inside next_lesson either. With record_result gone
        # there is no tool that accepts a lesson id, so handing one back serves nothing
        # and re-opens the surface this task closes.
        #
        # Nothing wall-clock may enter this dict -- no recorded_at, no row or attempt
        # id, no attempt count, no "already recorded" flag. test_tool_identity_boundary
        # calls the tool twice with identical arguments and requires identical answers.
        return {"recorded": True, "outcome": outcome, **standing}

    @staticmethod
    def _standing(learner_id: str, lesson_id: str, deps: ToolDependencies) -> dict[str, Any]:
        """Read back where the learner now stands in the language this lesson belongs to.

        Read AFTER the write, so the figures are the ones the tutor should speak, and the
        database rather than this tool decides what counts as done. It is also what makes
        the answer differ between two learners, which the boundary suite requires in order
        to prove an injected identity changed nothing.

        A language whose lookup fails leaves the counts None rather than guessing: the row
        is already saved by then, so a failed read must not turn into a failed write.
        """
        empty: dict[str, Any] = {
            "language": None,
            "lesson_title": None,
            "completed_count": None,
            "remaining_count": None,
            "next_lesson": None,
        }
        # One lookup, not a scan. get_lesson carries language_code, so the language is
        # known without asking every taught language for progress in turn -- work that
        # grew with the catalog and that the conversation path should not pay for.
        lesson = get_lesson(lesson_id, instance_path=deps.instance_path)
        if lesson is None:
            return empty

        progress = get_progress(learner_id, lesson.language_code, instance_path=deps.instance_path)
        if progress is None:
            return empty

        nxt = progress.next_lesson
        return {
            "language": progress.language_name,
            "lesson_title": lesson.title,
            "completed_count": len(progress.completed),
            "remaining_count": len(progress.remaining),
            # Position and title, never the id -- see the success dict above.
            "next_lesson": (
                {"position": nxt.position, "title": nxt.title, "objective": nxt.objective} if nxt else None
            ),
        }
