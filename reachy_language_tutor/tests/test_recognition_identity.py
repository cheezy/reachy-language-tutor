"""Recognition choosing who the app serves, and refusing to when it cannot be trusted.

THE CENTRAL FACT THIS FILE PINS: while `faces.THRESHOLD_CALIBRATED` is False, a
confident identification is made and then WITHHELD. The threshold it rests on has
never been measured against real faces -- W26 shipped it that way by an explicit
recorded decision, and this task is where that unmeasured control would otherwise
start making live authorization decisions.

The pair of tests at the top brackets that from both sides: same input, two flag
values, two different answers. Neither alone is enough. Without the first, deleting
the gate passes; without the second, gating before the match -- which would make the
resolver a constant None with extra steps -- also passes.

EVERY TEST HERE ASSERTS A DISPOSITION, not merely `is None`. Once nobody is the
default answer, `assert x is None` passes for any reason at all, including a
pipeline that broke on the first line, so a bare None assertion proves almost
nothing about the path it claims to exercise.
"""

from __future__ import annotations

import ast
import logging
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from reachy_language_tutor import current_learner, faces, main
from reachy_language_tutor.faces import matching as _matching_module
from reachy_language_tutor.current_learner import (
    DEV_CURRENT_LEARNER_ENV,
    RECOGNITION_DISPOSITIONS,
    RecognitionOutcome,
    recognise_current_learner,
)
from reachy_language_tutor.faces import FaceEmbedding, capture
from reachy_language_tutor.learners import store


DIMENSION = 128
MODEL = "opencv_sface_2021dec_fp32"


def _unit(index: int) -> tuple[float, ...]:
    """A unit vector along one axis: orthogonal to every other, so cosine is 0."""
    vector = [0.0] * DIMENSION
    vector[index] = 1.0
    return tuple(vector)


@pytest.fixture()
def instance(tmp_path: Path) -> Path:
    """A prepared learner database with nobody enrolled."""
    assert store.ensure_learner_database(tmp_path).ready is True
    return tmp_path


def _enrol(instance_path: Path, name: str, vector: tuple[float, ...]) -> str:
    """Put a real enrolled learner in the database and return their id."""
    agreed = store.record_consent(
        name,
        scope="face_recognition",
        statement_id="test.v1",
        statement_text="Wording used by the tests.",
        granted_by="the_person_themselves",
        granted_via="operator_at_the_robot",
        instance_path=instance_path,
    )
    assert agreed.recorded is True and agreed.learner_id is not None
    assert store.save_faceprint(agreed.learner_id, MODEL, vector, instance_path=instance_path).saved is True
    return agreed.learner_id


class _Camera:
    """A media handle that answers one frame. The only method the flow may call."""

    camera = object()

    def get_frame(self) -> object:
        return "a frame"


def _sees(monkeypatch: pytest.MonkeyPatch, vector: tuple[float, ...]) -> None:
    """Make the face pipeline return this faceprint, with no model and no camera."""
    monkeypatch.setattr(current_learner, "describe_face", lambda _image: FaceEmbedding(vector=vector))


# ------------------------------------------------- the calibration gate, both sides


