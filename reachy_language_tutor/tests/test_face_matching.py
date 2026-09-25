"""Tests for face matching: the decision that says who the robot is talking to.

No camera, no database, no model weights. The fixture vectors below are built on an
orthonormal basis, so every cosine similarity in this file is ANALYTIC -- a number we
chose, not one a model happened to produce. That is what lets both error directions
around the threshold be exercised on every run rather than only when hardware is
attached, and it is why a change to the threshold shows up here immediately.
"""

import ast
import sys
import math
import inspect
from pathlib import Path

import pytest

from reachy_language_tutor import faces
from reachy_language_tutor.faces import matching
from reachy_language_tutor.faces.matching import MatchOutcome, EnrolledFaceprint, match_faceprint


DIMENSION = 128
MODEL = "opencv_sface_2021dec_fp32"


def _unit(index: int) -> list[float]:
    """A unit vector along one axis: orthogonal to every other axis, so cosine is 0."""
    vector = [0.0] * DIMENSION
    vector[index] = 1.0
    return vector


def _at_similarity(target: float, *, axis: int = 0, other: int = 1) -> list[float]:
    """A unit vector whose cosine similarity with _unit(axis) is exactly `target`.

    cos(v, e_axis) = v[axis] when v is a unit vector, so setting v[axis] = target and
    putting the remaining magnitude on a different axis gives an exact, chosen
    similarity. Checked by the test below rather than asserted here.
    """
    vector = [0.0] * DIMENSION
    vector[axis] = target
    vector[other] = math.sqrt(max(0.0, 1.0 - target * target))
    return vector


def _enrolled(learner_id: str, vector: list[float], model: str = MODEL) -> EnrolledFaceprint:
    return EnrolledFaceprint(learner_id=learner_id, embedding_model=model, dimension=DIMENSION, vector=tuple(vector))


def test_the_fixture_builder_produces_the_similarity_it_claims() -> None:
    """The fixtures are the measuring instrument, so they get checked first.

    Every threshold assertion below rests on _at_similarity being exact. If it drifts,
    those tests would still pass or fail -- just not for the reasons they name.
    """
    for target in (0.0, 0.3, 0.49, 0.5, 0.51, 0.85, 0.99):
        similarity = matching._cosine_similarity(_unit(0), _at_similarity(target))
        assert similarity is not None
        assert similarity == pytest.approx(target, abs=1e-12), target


def test_an_empty_household_matches_nobody_rather_than_raising() -> None:
    """The state every robot is in before anyone enrols."""
    outcome = match_faceprint(_unit(0), [], embedding_model=MODEL)

    assert outcome == MatchOutcome(matched=False, reason="nobody_enrolled")


def test_a_stranger_matches_nobody() -> None:
    """A visiting friend, a delivery, a face on a television.

    The stranger is orthogonal to both enrolled members -- similarity 0.0 -- so this
    fails if the floor is ever removed, not merely if it is lowered a little.
    """
    household = [_enrolled("ana", _unit(0)), _enrolled("ben", _unit(1))]

    outcome = match_faceprint(_unit(7), household, embedding_model=MODEL)

    assert outcome.matched is False
    assert outcome.reason == "no_one_close_enough"
    assert outcome.learner_id is None


def test_the_person_themselves_is_matched() -> None:
    """The feature has to actually work, or every other test here is satisfied by a stub."""
    household = [_enrolled("ana", _unit(0)), _enrolled("ben", _unit(1))]

    outcome = match_faceprint(_unit(0), household, embedding_model=MODEL)

    assert outcome == MatchOutcome(matched=True, learner_id="ana")


def test_a_face_just_below_the_floor_is_refused_and_just_above_it_is_matched() -> None:
    """Both directions at the boundary, which is what makes this a threshold test.

    A single-sided test passes for a threshold of zero. Two enrolled members, and the
    runner-up is far away so the margin does not interfere with what this is measuring.
    """
    household = [_enrolled("ana", _unit(0)), _enrolled("ben", _unit(30))]
    floor = matching.FACE_MATCH_SIMILARITY_FLOOR

    below = match_faceprint(_at_similarity(floor - 0.01), household, embedding_model=MODEL)
    assert below.matched is False and below.reason == "no_one_close_enough"

    above = match_faceprint(_at_similarity(floor + 0.01), household, embedding_model=MODEL)
    assert above.matched is True and above.learner_id == "ana"


def test_a_household_of_one_is_held_to_the_same_floor_in_both_directions() -> None:
    """The state of every robot between the first enrolment and the second.

    With nobody else enrolled the margin has nothing to compare and is skipped, so the
    floor is the only control left. Measured before the lone-member floor existed: a
    candidate at floor + 1e-9 was matched. There is now ONE floor, and it is the higher
    of the two former values, so a household of one is held to it exactly.
    """
    lone = [_enrolled("ana", _unit(0))]
    floor = matching.FACE_MATCH_SIMILARITY_FLOOR

    below = match_faceprint(_at_similarity(floor - 0.01), lone, embedding_model=MODEL)
    assert below == MatchOutcome(matched=False, reason="no_one_close_enough")

    above = match_faceprint(_at_similarity(floor + 0.01), lone, embedding_model=MODEL)
    assert above == MatchOutcome(matched=True, learner_id="ana")


def test_enrolling_somebody_unrelated_never_lowers_the_bar_for_a_stranger() -> None:
    """The margin protects one member from another, never a household from a stranger.

    There used to be two floors: a higher one for a household of one, and a lower one
    once anybody else was enrolled, on the reasoning that the second member brought the
    margin's protection. Against a STRANGER it brings none -- they have no real person
    in the household to beat -- so enrolling an unrelated sibling dropped the bar.
    Measured on synthetic vectors: a non-member at 0.551 to Ana was refused with Ana
    alone and matched AS Ana once an unrelated member was enrolled.

    Stated as the property rather than at one number, so it does not depend on what
    the floor is: for every similarity to Ana, adding a member the candidate is
    orthogonal to must not change the answer. Restoring the lone-member floor makes
    this fail at every similarity between the two floors.
    """
    for hundredths in range(30, 100):
        similarity = hundredths / 100
        candidate = _at_similarity(similarity, axis=0, other=1)
        alone = match_faceprint(candidate, [_enrolled("ana", _unit(0))], embedding_model=MODEL)
        with_an_unrelated_member = match_faceprint(
            candidate, [_enrolled("ana", _unit(0)), _enrolled("ben", _unit(30))], embedding_model=MODEL
        )
        assert with_an_unrelated_member == alone, (
            f"at similarity {similarity}, enrolling an unrelated member changed the answer "
            f"from {alone} to {with_an_unrelated_member}"
        )


def test_there_is_exactly_one_floor() -> None:
    """A second floor is how the stranger case came to depend on who else is enrolled."""
    floors = [name for name in vars(matching) if name.startswith("FACE_MATCH_") and name.endswith("_FLOOR")]

    assert floors == ["FACE_MATCH_SIMILARITY_FLOOR"], floors


def test_a_one_dimensional_vector_cannot_clear_the_floor_by_geometry() -> None:
    """Cosine on a 1-element vector is always +/-1, so it clears any floor.

    The faceprints table permits dimension 1..1024, so a buggy or poisoned enrolment
    could store a short row. The matcher refuses to be the place that turns one into a
    match: anything that is not the expected dimension is refused outright.
    """
    for length in (1, 2, 64, 127, 129):
        household = [EnrolledFaceprint("ana", MODEL, length, tuple([1.0] * length))]
        outcome = match_faceprint([1.0] * length, household, embedding_model=MODEL)
        assert outcome.matched is False, length
        assert outcome.reason == "wrong_dimension", length


def test_a_nan_similarity_fails_closed_rather_than_matching() -> None:
    """The comparison direction, pinned.

    `if best < floor: refuse` is FALSE for NaN, so it falls through to a match -- a NaN
    similarity would be answered as the person. Written positively it refuses. This
    exercises the property through the public entry point with a candidate whose
    validation and arithmetic could disagree.
    """
    assert not (float("nan") >= matching.FACE_MATCH_SIMILARITY_FLOOR), "NaN must not clear the floor"

    household = [_enrolled("ana", _unit(0)), _enrolled("ben", _unit(30))]
    outcome = match_faceprint([float("nan")] * DIMENSION, household, embedding_model=MODEL)
    assert outcome.matched is False
    assert outcome.reason == "unusable_vector"


def test_two_similar_enrolled_faces_never_resolve_to_each_other() -> None:
    """The sibling case, which is the realistic one rather than a corner case.

    Both siblings clear the floor comfortably. Without the margin the winner is decided
    by whichever fraction of a point the lighting gave us, and the robot hands one
    child the other's records. The answer must be the right person or nobody.
    """
    ana = _at_similarity(0.90, axis=0, other=2)
    ben = _at_similarity(0.86, axis=0, other=3)
    household = [_enrolled("ana", ana), _enrolled("ben", ben)]

    outcome = match_faceprint(_unit(0), household, embedding_model=MODEL)

    assert outcome.learner_id != "ben", "a sibling must never be answered for the other"
    if outcome.matched:
        assert outcome.learner_id == "ana"
    else:
        assert outcome.reason == "too_close_to_call"


