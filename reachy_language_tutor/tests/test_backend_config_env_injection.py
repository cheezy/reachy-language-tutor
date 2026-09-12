"""The backend.config RPC is a writer, so what it accepts decides what reaches the .env.

These live in their own file on purpose. The obvious home is test_console.py, but that
file is one of seven excluded from the suite by `addopts` in pyproject.toml -- it carries
upstream profile-switching tests that fail by design under LOCKED_PROFILE. A security
test written there passes when run by hand and never runs in
`pytest reachy_language_tutor -q`, which is the command the task's verification step and
the after_doing hook both use. That is a green tick meaning nothing, so the tests that
pin this fix are kept where they are actually collected.
"""

import os
import json
from types import SimpleNamespace
from typing import Any
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from reachy_language_tutor.config import config
from reachy_language_tutor.console import LocalStream


def _rpc_call(app: FastAPI, method: str, params: Any = None) -> dict[str, Any]:
    """Send one JSON-RPC request over /rpc and return the response envelope."""
    with TestClient(app).websocket_connect("/rpc") as ws:
        ws.send_json({"jsonrpc": "2.0", "id": "1", "method": method, "params": params or {}})
        return ws.receive_json()


def _backend_config_stream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[FastAPI, Any]:
    """A settings app wired for backend.config calls, with the direct-mode config cleared."""
    monkeypatch.setattr(config, "HF_REALTIME_CONNECTION_MODE", "deployed")
    monkeypatch.setattr(config, "HF_REALTIME_SESSION_URL", None)
    monkeypatch.setattr(config, "HF_REALTIME_WS_URL", None)

    app = FastAPI()
    robot = SimpleNamespace(media=SimpleNamespace(audio=None, backend=None))
    stream = LocalStream(MagicMock(), robot, settings_app=app, instance_path=str(tmp_path))
    stream._init_settings_ui_if_needed()
    return app, stream


# Values that must never reach the instance .env. The first is the defect's own shape --
# a host whose newline turns one NAME=value into two lines, the second of which
# load_dotenv reads back as configuration on every later start.
_HOSTS_THAT_ARE_NOT_HOSTS: tuple[str, ...] = (
    "evil\nHF_REALTIME_WS_URL=ws://attacker.example",
    "evil\rHF_REALTIME_WS_URL=ws://attacker.example",
    "localhost\nINJECTED=1",
    "host with space",
    "http://localhost",
    "localhost/path",
    "localhost?x=1",
    "localhost#frag",
    "'; rm -rf /",
)


