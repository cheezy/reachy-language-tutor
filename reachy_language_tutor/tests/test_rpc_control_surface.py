"""What the /rpc control surface lets a caller DO, given it must be reachable on the LAN.

The app's UI port has to be LAN-reachable or the desktop dashboard cannot load the app and
stops it after 60s (D15/D20). Nothing this app can check distinguishes the dashboard's
iframe from anything else on that network: there is no credential the SDK carries to it,
and a token in the page it serves is readable by the same caller it would exclude -- see
docs/rpc-control-surface.md. So the protection is not "only the right caller may act", it
is that the reachable methods cannot do harm. These tests pin that.

They live here rather than in test_console.py, their otherwise natural home, because at
the time they were written pyproject excluded that whole file from collection, so a test
put there would never have run. D21 removed that exclusion -- every file is collected now
-- so this is a historical reason, not a live constraint: these tests are welcome to move
back beside their subject whenever somebody is editing them anyway.
"""

import re
from types import SimpleNamespace
from typing import Any
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from reachy_language_tutor.config import config
from reachy_language_tutor.console import LocalStream


def _stream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[FastAPI, Any]:
    monkeypatch.setattr(config, "HF_REALTIME_CONNECTION_MODE", "deployed")
    monkeypatch.setattr(config, "HF_REALTIME_SESSION_URL", None)
    monkeypatch.setattr(config, "HF_REALTIME_WS_URL", None)
    app = FastAPI()
    robot = SimpleNamespace(media=SimpleNamespace(audio=None, backend=None))
    stream = LocalStream(MagicMock(), robot, settings_app=app, instance_path=str(tmp_path))
    stream._init_settings_ui_if_needed()
    return app, stream


