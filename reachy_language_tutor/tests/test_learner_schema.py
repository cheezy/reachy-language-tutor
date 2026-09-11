"""Tests for the learner database schema, seeding, and its failure modes."""

import os
import sqlite3
import logging
from pathlib import Path

import pytest

from reachy_language_tutor.learners import store


def _counts(instance_path: Path) -> dict[str, int]:
    connection = store.connect(instance_path)
    try:
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("languages", "lessons", "learners", "lesson_results")
        }
    finally:
        connection.close()


# --------------------------------------------------------------- path derivation


def test_learner_db_path_is_under_instance_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The database belongs beside the instance's other state, never in the repository."""
    assert store.learner_db_path_for_instance(tmp_path) == tmp_path / "learners.v1.sqlite3"
    assert store.learner_db_path_for_instance(str(tmp_path)) == tmp_path / "learners.v1.sqlite3"

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    fallback = store.learner_db_path_for_instance(None)
    assert fallback == tmp_path / "xdg" / "reachy_language_tutor" / "learners.v1.sqlite3"


# ------------------------------------------------------------------ idempotency


def test_ensure_creates_and_seeds_on_first_run(tmp_path: Path) -> None:
    """A fresh instance path gets a schema and seed data in one call."""
    result = store.ensure_learner_database(tmp_path)

    assert result.ready is True
    assert result.schema_applied is True
    assert result.seeded is True
    assert result.error is None
    assert result.path.exists()

    connection = store.connect(tmp_path)
    try:
        assert int(connection.execute("PRAGMA user_version").fetchone()[0]) == store.SCHEMA_VERSION
        row = connection.execute(
            "SELECT value FROM schema_meta WHERE key = ?", (store.SEED_VERSION_KEY,)
        ).fetchone()
        assert row["value"] == str(store.SEED_VERSION)
    finally:
        connection.close()


def test_ensure_is_idempotent_across_runs(tmp_path: Path) -> None:
    """Re-running against an existing database does no work and changes nothing."""
    store.ensure_learner_database(tmp_path)
    first = _counts(tmp_path)

    second = store.ensure_learner_database(tmp_path)
    third = store.ensure_learner_database(tmp_path)

    for result in (second, third):
        assert result.ready is True
        assert result.schema_applied is False
        assert result.seeded is False

    assert _counts(tmp_path) == first


def test_seed_version_bump_upserts_without_duplicating(tmp_path: Path) -> None:
    """A seed version bump converges app-owned catalog rows instead of duplicating them."""
    store.ensure_learner_database(tmp_path)
    before = _counts(tmp_path)

    connection = store.connect(tmp_path)
    try:
        connection.execute("UPDATE lessons SET title = 'WRONG' WHERE id = ?", ("es-01-greetings",))
        connection.execute("UPDATE schema_meta SET value = '0' WHERE key = ?", (store.SEED_VERSION_KEY,))
        connection.commit()
    finally:
        connection.close()

    assert store.ensure_learner_database(tmp_path).seeded is True

    connection = store.connect(tmp_path)
    try:
        title = connection.execute("SELECT title FROM lessons WHERE id = ?", ("es-01-greetings",)).fetchone()
        assert title["title"] == "Greetings and goodbyes"
    finally:
        connection.close()

    assert _counts(tmp_path) == before


def test_deleted_seed_learner_is_not_resurrected(tmp_path: Path) -> None:
    """Deleting a learner must stick, and must take their results with it.

    docs/plan.md requires learners can delete their data. A seed that ran whenever a
    table looked empty would quietly bring a deleted person back on the next start.
    """
    store.ensure_learner_database(tmp_path)

    connection = store.connect(tmp_path)
    try:
        connection.execute("DELETE FROM learners WHERE id = ?", ("sample-learner",))
        connection.commit()
    finally:
        connection.close()

    store.ensure_learner_database(tmp_path)

    counts = _counts(tmp_path)
    assert counts["learners"] == 0
    assert counts["lesson_results"] == 0, "results should cascade away with their learner"


def test_deleted_learner_survives_a_seed_version_bump(tmp_path: Path) -> None:
    """Deletion must outlast a catalog update.

    ON CONFLICT(id) DO NOTHING does not fire for a row that was deleted, so a seed-version
    bump would otherwise re-insert a learner the household asked to forget -- along with
    their results, because the "has practised" guard finds nothing once results cascaded.
    """
    store.ensure_learner_database(tmp_path)

    connection = store.connect(tmp_path)
    try:
        connection.execute("DELETE FROM learners WHERE id = ?", ("sample-learner",))
        connection.execute("UPDATE schema_meta SET value = '0' WHERE key = ?", (store.SEED_VERSION_KEY,))
        connection.commit()
    finally:
        connection.close()

    assert store.ensure_learner_database(tmp_path).seeded is True

    counts = _counts(tmp_path)
    assert counts["learners"] == 0, "a deleted learner must not come back on a seed bump"
    assert counts["lesson_results"] == 0, "nor may their results"
    assert counts["lessons"] == 12, "catalog rows should still converge"


# -------------------------------------------------------------------- seed data


def test_seed_row_counts(tmp_path: Path) -> None:
    """Seed data covers two languages with several ordered lessons each."""
    store.ensure_learner_database(tmp_path)

    assert _counts(tmp_path) == {
        "languages": 2,
        "lessons": 12,
        "learners": 1,
        "lesson_results": 3,
    }


