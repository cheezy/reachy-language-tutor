"""Tests for the learner store's query interface: profiles, progress, and results.

Schema creation, seeding and their failure modes live in test_learner_schema.py.

These tests use direct SQL for setup. That is fine: the "all SQL lives in the store"
verification is scoped to the application package, not to the test suite.
"""

import re
import ast
import inspect
import sqlite3
import logging
import dataclasses
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest

import reachy_language_tutor.learners as learners
from reachy_language_tutor.learners import store
from reachy_language_tutor.learners.models import Lesson, LearnerProfile, LessonAttempt


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


def _add_result(instance_path: Path, learner_id: str, lesson_id: str, outcome: str, when: int = 1) -> None:
    connection = store.connect(instance_path)
    try:
        connection.execute(
            "INSERT INTO lesson_results (learner_id, lesson_id, outcome, score, recorded_at) "
            "VALUES (?, ?, ?, ?, ?)",
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

    assert profile == LearnerProfile(
        id="sample-learner", display_name="Sample Learner", created_at=1767225600000
    )


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
        "es-03-numbers",
        "es-04-ordering-food",
        "es-05-directions",
        "es-06-daily-routine",
    )
    assert progress.next_lesson is not None
    assert progress.next_lesson.id == "es-03-numbers"
    assert len(progress.attempts) == 3
    assert progress.attempts[0].lesson_id == "es-03-numbers", "newest attempt first"
    assert progress.attempts[0].outcome == "partial"


def test_get_progress_untouched_language_starts_at_lesson_one(instance: Path) -> None:
    """A language that is taught but never practised is a fresh start, not unknown."""
    progress = store.get_progress("sample-learner", "fr", instance_path=instance)

    assert progress is not None
    assert progress.completed == ()
    assert progress.attempts == ()
    assert len(progress.remaining) == 6
    assert progress.next_lesson is not None
    assert progress.next_lesson.id == "fr-01-greetings"


@pytest.mark.parametrize("language_code", ["de", "ES", ""])
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
    assert progress.next_lesson.id == "es-01-greetings"


