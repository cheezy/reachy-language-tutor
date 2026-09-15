"""Tests for the camera tool."""

import base64
from unittest.mock import MagicMock

import pytest

from reachy_language_tutor.tools import camera as camera_module
from reachy_language_tutor.tools.camera import Camera
from reachy_language_tutor.tools.core_tools import ToolDependencies


@pytest.mark.asyncio
async def test_camera_tool_returns_base64_of_sdk_jpeg() -> None:
    """The tool base64-encodes the JPEG bytes returned by the SDK."""
    jpeg_bytes = b"\xff\xd8jpeg\xff\xd9"
    reachy_mini = MagicMock()
    reachy_mini.media.get_frame_jpeg.return_value = jpeg_bytes

    deps = ToolDependencies(
        reachy_mini=reachy_mini,
        movement_manager=MagicMock(),
        camera_enabled=True,
    )

    result = await Camera()(deps, question="What color is this?")

    assert result["b64_im"] == base64.b64encode(jpeg_bytes).decode("utf-8")


@pytest.mark.asyncio
async def test_camera_tool_reports_error_when_no_frame() -> None:
    """With no frame available the tool returns an error."""
    reachy_mini = MagicMock()
    reachy_mini.media.get_frame_jpeg.return_value = None

    deps = ToolDependencies(
        reachy_mini=reachy_mini,
        movement_manager=MagicMock(),
        camera_enabled=True,
    )

    result = await Camera()(deps, question="What color is this?")

    assert "error" in result


@pytest.mark.asyncio
async def test_camera_tool_reports_error_when_camera_disabled() -> None:
    """With the camera disabled the tool returns an error and never reads a frame."""
    reachy_mini = MagicMock()
    deps = ToolDependencies(
        reachy_mini=reachy_mini,
        movement_manager=MagicMock(),
        camera_enabled=False,
    )

    result = await Camera()(deps, question="What color is this?")

    assert "error" in result
    reachy_mini.media.get_frame_jpeg.assert_not_called()


@pytest.mark.asyncio
async def test_an_early_read_is_retried_rather_than_reported_as_no_frame() -> None:
    """A miss by cadence is not a missing camera, and the person must not be told it is.

    get_frame_jpeg waits ~20ms for a sample while the camera produces one every ~33ms,
    so a read can return None simply by being early. Measured on 2026-09-14 against the
    desktop app's mockup simulation: 3 of 10 back-to-back calls returned bytes. Before
    this retry, asking the robot to look twice in a row answered "No frame available"
    with the camera working perfectly.
    """
    jpeg_bytes = b"\xff\xd8jpeg\xff\xd9"
    reachy_mini = MagicMock()
    reachy_mini.media.get_frame_jpeg.side_effect = [None, None, jpeg_bytes]

    deps = ToolDependencies(
        reachy_mini=reachy_mini,
        movement_manager=MagicMock(),
        camera_enabled=True,
    )

    result = await Camera()(deps, question="What color is this?")

    assert result["b64_im"] == base64.b64encode(jpeg_bytes).decode("utf-8")
    assert reachy_mini.media.get_frame_jpeg.call_count == 3


@pytest.mark.asyncio
async def test_a_camera_that_never_answers_still_reports_no_frame_within_its_budget() -> None:
    """The retry is bounded: a genuinely dead camera is still an answer, not a hang."""
    reachy_mini = MagicMock()
    reachy_mini.media.get_frame_jpeg.return_value = None

    deps = ToolDependencies(
        reachy_mini=reachy_mini,
        movement_manager=MagicMock(),
        camera_enabled=True,
    )

    result = await Camera()(deps, question="What color is this?")

    assert "error" in result
    assert reachy_mini.media.get_frame_jpeg.call_count == camera_module._FRAME_ATTEMPTS


def test_the_two_frame_budgets_stay_equal() -> None:
    """One number, two modules, and nothing was checking they matched.

    camera.py duplicates faces/capture.py's attempt budget deliberately -- the tools
    package must not import the faces package -- but deliberate duplication still needs
    a check, the same way the three copies of the embedding dimension have one. They
    describe the identical camera cadence, so they must move together.
    """
    from reachy_language_tutor.faces import capture

    assert camera_module._FRAME_ATTEMPTS == capture._FRAME_ATTEMPTS, (
        "the camera tool and the frame source disagree about how many reads one request may make; "
        "they are describing the same 30fps camera"
    )


def test_the_question_is_logged_only_through_the_redaction() -> None:
    """The model composes this string from a conversation that holds a person's name.

    There used to be TWO log lines here: an INFO through describe_for_log and, one
    line below it, a DEBUG printing question[:120] raw. The comment above them said
    in as many words why the value is dangerous -- "what is Alice holding" is within
    the schema's own examples -- and the second line did it anyway, so the redaction
    was decorative for anybody running with --debug. That is the D3/D9 rule, which
    this project treats as its loudest.

    Asserted structurally rather than by capturing output, so it holds for the DEBUG
    level without the test having to enable it.
    """
    import ast
    from pathlib import Path

    from reachy_language_tutor.tools import camera as camera_module

    tree = ast.parse(Path(camera_module.__file__).read_text(encoding="utf-8"))
    unredacted: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or getattr(node.func, "attr", None) not in {
            "debug", "info", "warning", "error", "exception", "critical", "log"
        }:
            continue
        for argument in node.args:
            mentions_question = any(
                isinstance(inner, ast.Name) and inner.id == "question" for inner in ast.walk(argument)
            )
            redacted = (
                isinstance(argument, ast.Call)
                and getattr(argument.func, "id", getattr(argument.func, "attr", None)) == "describe_for_log"
            )
            if mentions_question and not redacted:
                unredacted.append(f"line {node.lineno}")

    assert unredacted == [], (
        f"camera.py logs the model-composed question without redaction at {unredacted}; "
        "the model builds that string from a conversation holding a person's name"
    )
