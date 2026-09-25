"""Keep a learner's data out of the log lines that carry tool results.

A tool result is produced by a tool that is careful about what it logs, and then
logged AGAIN one layer up by code that knows nothing about what it is holding.
That second layer is where a household member's name actually reaches a log, so
this is where it has to be stopped.

Two sinks carry the same payload, and only one of them was obvious:
  - huggingface_realtime logs it at DEBUG when the tool completes;
  - console's play_loop logs whatever comes off the output queue at INFO, which
    is written whether or not --debug is on -- the worse of the two.
Both are covered here. A test that exercises only the tool would pass while
either leaked, which is exactly how this went unnoticed.

Conversation transcript reaches the same console sink by its own route, and D9
decided what happens to it: described at INFO, spoken at DEBUG. Those tests are
at the bottom of this file, beside the tool-result ones, because the two share a
single branch and a change to either can break the other.
"""

import re
import ast
import json
import asyncio
import logging
from typing import Any
from pathlib import Path
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
import pydantic

import reachy_language_tutor.huggingface_realtime as hf_mod

# Bound at module scope, which D28 is what made possible. Until test_external_loading.py,
# test_tool_space_runtime.py and test_profile_load_resilience.py stopped re-importing the
# tools package, a binding made here went stale the moment one of them ran: the re-import
# built a second Tool base class and _load_enabled_tools, which filters with issubclass,
# then matched nothing. See tests/tools_module_graph.py.
from reachy_language_tutor.tools import core_tools
from reachy_language_tutor.tools.get_profile import GetProfile
from reachy_language_tutor.utils import describe_for_log, tool_call_message, describe_json_for_log
from reachy_language_tutor.console import log_handler_message
from reachy_language_tutor.streaming import AdditionalOutputs
from reachy_language_tutor.tools.core_tools import ToolDependencies
from reachy_language_tutor.huggingface_realtime import HuggingFaceRealtimeHandler
from reachy_language_tutor.tools.background_tool_manager import (
    ToolState,
    ToolNotification,
    ToolCallRoutine,
    BackgroundToolManager,
)


LEARNER_NAME = "Alice Ferreira"
PROFILE_RESULT: dict[str, Any] = {
    "display_name": LEARNER_NAME,
    "languages": [{"code": "es", "name": "Spanish", "attempts": 3, "lessons_completed": 2}],
}


# --- The descriptor itself -------------------------------------------------------------


def test_describes_shape_without_any_value() -> None:
    """Keys are schema and survive; every value is replaced by its type."""
    described = describe_for_log(PROFILE_RESULT, trust_keys=True)

    assert "display_name" in described and "languages" in described
    assert LEARNER_NAME not in described
    assert "Spanish" not in described
    assert "str(len=" in described


def test_counts_are_not_reproduced_either() -> None:
    """Lesson counts are a learner's progress, so the number itself does not survive."""
    described = describe_for_log({"lessons_completed": 7}, trust_keys=True)

    assert "7" not in described
    assert "lessons_completed: int" in described


def test_describes_every_shape_a_tool_returns() -> None:
    """The tools return flat dicts, one nested list of dicts, and error dicts."""
    assert describe_for_log({}) == "{}"
    assert describe_for_log([]) == "[]"
    assert describe_for_log(None) == "None"
    assert describe_for_log({"ok": True}, trust_keys=True) == "{ok: bool}"
    assert describe_for_log({"repeat": 2}, trust_keys=True) == "{repeat: int}"
    assert describe_for_log(["a", "b"]) == "[2 x str(len=1)]"
    assert describe_for_log({"error": "nope"}, trust_keys=True) == "{error: str(len=4)}"


def test_the_default_suppresses_key_names() -> None:
    """The safe outcome must be what a caller gets by not thinking about it.

    Rendering a key name is only correct when the keys are our own schema, and the
    wrong choice is silent -- no error, no failing test, just a name in a log. So
    naming keys is the thing a caller has to ask for.
    """
    assert describe_for_log({"Alice Ferreira": "practise"}) == "{1 keys: str(len=8)}"
    assert describe_for_log({"display_name": "x"}, trust_keys=True) == "{display_name: str(len=1)}"


def test_trust_in_keys_does_not_propagate_into_nesting() -> None:
    """A caller can vouch for the envelope it built, not for what a tool nested in it.

    A Space tool's result carries keys a third party chose, and at milestone 5 the
    learner tools become that shape too -- their results will arrive from a hosted
    backend rather than from a local dict. So a result keyed by data at any depth
    below the top must not print those keys, even to a caller that passed
    trust_keys=True for the envelope.
    """
    remote_shaped = {"display_name": "x", "structured_content": {LEARNER_NAME: "es-3"}}

    described = describe_for_log(remote_shaped, trust_keys=True)

    assert LEARNER_NAME not in described
    # The top level is where the dispatch signal lives, and it survives.
    assert "display_name" in described and "structured_content" in described


