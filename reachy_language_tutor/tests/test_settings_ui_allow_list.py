"""The settings UI must offer only what the /rpc surface will actually run.

D20 turned /rpc into an allow-list and refuses every writer over the network. The UI was
never told. Thirteen controls across five files still called refused writers, so a click
produced a JSON-RPC error the page did nothing useful with -- the robot read as broken
rather than as deliberately restricted.

The fix is the one this repository keeps relearning: name what is PERMITTED. The server
sends the allow-list it already enforces; the UI disables whatever is not in it. A second
list of refused method names in JavaScript would be the deny-list shape CLAUDE.md records
four defects against, drifting from console.py the first time a method is added.

There is no JavaScript test runner in this project (no package.json, no jest), so these
read the shipped .js as text. That is weaker than executing it and is worth saying out
loud: they pin the wiring -- that the UI asks the server, and asks about real methods --
not the rendered result. The rendered result is the manual check in the task.
"""

import re
from types import SimpleNamespace
from typing import Any
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI

from reachy_language_tutor.config import config
from reachy_language_tutor.console import LocalStream


STATIC_JS = Path(__file__).resolve().parents[1] / "src" / "reachy_language_tutor" / "static" / "js"

# Where a control lives, as opposed to where the transport lives. api.js names every
# method because it is the client; a VIEW naming one is the thing worth watching.
VIEW_FILES = sorted((STATIC_JS / "views").glob("*.js")) + sorted((STATIC_JS / "components").glob("*.js"))


def _stream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[FastAPI, Any]:
    """Build the real settings surface, the way test_rpc_control_surface.py does."""
    monkeypatch.setattr(config, "HF_REALTIME_CONNECTION_MODE", "deployed")
    monkeypatch.setattr(config, "HF_REALTIME_SESSION_URL", None)
    monkeypatch.setattr(config, "HF_REALTIME_WS_URL", None)
    app = FastAPI()
    robot = SimpleNamespace(media=SimpleNamespace(audio=None, backend=None))
    stream = LocalStream(MagicMock(), robot, settings_app=app, instance_path=str(tmp_path))
    stream._init_settings_ui_if_needed()
    return app, stream


def _status_payload(stream: Any) -> dict[str, Any]:
    """Call the real conversation.status handler and return what a browser would get.

    The handler may be sync or async depending on how it was registered, so both are
    accepted rather than assuming one -- assuming async is what this helper did first,
    and it failed on a dict.
    """
    import asyncio
    import inspect

    result = stream._rpc._methods["conversation.status"]({})
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


def test_the_status_payload_offers_exactly_the_methods_the_registrar_allows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The UI is told what is permitted, and it is the same set the server enforces."""
    from reachy_language_tutor.console import _RPC_METHODS_EXPOSED_ON_THE_NETWORK

    _app, stream = _stream(tmp_path, monkeypatch)
    payload = _status_payload(stream)

    assert "rpc_methods_available" in payload, "the UI has no way to learn what it may call"
    assert set(payload["rpc_methods_available"]) == set(_RPC_METHODS_EXPOSED_ON_THE_NETWORK)


def test_the_payload_is_derived_from_the_allow_list_rather_than_a_copy_of_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point: a second list would drift, and drift silently.

    Proved by moving the allow-list and checking the payload moves with it. A hand-written
    copy in _status_payload would pass the test above on the day it was written and fail
    this one immediately -- which is the failure worth catching, because the copy only
    becomes wrong later, when somebody adds a method and changes one list of two.
    """
    import reachy_language_tutor.console as console

    _app, stream = _stream(tmp_path, monkeypatch)
    monkeypatch.setattr(console, "_RPC_METHODS_EXPOSED_ON_THE_NETWORK", frozenset({"conversation.status"}))

    payload = _status_payload(stream)

    assert payload["rpc_methods_available"] == ["conversation.status"], (
        "the status payload did not follow the allow-list, so it holds a copy rather than deriving it"
    )


