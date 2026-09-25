"""Tests for the learner store's query interface: profiles, progress, and results.

Schema creation, seeding and their failure modes live in test_learner_schema.py.

These tests use direct SQL for setup. That is fine: the "all SQL lives in the store"
verification is scoped to the application package, not to the test suite.
"""

import re
import ast
import string
import struct
import inspect
import logging
import sqlite3
import dataclasses
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest
from untaught_language import UNTAUGHT_CODE, UNTAUGHT_CODE_ABSENT, UNTAUGHT_LANGUAGE_ROW_NAME

import reachy_language_tutor.learners as learners
from reachy_language_tutor import memory
from reachy_language_tutor.learners import store
from reachy_language_tutor.learners.models import Lesson, LessonAttempt, LearnerProfile


@pytest.fixture
def instance(tmp_path: Path) -> Path:
    """A prepared learner database at a temporary instance path."""
    assert store.ensure_learner_database(tmp_path).ready is True
    return tmp_path


def _add_learner(instance_path: Path, learner_id: str, name: str = "Someone") -> None:
    connection = store.connect(instance_path)
    try:
        connection.execute(
            "INSERT INTO learners (id, display_name, created_at) VALUES (?, ?, ?)",
            (learner_id, name, 0),
        )
        connection.commit()
    finally:
        connection.close()


def _add_consent(instance_path: Path, learner_id: str) -> None:
    """Give this learner a standing consent to face recognition.

    Deliberately NOT folded into _add_learner. save_faceprint refuses a learner with
    no consent row -- its INSERT selects from the consents table -- so "a learner who
    has agreed" and "a learner who has not" are now two different fixtures, and a test
    has to say which one it means. Folding them together would hide the gate from
    every test that stores a faceprint, which is most of them.
    """
    connection = store.connect(instance_path)
    try:
        connection.execute(
            "INSERT INTO consents "
            "(learner_id, scope, statement_id, statement_text, granted_by, granted_via, granted_at) "
            "VALUES (?, 'face_recognition', 'test.v1', 'Wording used by the tests.', "
            "'the_person_themselves', 'operator_at_the_robot', 0)",
            (learner_id,),
        )
        connection.commit()
    finally:
        connection.close()


# Later than every seeded attempt, because "finished" means the LATEST result for a
# lesson is a completion rather than that one appears anywhere in its history. A row
# stamped at 1, as this defaulted to, is older than the sample learner's seeded Spanish
# attempts -- so a test that "completed" a lesson that way was quietly recording a
# completion the catalog then overruled, and only passed because the old rule ignored
# ordering entirely.
_AFTER_THE_SEEDED_HISTORY = store.utc_now_ms()


def _add_result(
    instance_path: Path, learner_id: str, lesson_id: str, outcome: str, when: int = _AFTER_THE_SEEDED_HISTORY
) -> None:
    connection = store.connect(instance_path)
    try:
        connection.execute(
            "INSERT INTO lesson_results (learner_id, lesson_id, outcome, score, recorded_at) VALUES (?, ?, ?, ?, ?)",
            (learner_id, lesson_id, outcome, None, when),
        )
        connection.commit()
    finally:
        connection.close()


def _count_results(instance_path: Path) -> int:
    connection = store.connect(instance_path)
    try:
        return int(connection.execute("SELECT COUNT(*) FROM lesson_results").fetchone()[0])
    finally:
        connection.close()


# ------------------------------------------------------------------- get_profile


def test_get_profile_returns_the_seeded_learner(instance: Path) -> None:
    """The profile comes back as plain data with the seeded values."""
    profile = store.get_profile("sample-learner", instance_path=instance)

    assert profile == LearnerProfile(id="sample-learner", display_name="Sample Learner", created_at=1767225600000)


@pytest.mark.parametrize("learner_id", ["nobody", "", "SAMPLE-LEARNER"])
def test_get_profile_unknown_learner_returns_none(instance: Path, learner_id: str) -> None:
    """An unknown learner is absence, not an error."""
    assert store.get_profile(learner_id, instance_path=instance) is None


# ------------------------------------------------------------------ get_progress


def test_get_progress_reflects_seeded_spanish_history(instance: Path) -> None:
    """A partial attempt does not advance the learner past that lesson."""
    progress = store.get_progress("sample-learner", "es", instance_path=instance)

    assert progress is not None
    assert progress.language_name == "Spanish"
    assert tuple(lesson.id for lesson in progress.completed) == (
        "es-01-greetings",
        "es-02-introductions",
    )
    assert tuple(lesson.id for lesson in progress.remaining) == (
        "es-fast-01-getting-started-in-class",
        "es-fast-02-at-the-restaurant",
        "es-fast-03-getting-around-inside",
        "es-fast-04-the-familiar-form",
        "es-fast-05-shopping-at-the-market",
        "es-fast-06-household-repairs",
        "es-03-numbers",
        "es-04-ordering-food",
        "es-05-directions",
        "es-06-daily-routine",
    )
    assert progress.next_lesson is not None
    assert progress.next_lesson.id == "es-fast-01-getting-started-in-class"
    assert len(progress.attempts) == 3
    assert progress.attempts[0].lesson_id == "es-03-numbers", "newest attempt first"
    assert progress.attempts[0].outcome == "partial"


def test_get_progress_untouched_language_starts_at_lesson_one(instance: Path) -> None:
    """A language that is taught but never practised is a fresh start, not unknown.

    German, because the claim is about a language carrying only its placeholder
    syllabus. French held this role until its converted units shipped and now carries
    eleven lessons rather than six.
    """
    progress = store.get_progress("sample-learner", "de", instance_path=instance)

    assert progress is not None
    assert progress.completed == ()
    assert progress.attempts == ()
    assert len(progress.remaining) == 6
    assert progress.next_lesson is not None
    assert progress.next_lesson.id == "de-01-greetings"


@pytest.mark.parametrize("language_code", [UNTAUGHT_CODE, "ES", ""])
def test_get_progress_unknown_language_returns_none(instance: Path, language_code: str) -> None:
    """None means one thing only: this robot does not teach that language."""
    assert store.get_progress("sample-learner", language_code, instance_path=instance) is None


def test_get_progress_unknown_learner_gets_a_fresh_start(instance: Path) -> None:
    """An unknown learner returns an empty history rather than raising.

    The lesson catalog is not personal data, so there is nothing to withhold here.
    Recording a result is where an unknown learner must be caught.
    """
    progress = store.get_progress("nobody", "es", instance_path=instance)

    assert progress is not None
    assert progress.completed == ()
    assert progress.attempts == ()
    assert progress.next_lesson is not None
    assert progress.next_lesson.id == "es-fast-01-getting-started-in-class"


def test_get_progress_never_returns_another_learners_rows(instance: Path) -> None:
    """The scoping boundary: one household member's history must not reach another."""
    _add_learner(instance, "other-learner", "Other Learner")
    for lesson_id in (
        "es-fast-01-getting-started-in-class",
        "es-fast-02-at-the-restaurant",
        "es-fast-03-getting-around-inside",
        "es-fast-04-the-familiar-form",
        "es-fast-05-shopping-at-the-market",
        "es-fast-06-household-repairs",
        "es-01-greetings",
        "es-02-introductions",
        "es-03-numbers",
        "es-04-ordering-food",
        "es-05-directions",
        "es-06-daily-routine",
    ):
        _add_result(instance, "other-learner", lesson_id, "completed")
    _add_result(instance, "other-learner", "fr-01-greetings", "completed")

    mine = store.get_progress("sample-learner", "es", instance_path=instance)
    assert mine is not None
    assert mine.next_lesson is not None
    # Stronger than it was: they have now finished all TWELVE Spanish lessons and
    # I am still on the first one, which I have never attempted.
    assert mine.next_lesson.id == "es-fast-01-getting-started-in-class", (
        "another learner's completions must not advance me"
    )
    assert len(mine.attempts) == 3
    assert all(attempt.learner_id == "sample-learner" for attempt in mine.attempts)

    theirs = store.get_progress("other-learner", "es", instance_path=instance)
    assert theirs is not None
    assert theirs.remaining == ()
    assert theirs.next_lesson is None

    my_french = store.get_progress("sample-learner", "fr", instance_path=instance)
    assert my_french is not None
    assert my_french.attempts == (), "another learner's French attempt must not appear here"


def test_get_progress_language_without_lessons_is_not_unknown(instance: Path) -> None:
    """A taught language with no content differs from a language that is not taught."""
    connection = store.connect(instance)
    try:
        connection.execute(
            "INSERT INTO languages (code, name) VALUES (?, ?)", (UNTAUGHT_CODE, UNTAUGHT_LANGUAGE_ROW_NAME)
        )
        connection.commit()
    finally:
        connection.close()

    progress = store.get_progress("sample-learner", UNTAUGHT_CODE, instance_path=instance)

    assert progress is not None, "the language is taught, so this is not None"
    assert progress.remaining == ()
    assert progress.completed == ()
    assert progress.next_lesson is None


def test_get_progress_finished_language_has_no_next_lesson(instance: Path) -> None:
    """Finishing a language is distinguishable from never starting it."""
    for lesson_id in (
        "es-fast-01-getting-started-in-class",
        "es-fast-02-at-the-restaurant",
        "es-fast-03-getting-around-inside",
        "es-fast-04-the-familiar-form",
        "es-fast-05-shopping-at-the-market",
        "es-fast-06-household-repairs",
        "es-03-numbers",
        "es-04-ordering-food",
        "es-05-directions",
        "es-06-daily-routine",
    ):
        _add_result(instance, "sample-learner", lesson_id, "completed")

    progress = store.get_progress("sample-learner", "es", instance_path=instance)

    assert progress is not None
    assert len(progress.completed) == 12
    assert progress.remaining == ()
    assert progress.next_lesson is None


def test_next_lesson_matches_next_lesson_sql(instance: Path) -> None:
    """The derived next lesson must not drift from the documented rule."""
    _add_learner(instance, "finished")
    for lesson_id in (
        "es-01-greetings",
        "es-02-introductions",
        "es-03-numbers",
        "es-04-ordering-food",
        "es-05-directions",
        "es-06-daily-routine",
    ):
        _add_result(instance, "finished", lesson_id, "completed")

    connection = store.connect(instance)
    try:
        for learner_id, language_code in [
            ("sample-learner", "es"),
            ("sample-learner", "fr"),
            ("nobody", "es"),
            ("finished", "es"),
        ]:
            progress = store.get_progress(learner_id, language_code, instance_path=instance)
            assert progress is not None
            row = connection.execute(
                store.NEXT_LESSON_SQL, store.next_lesson_params(language_code, learner_id)
            ).fetchone()
            expected = None if row is None else str(row["id"])
            actual = None if progress.next_lesson is None else progress.next_lesson.id
            assert actual == expected, f"drift for {learner_id}/{language_code}"
    finally:
        connection.close()


# ----------------------------------------------------------------- record_result


def test_record_result_appends_an_attempt(instance: Path) -> None:
    """Recording a completion advances the learner."""
    outcome = store.record_result("sample-learner", "es-03-numbers", "completed", score=88, instance_path=instance)

    assert outcome.recorded is True
    assert outcome.reason is None
    assert outcome.attempt is not None
    assert outcome.attempt.score == 88

    progress = store.get_progress("sample-learner", "es", instance_path=instance)
    assert progress is not None
    assert progress.next_lesson is not None
    # es-03 is done, but Cycle 10 at position 1 was never attempted and comes first.
    assert progress.next_lesson.id == "es-fast-01-getting-started-in-class"
    assert len(progress.attempts) == 4


def test_record_result_is_append_only(instance: Path) -> None:
    """A second attempt at the same lesson is a second row, not an update."""
    before = _count_results(instance)
    for _ in range(2):
        assert (
            store.record_result("sample-learner", "es-03-numbers", "partial", instance_path=instance).recorded is True
        )

    assert _count_results(instance) == before + 2


def test_record_result_unknown_lesson_is_reported(instance: Path) -> None:
    """An unknown lesson is named, not left to a constraint violation."""
    before = _count_results(instance)
    outcome = store.record_result("sample-learner", "no-such-lesson", "completed", instance_path=instance)

    assert outcome.recorded is False
    assert outcome.reason == "unknown_lesson"
    assert outcome.attempt is None
    assert _count_results(instance) == before


def test_record_result_unknown_learner_is_reported(instance: Path) -> None:
    """Recording for an unknown learner must not silently succeed."""
    before = _count_results(instance)
    outcome = store.record_result("nobody", "es-01-greetings", "completed", instance_path=instance)

    assert outcome.recorded is False
    assert outcome.reason == "unknown_learner"
    assert _count_results(instance) == before


@pytest.mark.parametrize("outcome_value", ["aced it", "COMPLETED", "done", ""])
def test_record_result_rejects_invalid_outcomes(instance: Path, outcome_value: str) -> None:
    """A model's guess at the vocabulary must not end the turn, or be recorded."""
    before = _count_results(instance)
    outcome = store.record_result("sample-learner", "es-01-greetings", outcome_value, instance_path=instance)

    assert outcome.recorded is False
    assert outcome.reason == "invalid_outcome"
    assert _count_results(instance) == before


@pytest.mark.parametrize(("score", "expected"), [(-1, False), (101, False), (0, True), (100, True), (None, True)])
def test_record_result_validates_score_range(instance: Path, score: int | None, expected: bool) -> None:
    """Scores outside 0-100 are refused before any write."""
    outcome = store.record_result("sample-learner", "es-01-greetings", "partial", score=score, instance_path=instance)

    assert outcome.recorded is expected
    if not expected:
        assert outcome.reason == "invalid_score"