def test_a_clear_winner_over_a_distant_runner_up_is_matched() -> None:
    """The margin must not refuse the ordinary case, or recognition never works."""
    household = [_enrolled("ana", _unit(0)), _enrolled("ben", _at_similarity(0.10, axis=0, other=5))]

    outcome = match_faceprint(_unit(0), household, embedding_model=MODEL)

    assert outcome == MatchOutcome(matched=True, learner_id="ana")


def test_two_faces_inside_the_margin_are_refused_even_when_both_clear_the_floor() -> None:
    """The margin, pinned on its own so it cannot be quietly set to zero."""
    margin = matching.FACE_MATCH_MARGIN
    household = [
        _enrolled("ana", _at_similarity(0.90, axis=0, other=2)),
        _enrolled("ben", _at_similarity(0.90 - margin / 2, axis=0, other=3)),
    ]

    outcome = match_faceprint(_unit(0), household, embedding_model=MODEL)

    assert outcome.matched is False
    assert outcome.reason == "too_close_to_call"


@pytest.mark.parametrize(
    ("label", "candidate"),
    [
        ("empty", []),
        ("a string", "not a vector"),
        ("bytes", b"not a vector"),
        ("not a sequence", 1.0),
        ("a NaN element", [float("nan")] + [0.0] * (DIMENSION - 1)),
        ("an infinite element", [float("inf")] + [0.0] * (DIMENSION - 1)),
        ("a boolean element", [True] + [0.0] * (DIMENSION - 1)),
        ("a string element", ["x"] + [0.0] * (DIMENSION - 1)),
    ],
)
def test_a_vector_that_cannot_be_compared_is_refused(label: str, candidate: object) -> None:
    """Refused, never silently compared.

    NaN is the one that matters most: every comparison against it is false, so an
    unguarded NaN candidate would sail through as "nobody" and look like correct
    behaviour while the real reason was arithmetic.
    """
    household = [_enrolled("ana", _unit(0))]

    outcome = match_faceprint(candidate, household, embedding_model=MODEL)  # type: ignore[arg-type]

    assert outcome.matched is False, label
    assert outcome.reason == "unusable_vector", label


def test_a_vector_of_the_wrong_dimension_is_refused_rather_than_compared() -> None:
    """A 64-element vector against 128-element rows is not a weak match, it is a bug.

    Its own reason code, not mixed_models. The two were one code, which told a caller
    a model upgrade was in progress whenever a length simply disagreed.
    """
    household = [_enrolled("ana", _unit(0))]

    outcome = match_faceprint([0.0] * 64, household, embedding_model=MODEL)

    assert outcome.matched is False
    assert outcome.reason == "wrong_dimension"


def test_a_household_on_another_model_is_refused_whole() -> None:
    """Refused entirely, not matched against the rows that happen to agree.

    Skipping the mismatched rows is the tempting version and it is the dangerous one:
    it leaves whoever remains as the best candidate, so a half-upgraded household
    would quietly match a look-alike instead of reporting that it cannot compare.
    """
    household = [
        _enrolled("ana", _unit(0), model="some_other_model_v2"),
        _enrolled("ben", _unit(1), model=MODEL),
    ]

    outcome = match_faceprint(_unit(0), household, embedding_model=MODEL)

    assert outcome.matched is False
    assert outcome.reason == "mixed_models"
    assert outcome.learner_id is None


def test_an_enrolled_row_with_an_unusable_vector_is_refused() -> None:
    """A corrupt stored row must not be scored as a very poor match."""
    household = [_enrolled("ana", [float("nan")] + [0.0] * (DIMENSION - 1))]

    outcome = match_faceprint(_unit(0), household, embedding_model=MODEL)

    assert outcome.matched is False
    assert outcome.reason == "unusable_vector"


def test_a_zero_vector_has_no_direction_and_is_refused() -> None:
    """Cosine is undefined at the origin; 0.0 would read as 'maximally dissimilar'."""
    assert matching._cosine_similarity([0.0] * DIMENSION, _unit(0)) is None

    household = [_enrolled("ana", _unit(0))]
    outcome = match_faceprint([0.0] * DIMENSION, household, embedding_model=MODEL)
    assert outcome.matched is False


def test_the_outcome_carries_no_score_for_a_caller_to_threshold() -> None:
    """The decision is made once, here.

    A score on this type is an invitation for a caller to apply its own threshold, and
    two thresholds in two places is how they come to differ.
    """
    fields = {field for field in MatchOutcome.__dataclass_fields__}

    assert fields == {"matched", "learner_id", "reason"}
    for forbidden in ("score", "similarity", "distance", "confidence", "candidates", "ranked"):
        assert forbidden not in fields


def test_every_reason_the_matcher_can_return_is_published() -> None:
    """A code a caller cannot anticipate is not an interface.

    Scans this module's own source for the reasons it actually returns, the same way
    the learner store's vocabulary is pinned, so a new code cannot be added without
    appearing in MATCH_REASONS.
    """
    source = Path(matching.__file__).read_text(encoding="utf-8")
    returned: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
        if name != "MatchOutcome":
            continue
        for keyword in node.keywords:
            if keyword.arg == "reason" and isinstance(keyword.value, ast.Constant):
                if isinstance(keyword.value.value, str):
                    returned.add(keyword.value.value)

    assert returned, "the scan found no reason codes at all, so it is proving nothing"
    assert returned == set(matching.MATCH_REASONS), sorted(returned ^ set(matching.MATCH_REASONS))


def test_the_threshold_and_margin_are_named_constants_with_a_justification() -> None:
    """Not inline numbers, and not bare ones.

    The task calls the threshold an authorization control. A number with no written
    reason beside it is a number the next person will tune until the tests pass, which
    is exactly the failure mode the task's pitfalls name.
    """
    source = Path(matching.__file__).read_text(encoding="utf-8")

    for constant in ("FACE_MATCH_SIMILARITY_FLOOR", "FACE_MATCH_MARGIN"):
        assert f"\n{constant} = " in source, f"{constant} must be a module-level named constant"
        preamble = source.split(f"\n{constant} = ")[0].rsplit("\n\n", 1)[-1]
        comment = [line for line in preamble.splitlines() if line.startswith("#")]
        assert len(comment) >= 5, f"{constant} needs a written justification, not a bare number"

    # And no inline decision numbers in the matching function itself.
    tree = ast.parse(source)
    function = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "match_faceprint")
    floats = [n.value for n in ast.walk(function) if isinstance(n, ast.Constant) and isinstance(n.value, float)]
    assert floats == [], f"match_faceprint must not carry inline thresholds; found {floats}"


