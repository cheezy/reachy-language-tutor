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

import reachy_language_tutor.huggingface_realtime as hf_mod

# Bound at module scope, which D28 is what made possible. Until test_external_loading.py,
# test_tool_space_runtime.py and test_profile_load_resilience.py stopped re-importing the
# tools package, a binding made here went stale the moment one of them ran: the re-import
# built a second Tool base class and _load_enabled_tools, which filters with issubclass,
# then matched nothing. See tests/tools_module_graph.py.
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


def test_the_handlers_own_transcript_lines_stay_at_debug() -> None:
    """The second sink, kept consistent with the console's.

    huggingface_realtime logs the same words one layer down. Those calls are already
    DEBUG, which is what made this the cheap half of the decision -- but nothing said
    so, and promoting one while chasing a bug would undo the console's half in a file
    where the word INFO never appears next to the word transcript.

    A line that renders its argument through describe_for_log is reading shape rather
    than words, so it is what this policy asks for and the scan lets it past at any
    level -- otherwise the error branch's fix would look exactly like its defect.

    What this does NOT catch: a log line that neither mentions transcript in its text
    nor interpolates a raw variable whose name is in _SPEECH_NAMES.
    ``logger.info("%s", event.payload)`` is invisible here. The scan reads names, and
    a name is the only handle this layer has -- so the list is the guard, and a line
    that starts carrying speech under a new name needs adding to it.
    """
    at_debug = 0
    measurements = 0
    above_debug: list[tuple[int, str, list[str]]] = []

    for node in ast.walk(_realtime_tree()):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr in _ABOVE_DEBUG | {"debug"}):
            continue
        if not (isinstance(func.value, ast.Name) and func.value.id == "logger"):
            continue
        # A bare constant is the format string; only an interpolated value carries words.
        interpolated = [arg for arg in node.args if not isinstance(arg, ast.Constant)]
        raw = [arg for arg in interpolated if not _is_rendered(arg)]
        names = [name for arg in raw for name in _identifiers_in(arg)]
        about_speech = "transcript" in _literal_text(node).lower() or any(
            word in name.lower() for name in names for word in _SPEECH_NAMES
        )
        if not (about_speech and raw):
            continue
        if names and all(name in _MEASUREMENTS for name in names):
            measurements += 1  # "first audio delta %.0f ms after user transcript"
            continue
        if func.attr == "debug":
            at_debug += 1
        else:
            above_debug.append((node.lineno, func.attr, sorted(set(names))))

    assert above_debug == [], above_debug
    # Non-vacuity, checked AFTER the claim so a scan that reads nothing cannot be
    # mistaken for a promoted line, and a promoted line cannot be reported as an
    # empty scan. Both happened while writing this.
    assert at_debug >= 6, f"only {at_debug} speech lines found, so this scan proves nothing"
    assert measurements >= 1, "the latency lines vanished, so the exemption is untested"


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
# The rule is logging_safety.log_safe. These are the modules that hold it.

_REDACTED_MODULES = (
    "learners/store.py",
    "memory.py",
    "console.py",
    "main.py",
)

_SAFE_RENDERERS = frozenset({"log_safe", "_log_safe", "describe_for_log", "describe_json_for_log"})


def _source_root() -> Path:
    return Path(__file__).resolve().parents[1] / "src" / "reachy_language_tutor"


def _exception_bound_names(tree: ast.Module) -> set[str]:
    """Names an `except ... as` binds, which therefore hold an exception."""
    return {node.name for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler) and node.name}


@pytest.mark.parametrize("module", _REDACTED_MODULES)
def test_no_caught_exception_is_logged_without_being_rendered_safe(module: str) -> None:
    """An exception reaching a log line goes through log_safe, in every module that holds it.

    This is an allow-list over one shape rather than a search for paths: a caught
    exception is rendered through a redactor, or it does not reach the log. Naming the
    forbidden thing instead -- "no argument called path" -- is what missed it the first
    time, because the offending argument was called `exc`.

    Wrapping is what the fix was: 26 log lines across console.py and main.py, plus the
    learner modules. Unwrapping any of them fails this.

    NOT covered, and deliberately: the other modules in the package. They have the same
    shape at roughly a hundred more call sites, which is a change too large to make
    without its own review -- see the completion notes on D31. This list is the set that
    has actually been swept, and a module joins it when it has been.
    """
    tree = ast.parse((_source_root() / module).read_text(encoding="utf-8"))
    caught = _exception_bound_names(tree)
    assert caught, f"{module} catches nothing, so this scan proves nothing"

    raw: list[str] = []
    checked = 0
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if not (isinstance(node.func.value, ast.Name) and node.func.value.id == "logger"):
            continue
        for argument in list(node.args) + [keyword.value for keyword in node.keywords]:
            for name in (n for n in ast.walk(argument) if isinstance(n, ast.Name)):
                if name.id not in caught:
                    continue
                checked += 1
                enclosing = [
                    call
                    for call in ast.walk(argument)
                    if isinstance(call, ast.Call)
                    and getattr(call.func, "id", getattr(call.func, "attr", "")) in _SAFE_RENDERERS
                    and any(n is name for n in ast.walk(call))
                ]
                # type(exc).__name__ is a shape, not a value, and is equally fine.
                shaped = any(
                    isinstance(a, ast.Attribute) and a.attr == "__name__" for a in ast.walk(argument)
                )
                if not enclosing and not shaped:
                    raw.append(f"{module}:{node.lineno}: `{name.id}` is logged without log_safe")

    assert checked, f"no caught exception reaches a log line in {module}, so this scan proves nothing"
    assert raw == [], raw
