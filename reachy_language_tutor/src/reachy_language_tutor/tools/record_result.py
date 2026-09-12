import logging
from typing import Any

from reachy_language_tutor.learners import OUTCOMES, get_lesson, get_progress, record_result
from reachy_language_tutor.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


# One answer per reason the store can refuse with. A test pins the keys to
# RECORD_REASONS, so a new reason upstream fails loudly here instead of falling
# into a generic string and telling a learner something vague.
#
# Every one of them says nothing was saved. A refusal that reads like a success is
# the worst outcome this tool has: the tutor would move on, and the lesson would be
# repeated forever because the database never heard about it.
_REFUSALS: dict[str, str] = {
    "unknown_learner": "I cannot find your records, so I have not saved that.",
    "unknown_lesson": "I do not know that lesson, so I have not saved anything.",
    "invalid_outcome": "I did not understand how that lesson went, so I have not saved it.",
    "invalid_score": "I could not save that just now, so it is not recorded.",
    "invalid_recorded_at": "I could not save that just now, so it is not recorded.",
    "rejected_by_database": "I could not save that just now, so it is not recorded.",
    "storage_unavailable": "I cannot reach my records right now, so nothing was saved.",
}

_UNKNOWN_REFUSAL = "I could not save that just now, so it is not recorded."


class RecordResult(Tool):
    """Write one lesson result for the current learner, and report where they now stand."""

    name = "record_result"
    description = (
        "Save how a lesson went, once the learner has finished practising it. Name the lesson you actually "
        "worked on, using the id get_progress gave you, and say whether they completed it, got part way "
        "through it, or skipped it. You cannot choose whose result you save. Save the lesson and the outcome "
        "only -- never a remark about the person. If it comes back with an error, say plainly that you could "
        "not save it, and never tell someone a lesson is recorded when it is not."
    )
    # Two properties, and nothing identity-shaped may ever join them. The learner's
    # identity comes from application state; anything declared here is something the
    # model fills in, which is something a person talking to the robot can influence.
    # A lesson id and an outcome are shared catalog vocabulary, not identity.
    #
    # NEITHER enum enforces anything at runtime -- _dispatch_tool_call does no schema
    # validation, and the store resolves the lesson against the live catalog, so a
    # lesson absent from this list still records. They are hints to the model, pinned
    # to the seed data by drift tests so they cannot quietly go stale.
    #
    # The lesson enum IS load-bearing for tests/test_tool_identity_boundary.py, and
    # for a different reason than get_progress's. _benign_args synthesises "w11" for a
    # required free string; the store then answers unknown_lesson, this tool returns an
    # error dict, and BOTH test_no_tool_can_be_steered_by_an_injected_identity (via
    # reached_only_validator) and test_every_learner_tool_is_individually_observable
    # (via inert -- the same error for both learners) fail, with no waiver available.
    #
    # Do NOT add a `default` to either property: _benign_args reads `default` before
    # `enum`, and a provider that materialises schema defaults would silently record a
    # lesson nobody named in a real household's database.
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "lesson_id": {
                "type": "string",
                "enum": [
                    "es-01-greetings",
                    "es-02-introductions",
                    "es-03-numbers",
                    "es-04-ordering-food",
                    "es-05-directions",
                    "es-06-daily-routine",
                    "fr-01-greetings",
                    "fr-02-introductions",
                    "fr-03-numbers",
                    "fr-04-ordering-food",
                    "fr-05-directions",
                    "fr-06-daily-routine",
                ],
                "description": "The id of the lesson that was just practised, exactly as get_progress reported it.",
            },
            "outcome": {
                "type": "string",
                "enum": list(OUTCOMES),
                "description": (
                    "How the lesson went: 'completed' if they finished it, 'partial' if they got part way "
                    "through, 'skipped' if they gave up on it or moved on without doing it."
                ),
            },
        },
        "required": ["lesson_id", "outcome"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Record one lesson attempt, or return an error dict saying nothing was saved."""
        # Only "lesson_id" and "outcome" are ever read out of kwargs. Reading an identity
        # out of it is the boundary violation this tool exists to prevent, and a test
        # parses this module to assert no other key is taken from it. A `notes` argument,
        # which the model may still send, is therefore ignored structurally rather than
        # by policy -- there is nowhere to store it and it would be commentary about a
        # child.
        #
        # This read must stay here, inside __call__: test_tool_identity_boundary.py's
        # _assert_sources_identity_from_deps looks for it syntactically in this function.
        learner_id = deps.current_learner_id
        if learner_id is None:
            logger.warning("record_result: no current learner is set")
            return {
                "recorded": False,
                "error": "I do not know who I am talking to yet, so I cannot save a lesson result.",
            }

        lesson_id = kwargs.get("lesson_id")
        if not isinstance(lesson_id, str) or not lesson_id.strip():
            # Refused before the store is touched: a bad argument costs no database work,
            # which is what keeps the voice loop responsive on the Wireless model. The
            # value is never logged or echoed -- it is unbounded text the model chose.
            logger.warning("record_result: the lesson_id argument was missing or not a usable string")
            return {"recorded": False, "error": "I did not catch which lesson that was, so I have not saved anything."}

        outcome = kwargs.get("outcome")
        if outcome not in OUTCOMES:
            # Checked against the store's own vocabulary rather than a copy, so the two
            # cannot drift. The task specified an "abandoned" outcome the database's CHECK
            # rejects; constraining the model to a word the store refuses would fail every
            # such call silently, so the declared enum is OUTCOMES and this is the guard.
            logger.warning("record_result: the outcome argument was missing or not in the store's vocabulary")
            return {
                "recorded": False,
                "error": "I need to know whether that lesson was completed, partial or skipped, "
                "so I have not saved anything.",
            }

        result = record_result(learner_id, lesson_id.strip(), outcome, instance_path=deps.instance_path)
        if not result.recorded:
            # Reason codes are safe constants; the lesson id is not -- test_log_redaction
            # pins that a lesson id must not reach a log line even inside an error.
            logger.warning("record_result: the store refused, reason=%s", result.reason)
            return {"recorded": False, "error": _REFUSALS.get(result.reason or "", _UNKNOWN_REFUSAL)}

        standing = self._standing(learner_id, lesson_id.strip(), deps)
        logger.info(
            "Tool call: record_result recorded=1 completed=%s remaining=%s",
            standing["completed_count"],
            standing["remaining_count"],
        )
        # Nothing wall-clock may enter this dict -- no recorded_at, no row or attempt id,
        # no attempt count, no "already recorded" flag. test_tool_identity_boundary.py
        # calls the tool twice with identical arguments and requires identical answers;
        # anything time-varying fails it, and re-recording the same lesson is otherwise
        # stable because `completed` is built from a DISTINCT set of lesson ids.
        return {"recorded": True, "lesson_id": lesson_id.strip(), "outcome": outcome, **standing}

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
            "next_lesson": (
                {"id": nxt.id, "position": nxt.position, "title": nxt.title, "objective": nxt.objective}
                if nxt is not None
                else None
            ),
        }
