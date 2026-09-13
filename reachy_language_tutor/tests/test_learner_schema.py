"""Tests for the learner database schema, seeding, and its failure modes."""

import os
import json
import stat
import struct
import logging
import sqlite3
from pathlib import Path

import pytest
from untaught_language import UNTAUGHT_CODE

from reachy_language_tutor.learners import store


def _tables(instance_path: Path) -> tuple[str, ...]:
    """Every table the schema actually created, asked of the database itself.

    Read from the database rather than listed here, because a hand-kept list is how a
    new table goes uncounted: the four names this helper used to carry were written
    when there were four tables, and five more arrived without it changing colour.
    """
    connection = store.connect(instance_path)
    try:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return tuple(str(row["name"]) for row in rows)
    finally:
        connection.close()


def _seeded_lesson_count() -> int:
    """How many lesson rows the seed writes, from both places it takes them from.

    len(SEED_LESSONS) was the whole answer until lessons converted from a published
    course arrived in converted_lessons.json, and three tests were asserting it as
    though it still were. A lesson is a lesson wherever its text is kept.
    """
    return len(store.SEED_LESSONS) + len(store._converted_lessons())


def _counts(instance_path: Path) -> dict[str, int]:
    """Row counts for every table in the database, keyed by table name.

    schema_meta is excluded: it is bookkeeping about the seed rather than seeded data,
    so counting it would make every caller's expectation move whenever a new key is
    recorded. Everything else is counted, so a new table cannot be seeded silently.
    """
    connection = store.connect(instance_path)
    try:
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in _tables(instance_path)
            if table != "schema_meta"
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
    assert counts["lessons"] == _seeded_lesson_count(), "catalog rows should still converge"


def _seeded_ids_raw(instance_path: Path) -> str:
    connection = store.connect(instance_path)
    try:
        row = connection.execute(
            "SELECT value FROM schema_meta WHERE key = ?", (store.SEEDED_LEARNERS_KEY,)
        ).fetchone()
        return "" if row is None else str(row["value"])
    finally:
        connection.close()


def _set_seeded_ids_raw(instance_path: Path, value: str) -> None:
    connection = store.connect(instance_path)
    try:
        connection.execute(
            "INSERT INTO schema_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (store.SEEDED_LEARNERS_KEY, value),
        )
        connection.commit()
    finally:
        connection.close()


def test_seeded_learner_ids_is_stored_as_json(tmp_path: Path) -> None:
    """A JSON array cannot fragment the way a comma-joined string can."""
    store.ensure_learner_database(tmp_path)

    parsed = json.loads(_seeded_ids_raw(tmp_path))
    assert isinstance(parsed, list)
    assert parsed == ["sample-learner"]


def test_legacy_comma_joined_record_is_still_read(tmp_path: Path) -> None:
    """An installation seeded before this became JSON must not lose its record.

    Losing it re-enables resurrecting a deleted learner on exactly the installations
    that already hold real learner data.
    """
    store.ensure_learner_database(tmp_path)
    _set_seeded_ids_raw(tmp_path, "sample-learner")

    connection = store.connect(tmp_path)
    try:
        assert store._seeded_learner_ids(connection) == {"sample-learner"}
    finally:
        connection.close()


def test_legacy_record_still_blocks_resurrection_after_upgrade(tmp_path: Path) -> None:
    """The upgrade path itself: legacy record + deleted learner + seed bump."""
    store.ensure_learner_database(tmp_path)
    _set_seeded_ids_raw(tmp_path, "sample-learner")

    connection = store.connect(tmp_path)
    try:
        connection.execute("DELETE FROM learners WHERE id = ?", ("sample-learner",))
        connection.execute("UPDATE schema_meta SET value = '0' WHERE key = ?", (store.SEED_VERSION_KEY,))
        connection.commit()
    finally:
        connection.close()

    assert store.ensure_learner_database(tmp_path).seeded is True
    assert _counts(tmp_path)["learners"] == 0, "a legacy record must still make deletion permanent"


def test_learner_id_containing_a_comma_round_trips(tmp_path: Path) -> None:
    """The failure the JSON encoding exists to prevent."""
    store.ensure_learner_database(tmp_path)
    _set_seeded_ids_raw(tmp_path, json.dumps(["smith, john"]))

    connection = store.connect(tmp_path)
    try:
        assert store._seeded_learner_ids(connection) == {"smith, john"}
    finally:
        connection.close()


def test_record_is_written_even_when_no_new_learners(tmp_path: Path) -> None:
    """The write must not depend on a learner row actually having been inserted.

    Reaching the path the old `if new_learners:` guard skipped needs every seeded learner
    to be recorded ALREADY, so the reseed computes no new learners at all. Deleting the
    record instead would empty already_seeded, make every learner "new", and fire the old
    guard - which is exactly the trap this test exists to avoid.

    The legacy comma-joined value doubles as the observable: under the old guard the write
    is skipped and the value stays a bare string; under the unconditional write it is
    rewritten as JSON.
    """
    store.ensure_learner_database(tmp_path)
    _set_seeded_ids_raw(tmp_path, "sample-learner")

    connection = store.connect(tmp_path)
    try:
        connection.execute("UPDATE schema_meta SET value = '0' WHERE key = ?", (store.SEED_VERSION_KEY,))
        connection.commit()
    finally:
        connection.close()

    # Precondition: with sample-learner already recorded, this reseed inserts no learner
    # row, so the removed guard would have skipped the write entirely.
    connection = store.connect(tmp_path)
    try:
        already = store._seeded_learner_ids(connection)
        assert {row[0] for row in store.SEED_LEARNERS} <= already, (
            "precondition failed: this test only covers the guard when nothing is new"
        )
    finally:
        connection.close()

    assert store.ensure_learner_database(tmp_path).seeded is True
    assert json.loads(_seeded_ids_raw(tmp_path)) == ["sample-learner"], (
        "the record must be rewritten even when no learner row was inserted"
    )