def test_record_result_defaults_recorded_at_to_now(instance: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An unspecified timestamp is filled in, and an explicit one is honoured."""
    monkeypatch.setattr(store, "utc_now_ms", lambda: 4242)

    defaulted = store.record_result("sample-learner", "es-01-greetings", "partial", instance_path=instance)
    assert defaulted.attempt is not None
    assert defaulted.attempt.recorded_at == 4242

    explicit = store.record_result(
        "sample-learner", "es-01-greetings", "partial", recorded_at=99, instance_path=instance
    )
    assert explicit.attempt is not None
    assert explicit.attempt.recorded_at == 99


def test_record_result_writes_only_the_named_learners_row(instance: Path) -> None:
    """Writing for one learner must leave another's history untouched."""
    _add_learner(instance, "other-learner")
    assert (
        store.record_result("other-learner", "es-01-greetings", "completed", instance_path=instance).recorded is True
    )

    mine = store.get_progress("sample-learner", "es", instance_path=instance)
    assert mine is not None
    assert len(mine.attempts) == 3


def test_learner_supplied_text_cannot_inject_sql(instance: Path) -> None:
    """Parameterised statements only: hostile text is a value, never syntax."""
    before = _count_results(instance)
    hostile_lesson = "es-01-greetings'; DROP TABLE lesson_results;--"
    hostile_learner = "sample-learner' OR '1'='1"

    assert (
        store.record_result("sample-learner", hostile_lesson, "completed", instance_path=instance).reason
        == "unknown_lesson"
    )
    assert (
        store.record_result(hostile_learner, "es-01-greetings", "completed", instance_path=instance).reason
        == "unknown_learner"
    )
    assert store.get_profile(hostile_learner, instance_path=instance) is None

    assert _count_results(instance) == before, "the table must still be intact"


@pytest.mark.parametrize("score", ["high", 3.5, True, [], {"a": 1}])
def test_record_result_rejects_a_non_integer_score(instance: Path, score: object) -> None:
    """A score of any type must land on a reason code, never an exception.

    The caller is an LLM tool layer, so `score` can be any JSON value. Comparing a str
    against an int raises TypeError straight through the conversation loop, which is
    exactly what this function exists to prevent.
    """
    before = _count_results(instance)
    outcome = store.record_result("sample-learner", "es-01-greetings", "partial", score=score, instance_path=instance)

    assert outcome.recorded is False
    assert outcome.reason == "invalid_score"
    assert _count_results(instance) == before


@pytest.mark.parametrize(
    "recorded_at",
    [
        "yesterday",
        "1700000000000",  # coerced and stored, before this check
        "00042",  # likewise
        " 42",  # likewise, leading space and all
        "1e3",  # likewise, as 1000
        3.0,  # likewise, as 3 -- and a JSON number often arrives as a float
        3.5,
        True,
        False,
        [],
        {"a": 1},
        (1,),
    ],
)
def test_record_result_rejects_a_non_integer_recorded_at(instance: Path, recorded_at: object) -> None:
    """A timestamp of any type must land on a caller-error code, never a storage one.

    The task framed this as a bad value coming back as storage_unavailable. Measuring
    it showed something worse, because a STRICT column is not the type gate it looks
    like: SQLite coerces any TEXT or REAL that converts losslessly, so "1700000000000",
    "00042", " 42", "1e3", 3.0 and True were all silently ACCEPTED and written. Only
    a lossy float reached rejected_by_database and only an unbindable type reached
    storage_unavailable.

    So the defect had two halves. The misleading code is the smaller one -- though it
    still matters, because an operator who learns to ignore storage_unavailable will
    ignore a real storage failure too. The larger half is the rows that looked
    legitimate for ever.
    """
    before = _count_results(instance)
    outcome = store.record_result(
        "sample-learner", "es-01-greetings", "partial", recorded_at=recorded_at, instance_path=instance
    )

    assert outcome.recorded is False
    assert outcome.reason == "invalid_recorded_at"
    assert _count_results(instance) == before, "nothing may be written"


def test_a_strict_column_does_not_refuse_what_it_can_coerce(instance: Path) -> None:
    """Why the Python check is load-bearing rather than belt-and-braces.

    It would be reasonable to assume STRICT made this check redundant. It does not,
    and the assumption is the reason the gap survived W5: a value only has to convert
    losslessly to be accepted, so a numeric string and a bool both become integers on
    the way in. This pins the database behaviour the check exists to cover, so that if
    a future SQLite tightens it, the reason this code is here is still on the record.
    """
    connection = store.connect(instance)
    try:
        connection.execute(
            "INSERT INTO lesson_results (learner_id, lesson_id, outcome, score, recorded_at) VALUES (?, ?, ?, ?, ?)",
            ("sample-learner", "es-01-greetings", "partial", None, "1700000000000"),
        )
        stored = connection.execute(
            "SELECT recorded_at, typeof(recorded_at) FROM lesson_results WHERE learner_id = ? "
            "ORDER BY id DESC LIMIT 1",
            ("sample-learner",),
        ).fetchone()
    finally:
        connection.close()

    assert stored[0] == 1_700_000_000_000
    assert stored[1] == "integer", "a numeric string is coerced, not refused"


def test_a_bool_recorded_at_was_silently_written_before_this_check(instance: Path) -> None:
    """The worst of the shapes, because it did not fail at all.

    bool is a subclass of int and the column carries no CHECK constraint, so True
    satisfied SQLite and recorded a real attempt timestamped 1ms after the epoch --
    a row that looks legitimate for ever. score excludes bool explicitly for the same
    reason; this is the sibling parameter catching up.
    """
    before = _count_results(instance)

    outcome = store.record_result(
        "sample-learner", "es-01-greetings", "partial", recorded_at=True, instance_path=instance
    )

    assert outcome.recorded is False
    assert outcome.reason == "invalid_recorded_at"
    assert outcome.attempt is None
    assert _count_results(instance) == before, "a bool must not become a timestamp of 1"


@pytest.mark.parametrize("recorded_at", [0, -1, 1, 1_700_000_000_000, 10**18, 2**63 - 1, -(2**63)])
def test_record_result_accepts_any_whole_number_of_milliseconds(instance: Path, recorded_at: int) -> None:
    """The representable range is pinned at both ends; no date judgement is made.

    Two different rules, and this test is where the difference shows. The 64-bit bound
    IS checked, and 2**63-1 and -(2**63) here are its boundaries -- not a policy but
    the column's physical extent, without which the insert raises rather than returns.

    A SEMANTIC range is deliberately absent, which is why 0 and -1 are accepted. A
    "is this a plausible date" rule invented in Python would live in one place while
    score's lives in two, and would be invisible to the direct-SQL writers this very
    test file uses. If one is ever wanted it belongs beside score's CHECK.
    """
    outcome = store.record_result(
        "sample-learner", "es-01-greetings", "partial", recorded_at=recorded_at, instance_path=instance
    )

    assert outcome.recorded is True
    assert outcome.attempt is not None
    assert outcome.attempt.recorded_at == recorded_at


@pytest.mark.parametrize("recorded_at", [2**63, -(2**63) - 1, 10**19, 10**30])
def test_an_integer_too_large_for_the_column_is_refused_rather_than_raised(instance: Path, recorded_at: int) -> None:
    """The one shape that got past the type check and then ended the turn.

    An int satisfies isinstance, so it reached the insert -- where sqlite3 raises
    OverflowError while BINDING, before the database sees the statement. OverflowError
    is neither sqlite3.Error nor ValueError, so no except clause caught it and it
    travelled up through the conversation loop. That is the exact failure record_result
    exists to prevent, and it survived the first version of this fix.

    This is representability, not judgement about the date: -(2**63) and 2**63-1 are
    both accepted by the test above.
    """
    before = _count_results(instance)

    outcome = store.record_result(
        "sample-learner", "es-01-greetings", "partial", recorded_at=recorded_at, instance_path=instance
    )

    assert outcome.recorded is False
    assert outcome.reason == "invalid_recorded_at"
    assert _count_results(instance) == before


def test_an_explicitly_timestamped_attempt_reads_back_through_get_progress(instance: Path) -> None:
    """The valid path end to end, so the new refusal cannot have narrowed it."""
    assert (
        store.record_result(
            "sample-learner", "es-01-greetings", "completed", recorded_at=1_700_000_000_000, instance_path=instance
        ).recorded
        is True
    )

    progress = store.get_progress("sample-learner", "es", instance_path=instance)

    assert progress is not None
    assert 1_700_000_000_000 in [attempt.recorded_at for attempt in progress.attempts]


def test_record_result_never_raises_whatever_the_timestamp(instance: Path) -> None:
    """The contract the whole function exists for, stated as a test rather than a docstring.

    The caller is a conversation tool, so an exception here ends the turn. object() is
    in the list on purpose: it is not JSON, but neither was the assumption that only
    JSON arrives.
    """
    for recorded_at in ["x", 3.5, True, [], {}, (), object(), float("nan"), b"1700000000000", 2**63, 10**30]:
        outcome = store.record_result(
            "sample-learner", "es-01-greetings", "partial", recorded_at=recorded_at, instance_path=instance
        )
        assert outcome.recorded is False, recorded_at
        assert outcome.reason in learners.RECORD_REASONS, recorded_at


# Every outcome type the store can return, paired with the vocabulary that publishes
# its reason codes. A LIST, because there is more than one writer now: the guards below
# were written when RecordResultOutcome was the only one, and SaveFaceprintOutcome
# joined the set without them noticing -- which is this repository's most repeated
# defect, a rule applied to one member while its sibling kept the bug. Adding a writer
# means adding a line here, and both guards widen with it.
_PUBLISHED_VOCABULARIES = [
    ("RecordResultOutcome", learners.RECORD_REASONS),
    ("SaveFaceprintOutcome", learners.FACEPRINT_REASONS),
    ("ConsentOutcome", learners.CONSENT_REASONS),
]


@pytest.mark.parametrize(("outcome_type", "vocabulary"), _PUBLISHED_VOCABULARIES)
def test_every_reason_the_store_can_return_is_in_the_published_vocabulary(
    outcome_type: str, vocabulary: tuple[str, ...]
) -> None:
    """A code a caller cannot anticipate is not an interface.

    The vocabulary is what a caller switches on. A reason returned from the module but
    missing from it would reach the tutor as an unknown string, and the branch that
    turns codes into speech would have nothing to say.
    """
    source = Path(store.__file__).resolve().read_text(encoding="utf-8")

    returned: set[str] = set()
    unreadable: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
        if name != outcome_type:
            continue
        given = [keyword.value for keyword in node.keywords if keyword.arg == "reason"]
        # A positional reason, or one that is not a literal, is a code this scan cannot
        # read -- and a scan that quietly skips what it cannot read proves only that the
        # parts it could read were fine.
        if len(node.args) > 1:
            unreadable.append(f"line {node.lineno}: reason passed positionally")
        for value in given:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                returned.add(value.value)
            else:
                unreadable.append(f"line {node.lineno}: reason is {type(value).__name__}, not a literal")

    assert returned, f"the scan found no {outcome_type} reason codes at all, so it is proving nothing"
    assert not unreadable, unreadable
    assert returned <= set(vocabulary), sorted(returned - set(vocabulary))


@pytest.mark.parametrize(("outcome_type", "vocabulary"), _PUBLISHED_VOCABULARIES)
def test_every_published_reason_is_documented(outcome_type: str, vocabulary: tuple[str, ...]) -> None:
    """The other half of the same contract, and the half a reader depends on.

    docs/learner-database.md's reason tables are where a code stops being a bare string
    and starts meaning something. Adding a code to a vocabulary without a row there
    leaves a caller able to switch on it and unable to find out what it means.

    EVERY such table is scanned, not the first one. Slicing at the first heading was
    what let the faceprint vocabulary go undocumented-but-unnoticed: its table exists,
    and a scan that stopped at record_result's table would never have reached it.
    """
    doc = (Path(__file__).resolve().parents[2] / "docs" / "learner-database.md").read_text(encoding="utf-8")

    # Reason tables only. Scanning the whole file would count the schema tables too,
    # and those carry rows named `outcome`, `score` and `learner_id` -- so a future
    # reason code sharing a column name would read as documented by a row that says
    # nothing about reason codes.
    heading = "| `reason` | Cause |"
    starts = [i for i in range(len(doc)) if doc.startswith(heading, i)]
    assert len(starts) >= len(_PUBLISHED_VOCABULARIES), (
        f"found {len(starts)} reason table(s) for {len(_PUBLISHED_VOCABULARIES)} published "
        "vocabularies; each one needs its own documented table under this heading"
    )

    tables = [
        set(re.findall(r"^\| `([a-z_]+)` \|", doc[start : doc.index("\n\n", start)], re.MULTILINE)) for start in starts
    ]

    assert all("outcome" not in table for table in tables), "a slice leaked into a schema table"

    # ONE table must document the whole vocabulary -- not the union of all of them.
    # The union was vacuous for exactly the codes that matter most: unknown_learner,
    # rejected_by_database and storage_unavailable appear in BOTH vocabularies, so
    # three of the five faceprint codes could be deleted from the faceprint table and
    # still be "documented" by record_result's. Measured: deleting the
    # unknown_learner row from the faceprint table left the union form green.
    missing_from_every_table = [sorted(set(vocabulary) - table) for table in tables]
    assert any(not missing for missing in missing_from_every_table), (
        f"no single reason table documents all of {outcome_type}'s codes; "
        f"closest tables are missing {sorted(missing_from_every_table, key=len)[:2]}"
    )


def test_store_is_available_separates_absence_from_breakage(tmp_path: Path, instance: Path) -> None:
    """Both readers answer None twice over, so a caller needs this to tell which it is.

    Saying "I do not teach that language" when the database is simply unreadable would be a
    confident falsehood, which is worse than admitting the lookup failed.
    """
    assert store.store_is_available(instance) is True
    assert store.get_progress("sample-learner", UNTAUGHT_CODE, instance_path=instance) is None, "not taught"

    broken = tmp_path / "no-database"
    broken.mkdir()
    assert store.store_is_available(broken) is False
    assert store.get_progress("sample-learner", "es", instance_path=broken) is None, "unreadable"


# -------------------------------------------------------- degrading and data shape


def test_missing_database_degrades_without_raising(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """With no database at all the interface reports absence and stays quiet about ids."""
    with caplog.at_level(logging.WARNING):
        assert store.get_profile("sample-learner", instance_path=tmp_path) is None
        assert store.get_progress("sample-learner", "es", instance_path=tmp_path) is None
        outcome = store.record_result("sample-learner", "es-01-greetings", "completed", instance_path=tmp_path)

    assert outcome.recorded is False
    assert outcome.reason == "storage_unavailable"
    assert "sample-learner" not in caplog.text, "learner ids are personal data and must not be logged"


def test_nothing_sqlite_escapes_the_store(instance: Path) -> None:
    """No returned value may carry a database type into a caller."""
    leaky = (sqlite3.Row, sqlite3.Cursor, sqlite3.Connection)

    returned: list[object] = []
    profile = store.get_profile("sample-learner", instance_path=instance)
    progress = store.get_progress("sample-learner", "es", instance_path=instance)
    assert profile is not None and progress is not None
    returned.extend([profile, progress, *progress.completed, *progress.remaining, *progress.attempts])

    for value in returned:
        assert dataclasses.is_dataclass(value)
        for field in dataclasses.fields(value):
            assert not isinstance(getattr(value, field.name), leaky)


def test_concurrent_access_is_safe(instance: Path) -> None:
    """Readers and writers from several threads must not collide."""
    before = _count_results(instance)
    writes = 8

    def write(_: int) -> bool:
        return store.record_result("sample-learner", "es-04-ordering-food", "partial", instance_path=instance).recorded

    def read(_: int) -> bool:
        return store.get_progress("sample-learner", "es", instance_path=instance) is not None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(write, range(writes))) + list(pool.map(read, range(writes)))

    assert all(results)
    assert _count_results(instance) == before + writes


# ------------------------------------------------------------- structural guards


# The three tables that hold anything about a person. Everything else in this schema --
# languages, lessons, schema_meta -- is shared reference data.
#
# This list is the test's own copy on purpose -- it must not import store's, or the
# guard would agree with whatever the module says rather than checking it.
_PERSONAL_TABLES = ("learners", "lesson_results", "faceprints")


def _personal(sql: str) -> bool:
    """Report whether a statement reads or writes anything about a person.

    Naming a personal table anywhere at all is enough. This used to look for the exact
    spellings "lesson_results", "FROM learners" and "INTO learners", which tied the
    answer to how the statement happened to be typed: `UPDATE learners SET ...` matched
    none of the three and was invisible, and so were `FROM main.learners`, `FROM
    "learners"`, a second space, and lowercase `from learners`.

    Matching the table name itself rather than the clause around it has no such
    spellings to miss. It is deliberately over-broad -- a statement that merely
    mentions one of these words in a comment is treated as personal -- because every
    error it can make is in the direction of demanding a learner filter that was not
    strictly needed, and none is in the direction of missing one.
    """
    return any(re.search(rf"\b{table}\b", sql, re.IGNORECASE) for table in _PERSONAL_TABLES)


# The verbs that can introduce a statement touching personal data. An allow-list of
# what a statement may BE, rather than the two spellings that happened to exist: the
# filter here used to read "SELECT" or "INSERT", so a named DELETE or UPDATE constant
# naming a personal table was invisible to this guard and could sit unregistered
# forever. _DELETE_FACEPRINT_SQL was the first such constant in the module and was
# measured escaping it, which is what prompted widening the set rather than adding the
# one verb that had just been needed.
_STATEMENT_VERBS = ("SELECT", "INSERT", "UPDATE", "DELETE", "REPLACE")


def test_every_module_level_query_touching_personal_data_is_scoped() -> None:
    """A named statement touching personal data cannot quietly skip the scoping guard.

    No exemptions: NEXT_LESSON_SQL is registered rather than skipped, because an
    exemption here is a precedent for skipping the next one.

    ONE STATEMENT IS IN A SECOND REGISTER, and it is admitted by NAME rather than by
    shape. Recognition compares a face against the household, which is a read of
    every learner and cannot be written any other way; _reads_every_learners_faceprint
    approves exactly that statement and refuses a write, a parameter, a join, a
    SELECT * or anything not enumerated. Widening this check to "or mentions
    faceprints" would be the bypass -- membership of the enumerated tuple is the
    whole control, and the test below pins that tuple at one entry.
    """
    for name, value in vars(store).items():
        if not isinstance(value, str) or not name.isupper() and not name.startswith("_"):
            continue
        if not isinstance(value, str) or not any(verb in value for verb in _STATEMENT_VERBS):
            continue
        if _personal(value):
            registered = value in store._LEARNER_SCOPED_SQL or value in store._EVERY_LEARNERS_FACEPRINT_SQL
            assert registered, f"{name} touches personal data unscoped"


def test_exactly_one_statement_reads_across_learners() -> None:
    """The cross-learner category is one statement, and growing it is a decision.

    Not a style rule. Every reader in this module is scoped to one learner by a rule
    that refuses anything else at import time, and recognition is the single
    operation that cannot be expressed that way. A second entry here means somebody
    decided a second thing cannot either -- which may be true, and must be argued
    rather than appended.
    """
    assert len(store._EVERY_LEARNERS_FACEPRINT_SQL) == 1

    # And it is still refused by the scoping rule. The exemption is a separate,
    # narrower rule -- nothing about _learner_scoped was loosened to admit it.
    with pytest.raises(ValueError, match="must constrain every personal relation"):
        store._learner_scoped(store._HOUSEHOLD_FACEPRINTS_SQL)


def test_the_cross_learner_read_is_the_scoped_read_with_its_filter_removed() -> None:
    """The column guard, and it maintains itself as the table changes.

    A hand-written allow-list of permitted columns would need updating by whoever
    adds a column, which is exactly the person not thinking about it. Deriving the
    unscoped statement from the scoped one instead means the cross-learner read
    cannot gain a column, a join to learners for a display name, or a join to
    lesson_results for history unless the SCOPED statement gains the same -- and that
    one is judged by _learner_scoped.
    """
    assert store._HOUSEHOLD_FACEPRINTS_SQL + " WHERE faceprints.learner_id = ?" == store._FACEPRINT_SQL


@pytest.mark.parametrize(
    ("statement", "refused_because"),
    [
        ("DELETE FROM faceprints", "only a SELECT"),
        ("UPDATE faceprints SET vector = vector", "only a SELECT"),
        ("SELECT learner_id, vector FROM faceprints WHERE faceprints.learner_id = ?", "takes no parameters"),
        ("SELECT * FROM faceprints", "must name its columns"),
        (
            "SELECT f.learner_id, l.display_name FROM faceprints AS f JOIN learners AS l ON l.id = f.learner_id",
            "faceprints and nothing else",
        ),
        ("SELECT learner_id, recorded_at FROM lesson_results", "faceprints and nothing else"),
    ],
)
def test_the_cross_learner_rule_refuses_everything_but_the_one_statement(statement: str, refused_because: str) -> None:
    """Each condition of the rule, refused for the reason it names.

    The join-to-learners case is the one that matters most: it is how a display name
    would leave this module through a read nobody scoped, and it is refused for
    naming a second personal relation rather than for mentioning a column.
    """
    with pytest.raises(ValueError, match=refused_because):
        store._reads_every_learners_faceprint(statement)


# --------------------------------------------------------------- the scoping rule

# Each of these reads every learner while naming a learner filter somewhere in its
# text, and each was ACCEPTED by the substring rule this replaced. The second element
# is the phrase the refusal must contain: a refusal for some other reason would be
# this test going green without checking the thing it is about.
_CROSS_LEARNER_STATEMENTS: dict[str, tuple[str, str]] = {
    "a self-join": (
        "SELECT r2.learner_id, r2.outcome FROM lesson_results AS r "
        "JOIN lesson_results AS r2 ON r2.lesson_id = r.lesson_id WHERE r.learner_id = ?",
        "nothing constrains lesson_results (as r2)",
    ),
    "an OR-widened predicate": (
        "SELECT outcome FROM lesson_results WHERE learner_id = ? OR outcome = 'completed'",
        "a WHERE clause with a top-level OR does not constrain what it looks like it does",
    ),
    "an unfiltered UNION leg": (
        "SELECT outcome FROM lesson_results WHERE learner_id = ? UNION ALL SELECT outcome FROM lesson_results",
        "other legs carry no filter of their own",
    ),
    "a correlated-subquery DELETE": (
        "DELETE FROM lesson_results WHERE lesson_id IN (SELECT lesson_id FROM lesson_results WHERE learner_id = ?)",
        "two relations are both called lesson_results",
    ),
}


@pytest.mark.parametrize("shape", sorted(_CROSS_LEARNER_STATEMENTS))
def test_a_statement_that_reads_every_learner_is_refused(shape: str) -> None:
    """A learner filter somewhere in the text is not the same as a scoped statement.

    A household comparison view writes the self-join and a "forget this lesson" action
    writes the DELETE, so none of these is an exotic shape -- they are the statements
    the next two features would produce.
    """
    sql, reason = _CROSS_LEARNER_STATEMENTS[shape]

    with pytest.raises(ValueError) as refusal:
        store._learner_scoped(sql)

    assert reason in str(refusal.value), (shape, str(refusal.value))


def test_every_registered_statement_is_still_accepted() -> None:
    """A rule that refuses a statement this module uses stops the app booting.

    Importing this file already proves it once, because a refusal would have raised
    during store's import. Saying it again here is what makes the failure name the
    statement instead of arriving as a collection error against every test in the file.
    """
    assert len(store._LEARNER_SCOPED_SQL) == 18

    for sql in store._LEARNER_SCOPED_SQL:
        assert store._learner_scoped(sql) == sql


def test_an_insert_is_scoped_by_its_first_column_and_carries_no_filter_at_all() -> None:
    """The insert rule stays separate because an insert has no WHERE clause to read."""
    sql = "INSERT INTO lesson_results (learner_id, lesson_id, outcome) VALUES (?, ?, ?)"
    assert "WHERE" not in sql.upper(), "this test is about a statement with no filter"

    assert store._learner_scoped(sql) == sql

    writes_someone_else_first = "INSERT INTO lesson_results (lesson_id, learner_id) VALUES (?, ?)"
    with pytest.raises(ValueError, match="first column"):
        store._learner_scoped(writes_someone_else_first)


def test_an_insert_that_also_reads_has_to_satisfy_both_rules() -> None:
    """INSERT ... SELECT is a read as well as a write.

    Writing learner_id first says nothing about the rows the SELECT reaches, so the
    first-column rule alone would let one learner's history be copied onto another.
    """
    reads_everyone = (
        "INSERT INTO lesson_results (learner_id, lesson_id) SELECT learner_id, lesson_id FROM lesson_results"
    )
    reads_one = (
        "INSERT INTO lesson_results (learner_id, lesson_id) "
        "SELECT r.learner_id, r.lesson_id FROM lesson_results AS r WHERE r.learner_id = ?"
    )

    with pytest.raises(ValueError, match="nothing constrains lesson_results"):
        store._learner_scoped(reads_everyone)

    assert store._learner_scoped(reads_one) == reads_one


def test_a_filter_inside_not_exists_is_what_scopes_the_statement_around_it() -> None:
    """NEXT_LESSON_SQL is this shape, so it is a live statement rather than a case.

    The filter is removed to show which text the acceptance rests on. Without that,
    "it passes" would be equally true of a rule that had stopped reading subqueries.
    """
    assert store._learner_scoped(store.NEXT_LESSON_SQL) == store.NEXT_LESSON_SQL

    without_the_filter = store.NEXT_LESSON_SQL.replace("r.learner_id = ? AND ", "", 1)
    assert without_the_filter != store.NEXT_LESSON_SQL, "the filter was not where this test thought"

    with pytest.raises(ValueError, match="nothing constrains lesson_results"):
        store._learner_scoped(without_the_filter)


def test_one_filter_does_not_cover_a_second_personal_relation() -> None:
    """Two personal relations need two filters, even when SQL would infer the second.

    `b.id = a.learner_id` does scope `learners` in practice, transitively. This rule
    does not follow transitivity, so it refuses and the statement gets an explicit
    filter instead. That is the conservative direction on purpose.
    """
    sql = "SELECT a.outcome FROM lesson_results AS a JOIN learners AS b ON b.id = a.learner_id WHERE a.learner_id = ?"

    with pytest.raises(ValueError, match="nothing constrains learners"):
        store._learner_scoped(sql)

    spelled_out = sql + " AND b.id = ?"
    assert store._learner_scoped(spelled_out) == spelled_out


# Statements that bind TWO learner parameters. Each personal relation they name is
# constrained by its own filter, so they satisfy the rule -- and binding two different
# ids returns two different people. Verified by execution against a two-learner database
# during D11's round-7 review: the first returned ('alice','bob').
#
# This list pins a KNOWN LIMIT, not a guarantee. If one of these starts being refused,
# that is not a regression -- it is someone strengthening the rule, and the thing to do
# is update the paragraph in docs/learner-database.md that this test exists to keep
# honest, then delete the entry from here. The failure message says so too.
_TWO_LEARNER_PARAMETERS: dict[str, str] = {
    "a join of both personal tables": (
        "SELECT l.id, r.learner_id FROM learners AS l JOIN lesson_results AS r ON 1 "
        "WHERE l.id = ? AND r.learner_id = ?"
    ),
    "a subquery beside the outer query": (
        "SELECT r.outcome, (SELECT group_concat(z.outcome) FROM lesson_results AS z WHERE z.learner_id = ?) "
        "FROM lesson_results AS r WHERE r.learner_id = ?"
    ),
    "an UPDATE ... FROM": (
        "UPDATE lesson_results SET score = z.score FROM lesson_results AS z "
        "WHERE lesson_results.learner_id = ? AND z.learner_id = ?"
    ),
}


@pytest.mark.parametrize("shape", sorted(_TWO_LEARNER_PARAMETERS))
def test_the_rule_constrains_each_relation_and_not_all_to_one_learner(shape: str) -> None:
    """What the guard certifies is narrower than it sounds, and this is the difference.

    "Every personal relation is constrained to one learner" is true of these statements.
    "Every personal relation is constrained to the SAME learner" is not, and the rule does
    not check it: each relation is judged against the filters independently, so two
    parameters bound to two people satisfy it.

    Nothing reaches this today -- the store's exported functions each take a single
    learner_id and no registered statement has two learner placeholders -- and closing it
    by refusing every statement with two learner filters would reject the legitimate
    two-table reads that test_one_filter_does_not_cover_a_second_personal_relation
    deliberately accepts. So the limit is documented rather than closed, and this test is
    what stops the documentation drifting away from the code, which is the failure D11
    spent seven review rounds removing.

    IF THIS TEST FAILS because a statement here is now refused, that is probably someone
    strengthening the rule on purpose. Update the "It proves the shape of a filter and
    never its value" section of docs/learner-database.md to match, then remove the entry.
    """
    sql = _TWO_LEARNER_PARAMETERS[shape]

    # The refusal has to be caught rather than allowed to propagate. A bare call would
    # fail this test with a ValueError traceback, and the whole point of the test is the
    # instruction below -- which a traceback would never show.
    try:
        assert store._learner_scoped(sql) == sql
    except ValueError as refusal:
        raise AssertionError(
            f"{shape} is now refused: {refusal}. If that was deliberate, "
            "docs/learner-database.md still tells readers it is accepted -- update the "
            '"It proves the shape of a filter and never its value" section to match, then '
            "delete this case."
        ) from None


def test_an_unqualified_filter_is_trusted_only_where_it_cannot_be_ambiguous() -> None:
    """`learner_id = ?` names no relation, so it proves scoping only when there is one."""
    alone = "SELECT outcome FROM lesson_results WHERE learner_id = ?"
    joined = "SELECT r.outcome FROM lesson_results AS r JOIN lessons AS l ON l.id = r.lesson_id WHERE learner_id = ?"

    assert store._learner_scoped(alone) == alone

    with pytest.raises(ValueError, match="does not say which relation it constrains"):
        store._learner_scoped(joined)


# The same defect as the four above, in shapes two earlier passes at this rule still let
# through: the filter is present, addressed to the right relation, and constrains
# nothing. Every one was found by probing the rule -- the first five by me after the
# task's own tests were green, the rest by the reviewers after those were green too.
# None of them was found by reading the code.
_NEUTRALISED_FILTERS: dict[str, tuple[str, str]] = {
    "a filter inside NOT EXISTS": (
        "SELECT r.outcome FROM lesson_results AS r "
        "WHERE NOT EXISTS (SELECT 1 FROM lessons AS l WHERE r.learner_id = ?)",
        "nothing constrains lesson_results (as r)",
    ),
    "a filter inside an IN subquery": (
        "SELECT r.outcome FROM lesson_results AS r "
        "WHERE r.lesson_id IN (SELECT l.id FROM lessons AS l WHERE r.learner_id = ?)",
        "nothing constrains lesson_results (as r)",
    ),
    "a filter written only in an outer join's ON": (
        "SELECT r.outcome FROM lesson_results AS r LEFT JOIN lessons AS l ON r.learner_id = ?",
        "nothing constrains lesson_results (as r)",
    ),
    "an ON filter laundered behind a subquery": (
        "SELECT r.learner_id, r.outcome FROM lesson_results AS r "
        "LEFT JOIN lessons AS l ON l.id IN (SELECT id FROM lessons WHERE 1 = 1) AND r.learner_id = ?",
        "nothing constrains lesson_results (as r)",
    ),
    "a filter smuggled into an ORDER BY": (
        "SELECT learner_id, outcome FROM lesson_results ORDER BY (SELECT 1 WHERE 1 = 1), learner_id = ?",
        "nothing constrains lesson_results",
    ),
    "a filter smuggled into a SET list": (
        "UPDATE lesson_results SET outcome = (SELECT 'skipped' WHERE 1 = 1), learner_id = ?",
        "writes the column that says which learner the row belongs to",
    ),
    "an update that re-attributes the row": (
        "UPDATE lesson_results SET learner_id = 'bob' WHERE learner_id = ?",
        "writes the column that says which learner the row belongs to",
    ),
    "an update that re-attributes a learner": (
        "UPDATE learners SET id = 'bob' WHERE id = ?",
        "writes the column that says which learner the row belongs to",
    ),
    "a negated filter": (
        "SELECT outcome FROM lesson_results WHERE NOT learner_id = ?",
        "not as a conjunct of its own",
    ),
    "a negation laundered behind a subquery": (
        "SELECT outcome FROM lesson_results WHERE lesson_id IN (SELECT id FROM lessons) "
        "AND NOT (1 IN (SELECT 1 WHERE 1 = 1) AND learner_id = ?)",
        "not as a conjunct of its own",
    ),
    "a filter neutralised by CASE": (
        "SELECT outcome FROM lesson_results WHERE CASE WHEN learner_id = ? THEN 1 ELSE 1 END = 1",
        "not as a conjunct of its own",
    ),
    "a filter neutralised by IIF": (
        "SELECT outcome FROM lesson_results WHERE IIF(learner_id = ?, 1, 1)",
        "not as a conjunct of its own",
    ),
    "a filter neutralised by a function call": (
        "SELECT outcome FROM lesson_results WHERE max(learner_id = ?, 1)",
        "not as a conjunct of its own",
    ),
    "a filter compared against a constant": (
        "SELECT outcome FROM lesson_results WHERE (learner_id = ?) = 0",
        "not as a conjunct of its own",
    ),
    "a filter in a chained comparison": (
        "SELECT outcome FROM lesson_results WHERE learner_id = ? = 0",
        "not as a conjunct of its own",
    ),
    "an aggregate's FILTER clause": (
        "SELECT group_concat(r.learner_id) AS who, COUNT(*) FILTER (WHERE r.learner_id = ?) AS mine "
        "FROM lesson_results AS r",
        "a WHERE straight after an opening bracket",
    ),
    "an aggregate's FILTER clause in HAVING": (
        "SELECT r.lesson_id FROM lesson_results AS r GROUP BY r.lesson_id "
        "HAVING COUNT(*) FILTER (WHERE r.learner_id = ?) >= 0",
        "a WHERE straight after an opening bracket",
    ),
    "an aggregate's FILTER clause driving a write": (
        "INSERT INTO lesson_results (learner_id, lesson_id, outcome, score, recorded_at) "
        "SELECT ?, r.lesson_id, r.outcome, r.score, r.recorded_at FROM lesson_results AS r "
        "GROUP BY r.id HAVING COUNT(*) FILTER (WHERE r.learner_id = ?) >= 0",
        "a WHERE straight after an opening bracket",
    ),
}


@pytest.mark.parametrize("shape", sorted(_NEUTRALISED_FILTERS))
def test_a_filter_that_does_not_constrain_its_own_relation_is_refused(shape: str) -> None:
    """Present, correctly addressed, and constraining nothing.

    `WHERE NOT learner_id = ?` is the cheapest cross-learner read in this list; the SET
    entry is worse than a read, being an UPDATE with no WHERE clause at all that
    reassigns every row in lesson_results to one learner; and the FILTER entries read
    every learner while the rule credits the aggregate's own WHERE as a row filter.

    The rule refuses all of these by requiring the WHERE conjunct to BE the filter rather
    than merely to contain one. Naming the ways a filter can be neutralised was the
    earlier design and it is why this list kept growing -- `IIF` alone is SQLite's
    documented equivalent of the `CASE` form a denylist had already been taught.

    Each shape carries the phrase its refusal must contain. A bare "it raised" would let
    a regression that refused one of these for an unrelated reason -- "two relations are
    both called r", say, or a readability refusal -- report green, and this file has
    already shipped one assertion loose enough to do that.
    """
    sql, reason = _NEUTRALISED_FILTERS[shape]

    with pytest.raises(ValueError) as refusal:
        store._learner_scoped(sql)

    assert reason in str(refusal.value), (shape, str(refusal.value))


def test_the_verdict_does_not_depend_on_the_order_the_predicates_were_written_in() -> None:
    """A subquery in one conjunct must not change how the next conjunct is read.

    These two statements are the same statement. An earlier version accepted one and
    refused the other, because the subquery's own WHERE leaked into everything after its
    closing bracket -- which accepted a filter that constrained nothing in one order and
    refused a real filter in the other. Both directions of that are in this assertion.
    """
    subquery_first = (
        "SELECT outcome FROM lesson_results WHERE lesson_id IN (SELECT id FROM lessons) AND learner_id = ?"
    )
    filter_first = "SELECT outcome FROM lesson_results WHERE learner_id = ? AND lesson_id IN (SELECT id FROM lessons)"

    assert store._learner_scoped(subquery_first) == subquery_first
    assert store._learner_scoped(filter_first) == filter_first


def test_bracketing_a_predicate_for_readability_does_not_move_it() -> None:
    """Depth counts subqueries, not brackets.

    The counterpart to the NOT EXISTS case above: a filter one subquery deep cannot
    scope the statement around it, but a filter in brackets is in the same clause it
    looks like it is in, and refusing it would be a rule that punishes formatting.
    """
    bracketed = "SELECT outcome FROM lesson_results WHERE (learner_id = ?)"

    assert store._learner_scoped(bracketed) == bracketed


# SQLite's whole conflict-handling surface, not the spellings this module happens to
# look for. The first version of this test was parameterized over exactly the two
# branches the implementation grepped for, so it reported green while REPLACE INTO --
# the shortest spelling of the same construct, and SQLite's documented alias for
# INSERT OR REPLACE -- skipped the write rule entirely and replaced another learner's
# row. A test written from the code cannot find what the code forgot.
_WRITES_THAT_ARE_NOT_A_PLAIN_INSERT: dict[str, str] = {
    "REPLACE INTO": "REPLACE INTO lesson_results (learner_id, lesson_id) VALUES (?, ?)",
    "REPLACE INTO another learner's row": "REPLACE INTO learners (id, display_name, created_at) VALUES (?, ?, ?)",
    "INSERT OR REPLACE": "INSERT OR REPLACE INTO lesson_results (learner_id, lesson_id) VALUES (?, ?)",
    "INSERT OR IGNORE": "INSERT OR IGNORE INTO lesson_results (learner_id, lesson_id) VALUES (?, ?)",
    "ON CONFLICT DO UPDATE": (
        "INSERT INTO lesson_results (learner_id, lesson_id) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET lesson_id = 1"
    ),
    "ON CONFLICT DO NOTHING": (
        "INSERT INTO lesson_results (learner_id, lesson_id) VALUES (?, ?) ON CONFLICT(id) DO NOTHING"
    ),
    "a column list that is not written first": "INSERT INTO lesson_results (lesson_id, learner_id) VALUES (?, ?)",
    "no column list at all": "INSERT INTO lesson_results VALUES (?, ?, ?, ?, ?)",
}


@pytest.mark.parametrize("shape", sorted(_WRITES_THAT_ARE_NOT_A_PLAIN_INSERT))
def test_only_a_plain_insert_into_is_scoped_by_the_column_it_writes_first(shape: str) -> None:
    """The first-column proof only covers a row this statement brings into existence.

    REPLACE and ON CONFLICT overwrite whoever already holds the conflicting row, and the
    first column says nothing about who that is. Verified against a real database during
    review: REPLACE INTO learners replaced a second household member's row and
    cascade-deleted every one of their lesson_results.

    The rule names the one shape it accepts rather than the shapes it forbids, which is
    why this list can grow without the rule having to.
    """
    with pytest.raises(ValueError, match="learner-scoped write"):
        store._learner_scoped(_WRITES_THAT_ARE_NOT_A_PLAIN_INSERT[shape])


@pytest.mark.parametrize(
    ("shape", "sql"),
    [
        ("a read", "SELECT learner_id, outcome FROM lesson_results WHERE lesson_id = ? /* AND learner_id = ?"),
        ("an update", "UPDATE lesson_results SET outcome = ? /* WHERE learner_id = ?"),
        ("a delete", "DELETE FROM lesson_results /* WHERE learner_id = ?"),
        (
            "brackets inside the dead text",
            "SELECT outcome FROM lesson_results WHERE lesson_id = ? /* ) ) ) AND learner_id = ?",
        ),
    ],
)
def test_a_statement_the_database_would_truncate_is_refused(shape: str, sql: str) -> None:
    """SQLite ends an unterminated block comment at the end of the input.

    So the filter after the `/*` never reaches the database, while a reader that does not
    know this sees one. That is the dangerous direction -- the rule seeing MORE than the
    database does is exactly how a filter counts while constraining nothing. The trigger
    is a forgotten `*/` when commenting out the trailing clause of a multi-line SQL
    constant, which is the author mistake this guard exists to catch.
    """
    with pytest.raises(ValueError, match="unterminated block comment"):
        store._learner_scoped(sql)


@pytest.mark.parametrize(
    ("shape", "sql"),
    [
        (
            "more than one statement",
            "INSERT INTO lesson_results (learner_id, lesson_id) VALUES (?, ?); DELETE FROM lesson_results",
        ),
        ("an unterminated literal", "INSERT INTO lesson_results (learner_id, lesson_id) VALUES (?, 'partial)"),
    ],
)
def test_a_write_whose_text_cannot_be_read_is_refused_like_any_other(shape: str, sql: str) -> None:
    """Whether a statement can be READ is asked before which rule should judge it.

    The write branch used to return before these checks, so an insert carrying a second
    statement or a broken literal was accepted by a guard that had not read it. sqlite3
    would reject both at execution, but that is sqlite3's behaviour rather than anything
    this module asserts.
    """
    with pytest.raises(ValueError, match="one this rule can read"):
        store._learner_scoped(sql)


def test_a_comma_join_gets_the_same_verdict_as_the_join_it_is_shorthand_for() -> None:
    """A formatting choice must not change what the rule proves.

    `FROM a, b` puts a relation where the rule does not read one, which left it out of
    the count the unqualified-filter check depends on -- so writing a comma instead of
    JOIN silently disabled that check. Refused rather than counted: the JOIN spelling of
    the same query is read correctly, so nothing is lost except the shorthand.
    """
    spelled_with_join = "SELECT outcome FROM lesson_results JOIN lessons WHERE learner_id = ?"
    spelled_with_comma = "SELECT outcome FROM lesson_results, lessons WHERE learner_id = ?"

    with pytest.raises(ValueError):
        store._learner_scoped(spelled_with_join)
    with pytest.raises(ValueError, match="separated by a comma"):
        store._learner_scoped(spelled_with_comma)


# BETWEEN spells its own AND, and that AND is not a conjunction. A splitter that reads it
# as one hands back a tail that looks exactly like a learner filter while the database
# binds it as the BETWEEN's upper bound. Every one of these was executed against a
# two-learner database during review: the first returned the other learner's rows, the
# second and third emptied tables, the fourth rewrote everyone's history, and the fifth
# copied another learner's lesson into this one's.
_BETWEEN_SPELLS_ITS_OWN_AND: dict[str, str] = {
    "a read": "SELECT learner_id, lesson_id, outcome, score FROM lesson_results WHERE NOT score BETWEEN 0 AND learner_id = ?",
    "a delete": "DELETE FROM lesson_results WHERE NOT recorded_at BETWEEN 0 AND learner_id = ?",
    "a delete of every learner": "DELETE FROM learners WHERE NOT created_at BETWEEN 0 AND id = ?",
    "an update": "UPDATE lesson_results SET outcome = 'skipped' WHERE NOT score BETWEEN 0 AND learner_id = ?",
    "a subquery inside a VALUES list": (
        "INSERT INTO lesson_results (learner_id, lesson_id, outcome, score, recorded_at) VALUES "
        "(?, (SELECT r.lesson_id FROM lesson_results AS r WHERE NOT r.score BETWEEN 0 AND r.learner_id = ? "
        "ORDER BY r.id DESC LIMIT 1), ?, ?, ?)"
    ),
}


@pytest.mark.parametrize("shape", sorted(_BETWEEN_SPELLS_ITS_OWN_AND))
def test_a_top_level_between_is_refused_because_its_and_is_not_a_conjunction(shape: str) -> None:
    """The top-level OR defect, in the one other operator that overloads AND.

    `WHERE NOT score BETWEEN 0 AND learner_id = ?` is read by a splitter as
    `(NOT score BETWEEN 0) AND (learner_id = ?)` and by SQLite as
    `NOT ((score BETWEEN 0 AND learner_id) = ?)`, which is true for every row.

    BETWEEN is the only SQLite operator that spells its own AND, so refusing it is not
    the start of a denylist -- it is the complete set. Naming a complete set is the same
    move the filter allow-list makes.
    """
    with pytest.raises(ValueError, match="top-level BETWEEN"):
        store._learner_scoped(_BETWEEN_SPELLS_ITS_OWN_AND[shape])


def test_an_update_may_not_hand_a_row_to_a_different_learner() -> None:
    """Which rows a write touches says nothing about who it attributes them to.

    `UPDATE lesson_results SET learner_id = 'bob' WHERE learner_id = ?` has a real
    filter that constrains exactly one learner's rows -- and then hands those rows to
    somebody else. Executed against a two-learner database during review, it moved the
    bound learner's row to a second, entirely unconstrained id; no second parameter is
    needed, a literal suffices.

    This is the asymmetry the insert rule exists for ("an insert has no WHERE clause"),
    in the one statement that has both halves. Refusing outright costs nothing today --
    no registered statement rewrites a learner id -- and a future merge-profiles feature
    should have to declare itself rather than inherit the read rule's silence.
    """
    ordinary = "UPDATE lesson_results SET outcome = ? WHERE learner_id = ?"
    assert store._learner_scoped(ordinary) == ordinary

    for reassigns in (
        "UPDATE lesson_results SET learner_id = 'bob' WHERE learner_id = ?",
        "UPDATE lesson_results SET learner_id = ? WHERE learner_id = ?",
        "UPDATE learners SET id = 'bob' WHERE id = ?",
    ):
        with pytest.raises(ValueError, match="writes the column that says which learner"):
            store._learner_scoped(reassigns)


# A SET expression that only READS a learner column -- a catalog lookup keyed on
# lessons.id, or a subquery reading the learner's own aggregate -- was refused table-blind
# by the pre-D17 sweep, which flagged any learner-column token anywhere in the SET region.
# D17 narrowed it to the SET's own subquery identity, so these now scope cleanly. Each is
# safe because the personal relation inside the subquery is still constrained by the
# relations check; if it were not, that check would refuse the statement independently
# (see test_a_set_subquery_reading_an_unconstrained_relation_is_still_refused).
_SET_READS_NOW_ACCEPTED: tuple[str, ...] = (
    # the task's catalog lookup: `id` here is lessons.id, not learners.id
    "UPDATE lesson_results SET lesson_id = (SELECT id FROM lessons WHERE lessons.id = ?) WHERE learner_id = ?",
    # a catalog lookup whose subquery selects the bare column `id` from a shared table
    "UPDATE lesson_results SET lesson_id = (SELECT id FROM lessons WHERE code = 'a') WHERE learner_id = ?",
    # the task's best-score cache: a correctly scoped correlated subquery
    "UPDATE lesson_results SET score = (SELECT max(z.score) FROM lesson_results AS z WHERE z.learner_id = ?) "
    "WHERE learner_id = ?",
    # the same shape reading a count rather than a max
    "UPDATE lesson_results SET score = (SELECT count(*) FROM lesson_results AS z WHERE z.learner_id = ?) "
    "WHERE learner_id = ?",
)


@pytest.mark.parametrize("sql", _SET_READS_NOW_ACCEPTED)
def test_a_set_that_only_reads_a_learner_column_in_a_subquery_is_accepted(sql: str) -> None:
    """The narrowing D17 exists for: a read inside a SET subquery is not a write.

    The pre-D17 sweep tested every token in the SET region against the learner columns
    and was table-blind, so `SET lesson_id = (SELECT id FROM lessons ...)` was refused for
    naming `id` -- which is lessons.id, not learners.id -- and the best-score cache was
    refused for naming learner_id inside its own correctly-scoped subquery. Measured during
    this task: of the statements the sweep refused, three in four only read a learner
    column. This pins that those reads scope.

    IF THIS TEST FAILS because one of these is refused again, the SET sweep has been
    widened back past the statement's own subquery. Re-read the SET loop in
    _unconstrained_personal_relation and the "Known refusals" list in
    docs/learner-database.md before changing the assertion.
    """
    assert store._learner_scoped(sql) == sql


# The narrowing must NOT reach a learner column at the SET's own query level, where an
# assignment lives and this rule cannot tell one from a read. These stay refused, and not
# as writes (saying they WRITE the column would be false for the reads among them).
_SET_OWN_LEVEL_STILL_REFUSED: tuple[str, ...] = (
    # a column-list assignment of a learner column -- the sweep's original reason to exist
    "UPDATE lesson_results SET (learner_id, score) = (SELECT 'bob', 5) WHERE learner_id = ?",
    "UPDATE lesson_results SET (score, learner_id) = (SELECT 5, 'bob') WHERE learner_id = ?",
    # a bare copy of the learner column into another column, at the SET's own level
    "UPDATE lesson_results SET lesson_id = learner_id WHERE learner_id = ?",
)


@pytest.mark.parametrize("sql", _SET_OWN_LEVEL_STILL_REFUSED)
def test_a_learner_column_at_the_sets_own_level_is_still_refused(sql: str) -> None:
    """The narrowing stops at the SET's own subquery, not before it.

    A column-list `SET (learner_id, x) = (...)` assigns the column while evading the plain
    `col =` test, and D11's review confirmed the region sweep is what caught it. A bare copy
    or a CASE that names the column at the SET's own level cannot be told apart from an
    assignment by a rule that reads text, so both are refused -- with a message that says it
    is the own-level ambiguity, not that the column is written.
    """
    with pytest.raises(ValueError, match="a SET names a learner column at the statement's own level"):
        store._learner_scoped(sql)


def test_a_set_subquery_reading_an_unconstrained_relation_is_still_refused() -> None:
    """The narrowing is safe because the relations check, not the sweep, guards the subquery.

    Once the SET sweep stops covering a nested subquery, an unconstrained personal relation
    inside it must still be refused -- by _personal_relations, a few lines down. If this
    were accepted, narrowing the sweep would have opened a cross-learner read.
    """
    unconstrained = (
        "UPDATE lesson_results SET score = (SELECT max(z.score) FROM lesson_results AS z) WHERE learner_id = ?"
    )
    with pytest.raises(ValueError, match="nothing constrains lesson_results"):
        store._learner_scoped(unconstrained)
    # A literal-targeted read of another learner is refused too: 'bob' is not the `?` filter
    # the rule counts, so the subquery's z carries no filter that scopes it.
    literal = (
        "UPDATE lesson_results SET score = (SELECT z.score FROM lesson_results AS z WHERE z.learner_id = 'bob') "
        "WHERE learner_id = ?"
    )
    with pytest.raises(ValueError, match="cannot tell what it constrains"):
        store._learner_scoped(literal)


def test_a_narrowed_set_read_does_not_move_a_row_across_learners() -> None:
    """Verify by execution, not by reading: the accepted best-score cache is data-safe.

    D11's lesson was that a rule verdict is trusted on nothing until the accepted statement
    is executed against a two-learner database. This runs the narrowed-accept best-score
    cache with one learner bound to every parameter and asserts the other learner's rows are
    untouched -- no re-attribution, no cross-learner value copied in. Built on the real
    schema rather than the seeded fixture so the two learners are the only rows in play.
    """
    schema = (Path(store.__file__).parent / "schema.sql").read_text()
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(schema)
        connection.executescript(
            "INSERT INTO learners (id, display_name, created_at) VALUES ('alice', 'Alice', 0), ('bob', 'Bob', 0);"
            "INSERT INTO languages (code, name) VALUES ('fr', 'French');"
            "INSERT INTO lessons (id, language_code, position, title, objective) "
            "VALUES ('l1', 'fr', 1, 'Greetings', 'Say hello');"
            "INSERT INTO lesson_results (id, learner_id, lesson_id, outcome, score, recorded_at) "
            "VALUES (1, 'alice', 'l1', 'completed', 10, 0), (2, 'bob', 'l1', 'completed', 99, 0);"
        )
        connection.commit()
        bob_before = connection.execute(
            "SELECT id, learner_id, score FROM lesson_results WHERE learner_id = 'bob'"
        ).fetchall()

        cache = (
            "UPDATE lesson_results SET score = (SELECT max(z.score) FROM lesson_results AS z WHERE z.learner_id = ?) "
            "WHERE learner_id = ?"
        )
        assert store._learner_scoped(cache) == cache  # accepted by the rule
        connection.execute(cache, ("alice", "alice"))
        connection.commit()

        bob_after = connection.execute(
            "SELECT id, learner_id, score FROM lesson_results WHERE learner_id = 'bob'"
        ).fetchall()
        assert bob_after == bob_before, "bob's rows must be untouched"
        owners = {row[0] for row in connection.execute("SELECT learner_id FROM lesson_results").fetchall()}
        assert owners == {"alice", "bob"}
    finally:
        connection.close()


def _old_set_region_hit(sql: str) -> bool:
    """Reconstruct the pre-D17 SET check: a learner column ANYWHERE in a SET region.

    This is the one line D17 changed, restated so the test can diff the two rules without
    a second copy of the whole module. The narrowed rule is `rows[at][1] == sub_depth`;
    the old rule dropped that guard and refused on any learner-column token in the region.
    """
    tokens = store._sql_tokens(sql)
    words = [token.upper() for token in tokens]
    rows, _ = store._bracket_map(tokens, words)
    for index in range(len(tokens)):
        if words[index] != "SET":
            continue
        members, _level, _sub = store._clause_region(tokens, words, rows, index)
        if any(tokens[at].lower() in store._LEARNER_COLUMNS for at in members):
            return True
    return False


# Labelled corpus for the accept-set measurement. Assignments must never move refuse->accept;
# nested-subquery reads are exactly what the narrowing lets through.
_MEASURE_ASSIGNMENTS: tuple[str, ...] = (
    "UPDATE lesson_results SET learner_id = ? WHERE learner_id = ?",
    "UPDATE lesson_results SET learner_id = 'bob' WHERE learner_id = ?",
    "UPDATE learners SET id = ? WHERE id = ?",
    "UPDATE lesson_results SET (learner_id, score) = (SELECT 'bob', 5) WHERE learner_id = ?",
    "UPDATE lesson_results SET (score, learner_id) = (SELECT 5, 'bob') WHERE learner_id = ?",
    "UPDATE lesson_results SET learner_id = CASE WHEN score > 0 THEN 'bob' ELSE 'bob' END WHERE learner_id = ?",
    "UPDATE lesson_results SET lesson_id = learner_id WHERE learner_id = ?",
    "UPDATE lesson_results SET learner_id = z.learner_id FROM lesson_results AS z WHERE lesson_results.learner_id = ?",
)
_MEASURE_READS: tuple[str, ...] = (
    "UPDATE lesson_results SET score = (SELECT max(z.score) FROM lesson_results AS z WHERE z.learner_id = ?) "
    "WHERE learner_id = ?",
    "UPDATE lesson_results SET score = (SELECT count(*) FROM lesson_results AS z WHERE z.learner_id = ?) "
    "WHERE learner_id = ?",
    "UPDATE lesson_results SET outcome = (SELECT z.outcome FROM lesson_results AS z WHERE z.learner_id = ? "
    "AND z.lesson_id = 'l1') WHERE learner_id = ?",
)


def test_the_set_narrowing_moves_only_reads() -> None:
    """Criterion 5, measured and committed: diff the old and new rules, verify what moved.

    Pitfall 4 forbids judging this by reading the diff, so the measurement is executed. The
    pre-D17 rule is reconstructed by _old_set_region_hit and the accept sets are diffed:

    - No assignment form moves. Every _MEASURE_ASSIGNMENTS statement is refused by the new
      rule, so none can have moved refuse->accept -- the isolation guarantee is untouched.
    - Every _MEASURE_READS statement WAS refused by the old region-wide rule and IS accepted
      by the new one: these are the moves. This is what beats the baseline over-refusal.
    - Each moved statement is then run against a two-learner database with one learner bound
      to every parameter, and leaves the other learner's rows untouched -- so the moves are
      reads, proven by execution rather than asserted.

    D11's baseline was 48 statements refused solely by this branch, only 12 re-attributing.
    The exact moved count depends on the corpus; what this pins is the DIRECTION -- reads
    move, assignments do not, and no move re-attributes a row. Reverting the narrowing makes
    every read below refuse again and the `moved` assertion fail.
    """
    for assignment in _MEASURE_ASSIGNMENTS:
        assert store._unconstrained_personal_relation(assignment) is not None, (
            f"assignment must stay refused: {assignment}"
        )

    moved = []
    for read in _MEASURE_READS:
        assert _old_set_region_hit(read), f"corpus error: old rule should have refused {read}"
        assert store._learner_scoped(read) == read, f"narrowed rule should accept {read}"
        moved.append(read)
    assert len(moved) == len(_MEASURE_READS), "every read in the corpus should move refuse->accept"

    schema = (Path(store.__file__).parent / "schema.sql").read_text()
    for read in moved:
        connection = sqlite3.connect(":memory:")
        try:
            connection.executescript(schema)
            connection.executescript(
                "INSERT INTO learners (id, display_name, created_at) VALUES ('alice', 'Alice', 0), ('bob', 'Bob', 0);"
                "INSERT INTO languages (code, name) VALUES ('fr', 'French');"
                "INSERT INTO lessons (id, language_code, position, title, objective) "
                "VALUES ('l1', 'fr', 1, 'Greetings', 'Say hello');"
                "INSERT INTO lesson_results (id, learner_id, lesson_id, outcome, score, recorded_at) "
                "VALUES (1, 'alice', 'l1', 'completed', 10, 0), (2, 'bob', 'l1', 'partial', 88, 0);"
            )
            connection.commit()
            bob_before = connection.execute(
                "SELECT id, learner_id, lesson_id, outcome, score FROM lesson_results WHERE learner_id = 'bob'"
            ).fetchall()
            connection.execute(read, tuple("alice" for _ in range(read.count("?"))))
            connection.commit()
            bob_after = connection.execute(
                "SELECT id, learner_id, lesson_id, outcome, score FROM lesson_results WHERE learner_id = 'bob'"
            ).fetchall()
            assert bob_after == bob_before, f"moved read re-attributed or altered bob's rows: {read}"
            owners = {row[0] for row in connection.execute("SELECT learner_id FROM lesson_results").fetchall()}
            assert owners == {"alice", "bob"}, f"moved read changed row ownership: {read}"
        finally:
            connection.close()


def test_a_filter_in_one_subquery_does_not_scope_a_relation_in_its_sibling() -> None:
    """A subquery is an identity, not a depth: two siblings are not the same query.

    A depth counter cannot tell them apart, so a filter written in the second subquery
    was credited to the relation read in the first. Executed against a two-learner
    database during review, this statement returned every learner's outcomes while the
    decoy subquery carried the filter that appeared to scope them.
    """
    decoy = (
        "SELECT (SELECT group_concat(learner_id || ':' || outcome) FROM lesson_results), "
        "(SELECT 1 FROM (SELECT ? AS learner_id) WHERE learner_id = ?)"
    )

    with pytest.raises(ValueError, match="nothing constrains lesson_results"):
        store._learner_scoped(decoy)


def test_a_bracketed_between_is_accepted_because_it_cannot_reach_past_its_brackets() -> None:
    """The counterpart, and the rewrite the refusal asks for.

    Refusing every BETWEEN would cost a statement the tutor will plausibly want -- "the
    lessons I scored between 0 and 100" -- for nothing, and this rule runs at import, so
    a false refusal is a robot that does not start.
    """
    bracketed = "SELECT outcome FROM lesson_results WHERE learner_id = ? AND (score BETWEEN 0 AND 100)"

    assert store._learner_scoped(bracketed) == bracketed


@pytest.mark.parametrize(
    ("shape", "sql"),
    [
        ("a bracketed conjunction", "SELECT outcome FROM lesson_results WHERE (learner_id = ? AND outcome = 'x')"),
        ("reversed operands", "SELECT outcome FROM lesson_results WHERE ? = learner_id"),
        ("a named placeholder", "SELECT outcome FROM lesson_results WHERE learner_id = :learner"),
    ],
)
def test_a_filter_the_rule_can_see_but_not_read_says_so(shape: str, sql: str) -> None:
    """Three refusals where the author DID write a learner filter.

    Told only that nothing constrains the statement, they go looking for a filter they
    already wrote -- and the obvious wrong fix for that is to widen the predicate until
    something passes, which is how a scoping bug gets written deliberately. The message
    has to distinguish "you wrote no filter" from "I cannot read the one you wrote".
    """
    with pytest.raises(ValueError, match="not as a conjunct of its own"):
        store._learner_scoped(sql)


def test_an_or_is_refused_where_it_can_widen_the_filter_and_allowed_where_it_cannot() -> None:
    """AND binds tighter than OR, so a top-level OR wins over the whole conjunction.

    `WHERE learner_id = ? AND outcome = 'a' OR 1 = 1` reads as
    `(learner_id = ? AND outcome = 'a') OR 1 = 1` and returns every row -- splitting on
    AND alone cannot see that, so an OR at the clause's own level is refused. Inside
    brackets it cannot reach past them, and refusing it there would reject a correctly
    scoped statement for nothing: this rule runs at import, so a false refusal is a robot
    that does not start.
    """
    widens_everything = "SELECT outcome FROM lesson_results WHERE learner_id = ? AND outcome = 'a' OR 1 = 1"
    benign = (
        "SELECT outcome FROM lesson_results WHERE learner_id = ? AND (outcome = 'completed' OR outcome = 'partial')"
    )

    with pytest.raises(ValueError, match="top-level OR"):
        store._learner_scoped(widens_everything)

    assert store._learner_scoped(benign) == benign


@pytest.mark.parametrize(
    ("shape", "sql"),
    [
        (
            "copying another learner's row",
            "INSERT INTO lesson_results (learner_id, lesson_id, outcome, score, recorded_at) "
            "VALUES (?, (SELECT lesson_id FROM lesson_results WHERE learner_id <> ? LIMIT 1), ?, ?, ?)",
        ),
        (
            "attributing the row to another learner",
            "INSERT INTO lesson_results (learner_id, lesson_id, outcome, score, recorded_at) "
            "VALUES ((SELECT id FROM learners WHERE id <> ? LIMIT 1), ?, ?, ?, ?)",
        ),
    ],
)
def test_a_values_list_that_reads_is_judged_like_any_other_read(shape: str, sql: str) -> None:
    """A VALUES list reads the moment it contains a scalar subquery.

    The write rule once checked the SELECT form of an insert and returned early on the
    VALUES form, which is an asymmetry with no reason behind it. Verified during review
    by execution against a two-learner database: the first of these copied another
    household member's completed lesson and score into the current learner's history,
    where the tutor reads it back and speaks it.
    """
    with pytest.raises(ValueError, match="attribute its row to one learner"):
        store._learner_scoped(sql)


@pytest.mark.parametrize(
    ("shape", "sql"),
    [
        ("a double-quoted table", 'SELECT outcome FROM "lesson_results" WHERE learner_id = ?'),
        ("a backtick-quoted table", "SELECT outcome FROM `lesson_results` WHERE learner_id = ?"),
        ("a bracket-quoted table", "SELECT outcome FROM [lesson_results] WHERE learner_id = ?"),
        (
            "a quoted span hiding an OR",
            'SELECT learner_id, score FROM lesson_results WHERE learner_id = ? AND "\'" OR 1 OR "\'"',
        ),
        ("an unterminated double quote", 'SELECT outcome FROM lesson_results WHERE outcome = " AND learner_id = ?'),
    ],
)
def test_an_identifier_quote_is_refused_rather_than_lexed(shape: str, sql: str) -> None:
    """SQLite has three identifier quotes and this lexer knows none of them.

    So a `'` inside a double-quoted span pairs differently here than in the database,
    which hides live SQL as a literal. The fourth case is the one that shows why this
    matters: the guard read it as `... AND " <literal> "` and never saw the top-level OR,
    while SQLite evaluated the OR and returned every learner's rows -- verified during
    review against a real database. Refused rather than lexed, because teaching the lexer
    three more quoting forms re-opens the question at the next one.
    """
    with pytest.raises(ValueError, match="quotes an identifier"):
        store._learner_scoped(sql)


def test_an_ambiguous_bare_filter_is_told_what_to_change() -> None:
    """The refusal has to name the cause, or it sends the author the wrong way.

    This statement IS correctly scoped; what it lacks is an alias on the filter. Told
    only that nothing constrains it, an author goes looking for a filter they already
    wrote -- and the obvious wrong fix for that is to widen the predicate until something
    passes, which is how a scoping bug gets written on purpose.
    """
    scoped_but_ambiguous = (
        "SELECT r.outcome FROM lesson_results AS r JOIN lessons AS l ON l.id = r.lesson_id WHERE learner_id = ?"
    )

    with pytest.raises(ValueError, match=r"write r\.learner_id = \? instead"):
        store._learner_scoped(scoped_but_ambiguous)

    named = scoped_but_ambiguous.replace("WHERE learner_id = ?", "WHERE r.learner_id = ?")
    assert named != scoped_but_ambiguous, "the replacement did not apply"
    assert store._learner_scoped(named) == named


def test_an_unqualified_filter_is_judged_against_the_relations_it_can_see() -> None:
    """A subquery's own tables are not in scope where the outer filter is written.

    The counterpart to the ambiguity test above. Refusing this would make the rule
    reject a statement whose filter is unambiguous, and a rule that cries wolf is one
    people route around.
    """
    one_relation_in_scope = (
        "SELECT outcome FROM lesson_results WHERE learner_id = ? AND lesson_id IN (SELECT id FROM lessons)"
    )

    assert store._learner_scoped(one_relation_in_scope) == one_relation_in_scope


@pytest.mark.parametrize(
    ("shape", "sql"),
    [
        (
            "a common table expression",
            "WITH mine AS (SELECT * FROM lesson_results WHERE learner_id = ?) SELECT outcome FROM mine",
        ),
        ("a schema-qualified table", "SELECT outcome FROM main.lesson_results WHERE learner_id = ?"),
        ("a statement that reaches no personal table", "SELECT code, name FROM languages WHERE code = ?"),
        ("more than one statement", "SELECT outcome FROM lesson_results WHERE learner_id = ?; DROP TABLE learners"),
    ],
)
def test_a_shape_the_rule_cannot_read_is_refused_rather_than_waved_through(shape: str, sql: str) -> None:
    """Refusing what cannot be judged is the rule, not an accident of how it is written.

    Every one of these has a learner filter in it, and the first two are things a
    developer might reasonably write. They are refused anyway, because accepting a
    statement this rule has not actually read is what made the previous bypasses
    invisible. The cost is a rewrite; the cost of the other direction is a household
    member reading someone else's history.
    """
    with pytest.raises(ValueError):
        store._learner_scoped(sql)


@pytest.mark.parametrize(
    ("shape", "sql"),
    [
        ("a line comment", "SELECT outcome FROM lesson_results -- WHERE learner_id = ?"),
        ("a block comment", "SELECT outcome FROM lesson_results /* WHERE learner_id = ? */"),
        ("a string literal", "SELECT outcome FROM lesson_results WHERE outcome = 'learner_id = ?'"),
    ],
)
def test_text_the_database_never_runs_as_sql_cannot_satisfy_the_rule(shape: str, sql: str) -> None:
    """A filter written in a comment filters nothing, and one inside quotes is a value.

    The AST guard below used to strip comments before consulting this rule because the
    rule itself could not tell the difference. It can now, which removes the asymmetry
    where the same statement was refused by the test and accepted at import time.
    """
    with pytest.raises(ValueError, match="nothing constrains lesson_results"):
        store._learner_scoped(sql)


# A statement is proved scoped by what the source literally says. Anything a
# runtime value supplies becomes this sentinel, which matches no scoping marker and
# no table name -- so interpolation can never be what makes a statement look safe.
_INTERPOLATED = "\x00interpolated\x00"

# The region of a statement that names tables: everything from a FROM/JOIN/UPDATE/INTO
# keyword up to the clause that ends the table list.
_TABLE_CLAUSE = re.compile(
    r"\b(?:FROM|JOIN|UPDATE|INTO)\b(.*?)(?=\b(?:WHERE|GROUP|ORDER|LIMIT|VALUES|SET|ON|RETURNING)\b|$)",
    re.IGNORECASE | re.DOTALL,
)


def _interpolates_a_table(sql: str) -> bool:
    """Report whether any table a statement reads is named by a runtime value.

    The whole table clause is searched, not just the position straight after the
    keyword, because a hole reaches the table list several ways: `FROM main.{table}`
    puts it after a schema qualifier, and `FROM lesson_results AS r, {other}` puts it
    after a comma. Both name a table this guard then cannot see, so _personal() would
    be answering for the wrong statement.

    A hole in the WHERE clause is left alone -- that is a value, and a value is what
    binding parameters are for. This is the line between "which rows" and "which
    table", and only the second is refused here.
    """
    return any(_INTERPOLATED in match.group(1) for match in _TABLE_CLAUSE.finditer(sql))


# The keyword that says what a statement DOES. If none of these survives once the
# holes are removed, nothing about the statement was written down.
_STATEMENT_HEAD = re.compile(
    r"\b(?:SELECT|INSERT|UPDATE|DELETE|REPLACE|PRAGMA|CREATE|DROP|ALTER|WITH|ATTACH)\b", re.IGNORECASE
)

# A PRAGMA configures the connection and reaches no table, so it is the one head that
# may be interpolated without naming one -- which is what lets store.py build
# `PRAGMA user_version = {n}`, a statement that cannot take a bound parameter.
#
# Stated as an exemption rather than as a list of table-accessing keywords on purpose.
# A list has to be complete to be safe, and the first version was not: it was anchored
# at the start of the statement, so a leading SQL comment slipped past it, and it named
# none of ATTACH, DROP or ALTER -- each of which interpolates an identifier that cannot
# be a bound parameter. Inverting it means a head nobody thought of fails closed.
_PRAGMA_HEAD = re.compile(r"^\s*PRAGMA\b", re.IGNORECASE)


def _names_a_literal_table(sql: str) -> bool:
    """Report whether at least one table is named in full, with no hole in it."""
    return any(match.group(1).strip() and _INTERPOLATED not in match.group(1) for match in _TABLE_CLAUSE.finditer(sql))


# SQL comments. A marker inside one is text the database never reads, so it must not
# be what satisfies the scoping rule -- but a table named in one still makes the
# statement worth demanding a filter for, so only the scoping check sees them stripped.
_SQL_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)

