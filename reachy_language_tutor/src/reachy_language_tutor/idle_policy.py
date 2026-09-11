"""Local idle tool selection and dispatch.

Default idle weights among active, registered tools:
- IdleDoNothing: 60%
- Dance: 16%
- PlayEmotion: 16%
- MoveHead: 8%

Profiles or sessions can disable tools. When one or more idle tools are unavailable, selection is renormalized across the
remaining active candidates.
"""

from __future__ import annotations
import json
import uuid
import random
import asyncio
import logging
from typing import Any, Final
from dataclasses import dataclass
from collections.abc import Mapping, Callable, Iterable

from reachy_language_tutor.tools import core_tools
from reachy_language_tutor.utils import tool_call_message, describe_json_for_log
from reachy_language_tutor.streaming import AdditionalOutputs
from reachy_language_tutor.tools.dance import Dance
from reachy_language_tutor.tools.move_head import MoveHead
from reachy_language_tutor.tools.play_emotion import PlayEmotion
from reachy_language_tutor.tools.idle_do_nothing import IdleDoNothing
from reachy_language_tutor.tools.background_tool_manager import (
    BackgroundTool,
    ToolCallRoutine,
    BackgroundToolManager,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IdleToolCandidate:
    """Weighted idle tool candidate and argument factory."""

    tool_type: type[core_tools.Tool]
    weight: float
    args_factory: Callable[[], dict[str, Any]]


def _no_args() -> dict[str, Any]:
    return {}


def _idle_do_nothing_args() -> dict[str, Any]:
    return {"reason": "random idle policy selected stillness"}


def _move_head_args() -> dict[str, Any]:
    return {"direction": random.choice(tuple(MoveHead.DELTAS))}


_IDLE_TOOL_CANDIDATES: Final[tuple[IdleToolCandidate, ...]] = (
    IdleToolCandidate(IdleDoNothing, 0.60, _idle_do_nothing_args),
    IdleToolCandidate(Dance, 0.16, _no_args),
    IdleToolCandidate(PlayEmotion, 0.16, _no_args),
    IdleToolCandidate(MoveHead, 0.08, _move_head_args),
)


def choose_idle_tool_call(
    available_tool_names: Iterable[str],
    *,
    tool_registry: Mapping[str, core_tools.Tool] | None = None,
) -> tuple[str, dict[str, Any]] | None:
    """Choose a weighted idle tool call from the tools available to the session."""
    available = set(available_tool_names)
    registry = core_tools.get_tools() if tool_registry is None else tool_registry
    candidates = [
        (tool.name, candidate)
        for candidate in _IDLE_TOOL_CANDIDATES
        for tool in registry.values()
        if tool.name in available and isinstance(tool, candidate.tool_type)
    ]
    if not candidates:
        return None

    selected_name, selected_candidate = random.choices(
        candidates,
        weights=[candidate.weight for _, candidate in candidates],
        k=1,
    )[0]
    return selected_name, selected_candidate.args_factory()


async def start_idle_tool_call(
    *,
    deps: core_tools.ToolDependencies,
    tool_manager: BackgroundToolManager,
    output_queue: asyncio.Queue[Any],
    available_tool_names: Iterable[str],
    idle_duration: float,
) -> BackgroundTool | None:
    """Start a locally selected idle tool and publish the console notification."""
    selected_tool = choose_idle_tool_call(available_tool_names)
    if selected_tool is None:
        logger.warning("No idle tools are available; idle action skipped")
        return None

    tool_name, arguments = selected_tool
    args_json_str = json.dumps(arguments)
    call_id = f"idle-{uuid.uuid4()}"
    background_tool = await tool_manager.start_tool(
        call_id=call_id,
        tool_call_routine=ToolCallRoutine(
            tool_name=tool_name,
            args_json_str=args_json_str,
            deps=deps,
        ),
        is_idle_tool_call=True,
    )
    await output_queue.put(
        AdditionalOutputs(
            tool_call_message("🛠️ Idle tool", tool_name, args_json_str, background_tool.tool_id),
        ),
    )
    logger.info(
        "Started local idle tool after %.1fs idle: %s (id=%s, call_id=%s, args=%s)",
        idle_duration,
        tool_name,
        background_tool.tool_id,
        call_id,
        # These arguments are built by args_factory() in this module, never by the
        # model, so their key names are ours to print.
        describe_json_for_log(args_json_str, trust_keys=True),
    )
    return background_tool
