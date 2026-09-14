import logging
from typing import Any

from reachy_language_tutor.learners import get_progress, get_language_catalog, split_catalog_by_material
from reachy_language_tutor.tools.core_tools import Tool, ToolDependencies
from reachy_language_tutor.tools._language_choice import resolve_language


logger = logging.getLogger(__name__)


def _most_recently_completed(progress: Any) -> str | None:
    """Return the lesson this learner finished LAST, by when they finished it.

    Not ``progress.completed[-1]``. That tuple is ordered by POSITION, so it names the
    furthest-ALONG completed lesson rather than the most recent one. The two agree
    only while a learner works straight through a fixed catalog in order, which is why
    this read the way it did for as long as it did.

    They diverge the moment content lands AHEAD of someone. Converting Cycle 10 of the
    Spanish FAST put six new lessons at positions 1-6 and pushed the placeholders to
    7-12; a learner who had already finished two placeholders and then completed one of
    the new lessons got back the title of a placeholder they had finished days earlier,
    because that placeholder sits at a higher position. The tutor says this field out loud, so the
    learner would have been congratulated on the wrong lesson.

    ``attempts`` is ordered ``recorded_at DESC, id DESC`` by the store, so the first
    completed attempt in it is the most recent one. Only lessons in ``completed`` are
    consulted, so a lesson that was later un-completed cannot be named.
    """
    titles = {lesson.id: lesson.title for lesson in progress.completed}
    for attempt in progress.attempts:
        if attempt.outcome == "completed" and attempt.lesson_id in titles:
            return titles[attempt.lesson_id]
    return None


