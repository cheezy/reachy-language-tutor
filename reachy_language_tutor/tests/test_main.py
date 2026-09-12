"""Tests for app-level runtime behavior."""

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import reachy_language_tutor.main as main_mod


def test_inactivity_timeout_thread_goes_to_sleep() -> None:
    """The watchdog should use the shared sleep shutdown path once activity is too old."""
    stream_manager = SimpleNamespace(seconds_since_activity=lambda: 10.0, close=MagicMock())
    go_to_sleep = MagicMock(return_value={"status": "sleeping"})

    thread = main_mod._start_inactivity_timeout_thread(
        timeout_minutes=0.0001,
        stream_manager=stream_manager,
        logger=MagicMock(),
        app_stop_event=threading.Event(),
        go_to_sleep=go_to_sleep,
    )

    thread.join(timeout=1.0)
    assert not thread.is_alive()
    go_to_sleep.assert_called_once_with()
    stream_manager.close.assert_not_called()


def test_inactivity_timeout_thread_closes_stream_manager_without_sleep_callback() -> None:
    """The watchdog should still close the stream when no sleep callback is available."""
    stream_manager = SimpleNamespace(seconds_since_activity=lambda: 10.0, close=MagicMock())

    thread = main_mod._start_inactivity_timeout_thread(
        timeout_minutes=0.0001,
        stream_manager=stream_manager,
        logger=MagicMock(),
        app_stop_event=threading.Event(),
    )

    thread.join(timeout=1.0)
    assert not thread.is_alive()
    stream_manager.close.assert_called_once_with()


def test_the_ui_server_binds_loopback_and_the_readme_says_so() -> None:
    """The --ui server carries the JSON-RPC control surface, so who can reach it matters.

    console.py mounts /rpc on this same app, and its methods make the robot speak, mute or
    UNMUTE the microphone, and REWRITE the speech backend's host and port. The README has
    always documented 127.0.0.1 and this server bound every interface; both halves are
    asserted here, because a guarantee a document makes and the code does not keep is
    worse than no guarantee.

    SCOPE, stated because this test is narrower than it looks: it covers ONLY the --ui
    development server, which is guarded by `settings_app is None` and therefore does not
    run on a robot. When the SDK launches the app it supplies settings_app itself and binds
    it from ReachyLanguageTutor.custom_app_url. That path is loopback too, and is pinned by
    test_the_address_the_sdk_binds_on_a_robot_is_loopback below -- by a different assertion,
    because it is a different server reading a different value.

    The declared integration checks (a /rpc call refused from a non-loopback address, and
    one from loopback still working) are NOT automated here: both need a second address on
    a running server, and a test that binds a real port and dials the host's LAN address is
    a flaky test on any machine whose network changes. That is a stated gap, not a covered
    one.
    """
    import re
    from ipaddress import ip_address
    from pathlib import Path
    from urllib.parse import urlparse

    assert ip_address(main_mod.UI_BIND_HOST).is_loopback, main_mod.UI_BIND_HOST

    source = Path(main_mod.__file__).resolve().read_text(encoding="utf-8")
    ui_server = source[source.index("if args.ui and settings_app is None and effective_settings_app") :]
    ui_server = ui_server[: ui_server.index("threading.Thread")]
    assert "UI_BIND_HOST" in ui_server, "the --ui server stopped using the constant this test pins"
    assert "0.0.0.0" not in ui_server, f"the --ui server binds every interface again: {ui_server}"

    # Every document a reader might reach for, not just the retired one: the guarantee this
    # is about is the one the docs make, so a doc that drifts is the defect coming back.
    #
    # Only lines documenting a URL are checked. "the port is 7860" and "Port 8000 or 7860
    # already in use" name the port without promising an address, and demanding loopback of
    # those was this test's first draft failing on prose it had no business policing.
    root = Path(main_mod.__file__).resolve().parents[3]
    documented_urls = [
        (doc.name, url)
        for doc in root.rglob("*.md")
        # Skip every dotted directory: .stride holds review reports that quote the old
        # address verbatim, and .git and the caches are not documentation either.
        if not any(part.startswith(".") for part in doc.parts)
        for url in re.findall(r"https?://[A-Za-z0-9_.:-]+:7860", doc.read_text(encoding="utf-8"))
    ]
    assert len(documented_urls) >= 5, f"only {len(documented_urls)} documented 7860 URLs found, so this proves little"
    for name, url in documented_urls:
        assert urlparse(url).hostname == main_mod.UI_BIND_HOST, f"{name} documents {url}, which the app does not bind"


def test_the_address_the_sdk_binds_on_a_robot_is_loopback() -> None:
    """This is the path a deployed Reachy Mini actually uses, and it is text, not code.

    When the daemon launches the app the SDK urlparses ReachyLanguageTutor.custom_app_url
    and binds uvicorn to its hostname, and console.py mounts the JSON-RPC surface onto that
    same server -- the methods that make the robot speak, unmute its microphone and rewrite
    the speech backend's host and port, none of them authenticated.

    Both halves are asserted. The attribute has to be loopback, and the FIRST
    `custom_app_url = "..."` match in the file has to be that same value, because the SDK
    extracts it from this source with a first-match regex rather than by importing the
    module -- so a stray assignment written above would silently become the address the
    robot binds, without anything else in this suite noticing.
    """
    import re
    from ipaddress import ip_address
    from pathlib import Path
    from urllib.parse import urlparse

    from reachy_language_tutor.main import ReachyLanguageTutor

    declared = ReachyLanguageTutor.custom_app_url
    assert declared is not None
    host = urlparse(declared).hostname
    assert host is not None and ip_address(host).is_loopback, declared

    source = Path(main_mod.__file__).resolve().read_text(encoding="utf-8")
    first = re.search(r'custom_app_url\s*(?::\s*[^=]+)?\s*=\s*["\']([^"\']+)["\']', source)
    assert first is not None, "nothing in main.py matches the shape the SDK extracts"
    assert first.group(1) == declared, (
        f"the SDK would read {first.group(1)!r} out of this file, not {declared!r} -- "
        "an earlier assignment is shadowing the real one"
    )
