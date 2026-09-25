"""Erasing a household member, and proving it rather than asserting it.

TWO DIFFERENT ERASURES, and a person will want either. "Stop recognising me" keeps a
year of learning and turns off a camera feature; "forget me" removes the person. A
single operation doing both would make somebody pay for one with the other, so they
are separate functions, separate operator flags, and separate sentences on screen.

WHAT THIS FILE MEASURES RATHER THAN CLAIMS. The task's fifth criterion asks whether
the faceprint BYTES survive a delete, and the honest answer is not the obvious one:

  * secure_delete is ON (connect() sets it), so a freed page is zeroed when it is
    WRITTEN, and emptying the write-ahead log is what writes it. Measured: after a
    full erasure, the packed vector, the display name and the learner id are absent
    from learners.v1.sqlite3, from its -wal AND from its -shm.
  * VACUUM is NOT required. Measured: running it changed nothing that the checkpoint
    had not already done.
  * A DATABASE IS THREE FILES, and the first version of this file measured one. It
    searched learners.v1.sqlite3 alone, found nothing, and concluded the data was
    gone. Measured with a second connection held open across the enrolment: the
    vector, the name and the id were all sitting in a 49 KB -wal, and the erasure had
    reported (busy=0, log_frames=12, checkpointed=12) -- a spotless result, because
    copying frames forward is not the same as removing them. SQLite will not rewind
    the log while anybody else is attached. Every byte assertion here now searches
    all three files, and the open-connection case is a parameter rather than an
    afterthought.
  * A REAL READER CAN STILL BLOCK IT, and then it must say so rather than wait
    forever. Measured: a connection in BEGIN gives (busy=1, log_frames=12,
    checkpointed=6), the rows are gone, the bytes are not.

That last point is why ErasureOutcome carries pages_still_in_the_log rather than a
bare success, and why the operator command says which of the two happened.
"""

from __future__ import annotations

import logging
import struct
from pathlib import Path

import pytest

from reachy_language_tutor import faces
from reachy_language_tutor.learners import store


DIMENSION = 128
MODEL = "opencv_sface_2021dec_fp32"
# A vector whose bytes are distinctive enough to search a binary file for, and whose
# floats are exactly representable so the packed form is stable.
TELLTALE = tuple(0.5 + index / 1024 for index in range(DIMENSION))
TELLTALE_BYTES = struct.pack(f"{store._VECTOR_FORMAT_PREFIX}{DIMENSION}f", *TELLTALE)
# A name no seed row or lesson could contain, so finding it in the file means finding
# the row this test wrote.
TELLTALE_NAME = "Zebediah Quixotic"
# A second household member, so an erasure can be shown to take one person and leave
# the other standing. Without a bystander, "recognition no longer matches" is proved
# by an empty household, which is a different fact.
BYSTANDER = tuple(-0.5 - index / 1024 for index in range(DIMENSION))
BYSTANDER_NAME = "Perpetua Wainscot"


@pytest.fixture()
def instance(tmp_path: Path) -> Path:
    """A prepared learner database."""
    assert store.ensure_learner_database(tmp_path).ready is True
    return tmp_path


def _database(instance_path: Path) -> Path:
    return store.learner_db_path_for_instance(instance_path)


def _every_file(instance_path: Path) -> tuple[Path, ...]:
    """Every file the database is, which is not the same as the file it is named after.

    The erasure promise is about the data rather than about learners.v1.sqlite3
    specifically. An earlier version of this module searched only the main file and
    concluded the bytes were gone; measured, with a second connection open they were
    sitting in the -wal companion in full, and every test here passed.

    FOUR SUFFIXES, matching store._restrict_permissions exactly. An earlier version of
    this helper listed three and cited that function as its justification, which is a
    guard meaning something narrower than the one it claims to mirror. The fourth is
    -journal, the rollback journal SQLite falls back to when WAL does not take, and
    that function's own comment says it holds freed pages just as the log does.
    """
    main = _database(instance_path)
    return (main, Path(f"{main}-wal"), Path(f"{main}-shm"), Path(f"{main}-journal"))


def _recoverable_from_disk(instance_path: Path, needle: bytes) -> tuple[str, ...]:
    """Which of the database's files still contain these bytes. Empty means gone."""
    return tuple(
        path.name for path in _every_file(instance_path) if path.exists() and needle in path.read_bytes()
    )


