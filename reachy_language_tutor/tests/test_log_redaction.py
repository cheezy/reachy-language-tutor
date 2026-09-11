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
"""

import json
import asyncio
import logging
from typing import Any
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

import reachy_language_tutor.huggingface_realtime as hf_mod
from reachy_language_tutor.tools import core_tools
from reachy_language_tutor.utils import describe_for_log, tool_call_message, describe_json_for_log
from reachy_language_tutor.console import log_handler_message
from reachy_language_tutor.streaming import AdditionalOutputs
from reachy_language_tutor.tools.core_tools import ToolDependencies
from reachy_language_tutor.huggingface_realtime import HuggingFaceRealtimeHandler
from reachy_language_tutor.tools.background_tool_manager import ToolState, ToolNotification


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
    remote = MagicMock(spec=core_tools.RemoteMcpTool)
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
    registry = {name: MagicMock(spec=core_tools.Tool) for name in local_tools}
    monkeypatch.setattr(core_tools, "get_tools", lambda: registry)

    handler = HuggingFaceRealtimeHandler(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()))
    handler.connection = AsyncMock()
    handler.output_queue = asyncio.Queue()
    monkeypatch.setattr(handler, "_wait_for_response_done_before_tool_result", AsyncMock(return_value=True))
    monkeypatch.setattr(handler, "_safe_response_create", AsyncMock())
    return handler


def _profile_call() -> ToolNotification:
    """Build a completed get_profile call carrying a real-looking learner name."""
    return ToolNotification(
        id="call_a",
        tool_name="get_profile",
        is_idle_tool_call=False,
        status=ToolState.COMPLETED,
        result=dict(PROFILE_RESULT),
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

    Shaped after the tool that does not exist yet: W9's record_result is handed a
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
    from reachy_language_tutor.tools import core_tools

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
    from reachy_language_tutor.tools import core_tools

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


def test_the_console_still_logs_ordinary_transcript(caplog: pytest.LogCaptureFixture) -> None:
    """Only tool results are redacted; the conversation transcript is untouched.

    Redacting it too would destroy the console's purpose. That transcript carries a
    spoken name by another route is real, pre-existing, and tracked separately -- it
    is not something this seam should silently paper over.
    """
    logged = _log_one({"role": "user", "content": "I would like to practise Spanish"}, caplog)

    assert "I would like to practise Spanish" in logged


def test_a_tool_result_with_no_rendering_is_still_redacted(caplog: pytest.LogCaptureFixture) -> None:
    """The seam fails closed: the KIND decides, not the presence of a rendering.

    A producer that forgets log_safe, or builds a malformed one, must not fall
    through to cleartext -- the default outcome of this control has to be redaction.
    """
    raw = json.dumps(PROFILE_RESULT)

    for kind in ("tool_result", "tool_call"):
        for broken in ({"log_safe": None}, {"log_safe": 42}, {}):
            caplog.clear()
            logged = _log_one({"role": "assistant", "content": raw, "kind": kind, **broken}, caplog)
            assert LEARNER_NAME not in logged, (kind, broken)
            assert "str(len=" in logged, (kind, broken)
            # Degraded, but not blind -- and the key names come back only for a
            # RESULT, whose keys are our own schema. A call's keys stay suppressed.
            if kind == "tool_result":
                assert "display_name" in logged, (kind, broken)
            else:
                assert "display_name" not in logged, (kind, broken)
