"""Which result is a lesson's LATEST: the last one written, never the one with the latest clock.

Every reader that decides "is this lesson finished" asks the same question -- what was
the last thing that happened to it -- and they used to answer it with recorded_at, the
robot's wall clock. A clock that steps backwards (no battery-backed clock and no network
time yet, or a person changing it) then made an older row look newer: a completion was
silently outranked by the partial written before it. These tests set the clock through
the store's own utc_now_ms, which is what record_result stamps with, so they exercise
the real write path rather than a hand-built row.

The second half pins the sibling that D38's latest-wins rule never reached:
get_practised_languages counted "any completion ever" while get_progress counted the
latest word, so the profile and the progress tool disagreed about the same lesson.
"""

from __future__ import annotations
import logging
import sqlite3
from pathlib import Path

import pytest

from reachy_language_tutor.learners import store


_EPOCH_PLUS_A_DAY = 86_400_000


@pytest.fixture
def instance(tmp_path: Path) -> Path:
    """Prepare a learner database at a temporary instance path."""
    assert store.ensure_learner_database(tmp_path).ready is True
    return tmp_path


def _learner(instance: Path, name: str = "Test Person") -> str:
    outcome = store.record_consent(
        name,
        scope="local_profile",
        statement_id="test.v1",
        statement_text="Wording used by the tests.",
        granted_by="the_person_themselves",
        granted_via="operator_at_the_robot",
        instance_path=instance,
    )
    assert outcome.recorded and outcome.learner_id is not None
    return outcome.learner_id


def _record_at(
    instance: Path, monkeypatch: pytest.MonkeyPatch, clock: int, learner_id: str, lesson_id: str, outcome: str
) -> None:
    """Record through record_result with the robot's clock reading `clock`."""
    monkeypatch.setattr(store, "utc_now_ms", lambda: clock)
    assert store.record_result(learner_id, lesson_id, outcome, instance_path=instance).recorded is True
    monkeypatch.undo()


def _completed_ids(instance: Path, learner_id: str, language_code: str) -> set[str]:
    progress = store.get_progress(learner_id, language_code, instance_path=instance)
    assert progress is not None
    return {lesson.id for lesson in progress.completed}


def _next_lesson_from_the_sql(instance: Path, learner_id: str, language_code: str) -> str | None:
    connection = store.connect(instance)
    try:
        row = connection.execute(store.NEXT_LESSON_SQL, store.next_lesson_params(language_code, learner_id)).fetchone()
        return None if row is None else str(row["id"])
    finally:
        connection.close()


def _practised(instance: Path, learner_id: str, language_code: str) -> tuple[int, int] | None:
    for language in store.get_practised_languages(learner_id, instance_path=instance):
        if language.code == language_code:
            return (language.attempts, language.completed)
    return None


# ------------------------------------------------------------- a clock that steps back