# "%s", "%(name)s", "%5.2f" -- the holes in printf-style formatting.
_CONVERSION = re.compile(r"%(?:\([^)]*\))?[-#0 +]*[\d.*]*[a-zA-Z%]")


class _Unverifiable(Exception):
    """The statement's text cannot be reconstructed from the source alone."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# PEP 695 binders, present from Python 3.12. Looked up rather than named directly so
# this file still imports on an older interpreter.
_TYPE_ALIAS = tuple(n for n in (getattr(ast, "TypeAlias", None),) if n is not None)
_TYPE_PARAMS = tuple(
    n for n in (getattr(ast, name, None) for name in ("TypeVar", "ParamSpec", "TypeVarTuple")) if n is not None
)


def _target_names(target: ast.expr) -> list[str]:
    """Every name a single assignment target binds, including through unpacking."""
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, ast.Starred):
        return _target_names(target.value)
    if isinstance(target, (ast.Tuple, ast.List)):
        return [name for element in target.elts for name in _target_names(element)]
    return []


def _module_bindings(tree: ast.Module) -> dict[str, list[ast.expr | None]]:
    """Map every name the module binds to the expressions bound to it.

    A name bound by something other than a plain assignment -- a loop target, a walrus,
    a `with ... as`, a parameter, an import -- is recorded as None: bound, but by a
    construct whose value cannot be read from the source.

    Every scope is collected into one table deliberately. A name bound in two places --
    in two different functions, or at module level and again inside one -- comes back
    with more than one entry and is refused rather than resolved.

    Counting EVERY binding form is what makes that safe, and it is not a detail. An
    earlier version of this collected only assignments, and a loop target shadowing a
    module constant therefore resolved to the constant:

        QUERY = "SELECT outcome FROM lesson_results WHERE learner_id = ?"
        for QUERY in queries:
            connection.execute(QUERY)   # read as the scoped constant above

    The guard read the safe spelling and approved the dangerous one. So the claim that
    merging scopes can only make this stricter holds only while this table is complete,
    which is why an unrecognised binding form is recorded rather than ignored.
    """
    bindings: dict[str, list[ast.expr | None]] = {}

    def bind(name: str, value: ast.expr | None) -> None:
        bindings.setdefault(name, []).append(value)

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                names = _target_names(target)
                # Only a single bare name receives the whole expression; unpacking
                # gives each name a piece this guard does not try to take apart.
                for name in names:
                    bind(name, node.value if isinstance(target, ast.Name) else None)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            for name in _target_names(node.target):
                bind(name, node.value if isinstance(node, ast.AnnAssign) else None)
        elif isinstance(node, ast.NamedExpr):
            for name in _target_names(node.target):
                bind(name, node.value)
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            for name in _target_names(node.target):
                bind(name, None)
        elif isinstance(node, ast.withitem):
            if node.optional_vars is not None:
                for name in _target_names(node.optional_vars):
                    bind(name, None)
        elif isinstance(node, ast.ExceptHandler):
            if node.name:
                bind(node.name, None)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bind(alias.asname or alias.name.split(".")[0], None)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bind(node.name, None)
        elif isinstance(node, ast.arg):
            bind(node.arg, None)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            for name in node.names:
                bind(name, None)
        elif _TYPE_ALIAS and isinstance(node, _TYPE_ALIAS):
            for name in _target_names(node.name):
                bind(name, None)
        elif _TYPE_PARAMS and isinstance(node, _TYPE_PARAMS):
            bind(node.name, None)
        elif isinstance(node, ast.MatchAs) or isinstance(node, ast.MatchStar):
            if node.name:
                bind(node.name, None)
        elif isinstance(node, ast.MatchMapping):
            if node.rest:
                bind(node.rest, None)
    return bindings


def _skeleton(node: ast.expr, bindings: dict[str, list[ast.expr | None]], seen: frozenset[str] = frozenset()) -> str:
    """Reconstruct what a query expression literally says, or refuse to guess.

    Returns the statement with every interpolated fragment replaced by _INTERPOLATED.
    Raises _Unverifiable for a form whose text cannot be recovered from the source --
    which the caller reports as an offender, because silently skipping such a form is
    the defect this guard exists to close.
    """
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            return node.value
        raise _Unverifiable(f"a non-string constant of type {type(node.value).__name__}")

    if isinstance(node, ast.JoinedStr):  # an f-string
        return "".join(
            part.value if isinstance(part, ast.Constant) and isinstance(part.value, str) else _INTERPOLATED
            for part in node.values
        )

    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _skeleton(node.left, bindings, seen) + _skeleton(node.right, bindings, seen)

    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        return _CONVERSION.sub(_INTERPOLATED, _skeleton(node.left, bindings, seen))

    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Attribute) and node.func.attr == "format":
            return re.sub(r"\{[^{}]*\}", _INTERPOLATED, _skeleton(node.func.value, bindings, seen))
        # _learner_scoped returns its argument unchanged or raises at import time, so
        # reading through it is reading the statement itself.
        # Read through only if the name still means the module's own function. Every
        # other name here is refused when it is bound more than once; this one is the
        # most load-bearing in the module, so it does not get to be the exception.
        if isinstance(node.func, ast.Name) and node.func.id == "_learner_scoped" and node.args:
            if len(bindings.get("_learner_scoped", [])) != 1:
                raise _Unverifiable("_learner_scoped is bound more than once, so reading through it proves nothing")
            return _skeleton(node.args[0], bindings, seen)
        raise _Unverifiable("a call whose return value is not readable from the source")

    if isinstance(node, ast.Name):
        if node.id in seen:
            raise _Unverifiable(f"the name {node.id} is defined in terms of itself")
        bound = bindings.get(node.id, [])
        if not bound:
            raise _Unverifiable(f"the name {node.id} is not bound anywhere in this module")
        if len(bound) > 1:
            raise _Unverifiable(f"the name {node.id} is bound {len(bound)} times, so it has no single value")
        if bound[0] is None:
            raise _Unverifiable(f"the name {node.id} is bound by a construct the guard cannot read")
        return _skeleton(bound[0], bindings, seen | {node.id})

    raise _Unverifiable(f"a {type(node).__name__} expression the guard cannot read")


_EXECUTE_METHODS = ("execute", "executemany", "executescript")


def _assignment_pairs(node: ast.AST) -> list[tuple[ast.expr, ast.expr]]:
    """Each (target, value) an assignment statement binds, pairing a tuple unpack positionally."""
    pairs: list[tuple[ast.expr, ast.expr]] = []
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, (ast.Tuple, ast.List)) and isinstance(node.value, (ast.Tuple, ast.List)):
                pairs.extend(zip(target.elts, node.value.elts))
            else:
                pairs.append((target, node.value))
    elif isinstance(node, ast.AnnAssign) and node.value is not None:
        pairs.append((node.target, node.value))
    return pairs


def _is_execute_method(node: ast.expr) -> bool:
    """Report whether an expression IS a connection's execute method, rather than a call of it."""
    if isinstance(node, ast.Attribute) and node.attr in _EXECUTE_METHODS:
        return True
    # `connection.__getattribute__("execute")` -- the dunder is the func of a call, so
    # it is never a bare attribute here; the CALL is what yields the method.
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr in ("__getattribute__", "__getattr__"):
            return True
    if isinstance(node, ast.Attribute) and node.attr in ("__getattribute__", "__getattr__"):
        return True
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr"):
        return False
    if len(node.args) != 2:
        return False
    # A literal name is checked against the three methods. A computed one --
    # getattr(connection, name) -- could be any of them, so it counts as one: the
    # question this answers is whether the method might be being taken, and an
    # unreadable name cannot answer no.
    return not isinstance(node.args[1], ast.Constant) or node.args[1].value in _EXECUTE_METHODS