def _enrol_with_history(
    instance_path: Path,
    *,
    lessons: int = 2,
    name: str = TELLTALE_NAME,
    vector: tuple[float, ...] = TELLTALE,
) -> str:
    """A person with a faceprint, an agreement and some lesson history."""
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

    connection = store.connect(instance_path)
    try:
        taught = [row[0] for row in connection.execute("SELECT id FROM lessons LIMIT ?", (lessons,))]
    finally:
        connection.close()
    for lesson in taught:
        assert store.record_result(agreed.learner_id, lesson, "completed", instance_path=instance_path).recorded
    return agreed.learner_id


def _counts(instance_path: Path, learner_id: str) -> dict[str, int]:
    connection = store.connect(instance_path)
    try:
        return {
            "learners": connection.execute("SELECT count(*) FROM learners WHERE id = ?", (learner_id,)).fetchone()[0],
            "faceprints": connection.execute(
                "SELECT count(*) FROM faceprints WHERE learner_id = ?", (learner_id,)
            ).fetchone()[0],
            "consents": connection.execute(
                "SELECT count(*) FROM consents WHERE learner_id = ?", (learner_id,)
            ).fetchone()[0],
            "results": connection.execute(
                "SELECT count(*) FROM lesson_results WHERE learner_id = ?", (learner_id,)
            ).fetchone()[0],
        }
    finally:
        connection.close()


# ------------------------------------------------- the two erasures are different


def test_deleting_a_faceprint_keeps_the_person_and_their_learning(instance: Path) -> None:
    """Acceptance criterion 1. Stop recognising me, and keep everything else."""
    learner_id = _enrol_with_history(instance)

    removed = store.delete_faceprint(learner_id, instance_path=instance)

    assert removed == 1
    after = _counts(instance, learner_id)
    assert after == {"learners": 1, "faceprints": 0, "consents": 1, "results": 2}, after
    assert store.get_profile(learner_id, instance_path=instance) is not None
    assert store.get_faceprint(learner_id, instance_path=instance) is None


def test_forgetting_a_person_removes_everything_of_theirs(instance: Path) -> None:
    """Acceptance criterion 2, which no existing function could do.

    forget_learner refuses anybody with lesson history -- deliberately, because it
    exists to undo a half-finished enrolment. That refusal is exactly wrong for
    somebody asking to be forgotten, whose history is the largest thing they are
    asking to have removed.
    """
    learner_id = _enrol_with_history(instance)

    outcome = store.forget_learner_entirely(learner_id, instance_path=instance)

    assert outcome is not None and outcome.erased is True
    assert _counts(instance, learner_id) == {"learners": 0, "faceprints": 0, "consents": 0, "results": 0}
    assert store.get_profile(learner_id, instance_path=instance) is None


def test_the_two_erasures_are_not_the_same_operation(instance: Path) -> None:
    """The pitfall stated as a test: neither one can stand in for the other."""
    keeping_history = _enrol_with_history(instance)
    store.delete_faceprint(keeping_history, instance_path=instance)
    assert _counts(instance, keeping_history)["results"] == 2, "the narrow erasure took the history"

    forgetting = _enrol_with_history(instance)
    store.forget_learner_entirely(forgetting, instance_path=instance)
    assert _counts(instance, forgetting)["results"] == 0, "the full erasure left the history"

    # And the third function is neither: it refuses a person with history outright.
    with_history = _enrol_with_history(instance)
    assert store.forget_learner(with_history, instance_path=instance) == 0
    assert _counts(instance, with_history)["learners"] == 1


# ------------------------------------------------------- erasure is measured


