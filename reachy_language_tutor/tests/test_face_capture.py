"""Tests for the frame source: one frame on demand, or one clear reason why not.

Every test in this file runs with NO CAMERA and none of them may be skipped. The media
handle is an ordinary stand-in and the "frames" are small synthetic arrays, so the
no-camera paths -- the ones that decide what a household's robot says when it cannot
see -- are exercised on every run rather than on a developer's machine that happens to
have a webcam. A guard at the bottom of this file refuses any skip mechanism, so that
stays true by construction rather than by everyone remembering.
"""

import ast
import logging
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from reachy_language_tutor import faces
from reachy_language_tutor.faces import capture
from reachy_language_tutor.faces.capture import (
    CAPTURE_REASONS,
    FrameCapture,
    capture_frame,
)


def _frame(height: int = 720, width: int = 1280) -> np.ndarray:
    """A stand-in with the shape and dtype the SDK measurably returns: BGR HxWx3 uint8."""
    return np.zeros((height, width, 3), dtype=np.uint8)


def _media_returning(*frames: object) -> MagicMock:
    """A media handle that hands back the given answers in order, one per get_frame call."""
    media = MagicMock()
    media.camera = MagicMock()
    media.get_frame.side_effect = list(frames)
    return media


def test_a_disabled_camera_is_refused_before_the_media_handle_is_touched() -> None:
    """--no-camera is an answer on its own, and asking the media layer anyway would be wrong."""
    media = MagicMock()

    shot = capture_frame(media, camera_enabled=False)

    assert shot.reason == "camera_disabled"
    assert shot.frame is None
    assert not shot.usable
    media.get_frame.assert_not_called()


def test_no_media_handle_at_all_answers_no_camera() -> None:
    """A caller on a machine with no robot needs no special case of its own."""
    shot = capture_frame(None, camera_enabled=True)

    assert shot.reason == "no_camera"
    assert not shot.usable


def test_a_media_handle_whose_camera_is_none_answers_no_camera_without_reading() -> None:
    """`camera is None` is the lasting fact about the machine, and the only real no-camera signal."""
    media = MagicMock()
    media.camera = None

    shot = capture_frame(media, camera_enabled=True)

    assert shot.reason == "no_camera"
    media.get_frame.assert_not_called()


def test_a_media_handle_that_does_not_mention_a_camera_is_still_asked_for_a_frame() -> None:
    """Silence about cameras is not the same claim as `camera is None`, and is not read as one."""

    class SaysNothingAboutCameras:
        def __init__(self, frame: object) -> None:
            self.frame = frame

        def get_frame(self) -> object:
            return self.frame

    frame = _frame()

    shot = capture_frame(SaysNothingAboutCameras(frame), camera_enabled=True)

    assert shot.frame is frame


def test_the_frame_is_returned_as_the_very_object_the_sdk_handed_over() -> None:
    """Criterion 1's 'no conversion at the call site', proved by identity rather than by shape."""
    frame = _frame()
    media = _media_returning(frame)

    shot = capture_frame(media, camera_enabled=True)

    assert shot.frame is frame
    assert shot.usable
    assert shot.reason is None


def test_an_early_read_is_retried_and_stops_the_moment_a_frame_arrives() -> None:
    """get_frame answers None when it is simply early; that is not a missing camera."""
    frame = _frame()
    media = _media_returning(None, None, frame)

    shot = capture_frame(media, camera_enabled=True)

    assert shot.frame is frame
    assert media.get_frame.call_count == 3


def test_a_camera_that_never_produces_a_frame_answers_no_frame_within_its_budget() -> None:
    """The budget is pinned to the named constant, so tuning it cannot silently widen the wait."""
    media = _media_returning(*[None] * capture._FRAME_ATTEMPTS)

    shot = capture_frame(media, camera_enabled=True)

    assert shot.reason == "no_frame"
    assert media.get_frame.call_count == capture._FRAME_ATTEMPTS