def test_a_completion_written_after_the_clock_stepped_back_still_counts(
    instance: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The measured defect: the completion is the LAST row, and it must be the last word.

    With recorded_at deciding, the partial -- stamped today -- outranked the
    completion written after it on a clock reading 1970, and the lesson was offered
    again with the completion sitting in the table.
    """
    learner = _learner(instance)
    lesson = "de-01-greetings"
    _record_at(instance, monkeypatch, store.utc_now_ms(), learner, lesson, "partial")
    _record_at(instance, monkeypatch, _EPOCH_PLUS_A_DAY, learner, lesson, "completed")

    assert lesson in _completed_ids(instance, learner, "de"), "get_progress lost a completion to the clock"
    assert _next_lesson_from_the_sql(instance, learner, "de") != lesson, "NEXT_LESSON_SQL offered it again"
    assert _practised(instance, learner, "de") == (2, 1), "get_practised_languages lost it too"


def test_a_redo_written_after_the_clock_stepped_back_still_reopens_the_lesson(
    instance: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mirror image: a completion stamped by a clock running AHEAD.

    Once the clock is corrected, the redo the learner asked for carries the smaller
    stamp. Ordered by recorded_at it was ignored and the lesson stayed finished.
    """
    learner = _learner(instance)
    lesson = "de-01-greetings"
    _record_at(instance, monkeypatch, store.utc_now_ms() + 365 * _EPOCH_PLUS_A_DAY, learner, lesson, "completed")
    _record_at(instance, monkeypatch, store.utc_now_ms(), learner, lesson, "partial")

    assert lesson not in _completed_ids(instance, learner, "de"), "the redo was outranked by a future stamp"
    assert _next_lesson_from_the_sql(instance, learner, "de") == lesson
    assert _practised(instance, learner, "de") == (2, 0)


def test_attempts_are_listed_newest_written_first(instance: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """attempts[0] is what the tools read as a lesson's latest attempt.

    It has to be the same row the finished-or-not rule reads, or a tool would describe
    one attempt while the rule acted on another.
    """
    learner = _learner(instance)
    _record_at(instance, monkeypatch, store.utc_now_ms(), learner, "de-01-greetings", "partial")
    _record_at(instance, monkeypatch, _EPOCH_PLUS_A_DAY, learner, "de-01-greetings", "completed")

    progress = store.get_progress(learner, "de", instance_path=instance)
    assert progress is not None
    assert [attempt.outcome for attempt in progress.attempts] == ["completed", "partial"]


# ------------------------------------------------ the id really is insertion order


def test_the_next_row_is_above_every_surviving_row_after_the_top_rows_are_erased(instance: Path) -> None:
    """The property the whole rule rests on, run rather than argued.

    lesson_results has no AUTOINCREMENT, so after an erasure deletes the rows holding
    the maximum id, SQLite reuses those ids. That is safe only if every reused id is
    still above every row that survives -- which is what this asserts, and then checks
    the rule still reads the right row across the reuse.
    """
    keeper = _learner(instance, "Keeper Person")
    erased = _learner(instance, "Erased Person")
    assert store.record_result(keeper, "de-01-greetings", "completed", instance_path=instance).recorded
    for _ in range(3):
        assert store.record_result(erased, "de-01-greetings", "partial", instance_path=instance).recorded

    connection = sqlite3.connect(store.learner_db_path_for_instance(instance))
    try:
        top_before = int(connection.execute("SELECT max(id) FROM lesson_results").fetchone()[0])
        erased_ids = [
            row[0] for row in connection.execute("SELECT id FROM lesson_results WHERE learner_id = ?", (erased,))
        ]
    finally:
        connection.close()
    assert max(erased_ids) == top_before, "the erased learner must hold the maximum id, or this proves nothing"

    outcome = store.forget_learner_entirely(erased, instance_path=instance)
    assert outcome is not None and outcome.results == 3
    assert store.record_result(keeper, "de-01-greetings", "partial", instance_path=instance).recorded

    connection = sqlite3.connect(store.learner_db_path_for_instance(instance))
    try:
        rows = connection.execute(
            "SELECT id, outcome FROM lesson_results WHERE learner_id = ? ORDER BY id", (keeper,)
        ).fetchall()
    finally:
        connection.close()
    assert [outcome for _, outcome in rows] == ["completed", "partial"], "insertion order was not id order"
    assert rows[1][0] in erased_ids, "the id was not reused, so the case this pins was not reached"
    assert "de-01-greetings" not in _completed_ids(instance, keeper, "de"), "the redo on a reused id was missed"


# ----------------------------------------------- get_practised_languages, the sibling


def test_practised_languages_counts_a_completion_only_while_it_is_the_latest_word(instance: Path) -> None:
    """D38's rule, applied to the reader it had not reached.

    A skip after a completion is the latest word too: the lesson is not finished, and
    the skip itself is still not counted as practice.
    """
    learner = _learner(instance)
    record = lambda lesson, outcome: store.record_result(learner, lesson, outcome, instance_path=instance)  # noqa: E731

    assert record("de-01-greetings", "completed").recorded
    assert _practised(instance, learner, "de") == (1, 1)
    assert record("de-01-greetings", "partial").recorded
    assert _practised(instance, learner, "de") == (2, 0), "a redo left the lesson counted as completed"
    assert record("de-01-greetings", "completed").recorded
    assert _practised(instance, learner, "de") == (3, 1)
    assert record("de-01-greetings", "skipped").recorded
    assert _practised(instance, learner, "de") == (3, 0), "a completion followed by a skip is not finished"


def test_practised_languages_and_progress_agree_on_every_history(instance: Path) -> None:
    """A differential check rather than a list of cases: the two readers must agree.

    Every sequence of up to three outcomes on one lesson, beside every sequence of up
    to one on a second, compared on the completed count both readers publish.
    Measured with the fix reverted: 48 of these 160 histories disagreed.
    """
    outcomes = ("completed", "partial", "skipped")
    histories: list[tuple[str, ...]] = [()] + [(a,) for a in outcomes]
    histories += [(a, b) for a in outcomes for b in outcomes]
    histories += [(a, b, c) for a in outcomes for b in outcomes for c in outcomes]
    disagreements = []
    for first in histories:
        for second in histories[:4]:
            learner = _learner(instance, "Differential Person")
            for outcome in first:
                assert store.record_result(learner, "de-01-greetings", outcome, instance_path=instance).recorded
            for outcome in second:
                assert store.record_result(learner, "de-02-introductions", outcome, instance_path=instance).recorded
            progress = store.get_progress(learner, "de", instance_path=instance)
            assert progress is not None
            practised = _practised(instance, learner, "de")
            completed_here = 0 if practised is None else practised[1]
            if completed_here != len(progress.completed):
                disagreements.append((first, second, completed_here, len(progress.completed)))
            assert store.forget_learner_entirely(learner, instance_path=instance) is not None
    assert disagreements == [], f"{len(disagreements)} of {len(histories) * 4} histories disagree: {disagreements[:3]}"


def test_get_practised_languages_still_logs_nothing_for_a_real_learner(
    instance: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The new subquery binds the learner a second time; a miscount of parameters raises.

    sqlite3 would answer "Incorrect number of bindings" and the reader would absorb it
    as a storage failure -- an empty tuple and a warning -- so the populated answer and
    the silent log are both asserted.
    """
    with caplog.at_level(logging.WARNING):
        practised = store.get_practised_languages("sample-learner", instance_path=instance)
    assert [(language.code, language.attempts, language.completed) for language in practised] == [("es", 3, 2)]
    assert caplog.text == ""
