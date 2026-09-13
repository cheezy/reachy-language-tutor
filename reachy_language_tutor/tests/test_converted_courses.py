"""The converted-lesson file holds a LIST of courses, and what that has to guarantee.

W40 regrouped `converted_lessons.json` from one course into a list of them, so that the
four advertised languages with no material can each be added without the ones already
there moving. Italian is still the only course shipped; everything here therefore works
against synthetic files fed through the real seam, because the alternative -- shipping a
fake second course to test with -- would put unreviewed content in front of a learner.

Two things are being defended.

* **A lesson takes its language and its course name from the course that OWNS it.**
  With one course in the file those were global facts and any reading of them was
  correct. With several, taking them from "the course" is a bug that a single-course
  file can never expose, so it is tested with two.
* **The file's frame is an allow-list.** `_converted_courses` states the one shape it
  accepts and refuses everything else, rather than sniffing for the shapes that were
  wrong last time. The old single-course file is the first thing that has to bounce off
  it -- a half-migrated file is the realistic accident, and it looks almost right.

Every test here monkeypatches `store._converted_lessons_json`, which is the file-reading
seam, so the real guard, the real parser and the real seeder all run. Patching any
reader above it would step over the code under test.
"""

import json
from pathlib import Path

import pytest

from reachy_language_tutor.learners import store


# Synthetic lessons must cite a language the catalog seeds (lessons.language_code is a
# foreign key to languages) and sit clear of the positions SEED_LESSONS already holds --
# es/fr/de/pt hold 1-6 and it holds 7-12, so 90 and 91 collide with nothing.
FREE_POSITION = 90
OTHER_FREE_POSITION = 91

# Long enough to satisfy the rights-length check in test_converted_lessons.py, so these
# helpers build a course that the whole suite would accept rather than only this file.
SYNTHETIC_RIGHTS = (
    "Synthetic course used only by the test suite. It exists to exercise the shape of the converted-lesson "
    "file and never reaches a database anybody learns from."
)
SYNTHETIC_SHA = "0" * 64


def _lesson(lesson_id: str, position: int, *, unit: str = "I", page: int = 1) -> dict[str, object]:
    """One minimally valid converted lesson: a turn, a note and one whole drill."""
    return {
        "id": lesson_id,
        "position": position,
        "title": "A synthetic lesson",
        "objective": "Exercise the seeder without teaching anybody anything.",
        "source": {"module": "Volume 1", "unit": unit, "page": page},
        "dialogue_title": None,
        "turns": [{"speaker": "A", "text": "Una battuta."}],
        "notes": ["A note that says something to a learner."],
        "drills": [{"kind": "repetition", "target_text": "una parola", "english_gloss": "a word"}],
    }


def _course(name: str, language_code: str, lessons: list[dict[str, object]]) -> dict[str, object]:
    """Build a course block carrying every fact the loader requires of one."""
    return {
        "name": name,
        "language_code": language_code,
        "rights": SYNTHETIC_RIGHTS,
        "source_sha256": SYNTHETIC_SHA,
        "lessons": lessons,
    }


def _file(*courses: dict[str, object]) -> str:
    """Serialize a file in the one shape the loader accepts."""
    return json.dumps({"_about": ["A synthetic file."], "courses": list(courses)})


def _seed_from(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, payload: str) -> store.EnsureResult:
    """Point the file seam at `payload` and run the real preparation over it."""
    monkeypatch.setattr(store, "_converted_lessons_json", lambda: payload)
    return store.ensure_learner_database(tmp_path)