@pytest.mark.parametrize("a_second_connection_is_open", [False, True])
def test_a_forgotten_person_leaves_no_bytes_in_any_of_the_databases_files(
    instance: Path, a_second_connection_is_open: bool
) -> None:
    """Acceptance criterion 5, and the measurement the task asks for.

    Not "the row is gone" -- the promise made to a household is about the data. So
    every file the database has is searched for the packed vector, the display name
    AND the learner id, and all three must be absent from all of them.

    THE SECOND PARAMETER IS THE WHOLE POINT, and it is here because the first version
    of this test passed while the data was still on disk. A single-process run deletes
    the -wal at last close, so searching one file finds nothing and proves nothing.
    Holding an unrelated connection open across BOTH the enrolment and the erasure is
    what puts the vector in the log and keeps it there -- and measured, that case
    returned pages_still_in_the_log=False with a 49 KB -wal holding the vector, the
    name and the id. It is a revert-proof as well as a test: with the checkpoint back
    to PASSIVE, the True case fails on the -wal and the False case still passes.

    The pre-condition assertion matters as much as the post-condition: if the bytes
    were never on disk, their absence afterwards would prove nothing at all.
    """
    # Opened BEFORE the enrolment, so this connection is never the last one to close
    # and nothing is checkpointed out from under the test by accident.
    held_open = store.connect(instance) if a_second_connection_is_open else None
    try:
        learner_id = _enrol_with_history(instance)

        assert _recoverable_from_disk(instance, TELLTALE_BYTES), "the vector never reached disk"
        assert _recoverable_from_disk(instance, TELLTALE_NAME.encode()), "the name never reached disk"
        assert _recoverable_from_disk(instance, learner_id.encode()), "the id never reached disk"

        outcome = store.forget_learner_entirely(learner_id, instance_path=instance)

        assert outcome is not None and outcome.erased is True
        assert outcome.pages_still_in_the_log is False, "no reader was open, so nothing should be deferred"
        assert _recoverable_from_disk(instance, TELLTALE_BYTES) == (), "the faceprint is still recoverable"
        assert _recoverable_from_disk(instance, TELLTALE_NAME.encode()) == (), "the name is still recoverable"
        assert _recoverable_from_disk(instance, learner_id.encode()) == (), "the id is still recoverable"
    finally:
        if held_open is not None:
            held_open.close()


def test_vacuum_is_not_what_makes_the_erasure_true(instance: Path) -> None:
    """The finding the task's own wording anticipated, and it went the other way.

    The criterion says "with VACUUM used if that is what the promise requires". It is
    not: secure_delete zeroes a freed page as it is written and the checkpoint is
    what writes it, so by the time VACUUM could run there is nothing left for it to
    do. Recorded as a test rather than a comment so the claim stays checked.
    """
    learner_id = _enrol_with_history(instance)

    store.forget_learner_entirely(learner_id, instance_path=instance)

    assert _recoverable_from_disk(instance, TELLTALE_BYTES) == (), "the checkpoint alone did not erase the vector"
    assert _recoverable_from_disk(instance, TELLTALE_NAME.encode()) == (), "the checkpoint alone left the name"

    connection = store.connect(instance)
    try:
        connection.execute("VACUUM")
    finally:
        connection.close()

    assert _recoverable_from_disk(instance, TELLTALE_BYTES) == ()
    assert _recoverable_from_disk(instance, TELLTALE_NAME.encode()) == ()


def test_the_explicit_checkpoint_is_what_erases_and_not_the_connection_closing(
    instance: Path,
) -> None:
    """The checkpoint earns its line, and finding out took a measurement.

    Deleting the checkpoint did not fail the byte tests above, which looked like the
    line was redundant. It is not -- those tests could not tell, because the function
    closes its own connection and SQLite auto-checkpoints when the LAST connection to
    a database closes. Measured: with the checkpoint removed and the connection held
    open, the vector was still in the file; it went only when that connection closed.

    So the distinguishing case is a second connection that is merely OPEN. Then the
    erasing connection is not the last one, the automatic checkpoint cannot fire, and
    only the explicit one puts the bytes out of the file before the function returns.
    An idle connection is also the ordinary state of this app, which builds one per
    call and holds none -- but a dashboard, a probe or a second process would make
    this the normal path rather than the exotic one.
    """
    learner_id = _enrol_with_history(instance)
    assert _recoverable_from_disk(instance, TELLTALE_BYTES)

    # Open, but idle: no transaction, so nothing is DEFERRED -- it only stops the
    # close-time checkpoint from being the thing that saves us.
    merely_open = store.connect(instance)
    try:
        outcome = store.forget_learner_entirely(learner_id, instance_path=instance)

        assert outcome is not None and outcome.erased is True
        assert outcome.pages_still_in_the_log is False, "an idle connection should defer nothing"
        assert _recoverable_from_disk(instance, TELLTALE_BYTES) == (), (
            "the bytes survived while another connection was open, so the erasure was "
            "relying on close-time checkpointing rather than doing it"
        )
    finally:
        merely_open.close()


