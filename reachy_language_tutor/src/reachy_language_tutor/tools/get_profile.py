import logging
from typing import Any

from reachy_language_tutor.learners import (
    get_profile,
    store_is_available,
    get_language_catalog,
    get_practised_languages,
    split_catalog_by_material,
)
from reachy_language_tutor.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class GetProfile(Tool):
    """Look up who the app is serving, and what they have practised."""

    name = "get_profile"
    description = (
        "Look up the current learner's name, which languages they have practised, and which languages this robot "
        "can actually teach. Call this at the start of a conversation so you can greet them by name and pick up "
        "where they left off -- and call it again, before you answer, whenever someone asks what you can teach. "
        "It takes no arguments: you cannot choose whose profile to read, and you must not ask the person for a "
        "name or an id in order to call it. 'languages_with_material' is the list of languages you can teach: "
        "name those and no others, however sure you feel about a language that is not in it. "
        "'languages_without_material_yet' are planned and have nothing written in them, so you can say they are "
        "coming but never offer to teach one. If both lists are empty, say you cannot reach your records just "
        "now and name no language at all. If it returns an error, say plainly that you cannot look their profile "
        "up right now, and never invent a name, a progress figure, or a language."
    )
    # No properties, and none may ever be added. The learner's identity comes from
    # application state; anything declared here is something the model can fill in,
    # which is something a person talking to the robot can influence -- and that is
    # how someone would ask their way into another household member's data.
    parameters_schema: dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Return the current learner's profile, or an error dict explaining why not."""
        # kwargs is deliberately ignored rather than validated. There is nothing to
        # validate -- the schema declares no parameters -- and reading an identity out
        # of it is exactly the boundary violation this tool exists to prevent.
        # The catalog is read FIRST, and every return BELOW carries it -- with one
        # exception found by the security review of D35, recorded here so the next
        # reader does not lean on an invariant that has a hole in it: the dispatcher
        # splats the model's JSON as `tool(deps, **args)`, so a model-emitted key of
        # `deps` or `self` raises TypeError before this function runs at all, and the
        # dispatcher's own handler answers with a bare error dict carrying neither
        # key. That is bounded -- the description and the profile both say to name no
        # language on an error -- and it belongs to core_tools, not here. Which languages
        # this robot teaches is not a fact about the learner: it is the same answer for
        # everyone, and it is still true when there is no profile to read. Review of
        # D35 found the first version of this coupled the two, so an unbound learner
        # or a missing profile row left the tutor unable to say what it teaches -- and
        # "I do not know who you are" is precisely the moment it would otherwise
        # invent five languages, which is the defect D35 exists for.
        catalog = get_language_catalog(instance_path=deps.instance_path)
        with_material, without_material = split_catalog_by_material(catalog)
        taught = {
            "languages_with_material": with_material,
            "languages_without_material_yet": without_material,
        }

        learner_id = deps.current_learner_id
        if learner_id is None:
            logger.warning("get_profile: no current learner is set")
            return {
                "error": "I do not know who I am talking to yet, so I cannot look up a profile.",
                **taught,
            }

        profile = get_profile(learner_id, instance_path=deps.instance_path)
        if profile is None:
            # "No such learner" and "cannot read the store" must not be said the same
            # way: one is a fact about the person, the other is a fault in the robot.
            if store_is_available(deps.instance_path):
                logger.warning("get_profile: the current learner has no profile row")
                return {"error": "I do not have a profile saved for you yet.", **taught}
            logger.error("get_profile: the learner store is unreadable")
            return {
                "error": "I cannot reach my records right now, so I cannot look your profile up.",
                **taught,
            }

        practised = get_practised_languages(learner_id, instance_path=deps.instance_path)
        # Why the catalog is here at all, and not in a tool of its own: asked "what
        # languages can you teach me?", the tutor answered "Spanish, French, German,
        # Italian, and Portuguese" and CALLED NO TOOL. Three of those five have
        # nothing written in them. It could not have answered from evidence -- no tool
        # could answer the bare question, because get_progress and start_lesson both
        # require a language first, and this one returned only what the LEARNER had
        # practised, which is a different fact and is empty for somebody new.
        #
        # Answering it here keeps one source of truth and puts the list in front of
        # the model before the question is asked: this is the call the profile already
        # makes at the start of every conversation.
        #
        # Counts, never names -- a language name is not learner data, but the shape of
        # this line is the rule the whole module follows and the exception is not worth
        # the next reader's doubt.
        logger.info(
            "Tool call: get_profile languages=%d teachable=%d planned=%d",
            len(practised),
            len(with_material),
            len(without_material),
        )
        return {
            "display_name": profile.display_name,
            # Empty means the store could not be read OR no language has material, and
            # get_language_catalog's contract is that a caller treats those the same:
            # neither supports telling a person which languages are taught. The
            # description says what to do with two empty lists, which is to name none.
            **taught,
            "languages": [
                {
                    "code": language.code,
                    "name": language.name,
                    "attempts": language.attempts,
                    "lessons_completed": language.completed,
                }
                for language in practised
            ],
        }