def _execute_aliases(tree: ast.Module) -> set[str]:
    """Names bound to a connection's execute method.

    `run = connection.execute` followed by `run(query)` is a call on a Name, so the
    statement would otherwise reach neither a verdict nor a refusal -- it would simply
    not be seen, which is worse than the defect this guard was widened to fix.

    Returns only the names. The places the method is taken as a value WITHOUT landing
    in a bare name are _execute_methods_taken_as_values below -- deliberately a second
    function rather than a second element of a tuple, because a tuple return has to be
    unpacked correctly at every call site and one of them got it wrong: `aliases =
    _execute_aliases(tree)` left `name in (set, list)` evaluating False for every name,
    silently, so the coverage pin stopped seeing aliased statements while staying green.
    Two single-purpose functions have nothing to mis-unpack.
    """
    names = {
        target.id
        for node in ast.walk(tree)
        for target, value in _assignment_pairs(node)
        if _is_execute_method(value) and isinstance(target, ast.Name)
    }

    # An alias of an alias. `r2 = r1` binds a Name, not an Attribute, so one pass sees
    # only the first hop; repeating to a fixed point follows the chain however long it
    # is. Bounded by the number of assignments, so it always terminates.
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
                continue
            if not isinstance(node.value, ast.Name) or node.value.id not in names:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in names:
                    names.add(target.id)
                    changed = True

    return names


def _execute_methods_taken_as_values(tree: ast.Module) -> list[ast.expr]:
    """Every place the execute method is held rather than called, and not as a bare name.

    `self.run = connection.execute`, a list element, a default argument. These are
    reported rather than followed: the point is that taking this method as a value is
    never invisible, not that every way of doing it can be traced.
    """
    followed = {
        id(value)
        for node in ast.walk(tree)
        for target, value in _assignment_pairs(node)
        if _is_execute_method(value) and isinstance(target, ast.Name)
    }
    called = {id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    return [
        node
        for node in ast.walk(tree)
        if _is_execute_method(node) and id(node) not in called and id(node) not in followed
    ]


def _is_execute_call(node: ast.Call, aliases: set[str]) -> bool:
    """Report whether a call runs SQL, however the method was reached."""
    if isinstance(node.func, ast.Attribute) and node.func.attr in _EXECUTE_METHODS:
        return True
    if isinstance(node.func, ast.Name) and node.func.id in aliases:
        return True
    return _is_execute_method(node.func)


def _execute_sites(tree: ast.Module, aliases: set[str]) -> list[ast.Call]:
    """Every call in this source that runs SQL, however the method was reached.

    Shared by the guard and by the coverage pin below. They used to walk their own
    copies of this predicate, and when one gained alias handling the other silently
    kept the old blind spot -- the pin that exists to stop coverage shrinking had
    shrunk with it.
    """
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call) and _is_execute_call(node, aliases)]


def _shown(sql: str) -> str:
    """Render a skeleton for a failure message, with holes written the way a human wrote them."""
    return sql.replace(_INTERPOLATED, "{...}")[:70]


def unverified_inline_queries(source: str) -> list[str]:
    """Return one offender line per execute() argument this source cannot prove scoped.

    Split out from the test below so the same code path can be run over planted source,
    which is the only way to show the guard actually fails rather than merely passing.

    What this does NOT catch, stated plainly because a guard nobody knows the edges of
    gets trusted for things it never checked:

    * It inherits store._learner_scoped's rule, so that rule's limits are this guard's
      limits. That rule no longer searches for a substring: it reads which relations a
      statement names and demands a learner filter that actually constrains each of them:
      a WHERE conjunct, written in that relation's own subquery, that IS `alias.column = ?`
      rather than merely containing it. So a self-join, an OR-widened predicate, an
      unfiltered UNION leg, a correlated-subquery DELETE, a filter inside NOT EXISTS or
      IN, a filter written only in a join's ON, `WHERE NOT learner_id = ?`, and a filter
      wrapped in CASE, IIF or any other call are all refused. Every one of those used to
      report green here. The whole-conjunct requirement is an allow-list on purpose: the
      version of this rule that instead named the ways a filter can be neutralised was
      defeated four more times before the list stopped growing. Reusing it rather than restating
      it stays deliberate -- two copies would diverge -- so anything still open is closed
      in store.py and both call sites inherit it. What remains open:
      it proves the SHAPE of a filter and never its value, so nothing in "r.learner_id
      = ?" says the application binds the learner standing in front of the robot; and it
      refuses what it cannot read rather than reasoning about it, and it accepts a filter
      only as a WHERE conjunct that IS "alias.column = ?", written in that relation's own
      subquery (or bare "column = ?" where one relation is visible there; brackets around
      the whole conjunct are stripped first, so "WHERE (r.learner_id = ?)" is accepted). A good many correctly scoped statements are
      refused as a result, and THE LIST OF THEM LIVES IN docs/learner-database.md AND
      NOWHERE ELSE -- deliberately. It was kept in two places for one round and the copy
      here promptly fell an entry short, which is the exact failure this docstring exists
      to prevent. That document also says why the list is what has been found rather than
      a closed set. The cost falls on legitimate statements, which is the direction
      chosen: a refusal stops the module importing and gets fixed, an acceptance reads
      another household member's data quietly.
      A marker inside a SQL comment, or inside a string literal, is NOT in this list:
      the rule drops both itself now, so the import-time check and this one read the
      same statement. The asymmetry this bullet used to describe is gone.
    * A hole outside the table clause is allowed through: `WHERE learner_id = ? AND
      id = {lesson}` passes. What that leaves open is INJECTION, not scoping -- an
      interpolated value in a WHERE clause is an injection point, and nothing here
      refuses it. It is no longer a scoping hole: the reconstructed text keeps every
      literal operator, so `WHERE learner_id = ? OR {extra}` is refused by the rule's
      top-level-OR check even though what the hole would widen the predicate with is
      unknown. Refusing every interpolated statement would also refuse the scoped
      interpolated spellings this guard is required to keep accepting, so the injection
      limit is stated rather than closed.
    * It reads store.py only. A query written in any other module is outside it.
    * It reads through `_learner_scoped(...)` by name, and refuses when that name is
      bound more than once. What it cannot notice is the function itself being
      rewritten in store.py to strip a filter rather than check for one: the guard
      would read the literal, see the marker, and approve. Nothing in this file can
      close that -- _learner_scoped is the thing being trusted, and a test cannot
      verify its own trust anchor. It is stated so the anchor is known to be one.
    * A legitimate statement the guard cannot read has to be rewritten into a shape it
      can. That cost is the point, but it is a real one, and the cheap ways out are
      the ones worth naming. An aliased execute is followed where the binding lands in
      a bare name -- transitively, and through a computed getattr or a __getattribute__
      -- and refused where it does not; schema.sql is checked against an allow-list;
      and exec/eval is refused on sight. So none of those is a way to put a statement
      where this does not look.
      What defeats it is deliberate obfuscation rather than the shortcut of a hurried
      author: `f = connection.__getattribute__` followed by `f("execute")(sql)` calls
      the result of a call, which this does not trace. That is left open knowingly.
      The threat this guard answers is an author writing a cross-learner query without
      noticing, and anyone willing to write that line could delete this file instead.
      Any hiding place NOT in that category is a gap in this list, not a licence.
    """
    tree = ast.parse(source)
    bindings = _module_bindings(tree)
    aliases = _execute_aliases(tree)

    offenders: list[str] = []
    for node in ast.walk(tree):
        # A statement assembled inside exec() or eval() is invisible to every check
        # below it, so its presence in this module is itself the thing to report.
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("exec", "eval"):
            offenders.append(f"line {node.lineno}: refused, {node.func.id}() can run a statement this cannot read")
    for node in _execute_methods_taken_as_values(tree):
        offenders.append(
            f"line {node.lineno}: refused, the execute method is taken as a value here, "
            "so the statements it later runs cannot be found"
        )
    # Nothing below can be trusted if the module cannot say which names it binds.
    if any(
        isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names) for node in ast.walk(tree)
    ):
        offenders.append("refused, a star-import rebinds an unknown set of names, so no name here can be read")

    for node in _execute_sites(tree, aliases):
        if not node.args:
            # sqlite3's own execute takes the statement positionally, so this shape does
            # not reach it -- but silence is the wrong answer whatever the reason, and a
            # wrapper named execute would take it happily.
            if node.keywords:
                offenders.append(f"line {node.lineno}: refused, the statement is passed by keyword")
            continue
        argument = node.args[0]

        # The bundled DDL, read from a file at runtime and so not readable here. Named
        # rather than skipped, and narrow: this exact zero-argument call and no other.
        # test_the_bundled_schema_creates_tables_and_nothing_else makes it safe.
        if (
            isinstance(argument, ast.Call)
            and isinstance(argument.func, ast.Name)
            and argument.func.id == "_schema_sql"
            and not argument.args
        ):
            continue

        # The consent-scope rebuild, read from a file for the same reason and exempted
        # on the same terms: this exact zero-argument call and no other. It needs its
        # OWN backing test rather than the schema one, because it legitimately does
        # what that guard refuses -- a DROP, an INSERT..SELECT and a RENAME are the
        # whole of a table rebuild. test_the_consent_migration_rebuilds_consents_and_
        # nothing_else is what makes it safe.
        if (
            isinstance(argument, ast.Call)
            and isinstance(argument.func, ast.Name)
            and argument.func.id == "_consent_scope_migration_sql"
            and not argument.args
        ):
            continue

        try:
            sql = _skeleton(argument, bindings)
        except _Unverifiable as refusal:
            offenders.append(f"line {argument.lineno}: refused, {refusal.reason}")
            continue

        # A statement that is entirely a hole -- `execute(f"{sql}")` -- reconstructs to
        # nothing but the sentinel, which names no table, so _personal would answer
        # False about a statement it has not seen a single character of.
        #
        # This is the shape the refusal machinery pushes authors toward, which is what
        # makes it the important one: `execute(sql)` on a parameter IS refused, and
        # wrapping it in an f-string is a one-character edit that used to silence the
        # refusal and turn the build green on the most dangerous spelling in the file.
        if _INTERPOLATED in sql and not _STATEMENT_HEAD.search(sql.replace(_INTERPOLATED, " ")):
            offenders.append(f"line {argument.lineno}: refused, nothing of this statement is written literally")
            continue
        if _interpolates_a_table(sql):
            offenders.append(f"line {argument.lineno}: refused, the table name is interpolated: {_shown(sql)}")
            continue
        # One literal keyword is not enough. `f"SELECT {sql}"` keeps a head, so the
        # check above passes, and then names no table -- so _interpolates_a_table finds
        # nothing to object to and _personal answers False about a statement it has
        # seen one word of. A statement that reaches a table has to say which table.
        if (
            _INTERPOLATED in sql
            and not _PRAGMA_HEAD.search(_SQL_COMMENT.sub(" ", sql))
            and not _names_a_literal_table(sql)
        ):
            offenders.append(f"line {argument.lineno}: refused, this statement names no table it is not building")
            continue
        if not _personal(sql):
            continue
        # An insert that can only ever CREATE a learner row is inherently unscoped:
        # it writes one row under an id it supplies and reads nothing.
        # there is no other learner's data it could reach. An upsert is a different
        # statement wearing the same prefix -- "ON CONFLICT(id) DO UPDATE SET ..."
        # overwrites whoever already holds that id -- and the module already uses that
        # idiom for three other tables, so it is the spelling an author would reach
        # for. Everything else must satisfy the module's own rule, reused here rather
        # than restated so the two cannot diverge.
        # "".join(split()) collapses EVERY whitespace character, not just spaces: SQL is
        # whitespace-insensitive, so "DO\tUPDATE" is a valid upsert that a space-only
        # normalisation leaves looking like a plain insert. Requiring VALUES( and
        # refusing any UPDATE or SELECT is what narrows this to the claim above --
        # an INSERT ... SELECT reads a table, and an upsert overwrites a row.
        normalised = "".join(sql.split()).upper()
        exempt = (
            normalised.startswith("INSERTINTOLEARNERS(ID,")
            and "VALUES(" in normalised
            and "UPDATE" not in normalised
            and "SELECT" not in normalised
        )
        if exempt:
            continue
        if sql in store._EVERY_LEARNERS_FACEPRINT_SQL:
            # The one read that crosses learners, admitted by MEMBERSHIP of the
            # enumerated tuple rather than by anything about its shape. Recognition
            # compares a face against the household and cannot be written one learner
            # at a time -- that would need to know who to ask about, which is the
            # question. Matching on shape here ("or it only reads faceprints") would
            # be the bypass; the tuple is the control, and a test pins it at one.
            continue
        try:
            # The statement goes in as written. Stripping comments here used to be
            # necessary because the rule could not tell a comment from SQL; it drops
            # them itself now, and stripping twice would only create a second place for
            # the two to disagree about what the database is going to run.
            store._learner_scoped(sql)
        except ValueError:
            offenders.append(f"line {argument.lineno}: {_shown(sql)}")
    return offenders


def test_no_inline_query_touches_personal_data_unscoped() -> None:
    """The guard's blind spot, closed by reading the module's own source.

    _learner_scoped only sees statements someone remembered to wrap, and the test above
    only sees module-level names. A query written inline inside a function body escapes
    both -- and inline is the form a future change is most likely to take, because the
    module already uses it for catalog reads.

    Every spelling is covered, not only a plain literal: an f-string, a concatenation,
    a .format() call and a name are reconstructed from the source, and a form that
    cannot be reconstructed is reported rather than skipped. What a runtime value
    supplies is treated as unreadable, so a statement is only ever proved scoped by
    what is literally written.
    """
    assert not unverified_inline_queries(Path(store.__file__).resolve().read_text(encoding="utf-8")), (
        "statements touching personal data the guard cannot prove learner-scoped"
    )


# Each pair below is the same statement written unscoped and scoped, in a spelling the
# old guard could not see at all. They are the demonstration the acceptance criteria
# ask for: a guard that has never been shown to fail is trusted without evidence.
_FORMS: dict[str, tuple[str, str]] = {
    "f-string": (
        'connection.execute(f"SELECT outcome FROM lesson_results WHERE lesson_id = {lesson}")',
        'connection.execute(f"SELECT outcome FROM lesson_results WHERE learner_id = ? AND lesson_id = {lesson}")',
    ),
    "concatenation": (
        'connection.execute("SELECT outcome " + "FROM lesson_results")',
        'connection.execute("SELECT outcome FROM lesson_results " + "WHERE learner_id = ?")',
    ),
    "format call": (
        'connection.execute("SELECT outcome FROM lesson_results WHERE lesson_id = {}".format(lesson))',
        'connection.execute("SELECT outcome FROM lesson_results WHERE learner_id = ? AND id = {}".format(lesson))',
    ),
    "percent formatting": (
        'connection.execute("SELECT outcome FROM lesson_results WHERE lesson_id = %s" % lesson)',
        'connection.execute("SELECT outcome FROM lesson_results WHERE learner_id = ? AND id = %s" % lesson)',
    ),
    "function-local name": (
        "def read(connection):\n"
        '    query = "SELECT outcome FROM lesson_results"\n'
        "    return connection.execute(query)",
        "def read(connection):\n"
        '    query = "SELECT outcome FROM lesson_results WHERE learner_id = ?"\n'
        "    return connection.execute(query)",
    ),
}


