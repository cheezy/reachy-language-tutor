"""Three fixes to the learner store that are about what reaches the database, and when.

* The id guards name the shape that is PERMITTED. Their predecessors refused
  whitespace and control characters and admitted everything else, and the invisible
  format characters were in "everything else": each bound cleanly, matched nothing,
  and came back as the silent answer that means "no such thing here".
* The consent-scope rebuild takes the write lock before it reads, so a second process
  upgrading at the same moment waits instead of failing the upgrade.
* An upgrade removes any faceprint whose learner holds no standing face-recognition
  consent -- and can never remove one whose learner does.
"""

from __future__ import annotations
import glob
import struct
import logging
import sqlite3
from pathlib import Path

import pytest

from reachy_language_tutor.learners import store


@pytest.fixture
def instance(tmp_path: Path) -> Path:
    """Prepare a learner database at a temporary instance path."""
    assert store.ensure_learner_database(tmp_path).ready is True
    return tmp_path


_INVISIBLE = ("​", "﻿", "­", "⁠", "‍")


# ------------------------------------------------------------------ the id shapes


@pytest.mark.parametrize("character", _INVISIBLE)
def test_a_lesson_id_carrying_an_invisible_character_is_refused_not_silently_absent(
    instance: Path, caplog: pytest.LogCaptureFixture, character: str
) -> None:
    """Each of these returned a SILENT None from both readers before the guard changed."""
    lesson_id = "es-01-greetings" + character
    with caplog.at_level(logging.WARNING):
        assert store.get_lesson(lesson_id, instance_path=instance) is None
        assert store.get_lesson_content(lesson_id, instance_path=instance) is None
        outcome = store.record_result("sample-learner", lesson_id, "partial", instance_path=instance)
    assert outcome.reason == "unknown_lesson"
    assert caplog.text.count("Could not read a lesson id") == 2
    assert "Could not record an attempt: the lesson id was" in caplog.text
    # And the real id beside it is untouched, so the refusal is about the character.
    assert store.get_lesson("es-01-greetings", instance_path=instance) is not None


@pytest.mark.parametrize("character", _INVISIBLE)
def test_a_language_code_carrying_an_invisible_character_is_refused_not_silently_untaught(
    instance: Path, caplog: pytest.LogCaptureFixture, character: str
) -> None:
    """get_progress's silent None means "not taught here"; these must not reach it."""
    with caplog.at_level(logging.WARNING):
        assert store.get_progress("sample-learner", "es" + character, instance_path=instance) is None
    assert "Could not read a language code" in caplog.text


def test_every_seeded_language_and_lesson_passes_the_readers_guards() -> None:
    """The guard and the catalog must agree, or a shipped lesson is unreachable.

    Walks both seed sources: the app-written tuples and every course in the converted
    file, each lesson under the course that owns it.
    """
    codes = [code for code, _ in store.SEED_LANGUAGES]
    lesson_ids = [row[0] for row in store.SEED_LESSONS]
    for course in store._converted_courses():
        codes.append(course["language_code"])
        lesson_ids.extend(lesson["id"] for lesson in course["lessons"])

    assert len(lesson_ids) == 53, "the catalog changed size; re-read what this test walks"
    assert [code for code in codes if store._cannot_be_a_catalog_code(code) is not None] == []
    assert [lesson for lesson in lesson_ids if store._cannot_name_a_lesson(lesson) is not None] == []