def test_matching_imports_nothing_heavy() -> None:
    """Its testability is the point, so it is pinned rather than hoped for.

    numpy, cv2, the learner store and the hub all stay out of this module. If one
    arrives, these tests start needing what this module exists to avoid needing.
    """
    source = Path(matching.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    assert imported <= {"__future__", "math", "logging", "typing", "dataclasses"}, sorted(imported)


# Everything the faces package is permitted to call. An ALLOW-LIST, which the first
# version of this test only claimed to be: it listed eight writer names, and a review
# disproved it by adding tempfile.NamedTemporaryFile(...).write(...) -- a real PNG of
# a person's face written to disk -- which passed, because neither name was on the
# eight. Naming what is permitted is the repository's rule precisely because a list of
# forbidden spellings is only ever as complete as the last person to think about it.
#
# Kept TIGHT: exactly what the package calls today, derived by walking its own AST,
# not an aspirational superset. A review found the loose version still permitting
# `embedding_dimension_of` after that function was deleted, listing `sorted` twice,
# and carrying 18 names nothing calls -- every one of those a slot something could
# later occupy without review. Adding a name here is a deliberate act.
_PERMITTED_CALLS = frozenset(
    {
        # builtins and stdlib
        "float", "int", "len", "tuple", "type", "zip", "sorted", "isinstance",
        "sqrt", "fsum", "isfinite", "get", "items", "getLogger", "warning",
        "dataclass", "RuntimeError",
        # the face pipeline
        "create", "detect", "setInputSize", "feature", "alignCrop", "flatten",
        "tolist", "zeros", "hf_hub_download",
        # this package's own
        "MatchOutcome", "_cosine_similarity", "_is_usable",
        "load_face_models", "loaded_face_models",
        # capture.py, added deliberately when the frame source arrived. Each of these
        # reads or decides; none of them can put bytes anywhere.
        #   get_frame -- the SDK read itself. It RETURNS a frame and takes no
        #     destination, and test_face_capture pins it as the only method capture.py
        #     is allowed to call on the media handle at all.
        #   getattr   -- reads `camera` off the handle to tell "no camera" from "no
        #     frame yet". A getattr used to REACH a writer does not slip through here:
        #     `getattr(f, "write")(...)` is a Call whose func is a Call, so the guard
        #     records it as `None` and reports it.
        #   debug     -- the only log level this module uses; what may be PASSED to it
        #     is constrained separately by the log-shape allow-list below.
        #   object, range, ValueError, FrameCapture -- a sentinel, the attempt budget,
        #     the construction invariants, and this package's own result type.
        "get_frame", "getattr", "debug",
        "object", "range", "ValueError", "FrameCapture",
        # embedding.py's split of "no faceprint" into five reasons, added deliberately
        # when enrolment needed to tell an operator WHICH of them happened.
        #   FaceEmbedding -- the result type, the sibling of FrameCapture above.
        #   describe_face -- the one implementation; embed_face now wraps it.
        "FaceEmbedding", "describe_face",
        # enrollment.py, added deliberately when the flow arrived. Each of these reads,
        # decides, or writes NUMBERS to the learner database; none can put bytes
        # anywhere a frame could land, which is the property this guard exists for.
        #   EnrolmentOutcome, _refused -- this module's own result type and its builder.
        #   record_consent, record_consent_for_enrolment -- write the agreement. Text
        #     and codes only; the statement they store is a module constant.
        #   save_faceprint -- writes the 128 floats. It takes a vector and an instance
        #     path and has no parameter that could name a file to create.
        #   forget_learner -- deletes a learner row; it returns a count and takes no
        #     destination.
        #   capture_frame, _gather_faceprints, _medoid, capture_faceprint,
        #     faceprint_similarity, warm_face_models -- read a frame, compare numbers,
        #     choose one. None of them opens anything.
        #   info -- the third log level this package uses, beside capture.py's debug
        #     and the warning that embedding.py, matching.py and _roll_back all use.
        #     WHAT may be passed to it is constrained by the log-shape allow-list
        #     below, exactly as debug and warning are.
        #   sleep -- time.sleep between capture attempts. Takes a float, returns None.
        #   sum, max, any, append, enumerate -- arithmetic and list building over
        #     floats already in memory.
        #   _roll_back_left_something -- removes the learner an unfinished enrolment
        #     created and reports whether one was LEFT. It calls
        #     forget_learner and logs two fixed sentences; it takes a learner id and a
        #     path to the instance DIRECTORY, never a filename it could create.
        "EnrolmentOutcome", "_refused", "_roll_back_left_something",
        #   uuid4 -- mints the learner id BEFORE the guarded region, which is what
        #     lets an interrupt landing between record_consent's COMMIT and its
        #     return still name the row it has to undo. Returns a value; takes no
        #     destination and reads nothing.
        "uuid4",
        #   print -- the ONE place this package writes to a stream, and it is stderr
        #     on the interrupt path only. It is here rather than in main.py because
        #     that path RE-RAISES: the outcome object never reaches the caller, so
        #     the caller cannot report it, and the operator who just pressed Ctrl-C
        #     is the only person who can act on a learner row left behind.
        #     THE JUSTIFICATION THIS COMMENT FIRST CARRIED WAS FALSE, and is left
        #     recorded because the way it was false is the lesson. It argued that
        #     print could not reach a file "because `open` is not on this list, so
        #     there is no way to obtain one". `open` was never needed:
        #     `print(frame, file=sys.stderr)` needs no Call at all -- sys.stderr is
        #     an ast.Attribute, which this guard never inspects -- and
        #     `enrol 2> capture.log` then puts a frame on disk. A review disproved it
        #     by putting exactly that line in the package and watching both guards
        #     report zero offenders.
        #     What actually keeps it safe is the same thing that keeps logger safe:
        #     its ARGUMENTS are pinned, by the log-shape allow-list below, which now
        #     inspects print as well as the logger methods.
        "print",
        "record_consent", "record_consent_for_enrolment",
        "save_faceprint", "forget_learner",
        "capture_frame", "_gather_faceprints", "_medoid", "capture_faceprint",
        "faceprint_similarity", "warm_face_models",
        "info", "sleep",
        "sum", "max", "any", "append", "enumerate",
        # matching.py's overflow fix, added deliberately. Each vector is scaled by its
        # own largest element before the arithmetic, so a finite vector of huge values
        # can no longer make fsum raise or the similarity come back NaN.
        #   abs -- a float in, a float out.
        "abs",
        # enrollment.py's terminating-signal handling, added deliberately. SIGTERM and
        # SIGHUP used to kill an enrolment before its rollback ran, leaving a consented
        # learner behind. None of these can put bytes anywhere:
        #   getsignal, signal -- read and set a process's handler for a signal number.
        #   current_thread, main_thread -- identity checks; handlers install on main only.
        #   SystemExit -- what the handler raises so the rollback runs before exit.
        #   _signals_handled_by, _restorable -- this module's own context manager and
        #     its helper, which call only the names above.
        "getsignal", "signal", "current_thread", "main_thread", "SystemExit",
        "_signals_handled_by", "_restorable",
        # embedding.py's model integrity check, added deliberately. The cached model
        # files are hashed before OpenCV is given them. Reading, never writing:
        #   Path, read_bytes -- read the model file the hub download returned.
        #   sha256, hexdigest -- hash those bytes.
        #   _verify_model_file, FaceModelMismatch -- this module's check and its refusal.
        "Path", "read_bytes", "sha256", "hexdigest", "_verify_model_file", "FaceModelMismatch",
    }
)


def test_nothing_in_the_faces_package_writes_an_image() -> None:
    """The promise that no frame or crop is ever written, as an actual allow-list.

    Every call the package makes must be on _PERMITTED_CALLS. A new way of writing --
    tempfile, pathlib, pickle, cv2.imwrite, an f-string into open() -- fails because it
    is not permitted, not because somebody remembered to forbid it.

    Reverting the allow-list to the old deny-list of writer names makes this test pass
    with a NamedTemporaryFile write in embed_face, which is how the review found it.
    """
    package = Path(faces.__file__).parent
    offenders: list[str] = []

    # rglob, not glob: a review created faces/detail/leak.py containing cv2.imwrite
    # and open().write() and BOTH guards passed, because neither file was opened.
    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name is None or name not in _PERMITTED_CALLS:
                offenders.append(f"{path.name}:{node.lineno} calls {name}")

    assert offenders == [], (
        "the faces package may only call what _PERMITTED_CALLS names; "
        f"add it there deliberately if it is safe: {offenders}"
    )


def _rebindings_of_permitted_names(tree: ast.Module) -> list[tuple[int, str]]:
    """Every place a name on _PERMITTED_CALLS is bound to something that may not be it.

    The call guard above matches NAMES, so it is only as good as the promise that a
    permitted name means what it was permitted as. Two lines broke that promise and
    both passed it -- measured: `from cv2 import imwrite as create` then
    `create("face.jpg", frame)`, and `detect = open` then `detect(path, "wb")`.

    An allow-list of the two ways a permitted name may come into being in this
    package: a `def` or `class` of that name (the package's own function), or an import
    that binds a name under its OWN name (`from pathlib import Path`). Anything else
    that binds one -- an `as` alias, an assignment, a loop or `with` target, a
    parameter, an except name, a walrus, an attribute assignment -- is refused.
    """
    found: list[tuple[int, str]] = []

    def note(node: ast.AST, name: str | None) -> None:
        if name in _PERMITTED_CALLS:
            found.append((getattr(node, "lineno", 0), name))

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                original = alias.name.split(".")[0] if isinstance(node, ast.Import) else alias.name
                if alias.asname is not None and alias.asname != original:
                    note(node, alias.asname)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            note(node, node.id)
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
            note(node, node.attr)
        elif isinstance(node, ast.arg):
            note(node, node.arg)
        elif isinstance(node, ast.ExceptHandler):
            note(node, node.name)
    return found


def test_no_permitted_name_is_bound_to_something_else() -> None:
    """The call guard's names must mean what they were permitted as."""
    package = Path(faces.__file__).parent
    offenders = [
        f"{path.name}:{line} binds {name}"
        for path in sorted(package.rglob("*.py"))
        for line, name in _rebindings_of_permitted_names(ast.parse(path.read_text(encoding="utf-8")))
    ]

    assert offenders == [], offenders


@pytest.mark.parametrize(
    "source",
    [
        "from cv2 import imwrite as create\ncreate('face.jpg', frame)\n",
        "detect = open\ndetect('face.raw', 'wb')\n",
        "import cv2 as sleep\nsleep.imwrite('face.jpg', frame)\n",
        "for get in (open,):\n    get('face.raw', 'wb')\n",
        "def f(info=open):\n    info('face.raw', 'wb')\n",
        "self.create = cv2.imwrite\n",
    ],
)
def test_the_rebinding_guard_catches_each_way_of_renaming_a_writer(source: str) -> None:
    """Measured first: the call guard alone reported none of these writers."""
    assert _rebindings_of_permitted_names(ast.parse(source)), source



def _log_shape_offenders(package: Path) -> list[str]:
    """Shapes and counts only, as an ALLOW-LIST of what a log argument may BE.

    This was a deny-list of nine variable names, and a review disproved it by
    execution: `enrolled` and `scored` (which hold learner ids and full faceprint
    vectors) and `face`, `faces` and `probe` (which hold image data) are all live
    locals in these two modules and all passed. That is the same defect its sibling
    write-guard had, and the repository's rule is to name what is PERMITTED.

    So: every argument to every logger call here must be a literal, a `len(...)`, or a
    `type(x).__name__`. A new local called `best_per_learner` is refused because it is
    not one of those shapes, rather than because somebody remembered to forbid it.
    """
    offenders: list[str] = []
    module_constants: set[str] = set()

    def _stream_name(node: ast.AST) -> str | None:
        """Render `sys.stderr` as its dotted name, so a `file=` target can be checked."""
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            return f"{node.value.id}.{node.attr}"
        return None

    def _print_shape(node: ast.AST) -> str | None:
        """Name the two print argument shapes the interrupt path is allowed to use.

        Shapes, not values: anything that is not exactly one of these falls through
        to the ordinary log-shape rule and is refused unless it is a literal, a len()
        or a type name.
        """
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == "_REFUSALS"
            and isinstance(node.slice, ast.Constant)
            # A claim about the VALUE, not the shape. The first version stopped at
            # "a literal subscript of _REFUSALS" -- and the dict is mutable, so
            # `_REFUSALS["leak"] = described.vector` followed by printing that key
            # walked a faceprint past both guards, because a subscript ASSIGNMENT is
            # not a Call and nothing inspected one. That is the same defect class as
            # the original print rationale: a statement about a shape standing in for
            # a statement about what is behind it.
            # The whole BINDING, not one key. Resolving the key closed the new-key
            # form (`_REFUSALS["leak"] = vector`) and left its sibling open:
            # OVERWRITING an existing literal key with a vector satisfied a key test
            # and reached stderr. So the shape is permitted only in a module where
            # the dict is never mutated after its literal definition -- which closes
            # the family instead of one member of it, and is the same inversion the
            # project's deny-list rule keeps asking for.
            and not refusals_are_mutated
            and node.slice.value in literal_refusals
        ):
            return "_REFUSALS[<literal>]"
        if isinstance(node, ast.JoinedStr):
            interpolated = [part.value for part in node.values if isinstance(part, ast.FormattedValue)]
            if len(interpolated) == 1:
                only = interpolated[0]
                # The id the operator needs to clear an orphan, in either spelling
                # it has had: the local minted before the guarded region, and the
                # attribute of the returned outcome.
                #
                # A claim about the BINDING, not the spelling. The first version
                # matched the exact name and nothing else, and its comment boasted
                # that "a frame bound to a similarly-named local cannot ride
                # through" -- true as written, false in what it implied: a frame
                # bound to that EXACT name rode through both guards, proved by
                # inserting `learner_id = described.vector` above the print. Same
                # inversion as the _REFUSALS arm above, and the same defect class --
                # a statement about a shape standing in for one about a value.
                if isinstance(only, ast.Name) and only.id == "learner_id" and learner_id_is_trustworthy:
                    return "f-string of the learner id"
                if (
                    isinstance(only, ast.Attribute)
                    and only.attr == "learner_id"
                    and isinstance(only.value, ast.Name)
                    and only.value.id == "agreed"
                ):
                    return "f-string of the learner id"
        return None

    def _is_permitted(node: ast.AST) -> bool:
        """True when this expression can only ever yield a shape, a count or a type."""
        if isinstance(node, ast.Constant):
            return True
        if isinstance(node, ast.JoinedStr):
            # An f-string is permitted only if every interpolation inside it is.
            return all(
                _is_permitted(part.value) for part in node.values if isinstance(part, ast.FormattedValue)
            )
        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
            return name == "len" and all(True for _ in node.args)
        if isinstance(node, ast.Attribute):
            # type(x).__name__ -- the class name, never the value.
            if node.attr == "__name__":
                inner = node.value
                if isinstance(inner, ast.Call):
                    fname = inner.func.id if isinstance(inner.func, ast.Name) else None
                    return fname == "type"
            return False
        if isinstance(node, ast.BinOp):
            return _is_permitted(node.left) and _is_permitted(node.right)
        if isinstance(node, ast.Name):
            # A module-level constant bound to a LITERAL. Resolved against this file's
            # own top-level assignments, not inferred from the name being uppercase --
            # a review broke that version in one line with `LEARNER = best_learner;
            # logger.warning("%s", LEARNER)`, because case is a naming convention and
            # this arm has to be a statement about the VALUE.
            return node.id in module_constants
        return False

    # rglob, not glob: a review created faces/detail/leak.py containing cv2.imwrite
    # and open().write() and BOTH guards passed, because neither file was opened.
    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))

        # The names this file binds to literals at module level. That is what makes
        # the constant arm above a claim about values rather than about spelling.
        # Whether _REFUSALS is touched anywhere after its literal definition: a
        # subscript assignment, an augmented assignment, or a mutating method. Any
        # of those and the literal-key resolution below stops being a statement
        # about what the printed value IS.
        refusals_are_mutated = any(
            (
                isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign))
                and any(
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "_REFUSALS"
                    for target in (
                        node.targets if isinstance(node, ast.Assign) else [node.target]
                    )
                )
            )
            or (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "_REFUSALS"
                # An allow-list of the READS, not a list of the mutators. The first
                # version treated every method call as a mutation and refused the
                # module's own `_REFUSALS.get(reason, ...)`; a list of mutators
                # instead would have been the deny-list this project has been bitten
                # by four times, and would have missed popitem or |= or whatever a
                # future dict grows. Anything not known to be a read counts as one.
                and node.func.attr not in {"get", "keys", "values", "items", "copy"}
            )
            for node in ast.walk(tree)
        )

        # Whether every binding of `learner_id` in this module comes from something
        # that cannot be a frame: a uuid hex, a parameter, or an X.learner_id
        # attribute. Anything else and the f-string arm stops describing a value.
        def _is_an_id_producer(node: ast.AST) -> bool:
            if isinstance(node, ast.Attribute) and node.attr in {"hex", "learner_id"}:
                return True
            if isinstance(node, ast.BoolOp):
                return all(_is_an_id_producer(value) for value in node.values)
            if isinstance(node, ast.Name) and node.id == "learner_id":
                return True
            # A None sentinel, which is what a keyword default is initialised to.
            # It cannot be a frame, and refusing it would refuse the real module.
            if isinstance(node, ast.Constant) and node.value is None:
                return True
            return False

        learner_id_is_trustworthy = all(
            _is_an_id_producer(node.value)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign))
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
            if isinstance(target, ast.Name) and target.id == "learner_id"
        )

        # The _REFUSALS keys this module maps to literal strings. Resolved from the
        # file, so the permitted-shape arm above is a statement about values.
        literal_refusals = {
            key.value
            for statement in tree.body
            if isinstance(statement, (ast.Assign, ast.AnnAssign))
            for target in ([statement.target] if isinstance(statement, ast.AnnAssign) else statement.targets)
            if isinstance(target, ast.Name) and target.id == "_REFUSALS" and isinstance(statement.value, ast.Dict)
            for key, value in zip(statement.value.keys, statement.value.values)
            if isinstance(key, ast.Constant) and isinstance(value, (ast.Constant, ast.JoinedStr))
        }

        module_constants = {
            target.id
            for statement in tree.body
            if isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Constant)
            for target in statement.targets
            if isinstance(target, ast.Name)
        } | {
            statement.target.id
            for statement in tree.body
            if isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and isinstance(statement.value, ast.Constant)
        }

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            # print IS a sink here, and adding it is the fix for a false rationale
            # rather than a widening. When print was permitted in _PERMITTED_CALLS the
            # comment claimed it could not reach a file without `open`; a review
            # disproved that by execution -- `print(frame, file=sys.stderr)` needs no
            # Call, because sys.stderr is an Attribute, and `enrol 2> capture.log`
            # then writes the frame to disk. Its arguments have to be pinned exactly
            # as a logger's are, or the package has one sink nothing checks.
            #
            # An ast.Name arm as well as the attribute one, because print is a bare
            # name: matching only node.func.attr skipped it entirely.
            is_logger_call = getattr(node.func, "attr", None) in {
                "debug", "info", "warning", "error", "exception", "log", "critical"
            }
            # Both spellings. Matching only the bare Name left `builtins.print(...)`
            # skipped here AND waved through by the write-guard, which permits the
            # name `print` -- re-opening the frame-to-stderr bypass one attribute
            # lookup away from where it was closed.
            is_print_call = (isinstance(node.func, ast.Name) and node.func.id == "print") or (
                isinstance(node.func, ast.Attribute) and node.func.attr == "print"
            )
            if not (is_logger_call or is_print_call):
                continue
            if is_print_call:
                # THE OPERATOR'S TERMINAL IS NOT A LOG, and this is the one place in
                # the package that writes to it: the interrupt path, where a failed
                # rollback has left a person in the database and the only human who
                # can clear it is the one who just pressed Ctrl-C. They need the id
                # to pass to `enrol --remove`, so the id is permitted HERE and
                # nowhere else -- a learner id in a logger call is still refused by
                # the arm above, which is the rule the project actually states.
                #
                # Named shapes rather than a blanket pass for print, so a frame or a
                # vector still cannot go through it. The self-check below proves
                # both: print(face) and print(feature, file=sys.stderr) are refused.
                permitted_print_arguments = {
                    "_REFUSALS[<literal>]",
                    "f-string of the learner id",
                }
                for position, argument in enumerate(node.args):
                    shape = _print_shape(argument)
                    if shape not in permitted_print_arguments and not _is_permitted(argument):
                        offenders.append(f"{path.name}:{node.lineno} print arg {position} is not a permitted shape")
                for keyword in node.keywords:
                    if keyword.arg != "file" or _stream_name(keyword.value) not in {"sys.stderr", "sys.stdout"}:
                        offenders.append(f"{path.name}:{node.lineno} print keyword {keyword.arg} is not permitted")
                continue
            for position, argument in enumerate(node.args):
                if not _is_permitted(argument):
                    offenders.append(f"{path.name}:{node.lineno} arg {position} is not a permitted log shape")
            # KEYWORDS TOO. Both reviews broke the args-only version the same way:
            # `extra={"learner": learner_id}` sets a record attribute that structured
            # handlers emit verbatim, and `exc_info=exc` prints the whole traceback
            # including the exception's message. Neither is an arg.
            for keyword in node.keywords:
                # exc_info and stack_info are refused whatever their value, and that is
                # not an oversight in the shape rule -- it is the shape rule failing to
                # apply. `exc_info=True` IS a literal, so it satisfies _is_permitted,
                # while its effect is to dump the live exception's whole traceback into
                # the record: message, arguments, and any filesystem path inside them.
                # These keywords are switches that pull in context, not data.
                if keyword.arg in {"exc_info", "stack_info"}:
                    offenders.append(
                        f"{path.name}:{node.lineno} keyword {keyword.arg} dumps a traceback into the log"
                    )
                    continue
                if not _is_permitted(keyword.value):
                    label = keyword.arg or "**kwargs"
                    offenders.append(f"{path.name}:{node.lineno} keyword {label} is not a permitted log shape")

    return offenders