@pytest.mark.parametrize("form", sorted(_FORMS))
def test_an_unscoped_query_is_flagged_in_every_spelling(form: str) -> None:
    """Each spelling the old guard skipped is now caught when it is written unscoped."""
    unscoped, _ = _FORMS[form]

    offenders = unverified_inline_queries(unscoped)

    assert offenders, f"an unscoped {form} was not flagged"
    # Flagged for the right reason. "Refused, cannot read this" would also be a
    # non-empty list, and would mean the guard never actually read the statement --
    # a weaker guarantee wearing the same green tick.
    assert "refused" not in offenders[0], f"an unscoped {form} was refused, not read: {offenders}"
    assert "lesson_results" in offenders[0], offenders


@pytest.mark.parametrize("form", sorted(_FORMS))
def test_the_scoped_spelling_of_each_form_passes(form: str) -> None:
    """Widening the guard must not cost a legitimate spelling the right to exist."""
    _, scoped = _FORMS[form]

    assert unverified_inline_queries(scoped) == [], f"a scoped {form} was flagged"


def test_interpolation_cannot_be_what_makes_a_statement_look_scoped() -> None:
    """The filter may not arrive at runtime -- the source has to say it.

    This is the whole reason a hole becomes a sentinel rather than being resolved
    optimistically. At runtime the substituted text may well read "learner_id = ?",
    but nothing in the source proves it, and a guard that accepts it is trusting the
    very thing it exists to check.
    """
    source = 'connection.execute(f"SELECT outcome FROM lesson_results WHERE {filter_clause}")'

    assert unverified_inline_queries(source), "a runtime-supplied filter was accepted as scoping"


def test_an_interpolated_table_name_is_refused() -> None:
    """A hole where the table goes means _personal cannot answer for the real statement."""
    source = 'connection.execute(f"SELECT outcome FROM {table} WHERE learner_id = ?")'

    offenders = unverified_inline_queries(source)

    assert offenders and "table name is interpolated" in offenders[0], offenders


@pytest.mark.parametrize(
    ("shape", "source"),
    [
        ("a call", "connection.execute(build_query(learner_id))"),
        ("an unbound name", "connection.execute(QUERY)"),
        ("a name bound twice", 'QUERY = "SELECT 1"\nQUERY = "SELECT 2"\nconnection.execute(QUERY)'),
        ("a non-string constant", "connection.execute(7)"),
        ("a name defined in terms of itself", "QUERY = QUERY\nconnection.execute(QUERY)"),
        ("a comprehension", "connection.execute([x for x in parts])"),
    ],
)
def test_a_statement_that_cannot_be_read_is_refused_rather_than_skipped(shape: str, source: str) -> None:
    """Silent skipping is the defect being fixed, so an unreadable form is an offender.

    Note what this costs: a legitimate statement written in one of these shapes fails
    the guard and has to be rewritten into one the source can be read from. That is the
    intended trade -- the alternative is a guard whose coverage nobody can state.
    """
    offenders = unverified_inline_queries(source)

    assert offenders, f"{shape} was skipped rather than refused"
    assert "refused" in offenders[0], offenders


# Each of these binds a name that a safe module constant already holds. If the binding
# table misses the second binding, the guard reads the safe spelling and approves the
# dangerous one -- which is exactly what it did before _module_bindings counted every
# binding form. These are the regression pins for that.
_SAFE_CONSTANT = 'QUERY = "SELECT outcome FROM lesson_results WHERE learner_id = ?"\n'


@pytest.mark.parametrize(
    ("shape", "body"),
    [
        (
            "a loop target",
            "def leak(connection, queries):\n    for QUERY in queries:\n        connection.execute(QUERY)\n",
        ),
        ("a walrus", "def leak(connection, raw):\n    if (QUERY := raw):\n        connection.execute(QUERY)\n"),
        ("a with-as", "def leak(connection, m):\n    with m as QUERY:\n        connection.execute(QUERY)\n"),
        (
            "a comprehension target",
            "def leak(connection, qs):\n    return [connection.execute(QUERY) for QUERY in qs]\n",
        ),
        ("a second assignment", 'QUERY = QUERY + " OR 1=1"\ndef leak(connection):\n    connection.execute(QUERY)\n'),
    ],
)
def test_a_name_shadowing_a_safe_constant_is_refused(shape: str, body: str) -> None:
    """Reading the safe spelling of a name and approving the dangerous one is the worst case.

    Worse than the original defect, in fact: a skipped statement is uncovered, but a
    misresolved one is covered wrongly and reports green.
    """
    offenders = unverified_inline_queries(_SAFE_CONSTANT + body)

    assert offenders, f"{shape} shadowing a safe constant was not refused"
    assert "refused" in offenders[0], offenders


@pytest.mark.parametrize(
    ("shape", "source"),
    [
        (
            "a parameter default",
            'def leak(connection, QUERY="SELECT outcome FROM lesson_results"):\n    connection.execute(QUERY)\n',
        ),
        (
            "an import alias",
            "from elsewhere import q as QUERY\ndef leak(connection):\n    connection.execute(QUERY)\n",
        ),
        (
            "tuple unpacking",
            'QUERY, OTHER = "SELECT outcome FROM lesson_results", 1\ndef leak(connection):\n    connection.execute(QUERY)\n',
        ),
        (
            "a constant joined from a tuple",
            'def leak(connection):\n    connection.execute(" ".join(("SELECT outcome", "FROM lesson_results")))\n',
        ),
    ],
)
def test_a_name_bound_by_an_unreadable_construct_is_refused(shape: str, source: str) -> None:
    """Bound, but by something whose value the source does not show. Refused, not guessed."""
    offenders = unverified_inline_queries(source)

    assert offenders, f"{shape} was not refused"
    assert "refused" in offenders[0], offenders


@pytest.mark.parametrize(
    ("shape", "source"),
    [
        ("an UPDATE", 'connection.execute("UPDATE learners SET display_name = ?", (name,))'),
        ("a schema-qualified name", 'connection.execute("SELECT id, display_name FROM main.learners")'),
        ("a quoted identifier", "connection.execute('SELECT id FROM \"learners\"')"),
        ("lowercase keywords", 'connection.execute("select id from learners")'),
        ("a second space", 'connection.execute("SELECT id FROM  learners")'),
    ],
)
def test_a_personal_table_is_recognised_however_it_is_spelled(shape: str, source: str) -> None:
    """Whether scoping is demanded at all must not depend on how the SQL was typed.

    Each of these reached the learners table while _personal answered False, so the
    statement was never even offered to the scoping rule. The UPDATE is the one that
    matters most: it is the exact spelling a profile-rename feature produces, and it
    rewrites every household member's row.
    """
    offenders = unverified_inline_queries(source)

    assert offenders, f"{shape} was not recognised as touching personal data"


@pytest.mark.parametrize(
    ("shape", "source"),
    [
        ("an alias", 'run = connection.execute\nrun("SELECT outcome FROM lesson_results")'),
        ("getattr", 'getattr(connection, "execute")("SELECT outcome FROM lesson_results")'),
    ],
)
def test_a_statement_run_through_a_hidden_method_is_still_seen(shape: str, source: str) -> None:
    """Binding the method to a name must not make the statement invisible.

    Not seeing a statement is worse than refusing it: a refusal is an author's problem
    to solve, but an unseen statement reports green and nobody learns anything. It is
    also the cheapest way around a refusal, which is exactly why it is covered.
    """
    assert unverified_inline_queries(source), f"a statement reached through {shape} was not seen"


def test_an_upsert_is_not_covered_by_the_learner_creation_exemption() -> None:
    """Creating a learner reaches nobody else's data. Overwriting one does.

    The two share a prefix, and the module already uses ON CONFLICT ... DO UPDATE for
    three other tables, so this is the spelling an author would reach for rather than
    an exotic one.
    """
    creates = 'connection.execute("INSERT INTO learners (id, display_name, created_at) VALUES (?, ?, ?) ON CONFLICT(id) DO NOTHING", r)'
    overwrites = (
        'connection.execute("INSERT INTO learners (id, display_name, created_at) VALUES (?, ?, ?) '
        'ON CONFLICT(id) DO UPDATE SET display_name = excluded.display_name", r)'
    )

    assert unverified_inline_queries(creates) == [], "the legitimate seeding insert must stay exempt"
    assert unverified_inline_queries(overwrites), "an upsert over an existing learner was exempted"


@pytest.mark.parametrize(
    ("shape", "source"),
    [
        ("after a schema qualifier", 'connection.execute(f"SELECT learner_id, outcome FROM main.{table}")'),
        ("after a comma", 'connection.execute(f"SELECT o FROM lesson_results AS r, {other} WHERE r.learner_id = ?")'),
        ("straight after FROM", 'connection.execute(f"SELECT outcome FROM {table} WHERE learner_id = ?")'),
    ],
)
def test_an_interpolated_table_is_refused_wherever_it_sits_in_the_clause(shape: str, source: str) -> None:
    """A hole reaches the table list by more than one route, and all of them hide a table."""
    offenders = unverified_inline_queries(source)

    assert offenders and "table name is interpolated" in offenders[0], (shape, offenders)


def test_a_hole_in_the_where_clause_is_still_allowed() -> None:
    """The line between which rows and which table, kept where the docstring says it is.

    This is the counterpart to the test above: widening the table check must not
    quietly turn into refusing every interpolated statement, which would fail the
    scoped spellings the guard is required to keep accepting.
    """
    source = 'connection.execute(f"SELECT outcome FROM lesson_results WHERE learner_id = ? AND id = {lesson}")'

    assert unverified_inline_queries(source) == []


@pytest.mark.parametrize(
    ("shape", "source"),
    [
        ("an f-string", 'connection.execute(f"{sql}")'),
        ("a format call", 'connection.execute("{}".format(sql))'),
        ("percent formatting", 'connection.execute("%s" % sql)'),
    ],
)
def test_a_statement_written_entirely_at_runtime_is_refused(shape: str, source: str) -> None:
    """The shape the refusal machinery pushes authors toward, so the one that must not pass.

    `connection.execute(sql)` on a parameter is refused. Wrapping it as
    `connection.execute(f"{sql}")` is a one-character edit, and it used to silence that
    refusal completely: the skeleton is nothing but a hole, so it names no table,
    _personal answers False about a statement it has not seen a character of, and the
    build goes green on a fully runtime-built query with no scoping check at all.

    A refusal an author can escape by making the code more dangerous is worse than no
    refusal, because it teaches the escape.
    """
    offenders = unverified_inline_queries(source)

    assert offenders, f"{shape} carrying the whole statement was skipped"
    assert "written literally" in offenders[0], offenders


def test_a_star_import_stops_the_guard_trusting_any_name() -> None:
    """A module that cannot say which names it binds cannot have its names read.

    `from x import *` rebinds an unknown set, so a module constant this guard resolved
    a moment ago may be something else entirely by the time the query runs.
    """
    source = (
        'QUERY = "SELECT outcome FROM lesson_results WHERE learner_id = ?"\n'
        "from elsewhere import *\n"
        "def read(connection):\n"
        "    return connection.execute(QUERY)\n"
    )

    offenders = unverified_inline_queries(source)

    assert offenders and "star-import" in offenders[0], offenders


@pytest.mark.parametrize(
    ("shape", "binder"),
    [
        ("a type alias", "type QUERY = str\n"),
        ("a type parameter", "def annotated[QUERY](x):\n    return x\n"),
    ],
)
def test_a_pep_695_binder_counts_as_a_binding(shape: str, binder: str) -> None:
    """Newer binding syntax shadows a name exactly as the older kinds do."""
    source = _SAFE_CONSTANT + binder + "def read(connection):\n    return connection.execute(QUERY)\n"

    offenders = unverified_inline_queries(source)

    assert offenders and "refused" in offenders[0], (shape, offenders)


@pytest.mark.parametrize(
    ("shape", "source"),
    [
        ("a double-quoted identifier", "connection.execute(f'SELECT outcome FROM \"{table}\" WHERE learner_id = ?')"),
        ("a bracketed identifier", 'connection.execute(f"SELECT outcome FROM [{table}] WHERE learner_id = ?")'),
        ("a backquoted identifier", 'connection.execute(f"SELECT outcome FROM `{table}` WHERE learner_id = ?")'),
    ],
)
def test_quoting_the_interpolated_table_does_not_hide_it(shape: str, source: str) -> None:
    """All three are valid SQLite identifier quoting, and all three hide a table name.

    Each carries a real learner filter, so the marker check would pass it. What is
    unknown is which table the filter is being applied to.
    """
    offenders = unverified_inline_queries(source)

    assert offenders and "table name is interpolated" in offenders[0], (shape, offenders)


def test_a_statement_passed_by_keyword_is_not_silently_skipped() -> None:
    """No positional argument is not the same as no statement.

    sqlite3's own execute takes the statement positionally so this shape never reaches
    it, but the guard should say so rather than fall quiet -- and a helper named
    execute would accept it.
    """
    offenders = unverified_inline_queries("connection.execute(sql=QUERY)")

    assert offenders and "by keyword" in offenders[0], offenders


@pytest.mark.parametrize(
    ("shape", "source"),
    [
        ("an annotated assignment", "run: Callable = connection.execute\nrun(QUERY)"),
        ("a tuple assignment", "run, _ = connection.execute, None\nrun(QUERY)"),
        ("a getattr value", 'run = getattr(connection, "execute")\nrun(QUERY)'),
        ("an attribute target", "self.run = connection.execute\nself.run(QUERY)"),
        ("a parameter default", "def read(connection, run=connection.execute):\n    return run(QUERY)"),
    ],
)
def test_taking_the_execute_method_as_a_value_is_never_invisible(shape: str, source: str) -> None:
    """Every way of holding the method, not just the one spelling that is easy to follow.

    Where the binding lands in a bare name the alias is followed and the statement gets
    a real verdict. Where it does not, the binding itself is refused. What must never
    happen is the third outcome -- nothing at all -- because an author reaching for
    `run = connection.execute` is usually hoisting a lookup out of a loop, not evading
    anything, and would never learn that the guard had stopped watching.
    """
    assert unverified_inline_queries(source), f"the method taken as a value via {shape} was invisible"


@pytest.mark.parametrize(
    ("shape", "statement"),
    [
        (
            "a tab inside DO UPDATE",
            '"INSERT INTO learners (id, x, y) VALUES (?, ?, ?) ON CONFLICT(id) DO\tUPDATE SET x = 1"',
        ),
        (
            "a newline inside DO UPDATE",
            '"INSERT INTO learners (id, x, y) VALUES (?, ?, ?) ON CONFLICT(id) DO\\nUPDATE SET x = 1"',
        ),
        (
            "an INSERT ... SELECT",
            '"INSERT INTO learners (id, x, y) SELECT learner_id, outcome, recorded_at FROM lesson_results"',
        ),
    ],
)
def test_the_learner_creation_exemption_covers_only_a_plain_insert(shape: str, statement: str) -> None:
    """SQL is whitespace-insensitive, so a space-only normalisation is not a normalisation.

    The exemption's justification is that creating a learner row reaches no other
    learner's data. That holds for an insert of literal values and nothing else: an
    upsert overwrites whoever holds that id, and an INSERT ... SELECT reads a table.
    """
    assert unverified_inline_queries(f"connection.execute({statement}, row)"), f"{shape} took the exemption"


def test_the_plain_seeding_insert_keeps_the_exemption() -> None:
    """The counterpart: narrowing the exemption must not break the statement it exists for."""
    seeding = (
        'connection.execute("INSERT INTO learners (id, display_name, created_at) '
        'VALUES (?, ?, ?) ON CONFLICT(id) DO NOTHING", rows)'
    )

    assert unverified_inline_queries(seeding) == []


def test_the_exemption_reads_a_plain_insert_however_it_is_laid_out() -> None:
    """Collapsing every whitespace character, rather than only spaces, cuts both ways.

    A tab defeating the UPDATE test is the dangerous direction and is covered above.
    This is the other one: a legitimate insert laid out with tabs must not be refused
    merely for its formatting, which a space-only normalisation would do by failing to
    find "VALUES(". A guard that cries wolf over whitespace is a guard people edit out.
    """
    spaced_out = (
        'connection.execute("INSERT INTO learners (id, display_name, created_at)\\t'
        'VALUES\\t(?, ?, ?) ON CONFLICT(id) DO NOTHING", rows)'
    )

    assert unverified_inline_queries(spaced_out) == []


@pytest.mark.parametrize(
    ("shape", "source"),
    [
        ("an f-string", 'connection.execute(f"SELECT {sql}")'),
        ("a format call", 'connection.execute("SELECT {}".format(sql))'),
        ("percent formatting", 'connection.execute("SELECT %s" % sql)'),
    ],
)
def test_one_literal_keyword_is_not_enough_to_pass(shape: str, source: str) -> None:
    """A head keyword says what the statement does, not what it does it to.

    This is the wholly-interpolated refusal narrowed by exactly one token, and it was
    the escape from that refusal: keep `SELECT` literal and the rest runtime-built, and
    the statement names no table, so nothing downstream has anything to object to.
    """
    offenders = unverified_inline_queries(source)

    assert offenders, f"{shape} carrying everything but a keyword was skipped"
    assert "names no table" in offenders[0], offenders


def test_a_pragma_may_still_interpolate_because_it_reaches_no_table() -> None:
    """The rule above must not catch the module's one legitimate interpolation.

    store.py builds `PRAGMA user_version = {int(SCHEMA_VERSION)}` that way because a
    pragma cannot take a bound parameter. It also cannot reach a table, which is why
    requiring a literal table name is a rule about table-accessing statements only.
    """
    assert unverified_inline_queries('connection.execute(f"PRAGMA user_version = {int(SCHEMA_VERSION)}")') == []


def test_reading_through_the_scoping_helper_stops_if_its_name_is_rebound() -> None:
    """The one name the guard trusts by spelling does not get to be the exception.

    Every other name is refused when it is bound more than once. Reading through a
    rebound _learner_scoped would mean reading the safe spelling of the rule and
    approving whatever the rebinding actually does.
    """
    source = (
        "def _learner_scoped(sql):\n    return sql\n\n"
        '_learner_scoped = lambda s: s.replace(" WHERE learner_id = ?", "")\n'
        'QUERY = _learner_scoped("SELECT outcome FROM lesson_results WHERE learner_id = ?")\n'
        "connection.execute(QUERY)\n"
    )

    offenders = unverified_inline_queries(source)

    assert offenders and "bound more than once" in offenders[0], offenders


@pytest.mark.parametrize(
    ("shape", "source"),
    [
        ("one hop", "run = connection.execute\nalias = run\nalias(QUERY)"),
        ("two hops", "run = connection.execute\nmid = run\nalias = mid\nalias(QUERY)"),
    ],
)
def test_an_alias_of_an_alias_is_followed(shape: str, source: str) -> None:
    """`r2 = r1` binds a Name, not an Attribute, so one pass sees only the first hop.

    Nothing about hoisting a method lookup says it happens once, and a chain is just
    as ordinary as a single step -- so following it has to be a fixed point, not a
    single pass.
    """
    assert unverified_inline_queries(source), f"an alias chain of {shape} went unseen"


@pytest.mark.parametrize("builtin", ["exec", "eval"])
def test_a_statement_built_inside_exec_or_eval_is_refused(builtin: str) -> None:
    """Source the guard reads cannot show what a string handed to exec() will run.

    store.py contains neither today. That is the point of reporting it: the moment one
    appears, every check in this guard is reasoning about a module whose statements it
    can no longer enumerate, and it should say so rather than keep reporting green.
    """
    offenders = unverified_inline_queries(f"{builtin}('connection.execute(\"SELECT outcome FROM lesson_results\")')")

    assert offenders and builtin in offenders[0], offenders


@pytest.mark.parametrize(
    ("shape", "statement"),
    [
        ("a line comment", '"SELECT outcome FROM lesson_results -- learner_id = ?"'),
        ("a block comment", '"SELECT outcome FROM lesson_results /* learner_id = ? */"'),
    ],
)
def test_a_scoping_marker_inside_a_comment_does_not_count(shape: str, statement: str) -> None:
    """The database never reads a comment, so a filter written in one filters nothing.

    _learner_scoped drops closed comments before it reads a statement -- and refuses a
    statement with an unterminated one -- so this is the same answer the import-time
    check gives. It used to be a different one: the rule was a
    substring search that could not tell a comment from SQL, and this guard stripped
    them on its way in, which left the identical statement refused here and accepted at
    import. The strip moved into the rule, so there is one answer now.
    """
    assert unverified_inline_queries(f"connection.execute({statement})"), f"{shape} passed as scoping"


def test_a_real_marker_outside_a_comment_still_counts() -> None:
    """The counterpart: stripping comments must not strip the filter itself."""
    scoped = 'connection.execute("SELECT outcome FROM lesson_results WHERE learner_id = ? -- by learner")'

    assert unverified_inline_queries(scoped) == []


@pytest.mark.parametrize(
    ("shape", "source"),
    [
        ("behind a line comment", 'connection.execute(f"-- note\\nSELECT {sql}")'),
        ("behind a block comment", 'connection.execute(f"/* note */ SELECT {sql}")'),
        ("an ATTACH", "connection.execute(f\"ATTACH DATABASE '{path}' AS other\")"),
        ("a DROP", 'connection.execute(f"DROP TABLE {table}")'),
        ("an ALTER", 'connection.execute(f"ALTER TABLE {table} RENAME TO x")'),
    ],
)
def test_an_interpolated_statement_must_name_a_table_whatever_its_head(shape: str, source: str) -> None:
    """Stating the exemption instead of listing the heads is what makes this hold.

    The first attempt listed the table-accessing keywords and anchored them at the
    start of the statement, so two characters of comment slipped past it, and it named
    none of ATTACH, DROP or ALTER -- each of which interpolates an identifier, which
    can never be a bound parameter. An ATTACH is the sharpest: the schema check already
    refuses one in schema.sql because it mounts another database, yet the same
    statement written inline was invisible here.
    """
    offenders = unverified_inline_queries(source)

    assert offenders, f"an interpolated statement {shape} was skipped"
    assert "names no table" in offenders[0], offenders


@pytest.mark.parametrize(
    ("shape", "source"),
    [
        ("a computed getattr", "run = getattr(connection, name)\nrun(QUERY)"),
        ("__getattribute__", 'run = connection.__getattribute__("execute")\nrun(QUERY)'),
        ("__getattr__", 'run = connection.__getattr__("execute")\nrun(QUERY)'),
    ],
)
def test_an_execute_reached_by_a_name_the_source_does_not_show_is_still_seen(shape: str, source: str) -> None:
    """A method name the source does not spell out could be any of the three.

    The question this answers is whether the execute method might be being taken, and
    an unreadable attribute name cannot answer no -- so it counts as one.
    """
    assert unverified_inline_queries(source), f"an execute reached through {shape} was invisible"


def test_every_statement_in_the_module_is_read_rather_than_skipped() -> None:
    """Coverage itself, pinned: exactly one statement in the module is not readable.

    The old guard skipped every non-literal silently, so its coverage could shrink to
    nothing without a test changing colour. This fails if a new unreadable statement
    appears, which is the point -- the author has to either rewrite it or argue for it.
    """
    source = Path(store.__file__).resolve().read_text(encoding="utf-8")
    tree = ast.parse(source)
    bindings = _module_bindings(tree)

    aliases = _execute_aliases(tree)

    unreadable: list[str] = []
    for node in _execute_sites(tree, aliases):
        if not node.args:
            continue
        try:
            _skeleton(node.args[0], bindings)
        except _Unverifiable:
            # The expression, not its line number: a line number pins this to edits
            # anywhere above it in another file, and the obvious way to quiet that
            # recurring red is to weaken the assertion.
            unreadable.append(ast.unparse(node.args[0]))

    assert sorted(unreadable) == ["_consent_scope_migration_sql()", "_schema_sql()"], (
        f"only the two bundled .sql files should be unreadable to the guard; got {unreadable}"
    )


def test_the_coverage_pin_looks_through_the_same_eyes_as_the_guard() -> None:
    """The pin and the guard must find the same statements, or the pin pins nothing.

    They used to walk separate copies of the same predicate. When the guard learned to
    follow an aliased execute, the pin did not, so the test whose whole job is to stop
    coverage shrinking had itself stopped seeing the statements the guard now reads --
    and it stayed green, because store.py happens to contain no alias. This asserts the
    two agree on planted source that does contain one.
    """
    planted = "run = connection.execute\nrun(build_the_query())\n"
    tree = ast.parse(planted)
    aliases = _execute_aliases(tree)

    assert [ast.unparse(site.func) for site in _execute_sites(tree, aliases)] == ["run"]
    assert unverified_inline_queries(planted), "the guard sees it, so the pin must too"


# What a schema file is allowed to contain. An allow-list, so CREATE VIEW, CREATE
# TRIGGER, ATTACH, DROP and ALTER all fail by default rather than needing to be
# thought of in advance.
_DDL_ALLOWED = ("PRAGMA", "CREATE TABLE", "CREATE INDEX", "CREATE UNIQUE INDEX")


def _statements_that_are_not_table_creation(ddl: str) -> list[str]:
    """Return every statement in a script that is not a plain table or index creation.

    Whole statements, not lines. A line test only ever sees what a statement starts
    with, so `CREATE VIEW everything AS SELECT * FROM lesson_results;` reads as a
    CREATE and a one-line trigger body hides its INSERT in the middle of the line.
    """
    without_comments = re.sub(r"/\*.*?\*/", " ", re.sub(r"--[^\n]*", " ", ddl), flags=re.DOTALL)
    statements = [" ".join(part.split()).upper() for part in without_comments.split(";") if part.strip()]
    return [
        statement[:70]
        for statement in statements
        # "CREATE TABLE x AS SELECT ..." begins with an allowed prefix while running a
        # query and minting a table name _personal has never heard of -- the same
        # laundering a view does, wearing an allowed head.
        if not statement.startswith(_DDL_ALLOWED) or "AS SELECT" in statement
    ]