@pytest.mark.asyncio
async def test_a_nested_data_key_does_not_reach_the_loops_log(
    monkeypatch: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """The same shape through the real loop, since that is where trust_keys is passed."""
    handler = _handler(monkeypatch)
    handler._in_flight_tool_calls = {"call_remote"}

    with caplog.at_level(logging.DEBUG):
        await handler._handle_tool_result(
            ToolNotification(
                id="call_remote",
                tool_name="some_space_tool",
                is_idle_tool_call=False,
                status=ToolState.COMPLETED,
                result={"structured_content": {LEARNER_NAME: "es-3"}},
            )
        )

    assert LEARNER_NAME not in " ".join(record.getMessage() for record in caplog.records)
    assert LEARNER_NAME not in handler.output_queue.get_nowait().args[0]["log_safe"]


@pytest.mark.asyncio
async def test_a_remote_tools_envelope_keys_are_not_trusted(
    monkeypatch: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """A Space tool composes its own envelope, so its top-level keys are not ours."""
    handler = _handler(monkeypatch, local_tools=())
    remote = MagicMock(spec=core_tools.RemoteMcpTool, log_keys_are_ours=False)
    monkeypatch.setattr(core_tools, "get_tools", lambda: {"space_tool": remote})
    handler._in_flight_tool_calls = {"call_r"}

    with caplog.at_level(logging.DEBUG):
        await handler._handle_tool_result(
            ToolNotification(
                id="call_r",
                tool_name="space_tool",
                is_idle_tool_call=False,
                status=ToolState.COMPLETED,
                result={LEARNER_NAME: "es-3"},
            )
        )

    assert LEARNER_NAME not in " ".join(record.getMessage() for record in caplog.records)


def test_a_bool_is_not_described_as_an_int() -> None:
    """Bool is a subclass of int, so the order of the checks is load-bearing."""
    assert describe_for_log(True) == "bool"


def test_pathological_nesting_terminates() -> None:
    """A runaway structure must not turn one log line into a wall of text."""
    deep: Any = "leaf"
    for _ in range(40):
        deep = {"next": deep}

    assert "..." in describe_for_log(deep)


# --- Sink 1: the realtime loop's DEBUG line ---------------------------------------------


@contextmanager
def caplog_at_warning() -> Any:
    """Capture WARNING records from anywhere, without pytest's caplog propagation rules."""
    records: list[str] = []

    class _Sink(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    sink = _Sink(level=logging.WARNING)
    root = logging.getLogger()
    root.addHandler(sink)
    previous = root.level
    root.setLevel(logging.WARNING)
    try:
        yield records
    finally:
        root.removeHandler(sink)
        root.setLevel(previous)


def _handler(monkeypatch: Any, *, local_tools: tuple[str, ...] = ("get_profile",)) -> HuggingFaceRealtimeHandler:
    """Build a handler that can complete a tool call with no websocket and no robot.

    The registry is pinned rather than loaded: whether a result's envelope keys are
    ours is decided from it at log time, and another test in this suite reloads the
    tools modules, so a real lookup would make these assertions depend on test order.
    """
    monkeypatch.setattr(hf_mod, "get_session_instructions", lambda _instance_path=None: "test")
    monkeypatch.setattr(hf_mod, "get_session_voice", lambda default=None: "Aiden")
    monkeypatch.setattr(hf_mod, "get_tool_specs", lambda: [])
    registry = {name: MagicMock(spec=core_tools.Tool, log_keys_are_ours=True) for name in local_tools}
    monkeypatch.setattr(core_tools, "get_tools", lambda: registry)

    handler = HuggingFaceRealtimeHandler(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()))
    handler.connection = AsyncMock()
    handler.output_queue = asyncio.Queue()
    monkeypatch.setattr(handler, "_wait_for_response_done_before_tool_result", AsyncMock(return_value=True))
    monkeypatch.setattr(handler, "_safe_response_create", AsyncMock())
    return handler


def _profile_call(log_keys_trusted: bool = True) -> ToolNotification:
    """Build a completed get_profile call carrying a real-looking learner name.

    `log_keys_trusted` is what the manager stamps at dispatch, and get_profile is a
    locally-registered tool, so True is what production would carry here. It is a
    parameter rather than a constant because the interesting case is the other one:
    the field defaults to False on the model, so anything that forgets to set it
    fails closed.
    """
    return ToolNotification(
        id="call_a",
        tool_name="get_profile",
        is_idle_tool_call=False,
        status=ToolState.COMPLETED,
        result=dict(PROFILE_RESULT),
        log_keys_trusted=log_keys_trusted,
    )


@pytest.mark.asyncio
async def test_the_realtime_loop_does_not_log_the_learners_name(
    monkeypatch: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """The sink named in the defect: DEBUG, one layer above the tool."""
    handler = _handler(monkeypatch)
    handler._in_flight_tool_calls = {"call_a"}

    with caplog.at_level(logging.DEBUG):
        await handler._handle_tool_result(_profile_call())

    logged = " ".join(record.getMessage() for record in caplog.records)
    assert LEARNER_NAME not in logged
    assert "Spanish" not in logged
    # The debug signal survives: which tool, and what shape it returned.
    assert "get_profile" in logged
    assert "display_name" in logged


@pytest.mark.asyncio
async def test_a_notification_that_forgets_the_verdict_is_not_trusted(
    monkeypatch: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """The field defaults to False, and that default is the control.

    `log_keys_trusted` is set by the manager at dispatch. Anything that builds a
    notification another way -- an older producer, a test, a future code path --
    gets False and has its envelope keys suppressed. The alternative default would
    hand a learner's name to the log on any path somebody forgot about, which is
    how this layer has been broken twice already (D3, D9).
    """
    handler = _handler(monkeypatch)
    handler._in_flight_tool_calls = {"call_a"}

    with caplog.at_level(logging.DEBUG):
        await handler._handle_tool_result(_profile_call(log_keys_trusted=False))

    logged = " ".join(record.getMessage() for record in caplog.records)
    assert LEARNER_NAME not in logged
    assert "display_name" not in logged, f"an unset verdict was treated as permission: {logged}"


@pytest.mark.asyncio
async def test_the_model_still_receives_the_real_name(monkeypatch: Any) -> None:
    """Redaction is for the log only -- Reachy cannot greet someone by a type name."""
    handler = _handler(monkeypatch)
    handler._in_flight_tool_calls = {"call_a"}

    await handler._handle_tool_result(_profile_call())

    submitted = json.dumps(
        [call.kwargs.get("item") for call in handler.connection.conversation.item.create.await_args_list]
    )
    assert LEARNER_NAME in submitted


@pytest.mark.asyncio
async def test_the_queued_message_still_carries_the_real_name_for_the_ui(monkeypatch: Any) -> None:
    """The console displays the content; only what it LOGS is redacted."""
    handler = _handler(monkeypatch)
    handler._in_flight_tool_calls = {"call_a"}

    await handler._handle_tool_result(_profile_call())

    message = handler.output_queue.get_nowait().args[0]
    assert LEARNER_NAME in message["content"]
    assert message["kind"] == "tool_result"
    assert LEARNER_NAME not in message["log_safe"]


@pytest.mark.asyncio
async def test_the_camera_image_never_reaches_the_model_or_the_log(
    monkeypatch: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """The pre-existing b64_im stripping, pinned so a change to it cannot pass quietly.

    It is not personal data, it is bulk -- but nothing guarded it, and the shape of
    that guard is the same one this defect is about.
    """
    handler = _handler(monkeypatch)
    handler._in_flight_tool_calls = {"call_cam"}
    payload = "QUJDREVGRw" * 40

    with caplog.at_level(logging.DEBUG):
        await handler._handle_tool_result(
            ToolNotification(
                id="call_cam",
                tool_name="camera",
                is_idle_tool_call=False,
                status=ToolState.COMPLETED,
                result={"b64_im": payload},
            )
        )

    items = [call.kwargs.get("item") for call in handler.connection.conversation.item.create.await_args_list]
    outputs = [item for item in items if item.get("type") == "function_call_output"]
    assert outputs, "the camera call produced no function_call_output"
    rendered = json.dumps(outputs)
    assert "image_attached" in rendered
    assert payload not in rendered, "b64_im must be stripped from the tool-result payload"

    # The image itself still reaches the model, deliberately, as its own content item --
    # that is what the camera tool is for. What must not happen is the base64 riding
    # along inside the tool result, or reaching a log.
    assert any(payload in json.dumps(item) for item in items if item not in outputs)

    message = handler.output_queue.get_nowait().args[0]
    assert payload not in message["log_safe"]
    assert payload not in " ".join(record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_a_tool_that_raises_does_not_put_its_message_in_the_log(
    monkeypatch: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """The error branch is ERROR, so it is written whether or not --debug is on.

    Shaped after a learner-writing tool -- W9's record_result, since replaced by
    finish_lesson (W16); the NAME here is a synthetic string in a fabricated log line
    and names no real tool. It is handed a
    lesson outcome, so an exception it raises is the likeliest way a learner's data
    reaches this line. Tools are required to return an error dict rather than raise,
    but this layer cannot tell a safe constant from an interpolated name.
    """
    handler = _handler(monkeypatch)
    handler._in_flight_tool_calls = {"call_err"}

    with caplog.at_level(logging.DEBUG):
        await handler._handle_tool_result(
            ToolNotification(
                id="call_err",
                tool_name="record_result",
                is_idle_tool_call=False,
                status=ToolState.FAILED,
                error=f"ValueError: lesson es-3 already recorded for {LEARNER_NAME}",
            )
        )

    logged = " ".join(record.getMessage() for record in caplog.records)
    assert LEARNER_NAME not in logged
    assert "es-3" not in logged
    # Still enough to act on: which tool, and that it failed.
    assert "record_result" in logged


# --- The other direction: what a tool is CALLED WITH ---------------------------------------


def test_tool_arguments_are_described_not_echoed() -> None:
    """W9 hands the lesson outcome IN the arguments, so the result side is only half."""
    described = describe_json_for_log(json.dumps({"lesson_id": "es-3", "outcome": "completed", "score": 90}))

    assert "es-3" not in described
    assert "completed" not in described
    assert "90" not in described
    # Nor the key names: this payload is composed by the model, so a KEY can carry
    # whatever a person just said. Only a tool result's keys are our own schema.
    assert "lesson_id" not in described
    assert described == "{3 keys: str(len=4), str(len=9), int}"


def test_a_model_composed_key_cannot_smuggle_a_name_into_the_log() -> None:
    """The argument object is the model's to shape, so its keys are untrusted too."""
    described = describe_json_for_log(json.dumps({"Alice Ferreira": "practise"}))

    assert "Alice Ferreira" not in described


def test_a_tool_results_own_keys_are_still_named() -> None:
    """Suppressing keys on the way in must not cost the debug signal on the way out."""
    described = describe_json_for_log(json.dumps(PROFILE_RESULT), trust_keys=True)

    assert "display_name" in described
    assert LEARNER_NAME not in described


def test_unparseable_arguments_fail_closed() -> None:
    """The least trustworthy input must not be the one that gets echoed verbatim."""
    described = describe_json_for_log("{not json at all, Alice Ferreira")

    assert "Alice Ferreira" not in described
    assert described.startswith("str(len=")


def test_non_string_arguments_are_still_described(caplog: pytest.LogCaptureFixture) -> None:
    """The invalid-tool-call branch passes whatever arrived, which may not be a string."""
    assert describe_json_for_log(None) == "None"
    assert describe_json_for_log({"learner": "Alice"}) == "{1 keys: str(len=5)}"


def test_malformed_arguments_are_not_echoed_by_the_dispatcher() -> None:
    """The same payload, one layer below the line that redacts it.

    _safe_load_obj is reached through both dispatch paths, logs at WARNING (which
    --debug does not gate), and is the branch a truncated realtime argument stream
    lands on -- so it is exactly where a W9 lesson result would be echoed.
    """
    truncated = '{"lesson_id": "es-3", "outcome": "completed", "notes": "Alice Ferreira did wel'

    with caplog_at_warning() as records:
        assert core_tools._safe_load_obj(truncated) == {}

    logged = " ".join(records)
    assert LEARNER_NAME not in logged
    assert "es-3" not in logged
    assert "str(len=" in logged


@pytest.mark.asyncio
async def test_a_remote_tools_first_failure_is_not_echoed(monkeypatch: Any) -> None:
    """The retry path logs at WARNING, which --debug does not gate.

    A remote server's error is written in response to a call whose arguments were
    the learner's data, so it can quote them back. Only the RETRY's failure reaches
    the dispatch-level guard, so the first failure needs its own.
    """
    leaky = f"Failed to call MCP tool 'record_result' from 'backend': no lesson es-3 for {LEARNER_NAME}"

    class _Client:
        def __init__(self) -> None:
            self.calls = 0

        async def call_tool(self, name: str, kwargs: Any) -> dict[str, Any]:
            self.calls += 1
            if self.calls == 1:
                raise core_tools.McpToolInvocationError(leaky)
            return {"status": "ok"}

    tool = core_tools.RemoteMcpTool(
        name="record_result",
        description="d",
        parameters_schema={"type": "object", "properties": {}},
        client=_Client(),
        client_tool_name="record_result",
        slug="owner/space",
    )
    monkeypatch.setattr(core_tools, "_REMOTE_TOOL_RETRY_DELAY_S", 0)
    with caplog_at_warning() as records:
        await tool(MagicMock())

    logged = " ".join(records)
    assert LEARNER_NAME not in logged
    assert "es-3" not in logged
    assert "McpToolInvocationError" in logged


# --- The producer of the tag, not just the consumer of it ----------------------------------


def test_the_tool_call_message_tags_itself() -> None:
    """Deleting this tag leaves every other test green while arguments log in cleartext.

    The consumer tests prove an already-tagged message is redacted. Nothing proved a
    producer tags, which is the half that a future call site gets wrong.
    """
    args = json.dumps({"lesson_id": "es-3", "outcome": "completed", "learner": LEARNER_NAME})

    message = tool_call_message("Used tool", "record_result", args, "t1")

    assert message["kind"] == "tool_call"
    # The UI shows the person their own arguments; the log does not.
    assert LEARNER_NAME in message["content"]
    assert LEARNER_NAME not in message["log_safe"]
    assert "es-3" not in message["log_safe"]


def test_the_tool_call_message_logs_no_cleartext_end_to_end(caplog: pytest.LogCaptureFixture) -> None:
    """Producer and consumer together, since each is only half the control."""
    args = json.dumps({"notes": LEARNER_NAME})

    logged = _log_one(tool_call_message("Used tool", "record_result", args, "t1"), caplog)

    assert LEARNER_NAME not in logged
    assert "record_result" in logged


@pytest.mark.asyncio
async def test_the_idle_producer_tags_its_message_too(monkeypatch: Any) -> None:
    """The second producer. A hand-built dict here would log in cleartext."""
    from reachy_language_tutor import idle_policy

    queue: asyncio.Queue = asyncio.Queue()
    manager = MagicMock()
    manager.start_tool = AsyncMock(return_value=MagicMock(tool_id="t9"))
    monkeypatch.setattr(
        idle_policy,
        "choose_idle_tool_call",
        lambda *a, **k: ("idle_do_nothing", {"reason": "quiet"}),
    )

    await idle_policy.start_idle_tool_call(
        tool_manager=manager,
        output_queue=queue,
        deps=ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()),
        available_tool_names=["idle_do_nothing"],
        idle_duration=12.0,
    )

    message = queue.get_nowait().args[0]
    assert message["kind"] == "tool_call"
    assert "log_safe" in message


# --- Sink 2: the console's INFO line, which --debug does not gate -------------------------


def _log_one(message: dict[str, Any], caplog: pytest.LogCaptureFixture) -> str:
    """Run one queued message through the console's REAL logging branch.

    Calls log_handler_message rather than restating what it does -- a test that
    reimplements the branch passes while the branch it is meant to guard leaks.
    """
    with caplog.at_level(logging.INFO):
        for msg in AdditionalOutputs(message).args:
            log_handler_message(msg)
    return " ".join(record.getMessage() for record in caplog.records)


def test_the_console_prefers_the_log_safe_rendering(caplog: pytest.LogCaptureFixture) -> None:
    """The console sink is INFO, so it is written whether or not --debug is on."""
    logged = _log_one(
        {
            "role": "assistant",
            "content": json.dumps(PROFILE_RESULT),
            "kind": "tool_result",
            "log_safe": describe_for_log(PROFILE_RESULT, trust_keys=True),
        },
        caplog,
    )

    assert LEARNER_NAME not in logged
    assert "display_name" in logged


def _log_one_at_debug(message: dict[str, Any], caplog: pytest.LogCaptureFixture) -> str:
    """The same real branch, read by an operator who passed --debug.

    Separate from _log_one because the whole D9 decision is a difference between
    two levels: a helper that captured both would be unable to express it.
    """
    with caplog.at_level(logging.DEBUG):
        for msg in AdditionalOutputs(message).args:
            log_handler_message(msg)
    return " ".join(record.getMessage() for record in caplog.records)


def _transcript(role: str, content: str) -> dict[str, Any]:
    """A queue message exactly as the four transcript producers build one: no kind."""
    return {"role": role, "content": content}


def test_a_tool_result_with_no_rendering_is_still_redacted(caplog: pytest.LogCaptureFixture) -> None:
    """The seam fails closed: the KIND decides, not the presence of a rendering.

    A producer that forgets log_safe, or builds a malformed one, must not fall
    through to cleartext -- the default outcome of this control has to be redaction.

    These records carry no verdict, and D34 changed what that means. The branch used
    to trust a result's keys on the strength of its kind alone; it now reads the
    `log_keys_trusted` the producer stamped, and an absent one is not a yes. So the
    key names are suppressed for BOTH kinds here, where a result's used to come back
    -- and that is the fix, not a loss: the records that legitimately want their keys
    named are the ones carrying a verdict to justify it, covered by
    test_the_fallback_names_the_keys_of_a_tool_this_app_wrote_itself below.
    """
    raw = json.dumps(PROFILE_RESULT)

    for kind in ("tool_result", "tool_call"):
        for broken in ({"log_safe": None}, {"log_safe": 42}, {}):
            caplog.clear()
            logged = _log_one({"role": "assistant", "content": raw, "kind": kind, **broken}, caplog)
            assert LEARNER_NAME not in logged, (kind, broken)
            assert "str(len=" in logged, (kind, broken)
            # Degraded, and with no name to justify trusting the envelope, not even
            # the key names come back. An absent tool_name is not permission.
            assert "display_name" not in logged, (kind, broken)


def test_the_fallback_does_not_trust_a_remote_tools_envelope_keys(caplog: pytest.LogCaptureFixture) -> None:
    """D34: the branch that runs WHEN THE PRODUCER FAILED was the unsafe one.

    A RemoteMcpTool returns dict(result) straight from a third-party Space, so its
    top-level keys are written by whoever wrote the Space and can be anything --
    including a learner's name, which is the shape used here. The producer already
    knew that and asked core_tools; this fallback asked only whether the record's
    kind was "tool_result" and answered yes for every tool. So a name arriving as an
    envelope KEY rendered in cleartext at INFO, on the one path that exists because
    the producer is not to be relied on.

    Driven through log_handler_message, not through the handler: the handler path
    already passed (test_a_remote_tools_envelope_keys_are_not_trusted above), and a
    test that exercised it would have gone green over this defect for the same
    reason the real session log did.

    Not reachable in today's tree -- the in-tree producer always supplies a string
    log_safe -- and that is the point: milestone 5 makes the learner tools
    remote-shaped, with a household's data behind them.
    """
    logged = _log_one(
        {
            "role": "assistant",
            "content": json.dumps({LEARNER_NAME: "es-3", "score": 88}),
            "kind": "tool_result",
            "log_keys_trusted": False,
            "log_safe": None,
        },
        caplog,
    )

    assert LEARNER_NAME not in logged, f"a learner name reached the log as an envelope key: {logged}"
    assert "2 keys" in logged, f"the shape should still be described: {logged}"


def test_the_fallback_names_the_keys_of_a_tool_this_app_wrote_itself(caplog: pytest.LogCaptureFixture) -> None:
    """The other half of the same decision, so the fix is not just "suppress it all".

    A locally-registered tool composes its own return dict, so its top-level keys are
    this application's schema and are most of the dispatch signal an operator reads.
    Suppressing those too would be a safe change and a worse one, and nothing would
    have failed -- which is exactly how a fix becomes a regression.
    """
    logged = _log_one(
        {
            "role": "assistant",
            "content": json.dumps(PROFILE_RESULT),
            "kind": "tool_result",
            "log_keys_trusted": True,
            "log_safe": None,
        },
        caplog,
    )

    assert LEARNER_NAME not in logged, f"the VALUE is never logged, only the key: {logged}"
    assert "display_name" in logged, f"our own schema keys are worth seeing: {logged}"


def test_a_tool_that_could_not_be_resolved_is_not_trusted() -> None:
    """None is what an unknown name resolves to, and None is never permission.

    D36 moved the rule from a NAME to the resolved object, which removed the
    registry from the rule entirely -- it no longer looks anything up, so it can no
    longer raise, and the previous version of this test (an exploding get_tools)
    tests nothing about it any more. What survives from that test is the property
    that actually mattered: the unresolvable case answers no.

    `_dispatch_tool_call` returns None as the tool when the registry does not know
    the name, so this is the value that really arrives, not a hypothetical.
    """
    assert core_tools.log_trust_for_resolved_tool(None) is False


@pytest.mark.parametrize(
    "absent", [{}, {"log_keys_trusted": None}, {"log_keys_trusted": "yes"}, {"log_keys_trusted": 1}]
)
def test_only_a_literal_true_verdict_is_trusted(absent: dict[str, Any], caplog: pytest.LogCaptureFixture) -> None:
    """An absent or oddly-typed verdict is not a yes.

    The record contract is the whole control now, so the thing that must not happen
    is a truthy value standing in for a decision nobody made. `1` and `"yes"` are
    both truthy; neither is a producer saying these keys are ours.
    """
    logged = _log_one(
        {
            "role": "assistant",
            "content": json.dumps(PROFILE_RESULT),
            "kind": "tool_result",
            "log_safe": None,
            **absent,
        },
        caplog,
    )

    assert LEARNER_NAME not in logged
    assert "display_name" not in logged, f"a non-True verdict was treated as permission: {logged}"


def _mentions_rule(path: Path, rule: str) -> bool:
    """Say whether a module reaches the named function by any spelling an AST can see.

    Three spellings, because review found the first version catching only one:

    * a call -- `rule()` or `mod.rule()`, read from ast.Call;
    * an import -- `from ... import rule` or `... as r`, which is how an alias gets
      its other name, so the alias is caught at the point it is created rather than
      at the point it is used;
    * a string literal equal to the name, which is what `getattr(mod, "rule")` is
      made of.

    A module that imports the rule and never calls it is reported too. That is
    deliberate: for a rule whose whole point is having one caller, importing it is
    already the interesting event.

    THE LIMIT, stated rather than glossed: a name assembled at runtime --
    `getattr(mod, "result_keys" + "_are_ours")` -- is invisible to any static read,
    and no version of this guard will see it. What this closes is the accidental
    second caller, which is what actually happened here; it is not a defence against
    someone deliberately hiding one.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:  # a module that does not parse cannot be cleared by reading it
        raise AssertionError(f"{path} does not parse, so this guard cannot clear it") from None

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == rule:
                return True
            if isinstance(func, ast.Attribute) and func.attr == rule:
                return True
        elif isinstance(node, ast.ImportFrom):
            if any(alias.name == rule for alias in node.names):
                return True
        elif isinstance(node, ast.Constant) and node.value == rule:
            return True
    return False


def _real_local_tool(result: dict[str, Any]) -> core_tools.Tool:
    """Build a REAL in-tree Tool, not a double, returning *result*.

    Doubles cannot stand in here any more and that is the point. The rule now asks
    two positive questions -- is the marker True, and is the class defined under
    this package's own tools -- and a MagicMock answers neither honestly: its
    marker is whatever the test supplied and its __module__ is unittest.mock. Review
    measured what that cost: with every double supplying the marker, flipping
    RemoteMcpTool's real opt-out to True left the entire suite green. So these tests
    subclass a real in-tree tool, which carries the real class attributes and the
    real provenance.
    """

    class _Local(GetProfile):
        async def __call__(self, deps: Any, **kwargs: Any) -> dict[str, Any]:
            return dict(result)

    # Defined in a test module, so give it the provenance its base has -- the thing
    # under test is the RULE, and a class defined here would otherwise be refused on
    # a technicality that says nothing about whether a local tool is trusted.
    _Local.__module__ = GetProfile.__module__
    return _Local()


def _real_remote_tool(result: dict[str, Any]) -> core_tools.RemoteMcpTool:
    """Build a REAL RemoteMcpTool, carrying its own class-level opt-out."""

    class _Remote(core_tools.RemoteMcpTool):
        def __init__(self) -> None:
            self.name = "space_tool"
            self.description = "a third party's tool"
            self.parameters_schema = {"type": "object", "properties": {}}

        async def __call__(self, deps: Any, **kwargs: Any) -> dict[str, Any]:
            return dict(result)

    _Remote.__module__ = core_tools.RemoteMcpTool.__module__
    return _Remote()


def test_the_real_classes_carry_the_markers_the_rule_reads() -> None:
    """The class attributes themselves, because the doubles stopped covering them.

    Review flipped RemoteMcpTool.log_keys_are_ours to True -- deleting the single
    attribute that stops a third-party Space's envelope keys being named at INFO --
    and the whole suite stayed green, because every double supplied its own marker.
    A test that reads the value it just wrote proves nothing about the class.
    """
    assert core_tools.RemoteMcpTool.log_keys_are_ours is False
    assert core_tools.Tool.log_keys_are_ours is True
    assert GetProfile.log_keys_are_ours is True

    assert core_tools.log_trust_for_resolved_tool(_real_remote_tool({})) is False
    assert core_tools.log_trust_for_resolved_tool(_real_local_tool({})) is True


def test_a_tool_loaded_from_the_external_directory_is_refused_on_provenance_alone() -> None:
    """The other leg of the rule, isolated so it is revert-proofed by itself.

    `TOOLS_DIRECTORY` loads external tool FILES and their classes enter the registry
    as ordinary Tools under `_EXTERNAL_TOOL_MODULE_NAMESPACE`. They inherit the
    base's marker of True, so the MARKER cannot refuse them -- provenance is the only
    thing that does, and until this test nothing checked it: widening
    `_OUR_TOOLS_NAMESPACE` from "reachy_language_tutor.tools." to
    "reachy_language_tutor." -- exactly the error core_tools warns against, since
    _external_tools sits under the package too -- undid the security fix and failed
    no test at all.

    The marker is left at its inherited True deliberately. If this class opted out
    the test would pass on the marker and say nothing about provenance, which is the
    mistake the sibling test above made in the other direction.
    """

    class DroppedInByAnOperator(GetProfile):
        """A file-backed tool that proxies somebody else's service."""

    assert DroppedInByAnOperator.log_keys_are_ours is True, "the marker must not be what refuses this"
    DroppedInByAnOperator.__module__ = f"{core_tools._EXTERNAL_TOOL_MODULE_NAMESPACE}.dropped_in"

    assert core_tools.log_trust_for_resolved_tool(DroppedInByAnOperator()) is False


def test_a_tool_that_wraps_somebody_elses_service_must_opt_in_to_being_logged() -> None:
    """Trust is a marker on the class, so a NEW kind of remote tool is not trusted.

    The subclass test this replaced -- `not isinstance(tool, RemoteMcpTool)` -- was
    trusted-by-default for anything that was not literally that class. The security
    review of D36 showed that is reachable today rather than after some future
    refactor: TOOLS_DIRECTORY loads external tool FILES whose classes enter the
    registry as plain Tools, so a file-backed proxy returning a third party's payload
    verbatim would have had its envelope keys printed.

    `is True` rather than a truthiness test, so a subclass that sets the marker to
    something odd is refused rather than accepted.
    """

    class WrapsSomeoneElse(core_tools.Tool):
        """A plain Tool that is not a RemoteMcpTool and is not ours to trust."""

        log_keys_are_ours = False
        name = "proxy"
        description = "returns a third party's payload"
        parameters_schema: dict[str, Any] = {"type": "object", "properties": {}}

        async def __call__(self, deps: Any, **kwargs: Any) -> dict[str, Any]:
            return {}

    class ForgotToSayAnything(WrapsSomeoneElse):
        """Inherits the refusal rather than silently regaining trust."""

    class SaysSomethingOdd(core_tools.Tool):
        """A marker that is truthy but not True is not permission."""

        log_keys_are_ours = "yes"  # type: ignore[assignment]
        name = "odd"
        description = "sets the marker to a truthy non-bool"
        parameters_schema: dict[str, Any] = {"type": "object", "properties": {}}

        async def __call__(self, deps: Any, **kwargs: Any) -> dict[str, Any]:
            return {}

    # Stamped IN-TREE on purpose, so the provenance leg cannot be what refuses them
    # and only the marker is under test. Without this the test passed for the wrong
    # reason: all three are defined in this module, so provenance refused them and
    # deleting the marker condition entirely would have left it green.
    for cls in (WrapsSomeoneElse, ForgotToSayAnything, SaysSomethingOdd):
        cls.__module__ = GetProfile.__module__

    assert core_tools.log_trust_for_resolved_tool(WrapsSomeoneElse()) is False
    assert core_tools.log_trust_for_resolved_tool(ForgotToSayAnything()) is False
    assert core_tools.log_trust_for_resolved_tool(SaysSomethingOdd()) is False


def test_the_rule_answers_no_for_a_space_tool_and_yes_for_one_this_app_registered() -> None:
    """The rule itself, both ways, since everything else now trusts its verdict.

    A RemoteMcpTool returns dict(result) from a third-party Space, so its envelope
    keys belong to whoever wrote it. A locally-registered tool composes its own
    return dict here, so its keys are this application's schema.

    It takes the resolved OBJECT since D36 -- no registry, no name, nothing to be
    rebound between asking and answering.
    """
    assert core_tools.log_trust_for_resolved_tool(_real_remote_tool({})) is False
    assert core_tools.log_trust_for_resolved_tool(_real_local_tool({})) is True
    # A double is refused too, and that is correct rather than inconvenient: its
    # provenance is unittest.mock and its marker is whatever the test supplied, so
    # it can answer neither question the rule asks.
    assert core_tools.log_trust_for_resolved_tool(MagicMock(spec=core_tools.Tool, log_keys_are_ours=True)) is False
    # Not a Tool at all, which is what a future caller passing the wrong thing looks
    # like. Anything that is not recognisably ours is not ours.
    for not_a_tool in (None, "get_profile", 5, object()):
        assert core_tools.log_trust_for_resolved_tool(not_a_tool) is False, not_a_tool


@pytest.mark.parametrize("coercible", ["yes", 1, "true", 0])
def test_the_verdict_field_refuses_a_value_that_merely_looks_true(coercible: Any) -> None:
    """StrictBool, pinned -- relaxing it to `bool` left the whole suite green.

    The console requires `log_keys_trusted is True` and a sibling test pins that. If
    the model coerced on the way in, the two ends of one carried value would disagree
    about what counts as a yes, and a future producer forwarding a field out of a
    JSON payload would get permission from a truthy string.

    Asserted on the error TYPE, not merely that something raised: pydantic's own
    `bool_type`, so this fails for the reason it names rather than for a typo.
    """
    with pytest.raises(pydantic.ValidationError) as caught:
        ToolNotification(
            id="x",
            tool_name="get_profile",
            is_idle_tool_call=False,
            status=ToolState.COMPLETED,
            log_keys_trusted=coercible,
        )

    assert [error["type"] for error in caught.value.errors()] == ["bool_type"]


@pytest.mark.asyncio
async def test_the_manager_stamps_the_verdict_from_the_tool_that_actually_ran(monkeypatch: Any) -> None:
    """The one expression the whole design rests on, pinned behaviourally.

    Everything downstream -- the handler, the record, the console -- carries this
    verdict rather than deriving it, so the single place it IS derived is the single
    point of failure. Nothing tested it until D34's third security pass: replacing
    it with a bare `True` left the entire suite green, which is the original defect
    restored at the producer.

    D36 moved WHERE it is derived, and this test moved with it. It used to assert on
    `start_tool`'s synchronous return, because the verdict was taken from the tool
    NAME at dispatch. It is now taken from the tool OBJECT that ran, in `_run_tool`,
    so the assertion has to wait for the tool to finish -- and the waiting is the
    point rather than an inconvenience: a verdict that exists before the tool has
    been resolved is precisely the one that could be about a different tool.

    THE RACE THIS CLOSES, exercised rather than described: the registry is rebound
    between dispatch and execution, which `initialize_tools(force=True)` can do
    off-loop. The verdict must follow the tool that ran, not the one the name meant
    when the work was queued.

    Both directions, because a guard that only checks the safe answer would pass an
    implementation that always says no and quietly costs an operator the dispatch
    signal they read.
    """
    # AsyncMock so the tool genuinely RUNS and _run_tool takes its success branch --
    # a MagicMock is not awaitable, and a version of this test using one exercised
    # the error path while appearing to test the happy one.
    remote = _real_remote_tool({"envelope": "from a Space"})
    local = _real_local_tool({"display_name": "..."})

    async def _run(name: str, registry_at_run: dict[str, Any]) -> bool:
        monkeypatch.setattr(core_tools, "get_tools", lambda: registry_at_run)
        manager = BackgroundToolManager()
        tool = await manager.start_tool(
            call_id=f"call-{name}-{len(registry_at_run)}",
            tool_call_routine=ToolCallRoutine(
                tool_name=name,
                args_json_str="{}",
                deps=ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()),
            ),
            is_idle_tool_call=False,
        )
        notification = await asyncio.wait_for(manager._notification_queue.get(), timeout=5)
        assert notification.log_keys_trusted == tool.log_keys_trusted, "the notification lost the verdict"
        return notification.log_keys_trusted

    assert await _run("space_tool", {"space_tool": remote}) is False
    assert await _run("get_profile", {"get_profile": local}) is True
    assert await _run("never_registered", {}) is False, "an unresolvable name is not permission"


@pytest.mark.asyncio
async def test_a_name_rebound_to_a_remote_tool_after_dispatch_is_not_trusted(
    monkeypatch: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """D36 itself: the verdict follows the tool that RAN, not the name's old meaning.

    Reproduced by the specialist security review of D34 and left open there. A name
    bound to a local Tool when the work was queued, rebound to a RemoteMcpTool before
    it executed, produced a verdict of True over a third-party Space's envelope --
    and the Space's keys, which can be a learner's name, rendered at INFO.

    `initialize_tools(force=True)` is reachable off-loop through `asyncio.to_thread`
    from the tool-space and profile routes, so the window is wall-clock rather than
    cooperative. Here it is simulated deterministically by swapping the registry the
    dispatcher reads, which is what that call does.
    """
    local = _real_local_tool({"display_name": "..."})
    remote = _real_remote_tool({LEARNER_NAME: "es-3"})

    # Bound LOCALLY when the work is QUEUED. start_tool returns as soon as the task
    # is created, so nothing has resolved the callable yet at this point.
    monkeypatch.setattr(core_tools, "get_tools", lambda: {"drifting": local})
    manager = BackgroundToolManager()
    routine = ToolCallRoutine(
        tool_name="drifting",
        args_json_str="{}",
        deps=ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()),
    )
    await manager.start_tool(call_id="drift", tool_call_routine=routine, is_idle_tool_call=False)

    # ...and REMOTE before the task gets to run. This is the window: the rebind lands
    # AFTER dispatch and BEFORE the dispatcher resolves the callable, which is what
    # initialize_tools(force=True) does off-loop through asyncio.to_thread.
    monkeypatch.setattr(core_tools, "get_tools", lambda: {"drifting": remote})

    notification = await asyncio.wait_for(manager._notification_queue.get(), timeout=5)

    assert notification.log_keys_trusted is False, (
        "the verdict followed the name's meaning at dispatch, not the tool that ran"
    )

    # ...and then the whole way to the log line, because a verdict nobody renders
    # protects nobody. Criterion 3 asks for the RENDERED line, so the notification
    # goes through the real handler, which builds the real console record, which
    # goes through the real log_handler_message.
    handler = _handler(monkeypatch, local_tools=())
    handler._in_flight_tool_calls = {notification.id}
    with caplog.at_level(logging.DEBUG):
        await handler._handle_tool_result(notification)
        queued = handler.output_queue.get_nowait()
        _log_one(queued.args[0], caplog)

    logged = " ".join(record.getMessage() for record in caplog.records)
    assert LEARNER_NAME not in logged, f"a learner's name from a Space reached the log: {logged}"
    assert "1 keys" in logged, f"the shape should still be described: {logged}"


@pytest.mark.asyncio
async def test_a_name_rebound_to_a_local_tool_after_dispatch_is_trusted(monkeypatch: Any) -> None:
    """The rebind in the SAFE direction, so the fix is not "always answer no".

    The coverage target asks for the window exercised BOTH ways, and only one of
    them was: an implementation that ignored the resolved tool and returned False
    would have passed every rebind test. Here the name is remote when the work is
    queued and local by the time it runs, and the verdict must follow the tool that
    ran -- to yes, which is the answer an operator's dispatch signal depends on.
    """
    remote = _real_remote_tool({"envelope": "from a Space"})
    local = _real_local_tool({"display_name": "..."})

    monkeypatch.setattr(core_tools, "get_tools", lambda: {"drifting": remote})
    manager = BackgroundToolManager()
    routine = ToolCallRoutine(
        tool_name="drifting",
        args_json_str="{}",
        deps=ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()),
    )
    await manager.start_tool(call_id="drift-safe", tool_call_routine=routine, is_idle_tool_call=False)
    monkeypatch.setattr(core_tools, "get_tools", lambda: {"drifting": local})

    notification = await asyncio.wait_for(manager._notification_queue.get(), timeout=5)

    assert notification.log_keys_trusted is True, (
        "the verdict stayed with the name's meaning at dispatch instead of the tool that ran"
    )


def _call_name(node: ast.Call) -> str | None:
    """The bare name of a called function, however it was reached."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


CARRIED_FIELD = "log_keys_trusted"


def _names_bound_from(module: Path, rule: str) -> set[str]:
    """Local names holding a verdict that came from the rule, directly or carried.

    Two provenances are legitimate and a third is not:

    * `x = log_trust_for_resolved_tool(...)` -- computed here from the rule;
    * `x = something.log_keys_trusted` -- carried from a notification that took the
      answer at dispatch, which is where D34 ended up after the security review
      showed that computing it later asks a registry that may have been rebound;
    * `x = True` -- which is the hardcoded verdict this guard exists to refuse.
    """
    bound: set[str] = set()
    for node in ast.walk(ast.parse(module.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        from_rule = isinstance(value, ast.Call) and _call_name(value) == rule
        carried = isinstance(value, ast.Attribute) and value.attr == CARRIED_FIELD
        if not (from_rule or carried):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                bound.add(target.id)
    return bound


def test_exactly_one_place_in_the_tree_decides_whether_a_result_s_keys_are_logged() -> None:
    """Criterion 4, read off the AST: the two sides cannot drift apart again.

    They already did once, and that is this defect -- the producer asked
    the registry by name while the console's fallback asked whether the record's
    kind was "tool_result". Two spellings of one decision.

    Criterion 4 asks that both sides "derive trust_keys from one shared function".
    What is asserted here is STRONGER than two callers of one function: there is one
    CALL, at the producer, and the console reads the verdict it stamped. The security
    review of D34 is why -- deriving it twice, even through one function, means
    deriving it at two moments, and `initialize_tools(force=True)` can rebind a tool
    name in between, which it reproduced as a learner's name reaching INFO. One
    computation cannot disagree with itself.

    An ALLOW-LIST over the whole source tree, not a check of two named files: a THIRD
    module reaching the rule would reintroduce the second spelling unseen, and the
    narrow version of this guard was not looking.
    """
    RULE = "log_trust_for_resolved_tool"
    root = _source_root()
    modules = sorted(root.rglob("*.py"))
    assert len(modules) > 10, "the source sweep found almost nothing, so this guard proves nothing"

    deciders = {path for path in modules if _mentions_rule(path, RULE)}
    # `_mentions_rule` reads reaches, not definitions, so core_tools -- which defines
    # the rule and never calls it -- is correctly absent here. Its definition is
    # asserted separately below rather than by widening the sweep.
    assert deciders == {root / "tools" / "background_tool_manager.py"}, (
        "the verdict must be APPLIED in exactly one place, _run_tool, from the tool object that ran; "
        f"found: {sorted(str(path.relative_to(root)) for path in deciders)}"
    )
    assert f"def {RULE}" in (root / "tools" / "core_tools.py").read_text(encoding="utf-8"), (
        "the rule has moved out of core_tools, so this guard is looking in the wrong place"
    )

    # ...and the ARGUMENT is the tool the routine reported, not another lookup.
    # Without this the guard's own message -- "from the tool object that ran" -- is
    # not what it enforces: `log_trust_for_resolved_tool(get_tools().get(name))`
    # satisfied every other assertion here while reintroducing the two-reads shape
    # D36 removed, merely with a narrower window. Review found that by mutation.
    manager_src = (root / "tools" / "background_tool_manager.py").read_text(encoding="utf-8")
    manager_ast = ast.parse(manager_src)
    awaited_names = {
        target.id
        for node in ast.walk(manager_ast)
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Await)
        for target in ast.walk(node.targets[0])
        if isinstance(target, ast.Name)
    }
    verdict_args = [
        node.args[0]
        for node in ast.walk(manager_ast)
        if isinstance(node, ast.Call) and _call_name(node) == RULE and node.args
    ]
    assert verdict_args, f"nothing in background_tool_manager calls {RULE}"
    for arg in verdict_args:
        assert isinstance(arg, ast.Name), (
            f"{RULE} must be given the tool the routine reported, not an expression resolved here"
        )
        assert arg.id in awaited_names, (
            f"{RULE} is given '{arg.id}', which is not bound from awaiting the routine -- "
            "the verdict must be about the tool that ran, not another registry read"
        )

    # The predicate itself lives in exactly one function. Before D36 that was true by
    # accident -- one caller, one isinstance. Now that the rule takes an object and
    # could be re-expressed anywhere, it is asserted.
    remote_checks = {
        path
        for path in modules
        if "RemoteMcpTool)" in path.read_text(encoding="utf-8") and path.name != "core_tools.py"
    }
    assert not remote_checks, (
        "the RemoteMcpTool test belongs only in core_tools' rule; "
        f"found: {sorted(str(path.relative_to(root)) for path in remote_checks)}"
    )

    # The NAME-based rules are gone, not merely unused. `result_keys_are_ours` and
    # `log_trust_for_tool_result` both answered "may these keys be logged?" from a
    # tool name, which is the second read of the registry D36 exists to remove.
    # Leaving either in place, callable and documented, is how the name lookup comes
    # back: the next author reaches for the one that takes what they have.
    for retired in ("result_keys_are_ours", "log_trust_for_tool_result"):
        survivors = {
            path
            for path in modules
            if _mentions_rule(path, retired) or f"def {retired}" in path.read_text(encoding="utf-8")
        }
        assert not survivors, (
            f"{retired} takes a tool NAME and was retired by D36; "
            f"found: {sorted(str(path.relative_to(root)) for path in survivors)}"
        )

    # THE PRODUCERS, not a fixed list of modules. An earlier version of this guard
    # asserted the decider set was exactly {huggingface_realtime.py}, and the
    # security review showed that was worse than useless: it FAILED a second
    # producer that did the right thing and called the rule, while passing one that
    # hardcoded `"log_keys_trusted": True` -- so it pushed the next author toward the
    # literal nothing checks. Constrain the record instead.
    #
    # WHAT THIS FINDS, stated exactly, because a guard claiming more than it checks
    # is the D19 shape this task's own patterns_to_follow names. It finds a dict
    # LITERAL carrying `"kind": "tool_result"` as a literal pair. There is exactly
    # one in the tree today -- huggingface_realtime, inline in _handle_tool_result --
    # and the sweep walks every module, so one written inside a helper would be found
    # too. It does NOT find a record built by `dict(kind=...)`,
    # assembled by item assignment or `update()`, spread from a base mapping that
    # carries the kind, or tagged with a named constant (`"kind": TOOL_RESULT_KIND`
    # -- the D6 shape this repository has already paid for). Those would pass unseen.
    # Widening to catch them is possible and was not done, because a second producer
    # is hypothetical and the guard's value is in constraining the one that exists.
    literals = []
    for module in modules:
        for node in ast.walk(ast.parse(module.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Dict):
                continue
            entries = {
                key.value: value
                for key, value in zip(node.keys, node.values)
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
            if entries.get("kind") is None or getattr(entries["kind"], "value", None) != "tool_result":
                continue
            verdict = entries.get("log_keys_trusted")
            where = f"{module.relative_to(root)}:{node.lineno}"
            if verdict is None:
                literals.append(f"{where} carries no verdict at all")
            elif isinstance(verdict, ast.Call):
                if _call_name(verdict) != RULE:
                    literals.append(f"{where} computes its verdict with something other than {RULE}")
            elif isinstance(verdict, ast.Attribute) and verdict.attr == CARRIED_FIELD:
                pass  # carried straight from the notification that took it at dispatch
            elif isinstance(verdict, ast.Name):
                # A bare name is only acceptable if it was BOUND from the rule in this
                # module. Without this, `ok = True` then `"log_keys_trusted": ok`
                # walks straight through the guard that exists to stop exactly that.
                if verdict.id not in _names_bound_from(module, RULE):
                    literals.append(f"{where} uses a name not bound from {RULE}")
            else:
                literals.append(f"{where} computes its verdict some other way")
    assert not literals, "a tool_result record must take its verdict from log_trust_for_resolved_tool: " + "; ".join(
        literals
    )


# --- Sink 2, transcript: the D9 decision --------------------------------------------
#
# D3 closed the tool-result routes and left this one open on purpose. D9 chose
# DEMOTE over accept and over redact, and these tests are what makes that a
# decision rather than a preference: each one fails if the split is reversed,
# and between them they pin both halves of it.


# The recipe docs/SETUP.md gives an operator to answer "did it hear me at all".
# It reads a count of ZERO as a deaf microphone, so an INFO line that stopped
# matching would not be a quiet regression -- it would print a false diagnosis in
# the one place a stuck user is told to trust.
DOCUMENTED_SPEECH_EVENT_GREP = re.compile(r"role=user|transcript|speech_started", re.IGNORECASE)

SPOKEN_GREETING = f"Hello {LEARNER_NAME}, ready for Spanish?"


def test_a_spoken_name_does_not_reach_the_log_that_is_written_anyway(caplog: pytest.LogCaptureFixture) -> None:
    """The defect, stated as a test.

    get_profile exists so Reachy can greet the learner by name, so the sentence
    right after it returns carries that name -- as transcript, which D3 did not
    touch. INFO is written whether or not --debug is on, in every home the app runs
    in, so this is the line that actually leaks. However many that turns out to be, it
    is written in all of them.
    """
    logged = _log_one(_transcript("assistant", SPOKEN_GREETING), caplog)

    assert LEARNER_NAME not in logged
    assert "Spanish" not in logged


def test_info_still_says_who_spoke_and_how_much(caplog: pytest.LogCaptureFixture) -> None:
    """Demoted, not deleted: an operator can still see that a turn happened.

    This is the half that separates D9's choice from blanket redaction. Without
    it the log would say nothing at all about the conversation, and a robot that
    was talking would look identical to one that was not.
    """
    logged = _log_one(_transcript("user", "I would like to practise Spanish"), caplog)

    assert "role=user" in logged
    # The house rendering for a string, the same one describe_for_log gives a tool
    # result -- shape, not content.
    assert "str(len=32)" in logged


def test_the_documented_deaf_microphone_grep_still_counts_a_user_turn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """docs/SETUP.md tells a stuck user to trust this count. Keep it truthful.

    Pinned as the regex itself rather than as a description of it, because the
    failure being guarded is a rewording of the INFO line that keeps a length and
    silently drops the token the doc greps for.
    """
    logged = _log_one(_transcript("user", "I would like to practise Spanish"), caplog)

    assert DOCUMENTED_SPEECH_EVENT_GREP.search(logged) is not None


def test_the_words_are_there_for_an_operator_who_asks_for_them(caplog: pytest.LogCaptureFixture) -> None:
    """--debug is the opt-in. Someone debugging their own robot still sees the words.

    The console UI never depended on this line -- it is fed by
    ConsoleApp._dispatch_transcript over JSON-RPC -- but a log with no way back to
    the conversation would make a real support problem unsolvable.
    """
    logged = _log_one_at_debug(_transcript("assistant", SPOKEN_GREETING), caplog)

    assert SPOKEN_GREETING in logged


def test_every_transcript_producer_is_treated_the_same(caplog: pytest.LogCaptureFixture) -> None:
    """All four non-tool-result producers, including the two easy ones to forget.

    huggingface_realtime queues four untagged messages: a debounced partial, a
    final user turn, an assistant turn, and an error rendered as assistant speech.
    A policy that covered the two obvious roles would leave a partial transcript
    -- which carries the same words -- logging in cleartext.
    """
    turns = {
        "user_partial": "I would like to practise Spa",
        "user": f"My name is {LEARNER_NAME}",
        "assistant": SPOKEN_GREETING,
        "assistant_error": f"[error] session failed for {LEARNER_NAME}",
    }

    for role, text in turns.items():
        caplog.clear()
        logged = _log_one(_transcript(role.removesuffix("_error"), text), caplog)
        assert LEARNER_NAME not in logged, role
        assert "Spa" not in logged, role
        assert f"content=str(len={len(text)})" in logged, role


def test_a_long_turn_is_measured_rather_than_truncated_into_info(caplog: pytest.LogCaptureFixture) -> None:
    """The 500-character cut belongs to the DEBUG line now.

    Truncating at INFO would have been the tempting half-measure: it bounds the
    log's size and leaks the first 500 characters, which is where a greeting puts
    the name.
    """
    long_turn = f"{LEARNER_NAME} said: " + "hola " * 400

    logged = _log_one(_transcript("assistant", long_turn), caplog)

    assert LEARNER_NAME not in logged
    assert f"content=str(len={len(long_turn)})" in logged

    caplog.clear()
    debugged = _log_one_at_debug(_transcript("assistant", long_turn), caplog)
    assert "…" in debugged


def test_the_policy_is_not_name_detection(caplog: pytest.LogCaptureFixture) -> None:
    """A turn with no name in it is demoted too, and that is deliberate.

    Nothing at this layer can tell a sentence containing a household member's name
    from one that does not, and a rule that tried would fail open on the first
    spelling it did not expect. The KIND decides, exactly as it does for tool
    results one branch above.
    """
    logged = _log_one(_transcript("user", "yes please"), caplog)

    assert "yes please" not in logged
    assert "content=str(len=10)" in logged


def test_tool_results_are_still_redacted_whatever_happened_to_transcript(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The two branches share one function; D9 must not have loosened D3's.

    Worth stating separately because the transcript change edits the fall-through
    that a tool result reaches when its rendering is missing.
    """
    logged = _log_one_at_debug(
        {
            "role": "assistant",
            "content": json.dumps(PROFILE_RESULT),
            "kind": "tool_result",
            "log_safe": describe_for_log(PROFILE_RESULT, trust_keys=True),
        },
        caplog,
    )

    assert LEARNER_NAME not in logged
    assert "display_name" in logged


def test_a_greeting_turn_leaks_nothing_end_to_end(caplog: pytest.LogCaptureFixture) -> None:
    """The real sequence: get_profile returns, then Reachy says the name out loud.

    Each half was already covered -- the tool result by D3, the transcript above --
    and neither proves the pair. This is the turn the defect was actually about.
    """
    with caplog.at_level(logging.INFO):
        for message in (
            {
                "role": "assistant",
                "content": json.dumps(PROFILE_RESULT),
                "kind": "tool_result",
                "log_safe": describe_for_log(PROFILE_RESULT, trust_keys=True),
            },
            _transcript("assistant", SPOKEN_GREETING),
        ):
            for msg in AdditionalOutputs(message).args:
                log_handler_message(msg)

    logged = " ".join(record.getMessage() for record in caplog.records)

    assert LEARNER_NAME not in logged
    # Still diagnosable: the tool came back with our schema, and a turn was spoken.
    assert "display_name" in logged
    assert "role=assistant" in logged


# --- The producers, read from the source rather than from memory ----------------------


_ABOVE_DEBUG = frozenset({"info", "warning", "error", "critical", "exception"})


def _realtime_tree() -> ast.Module:
    """Parse the realtime handler. The queue producers are inline in its event loop.

    They are reached only by driving a live websocket session, so a behavioural test
    would either mock the thing under test or cover nothing. Reading the source is
    the honest way to assert how many there are.
    """
    return ast.parse(Path(hf_mod.__file__).read_text(encoding="utf-8"))


def _queued_message_shapes() -> list[tuple[int, Any]]:
    """Every ``output_queue.put(...)`` in the handler, paired with what it queues."""
    shapes: list[tuple[int, Any]] = []
    for node in ast.walk(_realtime_tree()):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "put"):
            continue
        if not (isinstance(func.value, ast.Attribute) and func.value.attr == "output_queue"):
            continue
        shapes.append((node.lineno, node.args[0] if node.args else None))
    return shapes


def _is_rendered(arg: ast.AST) -> bool:
    """True when this argument is a call to the log-safe rendering seam."""
    return isinstance(arg, ast.Call) and getattr(arg.func, "id", None) in _RENDERERS


def _identifiers_in(node: ast.AST) -> list[str]:
    """Every name and attribute reachable from a node -- what a log call interpolates."""
    found: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            found.append(child.id)
        elif isinstance(child, ast.Attribute):
            found.append(child.attr)
    return found


def test_exactly_four_producers_queue_untagged_transcript() -> None:
    """The count in D9's acceptance criteria, checked against the code.

    A fifth producer is not a bug by itself -- everything on this queue reaches
    log_handler_message, so a new transcript message is demoted like the rest. What
    this catches is the KEY SET changing underneath the policy: a transcript message
    that starts carrying a ``kind`` would be redacted as a payload instead.

    It reads keys only. It says nothing about the VALUE behind "content" -- a
    producer that queued a non-string would pass here, which is why the branch logs
    its INFO line before testing the type at all, and why
    test_a_turn_with_no_text_still_leaves_a_trace covers that half behaviourally.
    """
    untagged: list[tuple[int, list[str]]] = []
    tagged = 0

    for lineno, queued in _queued_message_shapes():
        if not (isinstance(queued, ast.Call) and getattr(queued.func, "id", None) == "AdditionalOutputs"):
            continue  # the audio frames, which are tuples and carry no text
        inner = queued.args[0] if queued.args else None
        if isinstance(inner, ast.Call):
            tagged += 1  # tool_call_message builds its own kind and log_safe
            continue
        assert isinstance(inner, ast.Dict), f"line {lineno}: cannot read this message"
        keys = [key.value for key in inner.keys if isinstance(key, ast.Constant)]
        assert len(keys) == len(inner.keys), f"line {lineno}: a computed key"
        if "kind" in keys:
            tagged += 1
        else:
            untagged.append((lineno, sorted(keys)))

    assert tagged >= 2, "the tagged producers vanished, so this scan is not reading the file it thinks"
    assert len(untagged) == 4, untagged
    for lineno, keys in untagged:
        assert keys == ["content", "role"], (lineno, keys)


def _literal_text(call: ast.Call) -> str:
    """Every constant string the call carries -- its format string and any f-string parts."""
    parts = []
    for arg in call.args:
        for child in ast.walk(arg):
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                parts.append(child.value)
    return " ".join(parts)


# Names that measure a turn rather than quote it. A latency line mentions the word
# transcript and interpolates a number, and it is not a sink -- but it is also not a
# licence to wave through anything, so the exemption is a fixed list of names.
_MEASUREMENTS = frozenset({"delta_ms"})

# What a variable carrying speech is called in this file. Started as "transcript"
# alone, which read every line the word appeared on and missed
# logger.debug("response text done: %s", event.text) -- assistant speech under a name
# that never says so. Widening it is cheap here because the only INFO lines these
# words reach are the two latency ones, which the exemption above already names.
_SPEECH_NAMES = ("transcript", "text", "delta", "content", "speech", "message", "msg")

# The seam itself. An argument already passed through one of these is a shape, not
# words, so a line that renders its speech is what the policy asks for rather than a
# violation of it -- without this, the fix and the defect look identical to the scan.
_RENDERERS = frozenset({"describe_for_log", "describe_json_for_log"})


def test_the_handlers_own_transcript_lines_carry_shape_at_every_level() -> None:
    """The second sink, and it no longer carries the words at all.

    huggingface_realtime logged the same words the console logs, one layer down --
    at DEBUG, which this test used to accept. But the console's DEBUG copy is the
    one docs/privacy-and-consent.md describes, "truncated at 500 characters", and
    these were a second copy with no truncation: measured, a 1,598-character user
    transcript and the assistant's reply each appeared whole, beside the console's
    truncated line. So the handler now renders speech as its shape at every level,
    and the console's single DEBUG line is the only place the words are written.

    A line that renders its argument through describe_for_log is reading shape rather
    than words, so it is what this policy asks for. What this does NOT catch: a log
    line that neither mentions transcript in its text nor interpolates a raw variable
    whose name is in _SPEECH_NAMES. ``logger.info("%s", event.payload)`` is invisible
    here. The scan reads names, and a name is the only handle this layer has.
    """
    rendered_speech = 0
    measurements = 0
    raw_speech: list[tuple[int, str, list[str]]] = []

    for node in ast.walk(_realtime_tree()):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr in _ABOVE_DEBUG | {"debug"}):
            continue
        if not (isinstance(func.value, ast.Name) and func.value.id == "logger"):
            continue
        interpolated = [arg for arg in node.args if not isinstance(arg, ast.Constant)]
        raw = [arg for arg in interpolated if not _is_rendered(arg)]
        rendered = [arg for arg in interpolated if _is_rendered(arg)]
        raw_names = [name for arg in raw for name in _identifiers_in(arg)]

        def _speech(names: list[str]) -> bool:
            return any(word in name.lower() for name in names for word in _SPEECH_NAMES)

        if _speech([name for arg in rendered for name in _identifiers_in(arg)]) and not _speech(raw_names):
            rendered_speech += 1
        if not ("transcript" in _literal_text(node).lower() or _speech(raw_names)) or not raw:
            continue
        if all(name in _MEASUREMENTS for name in raw_names):
            measurements += 1  # "first audio delta %.0f ms after user transcript"
            continue
        raw_speech.append((node.lineno, func.attr, sorted(set(raw_names))))

    assert raw_speech == [], raw_speech
    # Non-vacuity, checked AFTER the claim so a scan that reads nothing cannot be
    # mistaken for a clean one.
    assert rendered_speech >= 6, f"only {rendered_speech} rendered speech lines found, so this scan proves nothing"
    assert measurements >= 1, "the latency lines vanished, so the exemption is untested"


def test_a_transcript_reaches_the_debug_log_once_and_truncated(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Executed, not scanned: the documented DEBUG exception is exactly one copy.

    docs/privacy-and-consent.md says --debug logs the words "truncated at 500
    characters". Measured before the fix, the realtime handler added its own
    untruncated copies of the user transcript, the assistant transcript, the partial
    transcript and response text. A sentinel past character 1,500 of each proves no
    untruncated copy is written by the handler, and the console's truncated copy is
    what remains.
    """
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_huggingface_realtime import HF_DEFAULT_VOICE, _FakeEvent, _make_fake_realtime_client

    sentinel = "ZEBEDIAH"
    long_user = "x" * 1500 + f" my name is Alice {sentinel}"
    long_assistant = "y" * 1500 + f" hello Alice {sentinel}"

    async def run() -> None:
        handler = HuggingFaceRealtimeHandler(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()))
        handler.partial_debounce_delay = 0
        handler.client = _make_fake_realtime_client(
            events=(
                _FakeEvent("conversation.item.input_audio_transcription.delta", item_id="i1", delta=f"it is {sentinel}"),
                _FakeEvent("conversation.item.input_audio_transcription.completed", item_id="i1", transcript=long_user),
                _FakeEvent("response.output_audio_transcript.done", transcript=long_assistant),
                _FakeEvent("response.output_text.done", text=f"text {sentinel}"),
            )
        )
        monkeypatch.setattr(type(handler.tool_manager), "start_up", MagicMock())
        monkeypatch.setattr(type(handler.tool_manager), "shutdown", AsyncMock())
        await handler._run_realtime_session()
        await asyncio.sleep(0.05)

    monkeypatch.setattr(hf_mod, "get_session_instructions", lambda _instance_path=None: "test")
    monkeypatch.setattr(hf_mod, "get_session_voice", lambda default=HF_DEFAULT_VOICE: "Aiden")
    monkeypatch.setattr(hf_mod, "get_tool_specs", lambda: [])
    with caplog.at_level(logging.DEBUG):
        asyncio.run(run())

    from_the_handler = [r for r in caplog.records if r.name == hf_mod.__name__]
    assert from_the_handler, "the handler logged nothing, so this proves nothing"
    leaked = [f"{r.lineno}: {r.getMessage()[:80]}" for r in from_the_handler if sentinel in r.getMessage()]
    assert leaked == [], leaked


def test_a_turn_with_no_text_still_leaves_a_trace(caplog: pytest.LogCaptureFixture) -> None:
    """The SDK types an assistant transcript as optional, and None is not a string.

    The shape line has to sit outside the isinstance guard or the one turn whose
    absence an operator most needs to see -- a turn that produced no text -- is the
    one turn that logs nothing at any level. That is worse than the leak this task
    closed: a silent gap reads as a robot that never spoke.
    """
    logged = _log_one({"role": "assistant", "content": None}, caplog)

    assert "role=assistant" in logged
    assert "content=None" in logged


# --- Sink 3: the tools that log their own arguments, one layer below the console -----
#
# D3 and D9 both operate on log_handler_message. These four log inside the tool, so
# neither seam ever sees them, and they were still writing a learner's data in
# cleartext at INFO after both. The specialist security review of D9 found them.


@pytest.fixture()
def memory_deps(tmp_path: Any) -> ToolDependencies:
    """Tool dependencies whose memory store is a throwaway directory."""
    return ToolDependencies(
        reachy_mini=MagicMock(),
        movement_manager=MagicMock(),
        instance_path=tmp_path,
    )


@pytest.mark.asyncio
async def test_remember_does_not_write_the_fact_it_was_asked_to_store(
    memory_deps: ToolDependencies, caplog: pytest.LogCaptureFixture
) -> None:
    """The worst of the four: this tool's description tells the model to store a name.

    "Is called Alice Ferreira" is the canonical call, not an unlucky one, so this
    line put a household member's name in the default-on log every time the tutor
    was introduced to someone.
    """
    from reachy_language_tutor.tools.remember import Remember

    fact = f"Is called {LEARNER_NAME}"
    with caplog.at_level(logging.INFO):
        await Remember()(memory_deps, fact=fact)
    logged = " ".join(record.getMessage() for record in caplog.records)

    assert LEARNER_NAME not in logged
    assert "Tool call: remember" in logged
    assert f"str(len={len(fact)})" in logged


@pytest.mark.asyncio
async def test_remember_still_shows_the_fact_under_debug(
    memory_deps: ToolDependencies, caplog: pytest.LogCaptureFixture
) -> None:
    """Demoted, not deleted -- the same split the console uses for transcript."""
    from reachy_language_tutor.tools.remember import Remember

    with caplog.at_level(logging.DEBUG):
        await Remember()(memory_deps, fact=f"Is called {LEARNER_NAME}")

    assert LEARNER_NAME in " ".join(record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_forget_redacts_both_paths_not_just_the_one_that_matched(
    memory_deps: ToolDependencies, caplog: pytest.LogCaptureFixture
) -> None:
    """The no-match path is the common one, and it carries the query verbatim.

    A fix that covered only the line with `removed=` in it would leave the more
    frequent branch leaking, which is the shape this kind of fix usually fails in.
    """
    from reachy_language_tutor.tools.forget import Forget
    from reachy_language_tutor.tools.remember import Remember

    with caplog.at_level(logging.INFO):
        await Forget()(memory_deps, query=f"anything about {LEARNER_NAME}")
    missed = " ".join(record.getMessage() for record in caplog.records)

    assert LEARNER_NAME not in missed
    assert "no_match" in missed

    await Remember()(memory_deps, fact=f"Is called {LEARNER_NAME}")
    caplog.clear()
    with caplog.at_level(logging.INFO):
        await Forget()(memory_deps, query=LEARNER_NAME)
    matched = " ".join(record.getMessage() for record in caplog.records)

    assert LEARNER_NAME not in matched
    # Which branch this took, not just that forget logged something. Both branches
    # emit "Tool call: forget", so asserting only that would let the second half
    # decay into a copy of the first if matching ever changed -- and the match
    # path's redaction would stop being revert-proof with nothing going red.
    assert "removed=" in matched
    assert "no_match" not in matched


@pytest.mark.asyncio
async def test_the_camera_question_is_described_rather_than_quoted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The model composes this from a conversation that already holds the profile.

    "What is Alice holding" is inside the parameter schema's own examples, so the
    leak is a phrasing away rather than a misuse.
    """
    from reachy_language_tutor.tools.camera import Camera

    deps = ToolDependencies(
        reachy_mini=MagicMock(),
        movement_manager=MagicMock(),
        camera_enabled=False,
    )
    question = f"What is {LEARNER_NAME} holding?"

    with caplog.at_level(logging.INFO):
        await Camera()(deps, question=question)
    logged = " ".join(record.getMessage() for record in caplog.records)

    assert LEARNER_NAME not in logged
    assert "Tool call: camera" in logged


@pytest.mark.asyncio
async def test_the_idle_reason_is_described_rather_than_quoted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An open free-text field with no truncation -- the widest of the four."""
    from reachy_language_tutor.tools.idle_do_nothing import IdleDoNothing

    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())

    with caplog.at_level(logging.INFO):
        await IdleDoNothing()(deps, reason=f"{LEARNER_NAME} asked me to wait")
    logged = " ".join(record.getMessage() for record in caplog.records)

    assert LEARNER_NAME not in logged
    assert "Tool call: idle_do_nothing" in logged


# --- Sink 4: an exception rendered raw, which carries the path it failed on ----------
#
# The subtlest of the four, because the log line looks clean. A warning that carefully
# does not interpolate a path still prints one when the exception IS an OSError:
# OSError.__str__ embeds its filename. Measured on a real blocked path, the learner
# store printed "/.../alice-smith-household/instance" from a line whose format string
# mentions no path at all, and memory.py printed the household directory twice.
#
# The rule is logging_safety.log_safe, and it holds in EVERY module of the package.
#
# It used to hold in a list of modules -- four, then ten -- and "a module joins it when
# it has been swept". That list was the deny-list shape one level up: the guard covered
# what somebody had remembered to add, and ninety call sites in twenty-odd modules sat
# outside it, including a traceback of session.update (every remembered fact) and a
# raw exception quoting the instance directory. So the guard now names what a logging
# call may DO with a caught exception, and applies it to every file under src.

# The methods that make a call a logging call, on any receiver -- `logger`, the `log`
# parameter audio/startup_config.py takes, logging's own module functions. A receiver
# list would be one more thing to forget; a stray argparse `.error` is checked too, and
# is harmless because it carries no exception.
_LOG_METHODS = frozenset({"debug", "info", "warning", "warn", "error", "critical", "log", "exception"})

# The only calls a caught exception may be passed to inside a logging call. Each
# renders a shape: log_safe a class (or a message this app worded from types), where
# the file:line frames, describe_for_log a type and a size.
_SAFE_RENDERERS = frozenset({"log_safe", "_log_safe", "describe_for_log", "describe_json_for_log", "where"})


def _source_root() -> Path:
    return Path(__file__).resolve().parents[1] / "src" / "reachy_language_tutor"


def _every_module() -> list[Path]:
    return sorted(path for path in _source_root().rglob("*.py") if "__pycache__" not in path.parts)


def _called_name(call: ast.Call) -> str | None:
    return getattr(call.func, "id", None) or getattr(call.func, "attr", None)


def _logging_shape_offences(tree: ast.Module) -> tuple[list[tuple[int, str]], int]:
    """Every logging call that renders a caught exception other than as a shape.

    AN ALLOW-LIST OF SHAPES. Inside the `except ... as NAME` block that bound it, NAME
    may appear in a logging call's arguments only as

      - the argument of a safe renderer:   log_safe(NAME), where(NAME), ...
      - its class name:                    type(NAME).__name__

    Anything else -- the bare name, str(NAME), NAME.args, an f-string, `%r` of it --
    is refused without having to be foreseen. A logging call must also never render a
    traceback: `.exception(...)` and any `exc_info=` other than a literal False print
    the exception's message beside its frames, which is the same raw rendering by
    another route. `where` gives the frames without the message.

    Returns the offences and how many exception references were checked, so a scan
    that read nothing cannot be mistaken for a clean one.

    NOT covered, said here rather than implied: an exception copied into another
    variable and logged under that name, or rendered by a helper before it reaches the
    call (traceback.format_exc(), a message built with f"{exc}" on the line above).
    The scan follows the name the except clause binds, within that clause.
    """
    offences: list[tuple[int, str]] = []
    checked = 0
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}

    def is_logging_call(node: ast.AST) -> bool:
        return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _LOG_METHODS

    for node in ast.walk(tree):
        if is_logging_call(node):
            assert isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            if node.func.attr == "exception":
                offences.append((node.lineno, "a traceback via .exception()"))
            for keyword in node.keywords:
                if keyword.arg == "exc_info" and not (
                    isinstance(keyword.value, ast.Constant) and keyword.value.value is False
                ):
                    offences.append((node.lineno, "a traceback via exc_info="))

    for handler in (n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler) and n.name):
        for statement in handler.body:
            for call in (n for n in ast.walk(statement) if is_logging_call(n)):
                assert isinstance(call, ast.Call)
                for argument in [*call.args, *(keyword.value for keyword in call.keywords)]:
                    for name in ast.walk(argument):
                        if not (isinstance(name, ast.Name) and name.id == handler.name):
                            continue
                        checked += 1
                        parent = parents.get(name)
                        rendered = isinstance(parent, ast.Call) and _called_name(parent) in _SAFE_RENDERERS
                        rendered = rendered and name in parent.args  # type: ignore[union-attr]
                        typed = (
                            isinstance(parent, ast.Call)
                            and _called_name(parent) == "type"
                            and isinstance(parents.get(parent), ast.Attribute)
                            and parents[parent].attr == "__name__"  # type: ignore[attr-defined]
                        )
                        if not (rendered or typed):
                            offences.append((call.lineno, f"`{handler.name}` rendered as {ast.unparse(argument)[:60]}"))
    return offences, checked


def test_no_module_logs_a_caught_exception_in_any_shape_but_a_rendered_one() -> None:
    """Every file under src, against the allow-list of shapes above.

    Wrapping was the fix, module by module: 26 lines across console.py and main.py
    first, then the learner modules, then ten more, then the remaining twenty-odd
    modules at once. Unwrapping any one of them, or adding a new raw one anywhere, fails
    this -- including in a module written after this test.
    """
    offences: list[str] = []
    checked = 0
    modules = _every_module()
    for path in modules:
        found, counted = _logging_shape_offences(ast.parse(path.read_text(encoding="utf-8")))
        checked += counted
        relative = path.relative_to(_source_root())
        offences.extend(f"{relative}:{line}: {what}" for line, what in found)

    assert offences == [], offences
    # Non-vacuity: the package logs caught exceptions in well over a hundred places.
    assert len(modules) > 50, f"only {len(modules)} modules found, so the scan read the wrong tree"
    assert checked > 100, f"only {checked} exception references checked, so this scan proves nothing"


@pytest.mark.parametrize(
    "source",
    [
        # Each is a shape this package actually shipped, or the one-token neighbour of one.
        "try:\n    f()\nexcept OSError as exc:\n    logger.warning('failed: %s', exc)\n",
        "try:\n    f()\nexcept Exception as e:\n    logger.error(f'failed: {e}')\n",
        "try:\n    f()\nexcept Exception as e:\n    logger.error(f'{type(e).__name__}: {e}')\n",
        "try:\n    f()\nexcept Exception as e:\n    logger.error('failed: %s', str(e))\n",
        "try:\n    f()\nexcept Exception as e:\n    log.warning('failed: %s', e)\n",
        "try:\n    f()\nexcept Exception:\n    logger.exception('failed')\n",
        "try:\n    f()\nexcept Exception:\n    logger.warning('failed', exc_info=True)\n",
        "try:\n    f()\nexcept Exception as e:\n    logger.warning('failed', exc_info=logger.isEnabledFor(10))\n",
        "try:\n    f()\nexcept Exception as e:\n    logger.warning('failed: %s', log_safe(e.args))\n",
    ],
)
def test_the_shape_guard_refuses_each_raw_rendering(source: str) -> None:
    """The guard, pointed at the shapes it exists for, so it cannot pass by reading nothing."""
    offences, _checked = _logging_shape_offences(ast.parse(source))

    assert offences, source


@pytest.mark.parametrize(
    "source",
    [
        "try:\n    f()\nexcept OSError as exc:\n    logger.warning('failed: %s', log_safe(exc))\n",
        "try:\n    f()\nexcept OSError as exc:\n    logger.warning('failed: %s at %s', log_safe(exc), where(exc))\n",
        "try:\n    f()\nexcept OSError as exc:\n    logger.warning('failed: %s', type(exc).__name__)\n",
        "try:\n    f()\nexcept OSError as exc:\n    logger.warning('failed', exc_info=False)\n",
    ],
)
def test_the_shape_guard_permits_the_rendered_shapes(source: str) -> None:
    """The other direction, or a guard that refuses everything would pass the test above."""
    offences, checked = _logging_shape_offences(ast.parse(source))

    assert offences == [], offences
    assert checked == (0 if "exc_info" in source else 1 + source.count("where(exc)"))


def test_a_runtime_error_is_rendered_by_its_class_alone() -> None:
    """RuntimeError left the allow-list, because this package puts paths in them.

    profile_toolsets.py raises `RuntimeError(f"Failed to read profile toolsets from
    {settings_path}: {exc}")`. log_safe rendered that in full, on the justification
    that a RuntimeError here "names no path".
    """
    from reachy_language_tutor.logging_safety import SafeToLog, log_safe

    household = "/Users/x/alice-zebediah-household/instance/profile_toolsets.json"
    assert str(log_safe(RuntimeError(f"Failed to read profile toolsets from {household}"))) == "RuntimeError"
    # A message this app worded from types alone still gets through, by saying so.
    worded = SafeToLog("instance_path must be a path, not int")
    assert log_safe(worded) is worded


def test_a_broken_settings_file_does_not_put_the_instance_directory_in_the_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end, through the lines that printed it.

    Measured before the fix: with the instance directory under a household's name, a
    corrupt installed_tool_spaces.json made main's "Failed to initialize tools" line and
    app_lifecycle's fallback line print that directory at ERROR.
    """
    from reachy_language_tutor import app_lifecycle
    from reachy_language_tutor.logging_safety import log_safe

    instance = tmp_path / "alice-zebediah-household" / "instance"
    instance.mkdir(parents=True)
    (instance / "installed_tool_spaces.json").write_text("{bad", encoding="utf-8")
    logger = logging.getLogger("reachy_language_tutor.main")

    # initialize_tools remembers the instance it was last given; put the registry back
    # as it was so no later test inherits a broken instance.
    for name in ("_TOOLS_INSTANCE_PATH", "_TOOLS_SIGNATURE", "ALL_TOOLS"):
        monkeypatch.setattr(core_tools, name, getattr(core_tools, name))

    with caplog.at_level(logging.DEBUG):
        try:
            app_lifecycle.initialize_tools_with_default_fallback(instance, logger)
        except Exception as exc:
            logger.error("Failed to initialize tools: %s", log_safe(exc))

    assert caplog.records, "nothing was logged, so this proves nothing"
    assert "zebediah" not in caplog.text, [r.getMessage() for r in caplog.records if "zebediah" in r.getMessage()]
