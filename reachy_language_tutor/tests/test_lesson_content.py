"""What a lesson is MADE of: the dialogue, the notes, the drills, and where it came from.

Everything here is shared catalog material. None of it belongs to a learner, none of it
may name one, and every test in this file runs without a learner id -- which is itself
the claim being made: adding five tables did not add a place for personal data.

Three things are proved by execution against a real database rather than by reading:

* **the constraints**, because a CHECK that reads as correct can still be satisfied by
  NULL -- the exact hole this schema's IS NOT NULL clauses exist to close, measured
  here rather than assumed;
* **the upgrade**, because these tables have to arrive on a robot that already has a
  database, and the only thing that carries them is a SCHEMA_VERSION bump re-running a
  script of IF NOT EXISTS statements;
* **the empty case**, because most of the seeded catalog still carries no content and
  has to keep working while the corpus is converted a unit at a time. Six Italian
  lessons have been converted since; the other twenty-four are still a title and an
  objective, and this file is largely about them.

Schema creation and seeding in general live in test_learner_schema.py; the query
interface for learners lives in test_learner_store.py.
"""

import re
import logging
import sqlite3
from pathlib import Path

import pytest

from reachy_language_tutor.learners import store
from reachy_language_tutor.learners.models import DRILL_KINDS, LESSON_ORIGINS


# A seeded lesson, named by id. Its position in SEED_LESSONS is not load-bearing here:
# what matters is that the catalog contains it, which the first test asserts.
LESSON = "es-01-greetings"
OTHER_LESSON = "fr-01-greetings"


@pytest.fixture
def instance(tmp_path: Path) -> Path:
    """Return a prepared learner database at a temporary instance path."""
    assert store.ensure_learner_database(tmp_path).ready is True
    return tmp_path


def _write(instance_path: Path, *statements: tuple[str, tuple[object, ...]]) -> None:
    """Run setup statements against the database, committing once."""
    connection = store.connect(instance_path)
    try:
        for sql, parameters in statements:
            connection.execute(sql, parameters)
        connection.commit()
    finally:
        connection.close()


def _refuses(instance_path: Path, sql: str, parameters: tuple[object, ...]) -> str:
    """Run one statement that must fail, and return the database's own message."""
    connection = store.connect(instance_path)
    try:
        with pytest.raises(sqlite3.Error) as raised:
            connection.execute(sql, parameters)
        return str(raised.value)
    finally:
        connection.rollback()
        connection.close()


_TURN = "INSERT INTO lesson_dialogue_turns (lesson_id, position, speaker, text) VALUES (?, ?, ?, ?)"
_NOTE = "INSERT INTO lesson_notes (lesson_id, number, text) VALUES (?, ?, ?)"
_DIALOGUE = "INSERT INTO lesson_dialogues (lesson_id, title) VALUES (?, ?)"
_DRILL = (
    "INSERT INTO lesson_drills (lesson_id, position, kind, target_text, english_gloss, cue, expected_response) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)"
)
_SOURCE = "INSERT INTO lesson_sources (lesson_id, origin, course, module, unit, page) VALUES (?, ?, ?, ?, ?, ?)"
_REPLACE_SOURCE = _SOURCE.replace("INSERT INTO", "INSERT OR REPLACE INTO")


# ------------------------------------------------------------------- the catalog


def test_the_lessons_these_tests_use_are_really_in_the_catalog(instance: Path) -> None:
    """Named by id rather than by position, so this file survives a reordered seed."""
    assert store.get_lesson(LESSON, instance_path=instance) is not None
    assert store.get_lesson(OTHER_LESSON, instance_path=instance) is not None


# --------------------------------------------------------------- what may be stored


def test_a_drill_of_each_kind_is_stored_and_read_back_whole(instance: Path) -> None:
    """The two kinds that are runnable as speech, each keeping its two halves apart."""
    _write(
        instance,
        (_DRILL, (LESSON, 1, "repetition", "buon giorno", "good morning", None, None)),
        (_DRILL, (LESSON, 2, "cue_response", None, None, "morning/Capitano", "Buon giorno, Signor Capitano.")),
    )

    drills = store.get_lesson_content(LESSON, instance_path=instance).drills

    assert [drill.kind for drill in drills] == ["repetition", "cue_response"]
    assert (drills[0].target_text, drills[0].english_gloss) == ("buon giorno", "good morning")
    assert (drills[0].cue, drills[0].expected_response) == (None, None)
    # The half that makes a cue-response drill checkable rather than merely sayable.
    assert drills[1].expected_response == "Buon giorno, Signor Capitano."
    assert (drills[1].target_text, drills[1].english_gloss) == (None, None)


