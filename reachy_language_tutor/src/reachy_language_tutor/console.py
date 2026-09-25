"""Bidirectional local audio stream with optional web settings UI.

If the selected backend is missing its required API key, a settings page is
served via the Reachy Mini Apps settings server so users can configure it.
"""

import os
import re
import time
import asyncio
import logging
import ipaddress
from typing import Any, List, Optional
from pathlib import Path
from urllib.parse import urlsplit
from collections.abc import Callable

import numpy as np

from reachy_mini import ReachyMini
from reachy_mini.io.jsonrpc import JsonRpcError
from reachy_mini.apps.jsonrpc_server import JsonRpcServer
from reachy_mini.media.media_manager import MediaBackend
from reachy_language_tutor.utils import describe_for_log, describe_json_for_log
from reachy_language_tutor.config import (
    HF_BACKEND,
    LOCKED_PROFILE,
    HF_REALTIME_WS_URL_ENV,
    HF_LOCAL_CONNECTION_MODE,
    HF_DEPLOYED_CONNECTION_MODE,
    HF_REALTIME_CONNECTION_MODE_ENV,
    config,
    get_default_voice,
    get_hf_session_url,
    set_custom_profile,
    get_available_voices,
    get_hf_direct_ws_url,
    build_hf_direct_ws_url,
    has_hf_realtime_target,
    parse_hf_direct_target,
    get_hf_connection_selection,
    refresh_runtime_config_from_env,
)
from reachy_language_tutor.prompts import get_session_voice, get_session_instructions
from reachy_language_tutor.streaming import AdditionalOutputs, audio_to_float32
from reachy_language_tutor.logging_safety import where, log_safe
from reachy_language_tutor.startup_settings import read_startup_settings, write_startup_settings
from reachy_language_tutor.tools.core_tools import initialize_tools
from reachy_language_tutor.tool_space_routes import register_tool_space_methods
from reachy_language_tutor.personality_routes import (
    build_personality_ops,
    register_personality_methods,
)
from reachy_language_tutor.profile_tool_routes import register_profile_tool_methods
from reachy_language_tutor.audio.startup_config import apply_audio_startup_config
from reachy_language_tutor.conversation_handler import ConversationHandler


try:
    # FastAPI is provided by the Reachy Mini Apps runtime
    from fastapi import FastAPI, Response, WebSocket
    from pydantic import BaseModel
    from fastapi.responses import FileResponse
    from starlette.staticfiles import StaticFiles
except Exception:  # pragma: no cover - only loaded when settings_app is used
    FastAPI = object  # type: ignore
    WebSocket = object  # type: ignore
    FileResponse = object  # type: ignore
    StaticFiles = object  # type: ignore
    BaseModel = object  # type: ignore

logger = logging.getLogger(__name__)


# What a message IS, when what it is means it must not be logged in cleartext. A tool
# call and a tool result are different things that need the same handling, so the set
# says so rather than one standing in for the other.
REDACTED_MESSAGE_KINDS = frozenset({"tool_call", "tool_result"})

# What a Hugging Face host IS: a DNS name or an IP literal, and nothing else.
#
# This is an allow-list on purpose. The deny-list it replaces named four URL characters
# -- "://", "/", "?" and "#" -- and so admitted a line break, which is the whole of D19:
# `_persist_env_values` writes `NAME=value` and joins on "\n", so a newline inside the
# value became extra .env lines that were never validated as a NAME=value pair, and
# `load_dotenv` read them back on every later start. `.strip()` only trims the ends, so
# it never saw one in the middle. Naming what is permitted refuses a line break, a space
# and every shell metacharacter at once, including the ones nobody has thought of -- the
# same reason the learner-scoping rule in learners/store.py is an allow-list.
#
# Underscore is admitted alongside the RFC hostname characters because container and
# internal DNS names use it and the old check allowed it; it carries none of the
# injection risk this rule is about, so refusing it would only break working setups.
#
# The leading lookahead requires at least one letter or digit, so `..`, `-` and a run of
# dots are refused rather than accepted as names. That is strictness, not security: every
# character in the class is inert in both sinks -- none can break a NAME=value line or the
# ws:// URL's structure -- so a degenerate value only ever failed to connect. It is here so
# the rule means what the line above says it means.
_HF_NAME = re.compile(r"(?=.*[A-Za-z0-9])[A-Za-z0-9._-]{1,255}")


def _is_an_hf_host(host: str) -> bool:
    """Say whether this is a DNS name or an IP literal, and nothing else.

    IPv6 has to be accepted in BOTH spellings, and getting that wrong is a round trip that
    does not close: `build_hf_direct_ws_url` stores the bracketed form `ws://[::1]:8765/`,
    but `parse_hf_direct_target` reads the host back with `urlsplit().hostname`, which
    strips the brackets and yields `::1`. A rule that accepted only the bracketed form
    therefore admitted an address on the way in and refused the very same address on the
    way back, locking an IPv6-configured instance out of its own settings form. That helper
    is what `patterns_to_follow` names as the definition of a valid host, so the rule is
    written to agree with it rather than beside it.

    `ipaddress` decides the IPv6 half instead of a hand-rolled character class: it accepts
    exactly the real addresses and rejects `:::::`, and it cannot admit a line break or a
    separator because anything malformed raises. A zone id (`fe80::1%eth0`) is refused --
    `ipaddress` allows it, but `%` is percent-encoding to every URL parser downstream.
    """
    if _HF_NAME.fullmatch(host):
        return True
    candidate = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    if "%" in candidate:
        return False
    try:
        return isinstance(ipaddress.ip_address(candidate), ipaddress.IPv6Address)
    except ValueError:
        return False


# The only /rpc methods a caller may invoke, and an ALLOW-LIST on purpose.
#
# The app's UI port has to be LAN-reachable (D15/D20) and nothing distinguishes the
# dashboard's iframe from anything else on that network, so every method registered here is
# reachable by anyone in the house. D20's first attempt neutered the two methods the task
# named -- conversation.mic and backend.config -- and left eight other writers reachable
# while restoring the LAN bind: personalities.save/delete/apply, voices.apply,
# tool_spaces.add/remove and profile_tools.save/reset, of which tool_spaces.add installs a
# caller-named Hugging Face Space as a tool the conversation can call. Opening the port
# having closed two of ten doors was a net loss.
#
# Naming the dangerous ones is the same losing shape as D19's four-character deny-list and
# D11's substring rule: such a list is only ever as complete as the last person to read it.
# So this names what is EXPOSED and refuses everything else -- including any method added
# later, by this file or by the register_*_methods helpers, which is the property that
# survives the next change rather than the next reading.
#
# conversation.say and conversation.interrupt are here deliberately and are not reads.
# interrupt cuts the robot off. say is more than "make the robot speak": it injects a
# role=user message and asks the model to respond, exactly like a transcribed learner
# turn, so the model may call learner tools on it -- including finish_lesson, which
# writes -- for whoever the app is serving. The method itself persists nothing; what the
# model does next can. Keeping it is recorded as an open exposure in
# docs/rpc-control-surface.md and docs/privacy-and-consent.md, pending a decision on
# whether the dashboard needs it.
#
# Re-allowing a writer means re-adding whatever restriction made it safe -- notably
# backend.config, whose host-validation and .env-sink checks stop being reachable once it is
# gated off here -- re-exposing it means deciding again what target it may be given.
_RPC_METHODS_EXPOSED_ON_THE_NETWORK = frozenset(
    {
        "conversation.status",
        "conversation.say",
        "conversation.interrupt",
        "conversation.mic",
        "personalities.list",
        "personalities.all",
        "personalities.load",
        "personalities.avatar",
        "voices.list",
        "voices.current",
        "tool_spaces.list",
        "profile_tools.get",
    }
)


