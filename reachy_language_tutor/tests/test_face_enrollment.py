"""Enrolment: consent first, a faceprint second, and nothing in between that can be lost.

The ordering is the task, so most of what is pinned here is about ORDER and about what
survives an interruption -- not about whether a faceprint comes out at the end.

WHAT THESE TESTS CANNOT PROVE, said once here rather than implied everywhere: they do
not prove recognition QUALITY. There are no photographs in this repository and there
must not be, so the embeddings in the unit tests are synthetic vectors whose
similarities are exact and chosen. What they prove is the plumbing and the ordering --
that consent is written before a frame is looked at, that the database refuses a
faceprint without one, that a failure after consent leaves nothing behind, and that a
frame the real detector cannot use is refused rather than stored. Whether a stored
faceprint actually recognises its owner in a real room is what
scripts/calibrate_faceprints.py exists for, and this file does not pretend otherwise.
"""

from __future__ import annotations
import ast
import math
import struct
import sqlite3
from pathlib import Path

import pytest

from reachy_language_tutor import faces
from reachy_language_tutor.faces import enrollment
from reachy_language_tutor.learners import store


DIMENSION = 128


@pytest.fixture()
def instance(tmp_path: Path) -> Path:
    """A prepared learner database at a temporary instance path."""
    assert store.ensure_learner_database(tmp_path).ready is True
    return tmp_path


def _unit(index: int) -> tuple[float, ...]:
    """A unit vector along one axis: orthogonal to every other axis, so cosine is 0."""
    vector = [0.0] * DIMENSION
    vector[index] = 1.0
    return tuple(vector)


def _at_similarity(target: float, *, axis: int = 0, other: int = 1) -> tuple[float, ...]:
    """A unit vector whose cosine similarity with _unit(axis) is exactly `target`."""
    vector = [0.0] * DIMENSION
    vector[axis] = target
    vector[other] = math.sqrt(max(0.0, 1.0 - target * target))
    return tuple(vector)


class _Camera:
    """A media handle that hands back the same object every time it is asked.

    A stand-in rather than a mock, the shape test_face_capture.py already uses: it
    declares `camera` so capture_frame does not report no_camera, and `get_frame` is
    the only method it has -- so a flow that called anything else on a media handle
    would fail here rather than in front of a household.
    """

    def __init__(self, frame: object = "a frame", frames: list[object] | None = None) -> None:
        self.camera = object()
        self._frame = frame
        self._frames = frames
        self.reads = 0

    def get_frame(self) -> object:
        self.reads += 1
        if self._frames is not None:
            return self._frames.pop(0) if self._frames else None
        return self._frame


def _counts(instance_path: Path) -> dict[str, int]:
    """Row counts for the three tables enrolment can touch."""
    connection = store.connect(instance_path)
    try:
        return {
            table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in ("learners", "consents", "faceprints")
        }
    finally:
        connection.close()


def _snapshot(instance_path: Path) -> list[tuple]:
    """Every row of every personal table, ordered, so "unchanged" can be asserted whole."""
    connection = store.connect(instance_path)
    try:
        rows: list[tuple] = []
        for table in sorted(store._PERSONAL_TABLES):
            rows.append((table, tuple(tuple(row) for row in connection.execute(f"SELECT * FROM {table}"))))
        return rows
    finally:
        connection.close()


def _describes(*vectors: tuple[float, ...]):
    """A describe_face stand-in that returns these faceprints in order, then no_face."""
    remaining = list(vectors)

    def describe(_image: object) -> faces.FaceEmbedding:
        if remaining:
            return faces.FaceEmbedding(vector=remaining.pop(0))
        return faces.FaceEmbedding(reason="no_face")

    return describe


def _five_agreeing() -> tuple[tuple[float, ...], ...]:
    """Five faceprints that all clear the matcher's floor against each other."""
    return tuple(_at_similarity(0.99, other=index + 1) for index in range(5))


@pytest.fixture()
def models_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let the flow run without loading real weights, for the tests that are about order."""
    monkeypatch.setattr(enrollment, "warm_face_models", lambda: True)


# --------------------------------------------------------- consent before the faceprint


def test_consent_is_recorded_before_the_faceprint_is_computed(
    instance: Path, monkeypatch: pytest.MonkeyPatch, models_ready: None
) -> None:
    """Acceptance criterion 1, proved by the order the calls actually happened in.

    Not by reading the source: the order is recorded as the flow runs, so a refactor
    that moved the capture above the consent would fail here rather than pass a
    structural check that no longer described the code.
    """
    order: list[str] = []
    real_record = enrollment.record_consent

    def recording(*args: object, **kwargs: object):
        order.append("consent")
        return real_record(*args, **kwargs)

    def describing(image: object) -> faces.FaceEmbedding:
        order.append("describe")
        return faces.FaceEmbedding(vector=_at_similarity(0.99, other=len(order)))

    real_save = enrollment.save_faceprint

    def saving(*args: object, **kwargs: object):
        order.append("save")
        return real_save(*args, **kwargs)

    monkeypatch.setattr(enrollment, "record_consent", recording)
    monkeypatch.setattr(enrollment, "describe_face", describing)
    monkeypatch.setattr(enrollment, "save_faceprint", saving)

    outcome = enrollment.enrol("Ana", _Camera(), granted_by="the_person_themselves", instance_path=instance)

    assert outcome.enrolled is True, outcome.reason
    assert order[0] == "consent", "the agreement has to be written before a camera is touched"
    assert order[-1] == "save", "the faceprint is the last thing to happen"
    assert order.count("consent") == 1
    assert set(order[1:-1]) == {"describe"}, "nothing else happens between agreeing and storing"


def test_an_enrolment_abandoned_after_consent_leaves_no_faceprint(instance: Path) -> None:
    """The interruption this whole ordering exists for, produced directly.

    Phase one is a real entry point rather than a simulated failure, so "interrupted
    between the two" is a state a test can simply be in.
    """
    agreed = enrollment.record_consent_for_enrolment(
        "Ana", granted_by="an_adult_of_the_household", instance_path=instance
    )

    assert agreed.recorded is True
    assert _counts(instance)["consents"] == 1
    assert _counts(instance)["faceprints"] == 0, "no camera was ever reached"
    assert store.get_faceprint(agreed.learner_id, instance_path=instance) is None


def test_a_faceprint_cannot_be_stored_for_a_learner_who_never_consented(instance: Path) -> None:
    """The gate itself, reached past the flow entirely.

    This is the guarantee that does not depend on anybody calling things in the right
    order: the learner is inserted by raw SQL, so no consent row exists, and the store
    is asked directly. It must refuse.
    """
    connection = store.connect(instance)
    try:
        connection.execute("INSERT INTO learners (id, display_name, created_at) VALUES ('nobody', 'N', 0)")
        connection.commit()
    finally:
        connection.close()

    outcome = store.save_faceprint("nobody", "opencv_sface_2021dec_fp32", _unit(0), instance_path=instance)

    assert outcome.saved is False
    assert outcome.reason == "no_consent"
    assert _counts(instance)["faceprints"] == 0
    assert store.get_faceprint("nobody", instance_path=instance) is None


def test_a_refused_faceprint_leaves_the_previous_one_byte_identical(instance: Path) -> None:
    """A refusal must not destroy what it refused to replace.

    save_faceprint replaces by DELETE then INSERT in one transaction. The consent gate
    makes the INSERT match nothing, so without the rollback the DELETE would stand and
    a refused save would ERASE the faceprint it declined to update -- silently, and on
    biometric data.
    """
    agreed = enrollment.record_consent_for_enrolment(
        "Ana", granted_by="the_person_themselves", instance_path=instance
    )
    learner_id = agreed.learner_id
    assert store.save_faceprint(learner_id, "m", _unit(0), instance_path=instance).saved is True
    before = store.get_faceprint(learner_id, instance_path=instance)

    connection = store.connect(instance)
    try:
        connection.execute("DELETE FROM consents WHERE learner_id = ?", (learner_id,))
        connection.commit()
    finally:
        connection.close()

    refused = store.save_faceprint(learner_id, "m", _unit(5), instance_path=instance)

    assert refused.saved is False and refused.reason == "no_consent"
    after = store.get_faceprint(learner_id, instance_path=instance)
    assert after is not None, "the refusal erased the faceprint it declined to replace"
    assert after == before, "the existing faceprint changed under a refused write"


def test_a_withdrawn_consent_refuses_a_new_faceprint(instance: Path) -> None:
    """withdrawn_at is honoured by the gate, which is why the column ships now.

    The writer that sets it is a later task, but the column and the clause that reads
    it have to arrive together: a column added later never reaches a robot that
    already has a database.
    """
    agreed = enrollment.record_consent_for_enrolment(
        "Ana", granted_by="the_person_themselves", instance_path=instance
    )
    connection = store.connect(instance)
    try:
        connection.execute("UPDATE consents SET withdrawn_at = granted_at + 1 WHERE learner_id = ?", (agreed.learner_id,))
        connection.commit()
    finally:
        connection.close()

    outcome = store.save_faceprint(agreed.learner_id, "m", _unit(0), instance_path=instance)

    assert outcome.saved is False and outcome.reason == "no_consent"
    assert _counts(instance)["faceprints"] == 0


# --------------------------------------------------------------- what the record says


def test_the_consent_record_says_who_to_what_and_when(instance: Path) -> None:
    """Acceptance criterion 2: a person can be shown what they agreed to.

    Every column is asserted, including the wording, because a record missing any one
    of them cannot answer the question it exists to answer.
    """
    before = store.utc_now_ms()
    agreed = enrollment.record_consent_for_enrolment(
        "Ana", granted_by="an_adult_of_the_household", instance_path=instance
    )
    after = store.utc_now_ms()

    records = store.get_consents(agreed.learner_id, instance_path=instance)
    assert records is not None and len(records) == 1
    record = records[0]

    assert record.learner_id == agreed.learner_id
    assert record.scope == "face_recognition"
    assert record.granted_by == "an_adult_of_the_household"
    assert record.granted_via == "operator_at_the_robot"
    assert record.statement_id == enrollment.CONSENT_STATEMENT_ID
    assert record.statement_text == enrollment.CONSENT_STATEMENT, "the words, not a reference to them"
    assert before <= record.granted_at <= after
    assert record.withdrawn_at is None


def test_the_stored_wording_survives_a_later_edit_to_the_constant(
    instance: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reason the text is stored and not just its id.

    An id alone would let a later edit rewrite what a household was told: the record
    would answer a question about today instead of about the day they agreed.
    """
    agreed = enrollment.record_consent_for_enrolment(
        "Ana", granted_by="the_person_themselves", instance_path=instance
    )
    original = enrollment.CONSENT_STATEMENT

    monkeypatch.setattr(enrollment, "CONSENT_STATEMENT", "We keep everything forever.")

    records = store.get_consents(agreed.learner_id, instance_path=instance)
    assert records is not None
    assert records[0].statement_text == original, "the stored wording followed the edited constant"
    assert "forever" not in records[0].statement_text