def _offences_in(source: str, tmp_path: Path) -> list[str]:
    """Run ONE synthetic module through the very rule the package scan uses.

    Through `_log_shape_offenders`, not through a copy of its logic. An earlier
    version of the print self-check re-implemented the rule in a subprocess, and the
    consequence was measured rather than imagined: disabling the real print arm left
    that self-check green, because it was checking its own copy. A guard whose
    self-check cannot detect the guard being switched off is decoration.
    """
    module = tmp_path / "probe.py"
    module.write_text(
        "import sys\nimport builtins\n"
        '_REFUSALS = {"rollback_failed": "could not be removed"}\n'
        "EXPECTED_MATCH_DIMENSION = 128\n"
        "def f(candidate, enrolled, scored, best_per_learner, face, feature, exc, learner_id, "
        "LEARNER, a, b, shot, described, profile, agreed, reason):\n    " + source + "\n",
        encoding="utf-8",
    )
    try:
        return _log_shape_offenders(tmp_path)
    finally:
        module.unlink()


def test_every_log_argument_in_the_package_is_a_permitted_shape() -> None:
    """The real package, scanned by the shared rule above."""
    assert _log_shape_offenders(Path(faces.__file__).parent) == []


def test_the_log_shape_rule_refuses_what_it_claims_to(tmp_path: Path) -> None:
    """The allow-list is not vacuous, checked through the whole rule.

    Every line here is a shape that has to be refused, and each was chosen because
    some version of this guard let it through. They run through the same function the
    package scan calls, so switching an arm off fails this test too.
    """
    must_refuse = [
        ("logger.warning('%s', candidate)", "a bare personal name"),
        ("logger.warning('%s', enrolled)", "the enrolled rows"),
        ("logger.warning('%s', scored)", "the scored list"),
        ("logger.warning('%s', best_per_learner)", "the per-learner scores"),
        ("logger.warning('%s', face)", "a detection box"),
        ("logger.warning(f'x {feature!r}')", "an f-string hiding a faceprint"),
        ("logger.warning('%s', exc)", "a raw exception, which can quote its argument"),
        ("logger.warning('%s', learner_id)", "a lowercase identifier that is not a constant"),
        ("logger.warning('%s', LEARNER)", "an UPPER_CASE rebind of a personal value"),
        ("logger.warning('%s %s', a, b)", "two bare names"),
        ("logger.warning('scores', extra={'vector': list(candidate)})", "extra= carrying a vector"),
        ("logger.warning('failed', exc_info=exc)", "exc_info printing the whole traceback"),
        ("logger.warning('x', exc_info=True)", "exc_info=True, a permitted SHAPE that still dumps a traceback"),
        # The print sink, which needs no open() to reach a file: `enrol 2> capture.log`
        # is the whole exploit, and sys.stderr is an Attribute the write-guard never sees.
        ("print(shot.frame, file=sys.stderr)", "a frame printed to stderr"),
        ("print(described.vector)", "a faceprint printed to stdout"),
        ('print(f"{described.vector}", file=sys.stderr)', "a faceprint smuggled through an f-string"),
        ("print(f'name={profile.display_name}', file=sys.stderr)", "a name in an f-string"),
        ("print('ok', file=open('/tmp/leak.log', 'w'))", "a print straight into a file"),
        ("print(_REFUSALS[reason])", "a refusal looked up by a variable rather than a literal"),
        (
            '_REFUSALS["leak"] = described.vector\n    print(_REFUSALS["leak"], file=sys.stderr)',
            "stuffing a vector into the permitted dict under a NEW key",
        ),
        (
            '_REFUSALS["rollback_failed"] = described.vector\n'
            '    print(_REFUSALS["rollback_failed"], file=sys.stderr)',
            "OVERWRITING an existing permitted key -- the sibling of the case above",
        ),
        (
            '_REFUSALS.update({"rollback_failed": described.vector})\n'
            '    print(_REFUSALS["rollback_failed"], file=sys.stderr)',
            "mutating the dict through a method rather than a subscript",
        ),
        ("builtins.print(shot.frame, file=sys.stderr)", "a frame through the builtins.print alias"),
        (
            'learner_id = described.vector\n    print(f"  learner id: {learner_id}", file=sys.stderr)',
            "a faceprint rebound to the exact name the id arm permits",
        ),
        ("builtins.print(profile.display_name)", "a name through the builtins.print alias"),
    ]
    for source, label in must_refuse:
        assert _offences_in(source, tmp_path), f"{label} must be refused, and was not"