def _rows(tmp_path: Path, sql: str, parameters: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
    """Read rows back from the seeded database, which is the only witness that counts."""
    connection = store.connect(tmp_path)
    try:
        return [tuple(row) for row in connection.execute(sql, parameters).fetchall()]
    finally:
        connection.close()


# ------------------------------------------------- a lesson belongs to one course


def test_each_lesson_takes_the_language_and_course_name_of_the_course_that_owns_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The whole point of the courses list, and the one thing one course cannot show.

    Two courses, two languages. If anything reads "the" course -- the first, or a single
    global block -- the second course's lesson is seeded under the first's language and
    the first's name, and the row says so.
    """
    payload = _file(
        _course("Course A", "de", [_lesson("de-synth-01", FREE_POSITION)]),
        _course("Course B", "pt", [_lesson("pt-synth-01", FREE_POSITION)]),
    )

    assert _seed_from(monkeypatch, tmp_path, payload).ready is True

    seeded = {
        str(lesson_id): (language_code, course)
        for lesson_id, language_code, course in _rows(
            tmp_path,
            "SELECT l.id, l.language_code, s.course FROM lessons l "
            "JOIN lesson_sources s ON s.lesson_id = l.id WHERE s.origin = 'converted_from_course' ORDER BY l.id",
        )
    }

    assert seeded == {
        "de-synth-01": ("de", "Course A"),
        "pt-synth-01": ("pt", "Course B"),
    }


def test_a_file_with_one_course_is_not_a_special_case(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A list of one goes through the same path as a list of five.

    Guards against somebody reintroducing a `len(courses) == 1` shortcut that reads the
    single course as a global block again.
    """
    payload = _file(_course("Only Course", "fr", [_lesson("fr-synth-01", FREE_POSITION)]))

    assert _seed_from(monkeypatch, tmp_path, payload).ready is True

    assert _rows(
        tmp_path,
        "SELECT l.language_code, s.course FROM lessons l JOIN lesson_sources s ON s.lesson_id = l.id WHERE l.id = ?",
        ("fr-synth-01",),
    ) == [("fr", "Only Course")]


def test_two_courses_may_name_the_same_language(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A language can have more than one source course, and each keeps its own name.

    A second volume of the same course, or a different course entirely, is a normal
    thing to add. Nothing about the file forbids it, so this records that it works.
    """
    payload = _file(
        _course("Volume 1", "it", [_lesson("it-synth-v1", FREE_POSITION)]),
        _course("Volume 2", "it", [_lesson("it-synth-v2", OTHER_FREE_POSITION)]),
    )

    assert _seed_from(monkeypatch, tmp_path, payload).ready is True

    assert _rows(
        tmp_path,
        "SELECT l.id, l.language_code, s.course FROM lessons l JOIN lesson_sources s ON s.lesson_id = l.id "
        "WHERE l.id LIKE 'it-synth-%' ORDER BY l.id",
    ) == [("it-synth-v1", "it", "Volume 1"), ("it-synth-v2", "it", "Volume 2")]


# -------------------------------------------------------------- the shape allow-list


@pytest.mark.parametrize("missing", ("name", "language_code", "rights", "source_sha256", "lessons"))
def test_a_course_missing_one_of_its_own_facts_is_refused_before_any_lesson_is_written(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, missing: str
) -> None:
    """Every fact a course owns is required, including the two the seeder never reads.

    `rights` and `source_sha256` are not used to write a row, so a loader that validated
    only what it indexes would let a course ship with no rights position and no record
    of the file it came from. `name` and `language_code` are worse than that: their
    absence raises KeyError inside the seeder, and KeyError is not in the tuple
    ensure_learner_database absorbs, so it would escape a function documented as never
    raising.
    """
    course = _course("Course A", "de", [_lesson("de-synth-01", FREE_POSITION)])
    del course[missing]

    result = _seed_from(monkeypatch, tmp_path, _file(course))

    assert result.ready is False
    assert result.error is not None
    assert "'source_sha256'" in result.error, "the refusal names the shape that is accepted"
    assert _rows(tmp_path, "SELECT id FROM lessons WHERE id = ?", ("de-synth-01",)) == []


def test_the_old_single_course_shape_is_refused_by_a_message_naming_the_shape_expected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The file this one replaced must bounce, and say what it should have been.

    Somebody restoring a backup, or a half-finished merge, produces exactly this. It is
    the shape that was correct yesterday, which is what makes it worth a named test.
    """
    old_shape = json.dumps(
        {
            "_about": ["The shape before W40."],
            "course": {
                "name": "Course A",
                "language_code": "de",
                "rights": SYNTHETIC_RIGHTS,
                "source_sha256": SYNTHETIC_SHA,
            },
            "lessons": [_lesson("de-synth-01", FREE_POSITION)],
        }
    )

    result = _seed_from(monkeypatch, tmp_path, old_shape)

    assert result.ready is False
    assert result.error is not None
    assert "'courses' is a list" in result.error
    assert _rows(tmp_path, "SELECT id FROM lessons WHERE id = ?", ("de-synth-01",)) == []


def test_a_half_migrated_file_is_refused_rather_than_seeding_the_part_it_understands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The dangerous one: a file that is right enough to look finished.

    `courses` present and correct, `lessons` left behind at the top level. A guard that
    sniffed for the old `course` key would accept this and silently seed a subset -- the
    exact failure an allow-list exists to prevent, so it is tested rather than assumed.

    The refusal also has to point somewhere. "this file's top level is dict" is true of
    the CORRECT file too, so it tells an operator nothing; counting the keys that do not
    belong names the fault while keeping the message assembled from a type name and an
    integer, which is what _StoreRefusal promises -- it is SafeToLog and rendered in
    full, so a key name out of the file has no business in it.
    """
    half_migrated = json.dumps(
        {
            "courses": [_course("Course A", "de", [_lesson("de-synth-01", FREE_POSITION)])],
            "lessons": [_lesson("de-synth-02", OTHER_FREE_POSITION)],
        }
    )

    result = _seed_from(monkeypatch, tmp_path, half_migrated)

    assert result.ready is False
    assert result.error is not None
    assert "1 unexpected key(s) beside it" in result.error
    assert "lessons" not in result.error.split(";")[-1], "the offending key name is not printed"
    assert _rows(tmp_path, "SELECT id FROM lessons WHERE id LIKE 'de-synth-%'") == []


def test_a_file_with_no_courses_is_permitted_and_seeds_nothing_converted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An empty list is how "this build ships no converted lessons" is said.

    Deliberately not an error: the shape is right, there is simply nothing in it.
    """
    assert _seed_from(monkeypatch, tmp_path, _file()).ready is True

    assert _rows(tmp_path, "SELECT lesson_id FROM lesson_sources WHERE origin = 'converted_from_course'") == []
    assert store._converted_lessons() == ()


def test_a_course_may_carry_no_lessons_without_stopping_the_courses_after_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An empty course is a shape question, not a content one, so it loads."""
    payload = _file(
        _course("Empty Course", "de", []),
        _course("Course B", "pt", [_lesson("pt-synth-01", FREE_POSITION)]),
    )

    assert _seed_from(monkeypatch, tmp_path, payload).ready is True

    assert _rows(tmp_path, "SELECT id FROM lessons WHERE id = ?", ("pt-synth-01",)) == [("pt-synth-01",)]


# ------------------------------------------------------------------ one transaction


def test_a_malformed_second_course_rolls_the_first_one_back_and_leaves_the_version_unmoved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Every course goes in under one transaction, or a robot keeps half a catalog.

    Course A is valid and is written first; course B carries a drill the STRICT schema
    refuses. The failure has to take course A back out with it and leave the version
    marker unmoved, so the whole seed re-runs on the next start rather than converging
    on a catalog nobody wrote. This is what would break if the seeder ever opened its
    own `with connection:`.
    """
    bad_lesson = _lesson("pt-synth-01", FREE_POSITION)
    # Half of one drill kind and half of another: the CHECK on lesson_drills refuses it.
    bad_lesson["drills"] = [{"kind": "cue_response", "target_text": "wrong half", "english_gloss": "wrong half"}]

    payload = _file(
        _course("Course A", "de", [_lesson("de-synth-01", FREE_POSITION)]),
        _course("Course B", "pt", [bad_lesson]),
    )

    result = _seed_from(monkeypatch, tmp_path, payload)

    assert result.ready is False
    assert result.error is not None and "CHECK constraint failed" in result.error
    assert _rows(tmp_path, "SELECT id FROM lessons WHERE id = ?", ("de-synth-01",)) == [], (
        "the course written before the failure was rolled back with it"
    )
    assert _rows(tmp_path, "SELECT value FROM schema_meta WHERE key = ?", (store.SEED_VERSION_KEY,)) == [], (
        "the version marker did not move, so the seed re-runs rather than converging half-done"
    )


def test_two_courses_colliding_on_a_position_take_the_whole_seed_down_loudly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The hazard this format change newly creates, and it is not absorbed anywhere.

    Before the courses list a second course could not exist, so two lessons could not
    contend for one place in a language. Now they can, and `UNIQUE (language_code,
    position)` is what stops it -- but `lessons` is inserted `ON CONFLICT(id) DO UPDATE`,
    which absorbs a clash on the ID and NOT one on that index. So the insert raises
    IntegrityError, and because the seed is one transaction the robot is left with no
    catalog at all rather than a merged one.

    That is the right failure -- a silently merged catalog would be worse -- but it
    happens during an unattended seed in somebody's house, which is why it is pinned
    here rather than left to be discovered. docs/converting-a-course.md Step 7 carries
    the operational rule: positions are contiguous per language ACROSS every course.
    """
    payload = _file(
        _course("Volume 1", "it", [_lesson("it-synth-a", FREE_POSITION)]),
        _course("Volume 2", "it", [_lesson("it-synth-b", FREE_POSITION)]),
    )

    result = _seed_from(monkeypatch, tmp_path, payload)

    assert result.ready is False
    assert result.error is not None
    assert "UNIQUE constraint failed: lessons.language_code, lessons.position" in result.error, (
        "the collision is named by the index it broke, not by a generic failure"
    )
    assert _rows(tmp_path, "SELECT id FROM lessons WHERE id LIKE 'it-synth-%'") == []
    assert _rows(tmp_path, "SELECT value FROM schema_meta WHERE key = ?", (store.SEED_VERSION_KEY,)) == []


# --------------------------------------------- the allow-list reaches into the lesson


@pytest.mark.parametrize(
    "break_it",
    (
        pytest.param(lambda lesson: lesson.pop("dialogue_title"), id="lesson-missing-dialogue_title"),
        pytest.param(lambda lesson: lesson.pop("source"), id="lesson-missing-source"),
        pytest.param(lambda lesson: lesson.pop("notes"), id="lesson-missing-notes"),
        pytest.param(lambda lesson: lesson["source"].pop("page"), id="source-missing-page"),
        pytest.param(lambda lesson: lesson.__setitem__("sneaky", "x"), id="lesson-with-an-extra-key"),
    ),
)
def test_a_lesson_whose_skeleton_is_wrong_is_refused_rather_than_raising_out_of_the_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, break_it: object
) -> None:
    """A missing lesson key used to escape as KeyError from a never-raises function.

    ensure_learner_database absorbs (sqlite3.Error, OSError, ValueError, TypeError,
    RuntimeError) and documents itself as never raising. KeyError is in none of those,
    so `lesson["source"]` on a lesson without one went straight out through the app.
    Guarding the course frame alone left this open one level down -- the sibling half of
    the same bug, which is the mistake this codebase is most prone to.
    """
    lesson = _lesson("de-synth-01", FREE_POSITION)
    break_it(lesson)  # type: ignore[operator]

    result = _seed_from(monkeypatch, tmp_path, _file(_course("Course A", "de", [lesson])))

    assert result.ready is False
    assert result.error is not None
    assert "every converted lesson must be an object holding exactly" in result.error
    assert _rows(tmp_path, "SELECT id FROM lessons WHERE id = ?", ("de-synth-01",)) == []


@pytest.mark.parametrize("key", ("turns", "notes", "drills"))
def test_a_lesson_list_given_as_a_string_is_refused_before_it_is_enumerated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, key: str
) -> None:
    """The silent one, and the reason the guard checks types and not just presence.

    `notes` given as "abc" is not a crash: it enumerates, and three rows reading "a",
    "b" and "c" land in a lesson a child is read aloud. No CHECK constraint can catch
    that, because each character is a perfectly valid note -- the schema sees three
    well-formed rows. Only requiring a list at the door stops it, which makes this a
    content-integrity guard rather than a tidiness one.
    """
    lesson = _lesson("de-synth-01", FREE_POSITION)
    lesson[key] = "abc"

    result = _seed_from(monkeypatch, tmp_path, _file(_course("Course A", "de", [lesson])))

    assert result.ready is False
    assert result.error is not None and f"the '{key}' of course 0, lesson 0 is str" in result.error
    assert _rows(tmp_path, "SELECT COUNT(*) FROM lesson_notes WHERE lesson_id = ?", ("de-synth-01",)) == [(0,)]


@pytest.mark.parametrize(
    ("break_it", "expected"),
    (
        pytest.param(
            lambda lesson: lesson["turns"][0].pop("speaker"),
            "turn 0 of course 0, lesson 0 is not a speaker and a text",
            id="turn-missing-speaker",
        ),
        pytest.param(
            lambda lesson: lesson["turns"][0].pop("text"),
            "turn 0 of course 0, lesson 0 is not a speaker and a text",
            id="turn-missing-text",
        ),
        pytest.param(
            lambda lesson: lesson["drills"][0].pop("kind"),
            "drill 0 of course 0, lesson 0 is not",
            id="drill-missing-kind",
        ),
        pytest.param(
            lambda lesson: lesson["drills"][0].__setitem__("english_glos", "typo"),
            "drill 0 of course 0, lesson 0 is not",
            id="drill-with-a-misspelt-key",
        ),
        pytest.param(
            lambda lesson: lesson["notes"].__setitem__(0, {"not": "a string"}),
            "note 0 of course 0, lesson 0 is dict",
            id="note-that-is-not-a-string",
        ),
        pytest.param(
            lambda lesson: lesson["turns"].__setitem__(0, "a bare string"),
            "turn 0 of course 0, lesson 0 is not a speaker and a text",
            id="turn-that-is-not-an-object",
        ),
    ),
)
def test_the_allow_list_reaches_the_leaves_and_not_just_the_lesson(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, break_it: object, expected: str
) -> None:
    """The same KeyError escape lived one level below the lesson, and this is it.

    Guarding the course and then the lesson closed two rungs of one ladder and left the
    third: `turn["speaker"]` and `drill["kind"]` are indexed by the seeder exactly as
    `lesson["source"]` was. This codebase's most expensive recurring defect is a fix
    applied to the member it was shown while a sibling kept the bug, and this parametrize
    is the sibling sweep for that class rather than one more example of it.

    A misspelt drill key is here for a different reason: the seeder reaches for the
    optional ones with `.get`, so `english_glos` would not crash -- it would silently
    write NULL where a gloss belonged, and the drill's CHECK would then blame the wrong
    thing. An exact permitted set is what makes that a refusal instead of a mystery.
    """
    lesson = _lesson("de-synth-01", FREE_POSITION)
    break_it(lesson)  # type: ignore[operator]

    result = _seed_from(monkeypatch, tmp_path, _file(_course("Course A", "de", [lesson])))

    assert result.ready is False
    assert result.error is not None and expected in result.error
    assert _rows(tmp_path, "SELECT id FROM lessons WHERE id = ?", ("de-synth-01",)) == []


@pytest.mark.parametrize(
    ("payload", "expected"),
    (
        pytest.param(json.dumps([{"courses": []}]), "this file's top level is list", id="top-level-is-an-array"),
        pytest.param(json.dumps("a string"), "this file's top level is str", id="top-level-is-a-string"),
        pytest.param(json.dumps({"courses": {}}), "its 'courses' is dict", id="courses-is-an-object"),
        pytest.param(json.dumps({"courses": "it"}), "its 'courses' is str", id="courses-is-a-string"),
    ),
)
def test_a_file_that_is_not_shaped_like_a_file_at_all_is_refused_by_its_type(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, payload: str, expected: str
) -> None:
    """The two outermost branches, which the other tests reach past on their way in.

    Valid JSON is not a valid file. These say so with a type name and nothing else --
    the value never appears, because _StoreRefusal is SafeToLog and rendered in full.
    """
    result = _seed_from(monkeypatch, tmp_path, payload)

    assert result.ready is False
    assert result.error is not None and expected in result.error
    assert _rows(tmp_path, "SELECT lesson_id FROM lesson_sources WHERE origin = 'converted_from_course'") == []


@pytest.mark.parametrize(
    ("field", "mutate"),
    (
        pytest.param("source page", lambda lesson: lesson["source"].__setitem__("page", 2**64), id="int-leaf"),
        pytest.param("position", lambda lesson: lesson.__setitem__("position", 2**64), id="position-leaf"),
        pytest.param("title", lambda lesson: lesson.__setitem__("title", 2**64), id="string-leaf"),
        pytest.param(
            "turn speaker", lambda lesson: lesson["turns"][0].__setitem__("speaker", 2**64), id="nested-string-leaf"
        ),
    ),
)
def test_an_integer_too_wide_for_sqlite_is_reported_rather_than_raised(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, field: str, mutate: object
) -> None:
    """ensure_learner_database says it never raises, and OverflowError made that false.

    sqlite3 raises OverflowError while BINDING an int outside the range a SQLite INTEGER
    holds -- before the database sees the statement -- and because it subclasses
    ArithmeticError it was in none of the classes this function absorbed. _READER_ABSORBS
    had already named it for exactly this reason; this function had not, which is the
    unswept-sibling shape D10 and D14 are recorded for.

    Parametrized across an int leaf and two string leaves deliberately: the escape is not
    confined to the fields that are supposed to be integers, so a guard scoped to
    'position' and 'page' would have repeated the member-not-class mistake. A huge int
    given where a title belongs escapes identically.
    """
    lesson = _lesson("de-synth-01", FREE_POSITION)
    mutate(lesson)  # type: ignore[operator]

    # The assertion is that this RETURNS rather than raising; an escape fails the test
    # by propagating, which is the failure this exists to catch.
    result = _seed_from(monkeypatch, tmp_path, _file(_course("Course A", "de", [lesson])))

    assert result.ready is False, f"a too-wide int in {field} must be reported, not raised"
    assert result.error is not None
    assert _rows(tmp_path, "SELECT id FROM lessons WHERE id = ?", ("de-synth-01",)) == []
    assert _rows(tmp_path, "SELECT value FROM schema_meta WHERE key = ?", (store.SEED_VERSION_KEY,)) == []