def test_get_progress_never_returns_another_learners_rows(instance: Path) -> None:
    """The scoping boundary: one household member's history must not reach another."""
    _add_learner(instance, "other-learner", "Other Learner")
    for lesson_id in (
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
    assert mine.next_lesson.id == "es-03-numbers", "another learner's completions must not advance me"
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
        connection.execute("INSERT INTO languages (code, name) VALUES (?, ?)", ("de", "German"))
        connection.commit()
    finally:
        connection.close()

    progress = store.get_progress("sample-learner", "de", instance_path=instance)

    assert progress is not None, "the language is taught, so this is not None"
    assert progress.remaining == ()
    assert progress.completed == ()
    assert progress.next_lesson is None


def test_get_progress_finished_language_has_no_next_lesson(instance: Path) -> None:
    """Finishing a language is distinguishable from never starting it."""
    for lesson_id in ("es-03-numbers", "es-04-ordering-food", "es-05-directions", "es-06-daily-routine"):
        _add_result(instance, "sample-learner", lesson_id, "completed")

    progress = store.get_progress("sample-learner", "es", instance_path=instance)

    assert progress is not None
    assert len(progress.completed) == 6
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
            row = connection.execute(store.NEXT_LESSON_SQL, (language_code, learner_id)).fetchone()
            expected = None if row is None else str(row["id"])
            actual = None if progress.next_lesson is None else progress.next_lesson.id
            assert actual == expected, f"drift for {learner_id}/{language_code}"
    finally:
        connection.close()


# ----------------------------------------------------------------- record_result


def test_record_result_appends_an_attempt(instance: Path) -> None:
    """Recording a completion advances the learner."""
    outcome = store.record_result(
        "sample-learner", "es-03-numbers", "completed", score=88, instance_path=instance
    )

    assert outcome.recorded is True
    assert outcome.reason is None
    assert outcome.attempt is not None
    assert outcome.attempt.score == 88

    progress = store.get_progress("sample-learner", "es", instance_path=instance)
    assert progress is not None
    assert progress.next_lesson is not None
    assert progress.next_lesson.id == "es-04-ordering-food"
    assert len(progress.attempts) == 4


def test_record_result_is_append_only(instance: Path) -> None:
    """A second attempt at the same lesson is a second row, not an update."""
    before = _count_results(instance)
    for _ in range(2):
        assert store.record_result(
            "sample-learner", "es-03-numbers", "partial", instance_path=instance
        ).recorded is True

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
    outcome = store.record_result(
        "sample-learner", "es-01-greetings", outcome_value, instance_path=instance
    )

    assert outcome.recorded is False
    assert outcome.reason == "invalid_outcome"
    assert _count_results(instance) == before


@pytest.mark.parametrize(("score", "expected"), [(-1, False), (101, False), (0, True), (100, True), (None, True)])
def test_record_result_validates_score_range(instance: Path, score: int | None, expected: bool) -> None:
    """Scores outside 0-100 are refused before any write."""
    outcome = store.record_result(
        "sample-learner", "es-01-greetings", "partial", score=score, instance_path=instance
    )

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
    assert store.record_result(
        "other-learner", "es-01-greetings", "completed", instance_path=instance
    ).recorded is True

    mine = store.get_progress("sample-learner", "es", instance_path=instance)
    assert mine is not None
    assert len(mine.attempts) == 3


def test_learner_supplied_text_cannot_inject_sql(instance: Path) -> None:
    """Parameterised statements only: hostile text is a value, never syntax."""
    before = _count_results(instance)
    hostile_lesson = "es-01-greetings'; DROP TABLE lesson_results;--"
    hostile_learner = "sample-learner' OR '1'='1"

    assert store.record_result(
        "sample-learner", hostile_lesson, "completed", instance_path=instance
    ).reason == "unknown_lesson"
    assert store.record_result(
        hostile_learner, "es-01-greetings", "completed", instance_path=instance
    ).reason == "unknown_learner"
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
    outcome = store.record_result(
        "sample-learner", "es-01-greetings", "partial", score=score, instance_path=instance
    )

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
            "INSERT INTO lesson_results (learner_id, lesson_id, outcome, score, recorded_at) "
            "VALUES (?, ?, ?, ?, ?)",
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
def test_an_integer_too_large_for_the_column_is_refused_rather_than_raised(
    instance: Path, recorded_at: int
) -> None:
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
    assert store.record_result(
        "sample-learner", "es-01-greetings", "completed", recorded_at=1_700_000_000_000, instance_path=instance
    ).recorded is True

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


def test_every_reason_the_store_can_return_is_in_the_published_vocabulary() -> None:
    """A code a caller cannot anticipate is not an interface.

    RECORD_REASONS is what a caller switches on. A reason returned from the module but
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
        if name != "RecordResultOutcome":
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

    assert returned, "the scan found no reason codes at all, so it is proving nothing"
    assert not unreadable, unreadable
    assert returned <= set(learners.RECORD_REASONS), sorted(returned - set(learners.RECORD_REASONS))


def test_every_published_reason_is_documented() -> None:
    """The other half of the same contract, and the half a reader depends on.

    docs/learner-database.md's table is where a code stops being a bare string and
    starts meaning something. Adding a code to RECORD_REASONS without a row there
    leaves a caller able to switch on it and unable to find out what it means.
    """
    doc = (Path(__file__).resolve().parents[2] / "docs" / "learner-database.md").read_text(encoding="utf-8")

    # The reason table only. Scanning the whole file would count the schema tables too,
    # and those carry rows named `outcome`, `score` and `learner_id` -- so a future
    # reason code sharing a column name would read as documented by a row that says
    # nothing about reason codes.
    heading = doc.index("| `reason` | Cause |")
    table = doc[heading : doc.index("\n\n", heading)]
    documented = set(re.findall(r"^\| `([a-z_]+)` \|", table, re.MULTILINE))

    assert "outcome" not in documented, "the slice leaked into a schema table"
    assert set(learners.RECORD_REASONS) <= documented, sorted(set(learners.RECORD_REASONS) - documented)


def test_store_is_available_separates_absence_from_breakage(tmp_path: Path, instance: Path) -> None:
    """Both readers answer None twice over, so a caller needs this to tell which it is.

    Saying "I do not teach German" when the database is simply unreadable would be a
    confident falsehood, which is worse than admitting the lookup failed.
    """
    assert store.store_is_available(instance) is True
    assert store.get_progress("sample-learner", "de", instance_path=instance) is None, "not taught"

    broken = tmp_path / "no-database"
    broken.mkdir()
    assert store.store_is_available(broken) is False
    assert store.get_progress("sample-learner", "es", instance_path=broken) is None, "unreadable"


# -------------------------------------------------------- degrading and data shape


def test_missing_database_degrades_without_raising(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """With no database at all the interface reports absence and stays quiet about ids."""
    with caplog.at_level(logging.WARNING):
        assert store.get_profile("sample-learner", instance_path=tmp_path) is None
        assert store.get_progress("sample-learner", "es", instance_path=tmp_path) is None
        outcome = store.record_result(
            "sample-learner", "es-01-greetings", "completed", instance_path=tmp_path
        )

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
        return store.record_result(
            "sample-learner", "es-04-ordering-food", "partial", instance_path=instance
        ).recorded

    def read(_: int) -> bool:
        return store.get_progress("sample-learner", "es", instance_path=instance) is not None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(write, range(writes))) + list(pool.map(read, range(writes)))

    assert all(results)
    assert _count_results(instance) == before + writes


# ------------------------------------------------------------- structural guards


# The two tables that hold anything about a person. Everything else in this schema --
# languages, lessons, schema_meta -- is shared reference data.
_PERSONAL_TABLES = ("learners", "lesson_results")


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


def test_every_module_level_query_touching_personal_data_is_scoped() -> None:
    """A named statement touching personal data cannot quietly skip the scoping guard.

    No exemptions: NEXT_LESSON_SQL is registered rather than skipped, because an
    exemption here is a precedent for skipping the next one.
    """
    for name, value in vars(store).items():
        if not isinstance(value, str) or not name.isupper() and not name.startswith("_"):
            continue
        if not isinstance(value, str) or "SELECT" not in value and "INSERT" not in value:
            continue
        if _personal(value):
            assert value in store._LEARNER_SCOPED_SQL, f"{name} touches personal data unscoped"


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
    return any(
        match.group(1).strip() and _INTERPOLATED not in match.group(1)
        for match in _TABLE_CLAUSE.finditer(sql)
    )

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


def _skeleton(
    node: ast.expr, bindings: dict[str, list[ast.expr | None]], seen: frozenset[str] = frozenset()
) -> str:
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

    * It inherits store._learner_scoped's rule, which is a substring test for a learner
      filter and has no notion of WHICH rows that filter constrains. A statement that
      joins a personal table to itself satisfies it while reading everyone:
      "SELECT r2.outcome FROM lesson_results AS r JOIN lesson_results AS r2
       ON r2.lesson_id = r.lesson_id WHERE r.learner_id = ?" reports green here.
      The same goes for "... WHERE learner_id = ? OR outcome = 'completed'" and for
      the second leg of a UNION. Reusing the rule rather than restating it is
      deliberate -- two copies would diverge -- so closing those means strengthening
      _learner_scoped itself, in store.py, and both call sites would inherit it.
      A marker inside a SQL comment is NOT in this list: comments are stripped before
      the rule is consulted, which normalises the input rather than restating the
      rule. Note the asymmetry that creates -- store._learner_scoped is also called at
      import time, on the raw literal, so a comment marker still satisfies the
      import-time check. This guard is now stricter than the thing it reuses. Moving
      the strip into _learner_scoped would remove the asymmetry and is the better
      home for it, in store.py.
    * A hole outside the table clause is allowed through. `WHERE learner_id = ? AND
      id = {lesson}` passes, and so does `WHERE learner_id = ? OR {extra}`, where the
      hole widens the very predicate the marker is trusted for. So what this proves is
      narrower than it looks: that a scoping MARKER appears in the literal text. That
      is weaker than scoping, and weaker still than the absence of injection -- an
      interpolated value in a WHERE clause remains an injection point. Refusing those
      would also refuse the scoped interpolated spellings this guard is required to
      keep accepting, so the limit is stated rather than closed.
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
        isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names)
        for node in ast.walk(tree)
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
        try:
            # Comments stripped for this check only. Normalising the input is not the
            # same as restating the rule: store._learner_scoped is still the only thing
            # that decides what scoping means, it is just no longer shown text the
            # database will never execute.
            store._learner_scoped(_SQL_COMMENT.sub(" ", sql))
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
        'def read(connection):\n'
        '    query = "SELECT outcome FROM lesson_results"\n'
        '    return connection.execute(query)',
        'def read(connection):\n'
        '    query = "SELECT outcome FROM lesson_results WHERE learner_id = ?"\n'
        '    return connection.execute(query)',
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
        ("a call", 'connection.execute(build_query(learner_id))'),
        ("an unbound name", 'connection.execute(QUERY)'),
        ("a name bound twice", 'QUERY = "SELECT 1"\nQUERY = "SELECT 2"\nconnection.execute(QUERY)'),
        ("a non-string constant", 'connection.execute(7)'),
        ("a name defined in terms of itself", 'QUERY = QUERY\nconnection.execute(QUERY)'),
        ("a comprehension", 'connection.execute([x for x in parts])'),
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
        ("a loop target", "def leak(connection, queries):\n    for QUERY in queries:\n        connection.execute(QUERY)\n"),
        ("a walrus", "def leak(connection, raw):\n    if (QUERY := raw):\n        connection.execute(QUERY)\n"),
        ("a with-as", "def leak(connection, m):\n    with m as QUERY:\n        connection.execute(QUERY)\n"),
        ("a comprehension target", "def leak(connection, qs):\n    return [connection.execute(QUERY) for QUERY in qs]\n"),
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
        ("a parameter default", 'def leak(connection, QUERY="SELECT outcome FROM lesson_results"):\n    connection.execute(QUERY)\n'),
        ("an import alias", "from elsewhere import q as QUERY\ndef leak(connection):\n    connection.execute(QUERY)\n"),
        ("tuple unpacking", 'QUERY, OTHER = "SELECT outcome FROM lesson_results", 1\ndef leak(connection):\n    connection.execute(QUERY)\n'),
        ("a constant joined from a tuple", 'def leak(connection):\n    connection.execute(" ".join(("SELECT outcome", "FROM lesson_results")))\n'),
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
        ("a quoted identifier", 'connection.execute(\'SELECT id FROM "learners"\')'),
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
        ("a double-quoted identifier", 'connection.execute(f\'SELECT outcome FROM "{table}" WHERE learner_id = ?\')'),
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
        ("a tab inside DO UPDATE", '"INSERT INTO learners (id, x, y) VALUES (?, ?, ?) ON CONFLICT(id) DO\tUPDATE SET x = 1"'),
        ("a newline inside DO UPDATE", '"INSERT INTO learners (id, x, y) VALUES (?, ?, ?) ON CONFLICT(id) DO\\nUPDATE SET x = 1"'),
        ("an INSERT ... SELECT", '"INSERT INTO learners (id, x, y) SELECT learner_id, outcome, recorded_at FROM lesson_results"'),
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
        ("one hop", 'run = connection.execute\nalias = run\nalias(QUERY)'),
        ("two hops", 'run = connection.execute\nmid = run\nalias = mid\nalias(QUERY)'),
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
    offenders = unverified_inline_queries(f'{builtin}(\'connection.execute("SELECT outcome FROM lesson_results")\')')

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

    _learner_scoped searches for the marker as a substring and cannot tell the
    difference. Stripping comments before consulting it is not restating the rule --
    the rule still decides what scoping means; it is just no longer shown text that
    will never be executed.
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
        ("a computed getattr", 'run = getattr(connection, name)\nrun(QUERY)'),
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

    assert unreadable == ["_schema_sql()"], (
        f"the bundled DDL should be the only statement the guard cannot read; got {unreadable}"
    )


def test_the_coverage_pin_looks_through_the_same_eyes_as_the_guard() -> None:
    """The pin and the guard must find the same statements, or the pin pins nothing.

    They used to walk separate copies of the same predicate. When the guard learned to
    follow an aliased execute, the pin did not, so the test whose whole job is to stop
    coverage shrinking had itself stopped seeing the statements the guard now reads --
    and it stayed green, because store.py happens to contain no alias. This asserts the
    two agree on planted source that does contain one.
    """
    planted = 'run = connection.execute\nrun(build_the_query())\n'
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
        '\n\ndef _every_result(connection, language_code):\n'
        '    return connection.execute(\n'
        '        f"SELECT outcome FROM lesson_results AS r "\n'
        '        f"JOIN lessons AS l ON l.id = r.lesson_id WHERE l.language_code = {language_code}"\n'
        '    ).fetchall()\n'
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
        "LanguageProgress",
        "LearnerProfile",
        "LessonAttempt",
        "Lesson",
        "OUTCOMES",
        "PractisedLanguage",
        "RECORD_REASONS",
        "RecordResultOutcome",
        "get_practised_languages",
        "get_profile",
        "get_progress",
        "record_result",
        "store_is_available",
    }
    for leaked in ("connect", "NEXT_LESSON_SQL", "ensure_learner_database", "SEED_LESSONS"):
        assert leaked not in learners.__all__


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
    assert progress.next_lesson.id == "es-03-numbers"
    assert isinstance(progress.next_lesson, Lesson)
    assert isinstance(progress.attempts[0], LessonAttempt)