def test_the_log_shape_rule_permits_what_the_package_actually_needs(tmp_path: Path) -> None:
    """The other direction, so the rule cannot pass by refusing everything."""
    must_permit = [
        ("logger.warning('%d rows', len(enrolled))", "a count"),
        ("logger.warning('failed: %s', type(exc).__name__)", "an exception type"),
        ("logger.warning('nothing interpolated')", "a plain literal"),
        ("logger.warning('expected %d', EXPECTED_MATCH_DIMENSION)", "a named module constant"),
        ("print('nothing was recorded')", "a fixed sentence"),
        ('print(_REFUSALS["rollback_failed"], file=sys.stderr)', "the refusal the interrupt path prints"),
        ('print(f"  learner id: {learner_id}", file=sys.stderr)', "the id the operator needs to clear an orphan"),
        ('print(f"  learner id: {agreed.learner_id}", file=sys.stderr)', "the same id via the outcome object"),
    ]
    for source, label in must_permit:
        assert _offences_in(source, tmp_path) == [], f"{label} must be permitted, and was refused"


def test_the_print_arm_still_refuses_a_frame_or_a_vector(tmp_path: Path) -> None:
    """The print arm, exercised through the REAL rule rather than a copy of it.

    This test used to shell out to a re-implementation of the rule, and the copy had
    already drifted: it answered ALLOWED for builtins.print of a frame, for
    builtins.print of a name, and for the _REFUSALS overwrite -- three of the four
    bypasses the real rule was hardened against -- so it could not have detected the
    real arm being switched off, which was the entire reason it was extracted. It
    now calls _offences_in, the same entry point the package scan uses.
    """
    forbidden = [
        "print(shot.frame, file=sys.stderr)",
        "print(described.vector)",
        'print(f"name={profile.display_name}", file=sys.stderr)',
        "print(feature.tolist())",
        'print("ok", file=open("/tmp/leak.log", "w"))',
        "print(_REFUSALS[reason])",
        "builtins.print(shot.frame, file=sys.stderr)",
    ]
    permitted = [
        'print(_REFUSALS["rollback_failed"], file=sys.stderr)',
        'print(f"  learner id: {learner_id}", file=sys.stderr)',
        'print("nothing was recorded")',
    ]

    for line in forbidden:
        assert _offences_in(line, tmp_path), f"the print arm let this through: {line}"
    for line in permitted:
        assert _offences_in(line, tmp_path) == [], f"the print arm refused a shape the flow needs: {line}"

def test_the_model_identifier_is_storable_by_the_learner_database() -> None:
    """The two tasks have to agree, and this is where that is checked.

    The faceprints table refuses an embedding_model containing anything outside
    A-Za-z0-9._- , which rules out the hub-style "opencv/face_recognition_sface". If
    this module ever advertises an id the store cannot hold, every enrolment fails at
    the last step with a caller-error code and no obvious cause.
    """
    from reachy_language_tutor.learners import store

    assert store._cannot_be_an_embedding_model(faces.EMBEDDING_MODEL_ID) is None
    assert 1 <= faces.EXPECTED_EMBEDDING_DIMENSION <= store._MAX_VECTOR_DIMENSION