@pytest.mark.parametrize(
    ("shape", "row"),
    [
        ("a repetition drill with no gloss", (LESSON, 1, "repetition", "buon giorno", None, None, None)),
        ("a repetition drill with no target", (LESSON, 1, "repetition", None, "good morning", None, None)),
        ("a cue-response drill with no answer", (LESSON, 1, "cue_response", None, None, "morning", None)),
        ("a cue-response drill with no cue", (LESSON, 1, "cue_response", None, None, None, "Buon giorno.")),
        ("a repetition drill carrying a cue", (LESSON, 1, "repetition", "a", "b", "morning", None)),
        ("a cue-response drill carrying a target", (LESSON, 1, "cue_response", "a", None, "c", "d")),
        ("a drill of both kinds at once", (LESSON, 1, "repetition", "a", "b", "c", "d")),
        ("a blank target", (LESSON, 1, "repetition", "   ", "good morning", None, None)),
        ("a blank expected response", (LESSON, 1, "cue_response", None, None, "morning", "  ")),
    ],
)
def test_a_drill_that_is_not_one_of_the_two_shapes_is_refused(
    instance: Path, shape: str, row: tuple[object, ...]
) -> None:
    """The half-filled drills, refused by the shape CHECK rather than by convention.

    Every one of the first four is a NULL case, and NULL is why this test exists at
    all: `length(trim(NULL)) > 0` is NULL rather than false, and a CHECK is satisfied
    by NULL exactly as readily as by true. Written the obvious shorter way -- without
    the IS NOT NULL beside each trim -- this constraint ACCEPTS a cue-response drill
    with no answer, which is a drill the tutor would start and could never mark. That
    was measured against a real table, not reasoned about.

    The assertion names the shape CHECK specifically, so a row refused for some other
    reason -- a foreign key, a duplicate position -- cannot pass this test for a reason
    it does not name.
    """
    message = _refuses(instance, _DRILL, row)

    assert "CHECK constraint failed" in message, message
    assert "kind = 'repetition'" in message, f"{shape} was refused by something other than the shape rule"


def test_a_drill_kind_nobody_has_defined_is_refused(instance: Path) -> None:
    """The allow-list, not a list of kinds somebody thought to forbid."""
    message = _refuses(instance, _DRILL, (LESSON, 1, "translation", "a", "b", None, None))

    assert "kind IN ('repetition', 'cue_response')" in message, message


def test_a_kind_added_to_the_vocabulary_alone_can_store_nothing(instance: Path) -> None:
    """Fail closed: naming a kind is not the same as saying what shape its rows take.

    The two CHECKs are separate on purpose. If a later change adds a kind to the first
    and forgets the second, every row of that kind is refused -- which is loud, and the
    opposite of a half-specified drill reaching a child as a question with no answer.

    Proved by building the same table with a third kind permitted and nothing else
    changed, rather than by reading the constraint.
    """
    schema = (Path(store.__file__).resolve().parent / "schema.sql").read_text(encoding="utf-8")
    body = schema.split("CREATE TABLE IF NOT EXISTS lesson_drills", 1)[1].split(";", 1)[0]
    widened = ("CREATE TABLE widened" + body).replace(
        "kind IN ('repetition', 'cue_response')",
        "kind IN ('repetition', 'cue_response', 'translation')",
        1,
    )
    # Standalone, so the foreign key does not need a lessons table to be here too.
    widened = widened.replace("REFERENCES lessons(id) ON DELETE CASCADE", "", 1)

    connection = sqlite3.connect(":memory:")
    try:
        connection.execute(widened)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO widened VALUES (?, ?, ?, ?, ?, ?, ?)",
                (LESSON, 1, "translation", "casa", "house", None, None),
            )
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("shape", "row"),
    [
        ("no module", (LESSON, "converted_from_course", "FSI Italian FAST", None, "1", 12)),
        ("no unit", (LESSON, "converted_from_course", "FSI Italian FAST", "I", None, 12)),
        ("no page", (LESSON, "converted_from_course", "FSI Italian FAST", "I", "1", None)),
        ("a blank module", (LESSON, "converted_from_course", "FSI Italian FAST", "  ", "1", 12)),
        ("page zero", (LESSON, "converted_from_course", "FSI Italian FAST", "I", "1", 0)),
        ("an original lesson citing a page", (LESSON, "written_for_this_app", "House catalog", None, None, 12)),
        ("an original lesson citing a unit", (LESSON, "written_for_this_app", "House catalog", None, "1", None)),
    ],
)
def test_provenance_that_could_not_be_followed_back_is_refused(
    instance: Path, shape: str, row: tuple[object, ...]
) -> None:
    """A citation is all four parts or it is not a citation.

    The first three are the NULL cases again, and they matter more here than anywhere:
    the whole point of recording provenance is that somebody can go and check a suspect
    line against the page it was read off. A converted lesson missing its unit records
    a source nobody can find, which is worse than recording none -- it reads like a
    check that was done.
    """
    message = _refuses(instance, _REPLACE_SOURCE, row)

    assert "CHECK constraint failed" in message, message
    assert "origin = 'written_for_this_app'" in message, f"{shape} was refused by the wrong rule"