def test_an_open_reader_defers_the_erasure_and_that_is_reported(instance: Path) -> None:
    """The measured trap, and the reason the outcome carries a deferral flag.

    A real reader -- a connection sitting in BEGIN with a statement behind it -- can
    stop the log being emptied, and an erasure must not wait on one indefinitely. So
    it is allowed to fail, and then it must SAY so: measured, this case comes back
    (busy=1, log_frames=12, checkpointed=6), the rows are gone and the bytes are not,
    and a household promised erasure is owed that difference rather than a clean
    sentence.
    """
    learner_id = _enrol_with_history(instance)

    holding_it_open = store.connect(instance)
    try:
        holding_it_open.execute("BEGIN")
        holding_it_open.execute("SELECT count(*) FROM learners").fetchone()

        outcome = store.forget_learner_entirely(learner_id, instance_path=instance)

        assert outcome is not None and outcome.erased is True
        assert outcome.pages_still_in_the_log is True, (
            "an open reader deferred the checkpoint and the outcome did not say so"
        )
        assert _recoverable_from_disk(instance, TELLTALE_BYTES), (
            "this test asserts a deferral; if the bytes are already gone it is not "
            "testing the deferral any more"
        )
        # The rows are gone regardless -- it is only the file that lags.
        assert _counts(instance, learner_id)["learners"] == 0
    finally:
        holding_it_open.close()

    # And it resolves: the bytes go once nobody is holding the log open.
    connection = store.connect(instance)
    try:
        assert store._checkpoint_the_log(connection) is False, "the deferral did not resolve"
    finally:
        connection.close()
    assert _recoverable_from_disk(instance, TELLTALE_BYTES) == (), "the deferral never resolved"


# --------------------------------------------------------- honest answers


def test_deletion_reports_counts_and_never_a_name(instance: Path) -> None:
    """Acceptance criterion 3. Every field of the outcome is a number or a flag."""
    import dataclasses

    learner_id = _enrol_with_history(instance)
    outcome = store.forget_learner_entirely(learner_id, instance_path=instance)

    assert outcome is not None
    assert outcome.learners == 1 and outcome.faceprints == 1
    assert outcome.consents == 1 and outcome.results == 2
    for field in dataclasses.fields(outcome):
        assert isinstance(getattr(outcome, field.name), (int, bool)), (
            f"{field.name} is not a count or a flag, so an erasure record could carry a person"
        )


@pytest.mark.parametrize("who", ["never-existed", "", "   "])
def test_forgetting_somebody_who_does_not_exist_is_a_clean_answer(instance: Path, who: str) -> None:
    """Acceptance criterion 6. Not an error, and not a pretend success either."""
    outcome = store.forget_learner_entirely(who, instance_path=instance)

    assert outcome is not None
    assert outcome.erased is False
    assert (outcome.learners, outcome.faceprints, outcome.consents, outcome.results) == (0, 0, 0, 0)


def test_forgetting_twice_is_the_same_clean_answer(instance: Path) -> None:
    """Deleting twice is an edge case the task names, and idempotence is the answer."""
    learner_id = _enrol_with_history(instance)

    first = store.forget_learner_entirely(learner_id, instance_path=instance)
    second = store.forget_learner_entirely(learner_id, instance_path=instance)

    assert first is not None and first.erased is True
    assert second is not None and second.erased is False


def test_an_unreadable_store_is_not_a_successful_erasure(instance: Path) -> None:
    """None means "I could not tell", which must never be reported as "it is gone"."""
    assert store.forget_learner_entirely("somebody", instance_path="/nonexistent/path") is None


def test_forgetting_the_only_enrolled_member_leaves_an_empty_household(instance: Path) -> None:
    """The edge case the task names. The household reader must answer () not None."""
    learner_id = _enrol_with_history(instance)
    assert len(store.get_enrolled_faceprints(instance_path=instance) or ()) == 1

    store.forget_learner_entirely(learner_id, instance_path=instance)

    household = store.get_enrolled_faceprints(instance_path=instance)
    assert household == (), "an empty household must be an empty tuple, never the unreadable answer"