def test_one_learner_enrolled_twice_is_still_matched() -> None:
    """The margin asks whether another PERSON is close, not another row.

    Comparing the top two ROWS made a learner with two stored faceprints refuse
    themselves: their two rows sit within the margin of each other, so the runner-up
    was the same person and the answer was too_close_to_call. The faceprints table's
    primary key makes one row per learner the norm, so this is defensive rather than
    reachable today -- but the code now means what the margin's comment says.

    Reverting to per-row scoring makes this return too_close_to_call.
    """
    household = [_enrolled("ana", _unit(0)), _enrolled("ana", _at_similarity(0.97, axis=0, other=4))]

    outcome = match_faceprint(_unit(0), household, embedding_model=MODEL)

    assert outcome == MatchOutcome(matched=True, learner_id="ana")


def test_a_second_person_inside_the_margin_still_refuses() -> None:
    """The per-person grouping must not weaken the sibling refusal it sits beside."""
    household = [
        _enrolled("ana", _unit(0)),
        _enrolled("ana", _at_similarity(0.98, axis=0, other=4)),
        _enrolled("ben", _at_similarity(0.95, axis=0, other=5)),
    ]

    outcome = match_faceprint(_unit(0), household, embedding_model=MODEL)

    assert outcome.matched is False
    assert outcome.reason == "too_close_to_call"


# --------------------------------------------------------------- the embedding side

# These need the real models, which means the library AND a network fetch the first
# time. Bound to the real cause and self-healing, the way tests/profile_lock.py binds
# its skips to config.LOCKED_PROFILE: the day the models are present these run by
# themselves, and nobody has to remember this comment exists.
#
# The first version of this task shipped NO test here at all, on the stated grounds
# that testing an embedding needs a photograph of a person and the repository forbids
# committing one. The second half of that is true and the conclusion was wrong: a
# synthetic array exercises determinism and dimension perfectly well, because neither
# claim is about a face. What a photograph would buy is the recognition quality
# question, which is what scripts/calibrate_faceprints.py exists for.
def _models_ready() -> bool:
    """Report whether the real face models can be loaded, without raising."""
    from reachy_language_tutor.faces import embedding

    if not embedding.FACE_EMBEDDING_AVAILABLE:
        return False
    return embedding.warm_face_models()


_MODELS_READY = _models_ready()
_needs_models = pytest.mark.skipif(not _MODELS_READY, reason="face models unavailable (no library, or no network)")


@_needs_models
def test_the_same_image_yields_the_same_embedding() -> None:
    """Acceptance criterion 1, and it needs no photograph.

    Determinism is a property of the model, not of the subject: a synthetic array
    exercises it exactly. If this ever fails, something in the pipeline has become
    non-deterministic -- a random crop, a changed model behind the pin -- and every
    stored faceprint would stop matching its owner.
    """
    import numpy as np
    from reachy_language_tutor.faces.embedding import load_face_models

    _, recognizer = load_face_models()
    crop = (np.random.default_rng(0).random((112, 112, 3)) * 255).astype(np.uint8)

    first = recognizer.feature(crop)
    second = recognizer.feature(crop)

    assert np.array_equal(first, second), "the same input must produce the same faceprint"


@_needs_models
def test_an_embedding_has_the_dimension_the_module_advertises() -> None:
    """The module says 128; the model has to agree, or the database bound is wrong.

    EXPECTED_EMBEDDING_DIMENSION is what the loader refuses to come up without, and
    what the faceprints table's 1..1024 CHECK was chosen around. Read from the model
    rather than asserted about it.
    """
    import numpy as np
    from reachy_language_tutor.faces.embedding import EXPECTED_EMBEDDING_DIMENSION, load_face_models

    _, recognizer = load_face_models()
    crop = np.zeros((112, 112, 3), dtype=np.uint8)

    feature = recognizer.feature(crop)

    assert feature.shape == (1, EXPECTED_EMBEDDING_DIMENSION)
    assert feature.dtype.name == "float32", "the faceprints table stores little-endian float32"


@_needs_models
def test_a_frame_with_no_face_yields_no_faceprint() -> None:
    """'No face' is an answer, not an error, and it must not produce a vector.

    A vector derived from a wall compared against a household is how somebody gets
    matched to furniture.
    """
    import numpy as np
    from reachy_language_tutor.faces.embedding import embed_face

    assert embed_face(np.zeros((240, 320, 3), dtype=np.uint8)) is None
    assert embed_face((np.random.default_rng(1).random((240, 320, 3)) * 255).astype(np.uint8)) is None


@_needs_models
def test_a_produced_faceprint_is_plain_floats_the_store_can_hold() -> None:
    """The boundary: numpy dies inside embedding.py, and the store must accept the rest.

    This is where the two tasks meet. A numpy array reaching the store would be a
    different defect in a different module, so it is pinned here at the join.
    """
    import numpy as np
    from reachy_language_tutor.learners import store
    from reachy_language_tutor.faces.embedding import EMBEDDING_MODEL_ID, load_face_models

    _, recognizer = load_face_models()
    crop = (np.random.default_rng(2).random((112, 112, 3)) * 255).astype(np.uint8)
    vector = tuple(float(v) for v in recognizer.feature(crop).flatten().tolist())

    assert all(type(value) is float for value in vector), "plain floats, never numpy scalars"
    assert store._pack_vector(vector) is not None, "the store must be able to pack what this produces"
    assert store._cannot_be_an_embedding_model(EMBEDDING_MODEL_ID) is None


@_needs_models
def test_a_frame_the_camera_actually_produces_goes_straight_into_embed_face() -> None:
    """The two halves of recognition meet here: what capture returns is what embedding takes.

    The shape and dtype are not invented for the test. Measured on 2026-09-14 against
    the desktop app's mockup simulation, `media.get_frame()` returned a (720, 1280, 3)
    uint8 BGR array, and `faces.capture_frame` hands that object back untouched. So an
    array of exactly that description must be something `embed_face` can be handed with
    no conversion at the call site -- which is the acceptance criterion this pins.

    A blank frame has no face in it, so `None` is the RIGHT answer; what is being
    asserted is that it comes back as an answer rather than as an exception about
    shapes, channels or dtype.
    """
    import numpy as np

    from reachy_language_tutor.faces import capture_frame, embed_face

    measured_frame = np.zeros((720, 1280, 3), dtype=np.uint8)

    class TheCameraAsMeasured:
        camera = object()

        def get_frame(self) -> object:
            return measured_frame

    shot = capture_frame(TheCameraAsMeasured(), camera_enabled=True)

    assert shot.usable
    assert shot.frame is measured_frame, "capture must not convert the frame on the way through"
    assert embed_face(shot.frame) is None, "a blank frame has no face; the point is that it did not raise"


def test_the_embedding_tests_are_not_silently_skipped_forever() -> None:
    """A skip nobody notices is a test that does not exist.

    Reports the state plainly so a run where the models were unavailable is visibly
    different from one where they were exercised. Never fails on the skip itself --
    a developer with no network must still get a green suite.
    """
    if not _MODELS_READY:
        pytest.skip("face models unavailable here; the four tests above did not run")
    from reachy_language_tutor.faces.embedding import loaded_face_models

    assert loaded_face_models() is not None


def test_the_threshold_says_out_loud_whether_it_has_been_calibrated() -> None:
    """An unvalidated authorization control must be readable at runtime, not just in a comment.

    A caller deciding whether to act on a recognition -- W29 sets the current learner
    from one -- can ask this rather than discovering the state from a source comment.
    The flag is False until somebody runs scripts/calibrate_faceprints.py against real
    faces and records the numbers; flipping it without that is the "tune until it looks
    fine" failure the task's pitfalls name.

    This test asserts the flag EXISTS and is a bool, not that it is False -- the day it
    is honestly True this must not start failing.
    """
    assert isinstance(faces.THRESHOLD_CALIBRATED, bool)
    assert "THRESHOLD_CALIBRATED" in faces.__all__, "a caller must reach it through the boundary"

    source = Path(matching.__file__).read_text(encoding="utf-8")
    if faces.THRESHOLD_CALIBRATED:
        # If somebody has flipped it, the numbers that justify it must be here too.
        assert "false accept" in source.lower() or "false-accept" in source.lower(), (
            "THRESHOLD_CALIBRATED is True but no measured error rates are recorded beside the constant"
        )


def test_the_published_reference_threshold_is_cited_rather_than_invented() -> None:
    """The floor is justified against a number somebody else published, and it is named.

    Verified at the pinned model revision: sface.py carries
    `self._threshold_cosine = 0.363`. Ours sits above it deliberately, because this is
    not general face verification -- it decides whose records a child is shown.
    """
    source = Path(matching.__file__).read_text(encoding="utf-8")

    assert "0.363" in source, "the reference point must be named, not implied"
    assert matching.FACE_MATCH_SIMILARITY_FLOOR > 0.363, (
        "the floor must sit above the general-verification operating point"
    )