def _wrapper_methods() -> dict[str, str]:
    """Map each api.js wrapper to the method it calls, read out of api.js itself.

    Derived rather than listed here, so a wrapper renamed or repointed at a different
    method is followed automatically instead of going stale in a test fixture.
    """
    api = (STATIC_JS / "api.js").read_text(encoding="utf-8")
    pattern = re.compile(r'export const (\w+)\s*=\s*\([^)]*\)\s*=>\s*\n?\s*rpcCall\(\s*"([^"]+)"', re.S)
    found = dict(pattern.findall(api))
    assert found, "could not read any wrapper out of api.js; the parser has gone stale"

    # Completeness, not just non-emptiness. The regex knows one wrapper shape; a wrapper
    # written as `async (x) =>`, as a function declaration, or with a ")" inside its
    # parameter list would be dropped silently, and the gating test below would quietly
    # stop covering it while still passing. Cross-checking against every method literal
    # that appears in an rpcCall() makes that drop loud instead. This is the same "a
    # guard must mean the same thing as the code it protects" rule that the round-one
    # finding was about.
    called = set(re.findall(r'rpcCall\(\s*"([^"]+)"', api))
    missed = called - set(found.values())
    assert not missed, (
        f"api.js calls these methods through a wrapper shape the parser does not recognise: "
        f"{sorted(missed)}. Widen the pattern, or the gating test silently stops covering them."
    )
    return found