def test_two_frames_requested_in_quick_succession_each_get_their_own_budget() -> None:
    """The edge case the acceptance criteria name: back-to-back reads miss by construction."""
    first, second = _frame(), _frame()
    media = _media_returning(None, first, None, second)

    one = capture_frame(media, camera_enabled=True)
    two = capture_frame(media, camera_enabled=True)

    assert one.frame is first
    assert two.frame is second


@pytest.mark.parametrize(
    "raised",
    [
        PermissionError("the operating system refused the camera"),
        RuntimeError("the camera is busy in another application"),
        OSError("the device went away"),
    ],
)
def test_a_backend_that_raises_becomes_an_answer_rather_than_an_exception(raised: Exception) -> None:
    """Criterion 3: a caller on the voice loop cannot afford an exception, whatever the backend does."""
    media = MagicMock()
    media.camera = MagicMock()
    media.get_frame.side_effect = raised

    shot = capture_frame(media, camera_enabled=True)

    assert shot.reason == "no_frame"
    assert not shot.usable


def test_the_only_thing_ever_called_on_the_media_handle_is_get_frame() -> None:
    """An allow-list of one method, so close(), release_media() and anything new are refused together.

    This is the release path's test. capture.py must not close or release the handle it
    borrowed: MediaManager.close() closes the audio device too, and that device is the
    tutor's voice, so a frame grab that released it would mute the lesson.
    """
    media = _media_returning(_frame())

    capture_frame(media, camera_enabled=True)

    called = {name.split(".")[0] for name, _, _ in media.mock_calls}
    assert called <= {"get_frame"}, f"capture_frame touched something other than get_frame: {called}"


def test_the_real_release_of_the_camera_handle_still_happens_in_main_shutdown() -> None:
    """capture.py owns nothing because main.py does; if that one release goes, this says so.

    The claim in capture.py's docstring -- that it releases nothing because whoever
    built the robot does -- is only true while main.py actually does it. Asserted
    structurally rather than by running the app, the same way test_lesson_feedback pins
    main.py's warm-up call.

    THIS USED TO PIN "EXACTLY ONCE", and that pin was right until there were two
    owners. Enrolment is an operator command that builds its own ReachyMini outside
    run(), so it must close its own media too, and a count of one would now be a rule
    against doing the correct thing. The count is replaced by the property the count
    was standing in for, which is also the stronger one: EVERY function here that
    constructs a ReachyMini closes .media in a finally. A third owner added later
    without a release fails this, where a count of two would simply have been bumped
    to three.
    """
    from reachy_language_tutor import main

    tree = ast.parse(Path(main.__file__).read_text(encoding="utf-8"))

    def closes_media(scope: ast.AST) -> list[ast.Call]:
        """Every `<something>.media.close()` call anywhere inside this node."""
        return [
            node
            for node in ast.walk(scope)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "close"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "media"
        ]

    everywhere = closes_media(tree)
    assert everywhere, "main.py no longer closes the media handle; capture.py's docstring names it as the owner"

    # Every builder releases. The set of owners is DERIVED from the file rather than
    # listed here, so a function that starts building a robot tomorrow is judged by
    # this rule without anybody remembering to add it.
    def builds_a_robot(scope: ast.AST) -> bool:
        return any(
            isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "ReachyMini"
            for node in ast.walk(scope)
        )

    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    owners = [function for function in functions if builds_a_robot(function)]
    assert owners, "nothing in main.py builds a ReachyMini any more, so this test proves nothing"

    unreleased = [
        function.name
        for function in owners
        if not any(
            closes_media(ast.Module(body=block.finalbody, type_ignores=[]))
            for block in ast.walk(function)
            if isinstance(block, ast.Try)
        )
    ]
    assert unreleased == [], (
        f"these build a ReachyMini and never release its media in a finally: {unreleased}. "
        "capture.py borrows a handle and closes nothing, so whoever opens one has to."
    )

    # And in run's shutdown finally specifically -- not merely in some try/finally
    # somewhere in the file, which is all the previous version of this test checked.
    run_functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "run"
    ]
    assert run_functions, "main.py no longer defines run(), which capture.py's docstring names as the owner"

    in_run_shutdown_finally = [
        call
        for function in run_functions
        for block in ast.walk(function)
        if isinstance(block, ast.Try)
        for call in closes_media(ast.Module(body=block.finalbody, type_ignores=[]))
    ]
    assert in_run_shutdown_finally, (
        "the media handle is no longer closed in run()'s shutdown finally; "
        "capture.py's docstring and docs/SETUP.md both name that as the one release site"
    )