def test_the_seed_refuses_a_lesson_no_reader_would_look_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The writer side of the same agreement: an unreachable id is refused, not seeded.

    Refused before anything is written, so the database stays at no seed version and
    the next start tries again rather than carrying a lesson nobody can open.
    """
    bad = ("Es-99-Capitals", "es", 99, "Capitals", "A lesson whose id the readers refuse.")
    monkeypatch.setattr(store, "SEED_LESSONS", (*store.SEED_LESSONS, bad))

    result = store.ensure_learner_database(tmp_path)

    assert result.ready is False
    assert result.error is not None and "0 language code(s) and 1 lesson id(s)" in result.error
    connection = sqlite3.connect(store.learner_db_path_for_instance(tmp_path))
    try:
        assert connection.execute("SELECT count(*) FROM lessons").fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        (["sample-learner"], "list, which is not a str, an int or a float"),
        ({"id": 1}, "dict, which is not a str, an int or a float"),
        (True, "a bool, which is a flag rather than an identifier"),
        (2**63, "an int too large for SQLite to bind, so it can reach no stored id"),
        (float("nan"), "not a number, and sqlite3 binds NaN as NULL"),
        ("a\ud800b", "not encodable as UTF-8"),
    ],
)
def test_the_learner_id_guard_names_what_it_refused(value: object, reason: str) -> None:
    """The reason text reaches a log line, so it must name a shape and never the value."""
    assert store._cannot_name_a_learner(value) == reason


@pytest.mark.parametrize("value", ["sample-learner", "", "42", 42, 2**63 - 1, -(2**63), 3.5, float("inf")])
def test_the_learner_id_guard_permits_every_value_that_can_reach_a_stored_id(value: object) -> None:
    """The other direction: the lookups test_learner_store.py already pins as real."""
    assert store._cannot_name_a_learner(value) is None


# ------------------------------------------------ the rebuild takes the write lock first


def _consent_rebuild_statements() -> list[str]:
    sql = (Path(store.__file__).resolve().parent / "consent_scopes.v5.sql").read_text(encoding="utf-8")
    without_comments = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
    return [statement.strip() for statement in without_comments.split(";") if statement.strip()]


def test_a_second_writer_cannot_slip_in_between_the_rebuilds_first_read_and_its_first_write(
    tmp_path: Path,
) -> None:
    """The failure, reproduced deterministically rather than by racing processes.

    The rebuild's first statements are its BEGIN and a DROP IF EXISTS, which reads the
    schema. Under a deferred BEGIN, another connection could then commit a write, and
    the rebuild's CREATE failed at once with "database is locked" -- a stale snapshot
    cannot be waited out. Measured across processes: 5 of 72 concurrent upgrades of a
    version-4 database failed that way. Under IMMEDIATE the rebuild holds the write
    lock from its first statement, so the other writer is the one that waits.
    """
    assert store.ensure_learner_database(tmp_path).ready is True
    database = store.learner_db_path_for_instance(tmp_path)
    statements = _consent_rebuild_statements()

    rebuild = sqlite3.connect(database, isolation_level=None)
    other = sqlite3.connect(database, isolation_level=None, timeout=0.2)
    try:
        rebuild.execute(statements[0])
        rebuild.execute(statements[1])
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            other.execute("INSERT INTO schema_meta (key, value) VALUES ('another-writer', 'x')")
        # And the rebuild's own first write goes through, which is what failed before.
        rebuild.execute(statements[2])
        rebuild.execute("ROLLBACK")
    finally:
        rebuild.close()
        other.close()


# ------------------------------------------- faceprints nobody agreed to, on upgrade


def _learner_with_consent(instance: Path, name: str, scope: str) -> str:
    outcome = store.record_consent(
        name,
        scope=scope,
        statement_id="test.v1",
        statement_text="Wording used by the tests.",
        granted_by="the_person_themselves",
        granted_via="operator_at_the_robot",
        instance_path=instance,
    )
    assert outcome.recorded and outcome.learner_id is not None
    return outcome.learner_id


def _plant_faceprint(instance: Path, learner_id: str, marker: float) -> None:
    """Write a faceprint the way a version-3 database could hold one: past the gate."""
    connection = sqlite3.connect(store.learner_db_path_for_instance(instance))
    try:
        connection.execute(
            "INSERT INTO faceprints (learner_id, embedding_model, dimension, vector, created_at) "
            "VALUES (?, 'test-model', 4, ?, 0)",
            (learner_id, struct.pack("<4f", *([marker] * 4))),
        )
        connection.commit()
    finally:
        connection.close()


def _household(instance: Path) -> dict[str, bytes]:
    faceprints = store.get_enrolled_faceprints(instance_path=instance)
    assert faceprints is not None
    return {faceprint.learner_id: struct.pack("<4f", *faceprint.vector) for faceprint in faceprints}


def _rewind_to_version_4(instance: Path) -> None:
    connection = sqlite3.connect(store.learner_db_path_for_instance(instance))
    try:
        connection.execute("PRAGMA user_version = 4")
        connection.commit()
    finally:
        connection.close()


def test_an_upgrade_removes_every_faceprint_the_gate_would_refuse_and_keeps_every_one_it_would_accept(
    instance: Path,
) -> None:
    """Both directions at once, judged against the insert gate itself.

    Four learners: one with a standing face consent, one with only a local_profile
    consent, one whose face consent was withdrawn, and one with no consent row at all.
    Which faceprints survive the upgrade must be exactly the set save_faceprint would
    store -- asked of save_faceprint afterwards, so "standing" is the gate's meaning
    rather than a copy of it.
    """
    agreed = _learner_with_consent(instance, "Agreed Person", "face_recognition")
    assert store.save_faceprint(agreed, "test-model", [1.25] * 4, instance_path=instance).saved is True
    profile_only = _learner_with_consent(instance, "Profile Person", "local_profile")
    withdrawn = _learner_with_consent(instance, "Withdrawn Person", "face_recognition")
    never = "legacy-v3-learner"
    connection = sqlite3.connect(store.learner_db_path_for_instance(instance))
    try:
        connection.execute("UPDATE consents SET withdrawn_at = granted_at + 1 WHERE learner_id = ?", (withdrawn,))
        connection.execute(
            "INSERT INTO learners (id, display_name, created_at) VALUES (?, 'Legacy Person', 0)", (never,)
        )
        connection.commit()
    finally:
        connection.close()
    for marker, learner in ((2.5, profile_only), (3.75, withdrawn), (5.5, never)):
        _plant_faceprint(instance, learner, marker)
    before = _household(instance)
    assert set(before) == {agreed, profile_only, withdrawn, never}

    _rewind_to_version_4(instance)
    assert store.ensure_learner_database(instance).schema_applied is True

    after = _household(instance)
    assert after == {agreed: before[agreed]}, "a consented faceprint changed, or an unconsented one survived"

    # The gate's own verdict on each learner, as the oracle for "standing".
    for learner, expected in ((agreed, True), (profile_only, False), (withdrawn, False), (never, False)):
        verdict = store.save_faceprint(learner, "test-model", [9.0] * 4, instance_path=instance)
        assert verdict.saved is expected, learner

    # And the removed vectors left the files, as every other erasure's do.
    for marker in (2.5, 3.75, 5.5):
        needle = struct.pack("<4f", *([marker] * 4))
        for path in glob.glob(str(store.learner_db_path_for_instance(instance)) + "*"):
            assert needle not in Path(path).read_bytes(), (marker, Path(path).name)


def test_an_upgrade_of_a_household_where_everyone_agreed_removes_nothing(instance: Path) -> None:
    """The direction that matters most: a consented faceprint is never touched."""
    people = [_learner_with_consent(instance, f"Person {name}", "face_recognition") for name in "ABC"]
    for index, learner in enumerate(people):
        assert store.save_faceprint(learner, "test-model", [float(index)] * 4, instance_path=instance).saved
    before = _household(instance)

    _rewind_to_version_4(instance)
    assert store.ensure_learner_database(instance).schema_applied is True

    assert _household(instance) == before