def test_every_method_the_ui_asks_about_is_one_the_server_really_has(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A typo in isAvailable() fails silently in both directions, so pin the names.

    `isAvailable("personalities.aply")` is never allow-listed, so the control would be
    disabled forever with no error anywhere; the opposite typo leaves a control enabled
    that cannot work. Neither shows up at runtime. Compared against what the server
    REGISTERS, because asking about a refused writer is the normal case -- whether it is
    permitted is the next test's job.
    """
    _app, stream = _stream(tmp_path, monkeypatch)
    registered = set(stream._rpc._methods)
    assert registered, "no methods registered -- the fixture is not exercising the real surface"

    asked_about: dict[str, str] = {}
    for path in [STATIC_JS / "api.js", *VIEW_FILES]:
        for method in re.findall(r'isAvailable\(\s*"([^"]+)"\s*\)', path.read_text(encoding="utf-8")):
            asked_about[method] = path.name

    assert asked_about, "no view asks whether anything is available, so nothing is gated"
    unknown = {m: f for m, f in asked_about.items() if m not in registered}
    assert not unknown, f"the UI asks about methods this server does not have: {unknown}"


@pytest.mark.parametrize("path", VIEW_FILES, ids=lambda p: p.name)
def test_a_view_that_calls_a_refused_writer_also_asks_whether_it_is_available(
    path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The criterion itself: a control may not reach a refused writer ungated.

    Compared against the ALLOW-LIST, not against what is registered. The registrar
    registers a refusing handler for everything it refuses, so every writer is
    "registered" -- an earlier version of this test compared against that set and
    therefore could never fail, which is how an ungated view shipped green.

    Both halves are derived: the wrapper-to-method map is read out of api.js, and the
    permitted set is imported from console.py. A view gaining a fourteenth call site
    fails here until it gates it.
    """
    from reachy_language_tutor.console import _RPC_METHODS_EXPOSED_ON_THE_NETWORK

    source = path.read_text(encoding="utf-8")
    asked = set(re.findall(r'isAvailable\(\s*"([^"]+)"\s*\)', source))

    ungated = {}
    for wrapper, method in _wrapper_methods().items():
        if method in _RPC_METHODS_EXPOSED_ON_THE_NETWORK:
            continue  # permitted: nothing to gate
        if re.search(rf"\b{re.escape(wrapper)}\s*\(", source) and method not in asked:
            ungated[wrapper] = method

    assert not ungated, (
        f"{path.name} calls refused writers without asking whether they are available: {ungated}. "
        "Gate the control with isAvailable(), or the click reaches a writer the server refuses."
    )


def test_no_control_can_reach_the_network_without_passing_the_availability_guard() -> None:
    """One chokepoint, not thirteen call sites each remembering to check.

    Thirteen call sites across five files is exactly the shape that grows a fourteenth
    somebody forgets. The guard lives in rpcCall, so a control added later is covered
    without its author knowing this rule exists -- provided nothing else writes to the
    socket. That last part is what this asserts.
    """
    api = (STATIC_JS / "api.js").read_text(encoding="utf-8")

    sends = re.findall(r"socket\.send\(", api)
    assert len(sends) == 1, f"expected exactly one place that writes to the socket, found {len(sends)}"

    guard = re.search(r"if \(!isAvailable\(method\)\)", api)
    assert guard, "rpcCall does not check availability before sending"
    assert guard.start() < api.index("socket.send("), "the guard runs after the send, so it guards nothing"


def test_the_refusal_has_copy_a_person_can_act_on() -> None:
    """A control that silently does nothing is worse than one that explains itself.

    The server's own sentence is developer-register and names the protocol. The task asks
    for an explanation, so the reason code has to map to something a person reads.
    """
    api = (STATIC_JS / "api.js").read_text(encoding="utf-8")

    assert "not_available_over_the_network:" in api, "the network refusal has no user-facing copy"
    assert "mic_is_read_only:" in api, "the microphone refusal has no user-facing copy"


@pytest.mark.parametrize("path", VIEW_FILES, ids=lambda p: p.name)
def test_no_view_keeps_its_own_list_of_what_is_refused(path: Path) -> None:
    """The deny-list shape, refused structurally rather than by review.

    A view may ask about a method. What it may not do is accumulate the refused ones into
    a list of its own -- that is a second copy of a rule the server already owns, in
    another language, and it goes stale the first time console.py changes. Three or more
    method-shaped strings in one file is the signal.
    """
    source = path.read_text(encoding="utf-8")

    # The shape being refused is a COLLECTION of method names -- an array or set literal
    # holding two or more of them. Counting method strings anywhere in the file does not
    # work: a file legitimately asks about several methods one at a time, and an earlier
    # version of this test excluded exactly those, so a list built from them was
    # invisible to it. Look for the bracket, not for the count.
    collections = re.findall(r'\[[^\]]*?"[a-z_]+\.[a-z_]+"[^\]]*?"[a-z_]+\.[a-z_]+"[^\]]*?\]', source, re.S)

    assert not collections, (
        f"{path.name} holds a list of rpc method names, which is a second copy of a rule "
        f"console.py already owns and will drift from it: {collections}"
    )


def test_the_app_asks_what_it_may_run_before_any_view_renders_a_control() -> None:
    """The gap that shipped green the first time, pinned so it cannot ship again.

    Every gate in the views reads availability synchronously and is permissive until the
    answer arrives -- deliberately, so a slow load never greys out a working control. That
    is only safe if something asks EARLY. In the first version of this change nothing did:
    getStatus() ran only from the settings view, so home, tools and talk rendered their
    controls permissively and a click reached a writer the server refuses.

    This pins the wiring, not the rendering. There is no JavaScript runtime here, so it
    cannot prove the answer arrives before the first paint -- only that the app asks on
    the path every view goes through. The rendered behaviour is the task's manual check.
    """
    main_js = (STATIC_JS / "main.js").read_text(encoding="utf-8")

    boot = main_js[main_js.index("function boot()") :]
    assert "refreshAvailability()" in boot, (
        "nothing primes the availability cache at boot, so the first render of every view "
        "offers writers the server refuses. (Checked for the CALL, not the import: an "
        "unused import satisfied an earlier version of this assertion.)"
    )
    assert "refreshAvailability()" in boot[: boot.index("createRouter(")], (
        "availability is primed after the router is built, so a view can render first"
    )


def test_the_app_keeps_asking_while_the_rpc_surface_is_still_coming_up() -> None:
    """Asking once is not enough, and the failure it causes lasts the whole session.

    /rpc can be unready for up to STARTUP_DEADLINE_MS after the page loads -- that is why
    untilReady exists. A single failed prime leaves availability unknown, unknown is
    permissive, and nothing else asks: home, tools and talk never call getStatus. So one
    unlucky cold start meant every gate read "available" for the rest of the tab and
    clicks reached writers the server refuses. That is the ordinary case, not an exotic
    one.

    Structural, like the boot test above: there is no JavaScript runtime here, so this
    pins that the retry exists, not that it recovers.
    """
    api = (STATIC_JS / "api.js").read_text(encoding="utf-8")
    body = api.split("export async function refreshAvailability")[1].split("\nexport ")[0]

    assert "STARTUP_DEADLINE_MS" in body, "the availability prime gives up without a deadline"
    assert "for (;;)" in body or "while (" in body, (
        "the availability prime asks once; a cold start that misses leaves every gate "
        "permissive for the rest of the session"
    )