def test_the_three_copies_of_the_embedding_dimension_agree() -> None:
    """One number, three places, and nothing was checking they matched.

    matching.py states EXPECTED_MATCH_DIMENSION so it can stay free of the heavy
    dependency; embedding.py states EXPECTED_EMBEDDING_DIMENSION so the loader can
    refuse a model that stopped producing it; this test module states DIMENSION for
    its fixtures. That duplication is deliberate -- importing one from the other would
    drag cv2 into the pure module -- but deliberate duplication still needs a check,
    or a model change updates one copy and every recognition silently refuses with the
    whole suite green.
    """
    from reachy_language_tutor.faces.embedding import EXPECTED_EMBEDDING_DIMENSION

    assert matching.EXPECTED_MATCH_DIMENSION == EXPECTED_EMBEDDING_DIMENSION, (
        "the matcher and the embedder disagree about the vector length; "
        "every recognition would refuse with wrong_dimension and no test would say why"
    )
    assert DIMENSION == matching.EXPECTED_MATCH_DIMENSION, "this module's fixtures are the wrong length"


def _branches_on_the_calibration_flag(tree: ast.Module) -> bool:
    """True when THRESHOLD_CALIBRATED is read somewhere a branch depends on it.
    The substring version of this check accepted a comment, a docstring, an
    unused import or a logging argument -- anything at all, as long as the
    fifteen characters appeared. The task that wired recognition in said in as
    many words that satisfying this with a passing mention is worse than not
    having the check, so the question asked here is whether control flow
    actually depends on the flag.
    """
    # ONLY the tests of control-flow nodes, and whatever those tests are built from.
    # Collecting every BoolOp and Compare operand wherever it appeared accepted
    # `x = THRESHOLD_CALIBRATED and y` -- an unused assignment controlling nothing,
    # which is the same passing mention the plain `flag = THRESHOLD_CALIBRATED` is
    # refused for. A review caught that the self-check had pinned the hole as
    # intended behaviour, which would have taught the next reader it was by design.
    conditions: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.IfExp, ast.While, ast.Assert)):
            conditions.append(node.test)
    return any(
        isinstance(inner, (ast.Name, ast.Attribute))
        and (getattr(inner, "id", None) or getattr(inner, "attr", None)) == "THRESHOLD_CALIBRATED"
        for condition in conditions
        for inner in ast.walk(condition)
    )


def test_a_passing_mention_does_not_satisfy_the_calibration_check() -> None:
    """The check's own self-check, and the reason it was rewritten.

    The substring version accepted any file containing the fifteen characters. The
    task that wired recognition in said satisfying it with a passing mention is
    worse than not having it, so each of these is a mention that must NOT count and
    each was accepted by the old form.
    """
    mentions_only = [
        ("a comment", "# THRESHOLD_CALIBRATED is False\nmatch_faceprint(a, b)"),
        ("a docstring", '"""See THRESHOLD_CALIBRATED."""\nmatch_faceprint(a, b)'),
        ("an import", "from x import THRESHOLD_CALIBRATED\nmatch_faceprint(a, b)"),
        ("a log argument", "log(THRESHOLD_CALIBRATED)\nmatch_faceprint(a, b)"),
        ("an unused assignment", "flag = THRESHOLD_CALIBRATED\nmatch_faceprint(a, b)"),
    ]
    for label, source in mentions_only:
        assert not _branches_on_the_calibration_flag(ast.parse(source)), f"{label} must not satisfy the check"

    # And these were accepted while controlling nothing. `x = A and y` is an unused
    # assignment exactly as `flag = A` is, and pinning it as acceptable was the
    # self-check endorsing the hole rather than finding it.
    mentions_only += [
        ("a non-controlling boolean op", "x = THRESHOLD_CALIBRATED and y"),
        ("a non-controlling comparison", "x = THRESHOLD_CALIBRATED is True"),
    ]
    for label, source in mentions_only[-2:]:
        assert not _branches_on_the_calibration_flag(ast.parse(source)), f"{label} must not satisfy the check"

    branches = [
        ("an if", "if not THRESHOLD_CALIBRATED:\n    pass"),
        ("a conditional expression", "x = 1 if THRESHOLD_CALIBRATED else 2"),
        ("a comparison in a condition", "if THRESHOLD_CALIBRATED is True:\n    pass"),
        ("a boolean op in a condition", "if THRESHOLD_CALIBRATED and y:\n    pass"),
        ("a while", "while THRESHOLD_CALIBRATED:\n    pass"),
        ("an assert", "assert THRESHOLD_CALIBRATED"),
    ]
    for label, source in branches:
        assert _branches_on_the_calibration_flag(ast.parse(source)), f"{label} must satisfy the check"


def test_an_aliased_import_does_not_evade_the_calibration_check() -> None:
    """The evasion a review measured, pinned so the narrowing cannot recur.

    `from reachy_language_tutor.faces import match_faceprint as identify` followed by
    `identify(...)` was invisible to the AST rewrite while the substring form it
    replaced caught it -- so the rewrite, sold as a strengthening, was a narrowing.
    """
    aliased = ast.parse(
        "from reachy_language_tutor.faces import match_faceprint as identify\n"
        "def who(v, h):\n    return identify(v, h, embedding_model='m').learner_id\n"
    )
    plain = ast.parse("from reachy_language_tutor.faces import match_faceprint\nmatch_faceprint(a, b)\n")
    unrelated = ast.parse("def who():\n    return identify(1, 2)\n")

    assert _reaches_the_matcher(aliased), "an aliased import evaded the caller check"
    assert _reaches_the_matcher(plain)
    assert not _reaches_the_matcher(unrelated), "a same-named call with no import must not count"


def test_the_calibration_check_refuses_to_pass_with_no_callers() -> None:
    """It passed vacuously for two whole tasks, and its own docstring admitted it.

    Pinned so that deleting the recognition wiring fails loudly rather than
    returning this guard to the state where it guarded nothing.
    """
    from reachy_language_tutor import current_learner

    source = Path(current_learner.__file__).read_text(encoding="utf-8")
    assert "match_faceprint" in source, (
        "current_learner.py no longer calls match_faceprint, so the calibration check "
        "has gone back to passing vacuously"
    )


# Every module outside faces/ that may reach match_faceprint, by any spelling.
PERMITTED_MATCHER_CONSUMERS = frozenset({"current_learner.py"})


