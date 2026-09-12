"""What the /rpc control surface lets a caller DO, given it must be reachable on the LAN.

The app's UI port has to be LAN-reachable or the desktop dashboard cannot load the app and
stops it after 60s (D15/D20). Nothing this app can check distinguishes the dashboard's
iframe from anything else on that network: there is no credential the SDK carries to it,
and a token in the page it serves is readable by the same caller it would exclude -- see
docs/rpc-control-surface.md. So the protection is not "only the right caller may act", it
is that the reachable methods cannot do harm. These tests pin that.

In their own file because pyproject's addopts excludes test_console.py, the otherwise
natural home, so a test written there is never collected by `pytest reachy_language_tutor -q`
-- the command the after_doing hook runs. See D21.
"""

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
