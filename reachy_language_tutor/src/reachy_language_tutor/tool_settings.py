"""Apply persisted tool settings to the active conversation."""

import asyncio
import logging
from typing import NoReturn
from pathlib import Path
from collections.abc import Callable, Coroutine
from concurrent.futures import Future

from reachy_mini.io.jsonrpc import JsonRpcError
from reachy_language_tutor.logging_safety import where, log_safe
from reachy_language_tutor.tools.core_tools import initialize_tools


logger = logging.getLogger(__name__)

RestartCallback = Callable[[str], Coroutine[None, None, None]]


def raise_tool_settings_error(reason: str, detail: str) -> NoReturn:
    """Raise a stable tool-settings error with user-facing detail."""
    raise JsonRpcError(detail, reason=reason, data={"detail": detail})


def _log_restart_completion(future: Future[None]) -> None:
    try:
        future.result()
    except Exception as exc:
        logger.error("Failed to restart the conversation after a tool change: %s at %s", log_safe(exc), where(exc))


def apply_tool_change(
    instance_path: str | Path | None,
    get_loop: Callable[[], asyncio.AbstractEventLoop | None],
    restart_conversation: RestartCallback,
    reason: str,
) -> str:
    """Reload active tools and reconnect a running conversation."""
    try:
        initialize_tools(instance_path=instance_path, force=True)
    except Exception as exc:
        logger.error("Failed to reload tools after saving tool settings: %s at %s", log_safe(exc), where(exc))
        return "Saved. Restart the conversation app to apply the tool changes."

    try:
        conversation_loop = get_loop()
    except Exception as exc:
        logger.error(
            "Failed to inspect the conversation loop after a tool change: %s at %s", log_safe(exc), where(exc)
        )
        return "Saved. Restart the conversation app to apply the tool changes."
    if conversation_loop is None or not conversation_loop.is_running():
        return "Tools will apply when the conversation starts or restarts."

    restart_coroutine: Coroutine[None, None, None] | None = None
    try:
        restart_coroutine = restart_conversation(reason)
        restart_future = asyncio.run_coroutine_threadsafe(
            restart_coroutine,
            conversation_loop,
        )
    except Exception as exc:
        if restart_coroutine is not None:
            restart_coroutine.close()
        logger.error(
            "Failed to schedule a conversation restart after a tool change: %s at %s", log_safe(exc), where(exc)
        )
        return "Saved. Restart the conversation app to apply the tool changes."
    restart_future.add_done_callback(_log_restart_completion)
    return "Reconnecting the conversation to apply the tool changes."
