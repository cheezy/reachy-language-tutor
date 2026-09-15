"""Tests for face matching: the decision that says who the robot is talking to.

No camera, no database, no model weights. The fixture vectors below are built on an
orthonormal basis, so every cosine similarity in this file is ANALYTIC -- a number we
chose, not one a model happened to produce. That is what lets both error directions
around the threshold be exercised on every run rather than only when hardware is
attached, and it is why a change to the threshold shows up here immediately.
"""

import ast
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

    A single-sided test passes for a threshold of zero. Two enrolled members, so the
    ordinary floor applies rather than the lone-member one; the runner-up is far away
    so the margin does not interfere with what this is measuring.
    """
    household = [_enrolled("ana", _unit(0)), _enrolled("ben", _unit(30))]
    floor = matching.FACE_MATCH_SIMILARITY_FLOOR

    below = match_faceprint(_at_similarity(floor - 0.01), household, embedding_model=MODEL)
    assert below.matched is False and below.reason == "no_one_close_enough"

    above = match_faceprint(_at_similarity(floor + 0.01), household, embedding_model=MODEL)
    assert above.matched is True and above.learner_id == "ana"


def test_a_household_of_one_is_held_to_a_higher_floor() -> None:
    """The state of every robot between the first enrolment and the second.

    With nobody else enrolled the margin has nothing to compare and is skipped, so the
    floor is the only control left. Measured before the fix: a candidate at
    floor + 1e-9 was matched. It now has to clear FACE_MATCH_LONE_MEMBER_FLOOR.

    Reverting to the shared floor makes the first assertion below return matched=True.
    """
    lone = [_enrolled("ana", _unit(0))]
    ordinary = matching.FACE_MATCH_SIMILARITY_FLOOR
    lone_floor = matching.FACE_MATCH_LONE_MEMBER_FLOOR

    assert lone_floor > ordinary, "the lone-member bar must be higher, or it protects nothing"

    just_over_the_ordinary_floor = match_faceprint(_at_similarity(ordinary + 0.01), lone, embedding_model=MODEL)
    assert just_over_the_ordinary_floor.matched is False
    assert just_over_the_ordinary_floor.reason == "no_one_close_enough"

    over_the_lone_floor = match_faceprint(_at_similarity(lone_floor + 0.01), lone, embedding_model=MODEL)
    assert over_the_lone_floor == MatchOutcome(matched=True, learner_id="ana")


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


def test_every_log_argument_in_the_package_is_a_permitted_shape() -> None:
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
    package = Path(faces.__file__).parent
    offenders: list[str] = []
    module_constants: set[str] = set()

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
            if getattr(node.func, "attr", None) not in {"debug", "info", "warning", "error", "exception", "log", "critical"}:
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

    assert offenders == [], (
        "a log argument here may only be a literal, len(...), or type(x).__name__: " + str(offenders)
    )

    # And the allow-list is not vacuous: each of these shapes is refused.
    for source, label in [
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
    ]:
        call = ast.parse(source).body[0].value
        assert not all(_is_permitted(a) for a in call.args), f"{label} must be refused"

    for source, label in [
        ("logger.warning('%d rows', len(enrolled))", "a count"),
        ("logger.warning('failed: %s', type(exc).__name__)", "an exception type"),
        ("logger.warning('nothing interpolated')", "a plain literal"),
        ("logger.warning('expected %d', EXPECTED_MATCH_DIMENSION)", "a named module constant"),
    ]:
        call = ast.parse(source).body[0].value
        assert all(_is_permitted(a) for a in call.args), f"{label} must be permitted"

    # And the keyword path, which is where both reviews got a learner id out.
    for source, label in [
        ("logger.warning('scores', extra={'vector': list(candidate)})", "extra= carrying a vector"),
        ("logger.warning('failed', exc_info=exc)", "exc_info printing the whole traceback"),
    ]:
        call = ast.parse(source).body[0].value
        assert not all(_is_permitted(k.value) for k in call.keywords), f"{label} must be refused"

    # exc_info=True is a LITERAL, so the shape rule alone lets it through while it dumps
    # the live traceback. Pinned separately because it is the one case where a permitted
    # shape is still a leak, and a review found it by executing exactly this.
    for source in ("logger.warning('x', exc_info=True)", "logger.warning('x', stack_info=True)"):
        call = ast.parse(source).body[0].value
        assert all(_is_permitted(k.value) for k in call.keywords), "the literal itself is a permitted shape"
        assert {k.arg for k in call.keywords} & {"exc_info", "stack_info"}, (
            "which is why these two are refused by name rather than by shape"
        )


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

    for path in sorted(source_root.rglob("*.py")):
        if path.is_relative_to(Path(faces.__file__).parent):
            continue  # the package itself defines these
        text = path.read_text(encoding="utf-8")
        if "match_faceprint" in text and "THRESHOLD_CALIBRATED" not in text:
            offenders.append(str(path.relative_to(source_root)))

    assert offenders == [], (
        "these call match_faceprint without consulting faces.THRESHOLD_CALIBRATED; "
        "an uncalibrated threshold must change behaviour, not just be documented: " + str(offenders)
    )