def test_the_bundled_schema_creates_tables_and_nothing_else() -> None:
    """What makes the one exemption safe, rather than merely narrow.

    _schema_sql() reads its text from a file at runtime, so no amount of source reading
    can show what it executes. The exemption is therefore backed here instead.

    Creating tables and indexes is the whole of what it may do. A view would be the
    quiet way through: `CREATE VIEW all_results AS SELECT * FROM lesson_results` both
    runs a personal-data query here AND launders the table name, so a later
    `execute("SELECT * FROM all_results")` would not look personal to the guard either.
    Two green tests and a complete cross-learner read path.
    """
    ddl = (Path(store.__file__).resolve().parent / "schema.sql").read_text(encoding="utf-8")

    assert _statements_that_are_not_table_creation(ddl) == []


@pytest.mark.parametrize(
    ("shape", "statement"),
    [
        ("a view", "CREATE VIEW all_results AS SELECT * FROM lesson_results;"),
        ("a one-line trigger", "CREATE TRIGGER t AFTER INSERT ON lesson_results BEGIN SELECT 1; END;"),
        ("an attach", "ATTACH DATABASE '/tmp/other.sqlite' AS other;"),
        ("a bare select", "SELECT * FROM lesson_results;"),
        ("a drop", "DROP TABLE learners;"),
        ("a create-table-as-select", "CREATE TABLE all_results AS SELECT * FROM lesson_results;"),
    ],
)
def test_the_schema_check_rejects_what_it_claims_to(shape: str, statement: str) -> None:
    """The guard backing the exemption, shown failing before it is trusted.

    It is a new guard introduced by this change, so it is subject to the same rule as
    the rest: a guard that has never been demonstrated to fail is trusted on nothing.
    """
    ddl = (Path(store.__file__).resolve().parent / "schema.sql").read_text(encoding="utf-8")

    assert _statements_that_are_not_table_creation(ddl + "\n" + statement), f"{shape} was accepted"


def test_the_guard_flags_a_leak_planted_in_the_real_module_source() -> None:
    """The proof runs over the real file, not only over a snippet.

    A guard demonstrated only against hand-written fragments proves that the fragments
    parse. Planting into the module's own source is what shows the guard would catch
    this change if someone actually made it.
    """
    source = Path(store.__file__).resolve().read_text(encoding="utf-8")
    assert unverified_inline_queries(source) == [], "the unmodified module must be clean"

    planted = source + (
        "\n\ndef _every_result(connection, language_code):\n"
        "    return connection.execute(\n"
        '        f"SELECT outcome FROM lesson_results AS r "\n'
        '        f"JOIN lessons AS l ON l.id = r.lesson_id WHERE l.language_code = {language_code}"\n'
        "    ).fetchall()\n"
    )

    offenders = unverified_inline_queries(planted)

    assert len(offenders) == 1, offenders
    assert "lesson_results" in offenders[0], offenders


def test_learner_id_is_the_first_argument() -> None:
    """Every learner-scoped function takes the learner id first."""
    for function in (store.get_profile, store.get_progress, store.record_result):
        first = list(inspect.signature(function).parameters)[0]
        assert first == "learner_id", f"{function.__name__} takes {first} first"


def test_package_exports_only_the_interface() -> None:
    """The package boundary must not leak the storage engine."""
    assert set(learners.__all__) == {
        # The wording for the local_profile scope. Published because the operator
        # command shows it before asking, and a notice a surface cannot reach is a
        # notice nobody is shown.
        "LOCAL_PROFILE_STATEMENT",
        "LOCAL_PROFILE_STATEMENT_DIGESTS",
        "LOCAL_PROFILE_STATEMENT_ID",
        "DRILL_KINDS",
        "DialogueTurn",
        "Drill",
        "LESSON_ORIGINS",
        "LessonContent",
        "LessonSource",
        "UsageNote",
        "get_lesson_content",
        "CatalogLanguage",
        "LanguageProgress",
        "LearnerProfile",
        "LessonAttempt",
        "Lesson",
        "OUTCOMES",
        "PractisedLanguage",
        "RECORD_REASONS",
        "RecordResultOutcome",
        "get_language_catalog",
        "get_lesson",
        "get_practised_languages",
        "get_profile",
        "get_progress",
        "record_result",
        "store_is_available",
        "FACEPRINT_REASONS",
        "Faceprint",
        "SaveFaceprintOutcome",
        "get_faceprint",
        "get_enrolled_faceprints",
        "save_faceprint",
        "delete_faceprint",
        "CONSENT_SCOPES",
        "CONSENT_REASONS",
        "CONSENT_GRANTED_BY",
        "CONSENT_GRANTED_VIA",
        "ConsentRecord",
        "ConsentOutcome",
        "record_consent",
        "get_consents",
        "forget_learner",
        "forget_learner_entirely",
        "ErasureOutcome",
        "split_catalog_by_material",
    }
    for leaked in ("connect", "NEXT_LESSON_SQL", "ensure_learner_database", "SEED_LESSONS"):
        assert leaked not in learners.__all__


def test_the_documented_import_block_lists_every_exported_function() -> None:
    """The doc calls that block "the whole vocabulary a caller needs", so it must be.

    It drifted the moment get_language_catalog was added: the table below it was
    updated and the block above it was not. A reader copying the block would not get
    the function the table documents.
    """
    doc = (Path(__file__).resolve().parents[2] / "docs" / "learner-database.md").read_text(encoding="utf-8")
    block = doc.split("from reachy_language_tutor.learners import (", 1)[1].split(")", 1)[0]
    documented = {line.strip().rstrip(",") for line in block.splitlines() if line.strip()}

    exported_functions = {name for name in learners.__all__ if not name[0].isupper()}
    assert documented == exported_functions


def test_a_lesson_can_be_looked_up_by_id(instance: Path) -> None:
    """One read, so a caller holding a lesson id need not scan every language."""
    lesson = store.get_lesson("es-03-numbers", instance_path=instance)

    assert lesson is not None
    assert lesson.language_code == "es"
    # 9, not 3: the six Spanish placeholders shifted to 7-12 when the six converted
    # Cycles of the Spanish FAST took positions 1-6.
    assert lesson.position == 9


def test_an_unknown_lesson_id_is_none(instance: Path) -> None:
    """No such lesson is an answer, not a failure."""
    assert store.get_lesson("es-99-nonexistent", instance_path=instance) is None


@pytest.mark.parametrize(
    "lesson_id",
    [None, 7, b"es-03-numbers", "", ["es-03-numbers"], " es-03-numbers", "es-03-numbers ", "es-03\nnumbers"],
)
def test_an_unusable_lesson_id_is_refused_rather_than_bound(
    instance: Path, lesson_id: object, caplog: pytest.LogCaptureFixture
) -> None:
    """Values the database would ACCEPT as a binding but that can never match.

    The same class store.get_progress refuses for a catalog code: bound cleanly,
    matched nothing, and came back as a silent absence.

    Asserting the LOG, not only the None, is what gives this teeth: an UNGUARDED
    get_lesson returns None for every one of these too, by binding and missing, so a
    bare `is None` cannot tell "refused" from "matched nothing" -- which is the exact
    distinction this test's name claims. Delete the guard and this fails.
    """
    with caplog.at_level(logging.WARNING):
        assert store.get_lesson(lesson_id, instance_path=instance) is None

    assert "Could not read a lesson id" in caplog.text