def test_the_words_shown_before_consent_are_the_module_constant() -> None:
    """Acceptance criterion 6: what a person is told is written down, not improvised.

    consent_statement() is the one source, and every claim in it is one this app
    keeps. The claims are pinned individually because a sentence removed from the
    notice is a promise quietly withdrawn from the household.
    """
    statement = enrollment.consent_statement()

    assert statement == enrollment.CONSENT_STATEMENT
    assert "does not keep the pictures" in statement
    assert "The numbers never leave this robot" in statement
    # And the honest counterpart, because the unqualified version of this claim was
    # false about the name. test_the_notice_matches_what_actually_leaves_the_robot
    # derives that from the tool code rather than from this sentence.
    assert "the name it calls you by" in statement
    assert "not a lock" in statement
    assert "an adult of the household has to agree for them" in statement
    assert "say no now" in statement
    # The deletion promise, and the route behind it. A security review found this
    # sentence had no invocable surface at all -- delete_faceprint was exported and
    # never called -- so the words and the command are pinned together here.
    assert "delete the numbers" in statement
    assert "ask whoever set this robot up" in statement
    # And what survives is named too. This paragraph has now been wrong TWICE: first
    # it promised deletion with no route at all, then the route arrived and it still
    # said "delete all of it", which --forget does not do -- main.py's own success
    # output contradicted it two lines later. A promise about deletion has to match
    # what the command leaves behind, not only what it removes.
    assert "The name and this agreement are kept" in statement
    assert "delete all of it" not in statement
    # What is stored BESIDE the numbers. Named in the notice because it is written by
    # the same transaction, and omitting it was the Cadillac Fairview failure in
    # miniature: consent technically obtained while the notice understated what was
    # collected.
    assert "the name you are being enrolled under" in statement
    assert "whether you agreed yourself or an adult agreed for you" in statement
    assert "does not save the name of the adult" in statement


def test_the_deletion_the_notice_promises_has_a_route_and_it_works(
    instance: Path, monkeypatch: pytest.MonkeyPatch, models_ready: None
) -> None:
    """The promise, executed. A security review found it had no code behind it at all.

    CONSENT_STATEMENT tells a household "you can ask whoever set this robot up to
    delete all of it, and it will be deleted". Before `enrol --forget` existed,
    delete_faceprint was exported from the learners package and called from NOWHERE --
    a household asking for their child's faceprint to be removed could only be served
    by somebody opening the SQLite file by hand.

    The consent row is deliberately KEPT: erasing it would destroy the answer to what
    was agreed and when, which is the record the household is owed. The notice says
    the numbers go, and the numbers are what go.
    """
    from reachy_language_tutor.main import handle_enrol_command

    monkeypatch.setattr(enrollment, "describe_face", _describes(*_five_agreeing()))
    enrolled = enrollment.enrol("Ana", _Camera(), granted_by="the_person_themselves", instance_path=instance)
    assert enrolled.enrolled is True
    assert store.get_faceprint(enrolled.learner_id, instance_path=instance) is not None

    class _Args:
        instance_path = str(instance)
        forget_learner_id = enrolled.learner_id
        show_learner = None
        enrol_name = None
        consent_from = None
        no_camera = False

    code = handle_enrol_command(_Args())

    assert code == 0
    assert store.get_faceprint(enrolled.learner_id, instance_path=instance) is None, "the promise was not kept"
    consents = store.get_consents(enrolled.learner_id, instance_path=instance)
    assert consents and len(consents) == 1, (
        "the record of what was agreed must survive -- deleting it would destroy the "
        "answer the household is owed about what they consented to"
    )


def test_what_forget_leaves_behind_is_what_the_notice_says_it_leaves(
    instance: Path, monkeypatch: pytest.MonkeyPatch, models_ready: None
) -> None:
    """The deletion paragraph, checked against the command rather than against itself.

    The sibling of test_the_notice_names_every_table_this_flow_writes, and it exists
    because that test only covered the COLLECTION paragraph -- which is how the
    deletion paragraph went on overclaiming after the collection one was fixed.
    """
    from reachy_language_tutor.main import handle_enrol_command

    monkeypatch.setattr(enrollment, "describe_face", _describes(*_five_agreeing()))
    enrolled = enrollment.enrol("Ana", _Camera(), granted_by="the_person_themselves", instance_path=instance)

    class _Args:
        instance_path = str(instance)
        forget_learner_id = enrolled.learner_id
        remove_learner_id = None
        show_learner = None
        enrol_name = None
        consent_from = None
        no_camera = False

    assert handle_enrol_command(_Args()) == 0

    survived = {
        "the faceprint": store.get_faceprint(enrolled.learner_id, instance_path=instance) is not None,
        "the name": store.get_profile(enrolled.learner_id, instance_path=instance) is not None,
        "the agreement": bool(store.get_consents(enrolled.learner_id, instance_path=instance)),
    }
    assert survived == {"the faceprint": False, "the name": True, "the agreement": True}, survived

    statement = enrollment.consent_statement()
    assert "delete the numbers" in statement, "the notice must name what goes"
    assert "The name and this agreement are kept" in statement, "and what stays"