def test_a_frame_capture_never_renders_a_pixel_when_it_is_printed() -> None:
    """A privacy control, not formatting: the generated repr would put a face in every log line."""
    frame = np.arange(4 * 4 * 3, dtype=np.uint8).reshape((4, 4, 3))

    rendered = repr(FrameCapture(frame=frame))

    assert repr(frame) not in rendered
    assert "11" not in rendered.replace("(4, 4, 3)", "")
    assert "(4, 4, 3)" in rendered
    assert "uint8" in rendered
    assert len(rendered) < 120


def test_a_refusal_renders_its_reason_and_nothing_else() -> None:
    """The other half of the repr: a refusal has no frame to hide, and says which one it is."""
    rendered = repr(FrameCapture(reason="no_camera"))

    assert "no_camera" in rendered
    assert "frame" not in rendered


def test_comparing_or_hashing_an_answer_never_raises_on_the_voice_path() -> None:
    """The sibling of the __repr__ override: __eq__ and __hash__ are wrong for an array too.

    Generated elementwise, `a == b` hits numpy's "truth value of an array with more than
    one element is ambiguous" and `hash(shot)` hits "unhashable type: ndarray". Both are
    raised by ordinary Python a caller would write -- `shot in seen`, a dict key, an
    assertEqual -- in a module whose contract is that the voice loop never gets an
    exception. Identity is the honest answer for a value wrapping a buffer.
    """
    frame = _frame(4, 4)
    one, two = FrameCapture(frame=frame), FrameCapture(frame=frame)

    assert (one == two) is False, "two distinct answers are not equal; identity is the rule"
    assert one == one
    assert one != two
    assert {one, two} == {one, two}
    assert one in {one: "seen"}
    assert isinstance(hash(one), int)

    refusal = FrameCapture(reason="no_camera")
    assert refusal != FrameCapture(reason="no_camera")
    assert isinstance(hash(refusal), int)


def test_an_array_handed_in_as_a_reason_never_reaches_the_error_message() -> None:
    """The argument-order slip the type exists to survive: reason=<array> must not render values.

    `reason` is annotated str|None and nothing enforces it, and the other field is the
    frame, so passing one to the other is a plausible mistake. With `!r` in the message
    that ValueError would carry numpy's own repr into whatever logs or re-raises it --
    the leak the __repr__ override closes elsewhere, in the sibling method that was not
    swept.

    Both branches are checked because they are genuinely different, which is the whole
    reason this is worth a test rather than an assertion. A REAL frame cannot reach the
    message at all: `reason not in CAPTURE_REASONS` compares elementwise first, so numpy
    raises its own ambiguity error before the f-string is ever built. A ONE-element array
    has an unambiguous truth value, so it does reach the message -- and that is the case
    the fix is for.
    """
    frame = np.arange(2 * 2 * 3, dtype=np.uint8).reshape((2, 2, 3))

    with pytest.raises(ValueError) as caught:
        FrameCapture(reason=frame)

    multi_element = str(caught.value)
    assert repr(frame) not in multi_element
    assert "ambiguous" in multi_element, "numpy refuses a real frame before our message is built"
    for value in frame.flatten().tolist():
        assert f" {value} " not in f" {multi_element} "

    lone = np.array([7], dtype=np.uint8)

    with pytest.raises(ValueError) as caught:
        FrameCapture(reason=lone)

    single_element = str(caught.value)
    assert "ndarray" in single_element, "the type is named, so the message stays actionable"
    assert repr(lone) not in single_element
    assert "7" not in single_element, "the value itself must never be rendered"