# The developer opt-in that lets conversation.transcript leave this process. Read ONCE,
# when the /rpc server is built at startup (after the instance .env has been loaded), and
# only the exact value "1" switches it on -- an allow-list of one, so "true", "yes" or a
# stray "0" mean off rather than whatever a parser guessed.
#
# Why it is off by default: /rpc is LAN-reachable and cannot be authenticated (see
# docs/rpc-control-surface.md), and a broadcast reaches every attached socket whether or
# not it calls a method. Measured before this change: a client dialling the robot's LAN
# address, with no Origin and no credential, received the learner's words and the tutor's
# replies verbatim. The shipped settings UI never subscribes to conversation.transcript
# (static/ uses conversation.status, .mic and .activity only), so switching it off costs
# the UI nothing. docs/manual-test-script.md is the one workflow that reads it, and it
# sets this variable on purpose.
DEV_BROADCAST_TRANSCRIPT_ENV = "REACHY_MINI_DEV_BROADCAST_TRANSCRIPT"

# What a machine code looks like: lower-case, underscores, digits, short. Every string an
# allowed notification carries has to be one of these, which is what keeps free text --
# a name, a sentence somebody said -- out of the params even of a permitted notification.
_MACHINE_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")


def _is_code(value: object) -> bool:
    return isinstance(value, str) and _MACHINE_CODE.fullmatch(value) is not None


def _is_code_or_none(value: object) -> bool:
    return value is None or _is_code(value)


def _is_speaker(value: object) -> bool:
    return value in ("user", "assistant")