def test_an_unreadable_store_makes_a_lesson_lookup_empty_and_noisy(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Breakage logs; a genuine absence does not."""
    with caplog.at_level(logging.WARNING):
        assert store.get_lesson("es-03-numbers", instance_path=tmp_path / "nonexistent") is None

    # The colon matters: "Could not read a lesson" is a prefix of BOTH this message and
    # the argument guard's, so without it a guard that started firing on valid input
    # would pass this test. One prefix per meaning, asserted as one prefix per meaning.
    assert "Could not read a lesson:" in caplog.text
    assert "Could not read a lesson id" not in caplog.text


def test_the_lesson_lookup_query_names_no_personal_table() -> None:
    """Shared reference data, so it is correctly absent from the learner-scoped set."""
    assert _personal(store._LESSON_BY_ID_SQL) is False
    assert store._LESSON_BY_ID_SQL not in store._LEARNER_SCOPED_SQL


def test_the_language_catalog_lists_every_taught_language(instance: Path) -> None:
    """The catalog is what a caller checks instead of reading get_progress's None."""
    catalog = store.get_language_catalog(instance_path=instance)

    assert [(entry.code, entry.name) for entry in catalog] == [
        ("fr", "French"),
        ("de", "German"),
        ("it", "Italian"),
        ("pt", "Portuguese"),
        ("es", "Spanish"),
    ]


def test_the_language_catalog_is_empty_and_noisy_when_the_store_is_unreadable(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Silence is what separates a genuine absence from breakage, so breakage must log."""
    with caplog.at_level(logging.WARNING):
        catalog = store.get_language_catalog(instance_path=tmp_path / "nonexistent")

    assert catalog == ()
    assert "Could not read the language catalog" in caplog.text


def test_the_language_catalog_is_silently_empty_when_the_table_holds_no_rows(instance: Path) -> None:
    """An empty catalog is not breakage, but a caller must still not claim what is taught.

    store_is_available answers True here, which is exactly why it cannot be the test a
    caller uses to tell "not taught" from "unreadable".
    """
    connection = store.connect(instance)
    connection.execute("DELETE FROM languages")
    connection.commit()
    connection.close()

    assert store.get_language_catalog(instance_path=instance) == ()
    assert store.store_is_available(instance_path=instance) is True


def test_the_catalog_query_names_no_personal_table() -> None:
    """Shared reference data, so it is correctly absent from the learner-scoped set."""
    assert _personal(store._LANGUAGE_CATALOG_SQL) is False
    assert store._LANGUAGE_CATALOG_SQL not in store._LEARNER_SCOPED_SQL


def test_practised_languages_lists_only_what_the_learner_has_worked_on(instance: Path) -> None:
    """Seeded Spanish has attempts; French does not, so only Spanish comes back."""
    practised = store.get_practised_languages("sample-learner", instance_path=instance)

    assert [(language.code, language.name) for language in practised] == [("es", "Spanish")]
    assert practised[0].attempts >= 1
    assert practised[0].completed >= 1


def test_practised_languages_counts_attempts_and_completions_separately(instance: Path) -> None:
    """A retried lesson is two attempts but one completion; the tutor says different things."""
    lesson = store.get_progress("sample-learner", "fr", instance_path=instance).next_lesson
    for outcome in ("partial", "partial", "completed"):
        assert store.record_result("sample-learner", lesson.id, outcome, instance_path=instance).recorded is True

    french = next(
        language
        for language in store.get_practised_languages("sample-learner", instance_path=instance)
        if language.code == "fr"
    )
    assert french.attempts == 3
    assert french.completed == 1


def test_a_skipped_lesson_is_not_practice(instance: Path) -> None:
    """Declining a lesson must not be reported back as having worked on the language."""
    lesson = store.get_progress("sample-learner", "fr", instance_path=instance).next_lesson
    assert store.record_result("sample-learner", lesson.id, "skipped", instance_path=instance).recorded is True

    practised = store.get_practised_languages("sample-learner", instance_path=instance)
    assert [language.code for language in practised] == ["es"], "a skipped-only language is not practised"

    # The skip is still recorded; it simply does not count as practice.
    assert store.record_result("sample-learner", lesson.id, "partial", instance_path=instance).recorded is True
    french = next(
        language
        for language in store.get_practised_languages("sample-learner", instance_path=instance)
        if language.code == "fr"
    )
    assert (french.attempts, french.completed) == (1, 0), french


def test_practised_languages_is_ordered_by_name(instance: Path) -> None:
    """A stable order, so the tutor does not name languages differently each time."""
    lesson = store.get_progress("sample-learner", "fr", instance_path=instance).next_lesson
    store.record_result("sample-learner", lesson.id, "completed", instance_path=instance)

    practised = store.get_practised_languages("sample-learner", instance_path=instance)
    assert [language.name for language in practised] == ["French", "Spanish"]


def test_practised_languages_never_reaches_another_learner(instance: Path) -> None:
    """The scoping boundary, checked on real rows rather than on the statement alone."""
    _add_learner(instance, "housemate")
    lesson = store.get_progress("housemate", "fr", instance_path=instance).next_lesson
    store.record_result("housemate", lesson.id, "completed", instance_path=instance)

    assert [language.code for language in store.get_practised_languages("sample-learner", instance_path=instance)] == [
        "es"
    ]
    assert [language.code for language in store.get_practised_languages("housemate", instance_path=instance)] == ["fr"]


def test_practised_languages_is_empty_for_an_unknown_learner(instance: Path) -> None:
    """An unknown learner has practised nothing; that is an empty tuple, not an error."""
    assert store.get_practised_languages("no-such-learner", instance_path=instance) == ()


def test_practised_languages_is_empty_when_the_store_is_unreadable(tmp_path: Path) -> None:
    """It must not raise; the caller checks store_is_available to tell the two apart."""
    assert store.get_practised_languages("sample-learner", instance_path=tmp_path) == ()
    assert store.store_is_available(tmp_path) is False


def test_outcomes_match_the_schema_constraint() -> None:
    """The Python vocabulary and the database's own constraint must not drift."""
    schema = (Path(store.__file__).resolve().parent / "schema.sql").read_text(encoding="utf-8")
    constraint = schema.split("outcome     TEXT    NOT NULL CHECK (outcome IN (", 1)[1].split("))", 1)[0]

    for outcome in learners.OUTCOMES:
        assert f"'{outcome}'" in constraint
    assert constraint.count(",") == len(learners.OUTCOMES) - 1


# ------------------------------------------------------------------- integration


def test_fresh_instance_path_is_queryable_in_one_call(tmp_path: Path) -> None:
    """A path that does not exist becomes a queryable store with no robot or daemon."""
    fresh = tmp_path / "instance"
    assert not fresh.exists()

    assert store.ensure_learner_database(fresh).ready is True

    progress = learners.get_progress("sample-learner", "es", instance_path=fresh)
    assert progress is not None
    assert progress.next_lesson is not None
    assert progress.next_lesson.id == "es-fast-01-getting-started-in-class"
    assert isinstance(progress.next_lesson, Lesson)
    assert isinstance(progress.attempts[0], LessonAttempt)


# ------------------------------------------------- a value the driver cannot bind
#
# sqlite3 raises OverflowError while BINDING an int outside SQLite's signed 64-bit
# range, before the database sees the statement. It subclasses ArithmeticError, so it
# was in none of (sqlite3.Error, OSError, ValueError) and travelled straight out of
# three readers whose docstrings promise never to raise. D5 fixed the same class of
# bug in record_result; these are the rest.


# Everything a conversation tool can put where a learner id belongs. object() is here
# for the same reason it is in the recorded_at list: it is not JSON, but neither was
# the assumption that only JSON arrives.
_HOSTILE_IDS = [
    2**63,
    -(2**63) - 1,
    10**30,
    -(10**30),
    "sample-learner",
    "42",
    "",
    None,
    True,
    3.5,
    float("nan"),
    b"sample-learner",
    [],
    {},
    (),
    object(),
]


def test_no_reader_raises_whatever_the_learner_id(instance: Path) -> None:
    """The contract all three share, stated as a test rather than three docstrings.

    The caller is a conversation tool, so an exception here ends the turn -- and the
    learner id is normally a str, which means an int arriving at all already says
    something upstream is wrong. That is exactly when a reader is supposed to answer
    rather than crash.
    """
    for learner_id in _HOSTILE_IDS:
        # Each reader's own contract type, never "is None or True" -- that spelling
        # can never fail, so it would assert only that nothing raised while reading
        # like it checked the answer.
        profile = store.get_profile(learner_id, instance_path=instance)
        assert profile is None or isinstance(profile, LearnerProfile), learner_id
        assert isinstance(store.get_practised_languages(learner_id, instance_path=instance), tuple), learner_id
        # NOT "progress is None or it is about es" -- a populated fresh-start for an
        # id that can never match satisfies that while being the wrong answer, and
        # float("nan") in the list above walked exactly that path unnoticed.
        progress = store.get_progress(learner_id, "es", instance_path=instance)
        if progress is not None:
            assert progress.learner_id == learner_id, learner_id
            assert progress.language_code == "es", learner_id


def test_an_unbindable_id_answers_absence_rather_than_raising(instance: Path) -> None:
    """Each reader's own contract value, not merely 'it did not raise'.

    get_practised_languages returns an empty tuple rather than None, so a test that
    only asserted 'no exception' would pass while one of them started answering the
    wrong shape.
    """
    for learner_id in (2**63, -(2**63) - 1, 10**30):
        assert store.get_profile(learner_id, instance_path=instance) is None, learner_id
        assert store.get_practised_languages(learner_id, instance_path=instance) == (), learner_id
        assert store.get_progress(learner_id, "es", instance_path=instance) is None, learner_id


def test_the_last_id_sqlite_can_bind_is_still_looked_up(instance: Path) -> None:
    """The boundary, from both sides, so the fix cannot become "refuse large ints".

    An earlier version of this test only asserted None at the boundary -- which a
    reader that had short-circuited on magnitude would also satisfy, so it proved
    nothing. These ids are seeded as the text they are, and SQLite's TEXT affinity
    makes the bound integer match them, so finding the row is only possible if the
    value really reached the database.
    """
    for learner_id in (2**63 - 1, -(2**63)):
        _add_learner(instance, str(learner_id), name=f"Edge {learner_id}")
        _add_result(instance, str(learner_id), "es-01-greetings", "completed")

        profile = store.get_profile(learner_id, instance_path=instance)
        assert profile is not None and profile.display_name == f"Edge {learner_id}", learner_id

        # Still a real query on the other two as well, and each asserted on a POPULATED
        # answer. An earlier version asserted get_practised_languages(...) == (), which
        # is also what an absorbed failure returns -- so that line could not tell a real
        # query from a reader that had bailed out on magnitude, and proved nothing.
        progress = store.get_progress(learner_id, "es", instance_path=instance)
        assert progress is not None and progress.next_lesson is not None, learner_id
        practised = store.get_practised_languages(learner_id, instance_path=instance)
        assert [(lang.code, lang.attempts) for lang in practised] == [("es", 1)], learner_id


def test_the_language_code_is_refused_before_it_can_ever_be_bound(
    instance: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The second value this reader takes, which the defect report did not name.

    An earlier version of this test asserted only that an out-of-range int came back
    None, and claimed to prove the widened except tuple covered it. It no longer does
    and should not: the guard refuses these before a connection is even opened, so
    OverflowError is unreachable from this argument by construction. Deleting
    OverflowError from _READER_ABSORBS would leave that old assertion green, which is
    the whole reason it is written this way now. The tuple is still pinned for the
    learner id, which does reach a bind, by the two tests above.
    """
    for language_code in (2**63, -(2**63) - 1, 10**30):
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            assert store.get_progress("sample-learner", language_code, instance_path=instance) is None
        assert "Could not read a language code: int is not a string" in caplog.text, language_code


def test_an_unbindable_language_code_is_separable_from_a_language_we_do_not_teach(
    instance: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Both answer None, and only the log can tell them apart.

    This reader's None means "this robot has no such language" -- a claim about the
    shared lesson catalog. An unbindable language code supports no such claim, and
    store_is_available cannot help: it binds no caller value, so it stays True. Without
    its own log line the tutor would say "I do not teach that" about a language it may
    well teach, with every available signal agreeing.
    """
    with caplog.at_level(logging.WARNING):
        assert store.get_progress("sample-learner", UNTAUGHT_CODE, instance_path=instance) is None
    assert caplog.text == "", "a language we genuinely do not teach is not a failure"

    with caplog.at_level(logging.WARNING):
        assert store.get_progress("sample-learner", 10**30, instance_path=instance) is None
    assert "Could not read a language code" in caplog.text
    assert store.store_is_available(instance) is True, "the store is fine; the argument was not"


def test_silence_is_what_separates_a_real_absence_from_every_other_none(
    instance: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """get_progress's None does three jobs; only the log tells them apart.

    store_is_available cannot: it binds no caller value, so it answers True for both
    argument failures below. A caller that read True as confirmation would tell a
    person "I do not teach that" about a language this robot may well teach, with
    every other signal agreeing. So the rule is silence, and it is pinned here rather
    than only asserted in the docstring.
    """
    # The first four rows are the ones that matter. An earlier version of this rule was
    # FALSE for them and this table did not notice, because every case it walked was a
    # value sqlite3 REFUSES to bind. These bind cleanly and match nothing: languages.code
    # is TEXT in a STRICT table, so a BLOB never equals it, NULL never equals anything,
    # and 42/3.5/True take TEXT affinity and become "42"/"3.5"/"1". Each answered a
    # silent None for a language this robot teaches.
    quiet_then_loud: list[tuple[str, object, object, str]] = [
        ("bytes language code", "sample-learner", b"es", "Could not read a language code"),
        ("None language code", "sample-learner", None, "Could not read a language code"),
        ("int language code", "sample-learner", 42, "Could not read a language code"),
        ("bool language code", "sample-learner", True, "Could not read a language code"),
        ("float language code", "sample-learner", 3.5, "Could not read a language code"),
        # The third class, and the one that survived two earlier versions of this
        # rule: a correctly typed str that binds cleanly and differs from a taught
        # code only in case or padding. schema.sql CHECKs code = lower(code), so a
        # non-lowercase code is a GUARANTEED false absence, never a real one.
        ("uppercase, which the catalog CHECK forbids", "sample-learner", "ES", "Could not read a language code"),
        ("mixed case", "sample-learner", "Es", "Could not read a language code"),
        (
            "a surrogate, which is a str the driver cannot bind",
            "sample-learner",
            "e\ud800s",
            "Could not read a language code",
        ),
        ("the empty string, too short to be a code", "sample-learner", "", "Could not read a language code"),
        (
            "padded, which no CHECK forbids and nothing matched",
            "sample-learner",
            " es",
            "Could not read a language code",
        ),
        ("a control character", "sample-learner", "es\x00", "Could not read a language code"),
        ("a language we really do not teach", "sample-learner", UNTAUGHT_CODE, ""),
        ("a legal-shaped code that is simply absent", "sample-learner", UNTAUGHT_CODE_ABSENT, ""),
        ("an unbindable language code", "sample-learner", 10**30, "Could not read a language code"),
        # The fourth class, and the one a list of refusals could not close: a character
        # that renders as nothing. Each of these used to bind, match nothing and answer
        # the SILENT None that means "not taught here". They are outside the permitted
        # shape, so they are refused by not being in it rather than by being spotted.
        ("a zero-width space", "sample-learner", "es\u200b", "Could not read a language code"),
        ("a byte-order mark", "sample-learner", "\ufeffes", "Could not read a language code"),
        ("a soft hyphen", "sample-learner", "e\u00ads", "Could not read a language code"),
        # An out-of-range id is refused by the learner-id guard before the bind now, so
        # it is named as the caller error it is. It used to reach the bind and be logged
        # as "Could not read learner progress" -- the store-fault prefix, on a healthy
        # store, which is the mislabel this table exists to catch.
        ("an unbindable learner id", 10**30, "es", "Could not read a learner id"),
        ("a list as a learner id", ["sample-learner"], "es", "Could not read a learner id"),
        ("a dict as a learner id", {"id": "sample-learner"}, "es", "Could not read a learner id"),
    ]

    for label, learner_id, language_code, expected in quiet_then_loud:
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            assert store.get_progress(learner_id, language_code, instance_path=instance) is None, label
        if expected:
            assert expected in caplog.text, label
        else:
            assert caplog.text == "", label
        # The disambiguator the docstring tells callers NOT to use, shown failing.
        assert store.store_is_available(instance) is True, label


def test_a_real_learner_is_unaffected(instance: Path) -> None:
    """The other half of any except-clause change: the path that was always fine."""
    profile = store.get_profile("sample-learner", instance_path=instance)
    assert profile is not None and profile.display_name == "Sample Learner"

    progress = store.get_progress("sample-learner", "es", instance_path=instance)
    assert progress is not None and progress.language_code == "es"

    practised = store.get_practised_languages("sample-learner", instance_path=instance)
    # A real row, not an empty tuple -- which is what an absorbed failure returns, so
    # an assertion of () here would have passed even if this reader had stopped working.
    assert [(lang.code, lang.attempts) for lang in practised] == [("es", 3)]


def test_the_overflow_warning_names_the_exception_and_not_the_id(
    instance: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Widening an except clause is exactly where the no-ids-in-logs rule gets lost.

    The existing rule is pinned for a missing database; this is the newly caught path,
    and the id here is a distinctive number rather than a name, so a leak is visible.
    """
    with caplog.at_level(logging.WARNING):
        store.get_profile(1234567890123456789012345, instance_path=instance)
        store.get_practised_languages(1234567890123456789012345, instance_path=instance)
        store.get_progress(1234567890123456789012345, "es", instance_path=instance)
        # The newest handler too. It refuses before the id is ever bound, which is
        # why it cannot leak one -- but "widening a handler is where this rule gets
        # forgotten" applies hardest to the handler added last.
        store.get_progress(1234567890123456789012345, b"es", instance_path=instance)

    assert "1234567890123456789012345" not in caplog.text, "learner ids are personal data"
    assert caplog.text.count("too large") == 3, "each reader should have logged its own refusal"
    assert "bytes is not a string" in caplog.text, "the type, which is safe; never the value"

    # A surrogate is the one value whose EXCEPTION message quotes its argument:
    # UnicodeEncodeError names the offending character and its index, so letting it
    # reach a handler would put a fragment of the id in a line whose comment promises
    # otherwise. It is refused before the bind now, and _log_safe covers the same
    # class on the paths a guard cannot reach.
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        store.get_profile("alice\ud800bob", instance_path=instance)
    assert "ud800" not in caplog.text and "position" not in caplog.text, "no fragment of the id"
    assert "not encodable as UTF-8" in caplog.text, "still diagnosable: the shape, not the value"


def test_a_broken_store_is_not_reported_as_a_bad_language_code(
    instance: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The two messages have to keep meaning what the docstring says they mean.

    An earlier version wrapped the first query in its own handler, so a dropped table
    -- a storage failure -- was reported as "Could not read a language code". That
    reads to an operator as a bad argument on a healthy store, which is the blur this
    task is about, arriving through the very line added to prevent it. The guard is a
    type check before the connection now, so a storage failure falls through to the
    handler that describes one.
    """
    connection = store.connect(instance)
    try:
        connection.execute("DROP TABLE languages")
        connection.commit()
    finally:
        connection.close()

    with caplog.at_level(logging.WARNING):
        assert store.get_progress("sample-learner", "es", instance_path=instance) is None

    assert "Could not read learner progress" in caplog.text
    assert "Could not read a language code" not in caplog.text


def test_a_bad_language_code_is_not_reported_as_a_broken_store(
    instance: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The converse direction, which is where the last regression hid.

    Pinning only "a storage failure is not called a bad code" left the mirror image
    free: a str carrying a surrogate passes any type check, fails at bind time with a
    UnicodeEncodeError that is a ValueError, and was logged as a failed lookup on a
    perfectly healthy store. One-directional pins are how a fix trades one mislabel
    for the other and the suite stays green.
    """
    with caplog.at_level(logging.WARNING):
        assert store.get_progress("sample-learner", "e\ud800s", instance_path=instance) is None

    assert "Could not read a language code" in caplog.text
    assert "Could not read learner progress" not in caplog.text
    assert store.store_is_available(instance) is True


def test_store_is_available_still_separates_absence_from_breakage(instance: Path, tmp_path: Path) -> None:
    """The distinction the readers depend on, re-checked after collapsing one more case.

    A value the driver cannot bind now answers None like a broken store does -- so the
    thing that tells those apart has to keep working, or the collapse would be the
    blurring it is not supposed to be.
    """
    assert store.store_is_available(instance) is True
    assert store.get_profile(10**30, instance_path=instance) is None

    broken = tmp_path / "broken"
    broken.mkdir()
    store.learner_db_path_for_instance(broken).write_bytes(b"not a database at all")
    assert store.store_is_available(broken) is False


def test_record_result_still_answers_with_a_reason_code(instance: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A learner id nothing can look up is refused, and refused as a caller error.

    record_result returns a reason rather than None. An unbindable id used to reach the
    driver and come back rejected_by_database; a list or a dict failed at the bind and
    came back storage_unavailable, telling a caller the robot was broken when it had
    been sent nonsense. Both are refused before anything is opened now, with the code a
    value naming no learner earns -- unknown_learner, as the lesson id's guard answers
    unknown_lesson.

    The two unknown_learner answers still differ, and the difference is pinned: a
    refused id logs why, a bindable id that is simply absent is silent.
    """
    for refused in (10**30, ["sample-learner"], {"id": "sample-learner"}, True, float("nan"), None):
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            outcome = store.record_result(refused, "es-01-greetings", "completed", instance_path=instance)
        assert outcome.recorded is False, refused
        assert outcome.reason == "unknown_learner", refused
        assert "Could not record an attempt: the learner id was" in caplog.text, refused
        assert "sample-learner" not in caplog.text, "the shape, never the value"

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        absent = store.record_result("nobody", "es-01-greetings", "completed", instance_path=instance)
    assert absent.reason == "unknown_learner"
    assert caplog.text == "", "a bindable id that is simply absent is a real answer, not a refusal"


def test_save_faceprint_refuses_a_learner_id_nothing_can_look_up(
    instance: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The sibling writer, held to the same guard and the same code.

    Measured before the guard: a list or a dict as the learner id came back
    storage_unavailable from a healthy store.
    """
    for refused in (["sample-learner"], {"id": "sample-learner"}, 10**30, True):
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            outcome = store.save_faceprint(refused, "arcface", [0.5] * 4, instance_path=instance)
        assert outcome.saved is False, refused
        assert outcome.reason == "unknown_learner", refused
        assert "Could not store a faceprint: the learner id was" in caplog.text, refused


# -------------------------------- values that can never match, and a bad instance path


def test_a_learner_who_exists_is_never_silently_reported_absent(
    instance: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The worst shape this defect took, and it was silent.

    bytes and None can never compare equal to a TEXT id however the store is filled --
    a BLOB never equals TEXT, and NULL never equals anything. So these lookups were
    guaranteed non-matches, and all three readers answered as if the learner were
    absent. get_progress was the worst of them: its answer for an unknown learner is a
    populated "fresh start", so a learner with real history was told they had
    completed nothing, which is the one claim the database exists to settle.
    """
    truth = store.get_progress("sample-learner", "es", instance_path=instance)
    assert truth is not None and truth.completed, "the learner really does have history"

    for learner_id in (b"sample-learner", None, float("nan")):
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            assert store.get_profile(learner_id, instance_path=instance) is None, learner_id
            assert store.get_practised_languages(learner_id, instance_path=instance) == (), learner_id
            progress = store.get_progress(learner_id, "es", instance_path=instance)

        # Not a populated object claiming an empty history. Refused outright.
        assert progress is None, learner_id
        assert caplog.text.count("Could not read a learner id") == 3, learner_id
        # One prefix per meaning: this is a caller error, not a failed lookup.
        assert "Could not read learner progress" not in caplog.text, learner_id
        assert "sample-learner" not in caplog.text, "the shape, never the value"


def test_nan_is_null_wearing_a_float(instance: Path) -> None:
    """Named separately because it is the case a type check cannot see.

    sqlite3 binds float("nan") as SQL NULL, so it is the None case above arriving
    through a type the guard does not inspect -- and it slipped past an earlier
    version. float("inf") is NOT in this class: it binds as REAL and takes TEXT
    affinity to "Inf", which is a real lookup, so a guard that refused floats would be
    refusing a value the database can genuinely compare.
    """
    assert store.get_profile(float("nan"), instance_path=instance) is None
    assert store.get_progress(float("nan"), "es", instance_path=instance) is None
    assert store.get_profile(float("inf"), instance_path=instance) is None, "bindable, simply absent"

    # SQLite renders an infinite REAL as "Inf" when TEXT affinity applies, so that is
    # the id it can find. Checked rather than assumed: "inf" does not match.
    _add_learner(instance, "Inf", name="Infinity")
    found = store.get_profile(float("inf"), instance_path=instance)
    assert found is not None and found.display_name == "Infinity", "a real lookup, not a refusal"


def test_a_number_is_still_looked_up_because_sqlite_can_compare_it(instance: Path) -> None:
    """The line the guard must not cross, and this task's edge cases draw it.

    SQLite applies the column's TEXT affinity to a bound number, so 42 finds the
    learner whose id is "42". A guard that simply required a str would refuse those --
    and would refuse the 64-bit boundary ids the edge_cases require to keep working.
    """
    _add_learner(instance, "42", name="Forty Two")

    assert store.get_profile(42, instance_path=instance) is not None
    assert store.get_profile(2**63 - 1, instance_path=instance) is None, "bindable, simply absent"
    assert isinstance(store.get_practised_languages(3.5, instance_path=instance), tuple)


def test_every_entry_point_answers_rather_than_raises_for_a_bad_instance_path(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Twenty-four combinations that used to raise TypeError: seven readers, three values
    each, plus record_result's own three below.

    The fix is one guard in learner_db_path_for_instance, which every entry point
    reaches through connect, raising ValueError rather than TypeError -- ValueError is
    already in every one of their handlers, so each answers with its own contract
    value. Fixing some of them would have left the interface less predictable than it
    was, which is also why the list below is now every reader that takes an
    instance_path rather than the four a past task happened to be looking at.
    """
    # Every reader that takes an instance_path, not the subset a past task happened to
    # touch: get_lesson and get_language_catalog were missing from this list since it
    # was written, and get_lesson_content would have been the third. A sweep that
    # covers four of seven entry points reports on four of seven.
    entry_points = [
        ("get_profile", lambda p: store.get_profile("a", instance_path=p), None),
        ("get_practised_languages", lambda p: store.get_practised_languages("a", instance_path=p), ()),
        ("get_progress", lambda p: store.get_progress("a", "es", instance_path=p), None),
        ("get_lesson", lambda p: store.get_lesson("es-01-greetings", instance_path=p), None),
        ("get_lesson_content", lambda p: store.get_lesson_content("es-01-greetings", instance_path=p), None),
        ("get_language_catalog", lambda p: store.get_language_catalog(instance_path=p), ()),
        ("store_is_available", lambda p: store.store_is_available(p), False),
    ]

    with caplog.at_level(logging.WARNING):
        for name, call, expected in entry_points:
            for bad in (12345, ["x"], b"/tmp"):
                assert call(bad) == expected, (name, bad)

        for bad in (12345, ["x"], b"/tmp"):
            outcome = store.record_result("a", "es-01-greetings", "completed", instance_path=bad)
            assert outcome.recorded is False, bad
            # The exact reason, not membership in RECORD_REASONS -- invalid_outcome,
            # unknown_learner and storage_unavailable all satisfy membership, so it
            # could not tell the path this test is about from any other refusal. The
            # same hollow shape this file already corrected once, at the record_result
            # reason-code test above.
            assert outcome.reason == "storage_unavailable", bad
            assert outcome.reason in learners.RECORD_REASONS, bad

    assert "/tmp" not in caplog.text, "a path can carry a username; log the type"
    assert "must be a path, not int" in caplog.text


def test_a_genuine_programming_error_still_raises(instance: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The other half of choosing ValueError: TypeError keeps meaning what it meant.

    Widening the readers' absorb tuple to include TypeError was the tempting
    alternative and is worse -- the row converters call int() on column values, so
    TypeError is also what a real bug in them raises, and absorbing it would turn that
    bug into a silent absence.
    """
    assert TypeError not in store._READER_ABSORBS

    def explode(_row: object) -> object:
        raise TypeError("a converter bug")

    monkeypatch.setattr(store, "_profile_from_row", explode)

    # End to end through the reader, so a reader that grew its own except TypeError
    # beside the shared tuple would fail here. Asserting on the tuple alone would not
    # notice that, which is the same shape of hollow assertion this file has already
    # had to correct twice.
    with pytest.raises(TypeError):
        store.get_profile("sample-learner", instance_path=instance)


def test_no_entry_point_raises_when_the_home_directory_cannot_be_found(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The default path branch, which no instance_path argument ever reaches.

    Path.home() raises RuntimeError when the home directory cannot be determined -- a
    robot service started without HOME. ensure_learner_database has caught that since
    it was written; the five entry points did not, so the branch taken whenever no
    instance_path is passed ended the conversation turn.
    """
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: (_ for _ in ()).throw(RuntimeError("no home"))))

    with caplog.at_level(logging.WARNING):
        assert store.get_profile("sample-learner") is None
        assert store.get_practised_languages("sample-learner") == ()
        assert store.get_progress("sample-learner", "es") is None
        assert store.get_lesson("es-01-greetings") is None
        assert store.get_lesson_content("es-01-greetings") is None
        assert store.get_language_catalog() == ()
        assert store.store_is_available() is False
        assert store.record_result("sample-learner", "es-01-greetings", "completed").recorded is False

    # Still diagnosable, in the store's own words -- and the RuntimeError's own
    # message, which elsewhere can carry a path, is not what reaches the log.
    assert "home directory could not be determined" in caplog.text, "still diagnosable"
    assert "no home" not in caplog.text, "the raw RuntimeError message reached the log"


def test_a_bad_instance_path_is_a_caller_error_not_a_broken_store(
    instance: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """store_is_available is the module's disambiguator; it must not blur either way.

    A non-path instance_path makes every reader answer absence, so if this said False
    with the same message a broken disk produces, a caller would conclude the store was
    unreadable when nothing is wrong with it.
    """
    with caplog.at_level(logging.WARNING):
        assert store.store_is_available(12345) is False

    assert "The instance path is not a path" in caplog.text
    assert "The learner store is not readable" not in caplog.text
    assert store.store_is_available(instance) is True


def test_no_entry_point_logs_a_fragment_of_a_surrogate_id(instance: Path, caplog: pytest.LogCaptureFixture) -> None:
    """The two sinks _log_safe did not originally cover.

    UnicodeEncodeError names the offending character and its INDEX, so an unencodable
    learner id leaves a fragment of itself in the log. record_result used to reach the
    handler with one; it now refuses it before the bind, as the readers do, and says
    so by shape. store_is_available still reaches its handler through the PATH, which
    is why the helper has to be applied there too rather than only where the rule was
    first written down.
    """
    with caplog.at_level(logging.WARNING):
        store.record_result("alice\ud800bob", "es-01-greetings", "completed", instance_path=instance)
        store.store_is_available(str(instance) + "/\ud800")

    assert "ud800" not in caplog.text and "position" not in caplog.text
    assert "Could not record an attempt: the learner id was not encodable as UTF-8" in caplog.text
    assert "UnicodeEncodeError" in caplog.text, "still diagnosable: the class, not the value"


def _seeded_positions_for(language_code: str) -> list[int]:
    """The positions the seed writes for one language, from both of its sources.

    Lessons come from SEED_LESSONS and from the converted-lesson file, so counting
    either alone answers a different question than the one being asked. The converted
    half is taken per COURSE: the file holds a list of them, each naming its own
    language, so a lesson only counts towards the language its OWN course names.
    Adding every course's positions to whichever language was asked for is how a second
    course would silently make this contiguity check pass while the catalog was broken.
    """
    positions = [row[2] for row in store.SEED_LESSONS if row[1] == language_code]
    positions += [
        int(lesson["position"])
        for course in store._converted_courses()
        if course["language_code"] == language_code
        for lesson in course["lessons"]
    ]
    return sorted(positions)


@pytest.mark.parametrize("language_code", ("de", "it", "pt"))
def test_a_newly_added_language_starts_from_lesson_one(instance: Path, language_code: str) -> None:
    """A learner who has never touched a new language is offered its first lesson.

    Parametrized over all three siblings rather than spot-checking one: the whole
    reason this task exists is that three languages arrived together, and checking
    German alone would say nothing about Italian or Portuguese.

    What it asserts is the PROPERTY rather than the spelling, and that is a change
    worth recording. It used to pin six lessons and the id `<code>-01-greetings`, which
    was true when every language held the same six placeholders and stopped being true
    the moment Italian got lessons converted from a published course: twelve rows, and
    a first lesson that is not a greetings lesson at all. A test that has to be edited
    whenever the catalog grows was testing the catalog, not the reader.
    """
    progress = store.get_progress("sample-learner", language_code, instance_path=instance)
    catalog = _seeded_positions_for(language_code)

    assert progress is not None, "a taught language must never answer the silent None that means 'not taught'"
    assert progress.completed == ()
    assert [lesson.position for lesson in progress.remaining] == catalog, (
        "everything the catalog holds for this language is still to do, in order"
    )
    assert progress.next_lesson is not None
    assert progress.next_lesson.position == 1, "and the one offered is the first"


# ------------------------------------------------------------------- faceprints


def _faceprint_rows(instance_path: Path, learner_id: str) -> list[tuple[object, ...]]:
    """Read the faceprint rows for one learner with raw SQL, bypassing the store."""
    connection = store.connect(instance_path)
    try:
        return [
            tuple(row)
            for row in connection.execute(
                "SELECT learner_id, embedding_model, dimension, vector FROM faceprints WHERE learner_id = ?",
                (learner_id,),
            )
        ]
    finally:
        connection.close()


def test_a_faceprint_round_trips_byte_identically(instance: Path) -> None:
    """Written, read back, same numbers and the same bytes underneath them.

    The values are float32-exact on purpose. 0.1 is not, so asserting on it would test
    the rounding rather than the round trip, and would fail for a reason that has
    nothing to do with storage.
    """
    _add_learner(instance, "someone")
    _add_consent(instance, "someone")
    values = (0.5, -0.25, 1.0)

    outcome = store.save_faceprint("someone", "arcface-r100", values, instance_path=instance)
    assert outcome.saved is True and outcome.reason is None

    read = store.get_faceprint("someone", instance_path=instance)
    assert read is not None
    assert read.vector == values
    assert read.dimension == 3
    assert read.embedding_model == "arcface-r100"
    assert read.learner_id == "someone"

    # And the bytes themselves, not just the floats they decode to.
    rows = _faceprint_rows(instance, "someone")
    assert len(rows) == 1
    assert bytes(rows[0][3]) == struct.pack("<3f", *values)


def test_the_vector_is_little_endian_float32_and_says_so_in_bytes(instance: Path) -> None:
    r"""Pin the byte order where a change to it is visible.

    Storing 1.0 as b"?\x80\x00\x00" instead would be big-endian, and every faceprint
    already on a robot would silently decode as different numbers -- a different person.
    A round-trip test through this module alone cannot see that, because it would
    unpack with whatever it packed with. This one names the bytes.
    """
    _add_learner(instance, "someone")
    _add_consent(instance, "someone")
    store.save_faceprint("someone", "m", [1.0], instance_path=instance)

    stored = bytes(_faceprint_rows(instance, "someone")[0][3])
    assert stored == b"\x00\x00\x80?", "little-endian float32"
    assert stored != b"?\x80\x00\x00", "big-endian would be this"
    assert len(stored) == 1 * store._VECTOR_BYTES_PER_ELEMENT


def test_a_second_faceprint_replaces_the_first(instance: Path) -> None:
    """One row per learner, and the newer one wins.

    Fails as an IntegrityError if the DELETE is dropped from the transaction, and as a
    count of 2 if the table ever stops keying on the learner.
    """
    _add_learner(instance, "someone")
    _add_consent(instance, "someone")
    store.save_faceprint("someone", "old-model", [1.0, 2.0], instance_path=instance)
    store.save_faceprint("someone", "new-model", [3.0, 4.0, 5.0], instance_path=instance)

    rows = _faceprint_rows(instance, "someone")
    assert len(rows) == 1, "a learner has one faceprint"

    read = store.get_faceprint("someone", instance_path=instance)
    assert read is not None
    assert read.embedding_model == "new-model"
    assert read.vector == (3.0, 4.0, 5.0)
    assert read.dimension == 3


def test_a_learner_with_no_faceprint_reads_as_none_and_says_nothing(
    instance: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A genuine absence is not a failure, so it must not log like one."""
    _add_learner(instance, "someone")

    with caplog.at_level(logging.WARNING):
        assert store.get_faceprint("someone", instance_path=instance) is None

    assert caplog.text == "", "nobody enrolled yet is not a problem to report"


def test_an_unknown_learner_cannot_have_a_faceprint(instance: Path) -> None:
    """Reported, and nothing written -- the check that must not appear to succeed."""
    outcome = store.save_faceprint("nobody-here", "m", [1.0], instance_path=instance)

    assert outcome.saved is False
    assert outcome.reason == "unknown_learner"
    assert outcome.faceprint is None
    assert _faceprint_rows(instance, "nobody-here") == []


@pytest.mark.parametrize(
    ("label", "vector"),
    [
        ("empty", []),
        ("a string element", ["x"]),
        ("a None element", [None]),
        ("too large for float32", [1e39]),
        ("booleans", [True, False]),
        ("a bare string", "abc"),
        ("bytes", b"abcd"),
        ("not a sequence", 1.0),
        ("longer than the bound", [0.0] * (store._MAX_VECTOR_DIMENSION + 1)),
    ],
)
def test_a_vector_the_store_cannot_pack_is_refused(instance: Path, label: str, vector: object) -> None:
    """Refused as a caller error, and nothing written.

    `booleans` is the one that regresses silently: struct.pack("<f", True) does not
    raise, it packs 1.0, so without the explicit exclusion a vector of flags would be
    stored as a face and this test would be the only thing that noticed.
    """
    _add_learner(instance, "someone")

    outcome = store.save_faceprint("someone", "m", vector, instance_path=instance)  # type: ignore[arg-type]

    assert outcome.saved is False, label
    assert outcome.reason == "invalid_vector", label
    assert _faceprint_rows(instance, "someone") == [], label


@pytest.mark.parametrize(
    ("label", "model"),
    [
        ("empty", ""),
        ("only whitespace", "   "),
        ("longer than the bound", "m" * (store._MAX_MODEL_NAME + 1)),
        ("a lone surrogate", "\ud800"),  # refused by the character allow-list, not by an encode test
        ("not a string", 7),
        ("None", None),
        # The spellings this column's character allow-list exists to refuse. It is the
        # only caller-supplied TEXT in the table and 128 characters is ample room for a
        # path, so "no column can hold a path to an image" rests on these failing.
        ("an absolute path", "/Users/someone/child.jpg"),
        ("a home-relative path", "~/Pictures/child.png"),
        ("a relative path", "faces/child.jpeg"),
        ("traversal", "../../etc/passwd"),
        ("a windows path", "C:\\Users\\someone\\face.bmp"),
        ("a file URL", "file:///tmp/face.png"),
        ("a space", "model name"),
    ],
)
def test_a_model_name_that_cannot_be_stored_is_refused(instance: Path, label: str, model: object) -> None:
    """invalid_model, never storage_unavailable: the caller sent nonsense.

    The lone surrogate is the one that used to be mislabelled by its sibling writers --
    it fails at bind time, so without this guard it would be reported as a broken robot.
    """
    _add_learner(instance, "someone")

    outcome = store.save_faceprint("someone", model, [1.0], instance_path=instance)  # type: ignore[arg-type]

    assert outcome.saved is False, label
    assert outcome.reason == "invalid_model", label
    assert _faceprint_rows(instance, "someone") == [], label


def test_a_statement_touching_the_faceprint_table_outside_the_scoped_path_raises() -> None:
    """The guard sees the new table, and refuses for the reason it names.

    Asserting the phrase rather than "something raised": a refusal for some other
    reason -- an unparsable statement, say -- would be this test passing over a table
    the guard cannot actually see.
    """
    with pytest.raises(ValueError, match="nothing constrains faceprints to one learner"):
        store._learner_scoped("SELECT learner_id, vector FROM faceprints")


def test_the_faceprint_statements_are_all_registered_as_scoped() -> None:
    """Every statement naming the new table goes through the scoped path."""
    for sql in (store._FACEPRINT_SQL, store._INSERT_FACEPRINT_SQL, store._DELETE_FACEPRINT_SQL):
        assert _personal(sql) is True
        assert sql in store._LEARNER_SCOPED_SQL


def test_deleting_a_faceprint_counts_rather_than_names(instance: Path) -> None:
    """0, 1 and None are three different answers and must stay that way.

    Erasure is a promise to a household, so "I could not tell" (None) must never be
    reported as "it is gone" (1).
    """
    _add_learner(instance, "someone")
    _add_consent(instance, "someone")

    assert store.delete_faceprint("someone", instance_path=instance) == 0, "had none"

    store.save_faceprint("someone", "m", [1.0], instance_path=instance)
    assert store.delete_faceprint("someone", instance_path=instance) == 1, "and now it is gone"
    assert store.get_faceprint("someone", instance_path=instance) is None
    assert _faceprint_rows(instance, "someone") == []

    # A value that could never name anybody removed nothing, which is the truth.
    assert store.delete_faceprint("", instance_path=instance) == 0


@pytest.mark.parametrize(
    ("label", "call", "expected"),
    [
        ("get", lambda path: store.get_faceprint("someone", instance_path=path), None),
        (
            "save",
            lambda path: store.save_faceprint("someone", "m", [1.0], instance_path=path).reason,
            "storage_unavailable",
        ),
        ("delete", lambda path: store.delete_faceprint("someone", instance_path=path), None),
    ],
)
def test_an_unreadable_store_answers_rather_than_raising(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, label: str, call: object, expected: object
) -> None:
    """Every one of the three, because a reader that raises ends the conversation turn."""
    broken = tmp_path / "nonexistent"

    with caplog.at_level(logging.WARNING):
        assert call(broken) == expected, label  # type: ignore[operator]

    assert caplog.text != "", f"{label}: breakage is reported"


def test_no_faceprint_log_line_carries_a_learner_id_a_model_or_a_vector(
    instance: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A faceprint and a learner id are personal data; a log file is not the place.

    Walks every failure path these three functions have, and asserts the id, the model
    name and the packed bytes are all absent from what was logged.
    """
    _add_learner(instance, "secret-person")
    secret_model = "a-very-distinctive-model-name"
    broken = instance / "nonexistent"

    with caplog.at_level(logging.WARNING):
        store.save_faceprint("secret-person", secret_model, ["not-a-number"], instance_path=instance)
        store.save_faceprint("secret-person", "\ud800", [1.0], instance_path=instance)
        store.save_faceprint("secret-person", secret_model, [1.0], instance_path=broken)
        store.get_faceprint("secret-person", instance_path=broken)
        store.delete_faceprint("secret-person", instance_path=broken)
        store.get_faceprint("\ud800", instance_path=instance)
        # The one arm that logs a raw exception rather than _log_safe(exc). It is
        # reached only by the race below, so without this call the sink the no-PII
        # claim is weakest at is the one sink this test never visits.
        _, raced_in_walk = _save_into_a_deleted_learner(instance, "secret-person", secret_model)

    # Asserted, not assumed: if the harness hook ever stops firing, this walk would
    # quietly stop visiting that sink while still passing. The flag is what stops the
    # coverage being real today and unpinned tomorrow.
    assert raced_in_walk is True, "the walk did not reach the database-refusal log sink"
    assert caplog.text != "", "these are failures and they were reported"
    assert "secret-person" not in caplog.text
    assert secret_model not in caplog.text
    assert "\ud800" not in caplog.text
    assert repr(struct.pack("<f", 1.0)) not in caplog.text


def test_the_faceprint_bounds_in_python_and_in_the_schema_say_the_same_thing() -> None:
    """Two copies of a bound is how a guard and its database drift apart.

    store.py refuses a vector longer than _MAX_VECTOR_DIMENSION; the schema refuses a
    row whose dimension exceeds its own bound. If those two numbers ever disagree, one
    of them is unreachable -- either a vector the store accepts and the database
    refuses, or a CHECK nothing can ever trip.
    """
    schema = (Path(store.__file__).resolve().parent / "schema.sql").read_text(encoding="utf-8")

    dimension = re.search(r"dimension BETWEEN 1 AND (\d+)", schema)
    model = re.search(r"length\(trim\(embedding_model\)\) BETWEEN 1 AND (\d+)", schema)
    width = re.search(r"length\(vector\) = dimension \* (\d+)", schema)

    assert dimension is not None and model is not None and width is not None, "the CHECKs moved"
    assert int(dimension.group(1)) == store._MAX_VECTOR_DIMENSION
    assert int(model.group(1)) == store._MAX_MODEL_NAME
    assert int(width.group(1)) == store._VECTOR_BYTES_PER_ELEMENT

    # And the character allow-list, which is the half that carries the no-paths promise.
    # The GLOB names the permitted characters; the Python set must name the same ones,
    # or one of the two lets through a spelling the other refuses.
    glob = re.search(r"embedding_model NOT GLOB '\*\[\^([^\]]+)\]\*'", schema)
    assert glob is not None, "the model-name allow-list moved"

    # The clause that makes that allow-list mean anything. Without it SQLite's
    # NUL-terminated length()/trim()/GLOB let a 5030-character path through as a
    # 3-character name -- measured, and pinned by its own test in the schema suite.
    assert "length(CAST(embedding_model AS BLOB)) = length(embedding_model)" in schema, (
        "the byte-length clause is gone; the character allow-list is NUL-steppable without it"
    )
    assert glob.group(1) == "A-Za-z0-9._-"
    assert store._MODEL_NAME_CHARACTERS == frozenset(string.ascii_letters + string.digits + "._-"), (
        "the two copies of the permitted set must say the same thing"
    )


class _RacingConnection:
    """A connection that deletes the learner the moment its existence has been checked.

    This is the "learner row deleted concurrently" edge case, made deterministic. A
    real race needs two connections and a scheduler that cooperates; this reproduces
    the same interleaving exactly -- existence answered yes, row gone, insert attempted
    -- by deleting on the same connection, inside the same transaction the insert will
    run in, so the foreign key sees it immediately.
    """

    def __init__(self, real: sqlite3.Connection, learner_id: str) -> None:
        self._real = real
        self._learner_id = learner_id
        self._raced = False

    def execute(self, sql: str, parameters: object = ()) -> sqlite3.Cursor:
        cursor = self._real.execute(sql, parameters)  # type: ignore[arg-type]
        if sql == store._LEARNER_EXISTS_SQL and not self._raced:
            self._raced = True
            self._real.execute("DELETE FROM learners WHERE id = ?", (self._learner_id,))
        return cursor

    def __enter__(self) -> sqlite3.Connection:
        return self._real.__enter__()

    def __exit__(self, *exc_info: object) -> object:
        return self._real.__exit__(*exc_info)  # type: ignore[arg-type]

    def __getattr__(self, name: str) -> object:
        return getattr(self._real, name)


def _save_into_a_deleted_learner(instance_path: Path, learner_id: str, model: str = "m") -> tuple[object, bool]:
    """Run save_faceprint against a learner deleted between the check and the insert.

    Returns the outcome and whether the interleaving actually happened, because a
    harness whose hook silently never fired would make the test using it assert the
    right thing about the wrong run.
    """
    real_connect = store.connect
    raced: list[_RacingConnection] = []

    def racing_connect(path: object = None) -> object:
        connection = _RacingConnection(real_connect(path), learner_id)  # type: ignore[arg-type]
        raced.append(connection)
        return connection

    store.connect = racing_connect  # type: ignore[assignment]
    try:
        outcome = store.save_faceprint(learner_id, model, [1.0], instance_path=instance_path)
    finally:
        store.connect = real_connect  # type: ignore[assignment]
    return outcome, any(connection._raced for connection in raced)


def test_a_learner_deleted_between_the_check_and_the_insert_is_refused_not_raised(instance: Path) -> None:
    """The edge case the testing strategy names, and the arm that had no test.

    save_faceprint checks the learner exists and then inserts. Between those two the
    row can go, and the insert must refuse rather than raise.

    THE CODE THIS RACE TAKES CHANGED when the consent gate arrived, and this test
    records the new answer rather than the old one. The insert now selects its row
    FROM the consents table, and deleting a learner cascades their consent away with
    them -- so by the time the insert runs there is no consent row to select, it
    matches nothing, and the refusal is `no_consent`. The foreign key is never
    consulted, because nothing is offered to it. Measured here, not reasoned about:
    before the gate this same harness produced `rejected_by_database`.

    The important half is unchanged and is what this test is really for: it does not
    RAISE. This is called from a tool layer driven by a model, and an exception here
    ends the conversation turn. Nothing is written either way.
    """
    _add_learner(instance, "racy")
    _add_consent(instance, "racy")

    outcome, raced = _save_into_a_deleted_learner(instance, "racy")

    assert raced is True, "the harness never reached the interleaving it exists to create"
    assert outcome.saved is False  # type: ignore[union-attr]
    assert outcome.reason == "no_consent", (  # type: ignore[union-attr]
        "the learner's consent cascaded away with them, so the insert matched nothing"
    )
    assert outcome.faceprint is None  # type: ignore[union-attr]
    assert _faceprint_rows(instance, "racy") == [], "and nothing was written"


def test_the_refused_race_leaves_the_database_exactly_as_it_was(instance: Path) -> None:
    """The other half, and a property worth having on purpose rather than by luck.

    The insert and the harness's delete run in one transaction, so when the foreign key
    refuses the insert the rollback takes the delete with it: the learner is still
    there afterwards and has no faceprint. A refused save leaves no damage.

    Written this way after the first attempt asserted the learner was GONE and failed
    -- the deletion really happens, and the rollback really undoes it. Asserting the
    side effect rather than the interleaving is what made that test wrong; `raced` is
    what pins the interleaving now.
    """
    _add_learner(instance, "racy")

    outcome, raced = _save_into_a_deleted_learner(instance, "racy")
    assert raced is True and outcome.saved is False  # type: ignore[union-attr]

    connection = store.connect(instance)
    try:
        assert connection.execute("SELECT 1 FROM learners WHERE id = ?", ("racy",)).fetchone() is not None
    finally:
        connection.close()
    assert _faceprint_rows(instance, "racy") == []


# What a logger call in store.py is allowed to interpolate. An allow-list, not a list of
# forbidden names: a deny-list of path-ish words has been wrong here four times, and it
# only ever refuses the spellings somebody thought of. Anything not named below fails
# this test, which is the point -- a new log line has to be looked at once.
#
#   _log_safe(...)        the module's own redacting renderer
#   type(exc).__name__    an error class, which is a shape and not a value
_PERMITTED_LOG_NAMES = frozenset(
    {
        "refusal",  # a reason code from the published vocabulary
        "schema_applied",  # bool
        "seeded",  # bool
        "checkpointed",  # int, WAL frames copied
        "log_frames",  # int, WAL frames pending
        "busy",  # int, the checkpoint's busy flag -- same unpacking as its two siblings
        "SEEDED_LEARNERS_KEY",  # a module constant naming a settings key
        "LEARNER_DB_FILENAME",  # the fixed filename, which names no person and no directory
        "suffix",  # "" / "-wal" / "-shm" / "-journal", from a literal tuple in the loop
        "language_count",  # int, len() of the catalog tuple
        "with_material_count",  # int, len() of a filtered list
    }
)


def _exception_bound_names(tree: ast.Module) -> frozenset[str]:
    """Names that can only be an exception, because an `except ... as` is what binds them."""
    bound = {node.name for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler) and node.name}
    # A name that is ALSO bound some other way is not one of these -- `except OSError as
    # x` elsewhere does not make `x = str(path)` here safe. A PARAMETER of that name is
    # the same hazard, unless it is annotated as an exception, which _log_safe's own
    # `exc: BaseException` is: the annotation is what makes the claim checkable.
    exception_annotations = {"BaseException", "Exception"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id in bound:
            bound.discard(node.id)
        if isinstance(node, ast.arg) and node.arg in bound:
            annotation = node.annotation
            annotated_as_exception = isinstance(annotation, ast.Name) and (
                annotation.id in exception_annotations or annotation.id.endswith("Error")
            )
            if not annotated_as_exception:
                bound.discard(node.arg)
    return frozenset(bound)


def _log_argument_shape(node: ast.expr, exception_names: frozenset[str] = frozenset()) -> str | None:
    """Name the permitted shape this argument has, or None if it has none."""
    if isinstance(node, ast.Constant):
        return "constant"
    if isinstance(node, ast.Name) and node.id in _PERMITTED_LOG_NAMES:
        return f"name:{node.id}"
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"_log_safe", "log_safe"}:
        # _log_safe renders an EXCEPTION safely. It renders anything else exactly as it
        # was handed over, so `_log_safe(path)` would launder a path straight through the
        # guard -- which is how the OSError leak stayed invisible to an earlier version
        # of this scan. Its argument must be a name bound by an `except ... as` clause.
        if len(node.args) == 1 and isinstance(node.args[0], ast.Name) and node.args[0].id in exception_names:
            return "_log_safe"
        return None
    # type(exc).__name__ -- an Attribute whose value is a call to type()
    if isinstance(node, ast.Attribute) and node.attr == "__name__":
        inner = node.value
        if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name) and inner.func.id == "type":
            return "type(...).__name__"
    return None


# The ONLY methods a store log line may call. An allow-list, and deliberately not the
# full set logging offers: `warn` and `fatal` are stdlib aliases that write exactly like
# `warning` and `critical`, and an earlier version of this guard named the writers it
# knew about and so waved both of them straight through. Naming what is permitted closes
# that family at once -- including `handle`, `_log`, and whatever the next alias is.
_PERMITTED_LOG_METHODS = frozenset({"debug", "info", "warning", "error", "exception", "critical"})


def _parents(tree: ast.Module) -> dict[int, ast.AST]:
    """Map each node to its parent, so a Name can be asked what it is being used AS."""
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    return parents


def _module_logger_binding(tree: ast.Module) -> ast.Assign:
    """Return the single module-level ``logger = logging.getLogger(...)`` assignment."""
    bindings = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if [t.id for t in node.targets if isinstance(t, ast.Name)] != ["logger"]:
            continue
        if isinstance(node.value, ast.Call):
            bindings.append(node)
    assert len(bindings) == 1, f"expected exactly one module-level logger binding, found {len(bindings)}"
    return bindings[0]


# Every module that logs beside a household's data, and the floor each scan must clear.
# A list rather than store.py alone, because the rule was moved into logging_safety and
# given a second consumer while the ENFORCEMENT stayed pointed at one file -- which is
# the same asymmetry that let memory.py log a home directory for as long as it did.
_GUARDED_MODULES = [(store, 25), (memory, 2)]


@pytest.mark.parametrize(("module", "minimum_calls"), _GUARDED_MODULES, ids=lambda v: getattr(v, "__name__", v))
def test_no_log_line_in_the_store_can_carry_a_path_or_a_learner(module: object, minimum_calls: int) -> None:
    """The rule the module states, held by the module rather than by whoever edits it.

    CLAUDE.md's most expensive lesson is that a rule obeyed in one function is broken in
    the one next to it: the no-PII rule was honoured in the store and broken in the loop
    above it (D3), and then again inside a single function here -- one branch scrubbed of
    the database path while its sibling four lines below still logged it in full.

    Grep found that sibling once. This is what stops the next one, and EVERY part of it
    is an allow-list, because the first two versions were not. Version one permitted an
    allow-list of argument shapes but recognised only calls spelled ``logger.<method>``,
    so ``log = logger`` made a line invisible to the scan rather than failing it. Version
    two fixed the receiver and still enumerated the METHODS that write, so ``logger.warn``
    and ``logger.fatal`` -- stdlib aliases a future editor reaches for without thinking --
    sailed through. A deny-list is only ever as complete as the last person to read it,
    and that is now three demonstrations of it in this one guard.

    So the permitted shape is stated positively and nothing else is allowed to exist:

    * the module builds exactly one logger, at module level;
    * the name ``logging`` appears nowhere but inside that one binding, which is what
      stops ``logging.warning(path)`` and ``from logging import warning``;
    * the name ``logger`` appears nowhere except as the direct receiver of a call to a
      method in _PERMITTED_LOG_METHODS, which is what stops aliasing, ``getattr``,
      containers, ``handle`` and ``_log``;
    * and each argument has a shape named in _PERMITTED_LOG_NAMES.

    Anything else fails this test. A log line that needs a new shape adds it above,
    deliberately, which is the whole point.
    """
    source = Path(module.__file__).resolve().read_text(encoding="utf-8")
    tree = ast.parse(source)
    parents = _parents(tree)

    exception_names = _exception_bound_names(tree)
    binding = _module_logger_binding(tree)
    permitted_logger_nodes = {id(target) for target in binding.targets}
    permitted_logging_nodes = {id(node) for node in ast.walk(binding.value)}

    scanned = 0
    unpermitted: list[str] = []

    # `import logging` is the only way the module may reach the logging package. A
    # `from logging import ...` binds a writer under a bare name with no receiver to
    # check, so it is refused outright rather than enumerated.
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "logging":
            unpermitted.append(f"line {node.lineno}: `from logging import ...` binds a writer with no receiver")

    for node in ast.walk(tree):
        if not isinstance(node, ast.Name):
            continue

        if node.id == "logging" and id(node) not in permitted_logging_nodes:
            unpermitted.append(f"line {node.lineno}: `logging` is used outside the single logger binding")
            continue

        if node.id != "logger" or id(node) in permitted_logger_nodes:
            continue

        # From here: a use of `logger`. It must be the receiver of a permitted method
        # call, and nothing else -- not assigned, not passed, not subscripted, not
        # handed to getattr.
        attribute = parents.get(id(node))
        if not (isinstance(attribute, ast.Attribute) and attribute.value is node):
            unpermitted.append(f"line {node.lineno}: `logger` is used as something other than a call receiver")
            continue
        call = parents.get(id(attribute))
        if not (isinstance(call, ast.Call) and call.func is attribute):
            unpermitted.append(f"line {node.lineno}: `logger.{attribute.attr}` is referenced without being called")
            continue
        if attribute.attr not in _PERMITTED_LOG_METHODS:
            unpermitted.append(f"line {node.lineno}: `logger.{attribute.attr}()` is not a permitted log method")
            continue

        scanned += 1
        # Keywords as well as positional arguments: extra= and exc_info= are how a value
        # reaches a handler without ever appearing in the format string.
        for argument in call.args:
            if _log_argument_shape(argument, exception_names) is None:
                unpermitted.append(f"line {call.lineno}: {ast.dump(argument)[:120]}")
        for keyword in call.keywords:
            unpermitted.append(f"line {call.lineno}: keyword {keyword.arg}= is not permitted on a store log line")

    assert scanned >= minimum_calls, f"only {scanned} logger calls found, so this scan proves nothing"
    assert unpermitted == [], unpermitted


# The module helpers that produce a value safe to log: each returns a reason built from
# type(value).__name__, or a bool. Named positively, because "anything but a path" is
# the deny-list shape that has now been wrong five times in this repository.
_SAFE_PRODUCERS = frozenset(
    {
        "_cannot_be_a_catalog_code",
        "_cannot_be_a_path",
        "_cannot_be_an_embedding_model",
        "_cannot_be_a_display_name",
        "_cannot_be_a_consent_statement",
        "_cannot_be_a_learner_id",
        "_cannot_name_a_learner",
        "_cannot_name_a_lesson",
        "_unconstrained_personal_relation",
        "_insert_is_attributed",
        "_apply_schema",
        "_seed",
    }
)


def test_the_generic_names_the_log_guard_permits_are_bound_to_what_they_claim() -> None:
    """An allow-list of NAMES is only as good as what those names are bound to.

    _PERMITTED_LOG_NAMES waves seven lowercase identifiers through on the strength of a
    comment beside each. The comments were true and nothing checked them, so
    ``refusal = str(path)`` followed by logging `refusal` passed green -- and `refusal`
    is bound at fourteen sites and logged at twelve, so it is the one that matters.
    Pinning only `suffix`, the name that prompted the finding, would be the
    fix-the-member-not-the-class mistake CLAUDE.md names as this board's most repeated.

    So every generic name is pinned, and the pin is an allow-list of PRODUCERS: each
    binding must be an `except ... as` clause, a `for` over string literals, a tuple
    unpack of int()s, or an assignment whose calls are all to _SAFE_PRODUCERS.

    SCREAMING_CASE entries are not covered here: they are module constants, checked by
    being constants.
    """
    tree = ast.parse(Path(store.__file__).resolve().read_text(encoding="utf-8"))
    generic = {name for name in _PERMITTED_LOG_NAMES if not name.isupper()}
    assert generic, "no generic names to check, so this test proves nothing"

    # Every producer named above must actually exist, or the allow-list is decoration.
    defined = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert _SAFE_PRODUCERS <= defined, sorted(_SAFE_PRODUCERS - defined)

    seen: dict[str, int] = dict.fromkeys(generic, 0)
    wrong: list[str] = []
    accounted: set[int] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.name in generic:
            seen[node.name] += 1
        elif isinstance(node, ast.For) and isinstance(node.target, ast.Name) and node.target.id in generic:
            seen[node.target.id] += 1
            accounted.add(id(node.target))
            literals = isinstance(node.iter, ast.Tuple) and all(
                isinstance(element, ast.Constant) and isinstance(element.value, str) for element in node.iter.elts
            )
            if not literals:
                wrong.append(f"line {node.lineno}: `{node.target.id}` iterates something other than string literals")
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id in generic:
            # A dataclass field declaration. It binds no value here, and the annotation
            # is the claim: bool and int cannot carry a name or a path. `path: Path`
            # would be refused by the same rule, which is the point.
            seen[node.target.id] += 1
            accounted.add(id(node.target))
            if not (isinstance(node.annotation, ast.Name) and node.annotation.id in {"bool", "int"}):
                wrong.append(f"line {node.lineno}: `{node.target.id}` is declared as something that can carry a value")
            # An annotated ASSIGNMENT is not a field declaration, and this branch was
            # checking only the annotation -- so `refusal: int = str(path)` would have
            # passed on the strength of the word `int`. A declaration has no value;
            # anything with one has to justify it the way a plain assignment does.
            # A field DEFAULT is a constant and is fine (`seeded: bool = False`). A
            # computed value is not a declaration at all, and this branch used to wave
            # it through on the strength of the annotation -- so `refusal: int =
            # str(path)` would have passed because the word `int` appeared.
            if node.value is not None and not isinstance(node.value, ast.Constant):
                wrong.append(
                    f"line {node.lineno}: `{node.target.id}` is an annotated assignment with a computed "
                    "value, so the annotation is a claim about the name rather than about what it is bound to"
                )
        elif isinstance(node, ast.Assign):
            targets = [t for t in ast.walk(node.targets[0]) if isinstance(t, ast.Name) and t.id in generic]
            if not targets:
                continue
            for target in targets:
                seen[target.id] += 1
                accounted.add(id(target))
            calls = [c for c in ast.walk(node.value) if isinstance(c, ast.Call)]
            called = {c.func.id for c in calls if isinstance(c.func, ast.Name)}
            called |= {c.func.attr for c in calls if isinstance(c.func, ast.Attribute)}
            # A tuple unpack of int()s, or an assignment built only from safe producers.
            # `len` joins them because CPython REQUIRES __len__ to return an int, so
            # len() of anything is a count and cannot carry a name or a path out of its
            # argument -- which is the property this list is about.
            #
            # `sum` was added here too, on the reasoning that summing 1s gives a count.
            # That reasoning is WRONG and review had the bypass: `sum([], start)`
            # returns `start` unchanged, because an empty iterable never touches the
            # addition. `with_material_count = sum([], instance_path)` passed both
            # guards and wrote a household path into the log line. Widening a security
            # allow-list on an argument that sounds right is how a guard stops being
            # one; the entry is gone and `len` is the only addition.
            if called and called <= (_SAFE_PRODUCERS | {"int", "tuple", "len"}):
                continue
            if not calls and all(isinstance(n, (ast.Constant, ast.Name, ast.Tuple)) for n in ast.walk(node.value)):
                continue
            wrong.append(
                f"line {node.lineno}: `{', '.join(sorted(t.id for t in targets))}` is built from "
                f"{sorted(called) or type(node.value).__name__}, not from a safe producer"
            )

    # Anything binding one of these names by a route not accounted for above.
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id in generic:
            if id(node) not in accounted:
                wrong.append(f"line {node.lineno}: `{node.id}` is bound by an unrecognised route")
        if isinstance(node, ast.arg) and node.arg in generic:
            wrong.append(f"line {node.lineno}: `{node.arg}` is a parameter, so its value comes from a caller")

    unbound = sorted(name for name, count in seen.items() if count == 0)
    assert not unbound, f"permitted but never bound, so the comment is unchecked: {unbound}"
    assert wrong == [], wrong


def test_the_log_guard_refuses_an_annotated_assignment_that_hides_a_value() -> None:
    """The hole the AnnAssign branch had, pinned so it cannot reopen.

    That branch exists for dataclass FIELD declarations, where the annotation is the
    whole claim: `seeded: bool = False` cannot carry a name or a path. It checked only
    the annotation, so an annotated ASSIGNMENT with a computed value -- `refusal: int
    = str(path)` -- passed on the strength of the word `int` while binding a path to a
    name the log guard waves through.

    Constants stay permitted, because a field default is one. Anything computed has to
    justify itself the way a plain assignment does.
    """
    checker = inspect.getsource(test_the_generic_names_the_log_guard_permits_are_bound_to_what_they_claim)

    def verdicts(source: str) -> list[str]:
        """Run the branch's rule over a snippet and return what it objected to."""
        wrong: list[str] = []
        generic = {"refusal", "seeded"}
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id in generic:
                if not (isinstance(node.annotation, ast.Name) and node.annotation.id in {"bool", "int"}):
                    wrong.append(f"{node.target.id}: annotation")
                if node.value is not None and not isinstance(node.value, ast.Constant):
                    wrong.append(f"{node.target.id}: computed value")
        return wrong

    assert "not isinstance(node.value, ast.Constant)" in checker, (
        "the AnnAssign value check is gone, so an annotated assignment can hide a path again"
    )
    assert verdicts("refusal: int = str(path)") == ["refusal: computed value"]
    assert verdicts("seeded: bool = False") == [], "a constant field default is still fine"
    assert verdicts("seeded: bool") == [], "a bare field declaration is still fine"


# What the consent-scope rebuild is allowed to be, as an allow-list of whole
# statements rather than a list of things it must not do. A rebuild legitimately
# drops and renames, so the schema guard's "creates tables and nothing else" cannot
# back it -- and a deny-list of dangerous verbs would be exactly the shape this
# repository has been burned by four times.
_PERMITTED_MIGRATION_SHAPES = (
    # The transaction that makes the rebuild all-or-nothing. Without it a crash
    # between the DROP and the RENAME destroys every consent record and the database
    # still comes up ready -- reproduced by a review, which is why these two are on
    # the list rather than merely tolerated.
    "BEGIN",
    "COMMIT",
    # Recovers a database wedged by an interrupted run of the transactionless version.
    "DROP TABLE IF EXISTS CONSENTS_MIGRATED",
    "CREATE TABLE CONSENTS_MIGRATED",
    "INSERT INTO CONSENTS_MIGRATED",
    "DROP TABLE CONSENTS",
    "ALTER TABLE CONSENTS_MIGRATED RENAME TO CONSENTS",
    "CREATE INDEX IF NOT EXISTS IDX_CONSENTS_LEARNER_SCOPE ON CONSENTS",
)


def _migration_statements(sql: str) -> list[str]:
    """The migration's statements, comments stripped, whitespace flattened."""
    without_comments = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
    return [" ".join(statement.split()) for statement in without_comments.split(";") if statement.strip()]


def _migration_statements_outside_the_allow_list(sql: str) -> list[str]:
    """Statements the consent rebuild is not permitted to contain."""
    return [
        statement
        for statement in _migration_statements(sql)
        if not any(statement.upper().startswith(shape) for shape in _PERMITTED_MIGRATION_SHAPES)
    ]


def test_the_consent_migration_rebuilds_consents_and_nothing_else() -> None:
    """What makes the second exemption safe, rather than merely narrow.

    The migration reads its text from a file at runtime, so source reading cannot show
    what it executes -- the same hole _schema_sql has, and it is backed the same way:
    here, instead.

    An allow-list of statement heads, because the thing to prove is not "it avoids the
    dangerous verbs" but "it is a rebuild of consents and nothing more". A statement
    touching learners, faceprints or lesson_results, or a CREATE VIEW laundering a
    table name, is refused by not being on the list rather than by being spotted.
    """
    sql = (Path(store.__file__).resolve().parent / "consent_scopes.v5.sql").read_text(encoding="utf-8")

    statements = _migration_statements(sql)
    assert len(statements) == 8, f"the rebuild should be eight statements, got {len(statements)}: {statements}"
    assert _migration_statements_outside_the_allow_list(sql) == []
    # ALL-OR-NOTHING, pinned by position rather than by presence: a BEGIN that is not
    # first, or a COMMIT that is not last, leaves part of the rebuild outside the
    # transaction and is exactly the failure this is here to stop.
    # IMMEDIATE, pinned exactly: a deferred BEGIN let a second process starting at the
    # same moment fail the upgrade with "database is locked" -- see
    # test_concurrent_upgrades_of_a_version_4_database_all_succeed.
    assert statements[0].upper() == "BEGIN IMMEDIATE", "the rebuild must take the write lock first"
    assert statements[-1].upper() == "COMMIT", "the rebuild must commit last"
    # And it names no other personal table, so the copy cannot reach anybody else's
    # data. Checked over the STATEMENTS rather than the file, so the prose explaining
    # the rebuild is free to mention the tables it does not touch.
    body = " ".join(statements).upper()
    assert body.count("LEARNERS") == 1, "the rebuild should name learners once, in the foreign key"
    assert "REFERENCES LEARNERS(ID) ON DELETE CASCADE" in body, "the cascade must survive the rebuild"
    for table in ("FACEPRINTS", "LESSON_RESULTS"):
        assert table not in body, f"the consent rebuild names {table}"
    assert "?" not in sql, "a migration takes no parameters"


@pytest.mark.parametrize(
    ("shape", "statement"),
    [
        ("a view", "CREATE VIEW all_consents AS SELECT * FROM consents;"),
        ("a read of another table", "SELECT * FROM lesson_results;"),
        ("a drop of the wrong table", "DROP TABLE learners;"),
        ("a rename onto another table", "ALTER TABLE consents_migrated RENAME TO learners;"),
    ],
)
def test_the_consent_migration_guard_rejects_what_it_claims_to(shape: str, statement: str) -> None:
    """The backing guard, shown failing before it is trusted."""
    sql = (Path(store.__file__).resolve().parent / "consent_scopes.v5.sql").read_text(encoding="utf-8")

    assert _migration_statements_outside_the_allow_list(sql + "\n" + statement), f"{shape} was accepted"


def test_a_completion_does_not_win_a_tie_against_a_later_row_at_the_same_instant(instance: Path) -> None:
    """The tie-break in latest-wins, which was load-bearing and untested.

    "Finished" means the last result for a lesson is a completion. When two results
    share a recorded_at -- which seed data and tests routinely produce, since they use
    fixed stamps -- "last" has to be decided by something, and it is the row id, the
    same way _ATTEMPTS_SQL decides it. Without that, the completion matched as well and
    the lesson counted as finished: the learner is skipped past material, which is the
    D38 harm exactly.

    Measured rather than assumed: removing ", r2.id DESC" from the two statements left
    the whole suite green before this case existed.
    """
    lesson = "it-fast-01-what-time-is-it"
    same_instant = _AFTER_THE_SEEDED_HISTORY + 5_000
    _add_result(instance, "sample-learner", lesson, "completed", when=same_instant)
    _add_result(instance, "sample-learner", lesson, "partial", when=same_instant)

    progress = store.get_progress("sample-learner", "it", instance_path=instance)

    assert progress is not None
    assert lesson not in {entry.id for entry in progress.completed}, (
        "a completion won a same-millisecond tie against the partial written after it"
    )
    assert progress.next_lesson is not None and progress.next_lesson.id == lesson, (
        "the learner was moved past a lesson whose last word was not a completion"
    )