def test_a_converted_lesson_records_all_four_parts(instance: Path) -> None:
    """The shape this table exists for, accepted and read back whole."""
    _write(instance, (_REPLACE_SOURCE, (LESSON, "converted_from_course", "FSI Italian FAST", "I", "1", 12)))

    source = store.get_lesson_content(LESSON, instance_path=instance).source

    assert source.origin == "converted_from_course"
    assert (source.course, source.module, source.unit, source.page) == ("FSI Italian FAST", "I", "1", 12)


def test_an_origin_nobody_has_defined_is_refused(instance: Path) -> None:
    """The allow-list again, on the other new vocabulary."""
    message = _refuses(instance, _REPLACE_SOURCE, (LESSON, "scraped_from_a_website", "Somewhere", None, None, None))

    assert "origin IN ('written_for_this_app', 'converted_from_course')" in message, message


@pytest.mark.parametrize(
    ("shape", "sql", "row"),
    [
        ("a turn at position zero", _TURN, (LESSON, 0, "Capitano", "Buon giorno.")),
        ("a turn with a blank speaker", _TURN, (LESSON, 1, "   ", "Buon giorno.")),
        ("a turn with no words", _TURN, (LESSON, 1, "Capitano", "  ")),
        ("a note numbered zero", _NOTE, (LESSON, 0, "A note.")),
        ("a blank note", _NOTE, (LESSON, 1, "   ")),
        ("an untitled dialogue", _DIALOGUE, (LESSON, "  ")),
    ],
)
def test_a_row_that_could_not_be_spoken_is_refused(
    instance: Path, shape: str, sql: str, row: tuple[object, ...]
) -> None:
    """Blank text and an unorderable position, refused by the table rather than tidied."""
    assert "CHECK constraint failed" in _refuses(instance, sql, row), shape


@pytest.mark.parametrize(
    ("shape", "sql", "rows"),
    [
        (
            "two turns at one position",
            _TURN,
            [(LESSON, 1, "Capitano", "Buon giorno."), (LESSON, 1, "Rossi", "Salve.")],
        ),
        ("two notes with one number", _NOTE, [(LESSON, 1, "First."), (LESSON, 1, "Also first.")]),
        (
            "two drills at one position",
            _DRILL,
            [
                (LESSON, 1, "repetition", "a", "b", None, None),
                (LESSON, 1, "repetition", "c", "d", None, None),
            ],
        ),
    ],
)
def test_order_within_a_lesson_cannot_tie(
    instance: Path, shape: str, sql: str, rows: list[tuple[object, ...]]
) -> None:
    """Ordered, not merely recorded: two rows cannot share a place in the sequence.

    The same reasoning as lessons UNIQUE (language_code, position). A tie would make
    "the third drill" mean two different things on two different reads, and the tutor
    runs these one at a time.
    """
    _write(instance, (sql, rows[0]))

    assert "UNIQUE constraint failed" in _refuses(instance, sql, rows[1]), shape


def test_content_belongs_to_a_lesson_that_exists(instance: Path) -> None:
    """No orphan content: a drill for no lesson is a drill nobody can ever run."""
    for sql, row in (
        (_DRILL, ("no-such-lesson", 1, "repetition", "a", "b", None, None)),
        (_TURN, ("no-such-lesson", 1, "A", "x")),
        (_NOTE, ("no-such-lesson", 1, "x")),
        (_DIALOGUE, ("no-such-lesson", "x")),
        (_SOURCE, ("no-such-lesson", "written_for_this_app", "House catalog", None, None, None)),
    ):
        assert "FOREIGN KEY constraint failed" in _refuses(instance, sql, row), sql


def test_removing_a_lesson_removes_everything_it_was_made_of(instance: Path) -> None:
    """Cascade from the lesson, so no content outlives the lesson it belongs to."""
    _write(
        instance,
        (_DIALOGUE, (LESSON, "Saluti")),
        (_TURN, (LESSON, 1, "Capitano", "Buon giorno.")),
        (_NOTE, (LESSON, 1, "A note.")),
        (_DRILL, (LESSON, 1, "repetition", "a", "b", None, None)),
        ("DELETE FROM lessons WHERE id = ?", (LESSON,)),
    )

    connection = store.connect(instance)
    try:
        remaining = {
            table: int(
                connection.execute(f"SELECT COUNT(*) FROM {table} WHERE lesson_id = ?", (LESSON,)).fetchone()[0]
            )
            for table in (
                "lesson_sources",
                "lesson_dialogues",
                "lesson_dialogue_turns",
                "lesson_notes",
                "lesson_drills",
            )
        }
    finally:
        connection.close()

    assert remaining == dict.fromkeys(remaining, 0)