def _is_unit_level(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0.0 <= value <= 1.0


def _is_text(value: object) -> bool:
    return isinstance(value, str)


def _is_flag(value: object) -> bool:
    return isinstance(value, bool)


# The only notifications /rpc may SEND, and the only params each may carry. The outgoing
# half of the allow-list above, and for the same reason: the methods allow-list governed
# what a caller may ask for, while every broadcast went to every peer unfiltered, so the
# exposure the port was judged by never looked at the one thing that carried a learner's
# words. A notification not named here, a param not named for it, or a value its check
# refuses, is dropped rather than sent -- so a broadcast added later is withheld by
# default until somebody decides here that it may cross the network.
_NOTIFICATIONS_SENT_ON_THE_NETWORK: dict[str, dict[str, Callable[[object], bool]]] = {
    "conversation.turn": {"state": _is_code, "reason": _is_code},
    "conversation.activity": {"reason": _is_code},
    "conversation.phase": {"phase": _is_code, "reason": _is_code_or_none},
    "conversation.level": {"role": _is_speaker, "rms": _is_unit_level},
}

# Added to the set above only under DEV_BROADCAST_TRANSCRIPT_ENV. Text is free text by
# definition, which is exactly why it is not in the default set.
_TRANSCRIPT_NOTIFICATION = ("conversation.transcript", {"role": _is_speaker, "text": _is_text, "final": _is_flag})


def _transcript_broadcast_opted_in() -> bool:
    """Say whether a developer asked, in the environment, for transcripts on /rpc."""
    return (os.getenv(DEV_BROADCAST_TRANSCRIPT_ENV) or "").strip() == "1"


def _notification_is_permitted(
    allowed: dict[str, dict[str, Callable[[object], bool]]], method: object, params: object
) -> bool:
    if not isinstance(method, str) or method not in allowed:
        return False
    if params is None:
        return True
    if not isinstance(params, dict):
        return False
    shape = allowed[method]
    return all(key in shape and shape[key](value) for key, value in params.items())


# What a caller is told when a method fails. A JSON-RPC error goes back to whoever sent
# the request, and on this port that is anyone on the household network, so its text is
# built here from the error's REASON -- a machine code the UI branches on
# (static/js/api.js ERROR_MESSAGES) -- and never from an exception's message or data.
# Measured before this: personalities.load answered a 300-character name with
# "[Errno 63] File name too long: '<instance>/user_personalities/.../profile.md'",
# tool_spaces.list quoted the manifest's full path, and the SDK turns any other exception
# into its str(). One wrapper at registration covers every handler, including ones added
# later, instead of a try/except per route that the next route forgets.
_INTERNAL_ERROR_REASON = "internal_error"


def _answer_the_network_safely(name: str, handler: Any) -> Any:
    """Wrap a handler so a failure reaches the caller as a reason code and nothing else."""

    async def _sanitised(params: dict[str, Any]) -> Any:
        try:
            result = handler(params)
            if asyncio.iscoroutine(result):
                result = await result
            return result
        except asyncio.CancelledError:
            raise
        except JsonRpcError as exc:
            reason = exc.reason if _is_code(exc.reason) else _INTERNAL_ERROR_REASON
            raise JsonRpcError(reason, reason=reason, code=exc.code) from None
        except Exception as exc:
            # The type, never the message: this is the text the caller no longer gets.
            logger.warning("/rpc method %s failed: %s", name, log_safe(exc))
            raise JsonRpcError(_INTERNAL_ERROR_REASON, reason=_INTERNAL_ERROR_REASON, code=-32603) from None

    return _sanitised


def _refuse_over_the_network(name: str) -> Any:
    """Build the handler that stands in for a method this instance does not expose."""

    async def _refused(_params: dict[str, Any]) -> Any:
        raise JsonRpcError(
            f"{name} is not available over the network; this instance is configured from its .env and files",
            reason="not_available_over_the_network",
            code=-32601,
        )

    return _refused


class _NetworkRestrictedRpcServer(JsonRpcServer):
    """A JsonRpcServer that refuses any method not named in the allow-list above.

    `JsonRpcServer.method()` delegates to `register()`, so overriding the one covers both
    registration paths: the decorator this file uses and the explicit `rpc.register(...)`
    calls in personality_routes, tool_space_routes and profile_tool_routes. A method is
    therefore refused by DEFAULT -- someone adding one has to come here to expose it, which
    is the opposite of the situation that made D20's first attempt wrong.

    It also owns the two things that go back OUT: every handler is wrapped so a failure
    answers with a reason code and no exception text, and `broadcast` -- which
    `broadcast_threadsafe` delegates to, so it is the one sink every notification passes
    through -- sends only what _NOTIFICATIONS_SENT_ON_THE_NETWORK names.
    """

    def __init__(self, *, broadcast_transcript: bool = False) -> None:
        """Fix, at construction, which notifications this server may send."""
        super().__init__()
        allowed = dict(_NOTIFICATIONS_SENT_ON_THE_NETWORK)
        if broadcast_transcript:
            allowed[_TRANSCRIPT_NOTIFICATION[0]] = _TRANSCRIPT_NOTIFICATION[1]
        self._notifications_allowed = allowed

    def register(self, name: str, handler: Any) -> None:
        """Register a handler, or a refusal in its place when the method is not exposed."""
        if name not in _RPC_METHODS_EXPOSED_ON_THE_NETWORK:
            handler = _refuse_over_the_network(name)
        super().register(name, _answer_the_network_safely(name, handler))

    async def broadcast(self, method: str, params: Optional[dict[str, Any]] = None) -> None:
        """Send a notification to every peer, if it is one this server may send."""
        if not _notification_is_permitted(self._notifications_allowed, method, params):
            # The name only when it is one of ours; never the params, which are what
            # was refused. DEBUG, because a withheld transcript is the normal case.
            shown = (
                method
                if method in _NOTIFICATIONS_SENT_ON_THE_NETWORK or method == _TRANSCRIPT_NOTIFICATION[0]
                else "an unlisted notification"
            )
            logger.debug("Withheld %s from /rpc: not permitted on the network", shown)
            return
        await super().broadcast(method, params)


def _origin_is_this_server(origin: str, host_header: str) -> bool:
    """Say whether this Origin is the page this very server sent, and not a DNS rebind.

    Comparing Origin to the Host header alone proves nothing, because a caller supplies
    both: a page on evil.example whose name is rebound to the robot's LAN address sends
    `Origin: http://evil.example` with `Host: evil.example:7860`, they agree, and the check
    that only compares them admits it. That is DNS rebinding, and it is a browser attack --
    exactly the thing an Origin check exists to stop -- so the comparison has to be anchored
    to something the attacker cannot restate.

    The anchor is WHICH NAMES CAN BE REBOUND FROM OFF THE LINK. Rebinding is a remote attack:
    it needs a name the attacker controls in the public DNS, pointed at this robot, so that a
    page they serve to a browser anywhere becomes same-origin with it. An IP literal cannot
    be used that way, `localhost` cannot, and `.local` has no public delegation, so none of
    the three gives a remote attacker an origin here. Every other name does, and is refused.

    `.local` is NOT unspoofable, and the comment should not claim it is: mDNS is
    unauthenticated, so somebody already on the link can answer for a `.local` name. That
    adversary is not the one this check is for -- they are on the network the port is open
    to, where the Origin header is theirs to write anyway, and where the real mitigation is
    that every writer is refused. What this check buys is the remote browser case, and there
    `.local` is genuinely out of reach.

    `.local` is admitted deliberately rather than by oversight: the dashboard builds its URL
    from whatever host the desktop app connected to, which is frequently the robot's mDNS
    name rather than its address, so refusing names outright would have locked the dashboard
    out of the app -- the exact failure D20 exists to undo, reintroduced by its own fix.

    A refusal is logged, because a handshake closed with 1008 and no explanation is the kind
    of thing that costs somebody an afternoon.
    """
    if urlsplit(origin).netloc != host_header:
        logger.warning("Refused an /rpc handshake: Origin %r does not match Host %r", origin, host_header)
        return False
    hostname = (urlsplit(f"//{host_header}").hostname or "").lower()
    if hostname == "localhost" or hostname.endswith(".local"):
        return True
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        logger.warning(
            "Refused an /rpc handshake from %r: this server is addressed by IP, localhost or an "
            "mDNS .local name, and a public DNS name can be rebound to it",
            host_header,
        )
        return False
    return True


def _mount_rpc_with_origin_check(rpc: JsonRpcServer, app: "FastAPI", path: str = "/rpc") -> None:
    """Mount /rpc behind a same-origin check, without patching the SDK.

    `JsonRpcServer.mount` registers a route that calls `_serve`, which accepts the handshake
    unconditionally. A WebSocket handshake is exempt from the same-origin policy and is not
    preflighted, so any page in any browser that can reach this port could open
    ws://<host>:7860/rpc cross-origin and call every method registered here. Registering the
    same route ourselves and checking Origin first closes that, and leaves the SDK
    untouched so the dependency stays replaceable.

    Two boundaries this deliberately does NOT cross:

    An ABSENT Origin is allowed, because the daemon's JSON-RPC relay connects to this
    endpoint itself and sends no Origin -- refusing it would break the daemon's own access
    rather than an attacker's, which is the trap D20 was filed with.

    And this is not protection against a LAN client. Origin is a header a browser attaches
    and anything else can forge, so a direct caller sets whatever it likes. The mitigation
    against a direct caller is that the methods reachable here cannot do harm: the mic is
    read-only and every writer is refused outright. docs/rpc-control-surface.md records
    why no credential is available to this app to do better.
    """

    @app.websocket(path)
    async def _rpc_ws(websocket: "WebSocket") -> None:  # pragma: no cover - I/O
        origin = websocket.headers.get("origin")
        if origin is not None and not _origin_is_this_server(origin, websocket.headers.get("host", "")):
            await websocket.close(code=1008)
            return
        await rpc._serve(websocket)


def log_handler_message(msg: dict) -> None:
    """Log one message from the handler's output queue.

    Extracted from play_loop so a test can drive the real thing: this line is INFO,
    so it is written whether or not --debug is on, and it is the sink a learner's
    name actually reaches. A test that reimplemented this branch would pass while
    the real one leaked, so there is one copy of it and the test calls it.

    A tool call or a tool result says so, and carries a log-safe rendering of itself,
    because its content is a person's data rather than transcript. The KIND decides:
    a producer that forgot the rendering, or built a malformed one, still gets
    redacted rather than falling through to cleartext. An ordinary transcript
    message carries neither, and is described at INFO and spoken at DEBUG -- see
    the branch below for why that split, and not the alternatives, was chosen.
    """
    kind = msg.get("kind")
    if kind in REDACTED_MESSAGE_KINDS:
        log_safe = msg.get("log_safe")
        if isinstance(log_safe, str):
            logger.info("role=%s content=%s", msg.get("role"), log_safe)
            return
        # Degraded but not blind, and NOT deciding this for itself. This branch used
        # to read `trust_keys=kind == "tool_result"`: a blanket yes for every tool
        # result, including a RemoteMcpTool whose envelope keys are written by a
        # third-party Space and may be a learner's name. The producer already knew
        # better one layer up, the two spellings disagreed, and this one runs
        # precisely when the producer failed -- so the fallback for a broken producer
        # was the unsafe one (D34).
        #
        # The verdict now arrives WITH the record, decided where the tool object is
        # unambiguous. Deriving it here from a tool name, which the first fix did,
        # re-opened the question against a registry that initialize_tools(force=True)
        # can rebind between the queue put and this get -- the security review
        # reproduced a learner's name reaching INFO that way.
        #
        # `is True` and not a truthiness test: an absent key, a None, a string, or
        # anything an older producer put there is not a yes. A call's keys are
        # composed by the model and are never trusted, which is why only a result
        # even carries a verdict.
        trusted = kind == "tool_result" and msg.get("log_keys_trusted") is True
        logger.info(
            "role=%s content=%s",
            msg.get("role"),
            describe_json_for_log(msg.get("content"), trust_keys=trusted),
        )
        return

    content = msg.get("content", "")
    # Outside the isinstance guard on purpose. A producer queues event.transcript
    # straight from the SDK, which types it as optional -- and a None there used to
    # fall off this branch and log nothing at any level, so the one turn whose shape
    # an operator most needs to see would be the turn that left no trace.
    # describe_for_log renders whatever arrives without reading it.
    logger.info("role=%s content=%s", msg.get("role"), describe_for_log(content))

    if isinstance(content, str):
        # Transcript -- the person's own words and the robot's own speech. It is the
        # remaining route a learner's name reaches the log: a tutor that has just read
        # get_profile says "Hello Alice" out loud, and that sentence is transcript, not
        # a tool result. D3 closed the tool-result routes and deliberately left this one
        # open, because blanket-redacting transcript would destroy what the console is
        # for. D9 is the decision about this route, and it is DEMOTE -- neither accept
        # nor redact:
        #
        #   INFO keeps the SHAPE: who spoke and how much. An operator reading captured
        #   stderr from someone's home still sees that a turn happened, and the
        #   "did it hear me" recipe in docs/SETUP.md -- grep -icE "role=user|..." --
        #   still counts turns, which a silent demotion would have broken while
        #   printing zero and reading as a deaf microphone.
        #
        #   The WORDS move to DEBUG, which is --debug: an opt-in an operator asks for
        #   on a robot they are debugging, rather than the default in every unrelated
        #   household whose logs nobody chose to collect. The argument does not depend on
        #   how many households there are, and only gets stronger as they multiply.
        #
        # This log line is not the only transcript sink: LocalStream._dispatch_transcript
        # offers the same words to /rpc as conversation.transcript, where the network
        # server withholds them unless DEV_BROADCAST_TRANSCRIPT_ENV is set -- the shipped
        # settings UI never displays them. Do not reach for log_safe here: that seam is
        # for payloads that are DATA, and transcript is the app's own speech.
        logger.debug(
            "role=%s content=%s",
            msg.get("role"),
            content if len(content) < 500 else content[:500] + "\u2026",
        )


def _detach_framework_root_routes(app: "FastAPI") -> None:
    """Strip framework routes that would shadow the settings UI."""
    routes = getattr(app, "router", None)
    routes = getattr(routes, "routes", None) if routes else getattr(app, "routes", None)
    if routes is None:
        return
    survivors = []
    for route in routes:
        path = getattr(route, "path", None)
        is_catch_all = isinstance(path, str) and path.startswith("/{") and path.endswith(":path}")
        if path in ("/", "/static") or is_catch_all:
            logger.debug("detaching framework-provided route %r (%s)", path, type(route).__name__)
            continue
        survivors.append(route)
    routes[:] = survivors


LOCAL_PLAYER_BACKEND = (
    getattr(MediaBackend, "LOCAL", None)
    or getattr(MediaBackend, "GSTREAMER", None)
    or getattr(MediaBackend, "DEFAULT", None)
)

HandlerFactory = Callable[[Optional[str]], ConversationHandler]

LEGACY_STARTUP_ENV_NAMES = (
    "REACHY_MINI_CUSTOM_PROFILE",
    "REACHY_MINI_VOICE_OVERRIDE",
)
BACKEND_RETRY_DELAY_SECONDS = 5.0


class LocalStream:
    """LocalStream using Reachy Mini's recorder/player."""

    def __init__(
        self,
        handler: ConversationHandler,
        robot: ReachyMini,
        *,
        settings_app: Optional[FastAPI] = None,
        instance_path: Optional[str] = None,
        handler_factory: HandlerFactory | None = None,
        startup_voice: Optional[str] = None,
    ):
        """Initialize the stream with a realtime handler and pipelines.

        - ``settings_app``: the Reachy Mini Apps FastAPI to attach settings endpoints.
        - ``instance_path``: directory where per-instance ``.env`` should be stored.
        - ``handler_factory``: builds a fresh handler for the currently selected backend.
        """
        self._robot = robot
        self._stop_event = asyncio.Event()
        self._restart_requested = asyncio.Event()
        self._tasks: List[asyncio.Task[None]] = []
        self._handler_factory = handler_factory
        self._voice_override = startup_voice
        self._settings_app: Optional[FastAPI] = settings_app
        self._instance_path: Optional[str] = instance_path
        self._settings_initialized = False
        self._asyncio_loop = None
        self._mic_muted = False  # mic starts live; the UI toggles it via the settings API
        self._backend_connection_state = "not_started"
        self._backend_error: str | None = None
        self._backend_retry_delay = BACKEND_RETRY_DELAY_SECONDS
        # JSON-RPC control surface (mounted at /rpc in _init_settings_ui_if_needed).
        # Notifications (conversation.turn/phase/activity/level) are pushed here, and
        # only the ones _NOTIFICATIONS_SENT_ON_THE_NETWORK names leave it -- transcripts
        # only under the developer opt-in. Survives handler rebuilds (mounted once).
        self._rpc: Optional[JsonRpcServer] = None
        self._last_turn_state: Optional[str] = None
        # Per-role throttle timestamps for conversation.level (orb audio meter).
        self._last_level_emit: dict[str, float] = {}
        self._install_handler(handler)

    def _install_handler(self, handler: ConversationHandler) -> None:
        """Set the active handler and wire LocalStream-owned helpers into it."""
        self.handler = handler
        self.handler._clear_queue = self.clear_audio_queue
        self._attach_observers_to_handler()

    def _attach_observers_to_handler(self) -> None:
        """Wire the handler's activity + transcript observers to JSON-RPC pushes."""
        setter = getattr(self.handler, "set_activity_observer", None)
        if callable(setter):
            setter(self._dispatch_activity)
        transcript_setter = getattr(self.handler, "set_transcript_observer", None)
        if callable(transcript_setter):
            transcript_setter(self._dispatch_transcript)

    def _dispatch_transcript(self, role: str, text: str, final: bool) -> None:
        """Offer a conversation.transcript notification to JSON-RPC clients.

        Offered, not sent: _NetworkRestrictedRpcServer.broadcast withholds it unless
        DEV_BROADCAST_TRANSCRIPT_ENV was set at startup, because every /rpc peer on the
        household network would otherwise receive the learner's words verbatim.
        """
        if self._rpc is not None:
            self._rpc.broadcast_threadsafe(
                "conversation.transcript",
                {"role": role, "text": text, "final": final},
            )

    # Audio level meter for the client orb. RMS is scaled into a visible 0..1
    # range and capped to ~15 Hz so it stays light on the DataChannel.
    _LEVEL_INTERVAL_S = 1.0 / 15.0
    _LEVEL_GAIN = 6.0

    def _emit_level(self, role: str, frame: Any) -> None:
        """Emit a throttled conversation.level (RMS) for ``role`` (user/assistant)."""
        if self._rpc is None:
            return
        now = time.monotonic()
        if now - self._last_level_emit.get(role, 0.0) < self._LEVEL_INTERVAL_S:
            return
        self._last_level_emit[role] = now
        try:
            samples = audio_to_float32(frame)
            rms = float(np.sqrt(np.mean(np.square(samples)))) if samples.size else 0.0
        except Exception:
            return
        level = max(0.0, min(1.0, rms * self._LEVEL_GAIN))
        self._rpc.broadcast_threadsafe("conversation.level", {"role": role, "rms": round(level, 3)})

    # Map backend activity reasons to the orb's turn states (mirrors the old
    # browser orb's mapActivityToState so the orb reliably reaches listening/
    # thinking/speaking across the reasons the HF handler actually emits).
    # Transcript text is delivered separately via the transcript observer.
    _REASON_TO_TURN = {
        "user_speech_started": "listening",
        "user_transcription_delta": "listening",
        "user_speech_stopped": "thinking",
        "user_transcription_completed": "thinking",
        "response_created": "thinking",
        "tool_call_received": "thinking",
        "tool_result_ready": "thinking",
        "assistant_audio_delta": "speaking",
        "assistant_transcript_done": "ready",
    }

    def _dispatch_activity(self, reason: str) -> None:
        """Fan one activity reason out to JSON-RPC clients."""
        if self._rpc is not None:
            # Raw reason (the browser orb maps it exactly like the old SSE feed)...
            self._rpc.broadcast_threadsafe("conversation.activity", {"reason": reason})
            # ...plus a semantic turn state for clients without that mapping (mobile).
            state = self._REASON_TO_TURN.get(reason)
            if state and state != self._last_turn_state:
                self._last_turn_state = state
                self._rpc.broadcast_threadsafe("conversation.turn", {"state": state})

    def _emit_phase(self, phase: str, reason: Optional[str] = None) -> None:
        """Push a conversation.phase notification to JSON-RPC clients."""
        if self._rpc is not None:
            self._rpc.broadcast_threadsafe("conversation.phase", {"phase": phase, "reason": reason})

    def seconds_since_activity(self) -> float:
        """Seconds since the live handler last saw conversation activity."""
        return time.monotonic() - self.handler.last_activity_time

    def _read_env_lines(self, env_path: Path) -> list[str]:
        """Load env file contents or a template as a list of lines."""
        inst = env_path.parent
        try:
            if env_path.exists():
                try:
                    return env_path.read_text(encoding="utf-8").splitlines()
                except Exception:
                    return []
            template_text = None
            ex = inst / ".env.example"
            if ex.exists():
                try:
                    template_text = ex.read_text(encoding="utf-8")
                except Exception:
                    template_text = None
            if template_text is None:
                try:
                    cwd_example = Path.cwd() / ".env.example"
                    if cwd_example.exists():
                        template_text = cwd_example.read_text(encoding="utf-8")
                except Exception:
                    template_text = None
            if template_text is None:
                packaged = Path(__file__).parent / ".env.example"
                if packaged.exists():
                    try:
                        template_text = packaged.read_text(encoding="utf-8")
                    except Exception:
                        template_text = None
            return template_text.splitlines() if template_text else []
        except Exception:
            return []

    def _backend_connected(self) -> bool:
        """Return whether the active handler currently has a realtime connection."""
        try:
            handler_state = vars(self.handler)
        except TypeError:
            handler_state = {}
        return handler_state.get("connection") is not None

    def _can_rebuild_handler(self) -> bool:
        """Return whether LocalStream can construct handlers for backend changes."""
        return self._handler_factory is not None

    def _build_handler_for_current_backend(self) -> ConversationHandler:
        """Create and install a fresh handler for the current runtime backend config."""
        if self._handler_factory is None:
            return self.handler
        handler = self._handler_factory(self._voice_override)
        self._install_handler(handler)
        return handler

    async def _shutdown_active_handler(self) -> None:
        """Best-effort shutdown for the currently active handler."""
        try:
            await self.handler.shutdown()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug("Active handler shutdown ignored during restart: %s", log_safe(e))

    def _mark_restart_requested(self, reason: str) -> None:
        """Request a backend restart from a synchronous route handler."""
        logger.info("Backend restart requested: %s", reason)
        self._set_backend_connection_state("connecting")
        loop = self._asyncio_loop
        if loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(self.request_backend_restart(reason), loop)
            return
        self._restart_requested.set()

    async def request_backend_restart(self, reason: str) -> None:
        """Ask the startup loop to rebuild the backend and stop the current handler."""
        self._set_backend_connection_state("connecting")
        self._restart_requested.set()
        await self._shutdown_active_handler()

    async def _sleep_or_restart_requested(self, delay: float) -> None:
        """Sleep for a retry interval, waking early if a restart is requested."""
        if self._restart_requested.is_set():
            return
        try:
            await asyncio.wait_for(self._restart_requested.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass

    @staticmethod
    def _format_backend_error(error: BaseException | str) -> str:
        """Return a compact user-facing backend error string, rendered the way a log line is.

        This string is not only user-facing: conversation.status hands it to every /rpc
        peer on the household network. The log line beside each caller already renders
        the same exception through log_safe, and this used the raw str() -- so an OSError
        naming a path under somebody's home directory was withheld from the log and sent
        to the network. The same rule now decides both: a family log_safe renders in full
        keeps its message, anything else is its type (or type and errno for an OSError).
        A plain str is the app's own wording (see the waiting_for_config caller), not an
        exception's, and passes through.
        """
        if isinstance(error, str):
            return error
        rendered = log_safe(error)
        if rendered is not error:
            return str(rendered)
        message = str(error).strip()
        if message:
            return f"{type(error).__name__}: {message}"
        return type(error).__name__

    def _set_backend_connection_state(self, state: str, error: BaseException | str | None = None) -> None:
        """Update backend connection status exposed through the settings UI."""
        self._backend_connection_state = state
        if error is not None:
            self._backend_error = self._format_backend_error(error)
        elif state != "disconnected":
            self._backend_error = None

    def _backend_connection_status(self) -> dict[str, object]:
        """Return the backend connection state exposed in the settings API."""
        connected = self._backend_connected()
        state = "connected" if connected else self._backend_connection_state
        return {
            "backend_connected": connected,
            "backend_connection_state": state,
            "backend_error": None if connected else self._backend_error,
        }

    def _persist_env_values(self, updates: dict[str, str]) -> None:
        """Persist non-empty environment values in memory and in the instance `.env`."""
        normalized_updates = {name: (value or "").strip() for name, value in updates.items()}
        normalized_updates = {name: value for name, value in normalized_updates.items() if value}
        if not normalized_updates:
            return

        # A value carrying CR or LF cannot survive this function as one entry: the writer
        # below appends `NAME=value` and joins the lines on "\n", so an embedded break
        # becomes extra .env lines that were never validated as a NAME=value pair, and
        # load_dotenv reads them back on every later start. The .strip() above trims only
        # the ends, so it never sees one in the middle.
        #
        # Callers are expected to validate their own input -- _rpc_backend_config does,
        # with the _is_an_hf_host allow-list -- and today exactly one attacker-reachable path
        # arrives here. This guard is what holds for the caller that forgets, which is the
        # reason it lives at the sink rather than only at that one validator. It raises
        # instead of dropping the value silently, so the mistake is visible where it is
        # made, and it sits deliberately ABOVE the try/except below, which downgrades
        # failures to a logged warning and would otherwise swallow it.
        # The test is `splitlines() != [value]` rather than a CR/LF check because it has to
        # mean the same thing the READER means by a line. _read_env_lines parses this file
        # with str.splitlines(), which splits on eight separators besides CR and LF:
        # \x0b \x0c \x1c \x1d \x1e \x85 \u2028 \u2029 -- so a CR/LF-only guard let all eight
        # through, wrote them inside one physical line, and the next call that re-read the
        # file split them apart and wrote them back joined on "\n", materialising the second
        # half as a real .env entry that load_dotenv then read on every later start. Testing
        # what splitlines() does is the only version of this guard that cannot drift from
        # the parser it is protecting.
        for env_name, value in normalized_updates.items():
            if value.splitlines() != [value]:
                raise ValueError(f"refusing to persist {env_name}: the value contains a line break")

        for env_name, value in normalized_updates.items():
            try:
                os.environ[env_name] = value
            except Exception:
                pass
        refresh_runtime_config_from_env()

        if not self._instance_path:
            return
        try:
            inst = Path(self._instance_path)
            env_path = inst / ".env"
            lines = self._read_env_lines(env_path)
            for env_name, value in normalized_updates.items():
                replaced = False
                for i, ln in enumerate(lines):
                    if ln.strip().startswith(f"{env_name}="):
                        lines[i] = f"{env_name}={value}"
                        replaced = True
                        break
                if not replaced:
                    lines.append(f"{env_name}={value}")
            final_text = "\n".join(lines) + "\n"
            env_path.write_text(final_text, encoding="utf-8")
            logger.info("Persisted %s to the instance .env", ", ".join(sorted(normalized_updates)))

            try:
                from dotenv import load_dotenv

                load_dotenv(dotenv_path=str(env_path))
            except Exception:
                pass
            refresh_runtime_config_from_env()
        except Exception as e:
            logger.warning("Failed to persist %s: %s", ", ".join(sorted(normalized_updates)), log_safe(e))

    def _remove_persisted_env_values(self, env_names: tuple[str, ...]) -> None:
        """Remove keys from the instance `.env` without mutating the current runtime."""
        normalized_names = tuple(sorted({name.strip() for name in env_names if name and name.strip()}))
        if not normalized_names or not self._instance_path:
            return

        env_path = Path(self._instance_path) / ".env"
        if not env_path.exists():
            return

        try:
            lines = env_path.read_text(encoding="utf-8").splitlines()
            filtered_lines = [
                line
                for line in lines
                if not any(line.strip().startswith(f"{env_name}=") for env_name in normalized_names)
            ]
            if filtered_lines == lines:
                return

            final_text = "\n".join(filtered_lines)
            if final_text:
                final_text += "\n"
            env_path.write_text(final_text, encoding="utf-8")
            logger.info("Removed %s from the instance .env", ", ".join(normalized_names))
        except Exception as e:
            logger.warning("Failed to remove %s: %s", ", ".join(normalized_names), log_safe(e))

    def _persist_hf_direct_connection(self, host: str, port: int) -> None:
        """Persist a direct Hugging Face websocket target."""
        self._persist_env_values(
            {
                HF_REALTIME_CONNECTION_MODE_ENV: HF_LOCAL_CONNECTION_MODE,
                HF_REALTIME_WS_URL_ENV: build_hf_direct_ws_url(host, port),
            }
        )

    def _persist_hf_allocator_connection(self) -> None:
        """Persist the deployed Hugging Face allocator mode."""
        self._persist_env_values({HF_REALTIME_CONNECTION_MODE_ENV: HF_DEPLOYED_CONNECTION_MODE})
        self._remove_persisted_env_values(("HF_REALTIME_SESSION_URL",))

    def _persist_personality(self, profile: Optional[str], voice_override: Optional[str] = None) -> None:
        """Persist startup profile and voice in instance-local UI settings."""
        if LOCKED_PROFILE is not None:
            return
        selection = (profile or "").strip() or None
        normalized_voice_override = (voice_override or "").strip() or None
        set_custom_profile(selection)

        if not self._instance_path:
            return
        try:
            # fallback_learner is read and passed back, not omitted: this function
            # replaces the whole file, so leaving it out would mean saving a
            # personality silently stops the robot serving the person an operator
            # configured -- with no message anywhere. The same reason :832 below
            # already reads the profile back rather than dropping it.
            existing = read_startup_settings(self._instance_path)
            write_startup_settings(
                self._instance_path,
                profile=selection,
                voice=normalized_voice_override,
                fallback_learner=existing.fallback_learner,
            )
            self._remove_persisted_env_values(LEGACY_STARTUP_ENV_NAMES)
            logger.info("Persisted startup personality settings to the instance directory")
        except Exception as e:
            logger.warning("Failed to persist startup personality settings: %s", log_safe(e))

    def _read_persisted_personality(self) -> Optional[str]:
        """Read the saved startup personality from instance-local UI settings."""
        return read_startup_settings(self._instance_path).profile

    async def apply_personality(self, profile: Optional[str]) -> str:
        """Apply a personality by updating config and restarting the active backend."""
        previous_profile = config.REACHY_MINI_CUSTOM_PROFILE
        set_custom_profile(profile)
        try:
            get_session_instructions()
            get_session_voice(default=get_default_voice())
            initialize_tools(force=True)
        except Exception:
            set_custom_profile(previous_profile)
            raise

        await self.request_backend_restart("personality_changed")
        return "Applied personality and restarting backend."

    async def get_available_voices(self) -> list[str]:
        """Return the voices available for the Hugging Face backend."""
        return get_available_voices()

    def get_current_voice(self) -> str:
        """Return the currently selected voice override or profile voice."""
        if self._voice_override:
            return self._voice_override
        try:
            return get_session_voice(default=get_default_voice())
        except Exception as exc:
            logger.warning("Failed to resolve the current profile voice: %s", log_safe(exc))
            return get_default_voice()

    async def change_voice(self, voice: str) -> str:
        """Change the voice through the active handler without rebuilding the backend."""
        try:
            status = await self.handler.change_voice(voice)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("Error changing voice to %r: %s", voice, log_safe(e))
            return f"Failed to change voice: {e}"

        try:
            current_voice = self.handler.get_current_voice()
            if isinstance(current_voice, str) and current_voice.strip():
                self._voice_override = current_voice
        except Exception as e:
            logger.debug("Could not sync LocalStream voice override after voice change: %s", log_safe(e))
        if self._voice_override:
            self._persist_voice_override(self._voice_override)
        return status

    def _persist_voice_override(self, voice: str) -> None:
        """Persist the chosen voice as the startup voice, keeping the startup profile."""
        if not self._instance_path:
            return
        try:
            existing = read_startup_settings(self._instance_path)
            write_startup_settings(
                self._instance_path,
                profile=existing.profile,
                voice=voice,
                fallback_learner=existing.fallback_learner,
            )
        except Exception as e:
            logger.warning("Failed to persist startup voice: %s", log_safe(e))

    def _init_settings_ui_if_needed(self) -> None:
        """Attach minimal settings UI to the settings app.

        Always mounts the UI when a settings_app is provided so that users
        see a confirmation message even if the API key is already configured.
        """
        if self._settings_initialized:
            return
        if self._settings_app is None:
            return
        settings_app = self._settings_app

        static_dir = Path(__file__).parent / "static"
        index_file = static_dir / "index.html"
        logger.info("Serving the settings UI from the packaged static directory")

        # Framework pre-registers GET / and /static; strip them so our routes aren't shadowed.
        _detach_framework_root_routes(settings_app)

        if hasattr(settings_app, "mount"):
            try:
                settings_app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
            except Exception as exc:
                logger.error("Failed to mount settings UI static assets: %s at %s", log_safe(exc), where(exc))
                raise

        def _status_payload() -> dict[str, object]:
            hf_session_url = get_hf_session_url()
            hf_ws_url = get_hf_direct_ws_url()
            hf_direct_host, hf_direct_port = parse_hf_direct_target(hf_ws_url)
            hf_connection_selection = get_hf_connection_selection()
            has_hf_connection = hf_connection_selection.has_target
            backend_connection = self._backend_connection_status()
            return {
                "backend": HF_BACKEND,
                "has_key": has_hf_connection,
                "has_hf_session_url": bool(hf_session_url),
                "has_hf_ws_url": bool(hf_ws_url),
                "has_hf_connection": has_hf_connection,
                "hf_connection_mode": hf_connection_selection.mode,
                "hf_direct_host": hf_direct_host,
                "hf_direct_port": hf_direct_port,
                "can_proceed": has_hf_connection,
                "can_proceed_with_hf": has_hf_connection,
                "requires_restart": not self._can_rebuild_handler(),
                # What this caller is actually allowed to invoke, DERIVED from the
                # allow-list rather than restated. D26: the settings UI used to offer
                # thirteen controls whose writers this surface refuses, so a click got a
                # JSON-RPC error and no explanation. The UI needs to know what is
                # permitted, and the only way to keep it in step with console.py is to
                # send the same object the registrar enforces -- a second list in
                # JavaScript would drift the first time a method is added, which is the
                # deny-list shape CLAUDE.md records four defects against.
                #
                # This is a CONVENIENCE for the UI and never a control: the registrar
                # still refuses anything outside the set, so a caller that ignores this
                # field, or skips the UI entirely, is refused exactly as before.
                "rpc_methods_available": sorted(_RPC_METHODS_EXPOSED_ON_THE_NETWORK),
                **backend_connection,
            }

        # GET / -> index.html
        @settings_app.get("/")
        def _root() -> FileResponse:
            return FileResponse(str(index_file))

        # GET /favicon.ico -> optional, avoid noisy 404s on some browsers
        @settings_app.get("/favicon.ico")
        def _favicon() -> Response:
            return Response(status_code=204)

        # ── JSON-RPC control surface (/rpc) ──────────────────────────────
        # The single wire format both the local browser UI and remote WebRTC
        # clients use (the daemon relays it over the DataChannel). Notifications
        # (conversation.turn/phase/transcript/activity) are pushed from activity.
        broadcast_transcript = _transcript_broadcast_opted_in()
        if broadcast_transcript:
            # WARNING, every start: this is a development switch that puts the
            # conversation on the network, and nobody should run with it by accident.
            logger.warning(
                "%s=1: conversation transcripts are being broadcast on /rpc. Anyone who can "
                "reach this port can read what is said to the robot. Development use only.",
                DEV_BROADCAST_TRANSCRIPT_ENV,
            )
        rpc = _NetworkRestrictedRpcServer(broadcast_transcript=broadcast_transcript)

        # SDK isn't marked py.typed, so mypy sees rpc.method as untyped; safe here.
        @rpc.method("conversation.status")  # type: ignore[untyped-decorator]
        def _rpc_status(_params: dict[str, object]) -> dict[str, object]:
            return _status_payload()

        @rpc.method("conversation.say")  # type: ignore[untyped-decorator]
        async def _rpc_say(params: dict[str, object]) -> dict[str, object]:
            text = str(params.get("text", "")).strip()
            if not text:
                raise JsonRpcError("say requires 'text'", reason="invalid_params", code=-32602)
            if not self.handler._is_connected():
                raise JsonRpcError("no active session", reason="not_running")
            self.clear_audio_queue()  # barge in if mid-utterance
            await self.handler.say(text)
            return {"ok": True}

        @rpc.method("conversation.interrupt")  # type: ignore[untyped-decorator]
        def _rpc_interrupt(_params: dict[str, object]) -> dict[str, object]:
            if not self.handler._is_connected():
                raise JsonRpcError("no active session", reason="not_running")
            self.clear_audio_queue()
            self._last_turn_state = "listening"
            rpc.broadcast_threadsafe("conversation.turn", {"state": "listening", "reason": "interrupted"})
            return {"ok": True}

        @rpc.method("conversation.mic")  # type: ignore[untyped-decorator]
        def _rpc_mic(params: dict[str, object]) -> dict[str, object]:
            """Report whether the microphone is muted. Deliberately read-only.

            This used to accept {"muted": false} and unmute. The app's UI port has to be
            reachable on the household LAN or the desktop dashboard cannot load the app and
            kills it after 60s (D15/D20), and nothing this app can check distinguishes the
            dashboard's iframe from any other caller on that network -- there is no
            credential the SDK can carry to it, which docs/rpc-control-surface.md records in
            full. So the protection cannot be "only the right caller may unmute"; it has to
            be that unmuting a microphone in someone's home is not offered here at all.

            A `muted` parameter is refused rather than ignored, because silently returning
            the unchanged state would read to a caller -- and to the dashboard -- as though
            the mute had been applied.
            """
            if "muted" in params:
                raise JsonRpcError(
                    "the microphone cannot be controlled remotely",
                    reason="mic_is_read_only",
                    code=-32601,
                )
            return {"muted": self._mic_muted}

        @rpc.method("backend.config")  # type: ignore[untyped-decorator]
        def _rpc_backend_config(params: dict[str, object]) -> dict[str, object]:
            hf_selection = get_hf_connection_selection()
            hf_mode = str(params.get("hf_mode") or hf_selection.mode).strip().lower()
            if hf_mode == HF_LOCAL_CONNECTION_MODE:
                existing_host, existing_port = parse_hf_direct_target(hf_selection.direct_ws_url)
                host = str(params.get("hf_host") or "").strip() or existing_host or ""
                if not host:
                    raise JsonRpcError("Hugging Face host required", reason="empty_hf_host", code=-32602)
                if not _is_an_hf_host(host):
                    raise JsonRpcError("invalid Hugging Face host", reason="invalid_hf_host", code=-32602)
                raw_port = params.get("hf_port")
                try:
                    port = int(raw_port) if isinstance(raw_port, (int, float, str)) else (existing_port or 8765)
                except (ValueError, OverflowError):
                    # A bare int() raises with the caller's own value inside the message, and
                    # letting that escape reported plain bad input as an internal error while
                    # reflecting the value back to whoever sent it. `from None` severs the
                    # chain so the original message cannot resurface in a traceback or log.
                    raise JsonRpcError("invalid Hugging Face port", reason="invalid_hf_port", code=-32602) from None
                if port < 1 or port > 65535:
                    raise JsonRpcError("invalid Hugging Face port", reason="invalid_hf_port", code=-32602)
                self._persist_hf_direct_connection(host, port)
            elif hf_mode == HF_DEPLOYED_CONNECTION_MODE:
                if not bool(get_hf_session_url()):
                    raise JsonRpcError(
                        "missing Hugging Face session url", reason="missing_hf_session_url", code=-32602
                    )
                self._persist_hf_allocator_connection()
            else:
                raise JsonRpcError("invalid Hugging Face mode", reason="invalid_hf_mode", code=-32602)

            if self._can_rebuild_handler():
                self._mark_restart_requested("backend_config_changed")
                message = "Connection saved. Reconnecting backend."
            else:
                message = "Connection saved. Restart Reachy Mini Conversation from the desktop app to apply it."
            return {"ok": True, "message": message, **_status_payload()}

        _mount_rpc_with_origin_check(rpc, settings_app)
        self._rpc = rpc

        try:
            personality_ops = build_personality_ops(
                self.handler,
                lambda: self._asyncio_loop,
                persist_personality=self._persist_personality,
                get_persisted_personality=self._read_persisted_personality,
                apply_personality=self.apply_personality,
                get_voices=self.get_available_voices,
                get_current_voice=self.get_current_voice,
                change_voice=self.change_voice,
            )
            # personalities.* / voices.* over JSON-RPC — the local UI and remote
            # clients drive personalities the same way, one control surface.
            register_personality_methods(rpc, personality_ops)
        except Exception as exc:
            logger.error(
                "Failed to register personality methods; the personality UI will be unavailable: %s at %s",
                log_safe(exc),
                where(exc),
            )

        try:
            register_tool_space_methods(
                rpc,
                lambda: self._asyncio_loop,
                self.request_backend_restart,
                instance_path=self._instance_path,
            )
        except Exception as exc:
            logger.error(
                "Failed to register Tool Space methods; remote tool settings will be unavailable: %s at %s",
                log_safe(exc),
                where(exc),
            )

        try:
            register_profile_tool_methods(
                rpc,
                lambda: self._asyncio_loop,
                self.request_backend_restart,
                instance_path=self._instance_path,
            )
        except Exception as exc:
            logger.error(
                "Failed to register profile tool methods; personality tool settings will be unavailable: %s at %s",
                log_safe(exc),
                where(exc),
            )

        self._settings_initialized = True

    async def _run_handler_startup_loop(self) -> None:
        """Start the realtime handler and keep settings UI alive after backend failures."""
        while not self._stop_event.is_set():
            if self._restart_requested.is_set():
                await self._shutdown_active_handler()
                if not self._can_rebuild_handler():
                    self._restart_requested.clear()
                    self._set_backend_connection_state("restart_required")
                    await self._sleep_or_restart_requested(0.5)
                    continue
                self._restart_requested.clear()
                try:
                    self._build_handler_for_current_backend()
                except Exception as e:
                    self._set_backend_connection_state("disconnected", e)
                    logger.warning(
                        "Backend handler failed to initialize: %s at %s. Retrying in %.1f seconds.",
                        log_safe(e),
                        where(e),
                        self._backend_retry_delay,
                    )
                    await self._sleep_or_restart_requested(self._backend_retry_delay)
                    continue

            if not has_hf_realtime_target():
                self._set_backend_connection_state(
                    "waiting_for_config", f"{HF_REALTIME_WS_URL_ENV} is not configured."
                )
                await self._sleep_or_restart_requested(0.5)
                continue

            self._set_backend_connection_state("connecting")
            try:
                await self.handler.start_up()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._set_backend_connection_state("disconnected", e)
                logger.warning(
                    "Backend failed to start: %s at %s. Settings UI remains available; retrying in %.1f seconds.",
                    log_safe(e),
                    where(e),
                    self._backend_retry_delay,
                )
            else:
                if self._stop_event.is_set():
                    return
                self._set_backend_connection_state("disconnected")
                if self._restart_requested.is_set():
                    logger.info("Backend stopped for requested restart.")
                    continue
                logger.info(
                    "Backend session ended. Settings UI remains available; retrying in %.1f seconds.",
                    self._backend_retry_delay,
                )

            await self._sleep_or_restart_requested(self._backend_retry_delay)

    def launch(self) -> None:
        """Start the recorder/player and run the async processing loops.

        If the selected backend is missing its required key, expose a tiny
        settings UI via the Reachy Mini settings server to collect it before
        starting streams.
        """
        self._stop_event.clear()

        # Try to load an existing instance .env first (covers subsequent runs)
        if self._instance_path:
            try:
                from dotenv import load_dotenv

                env_path = Path(self._instance_path) / ".env"
                if env_path.exists():
                    load_dotenv(dotenv_path=str(env_path), override=True)
                    refresh_runtime_config_from_env()
            except Exception:
                pass  # Instance .env loading is optional; continue with defaults

        # Always expose settings UI if a settings app is available
        # (do this AFTER loading the instance .env so status endpoint sees the right value)
        self._init_settings_ui_if_needed()

        # If the Hugging Face target is still missing -> wait until provided via the settings UI
        if not has_hf_realtime_target():
            self._set_backend_connection_state("waiting_for_config", f"{HF_REALTIME_WS_URL_ENV} is not configured.")
            if self._settings_app is None:
                logger.error(
                    "%s not found. Set it in the app .env before starting the Hugging Face backend.",
                    HF_REALTIME_WS_URL_ENV,
                )
                return
            logger.warning("%s not found. Open the app settings page to configure it.", HF_REALTIME_WS_URL_ENV)
            # Poll until a target becomes available (set via the settings UI)
            try:
                while not self._stop_event.is_set() and not has_hf_realtime_target():
                    time.sleep(0.2)
            except KeyboardInterrupt:
                logger.info("Interrupted while waiting for Hugging Face configuration.")
                return
            if self._stop_event.is_set():
                return
            self._set_backend_connection_state("not_started")

        # Start media after key is set/available
        self._robot.media.start_recording()
        self._robot.media.start_playing()

        async def runner() -> None:
            # Capture loop for cross-thread personality actions
            loop = asyncio.get_running_loop()
            self._asyncio_loop = loop  # type: ignore[assignment]
            # Connect the backend first so it overlaps the warmup and audio config below.
            handler_task = asyncio.create_task(self._run_handler_startup_loop(), name="realtime-handler")
            self._tasks = [handler_task]
            await asyncio.gather(
                asyncio.sleep(1),  # give the pipelines time to start
                asyncio.to_thread(apply_audio_startup_config, self._robot, logger=logger),
            )
            self._tasks += [
                asyncio.create_task(self.record_loop(), name="stream-record-loop"),
                asyncio.create_task(self.play_loop(), name="stream-play-loop"),
            ]
            try:
                await asyncio.gather(*self._tasks)
            except asyncio.CancelledError:
                logger.info("Tasks cancelled during shutdown")
            finally:
                # Ensure handler connection is closed
                await self.handler.shutdown()

        asyncio.run(runner())

    def close(self) -> None:
        """Stop the stream and underlying media pipelines.

        This method:
        - Stops audio recording and playback first
        - Sets the stop event to signal async loops to terminate
        - Cancels all pending async tasks (openai-handler, record-loop, play-loop)
        """
        logger.info("Stopping LocalStream...")

        # Stop media pipelines FIRST before cancelling async tasks
        # This ensures clean shutdown before PortAudio cleanup
        try:
            self._robot.media.stop_recording()
        except Exception as e:
            logger.debug("Error stopping recording (may already be stopped): %s", log_safe(e))

        try:
            self._robot.media.stop_playing()
        except Exception as e:
            logger.debug("Error stopping playback (may already be stopped): %s", log_safe(e))

        # close() runs on watcher threads, loop-owned state must change on the loop.
        loop = self._asyncio_loop
        if loop is None or not loop.is_running():
            self._stop_event.set()
            return
        loop.call_soon_threadsafe(self._stop_event.set)
        for task in self._tasks:
            if not task.done():
                loop.call_soon_threadsafe(task.cancel)

    def clear_audio_queue(self) -> None:
        """Flush queued playback audio immediately on user barge-in.

        Calls the SDK's ``clear_player()`` — now a first-class flush on both
        the local GStreamer and WebRTC backends (the WebRTC one also tells the
        daemon to drop audio already queued for the speaker). Falls back to the
        deprecated ``clear_output_buffer()`` only for older SDKs.
        """
        logger.info("User intervention: flushing player queue")
        audio = getattr(self._robot.media, "audio", None)
        if audio is not None:
            if hasattr(audio, "clear_player") and callable(audio.clear_player):
                audio.clear_player()
            elif hasattr(audio, "clear_output_buffer") and callable(audio.clear_output_buffer):
                # Older SDK without clear_player(); best-effort.
                audio.clear_output_buffer()
        # Drain the handler's pending output in place — do NOT replace the
        # queue object, since emit() may be awaiting it (wait_for_item).
        self._drain_output_queue()

    def _drain_output_queue(self) -> None:
        """Empty the handler's output queue in place without replacing it."""
        queue = getattr(self.handler, "output_queue", None)
        if queue is None:
            return
        while not queue.empty():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def record_loop(self) -> None:
        """Read mic frames from the recorder and forward them to the handler."""
        input_sample_rate = self._robot.media.get_input_audio_samplerate()
        logger.debug(f"Audio recording started at {input_sample_rate} Hz")

        while not self._stop_event.is_set():
            audio_frame = self._robot.media.get_audio_sample()
            if audio_frame is not None and not self._mic_muted:
                await self.handler.receive((input_sample_rate, audio_frame))
                self._emit_level("user", audio_frame)
            await asyncio.sleep(0)  # avoid busy loop

    async def play_loop(self) -> None:
        """Fetch outputs from the handler: log text and play audio frames."""
        while not self._stop_event.is_set():
            handler = self.handler
            try:
                handler_output = await asyncio.wait_for(handler.emit(), timeout=0.5)
            except asyncio.TimeoutError:
                continue

            if isinstance(handler_output, AdditionalOutputs):
                for msg in handler_output.args:
                    log_handler_message(msg)

            elif isinstance(handler_output, tuple):
                _, audio_data = handler_output

                # Skip empty audio frames
                if audio_data.size == 0:
                    continue

                # Reshape if needed
                if audio_data.ndim == 2:
                    # channels-last convention
                    if audio_data.shape[1] > audio_data.shape[0]:
                        audio_data = audio_data.T
                    # Multiple channels -> Mono channel
                    if audio_data.shape[1] > 1:
                        audio_data = audio_data[:, 0]

                # Cast if needed
                audio_frame = audio_to_float32(audio_data)

                self._robot.media.push_audio_sample(audio_frame)
                self._emit_level("assistant", audio_frame)

            else:
                logger.debug("Ignoring output type=%s", type(handler_output).__name__)

            await asyncio.sleep(0)  # yield to event loop
