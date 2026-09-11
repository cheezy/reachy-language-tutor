"""Tests for the learner database schema, seeding, and its failure modes."""

import os
import json
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
    """bool subclasses int, so it slipped through the number arm and became "True".

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