def _call(app: FastAPI, method: str, params: Any = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
    # starlette's TestClient does headers.setdefault(...), so None is not an accepted way
    # to say "no extra headers" -- an empty dict is. An absent Origin is exactly what the
    # daemon relay sends, so this path has to work.
    with TestClient(app).websocket_connect("/rpc", headers=dict(headers or {})) as ws:
        ws.send_json({"jsonrpc": "2.0", "id": "1", "method": method, "params": params or {}})
        return ws.receive_json()


def test_the_microphone_cannot_be_unmuted_over_rpc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The capability this whole surface is judged by: unmuting a mic in someone's home.

    conversation.mic used to accept {"muted": false}. Since the port must be LAN-reachable
    and no caller can be authenticated, the method is read-only -- reporting state is
    harmless, changing it is not. A `muted` parameter is REFUSED rather than ignored,
    because silently returning the unchanged state would read as though it had been applied.
    """
    app, stream = _stream(tmp_path, monkeypatch)

    assert _call(app, "conversation.mic")["result"] == {"muted": False}

    for attempt in ({"muted": False}, {"muted": True}):
        resp = _call(app, "conversation.mic", attempt)
        assert resp["error"]["data"]["reason"] == "mic_is_read_only", attempt
    assert stream._mic_muted is False, "no /rpc call may change the mute state"


# Every writer registered on this surface. Each one is reachable by anything on the
# household LAN once the port is bound there, so each one must be refused.
_WRITERS: tuple[str, ...] = (
    "backend.config",
    "personalities.save",
    "personalities.delete",
    "personalities.apply",
    "voices.apply",
    "tool_spaces.add",
    "tool_spaces.remove",
    "profile_tools.save",
    "profile_tools.reset",
)


@pytest.mark.parametrize("method", _WRITERS)
def test_no_writer_is_reachable_over_the_network(
    method: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first attempt at this refused two writers and left these eight reachable.

    tool_spaces.add is the one that makes the point: it installs a caller-named Hugging Face
    Space as a tool the conversation can call. A list of the dangerous methods is only ever
    as complete as the last person to read it, so the rule is an allow-list and this asserts
    the consequence for every writer by name.
    """
    app, _s = _stream(tmp_path, monkeypatch)

    resp = _call(app, method, {})

    assert resp["error"]["data"]["reason"] == "not_available_over_the_network", method


def test_the_exposed_method_set_is_exactly_what_was_signed_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A method added later must be refused by default, not exposed by default.

    The coverage target for this task is that every method a LAN caller can reach is either
    harmless or refused, and that cannot be checked by testing the methods someone
    remembered. This enumerates what is actually registered and compares it to the
    allow-list, so adding a method to any of the register_*_methods helpers fails here until
    somebody decides, in console.py, whether it may be exposed.
    """
    from reachy_language_tutor.console import _RPC_METHODS_EXPOSED_ON_THE_NETWORK

    _app, stream = _stream(tmp_path, monkeypatch)
    registered = set(stream._rpc._methods)

    assert registered, "no methods registered -- the fixture is not exercising the real surface"
    unexpected = _RPC_METHODS_EXPOSED_ON_THE_NETWORK - registered
    assert not unexpected, f"the allow-list names methods that do not exist: {sorted(unexpected)}"

    exposed = registered & _RPC_METHODS_EXPOSED_ON_THE_NETWORK
    assert exposed == _RPC_METHODS_EXPOSED_ON_THE_NETWORK

    # Everything else is registered as a refusal rather than simply absent, so a caller is
    # told the method exists and is not offered, and a typo in the allow-list cannot quietly
    # turn into "method not found".
    for name in sorted(registered - _RPC_METHODS_EXPOSED_ON_THE_NETWORK):
        resp = _call(_app, name, {})
        assert resp["error"]["data"]["reason"] == "not_available_over_the_network", name


def test_a_cross_origin_page_cannot_open_the_control_socket(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A WebSocket handshake is exempt from the same-origin policy and is not preflighted.

    Without this check any page in any browser that could reach the port could open
    ws://<host>:7860/rpc and call every method. The SDK accepts the handshake
    unconditionally, so the check is added by registering the route ourselves rather than
    by patching the dependency.
    """
    app, _s = _stream(tmp_path, monkeypatch)

    # Asserting "it raised" would pass for any reason at all, including a broken fixture.
    # The close code is what says the Origin check is the thing that refused it.
    from starlette.websockets import WebSocketDisconnect

    for origin in ("http://evil.example", "https://evil.example", "null"):
        with pytest.raises(WebSocketDisconnect) as refused:
            with TestClient(app).websocket_connect("/rpc", headers={"Origin": origin}) as ws:
                ws.send_json({"jsonrpc": "2.0", "id": "1", "method": "conversation.mic", "params": {}})
                ws.receive_json()
        assert refused.value.code == 1008, f"{origin} was refused, but not by the Origin check"


def test_the_daemon_relay_and_the_apps_own_page_are_still_admitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The check must not close the door on the two callers that are supposed to get in.

    The daemon's JSON-RPC relay connects to this endpoint itself and sends NO Origin header,
    so an absent Origin has to be allowed -- refusing it would break the daemon's own access
    rather than an attacker's, which is the trap this task was filed with.

    The app's own page sends an Origin equal to the host it was served from, and that host is
    an ADDRESS: the dashboard builds http://<robot-lan-ip>:7860/ and a developer types a
    literal. The Host is spelled as an address here for the same reason the check requires
    one -- a name in the Host header is what a DNS-rebinding page would carry.
    """
    app, _s = _stream(tmp_path, monkeypatch)

    # The daemon relay: no Origin at all.
    assert _call(app, "conversation.mic")["result"] == {"muted": False}

    for address in ("192.168.2.235:7860", "127.0.0.1:7860", "localhost:7860", "[::1]:7860"):
        admitted = _call(app, "conversation.mic", None, {"Host": address, "Origin": f"http://{address}"})
        assert admitted["result"] == {"muted": False}, address


def test_a_rebound_domain_name_is_refused_even_when_origin_matches_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Origin == Host proves nothing when the caller supplies both.

    A page on evil.example whose name is rebound to the robot's LAN address sends
    `Origin: http://evil.example` with `Host: evil.example:7860`. They agree, so a check that
    only compares them admits it. The anchor is which names can be REBOUND: a public DNS name
    can be pointed at this robot, so it is refused.
    """
    from starlette.websockets import WebSocketDisconnect

    app, _s = _stream(tmp_path, monkeypatch)

    for name in ("evil.example:7860", "attacker.co.uk", "rebind.attacker.example:7860"):
        with pytest.raises(WebSocketDisconnect) as refused:
            with TestClient(app).websocket_connect(
                "/rpc", headers={"Host": name, "Origin": f"http://{name}"}
            ) as ws:
                ws.send_json({"jsonrpc": "2.0", "id": "1", "method": "conversation.mic", "params": {}})
                ws.receive_json()
        assert refused.value.code == 1008, name


def test_the_robots_mdns_name_is_admitted_so_the_dashboard_is_not_locked_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rebinding fix must not refuse the name the dashboard actually uses.

    The dashboard builds its URL from whatever host the desktop app connected to, which is
    frequently the robot's mDNS name rather than its address. A check that refused every name
    would have closed the app to the dashboard -- reintroducing, through its own fix, the
    lockout D20 exists to undo. `.local` is safe to admit because that namespace is
    link-local: there is no public delegation an attacker could point at this robot.
    """
    app, _s = _stream(tmp_path, monkeypatch)

    for name in ("reachy-mini.local:7860", "REACHY-MINI.LOCAL:7860", "robot.local"):
        admitted = _call(app, "conversation.mic", None, {"Host": name, "Origin": f"http://{name}"})
        assert admitted["result"] == {"muted": False}, name


# --- D25: a LAN caller cannot walk out of the profiles root -------------------------------
#
# personalities.load and personalities.avatar are on the network allow-list and carry NO
# LOCKED_PROFILE gate, because D20 classed them as reads. Both passed a caller-supplied name
# into Config.resolve_profile_dir, which joined it onto the profiles root unvalidated. Measured
# before the fix: a canary profile.md and avatar.svg outside the repository were both read back
# in full, through an absolute path AND through a ../ traversal, through BOTH sinks.


def _canary_outside(tmp_path: Path) -> Path:
    """Write a profile.md and an avatar.svg somewhere no profile lookup may reach."""
    outside = tmp_path / "outside_the_root"
    outside.mkdir(parents=True, exist_ok=True)
    (outside / "profile.md").write_text(
        "+++\nschema_version = 1\ndefault_tools = []\n+++\n\nCANARY-D25-PROFILE\n", encoding="utf-8"
    )
    (outside / "avatar.svg").write_text("<svg>CANARY-D25-AVATAR</svg>", encoding="utf-8")
    return outside


# The four shapes that reached outside the root before the fix, TWO PER BRANCH of
# resolve_profile_dir. Both branches matter and the second pair is the one that catches
# a partial fix: validating only the bare-name branch leaves "user_personalities/<tail>"
# joining an unvalidated tail, and measured here, that partial fix passes every test in
# this file that does not carry a user_personalities prefix.
_ESCAPE_SHAPES = ("absolute", "traversal", "user_personalities_absolute", "user_personalities_traversal")


def _escape_name(shape: str, target: Path) -> str:
    """Build a name that, before the fix, resolved to `target` outside the profiles root."""
    walk = "../" * 24 + str(target).lstrip("/")
    return {
        "absolute": str(target),
        "traversal": walk,
        "user_personalities_absolute": f"user_personalities/{target}",
        "user_personalities_traversal": f"user_personalities/{walk}",
    }[shape]


@pytest.mark.parametrize("shape", _ESCAPE_SHAPES)
def test_a_lan_caller_cannot_read_a_profile_outside_the_profiles_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str
) -> None:
    """personalities.load must not return a profile.md from anywhere on the device."""
    app, _ = _stream(tmp_path, monkeypatch)
    outside = _canary_outside(tmp_path)
    name = _escape_name(shape, outside)

    answer = _call(app, "personalities.load", {"name": name})

    # The specific refusal, not merely "something went wrong": a load that succeeded and
    # happened to return empty instructions would pass a looser assertion.
    assert "result" not in answer, answer
    assert answer["error"]["message"] == "invalid_name"
    assert "CANARY-D25-PROFILE" not in str(answer)


@pytest.mark.parametrize("shape", _ESCAPE_SHAPES)
def test_a_lan_caller_cannot_read_an_avatar_outside_the_profiles_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str
) -> None:
    """The second sink. Fixing only the profile read would leave this one open."""
    app, _ = _stream(tmp_path, monkeypatch)
    outside = _canary_outside(tmp_path)
    name = _escape_name(shape, outside)

    answer = _call(app, "personalities.avatar", {"name": name})

    assert "CANARY-D25-AVATAR" not in str(answer)


@pytest.mark.parametrize("shape", _ESCAPE_SHAPES)
def test_the_avatar_route_is_not_a_filesystem_existence_oracle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str
) -> None:
    """avatar_id_for echoed the caller's own name back when the file existed.

    That made it a never-raising boolean probe for "<any directory>/avatar.svg" -- it leaks
    whether a path exists even when the bytes are withheld, so closing the read alone is not
    enough. A hostile name must be indistinguishable from one that simply has no avatar.
    """
    from reachy_language_tutor.avatars import avatar_id_for

    outside = _canary_outside(tmp_path)
    hostile = _escape_name(shape, outside)

    # The directory really does contain an avatar.svg, which is what makes this a probe.
    assert (outside / "avatar.svg").is_file()
    assert avatar_id_for(hostile) != hostile


def test_the_unknown_profile_error_carries_no_filesystem_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """profile_store quotes the resolved directory; that text must not reach the caller.

    The ProfileFormatError branch is the sharper half of the oracle, because a DIFFERENT
    message came back when a profile.md really was present -- so the two failures must be
    indistinguishable from outside, not merely path-free.
    """
    app, _ = _stream(tmp_path, monkeypatch)

    missing = _call(app, "personalities.load", {"name": "no_such_profile_anywhere"})
    rendered = str(missing)

    assert "result" not in missing, missing
    assert missing["error"]["message"] == "profile_unavailable"
    for leaked in (str(config.PROFILES_DIRECTORY), str(tmp_path), "profile.md", "no_such_profile_anywhere"):
        assert leaked not in rendered, f"the RPC error leaked {leaked!r}"

    # The sharper half of the oracle: a directory that EXISTS but holds an unparsable
    # profile.md used to come back with a different message, so the pair of answers told
    # a caller whether a path was there. Drive that branch too and require the two to be
    # indistinguishable -- the previous version of this test asserted this in a docstring
    # and exercised only the not-found path.
    # Into tmp_path, never the checkout. _stream redirects INSTANCE_PATH only, so
    # config.PROFILES_DIRECTORY still points at the version-controlled profiles/ directory;
    # an earlier revision of this test created its probe there, which a crash between the
    # mkdir and the cleanup would have left behind as a real-looking profile.
    monkeypatch.setattr(config, "PROFILES_DIRECTORY", tmp_path / "profiles")
    present = config.PROFILES_DIRECTORY / "d25_unparsable_probe"
    present.mkdir(parents=True, exist_ok=True)
    (present / "profile.md").write_text("+++\nthis is not valid toml at all\n", encoding="utf-8")
    malformed = _call(app, "personalities.load", {"name": "d25_unparsable_probe"})

    assert "result" not in malformed, malformed
    assert malformed["error"] == missing["error"], "a present-but-broken profile is distinguishable from an absent one"


# Every reference to a profiles root outside config.py, frozen. An allow-list, because the
# previous version of this test matched only `root / name` -- one join FORM -- and a
# reviewer bypassed it in seconds with .joinpath() and os.path.join(). Counting every
# mention instead means a new path builder cannot arrive in any spelling without turning
# this red, whatever syntax it uses. config.py is exempt: it owns the resolver.
PERMITTED_PROFILE_ROOT_REFERENCES = {
    "app_lifecycle.py": 1,
    "personality.py": 7,
    "profile_store.py": 2,
    "tool_spaces.py": 3,
    "tools/core_tools.py": 1,
}


def test_nothing_outside_the_resolver_gains_a_new_profiles_root_reference() -> None:
    """A fifth path builder must not be able to arrive unnoticed, in any spelling.

    The validation lives inside Config.resolve_profile_dir so every caller inherits it,
    which only holds while the resolver stays the only place a caller-supplied name meets
    a profiles root. This does not try to recognise a join -- that is a deny-list, and it
    was defeated by .joinpath() and os.path.join() when it was written that way. It counts
    MENTIONS of the roots instead, so any new one fails regardless of syntax.

    A red result is not automatically a defect: it means someone touched a profiles root
    outside the resolver and a human should decide whether that new reference builds a
    path from a caller-supplied name. Update the count when the answer is no.
    """
    import reachy_language_tutor as package

    source_root = Path(package.__file__).parent
    # Every spelling config.py exports for these roots, not the two that came to mind.
    # Measured: with only the first two, a builder written as
    # `config.INSTANCE_PATH / USER_PERSONALITIES_DIRNAME / n` or as
    # `TERMINAL_USER_PERSONALITIES_DIRECTORY / n` slipped past -- both live idioms in
    # config.py itself. Inverting the SYNTAX check was not enough while the list of
    # root NAMES under it stayed a deny-list.
    roots = (
        "PROFILES_DIRECTORY",
        "DEFAULT_PROFILES_DIRECTORY",
        "user_personalities_root",
        "USER_PERSONALITIES_DIRNAME",
        "TERMINAL_USER_PERSONALITIES_DIRECTORY",
    )
    found: dict[str, int] = {}
    for module in sorted(source_root.rglob("*.py")):
        if module.name == "config.py":
            continue
        hits = sum(
            1
            for line in module.read_text(encoding="utf-8").splitlines()
            if not line.strip().startswith("#") and any(root in line for root in roots)
        )
        if hits:
            found[str(module.relative_to(source_root))] = hits

    assert found == PERMITTED_PROFILE_ROOT_REFERENCES


# --- the allow-list, proven on its own terms -----------------------------------------------
#
# These names stay INSIDE the profiles root, so the containment guard accepts them and the
# character allow-list is the only thing that can refuse them. Without a case like this the
# allow-list is untestable: a reviewer deleted it entirely and the suite stayed green,
# because every traversal shape was being caught downstream by containment instead.
INSIDE_THE_ROOT_BUT_NOT_A_BARE_NAME = [
    # Branch B -- the bare-name else-branch.
    "my profile",
    "guide.md",
    "guide\u0661",
    "guide!",
    "",
    # Branch A -- the user_personalities tail. These were missing, so _permitted_segment(tail)
    # had no read-path proof at all: the four escape shapes are absorbed by containment, and
    # the only thing left failing was a WRITE-path test in another file that would silently
    # unprove this one if it were ever deleted.
    "user_personalities/my profile",
    "user_personalities/guide.md",
    "user_personalities/guide!",
]


@pytest.mark.parametrize("name", INSIDE_THE_ROOT_BUT_NOT_A_BARE_NAME)
def test_the_character_allow_list_refuses_a_name_containment_would_accept(name: str) -> None:
    """Pin the allow-list itself, not the guard standing behind it."""
    from reachy_language_tutor.config import ProfileNameError

    # Precondition: this would land inside the root, so containment is not what refuses it.
    if name:
        assert (config.PROFILES_DIRECTORY / name).resolve().is_relative_to(config.PROFILES_DIRECTORY.resolve())

    with pytest.raises(ProfileNameError):
        config.resolve_profile_dir(name)


def test_a_symlink_inside_the_root_cannot_be_read_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The escape the character allow-list cannot see, and why there are two guards.

    A symlink planted inside the profiles root is reached by a name that is entirely
    legitimate -- "evil" contains nothing a character rule could object to -- so only
    resolving the path and checking containment catches it. Measured before that check
    existed: this returned the target's profile.md in full.

    Lower severity than the name-based escape, and worth saying so rather than inflating
    it: planting the symlink needs local write access to the root, and anyone holding
    that can read the target directly. Closed as defence in depth, not because it is
    remotely reachable.

    This test was lost once already -- a careless whole-file rewrite truncated it, and a
    revert-proof was then reported against a test that no longer existed. That is the
    reason the suite total is asserted nowhere and each guard is revert-proved by name.
    """
    from reachy_language_tutor.config import ProfileNameError

    monkeypatch.setattr(config, "INSTANCE_PATH", tmp_path)
    outside = _canary_outside(tmp_path)
    root = config.user_personalities_root()
    root.mkdir(parents=True, exist_ok=True)
    (root / "evil").symlink_to(outside)

    # The name itself is unimpeachable; it is where it POINTS that is the problem, which
    # is exactly what the character allow-list cannot detect.
    assert re.fullmatch(r"[A-Za-z0-9_-]+", "evil")

    with pytest.raises(ProfileNameError):
        config.resolve_profile_dir("user_personalities/evil")

    # A real directory beside it in the same root still resolves.
    (root / "guide").mkdir()
    assert config.resolve_profile_dir("user_personalities/guide") == root / "guide"


# ------------------------------------------------ what the surface SENDS, not only accepts
#
# The method allow-list governs what a caller may ask for. Everything below is about what
# goes back out: notifications, which reach every attached socket whether it calls a method
# or not, and error responses, which reach whoever asked. Both are read by anyone on the
# household network, so both are allow-listed.


def _next_notification(ws: Any) -> dict[str, Any]:
    """Receive until a notification arrives, skipping responses."""
    while True:
        message = ws.receive_json()
        if "method" in message:
            return message


def _marker(stream: Any) -> None:
    """Send a notification that is always permitted, so a withheld one cannot hang a test.

    Broadcasts are scheduled in order on the server's loop, so if the notification sent
    before this one had been delivered it would arrive first.
    """
    stream._rpc.broadcast_threadsafe("conversation.activity", {"reason": "marker"})


def _connected(app: FastAPI) -> Any:
    ws = TestClient(app).websocket_connect("/rpc")
    return ws


def test_a_network_peer_does_not_receive_the_transcript_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exposure this was written for: a peer that calls nothing received the learner's words.

    Measured before the outgoing allow-list: a client dialling the robot's LAN address with
    no Origin and no credential received {"role": "user", "text": "I'm Zerelda, I'm seven"}.
    """
    from reachy_language_tutor.console import DEV_BROADCAST_TRANSCRIPT_ENV

    monkeypatch.delenv(DEV_BROADCAST_TRANSCRIPT_ENV, raising=False)
    app, stream = _stream(tmp_path, monkeypatch)

    with _connected(app) as ws:
        ws.send_json({"jsonrpc": "2.0", "id": "1", "method": "conversation.status", "params": {}})
        ws.receive_json()  # the peer is now attached, so a broadcast would reach it
        stream._dispatch_transcript("user", "I'm Zerelda, I'm seven", True)
        _marker(stream)
        first = _next_notification(ws)

    assert first["method"] == "conversation.activity", f"a transcript reached the network: {first}"
    assert first["params"] == {"reason": "marker"}


@pytest.mark.parametrize("value", ["true", "yes", "on", "0", "", "11", "1 please"])
def test_only_the_exact_opt_in_value_broadcasts_the_transcript(
    value: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An allow-list of one value: anything a parser might guess at is off."""
    from reachy_language_tutor.console import DEV_BROADCAST_TRANSCRIPT_ENV

    monkeypatch.setenv(DEV_BROADCAST_TRANSCRIPT_ENV, value)
    app, stream = _stream(tmp_path, monkeypatch)

    with _connected(app) as ws:
        ws.send_json({"jsonrpc": "2.0", "id": "1", "method": "conversation.status", "params": {}})
        ws.receive_json()
        stream._dispatch_transcript("assistant", "Hola Zerelda", True)
        _marker(stream)
        first = _next_notification(ws)

    assert first["method"] == "conversation.activity", f"{value!r} switched transcript broadcasting on"


def test_the_developer_opt_in_does_broadcast_the_transcript(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The opt-in is real, so manual-test-script.md's watcher still works when asked for."""
    from reachy_language_tutor.console import DEV_BROADCAST_TRANSCRIPT_ENV

    monkeypatch.setenv(DEV_BROADCAST_TRANSCRIPT_ENV, "1")
    app, stream = _stream(tmp_path, monkeypatch)

    with _connected(app) as ws:
        ws.send_json({"jsonrpc": "2.0", "id": "1", "method": "conversation.status", "params": {}})
        ws.receive_json()
        stream._dispatch_transcript("assistant", "Hola", True)
        first = _next_notification(ws)

    assert first == {
        "jsonrpc": "2.0",
        "method": "conversation.transcript",
        "params": {"role": "assistant", "text": "Hola", "final": True},
    }


@pytest.mark.parametrize(
    ("method", "params"),
    [
        ("conversation.activity", {"reason": "Zerelda said hello"}),  # free text in a permitted param
        ("conversation.turn", {"state": "listening", "learner": "zerelda"}),  # a param nobody listed
        ("conversation.level", {"role": "Zerelda", "rms": 0.5}),  # a value the check refuses
        ("conversation.learner", {"name": "zerelda"}),  # a notification nobody listed
    ],
)
def test_a_notification_outside_the_outgoing_allow_list_is_withheld(
    method: str, params: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Names what may be SENT, so a broadcast added later is withheld until someone decides."""
    app, stream = _stream(tmp_path, monkeypatch)

    with _connected(app) as ws:
        ws.send_json({"jsonrpc": "2.0", "id": "1", "method": "conversation.status", "params": {}})
        ws.receive_json()
        stream._rpc.broadcast_threadsafe(method, params)
        _marker(stream)
        first = _next_notification(ws)

    assert first["params"] == {"reason": "marker"}, f"{method} {params!r} reached the network"


def test_every_broadcast_site_still_reaches_the_dashboard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The allow-list must not silence what the orb and mobile clients actually use.

    One call per broadcast site in console.py other than the transcript: activity (and the
    turn it implies), level, phase, and the interrupt's turn. A site whose params the
    allow-list refused would fail here rather than go quiet in somebody's living room.
    """
    import numpy as np

    app, stream = _stream(tmp_path, monkeypatch)

    with _connected(app) as ws:
        ws.send_json({"jsonrpc": "2.0", "id": "1", "method": "conversation.status", "params": {}})
        ws.receive_json()
        stream._dispatch_activity("user_speech_started")
        stream._emit_level("user", np.full(160, 8000, dtype=np.int16))
        stream._emit_phase("running")
        stream._emit_phase("stopped", "backend_unavailable")
        received = [_next_notification(ws) for _ in range(5)]

    assert [m["method"] for m in received] == [
        "conversation.activity",
        "conversation.turn",
        "conversation.level",
        "conversation.phase",
        "conversation.phase",
    ]
    assert received[0]["params"] == {"reason": "user_speech_started"}
    assert received[1]["params"] == {"state": "listening"}
    assert received[2]["params"]["role"] == "user" and 0.0 <= received[2]["params"]["rms"] <= 1.0
    assert received[3]["params"] == {"phase": "running", "reason": None}
    assert received[4]["params"] == {"phase": "stopped", "reason": "backend_unavailable"}


def _bare_network_server(handlers: dict[str, Any]) -> FastAPI:
    """Build the network server on its own, with test handlers under exposed method names."""
    from reachy_language_tutor.console import _NetworkRestrictedRpcServer, _mount_rpc_with_origin_check

    rpc = _NetworkRestrictedRpcServer()
    for name, handler in handlers.items():
        rpc.register(name, handler)
    app = FastAPI()
    _mount_rpc_with_origin_check(rpc, app)
    return app


def test_a_failing_method_answers_with_a_reason_and_no_exception_text() -> None:
    """One wrapper at registration, so no route can hand an exception's text to the network.

    Measured before it: the SDK sends str(exc) for any exception that is not a
    JsonRpcError, and a JsonRpcError's own message and data went out verbatim.
    """
    from reachy_mini.io.jsonrpc import JsonRpcError

    def _raises_os_error(_params: dict[str, Any]) -> Any:
        raise PermissionError(13, "Permission denied", "/home/pollen/smith-household/instance/memory.v1.json")

    def _raises_rpc_error(_params: dict[str, Any]) -> Any:
        raise JsonRpcError("no learner called 'Zerelda'", reason="invalid_params", code=-32602, data={"detail": "Zerelda"})

    app = _bare_network_server({"conversation.status": _raises_os_error, "conversation.say": _raises_rpc_error})

    internal = _call(app, "conversation.status")
    refused = _call(app, "conversation.say")

    assert internal["error"] == {"code": -32603, "message": "internal_error", "data": {"reason": "internal_error"}}
    assert refused["error"] == {"code": -32602, "message": "invalid_params", "data": {"reason": "invalid_params"}}


def test_an_overlong_profile_name_is_answered_without_a_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ENAMETOOLONG is an OSError but not a FileNotFoundError, and it escaped _load_profile.

    Measured before: `[Errno 63] File name too long: '<profiles dir>/<name>/profile.md'`.
    """
    import json

    app, _s = _stream(tmp_path, monkeypatch)

    answer = _call(app, "personalities.load", {"name": "a" * 300})

    assert "profile.md" not in json.dumps(answer) and "Errno" not in json.dumps(answer), answer
    assert answer["error"]["data"]["reason"] == "profile_unavailable"


def test_a_corrupt_tool_space_manifest_is_answered_without_a_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """tool_spaces.list is exposed, and its error detail was the exception's str()."""
    import json

    from reachy_language_tutor.tool_spaces import get_installed_tool_spaces_path

    get_installed_tool_spaces_path(tmp_path).write_text("{not json", encoding="utf-8")
    app, _s = _stream(tmp_path, monkeypatch)

    answer = _call(app, "tool_spaces.list")

    assert answer["error"]["data"]["reason"] == "tool_spaces_unavailable"
    assert str(tmp_path) not in json.dumps(answer), "the instance directory reached the network"


def test_the_backend_error_in_status_is_rendered_like_a_log_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The log line beside it said PermissionError(errno=13); the network got the path."""
    app, stream = _stream(tmp_path, monkeypatch)
    stream._set_backend_connection_state(
        "disconnected", PermissionError(13, "Permission denied", "/home/pollen/smith-household/instance/token")
    )

    status = _call(app, "conversation.status")["result"]

    assert status["backend_error"] == "PermissionError(errno=13)"