@pytest.mark.parametrize("host", _HOSTS_THAT_ARE_NOT_HOSTS)
def test_backend_config_refuses_a_host_that_is_not_a_host(
    host: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rule this replaced was a deny-list of four URL characters, so it admitted \\n.

    _persist_env_values writes `NAME=value` and joins the lines on newlines, so an embedded
    one became extra .env lines that were never validated as a NAME=value pair. Asserting
    the refusal alone would not catch a regression that refuses and writes anyway, so this
    also asserts the file is untouched.
    """
    app, _stream = _backend_config_stream(tmp_path, monkeypatch)

    resp = _rpc_call(
        app,
        "backend.config",
        {"backend": "huggingface", "hf_mode": "local", "hf_host": host, "hf_port": 8765},
    )

    assert resp["error"]["data"]["reason"] == "invalid_hf_host"
    env_path = tmp_path / ".env"
    written = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    assert "attacker.example" not in written
    assert "INJECTED" not in written


@pytest.mark.parametrize(
    "host", ["localhost", "127.0.0.1", "example.com", "my-host.local", "hf_server", "[::1]", "::1", "fe80::1"]
)
def test_backend_config_still_accepts_a_real_host(
    host: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Switching to an allow-list must not refuse the hosts people actually configure.

    This is the half such a change can silently break: a rule that refuses everything would
    pass the test above and leave the app unable to save a working connection. Underscore
    and the bracketed IPv6 literal are here because the old check allowed them and nothing
    about the injection this fixes requires refusing them.
    """
    app, _stream = _backend_config_stream(tmp_path, monkeypatch)

    resp = _rpc_call(
        app,
        "backend.config",
        {"backend": "huggingface", "hf_mode": "local", "hf_host": host, "hf_port": 8765},
    )

    assert "error" not in resp, f"a legitimate host was refused: {resp.get('error')}"
    assert resp["result"]["ok"] is True


def test_persist_env_values_refuses_a_line_break_whatever_the_caller(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard lives at the sink, so a future caller that forgets to validate still fails.

    The hf_host allow-list is today's only attacker-reachable path here, but the sink is
    what makes the property hold for the next writer. It raises rather than dropping the
    value, so the mistake surfaces where it is made, and it sits above the try/except that
    would otherwise downgrade it to a logged warning -- a silently-swallowed guard would
    read as a pass here while writing the file anyway.
    """
    _app, stream = _backend_config_stream(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="line break"):
        stream._persist_env_values({"HF_REALTIME_WS_URL": "ws://ok.example:1/\nINJECTED=1"})
    with pytest.raises(ValueError, match="line break"):
        stream._persist_env_values({"HF_REALTIME_WS_URL": "ws://ok.example:1/\rINJECTED=1"})

    env_path = tmp_path / ".env"
    written = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    assert "INJECTED" not in written
    assert "INJECTED" not in os.environ.get("HF_REALTIME_WS_URL", "")

    # The same sink still persists an ordinary value, so the guard is not refusing everything.
    stream._persist_env_values({"HF_REALTIME_WS_URL": "ws://ok.example:1/v1/realtime"})
    assert "ws://ok.example:1/v1/realtime" in (tmp_path / ".env").read_text(encoding="utf-8")


# Everything str.splitlines() treats as a line terminator. _read_env_lines parses the .env
# with splitlines(), so each of these is a line break as far as the READER is concerned even
# though none of them is CR or LF.
_SPLITLINES_TERMINATORS: tuple[str, ...] = (
    "\x0b",
    "\x0c",
    "\x1c",
    "\x1d",
    "\x1e",
    "\x85",
    "\u2028",
    "\u2029",
)


@pytest.mark.parametrize("terminator", _SPLITLINES_TERMINATORS)
def test_persist_env_values_refuses_every_break_its_own_reader_splits_on(
    terminator: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A guard that means something narrower than its reader is not a guard.

    The first version of this tested for CR and LF. _read_env_lines parses the file with
    str.splitlines(), which splits on eight more characters (the tuple above), so a value carrying one of those
    passed the guard and was written inside a single physical line -- and then the next call
    that re-read the file split it apart and wrote it back joined on newlines, turning the
    second half into a real .env entry that load_dotenv read on every later start. The write
    looked innocent; the damage landed one call later. Testing what splitlines() actually
    does is the only version that cannot drift from the parser it protects.
    """
    _app, stream = _backend_config_stream(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="line break"):
        stream._persist_env_values(
            {"HF_REALTIME_WS_URL": f"ws://ok.example:1/{terminator}HF_REALTIME_WS_URL=ws://attacker.example"}
        )

    # The damage was never in the first write, so re-reading is what the assertion has to do:
    # persist something legitimate and confirm no second entry appeared.
    stream._persist_env_values({"HF_REALTIME_WS_URL": "ws://ok.example:1/v1/realtime"})
    written = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "attacker.example" not in written
    assert len([ln for ln in written.splitlines() if ln.startswith("HF_REALTIME_WS_URL=")]) == 1


@pytest.mark.parametrize("bad_port", ["8765; rm -rf /", "abc", "8765\nINJECTED=1", "1e400"])
def test_backend_config_refuses_a_bad_port_without_echoing_it(
    bad_port: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected value must not come back to the caller, and bad input is not an internal error.

    int(raw_port) raises with the caller's own value inside the message. Uncaught, that
    escaped into the generic JSON-RPC error path, so a plain bad port was reported as
    internal_error AND reflected the value back -- the one thing this task's security notes
    said not to do. The port is coerced in the same handler the host allow-list guards, so
    it gets the same treatment: a fixed message and the validated reason code.
    """
    app, _stream = _backend_config_stream(tmp_path, monkeypatch)

    resp = _rpc_call(
        app,
        "backend.config",
        {"backend": "huggingface", "hf_mode": "local", "hf_host": "localhost", "hf_port": bad_port},
    )

    assert resp["error"]["data"]["reason"] == "invalid_hf_port"
    assert bad_port not in json.dumps(resp), "the rejected value was echoed back to the caller"
    assert not (tmp_path / ".env").exists()


def test_a_valid_host_is_persisted_and_surrounding_whitespace_is_trimmed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The accept path has to actually write, or "still accepts" proves only that it did not error.

    Also covers the trailing/leading-whitespace edge case: .strip() runs before the allow-list,
    so a host typed with a stray space is a valid host rather than a refusal, and what lands in
    the .env is the trimmed form.
    """
    app, _stream = _backend_config_stream(tmp_path, monkeypatch)

    resp = _rpc_call(
        app,
        "backend.config",
        {"backend": "huggingface", "hf_mode": "local", "hf_host": "  localhost  ", "hf_port": 8765},
    )

    assert resp["result"]["ok"] is True
    written = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "ws://localhost:8765/v1/realtime" in written
    assert "  localhost" not in written


def test_an_empty_host_falls_back_to_the_configured_one_and_is_refused_when_there_is_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty means "leave it alone", not "write an empty host" -- the task's second edge case.

    With a direct URL already configured the call keeps that host; with nothing configured
    there is nothing to fall back to, and the pre-existing empty_hf_host refusal applies. The
    allow-list must not have turned the first case into a refusal, which is what would happen
    if it were applied before the fallback.
    """
    app, _stream = _backend_config_stream(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "HF_REALTIME_WS_URL", "ws://existing.example:9999/v1/realtime")

    resp = _rpc_call(
        app, "backend.config", {"backend": "huggingface", "hf_mode": "local", "hf_port": 8765}
    )
    assert resp["result"]["ok"] is True
    assert "ws://existing.example:8765/v1/realtime" in (tmp_path / ".env").read_text(encoding="utf-8")

    monkeypatch.setattr(config, "HF_REALTIME_WS_URL", None)
    app2, _s2 = _backend_config_stream(tmp_path, monkeypatch)
    resp2 = _rpc_call(
        app2, "backend.config", {"backend": "huggingface", "hf_mode": "local", "hf_port": 8765}
    )
    assert resp2["error"]["data"]["reason"] == "empty_hf_host"


def test_an_ipv6_host_survives_the_round_trip_back_through_the_parser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Accepting a host on the way in is worth nothing if it is refused on the way back.

    build_hf_direct_ws_url stores the bracketed form, but parse_hf_direct_target reads the
    host back with urlsplit().hostname, which strips the brackets. An allow-list that knew
    only the bracketed spelling accepted [::1], persisted it, and then refused the very same
    address the next time the settings form submitted or omitted the host -- locking an
    IPv6-configured instance out of its own settings. The two spellings are one address, so
    the rule has to agree with the helper that produces the second one.
    """
    app, _stream = _backend_config_stream(tmp_path, monkeypatch)

    first = _rpc_call(
        app,
        "backend.config",
        {"backend": "huggingface", "hf_mode": "local", "hf_host": "[::1]", "hf_port": 8765},
    )
    assert first["result"]["ok"] is True
    assert "ws://[::1]:8765/v1/realtime" in (tmp_path / ".env").read_text(encoding="utf-8")

    # What the parser hands back is the unbracketed form -- the exact value that used to be
    # refused. Feed it in as the settings form would.
    from reachy_language_tutor.config import parse_hf_direct_target

    read_back_host, read_back_port = parse_hf_direct_target("ws://[::1]:8765/v1/realtime")
    assert read_back_host == "::1", "precondition: urlsplit strips the brackets"

    second = _rpc_call(
        app,
        "backend.config",
        {"backend": "huggingface", "hf_mode": "local", "hf_host": read_back_host, "hf_port": read_back_port},
    )
    assert "error" not in second, f"the host we just persisted was refused on read-back: {second.get('error')}"
    assert second["result"]["ok"] is True

    # Accepting it is only half the round trip. The value the second call PERSISTED has to
    # be a URL the parser can still read -- an unbracketed IPv6 host interpolated straight
    # into ws://host:port/ yields ws://::1:8765/v1/realtime, which parses back to
    # (None, None) and would leave the instance pointing at a URL nothing can use. Asserting
    # only ok:True was green over exactly that.
    persisted = [
        ln.split("=", 1)[1]
        for ln in (tmp_path / ".env").read_text(encoding="utf-8").splitlines()
        if ln.startswith("HF_REALTIME_WS_URL=")
    ]
    assert persisted == ["ws://[::1]:8765/v1/realtime"], f"second call persisted {persisted}"
    assert parse_hf_direct_target(persisted[0]) == ("::1", 8765), "the persisted URL no longer round-trips"