def test_a_frame_capture_cannot_be_built_to_mean_nothing() -> None:
    """'An empty frame' is not a value this type can hold, so no caller has to check for one."""
    with pytest.raises(ValueError, match="neither"):
        FrameCapture()


def test_a_frame_capture_cannot_claim_a_frame_and_a_refusal_at_once() -> None:
    """Two answers is as unusable as none, and is refused at construction rather than at the call."""
    with pytest.raises(ValueError, match="both"):
        FrameCapture(frame=_frame(), reason="no_frame")


def test_a_reason_outside_the_published_vocabulary_is_refused() -> None:
    """A closed tuple, named as what is permitted: a code nobody agreed on cannot reach a caller."""
    with pytest.raises(ValueError, match="must be one of"):
        FrameCapture(reason="banana")


def test_every_published_reason_can_actually_be_built() -> None:
    """The vocabulary and the type agree, so a reason cannot be published and then be unusable."""
    for reason in CAPTURE_REASONS:
        assert FrameCapture(reason=reason).reason == reason


def test_no_frame_or_pixel_reaches_the_log_on_any_path(caplog: pytest.LogCaptureFixture) -> None:
    """The rule CLAUDE.md records as costing two defects, checked on every branch at once."""
    frame = np.arange(2 * 2 * 3, dtype=np.uint8).reshape((2, 2, 3))
    media = MagicMock()
    media.camera = MagicMock()

    with caplog.at_level(logging.DEBUG, logger=capture.logger.name):
        media.get_frame.return_value = frame
        capture_frame(media, camera_enabled=True)
        capture_frame(media, camera_enabled=False)
        capture_frame(None, camera_enabled=True)
        media.get_frame.return_value = None
        capture_frame(media, camera_enabled=True)
        media.get_frame.side_effect = PermissionError("/dev/video0 is not yours")
        capture_frame(media, camera_enabled=True)

    written = "\n".join(record.getMessage() for record in caplog.records)
    assert written, "the module logged nothing at all, so this guard would pass over any change"
    assert repr(frame) not in written
    assert "/dev/video0" not in written, "a backend's message reached the log and carried a device path"
    for value in frame.flatten().tolist():
        assert f" {value} " not in f" {written} "


def test_the_capture_module_is_reachable_through_the_package_boundary() -> None:
    """faces/__init__.py says import from here; a module callers must reach around would deny it."""
    assert faces.capture_frame is capture_frame
    assert faces.FrameCapture is FrameCapture
    assert "capture_frame" in faces.__all__
    assert "FrameCapture" in faces.__all__
    assert "CAPTURE_REASONS" in faces.__all__


def test_the_frame_source_may_import_only_what_it_needs_to_answer_one_question() -> None:
    """An allow-list of imports, which is what makes 'no thread and no timer' structural.

    threading and asyncio are absent, so a capture thread fails this test the moment it
    is imported. So is `time`: the retry budget is counted in attempts, and without a
    clock in the file there is no timer to add. numpy is absent too -- the frame is
    handed straight back, never touched, which is what makes 'no conversion at the call
    site' provable by identity. Named as what is permitted, so a module nobody thought
    of is refused by default.
    """
    permitted = {
        "__future__",
        "logging",
        "typing",
        "dataclasses",
    }
    source = Path(capture.__file__).read_text(encoding="utf-8")

    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            # Both granularities, so importing a MODULE out of a permitted package is
            # still visible rather than hidden behind the package's own entry.
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)
    # Attribute imports out of the standard-library modules already permitted above.
    imported -= {"__future__.annotations", "typing.Any", "dataclasses.dataclass"}

    assert imported <= permitted, f"capture imports something outside its allow-list: {imported - permitted}"