def test_the_operator_is_shown_the_learner_id_a_failed_rollback_leaves_behind(
    instance: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The remedy names "the learner id printed below" -- so it has to be printed.

    Found by revert-proofing: deleting main.py's id line left every test green,
    because the sibling test asserts on enrol()'s OUTCOME and never on what the
    command prints. The sentence pointed at a value the operator had never been
    shown, and with no way to list learners the orphan was unreachable.
    """
    from reachy_language_tutor.main import handle_enrol_command

    monkeypatch.setattr(enrollment, "describe_face", lambda _image: faces.FaceEmbedding(reason="no_face"))
    # None, not 0: 0 now means "there was no row", which is a SUCCESS -- the
    # interrupt-before-the-commit case. None is "the store could not be read",
    # which is the only outcome that leaves something unaccounted for.
    monkeypatch.setattr(enrollment, "forget_learner", lambda *a, **k: None)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "yes")

    class _Robot:
        media = _Camera()

        def __init__(self, *a: object, **k: object) -> None:
            self.client = type("C", (), {"disconnect": lambda _self: None})()

        def __getattr__(self, name: str) -> object:
            raise AttributeError(name)

    _Robot.media = _Camera()
    monkeypatch.setattr("reachy_language_tutor.main.ReachyMini", _Robot)

    class _Args:
        instance_path = str(instance)
        forget_learner_id = None
        remove_learner_id = None
        show_learner = None
        enrol_name = "Ana"
        consent_from = "the-person-themselves"
        no_camera = False

    capsys.readouterr()
    code = handle_enrol_command(_Args())
    printed = capsys.readouterr().out

    assert code == 1
    assert "could not be removed" in printed, "the operator was not told a row remains"
    connection = store.connect(instance)
    try:
        left_behind = [row[0] for row in connection.execute("SELECT learner_id FROM consents")]
    finally:
        connection.close()
    assert left_behind, "this test needs a real orphan to have been left"
    assert left_behind[0] in printed, (
        "the refusal names 'the learner id printed below' and the id was never printed -- "
        "with no way to list learners, the orphan is unreachable"
    )


def test_an_orphaned_enrolment_can_actually_be_removed_by_the_command_it_names(
    instance: Path
) -> None:
    """The remedy in the rollback_failed sentence has to be one that works.

    A security review found the sentence named `--forget`, which deletes a FACEPRINT
    -- and a learner whose rollback failed has none by construction, so the command
    answered "had no faceprint. Nothing to delete." and exited 0, leaving the orphan
    in place. With no way to list learners (cross-learner reads are refused by
    design) that row was unreachable from the app surface entirely.
    """
    from reachy_language_tutor.main import handle_enrol_command

    orphan = enrollment.record_consent_for_enrolment(
        "Ana", granted_by="an_adult_of_the_household", instance_path=instance
    )
    assert orphan.recorded is True
    assert _counts(instance)["consents"] == 1

    assert "--remove" in enrollment._REFUSALS["rollback_failed"], "the sentence must name this command"

    class _Args:
        instance_path = str(instance)
        remove_learner_id = orphan.learner_id
        forget_learner_id = None
        show_learner = None
        enrol_name = None
        consent_from = None
        no_camera = False

    assert handle_enrol_command(_Args()) == 0
    assert store.get_profile(orphan.learner_id, instance_path=instance) is None
    assert _counts(instance)["consents"] == 0, "the orphan's agreement went with them"


def test_an_unreadable_database_is_not_reported_as_no_such_learner(
    instance: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two different failures must not be shown to a household as the same sentence.

    get_profile returns None both when nobody matches AND when the store cannot be
    read, and its own docstring says the two must not be described the same way. A
    household asking for their child's faceprint to be deleted, on a database that
    cannot be opened, was being told the child is not in the database -- the exact
    conflation delete_faceprint's None return exists to prevent, undone by a
    pre-check one layer up. Checked on every branch that looks a learner up, because
    it was the same bug three times.
    """
    from reachy_language_tutor.main import handle_enrol_command

    monkeypatch.setattr("reachy_language_tutor.learners.get_profile", lambda *a, **k: None)
    monkeypatch.setattr("reachy_language_tutor.learners.store_is_available", lambda *a, **k: False)

    for field in ("forget_learner_id", "remove_learner_id", "show_learner"):
        class _Args:
            instance_path = str(instance)
            forget_learner_id = None
            remove_learner_id = None
            show_learner = None
            enrol_name = None
            consent_from = None
            no_camera = False

        setattr(_Args, field, "whoever")
        capsys.readouterr()

        assert handle_enrol_command(_Args()) == 1

        printed = capsys.readouterr().out
        assert "could not be read" in printed, f"{field}: an unreadable store was reported as a missing learner"
        assert "No such learner" not in printed, f"{field}: said the learner does not exist"


def test_a_failed_rollback_is_told_to_the_operator_and_not_only_to_a_log(
    instance: Path, monkeypatch: pytest.MonkeyPatch, models_ready: None
) -> None:
    """A row left behind has to reach the terminal, because only the operator can act.

    If the capture fails AND the rollback fails, somebody is in the database having
    agreed to something, with no faceprint and no way to find out. Reporting that only
    through logger.warning left the operator reading "Nobody is in shot" with no hint
    that a person had just been recorded.
    """
    monkeypatch.setattr(enrollment, "describe_face", lambda _image: faces.FaceEmbedding(reason="no_face"))
    # None, not 0: 0 now means "there was no row", which is a SUCCESS -- the
    # interrupt-before-the-commit case. None is "the store could not be read",
    # which is the only outcome that leaves something unaccounted for.
    monkeypatch.setattr(enrollment, "forget_learner", lambda *a, **k: None)

    outcome = enrollment.enrol("Ana", _Camera(), granted_by="the_person_themselves", instance_path=instance)

    assert outcome.enrolled is False
    assert outcome.reason == "rollback_failed", "the operator was told the capture failed and not that a row remains"
    assert "could not be removed" in (outcome.error or "")
    assert "--remove" in (outcome.error or ""), (
        "the sentence has to name a remedy that WORKS -- --forget deletes a faceprint, "
        "and a learner whose rollback failed has none"
    )
    assert outcome.learner_id is not None, "and it has to say which learner, so --forget can be used"


@pytest.mark.parametrize("invisible", ["\u3164\u3164\u3164", "\u115f\u1160", "\u3164 \u3164"])
def test_a_name_that_renders_as_nothing_is_refused(instance: Path, invisible: str) -> None:
    """A display name that looks blank makes the consent read-back untrustworthy.

    Found by the security review probing the guard: U+3164 HANGUL FILLER and
    U+115F/U+1160 carry general category Lo, so they satisfied a rule written in terms
    of "is it a letter" and were stored as names that render as empty space in
    `enrol --show`. Nothing reaches a filesystem or a shell, so this is not a breach --
    it is a record somebody may later have to rely on that cannot be read back.

    WHAT THIS DELIBERATELY DOES NOT REFUSE, because an earlier version of this test
    got it wrong: "Ana" with a filler appended is NOT in the list. It renders as
    "Ana" and reads back perfectly well, so refusing it would be over-refusal. The
    rule is that a name must have at least one character that shows up, not that it
    may contain none of these -- which is also why it is written as an allow-list
    over visible letters rather than as a ban on four code points.

    The related confusable problem -- a Cyrillic "А" standing in for a Latin "A" --
    is deliberately NOT closed here. Both render, so both read back; telling two
    households' scripts apart is a different question from whether a record can be
    read at all, and it is not one an enrolment guard should answer by refusing a
    perfectly good Cyrillic name.
    """
    outcome = store.record_consent(
        invisible,
        scope="face_recognition",
        statement_id="v1",
        statement_text="w",
        granted_by="the_person_themselves",
        granted_via="operator_at_the_robot",
        instance_path=instance,
    )

    assert outcome.recorded is False
    assert outcome.reason == "name_not_usable"


def test_the_notice_matches_what_actually_leaves_the_robot() -> None:
    """The claim about EGRESS, which no guard checked until a review found it false.

    The notice said "nothing is sent anywhere and no copy leaves it" of everything it
    had just listed, the name included -- while tools/get_profile.py returns
    display_name to the model, the realtime sanitizer strips only the camera image,
    and the result is json.dumps'd to a hosted endpoint. A household would have
    agreed against a description of the data flow this app does not honour, which is
    the Cadillac Fairview shape the task's own security consideration names.

    It had not broken in practice only because recognition is not yet wired to the
    current learner, so the name that leaves today is the hard-coded one. It would
    have become true as a lie the day that wiring lands, with nothing failing.

    So the rule is pinned from the code rather than from the sentence: if any tool
    returns a learner's display name to the model, the notice must say the name is
    sent -- and must NOT contain an unqualified promise that nothing leaves.
    """
    import ast as _ast

    tools = Path(store.__file__).resolve().parents[1] / "tools"
    # The ATTRIBUTE as well as the key. Matching only a Constant "display_name" was
    # too tight, not too loose: a review ran four realistic rewrites through it --
    # {"name": profile.display_name}, {"learner": p.display_name}, an f-string, and
    # dataclasses.asdict(profile) -- and the first three were missed, which would
    # have let the notice revert to "nothing is sent" while the name still shipped.
    # asdict() still escapes, and that is said here rather than left as a claim this
    # guard does not support.
    def mentions_the_name(tree: _ast.Module) -> bool:
        return any(
            (isinstance(node, _ast.Constant) and node.value == "display_name")
            or (isinstance(node, _ast.Attribute) and node.attr == "display_name")
            for node in _ast.walk(tree)
        )

    # The predicate is checked against the renames it exists for BEFORE it is used,
    # because today's get_profile.py happens to contain both shapes -- the constant
    # key and the attribute -- so deleting the attribute arm changes nothing about
    # this file's verdict and would pass unnoticed until the day somebody renames
    # the key. Each of these was missed by the constant-only version.
    for rewrite in (
        'def f(p): return {"name": p.display_name}',
        'def f(p): return {"learner": p.display_name}',
        'def f(p): return {"greeting": f"Hi {p.display_name}"}',
    ):
        assert mentions_the_name(_ast.parse(rewrite)), f"a rename would escape the guard: {rewrite}"
    # And one that still escapes, said plainly rather than left as a claim this guard
    # does not support: a whole-object dump names no field at all.
    assert not mentions_the_name(_ast.parse("import dataclasses\ndef f(p): return dataclasses.asdict(p)")), (
        "if this now passes, the guard got stronger and this comment is stale"
    )

    returns_the_name = sorted(
        path.name
        for path in tools.glob("*.py")
        if mentions_the_name(_ast.parse(path.read_text(encoding="utf-8")))
    )
    statement = enrollment.consent_statement()

    if returns_the_name:
        assert "the name it calls you by" in statement, (
            f"{returns_the_name} return a learner's name to the model, and the consent "
            "notice does not tell the household their name is sent anywhere"
        )
        assert "Nothing is sent anywhere" not in statement, (
            "the notice makes an unqualified no-egress promise while a tool ships the name"
        )
    # A picture of the person leaves too, and the notice has to say so. tools/camera.py
    # base64-encodes a live JPEG and the realtime layer posts it as an input_image --
    # the sanitizer strips b64_im from the TEXT echo only, which is what made this
    # easy to miss two paragraphs after "it does not keep the pictures".
    sends_an_image = sorted(
        path.name
        for path in tools.glob("*.py")
        if any(
            isinstance(node, _ast.Constant) and node.value == "b64_im"
            for node in _ast.walk(_ast.parse(path.read_text(encoding="utf-8")))
        )
    )
    if sends_an_image:
        assert "takes a picture" in statement and "the same service" in statement, (
            f"{sends_an_image} ship a picture of the person to a hosted service, and the "
            "notice does not tell the household a picture ever leaves the house"
        )
        # And it must not describe the camera as something the PERSON triggers: it is
        # an ordinary model-callable tool with no confirmation gate, so the model
        # decides. "If you ask it to look at something" stated a trigger the code
        # does not enforce, which is the same defect as an understated sink.
        assert "If you ask it to look at something" not in statement, (
            "the notice makes the camera conditional on the person asking, and nothing enforces that"
        )

    # THE GENERAL CLAIMS, which are what stop this guard being a list of the leaks
    # somebody already found. Four reviews each found one more sink missing from an
    # enumerating paragraph -- the name, lesson progress, the camera, then the raw
    # audio and the remembered facts. These assert that the notice speaks in
    # categories, so a fifth sink is already covered by a sentence nobody has to
    # remember to update.
    assert "what its microphone hears" in statement, (
        "the notice must say the microphone stream leaves, not merely 'what you say' -- the "
        "realtime layer uploads every audio frame and turn detection is server-side"
    )
    assert "anyone else in the room" in statement, (
        "people who never read this notice are recorded too, and it has to say so"
    )
    assert "anything it has been asked to remember" in statement, (
        "stored facts about the person are prepended to the session instructions every session"
    )
    assert "Anything else it looks up about you" in statement, (
        "a category of personal data with no sentence of its own would be unmentioned egress"
    )

    # AND THE PART THAT MAKES THE ABOVE MORE THAN A LONGER LIST. The three phrase
    # assertions read nothing from the code, so they can never fail when a new sink
    # arrives -- a review proved it by adding a tool returning a home address, a
    # sibling's name and an email, and watching this test pass. So the set of tools
    # that reach personal data is DERIVED, and each one has to be accounted for
    # here, the same way test_the_notice_names_every_table_this_flow_writes accounts
    # for each personal table. A new one fails until somebody decides what the
    # household is told about it.
    covered_by_the_notice = {
        "get_profile.py": "the name it calls you by",
        "get_progress.py": "how your lessons are going",
        "start_lesson.py": "Anything else it looks up about you",
        "finish_lesson.py": "Anything else it looks up about you",
        "get_lesson_content.py": "Anything else it looks up about you",
        # Reads which lessons this person has finished, in order to put the last one
        # back on their path (D38). Same sentence as the other lesson tools: it tells
        # the household nothing new about what leaves the robot, because it looks up
        # exactly what get_progress already does.
        "redo_lesson.py": "Anything else it looks up about you",
        # Found by this guard on its first run, which is the point of deriving the
        # set: a shared helper the hand-written list would not have contained.
        "_language_choice.py": "Anything else it looks up about you",
    }
    reaches_personal_data = sorted(
        path.name
        for path in tools.glob("*.py")
        if any(
            isinstance(node, _ast.ImportFrom) and (node.module or "").startswith("reachy_language_tutor.learners")
            for node in _ast.walk(_ast.parse(path.read_text(encoding="utf-8")))
        )
    )
    # The derivation is checked before it is used, because an empty result would
    # make every assertion below vacuous -- and that is exactly how this guard's
    # predecessor passed while a tool returning a home address sat in the tree.
    assert reaches_personal_data, "the derivation found no personal-data tools, so this proves nothing"
    assert "get_profile.py" in reaches_personal_data, "the derivation missed the obvious one"

    unaccounted = [name for name in reaches_personal_data if name not in covered_by_the_notice]
    assert unaccounted == [], (
        f"{unaccounted} read a household member's data and are sent to a hosted service, and "
        "nothing here says what the consent notice tells the household about them. Add each to "
        "covered_by_the_notice with the sentence that covers it, or add a sentence."
    )
    for name in reaches_personal_data:
        assert covered_by_the_notice[name] in statement, (
            f"{name} is covered by a sentence the notice no longer contains"
        )
    # And no claim about what the far end does with any of it, which this repository
    # cannot verify and therefore must not assert.
    assert "not kept, here or there" not in statement

    # The faceprint claim is the one that must hold either way: no tool may return a
    # vector, and the notice says the numbers never leave. Keyed on a tool reaching
    # the faces package or naming a vector-shaped return, rather than on two words
    # happening to co-occur in one file -- the previous form passed anything that
    # returned {"embedding": ...}.
    assert "The numbers never leave this robot" in statement
    for path in tools.glob("*.py"):
        tree = _ast.parse(path.read_text(encoding="utf-8"))
        reaches_faces = any(
            isinstance(node, _ast.ImportFrom) and (node.module or "").startswith("reachy_language_tutor.faces")
            for node in _ast.walk(tree)
        )
        returns_a_vector = any(
            isinstance(node, _ast.Constant) and node.value in {"vector", "embedding", "faceprint"}
            for node in _ast.walk(tree)
        )
        assert not (reaches_faces or returns_a_vector), (
            f"{path.name} could return a faceprint to the model; the notice promises the numbers never leave"
        )


@pytest.mark.parametrize(
    "supplied",
    ["", "   ", "../../etc/passwd", "Ana\x00/tmp/x.jpg", "A" * 65, 42, None.__class__, b"Ana"],
)
def test_a_caller_supplied_learner_id_is_validated_like_every_other_argument(
    instance: Path, supplied: object
) -> None:
    """The sibling sweep. This parameter arrived late and skipped the line.

    display_name, scope, statement_id, statement_text, granted_by and granted_via
    are all checked; learner_id was added so that an interrupted enrolment could
    name the row it must undo, and it went straight into a primary key unchecked.
    Measured before the check existed: "", "../../etc/passwd", a 5000-character
    string, the int 42 and a NUL-smuggled path were all accepted and committed --
    and learners.id has no CHECK, unlike display_name, which gained one in this very
    change.
    """
    before = _snapshot(instance)

    outcome = store.record_consent(
        "Ana",
        scope="face_recognition",
        statement_id="v1",
        statement_text="w",
        granted_by="the_person_themselves",
        granted_via="operator_at_the_robot",
        learner_id=supplied,  # type: ignore[arg-type]
        instance_path=instance,
    )

    assert outcome.recorded is False, f"{supplied!r} was accepted as a learner id"
    assert _snapshot(instance) == before


def test_the_wording_and_its_id_cannot_drift_apart() -> None:
    """Editing a sentence without bumping the id must fail here, not pass unnoticed.

    The id exists so that "who agreed to face_recognition.v3?" has one answer. Under
    v2 the words were changed twice and the id never moved -- the rule was a sentence
    in a comment and nothing enforced it, which is precisely the silent rewrite the
    id is supposed to prevent. Now the digest is the enforcement.
    """
    import hashlib

    digest = hashlib.sha256(enrollment.CONSENT_STATEMENT.encode("utf-8")).hexdigest()
    digests = enrollment.CONSENT_STATEMENT_DIGESTS

    assert enrollment.CONSENT_STATEMENT_ID in digests, (
        f"{enrollment.CONSENT_STATEMENT_ID} has no recorded digest; add one"
    )
    assert digests[enrollment.CONSENT_STATEMENT_ID] == digest, (
        "the consent wording changed. If no household has consented under "
        f"{enrollment.CONSENT_STATEMENT_ID} yet, update its digest to {digest}. If one has, "
        "add a NEW id and digest instead -- a stored statement_text is the evidence of what "
        "somebody was told, and an id that names two different texts cannot answer who "
        "agreed to what."
    )
    # Append-only, and the FIRST version of this assertion did the opposite. It read
    # `digests == {"face_recognition.v3": digests["face_recognition.v3"]}`, which
    # pinned the map to exactly one key -- so adding a v4, the very path the whole
    # scheme exists to make cheap, failed with "an earlier entry was changed or
    # removed", and deleting v3 raised KeyError inside the assertion rather than
    # failing as the violation it is. A guard that forbids the intended action and
    # misnames the violation it does catch is worse than none.
    #
    # The recorded history is a literal here, so growth is permitted while an edit or
    # a deletion of any past entry fails, each naming its own reason.
    recorded = {"face_recognition.v3": "e34bb750b8c4af6f819c28599e65ff744b3d05c77e61431fd10e389cb55a1e32"}
    for identifier, recorded_digest in recorded.items():
        assert identifier in digests, f"{identifier} was removed; entries are append-only"
        assert digests[identifier] == recorded_digest, (
            f"{identifier}'s digest was changed. A past entry describes a wording somebody may "
            "already have consented under; add a new id instead of editing this one."
        )
    assert all(len(value) == 64 and set(value) <= set("0123456789abcdef") for value in digests.values())


def test_the_notice_names_every_table_this_flow_writes() -> None:
    """Rule (b): a thing enrolment stores has to be named in the words shown first.

    Written after a security review found "keep only those numbers" was false --
    the same transaction also wrote the person's display name and, for a child, the
    fact that an adult consented for them. Neither was mentioned.

    The tables are DERIVED from the store's own registry rather than listed here, so
    a table added to the personal set later is judged by this rule without anybody
    remembering to come back. Each one is mapped to the phrase that covers it, and a
    new table with no phrase fails -- which is the point: it forces whoever adds it
    to decide what the household is told.
    """
    covers = {
        "learners": "the name you are being enrolled under",
        "consents": "these words you are reading now",
        "faceprints": "a list of numbers",
        # Not written by enrolment at all -- the tutor writes it during a lesson, and
        # a notice about enrolment should not claim otherwise.
        "lesson_results": None,
    }
    assert set(covers) == set(store._PERSONAL_TABLES), (
        f"the personal tables changed to {sorted(store._PERSONAL_TABLES)}; decide what the "
        "household is told about the new one and add it here"
    )

    statement = enrollment.consent_statement()
    for table, phrase in covers.items():
        if phrase is not None:
            assert phrase in statement, f"{table} is written by enrolment and the notice does not mention it"


# ------------------------------------------------------------------------- refusing


@pytest.mark.parametrize("answer", ["no", "n", "", "   ", "nope", "y", "ye", "yes please", "1"])
def test_declining_at_the_first_step_leaves_the_database_exactly_as_it_was(
    instance: Path, monkeypatch: pytest.MonkeyPatch, answer: str
) -> None:
    """Acceptance criterion 4, by actually running the command and answering it.

    THE FIRST VERSION OF THIS TEST WAS VACUOUS: it snapshotted, ran no code at all,
    snapshotted again and asserted the two were equal. Nothing an operator could break
    in the decline path would have failed it. It is the same shape already caught in
    the two-faces test, and it is fixed the same way -- by driving the real surface.

    Every answer here is one that is NOT the word yes: the abbreviations, the empty
    line somebody gets by pressing return, and "yes please", which contains the word
    without being the answer. `"YES "` is deliberately NOT in this list -- the command
    strips and lowercases, so a shouted yes with a trailing space is a yes, and an
    earlier version of this test asserted it was a decline and was wrong about the
    code rather than finding a bug in it.
    """
    from reachy_language_tutor.main import handle_enrol_command

    before = _snapshot(instance)
    monkeypatch.setattr("builtins.input", lambda _prompt="": answer)

    class _Args:
        instance_path = str(instance)
        show_learner = None
        enrol_name = "Ana"
        consent_from = "the-person-themselves"
        no_camera = False

    # Building a robot would be the next thing the command does, so a decline that
    # reached it would fail here loudly rather than silently enrol.
    monkeypatch.setattr(
        "reachy_language_tutor.main.ReachyMini",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("a declined enrolment reached the camera")),
    )

    code = handle_enrol_command(_Args())

    assert code == 2, "a decline is its own exit code, not a failure and not a success"
    assert _snapshot(instance) == before, f"answering {answer!r} changed the database"


