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

    console.py mounts /rpc on this same app. Its methods make the robot speak; since D20 the
    mic is read-only and every writer is refused outright, but this dev server still has
    no reason to listen beyond this machine. The README has
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


def test_the_address_the_sdk_binds_on_a_robot_is_lan_reachable() -> None:
    """This is the path a deployed Reachy Mini actually uses, and it is text, not code.

    When the daemon launches the app the SDK urlparses ReachyLanguageTutor.custom_app_url
    and binds uvicorn to its hostname. It MUST be LAN-reachable: the desktop dashboard
    discards this host and loads http://<robot-lan-ip>:7860/, then HEAD-polls the same URL
    and calls stopCurrentApp after 60s of failure. D15 set it to loopback and would
    therefore have killed the app about a minute after start on every Wireless unit; D20
    restored it. This test is the one that would have caught that, so it asserts the
    property that actually matters rather than the one that reads safer.

    The exposure this creates is answered by what the reachable methods may DO -- the mic
    is read-only and every writer is refused outright -- not by the bind address. See
    docs/rpc-control-surface.md.

    Both halves are asserted. The attribute has to be LAN-reachable, and the FIRST
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
    assert host is not None, declared
    assert not ip_address(host).is_loopback, (
        f"custom_app_url is {declared!r}: a loopback bind is refused by the dashboard, whose "
        "liveness probe then stops the app after 60s on a Wireless unit (D15/D20)"
    )

    source = Path(main_mod.__file__).resolve().read_text(encoding="utf-8")
    first = re.search(r'custom_app_url\s*(?::\s*[^=]+)?\s*=\s*["\']([^"\']+)["\']', source)
    assert first is not None, "nothing in main.py matches the shape the SDK extracts"
    assert first.group(1) == declared, (
        f"the SDK would read {first.group(1)!r} out of this file, not {declared!r} -- "
        "an earlier assignment is shadowing the real one"
    )


class _AppMustNotStart:
    """Stands in for the robot app in the entry-point tests; constructing it is the failure."""

    def __init__(self) -> None:
        raise AssertionError("the robot app was started in answer to an operator command")


def test_an_operator_command_run_as_a_module_runs_and_never_starts_the_app(tmp_path, monkeypatch, capsys) -> None:
    """`python -m reachy_language_tutor.main enrol ...` must run enrol, not the robot.

    It did not. The module's __main__ block went straight to wrapped_run(), so the
    subcommand was parsed, ignored, and the app started instead -- camera, microphone,
    a live voice session and a server on every interface. Measured before this fix:
    `python -m reachy_language_tutor.main enrol --serve-when-unrecognised sample-learner`
    logged "Serving nobody: no_face" off a real camera frame and held a voice session
    until it was killed. The same path turned `enrol --forget-everything ID`, an erasure
    request, into a capture.

    The oracle is the setting on disk, not an exit code: the command's whole effect is
    that file, so its presence proves enrol ran, and _AppMustNotStart proves nothing else did.
    """
    import sys

    from reachy_language_tutor.learners.store import ensure_learner_database
    from reachy_language_tutor.startup_settings import read_startup_settings

    assert ensure_learner_database(tmp_path).ready
    monkeypatch.setattr(main_mod, "ReachyLanguageTutor", _AppMustNotStart)
    monkeypatch.setattr(
        sys,
        "argv",
        ["main.py", "enrol", "--serve-when-unrecognised", "sample-learner", "--instance-path", str(tmp_path)],
    )

    import pytest

    with pytest.raises(SystemExit) as exited:
        main_mod.module_main()

    assert exited.value.code == 0
    assert read_startup_settings(tmp_path).fallback_learner == "sample-learner"
    assert "the app will serve" in capsys.readouterr().out


def test_the_module_with_no_arguments_still_starts_the_app(monkeypatch) -> None:
    """The daemon launches `python -u -m reachy_language_tutor.main` with no arguments.

    That path must keep reaching wrapped_run(), or every robot stops starting the app.
    """
    import sys

    started: list[str] = []

    class _RecordingApp:
        def wrapped_run(self) -> None:
            started.append("wrapped_run")

        def stop(self) -> None:
            started.append("stop")

    monkeypatch.setattr(main_mod, "ReachyLanguageTutor", _RecordingApp)
    monkeypatch.setattr(sys, "argv", ["main.py"])

    main_mod.module_main()

    assert started == ["wrapped_run"]


def test_a_command_main_does_not_dispatch_is_refused_rather_than_starting_the_app(monkeypatch) -> None:
    """A subcommand registered in parse_args but never dispatched must not fall through to run().

    main() used to end in an unconditional run(args), so the only thing keeping an
    operator command from starting the robot was every command being remembered above
    it. Now anything that is a command and is not dispatched is refused by name.
    """
    import argparse

    import pytest

    def _run_must_not_be_called(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("run() was reached for an operator command")

    monkeypatch.setattr(
        main_mod, "parse_args", lambda: (argparse.Namespace(command="a-later-command", debug=False), [])
    )
    monkeypatch.setattr(main_mod, "run", _run_must_not_be_called)

    with pytest.raises(SystemExit) as exited:
        main_mod.main()

    assert "No handler for the 'a-later-command' command" in str(exited.value.code)


def test_the_module_entry_block_is_module_main_and_nothing_else() -> None:
    """The fix only holds if `if __name__ == "__main__":` calls module_main.

    Read from the source, because running the block is running the robot.
    """
    import ast
    from pathlib import Path

    tree = ast.parse(Path(main_mod.__file__).resolve().read_text(encoding="utf-8"))
    blocks = [
        node
        for node in tree.body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
    ]
    assert len(blocks) == 1, f"expected one __main__ block, found {len(blocks)}"
    body = blocks[0].body
    assert len(body) == 1 and isinstance(body[0], ast.Expr), ast.dump(blocks[0])
    call = body[0].value
    assert isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "module_main", (
        f"the __main__ block no longer routes through module_main: {ast.unparse(blocks[0])}"
    )