def test_nothing_in_the_content_tables_can_name_a_learner(instance: Path) -> None:
    """The security claim, asked of the database rather than asserted in a comment.

    Lesson content is shared: every household gets the same rows. If one of these
    tables grew a learner column, content would become personal data that the learner
    tools' scoping rule has never heard of -- and deleting a household would stop being
    the single statement it is today.
    """
    connection = store.connect(instance)
    try:
        columns = {
            table: {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
            for table in (
                "lesson_sources",
                "lesson_dialogues",
                "lesson_dialogue_turns",
                "lesson_notes",
                "lesson_drills",
            )
        }
    finally:
        connection.close()

    for table, names in columns.items():
        assert not any("learner" in name for name in names), f"{table} names a learner"

    # And the rule the store enforces agrees: none of these is a personal table, so a
    # statement reading one needs no learner filter and must not be given one.
    assert set(store._PERSONAL_TABLES) == {"learners", "lesson_results", "faceprints", "consents"}


# ------------------------------------------------------------------- the reader


def test_a_lesson_reads_back_its_content_in_order(instance: Path) -> None:
    """Inserted out of order on purpose: the order comes from the database, not the insert."""
    _write(
        instance,
        (_DIALOGUE, (LESSON, "Saluti e presentazioni")),
        (_TURN, (LESSON, 2, "Rossi", "Buongiorno, Capitano.")),
        (_TURN, (LESSON, 1, "Capitano", "Buon giorno.")),
        (_NOTE, (LESSON, 2, "Second note.")),
        (_NOTE, (LESSON, 1, "First note.")),
        (_DRILL, (LESSON, 2, "cue_response", None, None, "morning", "Buon giorno.")),
        (_DRILL, (LESSON, 1, "repetition", "buon giorno", "good morning", None, None)),
    )

    content = store.get_lesson_content(LESSON, instance_path=instance)

    assert content.dialogue_title == "Saluti e presentazioni"
    assert [(turn.position, turn.speaker) for turn in content.turns] == [(1, "Capitano"), (2, "Rossi")]
    assert [note.number for note in content.notes] == [1, 2]
    assert [drill.position for drill in content.drills] == [1, 2]


def test_the_same_speaker_may_take_two_turns_in_a_row(instance: Path) -> None:
    """An edge case the task names. A speaker is a label, not a key."""
    _write(instance, (_TURN, (LESSON, 1, "Capitano", "Buon giorno.")), (_TURN, (LESSON, 2, "Capitano", "Come sta?")))

    turns = store.get_lesson_content(LESSON, instance_path=instance).turns

    assert [turn.speaker for turn in turns] == ["Capitano", "Capitano"]


def test_a_dialogue_of_one_turn_is_a_dialogue(instance: Path) -> None:
    """The other edge case the task names."""
    _write(instance, (_TURN, (LESSON, 1, "Capitano", "Buon giorno.")))

    assert len(store.get_lesson_content(LESSON, instance_path=instance).turns) == 1


def test_an_expected_response_may_be_a_whole_sentence_with_punctuation(instance: Path) -> None:
    """Stored as written. Deciding what counts as a correct answer is not the store's job.

    The task's edge case: an expected response the tutor must not require verbatim.
    This asserts the database keeps the sentence intact -- accents, comma and stop --
    so that whoever judges the answer later is judging what the source actually says.
    """
    expected = "Buon giorno, Signor Capitano. Come sta?"
    _write(instance, (_DRILL, (LESSON, 1, "cue_response", None, None, "morning/Capitano", expected)))

    assert store.get_lesson_content(LESSON, instance_path=instance).drills[0].expected_response == expected


def test_a_lesson_with_no_content_reads_back_empty_rather_than_raising(instance: Path) -> None:
    """The state every seeded lesson is in today, and will stay in for most of them.

    Empty is an answer. A caller must be able to tell "this lesson has not been
    converted yet" from "there is no such lesson" and from "the database is unreadable",
    and all three of those are tested here and below.
    """
    content = store.get_lesson_content(OTHER_LESSON, instance_path=instance)

    assert content is not None
    assert content.lesson.id == OTHER_LESSON
    assert content.lesson.title and content.lesson.objective, "the summary survives the content being absent"
    assert (content.turns, content.notes, content.drills) == ((), (), ())
    assert content.dialogue_title is None


def test_an_unconverted_lesson_still_says_where_it_came_from(instance: Path) -> None:
    """Provenance is not content: every seeded lesson has it from the first start."""
    source = store.get_lesson_content(OTHER_LESSON, instance_path=instance).source

    assert source is not None
    assert source.origin == "written_for_this_app"
    assert source.course == store.SEED_LESSON_COURSE
    assert (source.module, source.unit, source.page) == (None, None, None)


def test_a_lesson_whose_provenance_row_is_gone_reads_as_no_source(instance: Path) -> None:
    """Absent provenance is None, not an empty record that looks like a citation."""
    _write(instance, ("DELETE FROM lesson_sources WHERE lesson_id = ?", (LESSON,)))

    assert store.get_lesson_content(LESSON, instance_path=instance).source is None


def test_an_unknown_lesson_is_none(instance: Path) -> None:
    """No such lesson is an answer, and it is a different answer from empty content."""
    assert store.get_lesson_content("es-99-nonexistent", instance_path=instance) is None


def test_an_unreadable_store_is_none_and_says_so(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """The third meaning, told apart from the other two by the log.

    None alone cannot distinguish "no such lesson" from "could not look it up", which
    is why this asserts the message as well: a caller is told to ask
    store_is_available, and a reader that answered silently would give it nothing to
    ask about.
    """
    with caplog.at_level(logging.WARNING):
        assert store.get_lesson_content(LESSON, instance_path=tmp_path) is None

    assert "Could not read a lesson's content" in caplog.text


@pytest.mark.parametrize(
    "lesson_id",
    [None, 7, b"es-01-greetings", "", ["es-01-greetings"], " es-01-greetings", "es-01-greetings ", "es-01\ngreetings"],
)
def test_an_unusable_lesson_id_is_refused_rather_than_bound(
    instance: Path, lesson_id: object, caplog: pytest.LogCaptureFixture
) -> None:
    """The same values get_lesson refuses, refused here for the same reason.

    Each of these binds cleanly and matches nothing, so an unguarded reader answers a
    silent None indistinguishable from a lesson that genuinely does not exist. The log
    is what gives this teeth: without asserting it, the test passes against a reader
    with no guard at all.
    """
    with caplog.at_level(logging.WARNING):
        assert store.get_lesson_content(lesson_id, instance_path=instance) is None

    assert "Could not read a lesson id" in caplog.text


def test_both_lesson_readers_refuse_exactly_the_same_ids(instance: Path) -> None:
    """The sibling check, run rather than promised.

    get_lesson carried this guard inline and get_lesson_content would have been a
    second copy of it. Two copies is how a guard and its sibling drift apart -- the
    most repeated defect in this repository -- so they share one rule, and this asserts
    the sharing by running both over the same corpus.
    """
    corpus: list[object] = [
        None,
        7,
        3.5,
        b"es-01-greetings",
        "",
        " ",
        ["es-01-greetings"],
        " es-01-greetings",
        "es-01-greetings ",
        "es-01\ngreetings",
        "es-01\x00greetings",
        "es-01\x85greetings",
        "es-01-greetings",
        "no-such-lesson",
        OTHER_LESSON,
    ]

    for value in corpus:
        lesson = store.get_lesson(value, instance_path=instance)
        content = store.get_lesson_content(value, instance_path=instance)
        assert (lesson is None) == (content is None), f"the two readers disagree about {value!r}"
        if lesson is not None:
            assert content.lesson == lesson, "and when they agree it is there, it is the same lesson"


@pytest.mark.parametrize(
    "lesson_id",
    [None, 7, b"es-01-greetings", "", ["es-01-greetings"], " es-01-greetings", "es-01-greetings ", "es-01\ngreetings"],
)
def test_recording_an_attempt_refuses_the_same_lesson_ids_the_readers_do(
    instance: Path, lesson_id: object, caplog: pytest.LogCaptureFixture
) -> None:
    """The third function in the store that takes a lesson id, swept with the other two.

    Before the shared guard existed, these split three ways: some bound and missed
    (unknown_lesson), a list or dict failed to bind entirely and came back as
    storage_unavailable -- the robot reporting itself broken because a tool sent
    nonsense -- and none of them said anything. One rule, one code, and a log naming
    the shape.
    """
    connection = store.connect(instance)
    try:
        before = int(connection.execute("SELECT COUNT(*) FROM lesson_results").fetchone()[0])
    finally:
        connection.close()

    with caplog.at_level(logging.WARNING):
        outcome = store.record_result("sample-learner", lesson_id, "completed", instance_path=instance)

    assert outcome.recorded is False
    assert outcome.reason == "unknown_lesson", "a caller error, not a storage failure"
    assert "the lesson id was" in caplog.text

    connection = store.connect(instance)
    try:
        assert int(connection.execute("SELECT COUNT(*) FROM lesson_results").fetchone()[0]) == before
    finally:
        connection.close()


def test_an_id_the_driver_cannot_bind_is_a_caller_error_on_both_axes(
    instance: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A lone surrogate in either id names nothing, and neither is a broken store.

    Both halves of one class. The lesson id is refused before the bind, by the shared
    guard -- which had been extracted from a reader written before the encodability
    rule existed, so it was the only one of the three sibling refusals missing it. The
    learner id is caught AT the bind instead, deliberately: a test elsewhere pins this
    function as one of the two sinks where _log_safe stops UnicodeEncodeError naming
    the offending character and its index, which would put a fragment of a learner's
    id in a log file. Refusing earlier would have retired that proof silently.

    The third case is why the catch is placed where it is rather than around the whole
    body: a surrogate in the PATH is a storage problem and must keep saying so.
    """
    with caplog.at_level(logging.WARNING):
        learner = store.record_result("alice\ud800bob", LESSON, "completed", instance_path=instance)
        lesson = store.record_result("sample-learner", "es-01\ud800greetings", "completed", instance_path=instance)
        path = store.record_result("sample-learner", LESSON, "completed", instance_path=f"{instance}/\ud800")

    assert (learner.recorded, learner.reason) == (False, "unknown_learner")
    assert (lesson.recorded, lesson.reason) == (False, "unknown_lesson")
    assert (path.recorded, path.reason) == (False, "storage_unavailable"), (
        "a path the driver cannot bind is not a learner problem"
    )
    assert "ud800" not in caplog.text and "position" not in caplog.text, "no fragment of an id in the log"
    assert "UnicodeEncodeError" in caplog.text, "still diagnosable: the class, not the value"


def test_the_shared_guard_refuses_an_id_the_driver_cannot_bind(instance: Path) -> None:
    """The readers too, since the guard they share is where the fix went."""
    assert store.get_lesson("es-01\ud800greetings", instance_path=instance) is None
    assert store.get_lesson_content("es-01\ud800greetings", instance_path=instance) is None
    assert store._cannot_name_a_lesson("es-01\ud800greetings") == "not encodable as UTF-8"


def test_all_three_lesson_id_callers_agree_on_what_they_refuse(instance: Path) -> None:
    """The sweep itself, run rather than asserted in a comment.

    get_lesson, get_lesson_content and record_result all take a lesson id. A rule held
    by two of three is the shape of defect this repository has paid for most often, so
    the agreement is checked by running all three over one corpus.
    """
    corpus: list[object] = [None, 7, b"x", "", " es-01-greetings", "es-01-greetings\n", ["x"], LESSON, "no-such"]

    for value in corpus:
        refused = store._cannot_name_a_lesson(value) is not None
        assert (store.get_lesson(value, instance_path=instance) is None) or not refused
        assert (store.get_lesson_content(value, instance_path=instance) is None) or not refused
        outcome = store.record_result("sample-learner", value, "completed", instance_path=instance)
        if refused:
            assert outcome.reason == "unknown_lesson", f"record_result let {value!r} through"


def test_the_summary_the_tutor_speaks_is_unchanged_by_content(instance: Path) -> None:
    """get_progress still summarises a lesson in one line, whether or not it has content.

    The acceptance criterion in the task's own words, and the pitfall beside it: the
    one-line summary is a different job from running the lesson, so filling a lesson
    with dialogue and drills must not change what the tutor says when it is listing
    what comes next.
    """
    before = store.get_progress("sample-learner", "es", instance_path=instance)

    _write(
        instance,
        (_DIALOGUE, (LESSON, "Saluti")),
        (_TURN, (LESSON, 1, "Capitano", "Buon giorno.")),
        (_DRILL, (LESSON, 1, "repetition", "buon giorno", "good morning", None, None)),
        (_REPLACE_SOURCE, (LESSON, "converted_from_course", "FSI Italian FAST", "I", "1", 12)),
    )

    assert store.get_progress("sample-learner", "es", instance_path=instance) == before


# --------------------------------------------------------------- the seeded catalog


def test_every_seeded_lesson_records_where_it_came_from(instance: Path) -> None:
    """The acceptance criterion, asked of the database after a real first start."""
    connection = store.connect(instance)
    try:
        uncited = connection.execute(
            "SELECT lessons.id FROM lessons LEFT JOIN lesson_sources ON lesson_sources.lesson_id = lessons.id "
            "WHERE lesson_sources.lesson_id IS NULL"
        ).fetchall()
    finally:
        connection.close()

    assert [str(row["id"]) for row in uncited] == []


def test_the_seeded_provenance_names_only_lessons_that_exist(instance: Path) -> None:
    """A seed row for a lesson nobody ships would be refused by the database at start.

    Worth pinning in Python as well as in SQL: the foreign key turns this into a failed
    start that reports the whole store unusable, which blames the disk for what is
    really a two-tuple disagreement. This says which tuple is wrong.
    """
    seeded_lessons = {lesson_id for lesson_id, *_ in store.SEED_LESSONS}
    cited = {lesson_id for lesson_id, *_ in store.SEED_LESSON_SOURCES}

    assert cited - seeded_lessons == set(), "provenance for a lesson that is not in the catalog"
    assert seeded_lessons - cited == set(), "a lesson with no provenance"


def test_content_ships_for_the_converted_lessons_and_for_nothing_else(instance: Path) -> None:
    """Which lessons have content is a decision, so it is asserted rather than assumed.

    This replaces a test that asserted the catalog shipped NO content at all, which was
    true until units converted from a published course arrived. The claim worth keeping
    is the narrower one: content belongs to the lessons somebody converted, and a
    lesson acquiring content without a conversion behind it would be noticed here.
    """
    converted = {str(lesson["id"]) for lesson in store._converted_lessons()}
    assert converted, "the file ships lessons, or this test proves nothing"

    connection = store.connect(instance)
    try:
        with_content = {
            table: {
                str(row["lesson_id"])
                for row in connection.execute(f"SELECT DISTINCT lesson_id FROM {table}").fetchall()
            }
            for table in ("lesson_dialogue_turns", "lesson_notes", "lesson_drills")
        }
    finally:
        connection.close()

    for table, lesson_ids in with_content.items():
        assert lesson_ids == converted, f"{table} holds rows for a lesson nobody converted"


def test_corrected_provenance_reaches_a_robot_that_already_has_a_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Convergence, the way a correction actually ships.

    A robot in a house already has a database. The only thing that carries a corrected
    seed to it is the version bump, and _seed returns early without one -- so this
    rewinds the recorded version, changes the provenance, and asserts the change
    arrived rather than that the code looks like it would.
    """
    assert store.ensure_learner_database(tmp_path).ready is True

    corrected = tuple(
        (lesson_id, "converted_from_course", "FSI Italian FAST", "I", "1", 12) for lesson_id, *_ in store.SEED_LESSONS
    )
    monkeypatch.setattr(store, "SEED_LESSON_SOURCES", corrected)
    _write(tmp_path, ("UPDATE schema_meta SET value = ? WHERE key = ?", ("0", store.SEED_VERSION_KEY)))

    assert store.ensure_learner_database(tmp_path).ready is True

    source = store.get_lesson_content(LESSON, instance_path=tmp_path).source
    assert source.origin == "converted_from_course"
    assert source.page == 12

    connection = store.connect(tmp_path)
    try:
        expected = len(corrected) + len(store._converted_lessons())
        assert int(connection.execute("SELECT COUNT(*) FROM lesson_sources").fetchone()[0]) == expected, (
            "converged, not duplicated -- and the converted lessons carry their own provenance beside these"
        )
    finally:
        connection.close()


def test_a_learner_mid_course_keeps_their_progress_when_a_lesson_gains_content(instance: Path) -> None:
    """The edge case the task names: content arrives while somebody is partway through.

    Content is catalog data and results are learner data; they meet only at a lesson
    id. Nothing about converting a unit may disturb what a learner has finished, which
    is what the database is the source of truth for.
    """
    before = store.get_progress("sample-learner", "es", instance_path=instance)
    assert before.completed, "the sample learner has finished something to lose"

    _write(
        instance,
        (_DIALOGUE, (before.completed[0].id, "Saluti")),
        (_DRILL, (before.completed[0].id, 1, "repetition", "hola", "hello", None, None)),
        (_REPLACE_SOURCE, (before.completed[0].id, "converted_from_course", "FSI Spanish FAST", "I", "1", 7)),
    )

    after = store.get_progress("sample-learner", "es", instance_path=instance)

    assert [lesson.id for lesson in after.completed] == [lesson.id for lesson in before.completed]
    assert after.next_lesson == before.next_lesson
    assert after.attempts == before.attempts


# ------------------------------------------------------------- the upgrade itself


def _downgrade_to_the_previous_schema(instance_path: Path) -> None:
    """Turn a current database into what a robot that never saw this change has.

    Dropping the new tables and rewinding user_version is how the "already installed"
    case is reachable at all: the shipped schema.sql now creates them on any fresh
    start, so a fresh database can never be the old one.
    """
    connection = store.connect(instance_path)
    try:
        for table in ("lesson_drills", "lesson_notes", "lesson_dialogue_turns", "lesson_dialogues", "lesson_sources"):
            connection.execute(f"DROP TABLE {table}")
        connection.execute("PRAGMA user_version = 1")
        # The seed version goes back too, because that is the state a real installed
        # robot is in: it received schema 1 and seed 2 together. Rewinding only the
        # schema would leave _seed believing it had already written rows into tables
        # that no longer exist -- a state no robot has ever been in, and the test would
        # then be about an imaginary failure instead of the upgrade.
        connection.execute(
            "UPDATE schema_meta SET value = ? WHERE key = ?", (str(store.SEED_VERSION - 1), store.SEED_VERSION_KEY)
        )
        connection.commit()
    finally:
        connection.close()


def test_the_new_tables_reach_a_database_that_predates_them(tmp_path: Path) -> None:
    """The migration, executed. Without the SCHEMA_VERSION bump none of this arrives.

    Every statement in schema.sql is IF NOT EXISTS, so re-running it is how a new table
    reaches an installed robot -- and _apply_schema only re-runs it when the database's
    own user_version is behind. This asserts both halves: the tables appear, and the
    learner's existing history is still there afterwards.
    """
    assert store.ensure_learner_database(tmp_path).ready is True
    recorded = store.record_result("sample-learner", LESSON, "completed", instance_path=tmp_path)
    assert recorded.recorded is True

    _downgrade_to_the_previous_schema(tmp_path)

    connection = store.connect(tmp_path)
    try:
        present = {
            str(row["name"])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        assert "lesson_drills" not in present, "the downgrade has to actually remove them"
    finally:
        connection.close()

    result = store.ensure_learner_database(tmp_path)

    assert result.ready is True
    assert result.schema_applied is True, "the bump is what re-runs the script"
    content = store.get_lesson_content(LESSON, instance_path=tmp_path)
    assert content is not None and content.source is not None, "the new tables arrived and were seeded"

    progress = store.get_progress("sample-learner", "es", instance_path=tmp_path)
    assert LESSON in {lesson.id for lesson in progress.completed}, "and the learner's history survived"


def test_the_schema_version_moved_with_the_schema() -> None:
    """The bump itself, pinned so that a later table cannot arrive without one.

    A table added to schema.sql without moving SCHEMA_VERSION reaches every fresh
    install and no installed robot, and nothing reports a problem -- the same silent
    failure SEED_VERSION has its own guard for.
    """
    schema = (Path(store.__file__).resolve().parent / "schema.sql").read_text(encoding="utf-8")
    # Comments stripped first: this file explains itself at length, and one of those
    # explanations contains the words CREATE TABLE IF NOT EXISTS. Counting prose as a
    # table is how a structural check starts reporting on its own documentation.
    tables = re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", re.sub(r"--[^\n]*", " ", schema))

    assert store.SCHEMA_VERSION == 4, (
        "version 2 added lesson content; version 3 added faceprints; version 4 added consents"
    )
    assert sorted(tables) == [
        "consents",
        "faceprints",
        "languages",
        "learners",
        "lesson_dialogue_turns",
        "lesson_dialogues",
        "lesson_drills",
        "lesson_notes",
        "lesson_results",
        "lesson_sources",
        "lessons",
        "schema_meta",
    ], "a table arrived or left; SCHEMA_VERSION has to move with it"


def test_the_database_filename_did_not_move_with_the_schema() -> None:
    """The other half, and the one easier to get wrong.

    learners.v1.sqlite3 carrying user_version = 2 reads like a bug and is not: the
    filename changes only for a schema that CANNOT be migrated, because a new filename
    abandons every learner's progress. This change adds tables to the existing file,
    which is precisely the migratable case.
    """
    assert store.LEARNER_DB_FILENAME == "learners.v1.sqlite3"


# ------------------------------------------------------- vocabulary and its constraint


def _check_members(table: str, column: str) -> list[str]:
    """Return the values a column's IN constraint permits, read from schema.sql.

    A regex over the named CHECK rather than a split on a column's exact spacing: the
    older test of this shape splits on a literal run of spaces, so realigning a column
    would break it for a reason that has nothing to do with the vocabulary.
    """
    schema = (Path(store.__file__).resolve().parent / "schema.sql").read_text(encoding="utf-8")
    body = schema.split(f"CREATE TABLE IF NOT EXISTS {table}", 1)[1].split(";", 1)[0]
    match = re.search(rf"CHECK \(\s*{column} IN \(([^)]*)\)", body)
    assert match is not None, f"no IN constraint found for {table}.{column}"
    return re.findall(r"'([^']*)'", match.group(1))


@pytest.mark.parametrize(
    ("vocabulary", "table", "column"),
    [
        (DRILL_KINDS, "lesson_drills", "kind"),
        (LESSON_ORIGINS, "lesson_sources", "origin"),
    ],
)
def test_a_vocabulary_and_its_constraint_say_the_same_thing(
    vocabulary: tuple[str, ...], table: str, column: str
) -> None:
    """Both directions, because a subset test is not a mirror.

    Asserting only that every Python value appears in the CHECK would pass while the
    database permitted a third value no caller has ever heard of -- which is how a row
    the application cannot interpret gets stored. Equality, as sets and as counts.
    """
    permitted = _check_members(table, column)

    assert sorted(permitted) == sorted(vocabulary)
    assert len(permitted) == len(set(permitted)) == len(vocabulary)


def test_the_mirror_would_notice_a_constraint_nobody_updated() -> None:
    """The guard shown failing, since a mirror that cannot fail proves nothing."""
    permitted = _check_members("lesson_drills", "kind")

    assert sorted(permitted + ["translation"]) != sorted(DRILL_KINDS)
    assert sorted(permitted[:1]) != sorted(DRILL_KINDS)