def _reaches_the_matcher(tree: ast.Module) -> bool:
    """True when this module can call the matcher, under any name it imported it as.

    ALIASES RESOLVED. The first version of this asked whether a Call's func was
    spelled `match_faceprint`, which a review broke in one line:
    `from reachy_language_tutor.faces import match_faceprint as identify` then
    `identify(...)` was invisible to it, while the substring check this replaced
    would have caught it. Narrowing a security guard while calling it a
    strengthening is the worst of the two outcomes, so the names it was imported
    under are collected first and the call is matched against those.
    """
    names = {"match_faceprint"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("reachy_language_tutor.faces"):
            names |= {alias.asname or alias.name for alias in node.names if alias.name == "match_faceprint"}
    return any(
        isinstance(node, ast.Call)
        and (getattr(node.func, "id", None) or getattr(node.func, "attr", None)) in names
        for node in ast.walk(tree)
    )


def test_any_caller_of_the_matcher_must_consult_whether_it_is_calibrated() -> None:
    """THRESHOLD_CALIBRATED must have a consumer, not just a reader.

    A security review's point, and a fair one: the flag is exported but nothing reads
    it, so a robot on an uncalibrated authorization control behaves exactly like one on
    a calibrated control. This module cannot fix that itself -- it must not touch the
    conversation, and the caller that sets the current learner is W29, which does not
    exist yet.

    What it CAN do is make the omission impossible to make quietly: any module in this
    package's app that calls match_faceprint must also mention THRESHOLD_CALIBRATED.
    Today there are no callers and this passes vacuously; the day W29 wires recognition
    into choosing a profile, it fails unless that code has at least looked at the flag.
    """
    source_root = Path(faces.__file__).resolve().parents[1]
    offenders: list[str] = []
    callers: list[str] = []

    # The predicate is module-level so the self-check below exercises THIS rule
    # rather than a second copy of it -- a lesson paid for on the sibling guard in
    # test_face_matching's print arm, where a re-implemented copy drifted and left
    # the real check switchable-off without failing.
    for path in sorted(source_root.rglob("*.py")):
        if path.is_relative_to(Path(faces.__file__).parent):
            continue  # the package itself defines these
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if not _reaches_the_matcher(tree):
            continue
        callers.append(str(path.relative_to(source_root)))
        if not _branches_on_the_calibration_flag(tree):
            offenders.append(str(path.relative_to(source_root)))

    # AND THE SET OF CALLERS IS ITSELF AN ALLOW-LIST. Matching on how the call is
    # SPELLED is a deny-list of shapes however carefully it is written -- a review
    # proved it by planting a module that did `import match_faceprint as identify`
    # and watching this check report four passes. Naming the modules that may reach
    # the matcher at all closes that family: a new consumer has to be added here,
    # where somebody has to think about the unmeasured control it is about to use.
    assert set(callers) <= PERMITTED_MATCHER_CONSUMERS, (
        f"these modules reach the face matcher and are not on the approved list: "
        f"{sorted(set(callers) - PERMITTED_MATCHER_CONSUMERS)}. The matcher's threshold is an "
        "authorization control that has never been measured; a new consumer is a decision."
    )

    # ANTI-VACUITY, and it is not decoration: this check passed for the whole of W26
    # and W27 with zero callers, which its own docstring admitted. The day the
    # wiring is deleted it must fail rather than go quietly green again.
    assert callers, (
        "no module outside faces/ calls match_faceprint, so this check proves nothing. "
        "Recognition is wired in current_learner.py; if that has been removed, this "
        "guard has stopped guarding anything."
    )
    assert offenders == [], (
        "these call match_faceprint without BRANCHING on faces.THRESHOLD_CALIBRATED; "
        "an uncalibrated threshold must change behaviour, not just be documented: " + str(offenders)
    )


@pytest.mark.parametrize(
    ("label", "left", "right"),
    [
        # Each of these made the unscaled arithmetic fail, measured before the fix.
        ("large, mixed signs: fsum raised '-inf + inf in fsum'", [1e200, -1e200] * (DIMENSION // 2), [1e200] * DIMENSION),
        ("large, same sign: fsum raised 'intermediate overflow'", [1e154] * DIMENSION, [1e154] * DIMENSION),
        ("very large: the similarity came back NaN", [1e200] * DIMENSION, [1e200] * DIMENSION),
    ],
)
def test_finite_vectors_of_any_size_never_raise_and_never_answer_nan(
    label: str, left: list[float], right: list[float]
) -> None:
    """match_faceprint promises never to raise; faceprint_similarity promises None, not NaN.

    _is_usable admits any FINITE value, so these vectors reach the arithmetic. Before
    the fix each one either raised out of math.fsum or produced NaN. The answer for
    each is now a finite similarity, and matching completes with a reason code.
    """
    similarity = faces.faceprint_similarity(left, right)
    assert similarity is not None and math.isfinite(similarity), (label, similarity)

    outcome = match_faceprint(left, [_enrolled("ana", right)], embedding_model=MODEL)
    assert isinstance(outcome, MatchOutcome), label


def test_scaling_does_not_change_what_a_similarity_is() -> None:
    """Cosine is scale-free, so the fix must give the same number at any scale.

    Identical directions at 1e200 and at 1.0 are the same face; so are two vectors at
    a chosen similarity multiplied by any positive factor.
    """
    assert faces.faceprint_similarity([1e200] * DIMENSION, [1e200] * DIMENSION) == pytest.approx(1.0, abs=1e-12)
    for scale in (1e-300, 1e-3, 1.0, 1e3, 1e300):
        scaled = [value * scale for value in _at_similarity(0.7)]
        assert faces.faceprint_similarity(scaled, _unit(0)) == pytest.approx(0.7, abs=1e-12), scale


def test_nothing_that_identifies_a_person_is_rendered_by_the_matching_types() -> None:
    """A privacy control, the one FaceEmbedding and FrameCapture already carry.

    The generated dataclass repr printed all 128 floats and the learner id of an
    EnrolledFaceprint, and the learner id of a MatchOutcome, into any log line,
    f-string or assertion diff that touched them.
    """
    learner_id = "learner-4f1d9c"
    element = 0.123456789
    rendered = [
        repr(EnrolledFaceprint(learner_id, MODEL, DIMENSION, tuple([element] * DIMENSION))),
        str(EnrolledFaceprint(learner_id, MODEL, DIMENSION, tuple([element] * DIMENSION))),
        repr(MatchOutcome(matched=True, learner_id=learner_id)),
        f"{MatchOutcome(matched=True, learner_id=learner_id)}",
    ]
    for text in rendered:
        assert learner_id not in text, text
        assert str(element) not in text, text
    # Still useful: the shape and the decision survive.
    assert "matched=True" in rendered[2] and "<set>" in rendered[2]
    assert f"<{DIMENSION} floats>" in rendered[0]


def test_a_model_file_that_is_not_the_pinned_one_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The revision pins what the hub serves; the digest pins what this robot loads.

    Measured before the fix: a cached detector blob replaced with a different ONNX
    model was loaded without complaint, and every faceprint would have been stamped
    with EMBEDDING_MODEL_ID regardless. The refusal happens before OpenCV is handed
    the file, and warm_face_models reports it by class, with no path.
    """
    import logging
    from types import SimpleNamespace

    from reachy_language_tutor.faces import embedding

    if not embedding.FACE_EMBEDDING_AVAILABLE:
        pytest.skip("cv2 is not installed")
    impostor = tmp_path / "alice-household" / "model.onnx"
    impostor.parent.mkdir()
    impostor.write_bytes(b"not the pinned model")
    monkeypatch.setattr(embedding, "_LOADED_MODELS", None)
    monkeypatch.setattr(embedding, "hf_hub_download", lambda *_args, **_kwargs: str(impostor))
    handed_to_opencv: list[str] = []

    class _Recording:
        """Stands in for OpenCV's two model factories, recording what they were handed."""

        @staticmethod
        def create(path: str, *_args: object) -> None:
            handed_to_opencv.append(path)
            raise AssertionError("the file reached OpenCV before it was checked")

    monkeypatch.setattr(embedding, "cv2", SimpleNamespace(FaceDetectorYN=_Recording, FaceRecognizerSF=_Recording))

    with pytest.raises(embedding.FaceModelMismatch):
        embedding.load_face_models()
    assert handed_to_opencv == [], "the file reached OpenCV before it was checked"

    with caplog.at_level(logging.WARNING):
        assert embedding.warm_face_models() is False
    assert "FaceModelMismatch" in caplog.text
    assert "alice-household" not in caplog.text
    assert embedding.loaded_face_models() is None


@_needs_models
def test_the_pinned_digests_are_the_digests_of_the_pinned_files() -> None:
    """The pins, re-measured wherever the real files are present.

    A digest that does not match the genuine file would make recognition unavailable
    on every robot; this catches a mistyped pin, or a revision bumped without it.
    """
    import hashlib

    from huggingface_hub import hf_hub_download
    from reachy_language_tutor.faces import embedding

    for repo, name, revision, expected in (
        (embedding._DETECTOR_REPO, embedding._DETECTOR_FILE, embedding._DETECTOR_REVISION, embedding._DETECTOR_SHA256),
        (
            embedding._RECOGNIZER_REPO,
            embedding._RECOGNIZER_FILE,
            embedding._RECOGNIZER_REVISION,
            embedding._RECOGNIZER_SHA256,
        ),
    ):
        path = Path(hf_hub_download(repo, name, revision=revision))
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, name


def _calibration_script():
    """Load scripts/calibrate_faceprints.py as a module; it is not part of the package."""
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "scripts" / "calibrate_faceprints.py"
    spec = importlib.util.spec_from_file_location("calibrate_faceprints_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_calibration_measures_the_visitor_and_the_household_of_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The table that decides THRESHOLD_CALIBRATED must include the cases the floor decides alone.

    The first version enrolled everybody and asked only who each image was: it never
    tested a household of one or anybody who was not enrolled -- measured, every call
    it made was about a household of all three people. Here "Cleo" is a visitor whose
    face clears the floor against Ana's and nobody else's, so with Cleo not enrolled
    she is answered as Ana. A calibration that cannot show that row cannot justify
    flipping the flag.
    """
    calibrate = _calibration_script()
    floor = matching.FACE_MATCH_SIMILARITY_FLOOR
    faces_by_image = {
        "ana": [_unit(0), _at_similarity(0.99, axis=0, other=10)],
        "ben": [_unit(1), _at_similarity(0.99, axis=1, other=11)],
        # Close enough to Ana to clear the floor against her, far from everybody else.
        "cleo": [_at_similarity(floor + 0.05, axis=0, other=20), _at_similarity(floor + 0.05, axis=0, other=21)],
    }
    root = tmp_path / "faces"
    for person, vectors in faces_by_image.items():
        (root / person).mkdir(parents=True)
        for index in range(len(vectors)):
            (root / person / f"{index}.jpg").write_bytes(b"")

    def describe(image: object) -> faces.FaceEmbedding:
        path = Path(str(image))
        return faces.FaceEmbedding(vector=tuple(faces_by_image[path.parent.name][int(path.stem)]))

    monkeypatch.setattr(calibrate, "_load", lambda path: str(path))
    monkeypatch.setattr(calibrate, "describe_face", describe)
    monkeypatch.setattr(calibrate, "warm_face_models", lambda: True)
    monkeypatch.setattr(calibrate, "FACE_EMBEDDING_AVAILABLE", True)

    assert calibrate.main(["calibrate", str(root)]) == 0
    printed = capsys.readouterr().out

    assert "stranger, one other person enrolled" in printed, printed
    assert "member, alone in the household" in printed, printed
    assert "VISITOR MATCHED" in printed, "a visitor answered as the lone member was not reported"
    assert "FALSE ACCEPT" in printed
