"""The .env line-injection defect (D19), pinned at the functions that carry the property.

These were originally driven through the `backend.config` JSON-RPC method, which is where
the tainted value entered. D20 then stopped exposing that method over `/rpc` at all -- the
app's UI port has to be LAN-reachable and no caller on that network can be authenticated,
so every writer is refused (see test_rpc_control_surface.py). The injection property did
not go away with the delivery path: `_persist_env_values` is still the sink every current
and future writer reaches, and `_is_an_hf_host` is still what decides a host, so they are
asserted directly here rather than through a method that no longer answers.

They live here rather than in test_console.py because at the time they were written
pyproject excluded that whole file from collection: these tests passed when run directly
and were silently absent from the suite, which is what made the exclusion visible at all.
D21 removed it, so the reason is historical rather than live -- and this file is the
worked example D21 was filed from, which is reason enough to leave it where it is.
"""

import os
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI

from reachy_language_tutor.config import config, build_hf_direct_ws_url, parse_hf_direct_target
from reachy_language_tutor.console import LocalStream, _is_an_hf_host


def _stream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A LocalStream with an instance path, for exercising the .env sink directly."""
    monkeypatch.setattr(config, "HF_REALTIME_CONNECTION_MODE", "deployed")
    monkeypatch.setattr(config, "HF_REALTIME_SESSION_URL", None)
    monkeypatch.setattr(config, "HF_REALTIME_WS_URL", None)
    app = FastAPI()
    robot = SimpleNamespace(media=SimpleNamespace(audio=None, backend=None))
    stream = LocalStream(MagicMock(), robot, settings_app=app, instance_path=str(tmp_path))
    stream._init_settings_ui_if_needed()
    return stream


# The shapes that must never be accepted as a host. The first two are the defect itself --
# a newline turning one NAME=value into two lines, the second of which load_dotenv reads
# back as configuration on every later start.
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
    "${HF_TOKEN}.attacker.example",
    "..",
    "-",
    "\x0b",
    "\x85",
    " ",
    "a" * 256,
    "fe80::1%eth0",
    ":::::",
)


@pytest.mark.parametrize("host", _HOSTS_THAT_ARE_NOT_HOSTS)
def test_a_host_that_is_not_a_host_is_refused(host: str) -> None:
    """The rule this replaced was a deny-list of four URL characters, so it admitted \\n.

    Naming what is permitted refuses a line break, a space and every shell metacharacter at
    once -- including the ones nobody thought of, which is the whole argument for an
    allow-list over the list of known-bad characters it replaced.
    """
    assert _is_an_hf_host(host) is False


@pytest.mark.parametrize(
    "host", ["localhost", "127.0.0.1", "example.com", "my-host.local", "hf_server", "[::1]", "::1", "fe80::1"]
)
def test_a_real_host_is_still_accepted(host: str) -> None:
    """A rule that refuses everything would pass the test above and be useless.

    Both IPv6 spellings are here because the settings form sends the bracketed literal and
    `parse_hf_direct_target` hands back the bare one; a rule that knew only one of them
    refused an address it had just accepted.
    """
    assert _is_an_hf_host(host) is True


def test_an_ipv6_host_survives_the_round_trip_through_the_url() -> None:
    """Accepting an address is worth nothing if the URL built from it cannot be read back.

    `build_hf_direct_ws_url` interpolates the host, and an unbracketed IPv6 literal yields
    `ws://::1:8765/v1/realtime`, which `parse_hf_direct_target` reads back as (None, None) --
    so the instance would persist a URL neither the settings form nor the connect path could
    use. Bracketing at the point the URL is built is what closes it, for both spellings.
    """
    for host in ("::1", "[::1]"):
        url = build_hf_direct_ws_url(host, 8765)
        assert url == "ws://[::1]:8765/v1/realtime", host
        assert parse_hf_direct_target(url) == ("::1", 8765), host

    # A name is untouched -- the bracketing must not fire on anything but an IPv6 literal.
    assert build_hf_direct_ws_url("localhost", 8765) == "ws://localhost:8765/v1/realtime"
    assert parse_hf_direct_target("ws://localhost:8765/v1/realtime") == ("localhost", 8765)


def test_persist_env_values_refuses_a_line_break_whatever_the_caller(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard lives at the sink, so a future caller that forgets to validate still fails.

    This is the half that outlived the delivery path: `backend.config` is no longer exposed,
    but `_persist_env_values` is still what every writer reaches, and the guard is what makes
    the property hold for the next one. It raises rather than dropping the value, so the
    mistake surfaces where it is made, and it sits above the try/except that would otherwise
    downgrade it to a logged warning -- a silently-swallowed guard would read as a pass here
    while writing the file anyway.
    """
    stream = _stream(tmp_path, monkeypatch)

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
    " ",
    " ",
)


@pytest.mark.parametrize("terminator", _SPLITLINES_TERMINATORS)
def test_persist_env_values_refuses_every_break_its_own_reader_splits_on(
    terminator: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A guard that means something narrower than its reader is not a guard.

    The first version of this tested for CR and LF. _read_env_lines parses the file with
    str.splitlines(), which splits on eight more characters (the tuple above), so a value
    carrying one of those passed the guard and was written inside a single physical line --
    and then the next call that re-read the file split it apart and wrote it back joined on
    newlines, turning the second half into a real .env entry that load_dotenv read on every
    later start. The write looked innocent; the damage landed one call later.
    """
    stream = _stream(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="line break"):
        stream._persist_env_values(
            {"HF_REALTIME_WS_URL": f"ws://ok.example:1/{terminator}HF_REALTIME_WS_URL=ws://attacker.example"}
        )

    # The damage was never in the first write, so re-reading is what the assertion has to do.
    stream._persist_env_values({"HF_REALTIME_WS_URL": "ws://ok.example:1/v1/realtime"})
    written = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "attacker.example" not in written
    assert len([ln for ln in written.splitlines() if ln.startswith("HF_REALTIME_WS_URL=")]) == 1