def test_none_of_these_tests_can_be_skipped() -> None:
    """Criterion 6, enforced on this file by this file: a skipped test is a test that does not exist.

    TWO allow-lists, because one is not enough and the first version of this guard proved
    it twice. Allow-listing only the dotted path left `how pytest is reached` unconstrained,
    so `from pytest import skip` (no Attribute node at all), `import pytest as pt` (rooted
    at `pt`), `from pytest import mark` (rooted at `mark`) and `import unittest` all walked
    straight past it -- the same shape as D19 and the four deny-list defects CLAUDE.md
    records. So:

    1. Every import in this file must be one of the exact dotted sources named below. That
       is what refuses `from pytest import skip`, `from pytest import mark` and every route
       through `unittest`, before any attribute is even looked at.
    2. Every attribute path rooted at a name actually bound to pytest -- whatever it was
       called at the import, so an alias is followed rather than evaded -- must be one of
       the permitted surfaces.

    Together those refuse skip, skipif, importorskip, xfail and SkipTest by every spelling
    that names them, plus the indirect routes -- `getattr` and `__import__` are refused in
    this file outright, so a pytest root cannot be reached as a bare Name and dodge the
    attribute walk. That is a narrower claim than the previous wording, which said "every
    spelling that can reach them" and was not true.

    WHAT THIS GUARD DOES NOT COVER, named rather than implied: it cannot fire if this
    module is never collected. A conftest.py autouse fixture, `collect_ignore`, an
    `addopts` deselect, or a module-level `__test__ = False` all remove tests without
    touching this file. The sibling defence for that is the collection floor in .stride.md,
    which is enforced twice and fails on a silent drop -- not this test.

    The no-camera paths are the ones a household depends on, and they are exactly the
    paths a "skip unless a webcam is present" marker would quietly stop running.
    """
    permitted_imports = {
        "ast",
        "logging",
        "numpy",
        "pytest",
        "pathlib.Path",
        "unittest.mock.MagicMock",
        "reachy_language_tutor.main",
        "reachy_language_tutor.faces",
        "reachy_language_tutor.faces.capture",
        "reachy_language_tutor.faces.capture.CAPTURE_REASONS",
        "reachy_language_tutor.faces.capture.FrameCapture",
        "reachy_language_tutor.faces.capture.capture_frame",
    }
    permitted_surface = {"raises", "mark", "mark.parametrize", "LogCaptureFixture"}

    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))

    # What each local name actually refers to, so an alias cannot hide its origin.
    sources: set[str] = set()
    pytest_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                sources.add(alias.name)
                if alias.name == "pytest":
                    pytest_roots.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                sources.add(f"{node.module}.{alias.name}")
                if node.module == "pytest":
                    # Reaching INTO pytest binds a bare name; refused by the import
                    # allow-list above, and recorded here so the failure names it.
                    pytest_roots.add(alias.asname or alias.name)

    # A pytest root reached as a bare Name -- getattr(pytest, "skip")() or
    # __import__("pytest").skip() -- produces no Attribute rooted at a Name, so the walk
    # below cannot see it. Neither has any legitimate use in this file, so both are
    # refused here rather than left as a hole the docstring would have to admit.
    indirection = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"getattr", "__import__"}
    }
    assert not indirection, f"this file reaches for a module indirectly, which evades the check below: {indirection}"

    assert sources <= permitted_imports, (
        f"this file imports something it is not permitted, which is how a skip gets in: {sources - permitted_imports}"
    )
    assert pytest_roots, "no binding to pytest was found at all, so this guard would pass over any change"

    def rooted_at_pytest(node: ast.AST) -> str | None:
        """The dotted path below whatever name pytest was bound to, or None."""
        parts = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name) and node.id in pytest_roots:
            return ".".join(reversed(parts))
        return None

    used = {
        path
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and (path := rooted_at_pytest(node)) is not None
    }

    assert used, "no pytest surface was found at all, so this guard would pass over any change"
    assert used <= permitted_surface, (
        f"this file reaches for a pytest feature it is not permitted: {used - permitted_surface}"
    )