def test_an_uncalibrated_threshold_withholds_a_confident_match(
    instance: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A real identification is made, and the app serves nobody anyway.

    This is the decision the task exists to force. The similarity here is 1.0 against
    a lone enrolled member -- as confident as this matcher can be -- and the answer
    is still nobody, because the floor that confidence is measured against has never
    been checked against real faces.

    The DISPOSITION is what makes this test mean something: `no_one_close_enough`
    would also be `learner_id is None`, and would mean the opposite.
    """
    enrolled = _enrol(instance, "Ana", _unit(0))
    _sees(monkeypatch, _unit(0))
    monkeypatch.setattr(current_learner, "THRESHOLD_CALIBRATED", False)

    with caplog.at_level(logging.DEBUG):
        outcome = recognise_current_learner(media=_Camera(), camera_enabled=True, instance_path=instance)

    assert outcome.learner_id is None
    assert outcome.disposition == "declined_uncalibrated"
    assert enrolled not in "".join(record.getMessage() for record in caplog.records), (
        "the decline must not name the person it declined to serve"
    )


def test_the_same_confident_match_is_served_once_the_threshold_is_measured(
    instance: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Byte-identical input, one flag flipped, and now the person is served.

    The other half of the bracket. Gating BEFORE the match -- checking the flag and
    skipping the work -- would make the resolver a constant, and would pass the test
    above while failing this one.
    """
    enrolled = _enrol(instance, "Ana", _unit(0))
    _sees(monkeypatch, _unit(0))
    monkeypatch.setattr(current_learner, "THRESHOLD_CALIBRATED", True)

    outcome = recognise_current_learner(media=_Camera(), camera_enabled=True, instance_path=instance)

    assert outcome.learner_id == enrolled
    assert outcome.disposition == "identified"


def test_the_calibration_flag_cannot_be_flipped_while_identity_is_process_lifetime() -> None:
    """The precondition, ENFORCED rather than written down.

    A review's point and a fair one: recording a precondition in three comments is
    not the same as enforcing it. `THRESHOLD_CALIBRATED = True` is a one-token edit
    that simultaneously enables matching AND removes the only thing bounding stale
    identity -- because identity is resolved once per process and the conversation
    handler closes over that bundle, so a face recognised at boot is served for the
    life of the process.

    So flipping the flag fails here until somebody has done the other half. Delete
    this test deliberately, at the same time as building a per-session dependency
    bundle -- that is the point: the deletion is the decision, and it cannot happen
    by not noticing.
    """
    assert faces.THRESHOLD_CALIBRATED is False, (
        "THRESHOLD_CALIBRATED was flipped. Before it may be True, re-identification has "
        "to be solved: identity is resolved once per process and the handler closes over "
        "that bundle, so a match made at boot is served until the process ends. Build a "
        "fresh ToolDependencies per session, then delete this test in the same change."
    )


def test_the_reidentification_precondition_is_recorded_where_the_docstring_says() -> None:
    """A claim about where something is written down, checked against what is there.

    current_learner.py's docstring says solving re-identification is a precondition
    for flipping THRESHOLD_CALIBRATED and that it is "recorded beside that flag". A
    review found that was simply FALSE -- faces/matching.py was untouched and its
    comment listed only the calibration steps. The claim is now true, and this test
    is what stops it drifting back: the two things that must be said are said in the
    one place somebody about to flip the flag is guaranteed to read.

    Prose, pinned, for the same reason the consent notice's wording is pinned to a
    digest one task earlier: a sentence asserting where a decision lives is a claim
    about the repository, and claims about the repository can be checked.
    """
    matching_source = Path(_matching_module.__file__).read_text(encoding="utf-8")
    flag_at = matching_source.index("THRESHOLD_CALIBRATED = False")
    preamble = matching_source[:flag_at]

    assert "calibrate_faceprints" in preamble, "the measurement step is no longer recorded beside the flag"
    assert "MID-SESSION" in preamble.upper(), (
        "the re-identification precondition is not recorded beside THRESHOLD_CALIBRATED, "
        "and current_learner.py's docstring claims it is"
    )
    assert "recorded beside that flag" in Path(current_learner.__file__).read_text(encoding="utf-8"), (
        "the docstring no longer makes the claim this test exists to check"
    )


def test_recognition_actually_runs_before_it_is_declined(
    instance: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DECIDE, THEN WITHHOLD -- and this is the only test that can tell the difference.

    Found by revert-proofing. Checking the flag FIRST and skipping the pipeline is
    observably identical to running it and withholding, through every other test in
    this file: both answer `declined_uncalibrated` when the flag is False and both
    answer `identified` when it is True.

    The difference is whether recognition was consulted at all, and it is the whole
    of acceptance criterion 1. A resolver that short-circuits on the flag returns a
    constant None for every input and has stopped being recognition-derived; one
    that runs the pipeline and withholds the answer has not. So the camera read and
    the match are asserted directly, with the flag False.
    """
    enrolled = _enrol(instance, "Ana", _unit(0))
    _sees(monkeypatch, _unit(0))
    monkeypatch.setattr(current_learner, "THRESHOLD_CALIBRATED", False)

    frames: list[int] = []
    matches: list[int] = []
    real_capture = current_learner.capture_frame
    real_match = current_learner.match_faceprint

    def counting_capture(*args: object, **kwargs: object):
        frames.append(1)
        return real_capture(*args, **kwargs)  # type: ignore[arg-type]

    def counting_match(*args: object, **kwargs: object):
        matches.append(1)
        return real_match(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(current_learner, "capture_frame", counting_capture)
    monkeypatch.setattr(current_learner, "match_faceprint", counting_match)

    outcome = recognise_current_learner(media=_Camera(), camera_enabled=True, instance_path=instance)

    assert outcome.disposition == "declined_uncalibrated"
    assert frames == [1], "the camera was never read, so nothing was withheld -- it was skipped"
    assert matches == [1], (
        "the match was never attempted, so the flag is gating the pipeline rather than "
        "its result, and the answer is no longer recognition-derived"
    )
    assert enrolled is not None


def test_recognition_absorbs_its_own_failures_rather_than_relying_on_the_caller(
    instance: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The docstring says "never raises", and it has to be true HERE.

    It was not. The function had no try/except at all, and startup survived only
    because main's resolver wraps the call -- so the fail-closed posture was the
    caller's property while this function's docstring claimed it, and the test that
    looked like it covered this asserted through main and passed for the caller's
    reason. A second caller written on the strength of that docstring would have
    inherited an unguarded raise. Asserted directly against this function.
    """
    monkeypatch.setattr(
        current_learner,
        "get_enrolled_faceprints",
        lambda **_k: (_ for _ in ()).throw(MemoryError("out of memory")),
    )
    _sees(monkeypatch, _unit(0))

    outcome = recognise_current_learner(media=_Camera(), camera_enabled=True, instance_path=instance)

    assert outcome.learner_id is None
    assert outcome.disposition == "recognition_unavailable"


def test_a_match_carrying_an_empty_id_is_not_served_as_identified(
    instance: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The falsy class, not one member of it.

    `outcome.learner_id is None` let MatchOutcome(matched=True, learner_id="")
    through as `identified` with an empty id -- measured by a review. One character
    closes the family, and this is what stops it being reopened.
    """
    _enrol(instance, "Ana", _unit(0))
    _sees(monkeypatch, _unit(0))
    monkeypatch.setattr(current_learner, "THRESHOLD_CALIBRATED", True)
    monkeypatch.setattr(
        current_learner,
        "match_faceprint",
        lambda *a, **k: faces.MatchOutcome(matched=True, learner_id="", reason=None),
    )

    outcome = recognise_current_learner(media=_Camera(), camera_enabled=True, instance_path=instance)

    assert outcome.learner_id is None
    # not_recognised, specifically. Two controls close this -- the falsy guard here
    # and RecognitionOutcome's own refusal of an empty id -- and asserting only
    # "not identified" cannot tell them apart: with the guard reverted the
    # constructor raises instead and the outer absorb answers
    # recognition_unavailable, which is also "not identified". Naming the guard's
    # own disposition is what makes this test about the guard.
    assert outcome.disposition == "not_recognised", (
        "the falsy-id guard did not handle this; something else did"
    )


@pytest.mark.parametrize("bad", [12345, "", b"x", 0.0, []])
def test_an_outcome_cannot_carry_something_that_is_not_a_learner_id(bad: object) -> None:
    """The sibling field, validated. `disposition` was checked and this was not.

    RecognitionOutcome(12345, "identified") constructed fine, which is the
    D5 -> D10 -> D14 shape this board keeps paying for: a guard applied to one field
    of a pair. Neither refusal had a test -- `pytest.raises` appeared nowhere in
    this file -- so both are exercised now.
    """
    with pytest.raises(ValueError, match="learner_id must be None or a non-empty string"):
        RecognitionOutcome(bad, "identified")  # type: ignore[arg-type]


def test_an_outcome_cannot_carry_an_unpublished_disposition() -> None:
    """The other half of the pair, and the message names the value not the type."""
    with pytest.raises(ValueError, match="not 'made-up'"):
        RecognitionOutcome(None, "made-up")


def test_the_flag_this_module_reads_is_the_one_the_faces_package_publishes() -> None:
    """A local copy that drifted would make the gate a decoration."""
    assert current_learner.THRESHOLD_CALIBRATED is faces.THRESHOLD_CALIBRATED


def test_two_different_faces_resolve_to_two_different_learners(
    instance: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The strongest available statement of "comes from recognition".

    A resolver returning a constant passes every single-learner test in this file.
    It cannot pass this one.
    """
    ana = _enrol(instance, "Ana", _unit(0))
    ben = _enrol(instance, "Ben", _unit(40))
    monkeypatch.setattr(current_learner, "THRESHOLD_CALIBRATED", True)

    _sees(monkeypatch, _unit(0))
    first = recognise_current_learner(media=_Camera(), camera_enabled=True, instance_path=instance)
    _sees(monkeypatch, _unit(40))
    second = recognise_current_learner(media=_Camera(), camera_enabled=True, instance_path=instance)

    assert first.learner_id == ana
    assert second.learner_id == ben
    assert first.learner_id != second.learner_id


# ----------------------------------------------------- a wrong match is worse


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        (_unit(99), "no_one_close_enough"),
        (_unit(0), "too_close_to_call"),
    ],
)
def test_an_unsure_match_is_nobody_and_never_the_nearest_candidate(
    instance: Path, monkeypatch: pytest.MonkeyPatch, candidate: tuple[float, ...], expected: str
) -> None:
    """Both ways of being unsure, and neither returns the closest person.

    too_close_to_call is produced by enrolling the same face twice, which is the
    duplicate-enrolment state W28 left deliberately reachable -- and this is the
    refusal that makes it safe.
    """
    _enrol(instance, "Ana", _unit(0))
    _enrol(instance, "Ana again", _unit(0))
    _sees(monkeypatch, candidate)
    monkeypatch.setattr(current_learner, "THRESHOLD_CALIBRATED", True)

    outcome = recognise_current_learner(media=_Camera(), camera_enabled=True, instance_path=instance)

    assert outcome.learner_id is None
    assert outcome.disposition == expected


@pytest.mark.parametrize(
    ("camera_enabled", "media", "reason", "expected"),
    [
        (False, None, None, "camera_disabled"),
        (True, None, None, "no_camera"),
        (True, "camera", "no_face", "no_face"),
        (True, "camera", "several_faces", "several_faces"),
        (True, "camera", "recognition_unavailable", "recognition_unavailable"),
    ],
)
def test_every_way_of_not_knowing_keeps_its_own_answer(
    instance: Path,
    monkeypatch: pytest.MonkeyPatch,
    camera_enabled: bool,
    media: str | None,
    reason: str | None,
    expected: str,
) -> None:
    """Collapsing these would tell an operator to check hardware that is fine."""
    if reason is not None:
        monkeypatch.setattr(current_learner, "describe_face", lambda _i: FaceEmbedding(reason=reason))

    outcome = recognise_current_learner(
        media=_Camera() if media else None, camera_enabled=camera_enabled, instance_path=instance
    )

    assert outcome.learner_id is None
    assert outcome.disposition == expected


def test_an_unreadable_store_is_not_an_empty_household(
    instance: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"Nobody has enrolled" and "I cannot read my database" are different answers."""
    _sees(monkeypatch, _unit(0))

    empty = recognise_current_learner(media=_Camera(), camera_enabled=True, instance_path=instance)
    broken = recognise_current_learner(media=_Camera(), camera_enabled=True, instance_path="/nonexistent")

    assert empty.disposition == "nobody_enrolled"
    assert broken.disposition == "store_unreadable"


# --------------------------------------------------------- the development override


def test_the_override_is_not_consulted_when_the_variable_is_unset(
    instance: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unset means recognition runs -- proved by the camera actually being read."""
    monkeypatch.delenv(DEV_CURRENT_LEARNER_ENV, raising=False)
    reads: list[int] = []

    class _Counting(_Camera):
        def get_frame(self) -> object:
            reads.append(1)
            return "a frame"

    recognise_current_learner(media=_Counting(), camera_enabled=True, instance_path=instance)

    assert reads, "recognition was skipped even though no override was set"


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_a_blank_override_is_the_same_as_an_unset_one(
    instance: Path, monkeypatch: pytest.MonkeyPatch, blank: str
) -> None:
    """A stray `export VAR=` must not silently switch recognition off."""
    monkeypatch.setenv(DEV_CURRENT_LEARNER_ENV, blank)

    outcome = recognise_current_learner(media=None, camera_enabled=True, instance_path=instance)

    assert outcome.disposition == "no_camera", "a blank override took the override path"


def test_the_override_bypasses_the_camera_entirely(
    instance: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An override that still consults a camera is not deterministic."""
    monkeypatch.setenv(DEV_CURRENT_LEARNER_ENV, "dev-person")
    monkeypatch.setattr(
        current_learner,
        "capture_frame",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("the override read the camera")),
    )

    outcome = recognise_current_learner(media=_Camera(), camera_enabled=True, instance_path=instance)

    assert outcome.learner_id == "dev-person"
    assert outcome.disposition == "override"


def test_the_override_announces_itself_without_naming_who(
    instance: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A fallback nobody can see is one that ships. The VALUE still never appears."""
    monkeypatch.setenv(DEV_CURRENT_LEARNER_ENV, "dev-person")

    with caplog.at_level(logging.DEBUG):
        recognise_current_learner(media=None, camera_enabled=False, instance_path=instance)

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "the override said nothing"
    said = " ".join(r.getMessage() for r in warnings)
    assert DEV_CURRENT_LEARNER_ENV in said, "the operator is not told how to turn it off"
    assert "dev-person" not in said, "the override announced WHO it is serving"


def test_an_override_naming_nobody_still_serves_nobody(
    instance: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The override is untrusted input and goes through the same existence check."""
    monkeypatch.setenv(DEV_CURRENT_LEARNER_ENV, "no-such-learner")

    with caplog.at_level(logging.DEBUG):
        resolved = main.resolve_current_learner_id(instance, logging.getLogger(__name__))

    assert resolved is None
    # The message, not just the None. This file's own header says every test asserts
    # a disposition, and this one did not: with the override deleted the camera
    # branch also returns None and it would have passed unchanged.
    said = " ".join(record.getMessage() for record in caplog.records)
    assert "not in the learner database" in said, (
        "the override was not checked against the database; this passed via the camera branch"
    )
    assert "no-such-learner" not in said


def test_this_module_reads_exactly_one_environment_variable() -> None:
    """A second, undocumented override would be a second way to set an identity."""
    tree = ast.parse(Path(current_learner.__file__).read_text(encoding="utf-8"))
    reads = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "getenv"
    ]

    assert len(reads) == 1, f"expected one environment read, found {len(reads)}"
    assert isinstance(reads[0].args[0], ast.Name) and reads[0].args[0].id == "DEV_CURRENT_LEARNER_ENV"


# ------------------------------------------------------- posture and hygiene


@pytest.mark.parametrize("seam", ["capture_frame", "describe_face", "get_enrolled_faceprints", "match_faceprint"])
def test_startup_never_aborts_however_recognition_fails(
    instance: Path, monkeypatch: pytest.MonkeyPatch, seam: str
) -> None:
    """A corrupt database or a broken camera must not brick a robot in somebody's home.

    MemoryError rather than Exception, so narrowing the resolver's `except Exception`
    to a tuple of expected types would fail this.
    """
    # Reached, not merely patched. A review found two of these four seams were never
    # executed: without _sees, the real describe_face runs on the string "a frame",
    # answers recognition_unavailable or frame_unreadable, and returns before
    # get_enrolled_faceprints or match_faceprint is ever called -- so the injected
    # MemoryError was never raised and the test passed for the wrong reason. The
    # counter below is what makes that impossible to repeat.
    _enrol(instance, "Ana", _unit(0))
    _sees(monkeypatch, _unit(0))
    monkeypatch.setattr(current_learner, "THRESHOLD_CALIBRATED", True)

    reached: list[str] = []

    def _explode(*_args: object, **_kwargs: object):
        reached.append(seam)
        raise MemoryError("out of memory")

    monkeypatch.setattr(current_learner, seam, _explode)

    resolved = main.resolve_current_learner_id(
        instance, logging.getLogger(__name__), media=_Camera(), camera_enabled=True
    )

    assert resolved is None
    assert reached == [seam], f"the {seam} seam was never reached, so nothing was tested"


def test_one_resolution_makes_one_capture_request(
    instance: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No polling loop and no retry HERE: one capture request, compared and dropped.

    Named for what it measures. It used to be called "asks for at most one frame",
    which was false twice over: it patched capture_frame out and counted calls to
    its own stub, and capture_frame itself issues up to _FRAME_ATTEMPTS reads
    internally -- W28's budget for a 30fps camera that can be read early, not work
    this module adds. So the real reads are counted too, and asserted against that
    published budget rather than against one.
    """
    requests: list[int] = []
    real_capture = current_learner.capture_frame

    def counting(*args: object, **kwargs: object):
        requests.append(1)
        return real_capture(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(current_learner, "capture_frame", counting)

    reads: list[int] = []

    class _Counting(_Camera):
        def get_frame(self) -> object:
            reads.append(1)
            return None  # never a usable frame, so the budget is spent in full

    recognise_current_learner(media=_Counting(), camera_enabled=True, instance_path=instance)

    assert requests == [1], "recognition asked the camera more than once"
    assert len(reads) <= capture._FRAME_ATTEMPTS, (
        f"{len(reads)} reads exceeds capture.py's published budget of {capture._FRAME_ATTEMPTS}"
    )


def test_startup_consults_recognition_exactly_once(
    instance: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Twice would be two frames and two chances to answer differently."""
    calls: list[int] = []
    real = current_learner.recognise_current_learner

    def counting(**kwargs: object):
        calls.append(1)
        return real(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(current_learner, "recognise_current_learner", counting)

    main.build_tool_dependencies(
        robot=MagicMock(),
        movement_manager=MagicMock(),
        instance_path=instance,
        camera_enabled=False,
        logger=logging.getLogger(__name__),
    )

    assert len(calls) == 1


PERMITTED_IMPORTS = frozenset(
    {
        "__future__",
        "os",
        "logging",
        "typing",
        "dataclasses",
        "reachy_language_tutor.faces",
        "reachy_language_tutor.learners",
    }
)


def test_the_resolver_may_import_only_what_it_needs_to_choose_an_identity() -> None:
    """No thread and no timer, as a property of the file rather than a promise.

    The lesson_feedback.py pattern. `threading`, `time`, `asyncio` and `sched` are
    absent because they are not on the list, not because somebody remembered them --
    and `reachy_language_tutor.tools` is absent so this module cannot reach
    ToolDependencies and start setting an identity from somewhere else.
    """
    tree = ast.parse(Path(current_learner.__file__).read_text(encoding="utf-8"))
    imported = {
        node.module if isinstance(node, ast.ImportFrom) else alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    assert imported <= PERMITTED_IMPORTS, sorted(imported - PERMITTED_IMPORTS)


def test_the_outcome_never_renders_the_learner_id_when_printed() -> None:
    """The FrameCapture and FaceEmbedding sweep, applied to the third such type."""
    rendered = repr(RecognitionOutcome("a-real-person", "identified"))

    assert "a-real-person" not in rendered
    assert "identified" in rendered


def test_every_disposition_the_module_can_return_has_a_log_level() -> None:
    """A disposition with no level would reach an operator at whatever INFO means."""
    assert set(RECOGNITION_DISPOSITIONS) == set(main._DISPOSITION_LEVEL)


def test_the_disposition_vocabulary_covers_every_upstream_reason() -> None:
    """A reason added upstream must fail here rather than become a generic answer."""
    for reason in faces.CAPTURE_REASONS:
        assert reason in RECOGNITION_DISPOSITIONS, f"capture reason {reason} has no disposition"
    for reason in faces.EMBEDDING_REASONS:
        assert reason in RECOGNITION_DISPOSITIONS, f"embedding reason {reason} has no disposition"
    for reason in faces.MATCH_REASONS:
        assert reason in current_learner._FROM_MATCH, f"match reason {reason} has no disposition"


def test_no_learner_id_reaches_a_log_on_any_path(
    instance: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """These are children's households, and this is the module that knows who they are."""
    enrolled = _enrol(instance, "Ana", _unit(0))
    _sees(monkeypatch, _unit(0))

    with caplog.at_level(logging.DEBUG):
        for calibrated in (False, True):
            monkeypatch.setattr(current_learner, "THRESHOLD_CALIBRATED", calibrated)
            recognise_current_learner(media=_Camera(), camera_enabled=True, instance_path=instance)

    surface = " ".join(
        f"{record.getMessage()} {record.msg} {record.args}" for record in caplog.records
    )
    assert enrolled not in surface
    assert "Ana" not in surface
