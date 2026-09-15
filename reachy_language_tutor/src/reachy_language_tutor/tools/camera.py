import base64
import logging
from typing import Any, Dict

from reachy_language_tutor.utils import describe_for_log
from reachy_language_tutor.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)

# How many consecutive reads one picture may make before reporting no frame.
#
# The same arithmetic, and the same number, as faces/capture.py's _FRAME_ATTEMPTS:
# the read waits ~20ms for a sample while the camera produces one every ~33ms at
# 30fps, so a single read can miss simply by being early. Measured on 2026-09-14
# against the desktop app's mockup simulation, for this call specifically rather than
# inferred from the raw-frame one: 3 of 10 back-to-back get_frame_jpeg() calls returned
# bytes, 6 of 6 spaced 100ms apart did, and 18 of 18 at 100ms or more. Both reads are
# recorded side by side in the measurement table in docs/SETUP.md. Without this, asking the
# robot to look twice in a row told the person there was no frame while the camera was
# working perfectly. Deliberately duplicated rather than imported -- tools must not
# depend on the faces package -- and a test asserts the two numbers stay equal.
_FRAME_ATTEMPTS = 5


class Camera(Tool):
    """Take a picture with the camera to see what is in front of the robot."""

    name = "camera"
    description = (
        "Take a picture with the camera to see what is in front of the robot. "
        "Use this when the user asks you to look at something, see what they are holding, "
        "check their appearance, describe the scene, or comment on how they look. "
        "Also use it when the user asks what you can see or wants your visual opinion. "
        "The camera is live, each call captures the current moment. "
        "If the user asks you to look without saying at what, do not ask for clarification, call this tool and describe what you see. "
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": (
                    "What to observe or ask about in the picture. "
                    "Examples: what is the user holding, describe the user's outfit, "
                    "what do you see around you, how does the user look today."
                ),
            },
        },
        "required": ["question"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Take a picture with the camera and return the base64-encoded JPEG."""
        question = (kwargs.get("question") or "").strip()
        if not question:
            logger.warning("camera: empty question")
            return {"error": "question must be a non-empty string"}

        # The model composes this from the conversation, which by then holds the
        # profile get_profile returned -- "what is Alice holding" is within the
        # schema's own examples. INFO keeps the fact that the camera fired.
        logger.info("Tool call: camera question=%s", describe_for_log(question))
        logger.debug("Tool call: camera question=%s", question[:120])

        if not deps.camera_enabled:
            logger.error("Camera is disabled")
            return {"error": "Camera is disabled"}

        jpeg_bytes = None
        for _ in range(_FRAME_ATTEMPTS):
            jpeg_bytes = deps.reachy_mini.media.get_frame_jpeg()
            if jpeg_bytes is not None:
                break
        if jpeg_bytes is None:
            logger.error("No frame available from camera")
            return {"error": "No frame available"}

        return {"b64_im": base64.b64encode(jpeg_bytes).decode("utf-8")}