def test_foreign_keys_are_enforced(tmp_path: Path) -> None:
    """SQLite ignores foreign keys unless every connection enables them."""
    store.ensure_learner_database(tmp_path)

    connection = store.connect(tmp_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO lesson_results (learner_id, lesson_id, outcome, score, recorded_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("nobody", "es-01-greetings", "completed", 50, 0),
            )
    finally:
        connection.close()


def test_constraints_reject_bad_values(tmp_path: Path) -> None:
    """Outcome and score are constrained so progress stays queryable."""
    store.ensure_learner_database(tmp_path)

    connection = store.connect(tmp_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO lesson_results (learner_id, lesson_id, outcome, score, recorded_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("sample-learner", "es-01-greetings", "fantastic", 50, 0),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO lesson_results (learner_id, lesson_id, outcome, score, recorded_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("sample-learner", "es-01-greetings", "completed", 101, 0),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO learners (id, display_name, created_at) VALUES (?, ?, ?)",
                ("blank", "   ", 0),
            )

        connection.execute(
            "INSERT INTO lesson_results (learner_id, lesson_id, outcome, score, recorded_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("sample-learner", "es-01-greetings", "skipped", None, 0),
        )
    finally:
        connection.close()


# ---------------------------------------------------------------- lesson order


def test_lesson_positions_are_contiguous_and_unique(tmp_path: Path) -> None:
    """Ordering is explicit, so two lessons can never tie for 'next'."""
    store.ensure_learner_database(tmp_path)

    connection = store.connect(tmp_path)
    try:
        for code in ("es", "fr"):
            rows = connection.execute(
                "SELECT position FROM lessons WHERE language_code = ? ORDER BY position", (code,)
            ).fetchall()
            assert [row["position"] for row in rows] == [1, 2, 3, 4, 5, 6]

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO lessons (id, language_code, position, title, objective) VALUES (?, ?, ?, ?, ?)",
                ("es-99-clash", "es", 1, "Clashing position", "Should be rejected."),
            )
    finally:
        connection.close()


def test_next_lesson_is_unambiguous(tmp_path: Path) -> None:
    """A partial attempt does not advance the learner; an unstarted language begins at one."""
    store.ensure_learner_database(tmp_path)

    connection = store.connect(tmp_path)
    try:
        spanish = connection.execute(store.NEXT_LESSON_SQL, ("es", "sample-learner")).fetchone()
        assert spanish["id"] == "es-03-numbers", "a 'partial' result must not count as done"

        french = connection.execute(store.NEXT_LESSON_SQL, ("fr", "sample-learner")).fetchone()
        assert french["id"] == "fr-01-greetings"

        assert connection.execute(store.NEXT_LESSON_SQL, ("de", "sample-learner")).fetchone() is None
    finally:
        connection.close()


# -------------------------------------------------------------------- degrading


def test_corrupt_database_degrades_without_raising(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A corrupt file is reported, not deleted -- it may hold real progress."""
    path = store.learner_db_path_for_instance(tmp_path)
    path.write_bytes(b"this is not a database")

    with caplog.at_level(logging.WARNING):
        result = store.ensure_learner_database(tmp_path)

    assert result.ready is False
    assert result.error
    assert "unavailable" in caplog.text
    assert path.read_bytes() == b"this is not a database", "must never silently recreate the file"


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores directory permissions"
)
def test_read_only_instance_path_degrades(tmp_path: Path) -> None:
    """An unwritable instance path is reported rather than crashing the app."""
    instance = tmp_path / "readonly"
    instance.mkdir()
    os.chmod(instance, 0o500)
    try:
        result = store.ensure_learner_database(instance)
        assert result.ready is False
        assert result.error
    finally:
        os.chmod(instance, 0o700)


def test_empty_catalog_still_produces_usable_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no seed content the database is still valid and answers queries."""
    monkeypatch.setattr(store, "SEED_LANGUAGES", ())
    monkeypatch.setattr(store, "SEED_LESSONS", ())
    monkeypatch.setattr(store, "SEED_LEARNERS", ())
    monkeypatch.setattr(store, "SEED_RESULTS", ())

    assert store.ensure_learner_database(tmp_path).ready is True
    assert _counts(tmp_path) == {"languages": 0, "lessons": 0, "learners": 0, "lesson_results": 0}

    connection = store.connect(tmp_path)
    try:
        assert connection.execute(store.NEXT_LESSON_SQL, ("es", "sample-learner")).fetchone() is None
    finally:
        connection.close()


# -------------------------------------------------------------------- packaging


def test_schema_sql_is_declared_as_package_data() -> None:
    """schema.sql must ship in a built wheel, not only in a source checkout."""
    schema = Path(store.__file__).resolve().parent / "schema.sql"
    assert schema.is_file()

    pyproject = Path(store.__file__).resolve().parents[3] / "pyproject.toml"
    package_data = [
        line for line in pyproject.read_text().splitlines() if line.startswith("reachy_language_tutor = [")
    ]
    assert package_data, "package-data entry for reachy_language_tutor not found"
    assert "learners/*.sql" in package_data[0], (
        "schema.sql would not ship in a wheel; add learners/*.sql to [tool.setuptools.package-data]"
    )


# ------------------------------------------------------------------ integration


def test_fresh_instance_path_is_usable_on_first_start(tmp_path: Path) -> None:
    """A path that does not exist yet becomes a working database in one call."""
    instance = tmp_path / "instance"
    assert not instance.exists()

    assert store.ensure_learner_database(instance).ready is True

    connection = store.connect(instance, create=False)
    try:
        row = connection.execute(store.NEXT_LESSON_SQL, ("es", "sample-learner")).fetchone()
        assert row["id"] == "es-03-numbers"
    finally:
        connection.close()
