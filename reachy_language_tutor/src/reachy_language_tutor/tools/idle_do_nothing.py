import logging
from typing import Any, Dict

from reachy_language_tutor.utils import describe_for_log
from reachy_language_tutor.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class IdleDoNothing(Tool):
    """Explicitly choose no action during an idle turn."""

    name = "idle_do_nothing"
    description = (
        "Use only in response to an idle time update when you intentionally want Reachy to stay still and silent "
        "instead of choosing another idle action."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "Optional reason for staying idle during this idle turn.",
            },
        },
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Stay still and silent for the current idle turn."""
        reason = kwargs.get("reason", "idle turn")
        # An open free-text field the model fills in: nothing stops it from quoting
        # whoever just spoke. Untruncated before, which made it the widest of the
        # four tool sinks. Kept free of the word W11's discovery guard scans for,
        # because this tool reads no personal data and must not join that set.
        logger.info("Tool call: idle_do_nothing reason=%s", describe_for_log(reason))
        logger.debug("Tool call: idle_do_nothing reason=%s", reason)
        return {"status": "idle", "reason": reason}