def test_no_learner_id_or_name_reaches_a_log_on_any_erasure_path(
    instance: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Acceptance criterion 8. These are children's households."""
    learner_id = _enrol_with_history(instance)

    with caplog.at_level(logging.DEBUG):
        store.delete_faceprint(learner_id, instance_path=instance)
        store.forget_learner_entirely(learner_id, instance_path=instance)
        store.forget_learner_entirely(learner_id, instance_path=instance)
        store.forget_learner_entirely("never-existed", instance_path=instance)
        store.forget_learner_entirely(None, instance_path=instance)  # type: ignore[arg-type]

    # Both assertions below are "not in", so an empty capture would pass them while
    # proving nothing -- the failure shape this file calls out elsewhere. The erasure
    # paths do log (a refusal, a fixed sentence), so silence here means the capture
    # broke rather than that the code is clean.
    assert caplog.records, "nothing was captured, so these assertions prove nothing"
    surface = " ".join(f"{r.getMessage()} {r.msg} {r.args}" for r in caplog.records)
    assert learner_id not in surface
    assert TELLTALE_NAME not in surface


# --------------------------------------------------- recognition after erasure


def _household(instance_path: Path) -> tuple[faces.EnrolledFaceprint, ...]:
    """Whoever the matcher would be given, read back from the store."""
    rows = store.get_enrolled_faceprints(instance_path=instance_path) or ()
    return tuple(
        faces.EnrolledFaceprint(
            learner_id=row.learner_id,
            embedding_model=row.embedding_model,
            dimension=row.dimension,
            vector=row.vector,
        )
        for row in rows
    )


def test_recognition_no_longer_matches_a_forgotten_person(instance: Path) -> None:
    """Acceptance criterion 4, proved end to end rather than assumed.

    TWO PEOPLE, AND ONLY ONE IS FORGOTTEN, because with one the proof is degenerate.
    An earlier version erased the only enrolled member and pinned reason
    "nobody_enrolled" -- the code for "there is nobody to match", not "this person is
    gone" -- so it would have passed identically if the erasure had emptied the whole
    faceprints table and taken the other household member with it. With a bystander
    present the two failures are distinguishable, and the bystander still matching is
    what proves the cascade stayed scoped to one learner.

    Matching is pure arithmetic over what the store returns, so this needs no camera
    and no model.
    """
    forgotten = _enrol_with_history(instance)
    bystander = _enrol_with_history(instance, name=BYSTANDER_NAME, vector=BYSTANDER, lessons=1)

    before = faces.match_faceprint(TELLTALE, _household(instance), embedding_model=MODEL)
    assert before.matched is True and before.learner_id == forgotten

    store.forget_learner_entirely(forgotten, instance_path=instance)

    after = faces.match_faceprint(TELLTALE, _household(instance), embedding_model=MODEL)
    assert after.matched is False
    assert after.learner_id is None
    assert after.reason == "no_one_close_enough", (
        "the household was not empty, so a refusal for any other reason means this "
        "test is measuring something else"
    )

    # The person who did not ask to be forgotten is untouched, faceprint and history.
    still_here = faces.match_faceprint(BYSTANDER, _household(instance), embedding_model=MODEL)
    assert still_here.matched is True and still_here.learner_id == bystander
    assert _counts(instance, bystander) == {"learners": 1, "faceprints": 1, "consents": 1, "results": 1}


def test_recognition_no_longer_matches_after_only_the_faceprint_is_deleted(instance: Path) -> None:
    """The integration test the task's testing_strategy actually names.

    The strategy's one integration case is about delete_faceprint -- "stop
    recognising me, keep my progress" -- and that is the path where "recognition
    really stopped" matters most, because the person is still in the database and
    their row is still readable. The forgetting test above proves nothing about it:
    that erasure removes the learner as well, so the faceprint could go for the wrong
    reason and still look right.

    A bystander is present here too, for the same reason as above.
    """
    quiet = _enrol_with_history(instance)
    bystander = _enrol_with_history(instance, name=BYSTANDER_NAME, vector=BYSTANDER, lessons=1)

    before = faces.match_faceprint(TELLTALE, _household(instance), embedding_model=MODEL)
    assert before.matched is True and before.learner_id == quiet

    assert store.delete_faceprint(quiet, instance_path=instance) == 1

    after = faces.match_faceprint(TELLTALE, _household(instance), embedding_model=MODEL)
    assert after.matched is False and after.learner_id is None
    assert after.reason == "no_one_close_enough"

    # THE OTHER HALF OF THE PROMISE: recognition stopped, the learning stayed.
    assert _counts(instance, quiet) == {"learners": 1, "faceprints": 0, "consents": 1, "results": 2}
    still_here = faces.match_faceprint(BYSTANDER, _household(instance), embedding_model=MODEL)
    assert still_here.matched is True and still_here.learner_id == bystander


def test_an_erased_learners_id_is_inert_for_the_rest_of_the_session(instance: Path) -> None:
    """The fifth security consideration: what happens to the person being served.

    The decision recorded in current_learner.py is to do nothing to the running
    process -- ToolDependencies is sealed, so the id survives to the next session
    boundary -- and that decision is only defensible because a stale id can do
    nothing. This measures each of the four routes it has rather than trusting that.
    A regression here would turn "inert" into "wrong", which is the case the whole
    identity boundary exists to prevent.
    """
    served = _enrol_with_history(instance)
    bystander = _enrol_with_history(instance, name=BYSTANDER_NAME, vector=BYSTANDER, lessons=1)

    connection = store.connect(instance)
    try:
        lesson_id = str(connection.execute("SELECT id FROM lessons LIMIT 1").fetchone()[0])
    finally:
        connection.close()
    # A real lesson id, so a refusal below is about the LEARNER being unknown rather
    # than about the lesson -- a distinction that has already cost this task a wrong
    # conclusion once.
    assert store.record_result(served, lesson_id, "completed", instance_path=instance).recorded is True

    assert store.forget_learner_entirely(served, instance_path=instance).erased is True

    # Reads reach nobody. get_profile answers None; get_progress does NOT -- it hands
    # back the language's public course, which is the assertion this test exists to
    # make, because "answers None" was written here first and was wrong. What matters
    # is that nothing personal comes back and that it is indistinguishable from what
    # an id nobody ever had would get.
    assert store.get_profile(served, instance_path=instance) is None
    progress = store.get_progress(served, "it", instance_path=instance)
    assert progress is not None and progress.completed == () and progress.attempts == ()
    never_existed = store.get_progress("f" * 32, "it", instance_path=instance)
    assert progress.remaining == never_existed.remaining, "an erased id sees more than a never-seen one"
    # Writes are refused by name, so nothing resurrects the erased person.
    assert store.record_result(served, lesson_id, "completed", instance_path=instance).reason == "unknown_learner"
    assert store.save_faceprint(served, MODEL, TELLTALE, instance_path=instance).reason == "unknown_learner"
    assert _counts(instance, served) == {"learners": 0, "faceprints": 0, "consents": 0, "results": 0}
    # And the stale id never becomes somebody else's: ids are not reissued.
    assert served != bystander
    assert store.get_profile(bystander, instance_path=instance) is not None


# ------------------------------------------------------- the operator surface


def _args(instance_path: Path, **overrides: object):
    """The argparse namespace handle_enrol_command reads, with everything unset."""

    given = str(instance_path)

    class _Args:
        instance_path = given
        forget_everything_id = None
        forget_learner_id = None
        remove_learner_id = None
        show_learner = None
        enrol_name = None
        consent_from = None
        no_camera = False

    for key, value in overrides.items():
        setattr(_Args, key, value)
    return _Args()


def test_the_operator_can_ask_for_either_erasure_and_is_told_which_happened(
    instance: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The two flags, and the sentences that keep them apart.

    "Which one happened" must be unambiguous to the person who asked, so the output
    is asserted rather than just the exit code -- a command that erased the wrong
    thing quietly would exit 0 either way.
    """
    from reachy_language_tutor.main import handle_enrol_command

    keeping = _enrol_with_history(instance)
    capsys.readouterr()
    assert handle_enrol_command(_args(instance, forget_learner_id=keeping)) == 0
    narrow = capsys.readouterr().out
    assert "lesson history is untouched" in narrow
    assert _counts(instance, keeping)["results"] == 2

    forgetting = _enrol_with_history(instance)
    capsys.readouterr()
    assert handle_enrol_command(_args(instance, forget_everything_id=forgetting)) == 0
    full = capsys.readouterr().out
    assert "completely" in full
    assert "1 person" in full and "lesson result(s)" in full, "the counts must reach the person who asked"
    assert "Nothing of theirs is left in the database file" in full
    assert _counts(instance, forgetting) == {"learners": 0, "faceprints": 0, "consents": 0, "results": 0}


def test_erasing_somebody_who_does_not_exist_is_reported_not_crashed(
    instance: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Criterion 6 at the surface a person actually uses."""
    from reachy_language_tutor.main import handle_enrol_command

    capsys.readouterr()
    code = handle_enrol_command(_args(instance, forget_everything_id="never-existed"))

    assert code == 1
    assert "No such learner" in capsys.readouterr().out


def test_the_operator_is_told_when_the_bytes_have_not_left_the_file_yet(
    instance: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The deferral reaches the terminal, not only the outcome object.

    A person asking to be forgotten is owed the difference between "the rows are
    gone" and "the bytes are gone", and the operator is the only one who can see it.
    """
    from reachy_language_tutor.main import handle_enrol_command

    learner_id = _enrol_with_history(instance)
    holding_it_open = store.connect(instance)
    try:
        holding_it_open.execute("BEGIN")
        holding_it_open.execute("SELECT count(*) FROM learners").fetchone()
        capsys.readouterr()

        assert handle_enrol_command(_args(instance, forget_everything_id=learner_id)) == 0

        said = capsys.readouterr().out
        assert "write-ahead log" in said, "a deferred erasure was reported as a finished one"
        assert "Nothing of theirs is left" not in said
    finally:
        holding_it_open.close()


# ------------------------------------------------ not reachable from the model


def test_no_tool_can_erase_a_household_member() -> None:
    """Acceptance criterion 7. A tool that erases somebody is as dangerous as one
    that impersonates them.

    The same import-closure allow-list that keeps enrolment away from the model, so
    the new erasure function is covered by the rule that was already there rather
    than by a second rule written for it -- and the assertion below is that it is
    NOT on the permitted list, which is what makes the closure scan refuse it.
    """
    from test_face_enrollment import PERMITTED_LEARNER_NAMES_IN_TOOL_CODE, _tool_import_closure

    for erasing in ("forget_learner_entirely", "forget_learner", "delete_faceprint"):
        assert erasing not in PERMITTED_LEARNER_NAMES_IN_TOOL_CODE, (
            f"{erasing} is on the list of names tool code may import, so the model can reach it"
        )

    # And the closure really refuses them, rather than the allow-list merely omitting
    # a name nothing imports: every learner name any tool module reaches is checked
    # against that list, so an erasure appearing there would already have failed.
    import ast as _ast

    reached: set[str] = set()
    for tree in _tool_import_closure().values():
        for node in _ast.walk(tree):
            if isinstance(node, _ast.ImportFrom) and (node.module or "").startswith("reachy_language_tutor.learners"):
                reached |= {alias.name for alias in node.names}

    assert reached, "the closure reached no learner names, so this proves nothing"
    assert not (reached & {"forget_learner_entirely", "forget_learner", "delete_faceprint"}), (
        f"tool code reaches an erasure function: {sorted(reached)}"
    )


@pytest.mark.parametrize("a_second_connection_is_open", [False, True])
def test_a_replaced_faceprint_does_not_outlive_the_one_that_replaced_it(
    instance: Path, a_second_connection_is_open: bool
) -> None:
    """The fourth eraser, found by deriving the class instead of trusting three names.

    Re-enrolling somebody replaces their faceprint, and a replace frees the pages
    holding the OLD one. That is an erasure by every measure a household would care
    about -- a face template nobody is using any more is still a face template -- and
    save_faceprint did not empty the log. Measured before the fix, with a second
    connection open: the superseded vector was still recoverable from the -wal after
    save_faceprint returned, and went only when that connection closed.

    It is not one of the three functions the task names, which is exactly why it is
    here: the review that found it was looking for the class.
    """
    superseded = TELLTALE
    replacement = tuple(-0.25 - index / 2048 for index in range(DIMENSION))
    superseded_bytes = TELLTALE_BYTES
    replacement_bytes = struct.pack(f"{store._VECTOR_FORMAT_PREFIX}{DIMENSION}f", *replacement)

    held_open = store.connect(instance) if a_second_connection_is_open else None
    try:
        learner_id = _enrol_with_history(instance, vector=superseded)
        assert _recoverable_from_disk(instance, superseded_bytes), "the first vector never reached disk"

        assert store.save_faceprint(learner_id, MODEL, replacement, instance_path=instance).saved is True

        assert _recoverable_from_disk(instance, superseded_bytes) == (), (
            "the replaced faceprint is still recoverable from the database's files"
        )
        # The new one is genuinely there, so this is not passing because the write failed.
        assert _recoverable_from_disk(instance, replacement_bytes)
        stored = store.get_faceprint(learner_id, instance_path=instance)
        assert stored is not None and stored.vector == replacement
    finally:
        if held_open is not None:
            held_open.close()


# ------------------------------------------- one copy of the checkpoint rule


def test_every_erasure_empties_the_log_through_the_same_helper() -> None:
    """Fix the class, not the member -- enforced rather than remembered.

    Three functions erase personal data, and each had its own transcription of the
    checkpoint block. All three carried the same defect: they checkpointed PASSIVE
    and judged the result by whether frames were copied, which measured the wrong
    thing and left the faceprint in the -wal whenever another connection was open.
    One was found by review; the other two were siblings that would have been fixed
    a task later, which is this repository's most expensive recurring mistake.

    So the rule lives in _checkpoint_the_log, and this says so in a way that fails if
    somebody inlines it again or reaches for the weaker mode.
    """
    import ast
    import inspect

    source = inspect.getsource(store)
    tree = ast.parse(source)

    # Every string the module can execute, INCLUDING one bound to a name first. An
    # earlier version collected only literals sitting in a call, which is the hole D6
    # was: a pragma assigned to a constant and then executed would have been invisible
    # to the guard written to prevent exactly that. Prose is not scanned, because the
    # comments here have to be able to explain why PASSIVE was wrong without tripping
    # the rule that says so.
    def _text(value: ast.expr) -> str | None:
        """The SQL a bound name holds, whether it was wrapped on the way in.

        Every statement in this module is bound through _learner_scoped(...), so
        reading only bare literals would see none of them -- which is how the first
        version of this scan found zero DELETEs and still passed its own assertions.
        """
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
        if isinstance(value, ast.Call):
            parts = [
                argument.value
                for argument in value.args
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
            ]
            return " ".join(parts) if parts else None
        return None

    named: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        statement = _text(node.value)
        if statement is None:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                named[target.id] = statement
    executed: list[str] = []
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        for argument in call.args:
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                executed.append(argument.value)
            elif isinstance(argument, ast.Name) and argument.id in named:
                executed.append(named[argument.id])

    checkpoints = [statement for statement in executed if "wal_checkpoint" in statement]
    assert checkpoints == ["PRAGMA wal_checkpoint(TRUNCATE)"], (
        "the checkpoint rule has been forked or weakened. A passive checkpoint copies "
        "frames forward without emptying the log: measured, it left the vector, the name "
        "and the id in learners.v1.sqlite3-wal while reporting a clean "
        f"(busy=0, log_frames=12, checkpointed=12). Found: {checkpoints}"
    )

    # THE CLASS, DERIVED -- not three names typed out. Naming today's erasures would
    # let a fourth one written tomorrow pass, and there WAS a fourth: save_faceprint
    # frees the superseded faceprint's pages on a replace and did not checkpoint, so
    # measured, the old vector outlived the new one in the -wal. Anything executing a
    # DELETE frees pages that held personal data, so that is the rule.
    # PERSONAL tables specifically, taken from the module's own list rather than typed
    # out here -- a second copy would drift, and this rule has to mean what store.py
    # means by "personal". Lesson content is deleted and re-seeded on every import and
    # holds nothing about anybody, so it is not in that list and does not need this.
    personal = set(store._PERSONAL_TABLES)
    assert personal, "no personal tables found, so this scan proves nothing"

    def _deletes_personal_data(statement: str) -> bool:
        if "DELETE FROM" not in statement:
            return False
        return any(table in statement for table in personal)

    deleting_constants = {name for name, statement in named.items() if _deletes_personal_data(statement)}
    assert deleting_constants, "no personal-data DELETE statements found, so this scan proves nothing"

    missing = []
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef):
            continue
        names_used = {node.id for node in ast.walk(function) if isinstance(node, ast.Name)}
        deletes = names_used & deleting_constants
        inline = [
            node.value
            for node in ast.walk(function)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and _deletes_personal_data(node.value)
        ]
        if not deletes and not inline:
            continue
        if "_checkpoint_the_log" not in names_used:
            missing.append(f"{function.name} (deletes via {sorted(deletes) or inline})")

    assert missing == [], (
        "a function frees pages that held personal data without emptying the write-ahead "
        f"log, so the bytes can outlive the row: {missing}"
    )


def test_a_stored_faceprint_does_not_render_whose_it_is_or_its_numbers() -> None:
    """The store's Faceprint, and the outcome that carries it, keep the id and vector out.

    The generated dataclass repr printed the learner id and all 128 floats -- 1,398
    characters of one person's biometric data -- and SaveFaceprintOutcome renders the
    faceprint it holds, so the saved-faceprint answer carried it too.
    """
    from reachy_language_tutor.learners import Faceprint, SaveFaceprintOutcome

    learner_id = "learner-4f1d9c"
    element = 0.123456789
    faceprint = Faceprint(
        learner_id=learner_id,
        embedding_model="opencv_sface_2021dec_fp32",
        dimension=128,
        vector=tuple([element] * 128),
        created_at=1,
    )
    outcome = SaveFaceprintOutcome(saved=True, reason=None, faceprint=faceprint)

    for text in (repr(faceprint), f"{faceprint}", repr(outcome)):
        assert learner_id not in text, text
        assert str(element) not in text, text
    assert "<128 floats>" in repr(outcome)