def test_the_operator_is_shown_the_consent_wording_before_being_asked(
    instance: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Acceptance criterion 6 at the surface a person actually reads.

    The constant existing is not the same as it reaching the person. This runs the
    command and asserts the words appeared on stdout BEFORE the prompt was answered.
    """
    from reachy_language_tutor.main import handle_enrol_command

    shown_at_prompt: list[str] = []

    def prompt(_text: str = "") -> str:
        shown_at_prompt.append(capsys.readouterr().out)
        return "no"

    monkeypatch.setattr("builtins.input", prompt)

    class _Args:
        instance_path = str(instance)
        show_learner = None
        enrol_name = "Ana"
        consent_from = "the-person-themselves"
        no_camera = False

    handle_enrol_command(_Args())

    assert shown_at_prompt, "the operator was never prompted"
    assert enrollment.CONSENT_STATEMENT in shown_at_prompt[0], (
        "the wording was not on screen at the moment the person was asked to agree"
    )


def test_a_failure_after_consent_removes_the_learner_it_created(
    instance: Path, monkeypatch: pytest.MonkeyPatch, models_ready: None
) -> None:
    """Acceptance criterion 4 at the harder point: after the agreement is already written.

    Leaving the row would mean somebody is in the database, having agreed to
    something, with no faceprint and no way to find out.
    """
    before = _snapshot(instance)
    monkeypatch.setattr(enrollment, "describe_face", lambda _image: faces.FaceEmbedding(reason="no_face"))

    outcome = enrollment.enrol("Ana", _Camera(), granted_by="the_person_themselves", instance_path=instance)

    assert outcome.enrolled is False
    assert outcome.reason == "no_face"
    assert _snapshot(instance) == before, "the rolled-back enrolment left something behind"


@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit, MemoryError])
def test_an_interrupted_capture_rolls_the_learner_back_and_re_raises(
    instance: Path, monkeypatch: pytest.MonkeyPatch, models_ready: None, interruption: type[BaseException]
) -> None:
    """The promise CONSENT_STATEMENT makes about stopping, with nothing kept.

    THIS TEST DID NOT EXIST when the rollback was added, and the review found that
    deleting the whole `except BaseException` block left the suite green -- so the
    fix for the notice's own sentence was unpinned, which is worse than the other
    unpinned things because that sentence is a promise to a household.

    BaseException and not Exception is the whole point, so the parametrisation is
    three classes that are NOT Exception subclasses: KeyboardInterrupt is what an
    operator's Ctrl-C actually raises when the person in front of the camera says
    stop, and it is exactly the scenario the sentence describes.

    Re-raising is asserted as well as the rollback: swallowing the interrupt would
    leave an operator unable to stop the command.
    """
    def interrupt(_image: object) -> faces.FaceEmbedding:
        raise interruption

    monkeypatch.setattr(enrollment, "describe_face", interrupt)
    before = _snapshot(instance)

    with pytest.raises(interruption):
        enrollment.enrol("Ana", _Camera(), granted_by="the_person_themselves", instance_path=instance)

    assert _snapshot(instance) == before, (
        "an interrupted enrolment left the learner or their consent behind -- "
        "CONSENT_STATEMENT promises nothing is kept if they stop"
    )


def test_an_interrupt_whose_rollback_fails_says_so_on_stderr_before_re_raising(
    instance: Path, monkeypatch: pytest.MonkeyPatch, models_ready: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The worst combination, and the one the household is most likely to reach.

    Somebody changes their mind mid-capture, the operator presses Ctrl-C, AND the
    rollback fails. A person is then in the database having agreed to something, with
    no faceprint. The first version of this handler reported that only to a log,
    justified by a comment saying there was nowhere to print -- which a review
    rejected, correctly: stderr is open inside the except block, and Ctrl-C is
    precisely the case where the operator is watching the terminal, because they are
    the one who just pressed it.

    The re-raise is asserted too: reporting must not swallow the interrupt.
    """
    def interrupt(_image: object) -> faces.FaceEmbedding:
        raise KeyboardInterrupt

    monkeypatch.setattr(enrollment, "describe_face", interrupt)
    # None, not 0: 0 now means "there was no row", which is a SUCCESS -- the
    # interrupt-before-the-commit case. None is "the store could not be read",
    # which is the only outcome that leaves something unaccounted for.
    monkeypatch.setattr(enrollment, "forget_learner", lambda *a, **k: None)
    capsys.readouterr()

    with pytest.raises(KeyboardInterrupt):
        enrollment.enrol("Ana", _Camera(), granted_by="the_person_themselves", instance_path=instance)

    printed = capsys.readouterr().err
    assert "could not be removed" in printed, "a failed rollback on the interrupt path was silent"
    assert "--remove" in printed, "and it must name the command that can clear it"

    connection = store.connect(instance)
    try:
        left_behind = [row[0] for row in connection.execute("SELECT learner_id FROM consents")]
    finally:
        connection.close()
    assert left_behind and left_behind[0] in printed, "the id of the row left behind has to be on screen"


def test_an_interrupt_between_the_commit_and_the_return_leaves_nothing(instance: Path) -> None:
    """The narrowest window, and the last one. Reproduced rather than reasoned about.

    Moving record_consent inside the try was not enough: the rollback still needed
    the returned outcome to be BOUND, so an interrupt landing after the rows
    committed and before that assignment -- in the connection close, or while the
    outcome object was being built -- left a learner and a consent row that nothing
    could name, printed nothing, and could not be cleared, since there is no way to
    list learners. A review reproduced it against a real database.

    The fix is that enrol mints the id itself, before the guarded region, so every
    failure after that point can undo the row whether or not the call returned. This
    test forces exactly that interleaving: the real consent write runs and commits,
    and the interrupt is raised before enrol ever sees the result.
    """
    real = enrollment.record_consent_for_enrolment
    before = _snapshot(instance)

    def commits_then_interrupts(*args: object, **kwargs: object):
        real(*args, **kwargs)
        raise KeyboardInterrupt

    enrollment.record_consent_for_enrolment = commits_then_interrupts  # type: ignore[assignment]
    try:
        with pytest.raises(KeyboardInterrupt):
            enrollment.enrol("Ana", _Camera(), granted_by="the_person_themselves", instance_path=instance)
    finally:
        enrollment.record_consent_for_enrolment = real  # type: ignore[assignment]

    assert _snapshot(instance) == before, (
        "an interrupt between the commit and the return left a learner or a consent row behind"
    )


def test_an_interrupt_before_anything_is_written_says_nothing_to_the_operator(
    instance: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Nothing written, nothing to report -- and the operator must not be alarmed.

    The rollback helper reports whether a row was LEFT, not whether one was removed,
    and the difference is this case: forget_learner answers 0 for a row that never
    existed. An earlier version tested `== 1` and so treated "there was nothing to
    undo" as a failed rollback, printing a warning about a row that did not exist.
    """
    real = enrollment.record_consent_for_enrolment
    before = _snapshot(instance)

    def interrupts_before_writing(*args: object, **kwargs: object):
        raise KeyboardInterrupt

    enrollment.record_consent_for_enrolment = interrupts_before_writing  # type: ignore[assignment]
    capsys.readouterr()
    try:
        with pytest.raises(KeyboardInterrupt):
            enrollment.enrol("Ana", _Camera(), granted_by="the_person_themselves", instance_path=instance)
    finally:
        enrollment.record_consent_for_enrolment = real  # type: ignore[assignment]

    assert _snapshot(instance) == before
    assert capsys.readouterr().err == "", "nothing was written, so there is nothing to warn about"


def test_an_unrecognised_consent_role_names_the_role_and_not_the_name(instance: Path) -> None:
    """The refusal points at the argument that was actually wrong.

    Unpinned until the review asked for it: the only previous check iterated
    ENROLMENT_REASONS and would have passed just as happily while the flow still
    answered name_not_usable and told an operator their perfectly good name was the
    problem.
    """
    before = _snapshot(instance)

    outcome = enrollment.enrol(
        "Ana", _Camera(), granted_by="whoever-was-nearest", instance_path=instance
    )

    assert outcome.enrolled is False
    assert outcome.reason == "consent_role_not_understood"
    assert "permission" in (outcome.error or ""), "the sentence must name the role, not the name"
    assert "not a name this robot can store" not in (outcome.error or "")
    assert _snapshot(instance) == before


@pytest.mark.parametrize(
    "name",
    ["\u30e4\u30de\u30c0\u30fb\u30bf\u30ed\u30a6", "\uff94\uff8f\uff80\uff65\uff80\uff9b\uff73", "Jean\u00b7Luc"],
)
def test_a_name_separated_by_a_middle_dot_is_accepted(instance: Path, name: str) -> None:
    """The three middle dots, pinned individually.

    Unpinned until the review asked: the katakana case in test_a_real_name_is_accepted
    separates with a SPACE, so removing every middle dot from _NAME_PUNCTUATION broke
    no test. Each of these is a separator a real keyboard produces -- U+30FB from a
    Japanese IME, U+FF65 from the halfwidth key, U+00B7 in several Latin conventions --
    and all three are category Po, so no category rule admits them.
    """
    outcome = store.record_consent(
        name,
        scope="face_recognition",
        statement_id="v1",
        statement_text="w",
        granted_by="the_person_themselves",
        granted_via="operator_at_the_robot",
        instance_path=instance,
    )

    assert outcome.recorded is True, f"{name!r} is a real name form and was refused: {outcome.reason}"


def test_a_frame_with_no_face_is_refused_and_stores_nothing(
    instance: Path, monkeypatch: pytest.MonkeyPatch, models_ready: None
) -> None:
    """Acceptance criterion 5, first half. The operator is told which problem it is."""
    monkeypatch.setattr(enrollment, "describe_face", lambda _image: faces.FaceEmbedding(reason="no_face"))

    outcome = enrollment.enrol("Ana", _Camera(), granted_by="the_person_themselves", instance_path=instance)

    assert outcome.enrolled is False
    assert outcome.reason == "no_face"
    assert "Nobody is in shot" in (outcome.error or "")
    assert _counts(instance)["faceprints"] == 0


def test_a_frame_with_two_faces_is_refused_and_stores_nothing(
    instance: Path, monkeypatch: pytest.MonkeyPatch, models_ready: None
) -> None:
    """Acceptance criterion 5, second half, and a DIFFERENT answer from the first.

    Two people in shot has no single right answer, and the operator's remedy is not
    the remedy for an empty frame -- so these must not collapse into one code. Before
    describe_face existed they did: embed_face returned None for both.
    """
    monkeypatch.setattr(enrollment, "describe_face", lambda _image: faces.FaceEmbedding(reason="several_faces"))

    outcome = enrollment.enrol("Ana", _Camera(), granted_by="the_person_themselves", instance_path=instance)

    assert outcome.enrolled is False
    assert outcome.reason == "several_faces"
    assert outcome.reason != "no_face", "the two refusals must stay distinguishable"
    assert "step out of view" in (outcome.error or "")
    assert _counts(instance)["faceprints"] == 0


class _Detector:
    """A detector that reports exactly the faces it was handed, at a chosen confidence."""

    def __init__(self, count: int, confidence: float = 0.99) -> None:
        self._count = count
        self._confidence = confidence

    def setInputSize(self, _size: object) -> None:  # noqa: N802 - the SDK spells it this way
        return None

    def detect(self, _image: object):
        # The SUCCESS FLAG is always 1, exactly as the real model returns it for a
        # frame with no face in it. That is what makes this a fair stand-in: a
        # describe_face that read the first value as a count would pass its other
        # tests and fail here.
        if self._count == 0:
            return 1, None
        return 1, [[0, 0, 10, 10, self._confidence] for _ in range(self._count)]


class _Recognizer:
    """A recognizer that returns a fixed faceprint for whatever it is given."""

    def alignCrop(self, _image: object, _face: object) -> object:  # noqa: N802
        return "crop"

    def feature(self, _crop: object):
        class _Feature:
            def flatten(self):
                return self

            def tolist(self):
                return [0.5] * DIMENSION

        return _Feature()


@pytest.mark.parametrize(
    ("count", "confidence", "expected"),
    [
        (0, 0.99, "no_face"),
        (1, 0.99, None),
        (2, 0.99, "several_faces"),
        (3, 0.99, "several_faces"),
        (1, 0.10, "not_confident"),
    ],
)
def test_describe_face_counts_the_faces_it_was_given(
    monkeypatch: pytest.MonkeyPatch, count: int, confidence: float, expected: str | None
) -> None:
    """The counting itself, which the flow-level refusal tests do NOT exercise.

    Those tests stub describe_face to return a reason, so they prove the FLOW handles
    each reason -- and prove nothing about whether describe_face produces the right
    one. Found by reverting `if found > 1` and watching the two-faces flow test pass
    over it. This is the test that fails for that mutation, because the count is what
    it varies.

    A stand-in detector rather than photographs: two real faces would need two real
    people's images, which this repository must not contain.
    """
    from reachy_language_tutor.faces import embedding

    monkeypatch.setattr(embedding, "FACE_EMBEDDING_AVAILABLE", True)
    monkeypatch.setattr(
        embedding, "loaded_face_models", lambda: (_Detector(count, confidence), _Recognizer())
    )

    class _Frame:
        shape = (240, 320, 3)

    described = embedding.describe_face(_Frame())

    if expected is None:
        assert described.usable, f"one confident face must yield a faceprint, got {described.reason}"
        assert described.vector is not None and len(described.vector) == DIMENSION
    else:
        assert described.reason == expected
        assert described.vector is None, "a refusal must carry no faceprint"


def test_a_single_bad_frame_cannot_become_a_faceprint(
    instance: Path, monkeypatch: pytest.MonkeyPatch, models_ready: None
) -> None:
    """The quality rule: four good views and one of somebody else is not one person.

    A faceprint built from a bad frame produces a recogniser that fails for that
    person forever, and the person it fails for cannot debug it. So the views have to
    agree with each other by the same floor the matcher will judge them against.
    """
    agreeing = [_at_similarity(0.99, other=index + 1) for index in range(4)]
    monkeypatch.setattr(enrollment, "describe_face", _describes(*agreeing, _unit(60)))

    outcome = enrollment.enrol("Ana", _Camera(), granted_by="the_person_themselves", instance_path=instance)

    assert outcome.enrolled is False
    assert outcome.reason == "frames_disagree"
    assert _counts(instance)["faceprints"] == 0


def test_the_stored_faceprint_is_one_the_model_produced_and_not_an_average(
    instance: Path, monkeypatch: pytest.MonkeyPatch, models_ready: None
) -> None:
    """The medoid, pinned: what is stored is one of the views, byte for byte.

    An averaged vector is not an embedding the model ever produced, and nothing says
    the mean of several views of a face lands anywhere the model would put that face.
    """
    views = _five_agreeing()
    monkeypatch.setattr(enrollment, "describe_face", _describes(*views))

    outcome = enrollment.enrol("Ana", _Camera(), granted_by="the_person_themselves", instance_path=instance)

    assert outcome.enrolled is True, outcome.reason
    stored = store.get_faceprint(outcome.learner_id, instance_path=instance)
    assert stored is not None

    # Compared after a float32 round trip, because that is what the column holds --
    # the table stores little-endian float32 and the fixtures are float64, so exact
    # equality would fail on the storage format rather than on the choice of vector.
    def as_stored(vector: tuple[float, ...]) -> tuple[float, ...]:
        packed = store._pack_vector(vector)
        assert packed is not None
        return struct.unpack(f"{store._VECTOR_FORMAT_PREFIX}{len(vector)}f", packed)

    assert stored.vector in {as_stored(view) for view in views}, (
        "the stored faceprint was not one of the observed views -- an average is not an "
        "embedding the model ever produced"
    )


@pytest.mark.parametrize(
    ("camera_enabled", "media", "expected"),
    [
        (False, None, "camera_disabled"),
        (True, None, "no_camera"),
        (True, "no-camera-attribute", "no_camera"),
        (True, "never-answers", "no_frame"),
    ],
)
def test_enrolment_refuses_cleanly_when_the_camera_is_unavailable(
    instance: Path,
    monkeypatch: pytest.MonkeyPatch,
    models_ready: None,
    camera_enabled: bool,
    media: object,
    expected: str,
) -> None:
    """Every way the camera can be absent, each keeping its own answer.

    Collapsing these onto one code would tell an operator who passed --no-camera that
    their robot has no camera, which sends them looking for hardware that is fine.
    """
    if media == "no-camera-attribute":

        class NoCamera:
            camera = None

            def get_frame(self) -> object:
                raise AssertionError("a handle with no camera must not be read")

        handle: object = NoCamera()
    elif media == "never-answers":
        handle = _Camera(frames=[])
    else:
        handle = media

    outcome = enrollment.enrol(
        "Ana", handle, granted_by="the_person_themselves", camera_enabled=camera_enabled, instance_path=instance
    )

    assert outcome.enrolled is False
    assert outcome.reason == expected
    assert outcome.error, "every refusal gives the operator a sentence"
    assert _counts(instance)["faceprints"] == 0
    assert _counts(instance)["consents"] == 0, "a failed enrolment rolled its consent back too"


# ------------------------------------------------------------------------ the name


@pytest.mark.parametrize(
    "name",
    [
        "",
        "   ",
        "Ana ",
        " Ana",
        "Ana\x00/Users/secret/kid-face.jpg",
        "\xa0",
        "Ana\nBen",
        "Ana\tBen",
        "Ana/Ben",
        "..",
        "A" * 201,
        "Ana‮Ben",
        "Ana​Ben",
        "\ud800",
        b"Ana",
        None,
        42,
    ],
)
def test_a_name_that_is_not_a_name_is_refused_before_anything_is_written(instance: Path, name: object) -> None:
    """The display-name guard, and the database left untouched by every refusal.

    The NUL case is the one worth naming: the learners CHECK does not close it on a
    robot that already has a database, because length() and trim() stop at the first
    NUL -- 'Ana' + NUL + a path satisfies the CHECK as a three-character name and is
    stored whole. Measured against the real DDL. The Python guard is what closes it.
    """
    before = _snapshot(instance)

    outcome = store.record_consent(
        name,  # type: ignore[arg-type]
        scope="face_recognition",
        statement_id="v1",
        statement_text="w",
        granted_by="the_person_themselves",
        granted_via="operator_at_the_robot",
        instance_path=instance,
    )

    assert outcome.recorded is False
    assert outcome.reason == "name_not_usable", "refused for the reason this test names"
    assert outcome.learner_id is None
    assert _snapshot(instance) == before


@pytest.mark.parametrize(
    "name",
    ["Ana", "José", "José", "小明", "Ann-Marie O'Hara", "O’Brien", "Ана", "علي", "ヤマダ タロウ", "Ana 2", "J. Smith"],
)
def test_a_real_name_is_accepted(instance: Path, name: str) -> None:
    """The other direction, and the reason the guard is not the model-name allow-list.

    `[A-Za-z0-9._-]` is right for a machine identifier and wrong for a person: it
    refuses accents, apostrophes and every non-Latin script. A household member's name
    is exactly the value that must not be ASCII-only.
    """
    outcome = store.record_consent(
        name,
        scope="face_recognition",
        statement_id="v1",
        statement_text="w",
        granted_by="the_person_themselves",
        granted_via="operator_at_the_robot",
        instance_path=instance,
    )

    assert outcome.recorded is True, f"{name!r} is a real name and was refused: {outcome.reason}"
    profile = store.get_profile(outcome.learner_id, instance_path=instance)
    assert profile is not None and profile.display_name == name


def test_the_tightened_learners_check_refuses_an_embedded_nul(tmp_path: Path) -> None:
    """The schema half, on a fresh install, using the function that is not NUL-blind.

    length(), trim() and replace() all stop at the first NUL and cannot see the hidden
    tail; instr() reads the whole value. Measured before the clause was written.
    """
    connection = sqlite3.connect(tmp_path / "probe.sqlite3")
    try:
        connection.executescript(
            (Path(store.__file__).resolve().parent / "schema.sql").read_text(encoding="utf-8")
        )
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            connection.execute(
                "INSERT INTO learners (id, display_name, created_at) VALUES ('x', ?, 0)",
                ("Ana\x00/Users/secret/kid-face.jpg",),
            )
        # And it does not over-refuse the names it exists to allow.
        connection.execute("INSERT INTO learners (id, display_name, created_at) VALUES ('y', 'José', 0)")
    finally:
        connection.close()


# ------------------------------------------------------------- who may consent


def test_who_may_consent_must_be_stated_every_time() -> None:
    """Acceptance criterion 7: the child question is answered, never inherited.

    A default here would mean somebody enrolling a child never had to say an adult
    agreed for them. What THIS test asserts is the two Python signatures and the
    published vocabulary; the command-line surface and the database constraint are
    covered by test_the_command_line_makes_who_consented_a_required_choice and
    test_a_consent_role_outside_the_two_is_refused_by_the_database below. The
    docstring used to claim all three here and assert one of them against a Python
    tuple, which is the overclaiming shape this file has now produced three times.
    """
    import inspect

    signature = inspect.signature(enrollment.enrol)
    assert signature.parameters["granted_by"].default is inspect.Parameter.empty
    assert signature.parameters["granted_by"].kind is inspect.Parameter.KEYWORD_ONLY

    phase_one = inspect.signature(enrollment.record_consent_for_enrolment)
    assert phase_one.parameters["granted_by"].default is inspect.Parameter.empty

    assert set(store.CONSENT_GRANTED_BY) == {"the_person_themselves", "an_adult_of_the_household"}


def test_the_command_line_makes_who_consented_a_required_choice() -> None:
    """The other surface, because the flow's signature does not bind the operator."""
    import sys
    import argparse
    from unittest.mock import patch

    from reachy_language_tutor.utils import parse_args

    with patch.object(sys, "argv", ["app", "enrol", "--name", "Ana", "--consent-from", "guesswork"]):
        with pytest.raises(SystemExit):
            parse_args()

    with patch.object(sys, "argv", ["app", "enrol", "--name", "Ana"]):
        args, _ = parse_args()
    assert args.consent_from is None, "no default: the command must ask rather than assume"
    assert isinstance(args, argparse.Namespace)


def test_a_consent_role_outside_the_two_is_refused_by_the_database(instance: Path) -> None:
    """The schema constraint, reached past the Python check that normally stops it."""
    connection = store.connect(instance)
    try:
        connection.execute("INSERT INTO learners (id, display_name, created_at) VALUES ('x', 'X', 0)")
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            connection.execute(
                "INSERT INTO consents "
                "(learner_id, scope, statement_id, statement_text, granted_by, granted_via, granted_at) "
                "VALUES ('x', 'face_recognition', 'v1', 'w', 'the_dog', 'operator_at_the_robot', 0)"
            )
    finally:
        connection.close()


# ---------------------------------------------------------- two enrolments at once


def test_two_enrolments_produce_two_learners_each_with_their_own_consent(instance: Path) -> None:
    """Two people enrolled back to back do not share or overwrite anything.

    The ids are generated rather than derived from the name, so two people with the
    same name are still two learners -- which is the safe direction: the matcher
    refuses between them rather than guessing.
    """
    first = enrollment.record_consent_for_enrolment("Ana", granted_by="the_person_themselves", instance_path=instance)
    second = enrollment.record_consent_for_enrolment("Ana", granted_by="the_person_themselves", instance_path=instance)

    assert first.learner_id != second.learner_id
    assert _counts(instance)["learners"] == 3, "two new learners beside the seeded one"
    assert _counts(instance)["consents"] == 2
    for learner_id in (first.learner_id, second.learner_id):
        records = store.get_consents(learner_id, instance_path=instance)
        assert records is not None and len(records) == 1


def test_two_enrolments_running_at_once_never_leave_a_learner_without_their_consent(
    instance: Path
) -> None:
    """The edge case named "two enrollments started at once", run concurrently for real.

    THE SECTION ABOVE USED TO CLAIM THIS and ran two sequential calls on one thread,
    which cannot exercise a transaction boundary at all.

    WHAT THIS PROVES, AND WHAT IT DOES NOT. It proves the invariant survives real
    contention: eight threads released together, and every learner that exists at the
    end has a consent row. It does NOT prove that BEGIN IMMEDIATE is what makes that
    true -- measured, this test still passes with BEGIN IMMEDIATE deleted, because
    sqlite3's implicit deferred transaction also wraps the two inserts. So the claim
    checked here is the invariant under load, not the mechanism. The mechanism --
    that the two inserts are one atomic unit -- is pinned by
    test_a_failed_consent_insert_leaves_no_learner_behind below, which is the test
    that fails when they are split apart.

    The INVARIANT is asserted rather than any timing: threads give no guarantee about
    who wins, and a test that depended on an ordering would be flaky by construction.
    """
    import threading

    names = [f"Person {index}" for index in range(8)]
    results: list[object] = []
    barrier = threading.Barrier(len(names))
    lock = threading.Lock()

    failures: list[BaseException] = []

    def enrol_one(name: str) -> None:
        # The barrier is what makes this concurrent rather than merely threaded: every
        # thread waits here and they all go for the write lock in the same instant.
        barrier.wait()
        try:
            outcome = enrollment.record_consent_for_enrolment(
                name, granted_by="the_person_themselves", instance_path=instance
            )
        except BaseException as exc:  # noqa: BLE001 - a dead thread must not be invisible
            with lock:
                failures.append(exc)
            return
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=enrol_one, args=(name,)) for name in names]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not any(thread.is_alive() for thread in threads), "a thread never finished -- a lock was held"

    # join() does not re-raise, so a thread that died would otherwise be invisible.
    assert failures == [], f"a concurrent enrolment raised: {[type(e).__name__ for e in failures]}"

    recorded = [outcome for outcome in results if outcome.recorded]
    # ALL of them, not merely one. `assert recorded` passed with seven of eight
    # refused, which is exactly the regression this test should catch: record_consent
    # absorbs sqlite3.OperationalError into storage_unavailable, so contention that
    # stopped serialising and started failing would have looked green.
    assert len(recorded) == len(names), (
        f"only {len(recorded)} of {len(names)} concurrent enrolments were recorded: "
        f"{sorted({o.reason for o in results if not o.recorded})}"
    )

    connection = store.connect(instance)
    try:
        orphans = connection.execute(
            "SELECT count(*) FROM learners WHERE id NOT IN (SELECT learner_id FROM consents) AND id != 'sample-learner'"
        ).fetchone()[0]
        pairs = connection.execute("SELECT count(*) FROM consents").fetchone()[0]
    finally:
        connection.close()

    assert orphans == 0, (
        f"{orphans} learner(s) exist with no consent row -- a concurrent enrolment "
        "committed the learner and lost the agreement"
    )
    assert pairs == len(recorded), "every enrolment that reported success wrote exactly one consent"
    assert len({outcome.learner_id for outcome in recorded}) == len(recorded), "two enrolments shared an id"