def test_unreadable_record_degrades_and_warns(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """An unrecognisable value is permissive, so it must be loud."""
    store.ensure_learner_database(tmp_path)
    _set_seeded_ids_raw(tmp_path, json.dumps({"not": "a list"}))

    connection = store.connect(tmp_path)
    try:
        with caplog.at_level(logging.WARNING):
            assert store._seeded_learner_ids(connection) == set()
    finally:
        connection.close()

    assert "seeded_learner_ids" in caplog.text


# -------------------------------------------------------------------- seed data


def test_seed_row_counts(tmp_path: Path) -> None:
    """Seed data covers two languages with several ordered lessons each."""
    store.ensure_learner_database(tmp_path)

    converted = store._converted_lessons()

    assert _counts(tmp_path) == {
        "languages": len(store.SEED_LANGUAGES),
        "lessons": _seeded_lesson_count(),
        # Every lesson has provenance, from whichever of the two places it came.
        "lesson_sources": _seeded_lesson_count(),
        "learners": 1,
        "lesson_results": 3,
        # Nothing seeds a faceprint, and nothing ever should: a shipped faceprint would
        # be fabricated biometric data for a person who does not exist. Enrolment is the
        # only way a row gets in here, which is what W28 is for.
        "faceprints": 0,
        # Content ships only for the converted lessons. The rest of the catalog has
        # none yet and has to keep working meanwhile, which is why these are counted
        # from the file rather than pinned to a number somebody would have to update.
        "lesson_dialogues": sum(1 for lesson in converted if lesson["dialogue_title"] is not None),
        "lesson_dialogue_turns": sum(len(lesson["turns"]) for lesson in converted),
        "lesson_notes": sum(len(lesson["notes"]) for lesson in converted),
        "lesson_drills": sum(len(lesson["drills"]) for lesson in converted),
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

        assert connection.execute(store.NEXT_LESSON_SQL, (UNTAUGHT_CODE, "sample-learner")).fetchone() is None
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
    # Without this the sources upsert runs thirty rows against an empty lessons table,
    # the foreign key refuses them, and ensure_learner_database reports the store
    # unusable -- blaming the store for what is really an inconsistent seed.
    monkeypatch.setattr(store, "SEED_LESSON_SOURCES", ())
    # The converted lessons come from a file rather than a constant, so emptying the
    # constants is not enough: their foreign key would look for lessons nobody seeded.
    monkeypatch.setattr(store, "_converted_lessons", tuple)

    assert store.ensure_learner_database(tmp_path).ready is True
    assert set(_counts(tmp_path).values()) == {0}, "no seed content means no rows anywhere"

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


# ------------------------------------------------- every branch of the seeded-ids read
#
# This record is what makes a household's deletion permanent across seed-version bumps.
# Losing an id here re-seeds a learner someone asked to be forgotten, so every branch
# that can lose one is covered, and the two that silently did are pinned by value.


def _read_ids(instance_path: Path, raw: str) -> set[str]:
    """Store a raw record and read it back through the real function."""
    _set_seeded_ids_raw(instance_path, raw)
    connection = store.connect(instance_path)
    try:
        return store._seeded_learner_ids(connection)
    finally:
        connection.close()


@pytest.mark.parametrize(
    "raw",
    [
        "123",
        "1.50",
        "1e5",
        "0.1",
        "12345678901234567890",
        "-7",
        "1E5",
        "1.0e-3",
        # json.loads accepts these three bare tokens and returns floats, so they reach
        # the number arm too. Under str(parsed) they became "inf", "-inf" and "nan".
        "Infinity",
        "-Infinity",
        "NaN",
    ],
)
def test_a_stored_number_reads_back_exactly_as_stored(tmp_path: Path, raw: str) -> None:
    """The id is the stored TEXT; the parsed number is a lossy rendering of it.

    str(json.loads(x)) is not x for any literal whose Python repr differs from its
    stored spelling: "1e5" came back "100000.0" and "1.50" came back "1.5". Each is a
    DIFFERENT id, so the real one was dropped -- and dropping one here re-seeds a
    learner a household deleted. "123" and "0.1" round-tripped by luck, which is why
    they are in this list too: they are what made the bug invisible.
    """
    store.ensure_learner_database(tmp_path)

    assert _read_ids(tmp_path, raw) == {raw}


def test_a_stored_json_string_loses_its_quotes(tmp_path: Path) -> None:
    """The scalar branch's other arm, and the reason it cannot just use the raw text.

    A quoted JSON string must be read through the PARSED value; keeping the quotes
    would yield an id matching no real learner. This is the case that makes the number
    arm's opposite treatment look inconsistent until you see both.
    """
    store.ensure_learner_database(tmp_path)

    assert _read_ids(tmp_path, '"sample-learner"') == {"sample-learner"}


@pytest.mark.parametrize("raw", ["true", "false"])
def test_a_stored_boolean_warns_rather_than_becoming_an_id(
    tmp_path: Path, raw: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Bool subclasses int, so it slipped through the number arm and became "True".

    An id of "True" matches no learner, so the record was effectively empty -- but
    silently, and silence here is the permissive direction: it lets a deleted learner
    be seeded again. The warning is the whole point of this branch.
    """
    store.ensure_learner_database(tmp_path)

    with caplog.at_level(logging.WARNING):
        assert _read_ids(tmp_path, raw) == set()

    assert "treating it as empty" in caplog.text
    assert raw not in caplog.text, "the record's contents must not reach the log"


@pytest.mark.parametrize("raw", ["", "   ", "\t\n"])
def test_an_empty_record_reads_as_empty_without_a_warning(
    tmp_path: Path, raw: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Empty is a real state -- nothing has been seeded yet -- not a parse failure.

    Warning here would cry wolf on every fresh install, which is how a warning that
    matters stops being read.
    """
    store.ensure_learner_database(tmp_path)

    with caplog.at_level(logging.WARNING):
        assert _read_ids(tmp_path, raw) == set()

    assert caplog.text == ""


@pytest.mark.parametrize("raw", ["null", '{"a": 1}', "[1, 2]", '["ok", 2]'])
def test_a_shape_this_record_never_had_warns(
    tmp_path: Path, raw: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Neither a list of strings nor a scalar: degrade loudly, never quietly."""
    store.ensure_learner_database(tmp_path)

    with caplog.at_level(logging.WARNING):
        assert _read_ids(tmp_path, raw) == set()

    assert "allows sample learners to be seeded again" in caplog.text
    assert raw not in caplog.text, "the record's contents must not reach the log"


def test_the_failure_path_never_logs_the_record_itself(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The module's rule: log the exception or the key, never the stored contents.

    An earlier version of this pinned it by asserting "True" was absent while storing
    "true" -- substrings that could never appear however the warning was written, so
    the assertion could not fail and the rule had no coverage at all. The marker below
    is stored IN the record precisely so that a warning which started interpolating it
    would be caught.
    """
    marker = "learner-id-that-must-not-be-logged"
    store.ensure_learner_database(tmp_path)

    with caplog.at_level(logging.WARNING):
        assert _read_ids(tmp_path, json.dumps({marker: 1})) == set()

    assert "allows sample learners to be seeded again" in caplog.text, "it did warn"
    assert marker not in caplog.text, "the record's contents must not reach the log"


def test_a_refused_record_is_erased_by_the_next_seed_pass(tmp_path: Path) -> None:
    """The cost of degrading, stated in full rather than one step short.

    "Refused with a warning" sounds per-read and reversible. It is neither: _seed
    rewrites this record from the set it just read, in the same transaction, so the
    unreadable text is gone before anyone reads the warning and there is no later
    chance to repair it by hand. Pinned because the comment and the docs now claim it.
    """
    store.ensure_learner_database(tmp_path)
    _set_seeded_ids_raw(tmp_path, "true")

    connection = store.connect(tmp_path)
    try:
        connection.execute("UPDATE schema_meta SET value = '0' WHERE key = ?", (store.SEED_VERSION_KEY,))
        connection.commit()
    finally:
        connection.close()

    assert store.ensure_learner_database(tmp_path).seeded is True
    assert _seeded_ids_raw(tmp_path) == json.dumps(["sample-learner"]), "the original text is gone"


def test_a_comma_inside_json_is_data_and_not_a_separator(tmp_path: Path) -> None:
    """The same id must survive every JSON spelling, or the guarantee is a coin flip.

    test_learner_id_containing_a_comma_round_trips already pins this for the array
    form. The quoted-string arm used to comma-split as well, so "smith, john" survived
    as an array element and fragmented as a scalar -- two ids matching no learner, no
    warning, and the real one gone. Splitting belongs only where the raw text really is
    the legacy comma-joined form, which is the non-JSON branch.
    """
    store.ensure_learner_database(tmp_path)

    assert _read_ids(tmp_path, json.dumps("smith, john")) == {"smith, john"}
    assert _read_ids(tmp_path, json.dumps(["smith, john"])) == {"smith, john"}
    # And the branch that genuinely IS the legacy form still splits.
    assert _read_ids(tmp_path, "alice,bob") == {"alice", "bob"}


@pytest.mark.parametrize(
    ("label", "raw", "expected_after"),
    [
        ("JSON array", '["sample-learner"]', ["sample-learner"]),
        ("legacy comma-joined", "sample-learner", ["sample-learner"]),
        ("legacy comma-joined, several", "sample-learner,someone-else", ["sample-learner", "someone-else"]),
        ("JSON string scalar", '"sample-learner"', ["sample-learner"]),
    ],
)
def test_no_stored_form_resurrects_a_deleted_learner_across_a_seed_bump(
    tmp_path: Path, label: str, raw: str, expected_after: list[str]
) -> None:
    """The behaviour all of the above exists to protect, once per stored form.

    A unit test on the parse proves the ids come back; only this proves that coming
    back is what stops the re-seed.

    Only two of these four were ever WRITTEN by this code: W4 comma-joined the ids
    bare, and D2 onward writes a JSON array. A bare quoted string has never been
    emitted by any writer, and a bare number only as a single comma-joined id that
    happens to parse. They are kept as defensive coverage of arms that exist, not as
    claims about what is sitting on a disk somewhere.
    """
    store.ensure_learner_database(tmp_path)
    _set_seeded_ids_raw(tmp_path, raw)

    connection = store.connect(tmp_path)
    try:
        connection.execute("DELETE FROM learners WHERE id = ?", ("sample-learner",))
        connection.execute("UPDATE schema_meta SET value = '0' WHERE key = ?", (store.SEED_VERSION_KEY,))
        connection.commit()
    finally:
        connection.close()

    assert store.ensure_learner_database(tmp_path).seeded is True

    # learners == 0 alone cannot tell "the seed ran and withheld this learner" from
    # "the seed did nothing at all". The catalog rows cannot tell them apart either --
    # they survive from the FIRST ensure call and are only converged by the second, so
    # asserting on them proves nothing about THIS pass, which is what an earlier
    # version of this comment claimed. What does: the record is rewritten from the set
    # just read, and only by a pass that actually seeded.
    assert _seeded_ids_raw(tmp_path) == json.dumps(sorted(expected_after)), label
    assert _counts(tmp_path)["learners"] == 0, label


def test_a_numeric_id_read_from_the_raw_text_still_suppresses_the_reseed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The chain the unit test above cannot close, for the one arm this task changed.

    No shipped id is numeric -- SEED_LEARNERS holds only "sample-learner" -- so a
    numeric record cannot be exercised end to end without standing one up. Patching
    the seed constant is the smallest way to prove that "reads back exactly" is
    actually what stops the re-seed, rather than a property asserted in isolation and
    assumed to matter. Under the old str(parsed) the record read "100000.0", matched
    nothing, and this learner came back.
    """
    monkeypatch.setattr(store, "SEED_LEARNERS", (("1e5", "Numeric Learner", 0),))
    monkeypatch.setattr(store, "SEED_RESULTS", ())

    assert store.ensure_learner_database(tmp_path).seeded is True
    assert _counts(tmp_path)["learners"] == 1

    _set_seeded_ids_raw(tmp_path, "1e5")
    connection = store.connect(tmp_path)
    try:
        connection.execute("DELETE FROM learners WHERE id = ?", ("1e5",))
        connection.execute("UPDATE schema_meta SET value = '0' WHERE key = ?", (store.SEED_VERSION_KEY,))
        connection.commit()
    finally:
        connection.close()

    assert store.ensure_learner_database(tmp_path).seeded is True

    # Same oracle as above: the rewritten record is what shows this pass seeded.
    assert _seeded_ids_raw(tmp_path) == json.dumps(["1e5"])
    assert _counts(tmp_path)["learners"] == 0, "a numeric id must make deletion permanent too"


def test_the_untaught_placeholders_are_never_taught(tmp_path: Path) -> None:
    """The suite uses these to mean "a language this robot does not teach".

    Several tests depend on that being true, and nothing used to enforce it. They
    spelled it as German and Portuguese instead, which was a fact about the seed
    data rather than a property of the test -- so the task that added German broke
    six tests at once, one of them with a UNIQUE constraint violation, and hid a
    Portuguese sibling behind the first failure.

    Asserted against the live catalog as well as the constants, because a future
    catalog that came from somewhere other than SEED_LANGUAGES would slip past a
    constant-only check.
    """
    from untaught_language import (
        UNTAUGHT_CODE,
        UNTAUGHT_NAME,
        UNTAUGHT_CODE_ABSENT,
        UNTAUGHT_LANGUAGE_ROW_NAME,
    )

    reserved_codes = {UNTAUGHT_CODE, UNTAUGHT_CODE_ABSENT}
    reserved_names = {UNTAUGHT_NAME, UNTAUGHT_LANGUAGE_ROW_NAME}
    why = (
        "the test suite uses this to mean 'a language we do not teach'; "
        "seeding it makes several tests assert a falsehood"
    )

    assert reserved_codes.isdisjoint({code for code, _ in store.SEED_LANGUAGES}), why
    assert reserved_names.isdisjoint({name for _, name in store.SEED_LANGUAGES}), why

    assert store.ensure_learner_database(tmp_path).ready is True
    catalog = store.get_language_catalog(instance_path=tmp_path)
    assert reserved_codes.isdisjoint({entry.code for entry in catalog}), why
    assert reserved_names.isdisjoint({entry.name for entry in catalog}), why


def test_a_catalog_expansion_reaches_an_already_seeded_robot(tmp_path: Path) -> None:
    """The acceptance criterion that only a test can settle, so it is tested not asserted.

    Twenty robots are already in homes with a seeded database. Adding a language to
    SEED_LANGUAGES does nothing for any of them unless SEED_VERSION moves: _seed
    returns early on `_seed_version(connection) >= SEED_VERSION`, and no error is
    raised anywhere. The robot simply goes on teaching the old catalog forever.

    Rewound to SEED_VERSION - 1 rather than to 0, so this tests the bump that was
    actually made. A rewind to 0 would pass even if the constant had not moved.
    """
    assert store.ensure_learner_database(tmp_path).ready is True
    new_codes = ("de", "it", "pt")

    connection = store.connect(tmp_path)
    try:
        placeholders = ",".join("?" for _ in new_codes)
        connection.execute(f"DELETE FROM lessons WHERE language_code IN ({placeholders})", new_codes)
        connection.execute(f"DELETE FROM languages WHERE code IN ({placeholders})", new_codes)
        connection.execute(
            "UPDATE schema_meta SET value = ? WHERE key = ?",
            (str(store.SEED_VERSION - 1), store.SEED_VERSION_KEY),
        )
        connection.commit()
    finally:
        connection.close()

    behind = _counts(tmp_path)
    assert behind["languages"] == len(store.SEED_LANGUAGES) - len(new_codes)

    assert store.ensure_learner_database(tmp_path).seeded is True

    after = _counts(tmp_path)
    assert after["languages"] == len(store.SEED_LANGUAGES)
    assert after["lessons"] == _seeded_lesson_count()
    # The reason Spanish stays. A lesson upsert never deletes, so the ON DELETE CASCADE
    # from lessons to lesson_results never fires and nobody's history is touched.
    assert after["lesson_results"] == behind["lesson_results"]

    catalog = store.get_language_catalog(instance_path=tmp_path)
    assert {entry.code for entry in catalog} >= set(new_codes)


def test_existing_progress_survives_a_catalog_expansion(tmp_path: Path) -> None:
    """Nobody mid-course loses a lesson because three languages arrived."""
    assert store.ensure_learner_database(tmp_path).ready is True
    learner = store.SEED_LEARNERS[0][0]

    # Both languages the criterion names. SEED_RESULTS is Spanish-only, so a French
    # result has to be recorded here or the French half of the claim is untested.
    assert store.record_result(learner, "fr-01-greetings", "completed", instance_path=tmp_path).recorded is True

    before = {
        code: store.get_progress(learner, code, instance_path=tmp_path) for code in ("es", "fr")
    }
    assert all(progress is not None and progress.completed for progress in before.values())

    connection = store.connect(tmp_path)
    try:
        connection.execute(
            "UPDATE schema_meta SET value = ? WHERE key = ?",
            (str(store.SEED_VERSION - 1), store.SEED_VERSION_KEY),
        )
        connection.commit()
    finally:
        connection.close()
    assert store.ensure_learner_database(tmp_path).seeded is True

    for code, was in before.items():
        now = store.get_progress(learner, code, instance_path=tmp_path)
        assert now is not None, code
        assert [lesson.id for lesson in now.completed] == [lesson.id for lesson in was.completed], code
        assert (now.next_lesson.id if now.next_lesson else None) == (
            was.next_lesson.id if was.next_lesson else None
        ), code


# Every catalog that has ever shipped, keyed by the seed version that shipped it.
#
# Entries 1 and 2 were computed over SEED_LANGUAGES and SEED_LESSONS alone, which was
# every seed tuple that existed when they shipped. From entry 3 the fingerprint covers
# every SEED_* tuple the store declares -- see _catalog_fingerprint below. The older
# lines are NOT recomputed and must not be: each records what a robot in somebody's
# home actually received, and only the newest line is ever checked against the code.
#
# APPEND ONLY. Never edit an existing entry: each line is a record of what a robot in
# somebody's home actually received, and rewriting one makes this file lie about the
# installed base. To change the catalog, bump SEED_VERSION and add a line.
SHIPPED_CATALOGS = {
    1: "a739c1c9aed1de9888de223e3f4f31eb0800c965eb54d1d8271794751b62f389",  # Spanish, French
    2: "c6efbb187caf89a96c03c744fb5d2b9bd6baf63ab6e0cfe629c0fe72209913c8",  # + German, Italian, Portuguese
    3: "71d42df41af603be5d9471571273c40bdb91168fc6b4b111951e87358c7651fb",  # + a provenance row for every lesson
    4: "1ae76c0c18e1843da97f71a42d0da66df41c39073e5bef06ffe6b2b36ab57b4f",  # + six Italian lessons converted from FSI Italian FAST
}


# The only SEED_* names that are not seeded content, and why each is out.
#
# SEED_VERSION is the version this fingerprint is KEYED by. Folding it in would give
# every bump a unique hash no matter what the catalog did, which would satisfy the
# uniqueness assertion below automatically and quietly retire the thing that makes a
# version un-reusable. SEED_VERSION_KEY is the name of a schema_meta row, not content.
NOT_SEED_CONTENT = frozenset({"SEED_VERSION", "SEED_VERSION_KEY"})


def _catalog_fingerprint() -> str:
    """Hash every seed tuple the store declares, found rather than listed.

    Naming SEED_LANGUAGES and SEED_LESSONS is what this used to do, and it is the
    shape that goes stale: SEED_LESSON_SOURCES arrived and the fingerprint covering
    "the seeded catalog" did not cover it, so thirty new rows could have shipped
    without this guard changing colour. Anything the store calls a seed constant is
    part of what a robot receives, whatever its type, including the next one nobody
    has written yet.

    Sorted by name so the hash depends on the seed data and not on declaration order,
    with the bundled converted-lesson file hashed alongside it.

    What is EXCLUDED is named one constant at a time, and nothing else is. Filtering by
    type instead -- "every SEED_* that is a tuple" -- was the first version of this and
    it fails open: SEED_LESSON_COURSE is a str, is genuinely part of what ships, and
    was covered only by accident because SEED_LESSON_SOURCES happens to embed it. The
    next non-tuple seed constant would have had no such accident. A name has to be
    written into NOT_SEED_CONTENT to leave the hash, which is the direction that fails
    loudly.
    """
    import hashlib

    # The converted lessons are seeded content that is not a Python constant at all --
    # they live in a JSON file the seed reads at runtime. Hashing the file's bytes is
    # what puts them under the same rule as everything else here: correct a drill, and
    # this fingerprint moves, so the version has to move with it or a robot that
    # already has a database keeps teaching the uncorrected line.
    content = (Path(store.__file__).resolve().parent / store.CONVERTED_LESSONS_FILENAME).read_bytes()

    seeds = sorted(
        (name, repr(value))
        for name, value in vars(store).items()
        # The leading underscore is stripped first, so a PRIVATE seed constant is
        # covered too. _SEED_EPOCH_MS is one: it is the timestamp written into the
        # sample learner's rows, and it was previously covered only because
        # SEED_LEARNERS happens to embed its value -- the same by-accident coverage
        # this function's own docstring rejects one paragraph above.
        if name.lstrip("_").startswith("SEED_") and name.lstrip("_") not in NOT_SEED_CONTENT
    )
    return hashlib.sha256(repr(seeds).encode() + content).hexdigest()


def test_a_seed_constant_that_is_not_a_tuple_is_still_fingerprinted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard shown failing, on the exact case the earlier filter let through.

    This fingerprint used to admit only tuples, which meant a seed constant of any
    other type -- a course name, a default, a mapping -- shipped without the pin ever
    noticing. Planting one is the only way to show the inversion works: the existing
    non-tuple constant, SEED_LESSON_COURSE, is embedded in SEED_LESSON_SOURCES, so it
    would move the hash either way and proves nothing on its own.
    """
    before = _catalog_fingerprint()

    monkeypatch.setattr(store, "SEED_A_LATER_IDEA", {"language": "ja"}, raising=False)

    assert _catalog_fingerprint() != before, "a new seed constant escaped the fingerprint"


def test_the_fingerprint_still_ignores_the_version_it_is_keyed_by(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one exclusion that has to hold, or the uniqueness assertion means nothing.

    If SEED_VERSION were hashed, every bump would produce a different fingerprint on
    its own, so "two versions must not ship an identical catalog" would be satisfied by
    the bump rather than by the catalog -- and a version could be bumped with no change
    at all, which is precisely what that assertion exists to catch.
    """
    before = _catalog_fingerprint()

    monkeypatch.setattr(store, "SEED_VERSION", store.SEED_VERSION + 1)

    assert _catalog_fingerprint() == before


def test_changing_the_seeded_catalog_requires_bumping_seed_version() -> None:
    """The pitfall that costs the most in the field, and the only guard that can see it.

    Editing SEED_LANGUAGES or SEED_LESSONS without moving SEED_VERSION is silent. _seed
    returns early on `_seed_version(connection) >= SEED_VERSION`, so every robot that
    already has a database keeps the old catalog forever and nothing reports a problem.
    The learner is simply never offered the new language.

    A convergence test cannot catch this alone: rewinding a database to SEED_VERSION - 1
    re-seeds it whether or not the constant ever moved. Measured - leaving SEED_VERSION
    at 1 while adding three languages left the whole suite green.

    A single pinned fingerprint could not catch it either, and that is the more
    interesting failure: the first version of this test told you in its own message to
    paste the new hash, which satisfied it without bumping anything. A guard whose
    instructions describe the bypass is worse than none, because it reads as protection.

    So the pin is a HISTORY, not a value. Each shipped catalog keeps its own line, the
    current version must be the newest one, and a version can never be reused for
    different content - so the only way to change the catalog is to add a version.
    """
    fingerprint = _catalog_fingerprint()

    assert store.SEED_VERSION == max(SHIPPED_CATALOGS), (
        f"SEED_VERSION is {store.SEED_VERSION} but the newest shipped catalog is "
        f"{max(SHIPPED_CATALOGS)}. Bump SEED_VERSION and ADD a line to SHIPPED_CATALOGS; "
        "never overwrite an existing one."
    )
    assert SHIPPED_CATALOGS[store.SEED_VERSION] == fingerprint, (
        f"the seeded catalog is now {fingerprint}, which is not what seed version "
        f"{store.SEED_VERSION} shipped. You changed the catalog: bump SEED_VERSION so "
        "already-seeded robots converge, and ADD a new line to SHIPPED_CATALOGS. Do not "
        "edit the existing line - it records what robots in homes actually received."
    )
    assert len(set(SHIPPED_CATALOGS.values())) == len(SHIPPED_CATALOGS), (
        "two seed versions ship an identical catalog, so one of them changed nothing"
    )


# ------------------------------------------------------------------- faceprints


# Every column the faceprints table is permitted to have, as (name, declared type,
# notnull, part-of-primary-key). An ALLOW-LIST, not a list of forbidden names: a
# deny-list of "image", "crop", "thumbnail", "path" is only ever as complete as the
# imagination of whoever last edited it, and the column that breaks the promise will
# be called something nobody thought of. This says what the table may BE, so anything
# else -- a new column, a widened type, a dropped NOT NULL -- fails.
_PERMITTED_FACEPRINT_COLUMNS = {
    # notnull is 1 on the primary key because the table is STRICT -- SQLite makes a
    # STRICT table's PRIMARY KEY implicitly NOT NULL, which an ordinary table does not.
    # Measured from PRAGMA table_info rather than read off the CREATE statement.
    ("learner_id", "TEXT", 1, 1),
    ("embedding_model", "TEXT", 1, 0),
    ("dimension", "INTEGER", 1, 0),
    ("vector", "BLOB", 1, 0),
    ("created_at", "INTEGER", 1, 0),
}


def _faceprint_columns(instance_path: Path) -> set[tuple[str, str, int, int]]:
    """Read the faceprint table's real shape from the database rather than the file."""
    connection = store.connect(instance_path)
    try:
        return {
            (str(row["name"]), str(row["type"]), int(row["notnull"]), int(row["pk"]))
            for row in connection.execute("PRAGMA table_info(faceprints)")
        }
    finally:
        connection.close()


def test_no_faceprint_column_can_hold_an_image_a_crop_or_a_path_to_one(tmp_path: Path) -> None:
    """The privacy promise, checked against the table rather than against a sentence.

    docs/plan.md says faceprints are numeric face data and never photos. The way that
    stops being true is not somebody storing a JPEG in the vector column -- it is a
    later `image_path` or `last_seen_crop` column arriving because it was convenient,
    and the sentence in the doc never being reread.

    So this pins the whole shape. Adding any column fails it, whatever it is called.
    """
    store.ensure_learner_database(tmp_path)

    assert _faceprint_columns(tmp_path) == _PERMITTED_FACEPRINT_COLUMNS

    # And the one column that holds bytes at all is bounded to exactly the size its
    # own dimension says, so it cannot quietly become a place to put a file.
    connection = store.connect(tmp_path)
    try:
        connection.execute("INSERT INTO learners (id, display_name, created_at) VALUES ('p', 'P', 0)")
        with pytest.raises(sqlite3.IntegrityError, match="length\\(vector\\) = dimension"):
            connection.execute(
                "INSERT INTO faceprints VALUES ('p', 'm', 1, ?, 0)",
                (b"\x00" * 4096,),
            )

        # The other half, and the one the column list alone does not answer:
        # embedding_model is the only caller-supplied TEXT here and has room for a
        # path, so the database itself must refuse one. Asserted through raw SQL rather
        # than through save_faceprint, because the Python guard is a separate copy of
        # this rule and a test that only exercised it would pass with the CHECK gone.
        for path_like in (
            "/Users/someone/child.jpg",
            "~/Pictures/child.png",
            "faces/child.jpeg",
            "../../etc/passwd",
            "file:///tmp/face.png",
            # The one that stepped over every other clause. SQLite's length(), trim()
            # and GLOB stop at the first NUL, so before the byte-length clause this
            # value measured as 3 characters, passed the 1..128 bound, passed the
            # character allow-list, and stored the path in full.
            "arc\x00/Users/secret/kid-face.jpg" + "X" * 5000,
        ):
            with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
                connection.execute(
                    "INSERT INTO faceprints VALUES ('p', ?, 1, ?, 0)",
                    (path_like, b"\x00" * 4),
                )
    finally:
        connection.close()


def test_a_faceprint_check_that_trims_also_refuses_null(tmp_path: Path) -> None:
    """The CHECK, exercised where it can actually be reached.

    This is the trap W23 measured and this table inherits. NULL satisfies a bare CHECK
    -- length(trim(NULL)) is NULL, not 0 -- so `CHECK (length(trim(x)) > 0)` accepts a
    NULL happily, and only the paired `x IS NOT NULL` refuses it.

    The columns are ALSO declared NOT NULL, and NOT NULL fires first: inserting NULL
    into the real table raises "NOT NULL constraint failed", which is a different
    constraint. A test that stopped there would pass with the IS NOT NULL deleted.
    So the CHECK is rebuilt here with the NOT NULL stripped, which is the only way to
    put the clause under test on its own.
    """
    schema = (Path(store.__file__).resolve().parent / "schema.sql").read_text(encoding="utf-8")
    faceprints = schema.split("CREATE TABLE IF NOT EXISTS faceprints", 1)[1].split(") STRICT;", 1)[0]

    assert "embedding_model IS NOT NULL" in faceprints, "the paired IS NOT NULL is what this is about"

    connection = sqlite3.connect(":memory:")
    try:
        # The same clause, minus the NOT NULL that would otherwise answer first.
        connection.execute(
            "CREATE TABLE probe (embedding_model TEXT "
            "CHECK (embedding_model IS NOT NULL AND length(trim(embedding_model)) BETWEEN 1 AND 128)) STRICT"
        )
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            connection.execute("INSERT INTO probe (embedding_model) VALUES (NULL)")

        # And the proof that the pairing is load-bearing: without IS NOT NULL, the
        # same NULL is accepted. If this ever starts raising, the trap is gone and the
        # test above is no longer testing anything.
        connection.execute(
            "CREATE TABLE naive (embedding_model TEXT "
            "CHECK (length(trim(embedding_model)) BETWEEN 1 AND 128)) STRICT"
        )
        connection.execute("INSERT INTO naive (embedding_model) VALUES (NULL)")
        assert connection.execute("SELECT COUNT(*) FROM naive").fetchone()[0] == 1
    finally:
        connection.close()


def test_a_vector_whose_length_disagrees_with_its_dimension_is_refused(tmp_path: Path) -> None:
    """Prove dimension is not a hint: the database enforces it against the blob's length."""
    store.ensure_learner_database(tmp_path)

    connection = store.connect(tmp_path)
    try:
        connection.execute("INSERT INTO learners (id, display_name, created_at) VALUES ('p', 'P', 0)")
        with pytest.raises(sqlite3.IntegrityError, match="length\\(vector\\) = dimension"):
            connection.execute("INSERT INTO faceprints VALUES ('p', 'm', 4, ?, 0)", (b"\x00" * 12,))
        # STRICT refuses the other way in as well.
        with pytest.raises(sqlite3.IntegrityError, match="cannot store TEXT value in BLOB column"):
            connection.execute("INSERT INTO faceprints VALUES ('p', 'm', 3, 'not-bytes', 0)")
    finally:
        connection.close()


def test_deleting_a_learner_leaves_no_faceprint_row_behind(tmp_path: Path) -> None:
    """Erasure, proved by deleting a learner rather than by trusting the CASCADE clause.

    ON DELETE CASCADE is only enforced when the connection turns foreign keys on, so
    the clause being present in schema.sql proves nothing on its own. This deletes
    through store.connect(), which is the only way the app opens the database, and
    then asks both the raw table and the reader.
    """
    store.ensure_learner_database(tmp_path)

    connection = store.connect(tmp_path)
    try:
        connection.execute("INSERT INTO learners (id, display_name, created_at) VALUES ('p', 'P', 0)")
        connection.commit()
    finally:
        connection.close()

    assert store.save_faceprint("p", "m", [1.0, 2.0], instance_path=tmp_path).saved is True
    assert _counts(tmp_path)["faceprints"] == 1

    connection = store.connect(tmp_path)
    try:
        connection.execute("DELETE FROM learners WHERE id = ?", ("p",))
        connection.commit()
    finally:
        connection.close()

    assert _counts(tmp_path)["faceprints"] == 0, "the faceprint went with the person"
    assert store.get_faceprint("p", instance_path=tmp_path) is None


def _downgrade_to_schema_2(instance_path: Path) -> None:
    """Put a database back the way it looked before this change, and check it took."""
    connection = store.connect(instance_path)
    try:
        connection.execute("DROP TABLE faceprints")
        connection.execute("PRAGMA user_version = 2")
        connection.commit()
    finally:
        connection.close()
    assert "faceprints" not in _tables(instance_path), "the fixture must really remove it"


def test_the_faceprint_table_reaches_a_database_that_predates_it(tmp_path: Path) -> None:
    """The upgrade a robot in a house actually takes, executed rather than reasoned about.

    A new table reaches an installed robot only because SCHEMA_VERSION moved and
    _apply_schema re-runs the whole script below it. Without the bump this database
    would stay at version 2 forever and the table would reach fresh installs only --
    silently, which is the whole reason this test exists.

    Downgrading like this is not something a real robot does; it is how the "already
    installed" state is reached on a laptop. The faceprints it drops are the fixture's,
    not anybody's.
    """
    store.ensure_learner_database(tmp_path)
    _downgrade_to_schema_2(tmp_path)

    result = store.ensure_learner_database(tmp_path)

    assert result.ready is True
    assert result.schema_applied is True, "the bump is what re-ran the script"
    assert "faceprints" in _tables(tmp_path)

    connection = store.connect(tmp_path)
    try:
        assert int(connection.execute("PRAGMA user_version").fetchone()[0]) == store.SCHEMA_VERSION
    finally:
        connection.close()

    # The learner's existing history survived the upgrade, and the new table works.
    assert _counts(tmp_path)["lesson_results"] == 3
    assert store.save_faceprint("sample-learner", "m", [1.0], instance_path=tmp_path).saved is True


def test_a_nul_byte_cannot_step_over_the_model_name_rules(tmp_path: Path) -> None:
    """The measured bypass, pinned so it cannot come back.

    SQLite's length(), trim() and GLOB are NUL-terminated on a TEXT value. Measured:
    "arc" + NUL + "/Users/secret/kid-face.jpg" + 5000 characters is reported by
    length() as 3, satisfies BETWEEN 1 AND 128, and satisfies the character allow-list
    as containing nothing forbidden -- and the whole value was stored and read back
    with the path intact. One byte defeated all three clauses at once.

    length(CAST(x AS BLOB)) counts bytes rather than NUL-terminated characters, so
    requiring it to equal length(x) admits exactly the single-byte ASCII this column
    permits and nothing hiding behind a terminator.

    This is asserted against the DATABASE, not through save_faceprint: the Python
    guard refused this value all along, which is precisely why the hole was invisible.
    Two copies of one rule that do not mean the same thing is D19's defect shape.
    """
    store.ensure_learner_database(tmp_path)
    hidden = "arc\x00/Users/secret/kid-face.jpg" + "X" * 5000

    connection = store.connect(tmp_path)
    try:
        connection.execute("INSERT INTO learners (id, display_name, created_at) VALUES ('p', 'P', 0)")

        # What SQLite's own functions think, so the test names the mechanism it pins.
        assert connection.execute("SELECT length(?)", (hidden,)).fetchone()[0] == 3
        assert connection.execute("SELECT ? NOT GLOB '*[^A-Za-z0-9._-]*'", (hidden,)).fetchone()[0] == 1

        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            connection.execute("INSERT INTO faceprints VALUES ('p', ?, 1, ?, 0)", (hidden, b"\x00" * 4))

        # A bare NUL, and a name that is valid ASCII but multi-byte, both refused.
        for value in ("a\x00b", "arcfac\u00e9"):
            with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
                connection.execute("INSERT INTO faceprints VALUES ('p', ?, 1, ?, 0)", (value, b"\x00" * 4))

        # And a real model name still goes in.
        connection.execute("INSERT INTO faceprints VALUES ('p', 'arcface-r100', 1, ?, 0)", (b"\x00" * 4,))
    finally:
        connection.close()


def test_a_name_cannot_hide_unbounded_content_behind_a_nul(tmp_path: Path) -> None:
    """The sibling of the faceprint NUL rule, swept as far as this column can be.

    learners.display_name had the same bare length(trim(...)) > 0 clause, and the same
    NUL trick worked on it: "A" + NUL + a path + 5000 characters was stored whole while
    measuring as a one-character name. Latent rather than live -- the only writer today
    is the app-owned seed INSERT and the store publishes no create-learner function --
    but it is the same mechanism, and this repository's rule is to fix the class.

    What this pins is deliberately narrower than the faceprint rule, and the difference
    is the point: embedding_model may be ASCII-only because it is an identifier, while
    this column holds a person's NAME. Accents, non-Latin scripts, apostrophes and
    combining marks are real names and must keep working, so the bound is on bytes
    rather than on characters -- which refuses the unbounded case and still admits a
    SHORT hidden path. The complete guard belongs in the enrolment writer (W28), where
    it can also reach a robot that already has a database; a schema rule added later
    cannot, because CREATE TABLE IF NOT EXISTS is a no-op against an existing table.
    """
    store.ensure_learner_database(tmp_path)
    connection = store.connect(tmp_path)
    try:
        hidden = "A\x00/Users/secret/kid-face.jpg" + "X" * 5000
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            connection.execute(
                "INSERT INTO learners (id, display_name, created_at) VALUES ('x', ?, 0)", (hidden,)
            )

        # Every one of these is a real name and must still be accepted. If this half
        # ever goes red, the guard has been tightened into something that refuses
        # people rather than payloads.
        for index, name in enumerate(("Ana", "José García", "Zoë O'Brien-Smith", "大翔")):
            connection.execute(
                "INSERT INTO learners (id, display_name, created_at) VALUES (?, ?, 0)",
                (f"real{index}", name),
            )
        assert connection.execute("SELECT COUNT(*) FROM learners").fetchone()[0] == 5
    finally:
        connection.close()


# ------------------------------------ the file itself, not the rows it holds (D31)


def _faceprint_bytes(values: list[float]) -> bytes:
    """Pack the exact bytes the store writes for this vector, to search the file for."""
    return struct.pack(f"<{len(values)}f", *values)


def _all_database_files(instance_path: Path) -> list[Path]:
    """Return the database and whichever of its WAL companions currently exist."""
    main = Path(store.learner_db_path_for_instance(instance_path))
    return [path for path in (main, Path(f"{main}-wal"), Path(f"{main}-shm")) if path.exists()]


def test_the_database_and_its_companions_are_owner_only(tmp_path: Path) -> None:
    """Asked of the filesystem, not of the source.

    The file holds face templates for a household. Owner-only is the minimum, and the
    threat model is the one that does not go through the learner-scoping guard at all:
    another local account, an unencrypted backup, or the SD card out of a Wireless.

    The -wal and -shm companions are checked too. Note what this does and does not
    prove: SQLite COPIES the main file's mode onto them when it creates them --
    measured, a 0600 database yields 0600 companions and a 0644 one yields 0644 -- so
    on the create path this assertion holds whether or not the store touches them. The
    companion tightening earns its place on the UPGRADE path instead, where companions
    left behind by an earlier version already exist at 0644; that is what the test
    below exercises, and that is the one that fails if it is removed.
    """
    store.ensure_learner_database(tmp_path)
    connection = store.connect(tmp_path)
    try:
        connection.execute("INSERT INTO learners (id, display_name, created_at) VALUES ('p','P',0)")
        connection.commit()
        # A second connection while the WAL is live is what brings the companions into
        # existence and tightens them.
        store.connect(tmp_path).close()

        files = _all_database_files(tmp_path)
        assert len(files) >= 1, "the database should exist by now"
        for path in files:
            mode = stat.S_IMODE(path.stat().st_mode)
            assert mode == 0o600, f"{path.name} is {oct(mode)}, not owner-only"
    finally:
        connection.close()


def test_a_database_left_world_readable_is_tightened_on_the_next_open(tmp_path: Path) -> None:
    """The upgrade path, which matters more than the create path.

    Every robot already running has a database at 0644. If this only tightened on
    create, those files would stay readable forever and the fix would reach nobody who
    already has one -- the same silent-no-op shape as a schema change that only touches
    fresh installs.
    """
    store.ensure_learner_database(tmp_path)
    path = Path(store.learner_db_path_for_instance(tmp_path))
    path.chmod(0o644)
    assert stat.S_IMODE(path.stat().st_mode) == 0o644, "the fixture did not take"

    store.connect(tmp_path).close()

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_secure_delete_is_live_on_the_connection_that_actually_writes(tmp_path: Path) -> None:
    """Read the pragma back from a real connection.

    Grepping store.py for the line would pass with the pragma set on some other
    connection, or on none -- the same defect shape as a guard that checks a different
    invocation than the one running.
    """
    store.ensure_learner_database(tmp_path)
    connection = store.connect(tmp_path)
    try:
        assert int(connection.execute("PRAGMA secure_delete").fetchone()[0]) == 1
    finally:
        connection.close()


def test_a_deleted_faceprint_leaves_no_bytes_in_the_file(tmp_path: Path) -> None:
    """The claim W25 could not make, and the reason this task exists.

    W25's cascade test asks the TABLE whether the row is gone, which is a weaker claim:
    measured before this fix, the packed float32 vector and the model name were both
    still byte-recoverable from learners.v1.sqlite3 after delete_faceprint returned 1
    and a wal_checkpoint(TRUNCATE). A household asking to be forgotten means the larger
    thing, so this asks the FILE.

    Reverting PRAGMA secure_delete makes this fail with the vector still present.
    """
    store.ensure_learner_database(tmp_path)
    connection = store.connect(tmp_path)
    try:
        connection.execute("INSERT INTO learners (id, display_name, created_at) VALUES ('p','P',0)")
        connection.commit()
    finally:
        connection.close()

    values = [0.125 * index for index in range(64)]
    assert store.save_faceprint("p", "arcface-r100", values, instance_path=tmp_path).saved is True
    packed = _faceprint_bytes(values)

    # It really is in the file while the row exists, or this test could pass on a
    # database that never held the vector at all.
    checkpoint = store.connect(tmp_path)
    try:
        checkpoint.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        checkpoint.close()
    assert any(packed in path.read_bytes() for path in _all_database_files(tmp_path)), (
        "the vector was never written, so its absence afterwards would prove nothing"
    )

    assert store.delete_faceprint("p", instance_path=tmp_path) == 1
    checkpoint = store.connect(tmp_path)
    try:
        checkpoint.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        checkpoint.close()

    # The MAIN file by name. A checkpoint truncates -wal to zero bytes, so looping over
    # the companions here would assert `packed not in b""` and pass on an empty file --
    # vacuously. The main database is where the page actually lands, so that is what is
    # asserted; the concurrent-connection test below covers the case where the WAL is
    # the file still holding something.
    main = Path(store.learner_db_path_for_instance(tmp_path))
    blob = main.read_bytes()
    assert packed not in blob, "the faceprint's bytes survive in the database file"
    assert b"arcface-r100" not in blob, "the model name survives in the database file"


def test_deleting_a_learner_leaves_no_faceprint_bytes_in_the_file_either(tmp_path: Path) -> None:
    """The cascade path gets the same treatment, because it is the one W30 will use.

    Erasing a household member deletes the learner row and cascades to the faceprint;
    the bytes have to go with it, not merely the row.
    """
    store.ensure_learner_database(tmp_path)
    connection = store.connect(tmp_path)
    try:
        connection.execute("INSERT INTO learners (id, display_name, created_at) VALUES ('p','P',0)")
        connection.commit()
    finally:
        connection.close()

    values = [0.5 - 0.01 * index for index in range(64)]
    store.save_faceprint("p", "sface_probe", values, instance_path=tmp_path)
    packed = _faceprint_bytes(values)

    connection = store.connect(tmp_path)
    try:
        connection.execute("DELETE FROM learners WHERE id = ?", ("p",))
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()

    main = Path(store.learner_db_path_for_instance(tmp_path))
    assert packed not in main.read_bytes(), "the cascaded faceprint survives in the database file"


def test_a_filesystem_that_cannot_chmod_is_reported_rather_than_raised(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never raise into a caller, and never swallow either.

    Every caller of connect() is a store function that promises not to raise, so a
    read-only filesystem must not end a conversation turn. But an unreported failure to
    tighten permissions on biometric data is worse than a visible one, so it is logged
    -- with the error TYPE only, because the path can name somebody's home directory.
    """
    # Start from an unlatched module. Without this the test passes only because it is
    # the sole exercise of the branch and runs once: anything earlier in the process
    # that failed a chmod on the same file would de-duplicate this one away, and the
    # failure would be an order-dependency rather than the code under test. monkeypatch
    # restores the real set afterwards.
    monkeypatch.setattr(store, "_PERMISSION_FAILURES_REPORTED", set())

    store.ensure_learner_database(tmp_path)
    # The file must actually NEED tightening, or _restrict_permissions skips the chmod
    # and this test passes without ever reaching the branch it is about. os.chmod here
    # rather than Path.chmod, because Path.chmod is what gets patched below.
    os.chmod(store.learner_db_path_for_instance(tmp_path), 0o644)

    def refuse(self: Path, mode: int) -> None:
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(Path, "chmod", refuse)
    with caplog.at_level(logging.WARNING):
        store.connect(tmp_path).close()

    assert "Could not restrict permissions" in caplog.text
    assert "OSError" in caplog.text
    assert str(tmp_path) not in caplog.text, "the path must not reach the log"


def test_a_second_database_that_cannot_be_tightened_is_also_reported(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """De-duplicating a warning must not silence a different failure.

    A single process-wide flag reports the first chmod failure and then nothing, ever:
    measured, a genuine failure on a SECOND database at a second path, after the flag
    was set, logged nothing at all. That is the swallow the pitfall is about, arriving
    by way of the de-duplication rather than by way of the except.

    Collapsing _PERMISSION_FAILURES_REPORTED back to one boolean makes this fail.
    """
    monkeypatch.setattr(store, "_PERMISSION_FAILURES_REPORTED", set())

    first, second = tmp_path / "first", tmp_path / "second"
    for instance in (first, second):
        store.ensure_learner_database(instance)
        os.chmod(store.learner_db_path_for_instance(instance), 0o644)

    def refuse(self: Path, mode: int) -> None:
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(Path, "chmod", refuse)

    with caplog.at_level(logging.WARNING):
        store.connect(first).close()
    assert "Could not restrict permissions" in caplog.text, "the first failure went unreported"

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        store.connect(second).close()
    assert "Could not restrict permissions" in caplog.text, (
        "a failure on a different database was silenced by the first one"
    )
    for instance in (first, second):
        assert str(instance) not in caplog.text, "the path must not reach the log"


def test_companions_left_world_readable_by_an_earlier_version_are_tightened(tmp_path: Path) -> None:
    """The case where tightening the companions is the mechanism, not a side effect.

    SQLite copies the main file's mode onto -wal and -shm when it creates them, so on a
    fresh database they are already owner-only and the store's own chmod changes
    nothing. It matters on the upgrade path: a robot running the previous version has a
    0644 database AND 0644 companions, and the WAL holds pages that have not been
    checkpointed -- so a faceprint can sit in a world-readable -wal while the main file
    is already tightened.

    Removing the companions from the loop in _restrict_permissions makes this fail.
    """
    store.ensure_learner_database(tmp_path)
    main = Path(store.learner_db_path_for_instance(tmp_path))

    keep_wal_alive = store.connect(tmp_path)
    try:
        keep_wal_alive.execute("INSERT INTO learners (id, display_name, created_at) VALUES ('p','P',0)")
        keep_wal_alive.commit()

        # -journal too. SQLite writes one instead of a WAL when journal_mode = WAL
        # cannot take -- a filesystem without shared memory -- and it holds freed pages
        # exactly as the WAL does. It is created here rather than provoked, because
        # provoking it needs such a filesystem; what is being pinned is that the loop
        # in _restrict_permissions names the suffix at all. Without this, deleting
        # -journal from that loop leaves every test in this file green.
        journal = Path(f"{main}-journal")
        journal.touch()

        companions = [Path(f"{main}{suffix}") for suffix in ("-wal", "-shm", "-journal")]
        existing = [path for path in companions if path.exists()]
        assert journal in existing, "the rollback journal is not being checked"
        assert existing, "no WAL companions exist, so this test would prove nothing"

        # Put them back the way an earlier version would have left them.
        for path in existing:
            os.chmod(path, 0o644)
        assert all(stat.S_IMODE(path.stat().st_mode) == 0o644 for path in existing)

        store.connect(tmp_path).close()

        for path in existing:
            mode = stat.S_IMODE(path.stat().st_mode)
            assert mode == 0o600, f"{path.name} stayed {oct(mode)} after a connect"
    finally:
        keep_wal_alive.close()


def test_erasure_holds_while_another_connection_is_open(tmp_path: Path) -> None:
    """The case that actually broke, and the reason delete_faceprint checkpoints.

    SQLite checkpoints when the LAST connection closes, so a test that opens and closes
    one connection per call gets a checkpoint for free and proves nothing about a
    running app -- where the conversation loop and a tool can hold connections at the
    same time. Measured before the fix, with a second connection held open:
    delete_faceprint returned 1 while the packed vector AND the model name were both
    still fully recoverable from the MAIN database file, because the delete sat
    unshipped in the WAL and the main file still held the original page.

    Reverting the PRAGMA wal_checkpoint in delete_faceprint makes this fail.
    """
    store.ensure_learner_database(tmp_path)
    connection = store.connect(tmp_path)
    try:
        connection.execute("INSERT INTO learners (id, display_name, created_at) VALUES ('p','P',0)")
        connection.commit()
    finally:
        connection.close()

    values = [0.125 * index for index in range(64)]
    store.save_faceprint("p", "arcface-r100", values, instance_path=tmp_path)
    packed = _faceprint_bytes(values)

    holder = store.connect(tmp_path)
    try:
        assert store.delete_faceprint("p", instance_path=tmp_path) == 1

        for path in _all_database_files(tmp_path):
            blob = path.read_bytes()
            assert packed not in blob, (
                f"the faceprint survives in {path.name} while another connection is open -- "
                "which is the state a running app is in"
            )
            assert b"arcface-r100" not in blob, f"the model name survives in {path.name}"
    finally:
        holder.close()


def test_a_deferred_erase_is_reported_rather_than_silent(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A PASSIVE checkpoint can copy nothing and report no contention while doing it.

    This is the state the docs describe and the reason they no longer promise the bytes
    have gone. One ordinary open read transaction pins a snapshot, and measured,
    wal_checkpoint(PASSIVE) then returns (busy=0, log_frames=2, checkpointed=0) -- so
    busy is 0 and a busy-flag check would pass straight over it, while the packed vector
    and the model name are both still recoverable from the MAIN database file.

    The row is gone either way, which is why 1 is still the right answer. What must not
    happen is that it goes unsaid. Removing the checkpointed-versus-log_frames report
    from delete_faceprint makes this fail.
    """
    store.ensure_learner_database(tmp_path)
    connection = store.connect(tmp_path)
    try:
        connection.execute("INSERT INTO learners (id, display_name, created_at) VALUES ('p','P',0)")
        connection.commit()
    finally:
        connection.close()

    values = [0.125 * index for index in range(64)]
    store.save_faceprint("p", "arcface-r100", values, instance_path=tmp_path)
    packed = _faceprint_bytes(values)

    # Land the vector in the main file first, so what survives below survives THERE and
    # not merely in a companion the next checkpoint would truncate anyway.
    settle = store.connect(tmp_path)
    try:
        settle.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        settle.close()
    main = Path(store.learner_db_path_for_instance(tmp_path))
    assert packed in main.read_bytes(), "the vector never reached the main file, so this proves nothing"

    reader = sqlite3.connect(main)
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT count(*) FROM learners").fetchone()

        with caplog.at_level(logging.WARNING):
            assert store.delete_faceprint("p", instance_path=tmp_path) == 1

        assert "still in the write-ahead log" in caplog.text, "the erase was deferred and nothing said so"
        assert "0 of 2 frames checkpointed" in caplog.text, caplog.text
        assert packed in main.read_bytes(), "the premise of this test no longer holds: the bytes left the file anyway"
        assert str(tmp_path) not in caplog.text, "the path must not reach the log"
        assert "arcface-r100" not in caplog.text, "the model name must not reach the log"
    finally:
        reader.rollback()
        reader.close()

    # And it is temporary: once no reader is pinning a snapshot, the bytes go.
    closing = store.connect(tmp_path)
    try:
        closing.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        closing.close()
    blob = main.read_bytes()
    assert packed not in blob, "the vector survived a checkpoint with no reader holding it"
    assert b"arcface-r100" not in blob, "the model name survived a checkpoint with no reader"


def test_an_os_error_cannot_carry_the_household_directory_into_a_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The leak that made _log_safe an allow-list, pinned against its own return.

    ensure_learner_database's warning never interpolated the path -- and printed it
    anyway, because OSError embeds its filename in __str__ and _log_safe waved every
    class but UnicodeError straight through. Measured on a real blocked path, the line
    read: "The learner database is unavailable: [Errno 17] File exists:
    '/.../alice-smith-household/instance'". That is a household's name in a log file on
    a robot in their house, which is CWE-532 and the rule this module states about
    itself.

    Reverting _log_safe to render an OSError in full makes this fail.
    """
    household = tmp_path / "alice-smith-household"
    household.mkdir()
    # A FILE where the instance directory must be: mkdir then fails with FileExistsError,
    # and the filename it carries is the household's.
    blocked = household / "instance"
    blocked.write_text("")

    with caplog.at_level(logging.WARNING):
        result = store.ensure_learner_database(blocked)

    assert result.ready is False, "the premise is a database that could not be opened"
    assert "alice-smith-household" not in caplog.text, "the household's directory name reached a log"
    assert str(tmp_path) not in caplog.text, "the path reached a log"
    # Still diagnosable: the class and the errno say what an operator needs.
    assert "FileExistsError" in caplog.text
    assert "errno=17" in caplog.text


@pytest.mark.parametrize(
    ("exc", "must_not_contain", "must_contain"),
    [
        (FileNotFoundError(2, "No such file", "/home/alice-smith/db"), "alice-smith", "errno=2"),
        (PermissionError(13, "Denied", "/home/bob-jones/db"), "bob-jones", "errno=13"),
        (UnicodeEncodeError("utf-8", "carol\ud800", 5, 6, "surrogate"), "carol", "UnicodeEncodeError"),
        (ValueError("invalid literal for int() with base 10: 'dave-black'"), "dave-black", "ValueError"),
        (TypeError("'<' not supported between 'str' and 'int': 'erin-white'"), "erin-white", "TypeError"),
    ],
)
def test_log_safe_reduces_every_family_that_could_quote_a_value(
    exc: BaseException, must_not_contain: str, must_contain: str
) -> None:
    """The allow-list, exercised on the families it exists to stop.

    Each of these embeds a caller-supplied value in its own message. The old rule
    rendered all five in full; only the UnicodeError one was ever caught.
    """
    rendered = str(store._log_safe(exc))

    assert must_not_contain not in rendered, f"{type(exc).__name__} leaked its value"
    assert must_contain in rendered, "the rendering must still say what happened"


def test_log_safe_still_renders_the_families_an_operator_needs() -> None:
    """The other half: reducing everything to a class name would be its own defect.

    A guard that blinds the operator gets turned off. These four carry no caller value
    and are rendered in full, which is what keeps the allow-list affordable.
    """
    assert "UNIQUE constraint failed" in str(store._log_safe(sqlite3.IntegrityError("UNIQUE constraint failed: t.id")))
    assert "too large" in str(store._log_safe(OverflowError("Python int too large to convert to SQLite INTEGER")))
    assert "home" in str(store._log_safe(RuntimeError("Could not determine home directory")))
    assert "not a str" in str(store._log_safe(store._StoreRefusal("instance_path must be a path, not int, not a str")))
