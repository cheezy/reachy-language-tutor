"""Tests for the learner store's query interface: profiles, progress, and results.

Schema creation, seeding and their failure modes live in test_learner_schema.py.

These tests use direct SQL for setup. That is fine: the "all SQL lives in the store"
verification is scoped to the application package, not to the test suite.
"""

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


def _personal(sql: str) -> bool:
    """Report whether a statement reads or writes anything about a person."""
    return "lesson_results" in sql or "FROM learners" in sql or "INTO learners" in sql


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


def test_no_inline_query_touches_personal_data_unscoped() -> None:
    """The guard's blind spot, closed by reading the module's own source.

    _learner_scoped only sees statements someone remembered to wrap, and the test above
    only sees module-level names. A query written inline inside a function body escapes
    both -- and inline is the form a future change is most likely to take, because the
    module already uses it for catalog reads. So parse the source and check every
    string literal handed to execute().
    """
    source = Path(store.__file__).resolve().read_text(encoding="utf-8")
    tree = ast.parse(source)

    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in ("execute", "executemany", "executescript"):
            continue
        for argument in node.args[:1]:
            if not isinstance(argument, ast.Constant) or not isinstance(argument.value, str):
                continue  # a Name refers to a module constant, covered by the test above
            sql = argument.value
            if not _personal(sql):
                continue
            # Creating a learner row is inherently unscoped - there is no other
            # learner's data it could reach. Everything else must satisfy the module's
            # own rule, reused here rather than restated so the two cannot diverge.
            if sql.replace(" ", "").startswith("INSERTINTOlearners(id,"):
                continue
            try:
                store._learner_scoped(sql)
            except ValueError:
                offenders.append(f"line {argument.lineno}: {sql[:70]}")

    assert not offenders, "inline statements touching personal data without a learner filter: " + "; ".join(
        offenders
    )


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
