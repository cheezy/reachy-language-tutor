import logging
from typing import Any

from reachy_language_tutor.learners import get_profile, store_is_available, get_practised_languages
from reachy_language_tutor.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class GetProfile(Tool):
    """Look up who the app is serving, and what they have practised."""

    name = "get_profile"
    description = (
        "Look up the current learner's name and which languages they have practised. Call this at the start of a "
        "conversation so you can greet them by name and pick up where they left off. It takes no arguments: you "
        "cannot choose whose profile to read, and you must not ask the person for a name or an id in order to call "
        "it. If it returns an error, say plainly that you cannot look their profile up right now, and never invent "
        "a name or a progress figure."
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
        learner_id = deps.current_learner_id
        if learner_id is None:
            logger.warning("get_profile: no current learner is set")
            return {"error": "I do not know who I am talking to yet, so I cannot look up a profile."}

        profile = get_profile(learner_id, instance_path=deps.instance_path)
        if profile is None:
            # "No such learner" and "cannot read the store" must not be said the same
            # way: one is a fact about the person, the other is a fault in the robot.
            if store_is_available(deps.instance_path):
                logger.warning("get_profile: the current learner has no profile row")
                return {"error": "I do not have a profile saved for you yet."}
            logger.error("get_profile: the learner store is unreadable")
            return {"error": "I cannot reach my records right now, so I cannot look your profile up."}

        practised = get_practised_languages(learner_id, instance_path=deps.instance_path)
        logger.info("Tool call: get_profile languages=%d", len(practised))
        return {
            "display_name": profile.display_name,
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