def test_a_failed_consent_insert_leaves_no_learner_behind(
    instance: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mechanism the concurrency test above cannot reach: the two inserts are ONE unit.

    record_consent writes the learner row and then the consent row. If those are ever
    split into separate transactions, a failure on the second leaves a learner in the
    database who agreed to nothing -- the precise state this task exists to make
    unreachable, and one nobody would notice, because the function reports a refusal
    either way.

    Forced by making the consent insert violate its own CHECK. The learner insert has
    already run by then, so the only thing that can remove it is the rollback.
    """
    # The two role columns swapped, which keeps the placeholder count identical -- a
    # substitution that changed the arity would raise a binding error instead and
    # report storage_unavailable, testing the wrong arm. Swapped, each value lands in
    # the other column and violates that column's CHECK, which is a real IntegrityError
    # arriving after the learner row is already written.
    broken = store._INSERT_CONSENT_SQL.replace(
        "granted_by, granted_via, granted_at", "granted_via, granted_by, granted_at"
    )
    assert broken != store._INSERT_CONSENT_SQL, "the substitution did not take"
    assert broken.count("?") == store._INSERT_CONSENT_SQL.count("?"), "the arity must not change"
    monkeypatch.setattr(store, "_INSERT_CONSENT_SQL", broken)
    before = _snapshot(instance)

    outcome = store.record_consent(
        "Ana",
        scope="face_recognition",
        statement_id="v1",
        statement_text="w",
        granted_by="the_person_themselves",
        granted_via="operator_at_the_robot",
        instance_path=instance,
    )

    assert outcome.recorded is False
    assert outcome.reason == "rejected_by_database"
    assert _snapshot(instance) == before, (
        "the learner row survived a failed consent insert -- the two writes are not one transaction"
    )


def test_a_person_enrolled_through_the_flow_is_recognised_from_a_different_frame(
    instance: Path, monkeypatch: pytest.MonkeyPatch, models_ready: None
) -> None:
    """The task's integration test, and the end-to-end property everything else serves.

    Enrol through the real flow, then read back what the DATABASE holds -- not what
    the flow returned -- and hand the matcher a SIXTH vector that was never one of the
    five enrolment views. If that is recognised as this learner, then the whole chain
    held: the medoid was a real observation, struct packed and unpacked it without
    losing the direction, the model id travelled with it, and the similarity still
    clears the floor.

    A sixth vector rather than one of the five, deliberately: matching a stored vector
    against itself would pass with a pack/unpack bug in it, because both sides would
    be wrong the same way.
    """
    views = _five_agreeing()
    monkeypatch.setattr(enrollment, "describe_face", _describes(*views))

    outcome = enrollment.enrol("Ana", _Camera(), granted_by="the_person_themselves", instance_path=instance)
    assert outcome.enrolled is True, outcome.reason

    stored = store.get_faceprint(outcome.learner_id, instance_path=instance)
    assert stored is not None
    assert stored.embedding_model == faces.EMBEDDING_MODEL_ID, "the model has to travel with the vector"

    household = (
        faces.EnrolledFaceprint(
            learner_id=stored.learner_id,
            embedding_model=stored.embedding_model,
            dimension=stored.dimension,
            vector=stored.vector,
        ),
        faces.EnrolledFaceprint(
            learner_id="somebody-else",
            embedding_model=stored.embedding_model,
            dimension=DIMENSION,
            vector=_unit(80),
        ),
    )
    a_later_look = _at_similarity(0.98, other=40)

    match = faces.match_faceprint(a_later_look, household, embedding_model=faces.EMBEDDING_MODEL_ID)

    assert match.matched is True, f"the enrolled person was not recognised: {match.reason}"
    assert match.learner_id == outcome.learner_id
    assert a_later_look not in views, "this must be a frame enrolment never saw"


def test_enrolling_the_same_person_twice_creates_two_learners_the_matcher_then_refuses(
    instance: Path, monkeypatch: pytest.MonkeyPatch, models_ready: None
) -> None:
    """The out-of-scope consequence enrollment.py calls "bounded and tested" -- tested here.

    Duplicate detection needs a read across every learner in the household, which the
    store's scoping rule refuses by design, so enrolling somebody twice mints a SECOND
    learner rather than updating the first. That is a real limitation, and what makes
    it acceptable is the direction it fails in: the matcher sees two faceprints that
    are both close to the person, the margin between them is not met, and it answers
    too_close_to_call -- a refusal, not somebody else's records.

    If that ever changes to a confident match, this test fails, and it should: the
    limitation would have stopped being safe.
    """
    monkeypatch.setattr(enrollment, "describe_face", _describes(*_five_agreeing()))
    first = enrollment.enrol("Ana", _Camera(), granted_by="the_person_themselves", instance_path=instance)
    monkeypatch.setattr(enrollment, "describe_face", _describes(*_five_agreeing()))
    second = enrollment.enrol("Ana", _Camera(), granted_by="the_person_themselves", instance_path=instance)

    assert first.enrolled and second.enrolled
    assert first.learner_id != second.learner_id, "a second enrolment mints a second learner"
    assert _counts(instance)["consents"] == 2, "and each of them agreed separately"

    household = tuple(
        faces.EnrolledFaceprint(
            learner_id=learner_id,
            embedding_model=faces.EMBEDDING_MODEL_ID,
            dimension=DIMENSION,
            vector=store.get_faceprint(learner_id, instance_path=instance).vector,
        )
        for learner_id in (first.learner_id, second.learner_id)
    )

    match = faces.match_faceprint(
        _at_similarity(0.98, other=40), household, embedding_model=faces.EMBEDDING_MODEL_ID
    )

    assert match.matched is False, "two enrolments of one person must not produce a confident match"
    assert match.reason == "too_close_to_call", (
        "the safe direction is a refusal; anything else hands somebody another person's records"
    )


def test_a_learner_can_gain_a_second_consent_and_still_holds_one_faceprint(
    instance: Path, monkeypatch: pytest.MonkeyPatch, models_ready: None
) -> None:
    """Two STORE-level facts, and the title says so rather than implying a flow test.

    This was called "re-enrolling someone" and presented as the already-enrolled edge
    case, which it is not: it never re-runs enrol(), and the real flow behaviour for
    that case is the opposite of what the old title implied -- a second enrolment
    mints a second learner, which
    test_enrolling_the_same_person_twice_creates_two_learners_the_matcher_then_refuses
    now covers properly.

    What is genuinely worth pinning here is narrower and still true: consents APPEND
    rather than replace, because agreeing again is a new act at a new moment, while
    the faceprints table holds exactly one row per learner however many times it is
    written.
    """
    monkeypatch.setattr(enrollment, "describe_face", _describes(*_five_agreeing()))
    first = enrollment.enrol("Ana", _Camera(), granted_by="the_person_themselves", instance_path=instance)
    assert first.enrolled is True, first.reason

    connection = store.connect(instance)
    try:
        connection.execute(
            "INSERT INTO consents "
            "(learner_id, scope, statement_id, statement_text, granted_by, granted_via, granted_at) "
            "VALUES (?, 'face_recognition', 'v1', 'w', 'the_person_themselves', 'operator_at_the_robot', 1)",
            (first.learner_id,),
        )
        connection.commit()
    finally:
        connection.close()

    again = store.save_faceprint(first.learner_id, "m2", _unit(9), instance_path=instance)

    assert again.saved is True
    connection = store.connect(instance)
    try:
        assert int(connection.execute(
            "SELECT count(*) FROM faceprints WHERE learner_id = ?", (first.learner_id,)
        ).fetchone()[0]) == 1, "one faceprint per learner, always"
        assert int(connection.execute(
            "SELECT count(*) FROM consents WHERE learner_id = ?", (first.learner_id,)
        ).fetchone()[0]) == 2, "agreeing again is a new act, appended"
    finally:
        connection.close()


# ------------------------------------------------------ enrolment is not a tool


def _tool_import_closure() -> dict[str, ast.Module]:
    """Every module reachable from the packaged tools by following imports under src/."""
    source_root = Path(store.__file__).resolve().parents[1]
    tools_directory = source_root / "tools"
    pending = [path for path in sorted(tools_directory.glob("*.py"))]
    closure: dict[str, ast.Module] = {}

    while pending:
        path = pending.pop()
        key = str(path.relative_to(source_root))
        if key in closure:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        closure[key] = tree
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.ImportFrom) and node.module:
                module = node.module
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("reachy_language_tutor"):
                        module = alias.name
            if not module or not module.startswith("reachy_language_tutor"):
                continue
            # Stop AT the learner boundary rather than walking through it. What this
            # guard judges is which names tool code reaches from that package; what
            # the package imports from its own storage module underneath is the
            # boundary's own business, and following it in would conflate the two and
            # report the boundary itself as an offender.
            if module.startswith("reachy_language_tutor.learners"):
                continue
            relative = module.split(".", 1)[1] if "." in module else ""
            candidate = source_root / Path(relative.replace(".", "/") + ".py")
            package_init = source_root / Path(relative.replace(".", "/")) / "__init__.py"
            for option in (candidate, package_init):
                if option.exists():
                    pending.append(option)
    return closure


# The learner names a tool module may reach. An ALLOW-LIST, and deliberately not a list
# of writers to forbid: a writer added tomorrow under any spelling -- add_household_member,
# register_face, onboard -- fails this because it is not named here, where a deny-list of
# enrolment-shaped words would be exactly as complete as the last person to think about it.
# CLAUDE.md records four defects from deny-lists; this is the shape that closed each.
PERMITTED_LEARNER_NAMES_IN_TOOL_CODE = frozenset(
    {
        "OUTCOMES",
        "CatalogLanguage",
        "LanguageProgress",
        "LearnerProfile",
        "Lesson",
        "LessonAttempt",
        "LessonContent",
        "LessonSource",
        "PractisedLanguage",
        "RECORD_REASONS",
        "RecordResultOutcome",
        "DialogueTurn",
        "Drill",
        "UsageNote",
        "DRILL_KINDS",
        "LESSON_ORIGINS",
        "get_language_catalog",
        "get_lesson",
        "get_lesson_content",
        "get_practised_languages",
        "get_profile",
        "get_progress",
        "record_result",
        "split_catalog_by_material",
        "store_is_available",
    }
)


def test_no_tool_can_reach_a_writer_that_creates_a_learner_or_a_faceprint() -> None:
    """Acceptance criterion 3, as an allow-list over what tool code may import.

    The model must never be able to create an identity. PERMITTED_TOOL_PARAMETERS
    cannot answer this -- it constrains what a tool DECLARES, and an enrolment tool
    would need no parameter at all to be a breach. So this reads the import graph
    instead, transitively, and names what tool code may reach from the learner
    package.

    Three shapes are refused outright rather than judged, because the name rule cannot
    read them: `import reachy_language_tutor.learners` as a module object (which would
    make `learners.record_consent(...)` invisible here), a star import, and any direct
    import of the storage module underneath the boundary.

    WHAT THIS CANNOT SEE: the closure is STATIC, so it does not follow
    core_tools._load_module_from_file, which executes Python from a configured
    external tool directory. A file placed there could import anything. That is not a
    hole this change opened and not one this scan can close -- putting a file there
    needs filesystem write on the robot, which already exceeds the shell access the
    enrol command itself needs, so it is governed by filesystem access rather than by
    an import rule. Recorded so the scan is not trusted for more than it covers.
    """
    closure = _tool_import_closure()

    # Vacuity first: a scan that reached nothing would pass while proving nothing.
    assert len(closure) >= 20, f"the closure found only {len(closure)} modules, so this proves little"
    reached: set[str] = set()
    offenders: list[str] = []

    for name, tree in closure.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("reachy_language_tutor.learners"):
                        offenders.append(f"{name} imports the module object {alias.name}")
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            if node.module == "reachy_language_tutor.learners.store":
                offenders.append(f"{name} imports the storage module directly")
            if not node.module.startswith("reachy_language_tutor.learners"):
                continue
            for alias in node.names:
                if alias.name == "*":
                    offenders.append(f"{name} star-imports from {node.module}")
                    continue
                reached.add(alias.name)
                if alias.name not in PERMITTED_LEARNER_NAMES_IN_TOOL_CODE:
                    offenders.append(f"{name} imports {alias.name} from {node.module}")

    assert offenders == [], offenders
    # And the scan really saw the imports it was meant to judge.
    assert {"get_progress", "record_result"} <= reached, sorted(reached)


def test_the_import_scanner_catches_a_writer_reached_either_way() -> None:
    """The guard's own self-check, so it cannot pass by reading nothing.

    Both shapes are exercised against synthetic source: the named import the allow-list
    reads, and the module-object import it cannot read and therefore refuses.
    """
    named = ast.parse("from reachy_language_tutor.learners import record_consent\n")
    module_object = ast.parse("import reachy_language_tutor.learners\n")

    def scan(tree: ast.Module) -> list[str]:
        found: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found += [
                    f"module object {alias.name}"
                    for alias in node.names
                    if alias.name.startswith("reachy_language_tutor.learners")
                ]
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("reachy_language_tutor.learners"):
                found += [
                    f"name {alias.name}"
                    for alias in node.names
                    if alias.name not in PERMITTED_LEARNER_NAMES_IN_TOOL_CODE
                ]
        return found

    assert scan(named) == ["name record_consent"]
    assert scan(module_object) == ["module object reachy_language_tutor.learners"]


def test_enrolment_is_not_reachable_from_any_tool() -> None:
    """The executable form of the task's own verification step 3."""
    closure = _tool_import_closure()

    assert "faces/enrollment.py" not in closure, "a tool can reach the enrolment flow"
    for name, tree in closure.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "enrollment" in node.module:
                raise AssertionError(f"{name} imports {node.module}")


def test_enrolment_declares_no_tool() -> None:
    """Nothing in the enrolment module is a Tool subclass, and it registers nothing."""
    from reachy_language_tutor.tools.core_tools import Tool

    for value in vars(enrollment).values():
        assert not (isinstance(value, type) and issubclass(value, Tool)), value


# ------------------------------------------------------- nothing writes an image


def test_nothing_in_the_flow_opens_a_file_for_writing(
    instance: Path, monkeypatch: pytest.MonkeyPatch, models_ready: None
) -> None:
    """Acceptance criterion 8, behaviourally -- and this is the half that holds for code
    nobody has written yet.

    A static scan can only refuse the ways of writing somebody thought of. This
    replaces builtins.open for the duration of a whole successful enrolment and fails
    on any write mode at all.

    The database write still has to succeed under the patch, and that assertion is
    what keeps this honest rather than vacuous: sqlite3 opens through C and not
    through builtins.open, so without it this test would pass on a flow that wrote
    nothing because it did nothing.

    WHAT THIS CANNOT SEE, stated because the same fact that makes the assertion above
    necessary also bounds the test: a C-extension writer never touches
    builtins.open, so `cv2.imwrite` or `numpy.save` would pass straight through here.
    This covers PYTHON-LEVEL writers only. The guard that closes native ones is
    _PERMITTED_CALLS in test_face_matching.py, which rglobs the whole faces package
    and refuses any call not on an allow-list -- so imwrite fails there by absence
    rather than needing to be foreseen here.
    """
    real_open = open
    opened_for_writing: list[str] = []

    def guarded(file, mode="r", *args, **kwargs):  # type: ignore[no-untyped-def]
        if any(character in mode for character in "wax+"):
            opened_for_writing.append(f"{file!r} mode={mode!r}")
            raise AssertionError(f"the enrolment flow opened {file!r} for writing")
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", guarded)
    monkeypatch.setattr(enrollment, "describe_face", _describes(*_five_agreeing()))

    outcome = enrollment.enrol("Ana", _Camera(), granted_by="the_person_themselves", instance_path=instance)

    assert opened_for_writing == []
    assert outcome.enrolled is True, (
        "the patch must not be what made this pass -- the database write has to have succeeded under it"
    )
    assert store.get_faceprint(outcome.learner_id, instance_path=instance) is not None


def test_a_full_enrolment_adds_no_file_beyond_the_database(
    instance: Path, monkeypatch: pytest.MonkeyPatch, models_ready: None
) -> None:
    """The directory before and after, so a frame written by any means would show up."""
    monkeypatch.setattr(enrollment, "describe_face", _describes(*_five_agreeing()))
    before = {path.name for path in instance.rglob("*")}

    outcome = enrollment.enrol("Ana", _Camera(), granted_by="the_person_themselves", instance_path=instance)

    assert outcome.enrolled is True, outcome.reason
    added = {path.name for path in instance.rglob("*")} - before
    assert added <= {"learners.v1.sqlite3-wal", "learners.v1.sqlite3-shm"}, sorted(added)


# ------------------------------------------------------------- the real detector

_MODELS_READY = faces.FACE_EMBEDDING_AVAILABLE and faces.warm_face_models()
_needs_models = pytest.mark.skipif(not _MODELS_READY, reason="face models unavailable (no library, or no network)")


@_needs_models
def test_the_detectors_first_return_value_is_a_flag_and_not_a_face_count() -> None:
    """The trap that would make an empty frame read as one face, pinned at the source.

    Measured against the pinned YuNet model: detect() on a blank frame returns
    (1, None) -- the first value is a SUCCESS FLAG. Code written as `if retval == 0:
    no face` reports one face on an empty frame, and `len(faces)` raises on the None.
    describe_face takes its count from the array and treats None as zero; this test
    exists so that a future simplification back to the obvious-looking form fails.
    """
    import numpy as np

    detector, _ = faces.loaded_face_models()
    blank = np.zeros((240, 320, 3), dtype=np.uint8)
    detector.setInputSize((320, 240))

    retval, detected = detector.detect(blank)

    assert retval == 1, "the first value is a success flag; it is 1 for a frame with no face in it"
    assert detected is None, "and the absence of faces is the array being None"
    assert faces.describe_face(blank).reason == "no_face", "which describe_face must read as zero faces"


@_needs_models
def test_a_frame_the_camera_produces_is_refused_as_no_face_rather_than_raising(instance: Path) -> None:
    """The real models against a real frame shape, through the whole flow.

    A blank frame has no face in it, so refusing is the RIGHT answer. What is being
    pinned is that a (720, 1280, 3) uint8 BGR array -- the shape media.get_frame()
    actually returned, measured against the desktop app's mockup simulation -- reaches
    the detector and comes back as an answer rather than an exception about shapes.
    """
    import numpy as np

    measured_frame = np.zeros((720, 1280, 3), dtype=np.uint8)

    outcome = enrollment.enrol(
        "Ana", _Camera(frame=measured_frame), granted_by="the_person_themselves", instance_path=instance
    )

    assert outcome.enrolled is False
    assert outcome.reason == "no_face"
    assert _counts(instance)["faceprints"] == 0
    assert _counts(instance)["consents"] == 0, "and the consent it wrote first was rolled back"


# ------------------------------------------------------------------- vocabularies


def test_every_refusal_the_flow_can_return_has_a_sentence() -> None:
    """A code with no sentence reaches an operator as a bare string."""
    for reason in enrollment.ENROLMENT_REASONS:
        assert reason in enrollment._REFUSALS, reason
        assert enrollment._REFUSALS[reason].strip(), reason


def test_the_upstream_vocabularies_are_carried_verbatim() -> None:
    """Every capture and embedding reason has its own sentence here, not a generic one.

    Carried verbatim rather than re-spelled, the way finish_lesson carries
    RECORD_REASONS: a reason added upstream fails this test rather than falling
    silently into the catch-all sentence and telling an operator nothing.
    """
    for reason in faces.CAPTURE_REASONS:
        assert reason in enrollment._REFUSALS, f"capture reason {reason} has no sentence"
    for reason in faces.EMBEDDING_REASONS:
        assert reason in enrollment._REFUSALS, f"embedding reason {reason} has no sentence"


def test_the_faceprint_scope_the_store_requires_is_one_this_app_publishes() -> None:
    """Two modules naming the same permission, pinned so they cannot drift apart."""
    assert store._FACEPRINT_CONSENT_SCOPE in store.CONSENT_SCOPES
    assert enrollment.CONSENT_SCOPE == store._FACEPRINT_CONSENT_SCOPE
    assert enrollment.CONSENT_SCOPE in store.CONSENT_SCOPES


def test_no_log_line_in_the_enrolment_module_can_carry_a_name_or_a_vector() -> None:
    """The project's rule, checked on the module that handles both.

    Every argument to every logger call must be a literal or a len(); a name, a
    learner id, a vector or a count of rows bound from a call is refused. These are
    children's households, and this module is the one that touches a person's name.
    """
    tree = ast.parse(Path(enrollment.__file__).read_text(encoding="utf-8"))
    offenders: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if not (isinstance(node.func.value, ast.Name) and node.func.value.id == "logger"):
            continue
        for argument in node.args:
            permitted = isinstance(argument, ast.Constant) or (
                isinstance(argument, ast.Call)
                and isinstance(argument.func, ast.Name)
                and argument.func.id == "len"
            ) or (isinstance(argument, ast.Name) and argument.id.isupper())
            if not permitted:
                offenders.append(f"line {node.lineno}: {ast.dump(argument)[:60]}")

    assert offenders == [], offenders