class GetProgress(Tool):
    """Look up how far the current learner has got in one language."""

    name = "get_progress"
    description = (
        "Look up how far the current learner has got in one language: how many lessons they have finished, how "
        "many are left, and which lesson comes next. Name the language the person asked about. You cannot choose "
        "whose progress you read, and you must not ask anyone for a name or an id in order to call it. The listed "
        "languages are the ones this robot currently teaches; if someone asks about another one, this reports back "
        "which are taught, split into 'languages_with_material' and 'languages_without_material_yet'. Offer the "
        "first list: the second is a plan with nothing written in it. 'has_material' says whether the language "
        "asked about has ANY written lesson -- it describes the language, and it is not permission to start: "
        "start_lesson decides that for the particular lesson and may still refuse one that is empty. So do not "
        "promise a lesson from this answer; call start_lesson and say what it tells you. If it returns an "
        "error, say plainly what it says and never invent a lesson or a figure."
    )
    # One property, and nothing identity-shaped may ever join it. The learner's
    # identity comes from application state; anything declared here is something the
    # model fills in, which is something a person talking to the robot can influence
    # -- and that is how someone would ask their way into another household member's
    # progress. The language is not identity: it names a shared catalog row.
    #
    # The enum enforces nothing HERE -- _dispatch_tool_call performs no schema
    # validation at all -- so __call__ resolves against the live catalog instead, and a
    # language the database teaches still works if it is missing from this list. But it
    # is not free either: a provider doing strict or constrained function calling will
    # hold the model to it, so treat the list as the set the model can reliably name
    # rather than as a limit on what the tool accepts. A test pins it to the seeded
    # catalog so it cannot quietly go stale.
    #
    # The ORDER is load-bearing too, not just the membership. _benign_args over in
    # test_tool_identity_boundary.py takes enum[0], and the two probe learners are told
    # apart by their Spanish and French history -- so a first entry naming a language
    # NEITHER of them has practised makes both answers identical and quietly turns the
    # injection test vacuous. Seed order, not alphabetical, and a drift test pins the
    # sequence rather than the set.
    #
    # It is also load-bearing for the boundary suite: with no enum, _benign_args
    # synthesises "w11", the tool short-circuits on its not-taught branch, and
    # test_every_learner_tool_is_individually_observable fails rather than silently
    # covering nothing. Verified by removing it and watching that test name this tool.
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "language": {
                "type": "string",
                "enum": ["Spanish", "French", "German", "Italian", "Portuguese"],
                "description": "The language to report progress for, e.g. 'Spanish'.",
            }
        },
        "required": ["language"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Return the current learner's standing in one language, or an error dict."""
        # Only "language" is ever read out of kwargs. Reading an identity out of it is
        # exactly the boundary violation this tool exists to prevent, and a test parses
        # this module to assert that no other key is taken from it.
        learner_id = deps.current_learner_id
        if learner_id is None:
            logger.warning("get_progress: no current learner is set")
            return {"error": "I do not know who I am talking to yet, so I cannot look up any progress."}

        spoken = kwargs.get("language")
        if not isinstance(spoken, str) or not spoken.strip():
            # Refused before the store is touched. A bad argument costs no database
            # work, which is what keeps the voice loop responsive on the Wireless
            # model's onboard computer. The value itself is never logged or echoed:
            # it is unbounded text the model chose.
            logger.warning("get_progress: the language argument was missing or not a usable string")
            return {"error": "I did not catch which language that was, so ask which one they mean and try again."}

        # The catalog is what makes "I do not teach that" a fact rather than a guess.
        # get_progress answers None for a language it does not teach, for a store it
        # cannot read, and for an argument it will not accept, so reading that None as
        # absence would eventually have the robot deny teaching a language it teaches.
        catalog = get_language_catalog(instance_path=deps.instance_path)
        if not catalog:
            # Unreadable and empty-but-present are deliberately the same answer: neither
            # supports a claim about which languages are taught. store_is_available is
            # NOT the test here -- it binds no language, and it answers True for an
            # empty catalog, which is the case this branch exists to catch.
            logger.error("get_progress: the language catalog could not be read")
            return {"error": "I cannot reach my records right now, so I cannot tell you where you are."}

        # The matching rule itself lives in _language_choice, shared with start_lesson.
        # It is the part that decides whether the robot denies teaching a language it
        # teaches, so a second copy of it is the sibling-drift defect waiting to happen.
        matched = resolve_language(catalog, spoken)
        if matched is None:
            logger.warning("get_progress: the requested language is not in the catalog")
            # Split, not flattened. Listing all five as one set told a learner the
            # robot could teach five languages when it can teach two -- and the one
            # they just asked for was refused, so the list is the whole answer they
            # get. Both keys are always present, so the model cannot read an absent
            # key as "none of those".
            with_material, without_material = split_catalog_by_material(catalog)
            return {
                "error": "I do not teach that language.",
                "languages_taught": [entry.name for entry in catalog],
                "languages_with_material": with_material,
                "languages_without_material_yet": without_material,
            }

        progress = get_progress(learner_id, matched.code, instance_path=deps.instance_path)
        if progress is None:
            # The catalog row existed a moment ago, so this is not an absence: either
            # the store failed mid-call or deps carries an id no learner could have.
            # Both are faults in the robot, and neither is said as a fact about a
            # language. A distinct log line keeps one prefix per meaning.
            logger.error("get_progress: the store did not answer for a language the catalog resolved")
            return {"error": "I cannot reach my records right now, so I cannot tell you where you are."}

        # Counts, not rows. The tutor says "you have done two of six" out loud; it never
        # reads a list of six titles, and every row withheld is a row that cannot leak.
        # Attempt history is dropped entirely: scores are the most sensitive field here
        # and speaking a short answer does not need them.
        logger.info(
            "Tool call: get_progress completed=%d remaining=%d",
            len(progress.completed),
            len(progress.remaining),
        )
        next_lesson = progress.next_lesson
        return {
            "language": progress.language_name,
            "language_code": progress.language_code,
            # Derived from the content tables on every read, so it flips on its own
            # the day this language is converted. False means the lessons below are a
            # syllabus with nothing written in them yet.
            "has_material": matched.has_material,
            "completed_count": len(progress.completed),
            "remaining_count": len(progress.remaining),
            "total_lessons": len(progress.completed) + len(progress.remaining),
            "last_completed": _most_recently_completed(progress),
            "next_lesson": (
                {
                    "id": next_lesson.id,
                    "position": next_lesson.position,
                    "title": next_lesson.title,
                    "objective": next_lesson.objective,
                }
                if next_lesson is not None
                else None
            ),
        }
