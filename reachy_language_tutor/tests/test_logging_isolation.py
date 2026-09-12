"""Prove the root logger is put back after a test reconfigures it.

The app configures logging with ``logging.basicConfig(..., force=True)``
(``utils.setup_logger``, reached from ``main()``). ``force=True`` closes and removes
every existing root handler and installs its own, so one test driving that path
reconfigures logging for the rest of the session.

That really happened: ``test_tool_spaces.py`` invokes ``main()`` for the tool-space
CLI subcommands, and in reverse file order it left the root logger at INFO, which
made three ``test_learner_schema.py`` tests fail on ``caplog.text == ""`` by picking
up an INFO record they never emitted. An order-dependent failure blaming the wrong
file -- the same shape as D28.

``conftest.py``'s ``_restore_root_logging`` fixture is the guard. These two tests are
what stop it being deleted as "an autouse fixture that does nothing": the first
mutates logging exactly as the app does, and the second asserts the damage did not
survive. They rely on source order within a file, which pytest preserves.
"""

import logging

from reachy_language_tutor.utils import setup_logger


# Filled in by the first test, at its very start. NOT captured at module import:
# import happens during collection, before pytest installs its per-test capture
# handlers, so an import-time snapshot records a stack that never exists while a test
# is running and the comparison below would fail for the wrong reason. (It did, on the
# first draft of this file -- which is exactly the "a test must fail for the reason it
# names" rule catching a test rather than a defect.)
_BASELINE: dict[str, object] = {}


def test_a_test_may_reconfigure_logging_the_way_the_app_does() -> None:
    """Mutate the root logger through the real app entry point, not a stand-in.

    Calling ``setup_logger`` rather than ``basicConfig`` directly is deliberate: the
    guard has to survive whatever that function actually does, and a test that
    reimplemented it would stop tracking it the moment it changed.
    """
    root = logging.getLogger()
    _BASELINE["level"] = root.level
    _BASELINE["handlers"] = list(root.handlers)

    setup_logger(debug=False)

    assert root.level == logging.INFO, "setup_logger no longer sets INFO; this test's premise is stale"
    assert any(isinstance(h, logging.StreamHandler) for h in root.handlers)


def test_the_previous_tests_logging_changes_did_not_leak_into_this_one() -> None:
    """The guard. Fails with the level the previous test left behind, if it leaked.

    Asserted against what the PREVIOUS test saw at its own start, not a hard-coded
    WARNING, so this keeps working if pytest is configured with a different level.
    """
    assert _BASELINE, "the mutating test above did not run first; this file relies on source order"

    root = logging.getLogger()
    assert root.level == _BASELINE["level"], (
        f"the root log level leaked out of the previous test: {root.level} != {_BASELINE['level']}"
    )
    assert root.handlers == _BASELINE["handlers"], (
        "basicConfig(force=True) replaced pytest's root handlers and they were never put back"
    )


def test_third_party_logger_levels_are_restored_too() -> None:
    """The sibling sweep. setup_logger pins more than the root logger.

    It also sets levels on aiortc and aioice (and, under --debug, openai and
    websockets). A guard that restored only the root would leave those behind --
    the "fix the class, not the member" defect this board repeats most, committed
    inside the very fixture written to close an isolation leak.

    Measured: without the level sweep these came back 0 -> 40 and 0 -> 30 and stayed
    there for the rest of the session.
    """
    before = {name: logging.getLogger(name).level for name in ("aiortc", "aioice")}

    setup_logger(debug=False)
    assert logging.getLogger("aiortc").level == logging.ERROR, "setup_logger no longer pins aiortc; premise is stale"
    assert logging.getLogger("aioice").level == logging.WARNING

    # The restore happens at teardown, so what this test can assert directly is that
    # the premise holds. The test below is what proves the restore actually ran.
    assert before is not None


def test_the_third_party_levels_did_not_leak_out_of_the_previous_test() -> None:
    """The guard for the sweep, in the same ordered-pair shape as the root check."""
    assert logging.getLogger("aiortc").level == logging.NOTSET, "aiortc's level leaked out of the previous test"
    assert logging.getLogger("aioice").level == logging.NOTSET, "aioice's level leaked out of the previous test"
