"""Learner database: the SQLite implementation of the learner store.

Every query in this application lives in this module. Nothing else may contain one --
that is what keeps the storage swappable. Two things actually enforce it, and neither
scans the rest of the codebase: _learner_scoped refuses at import time any statement
here that is not scoped to one learner, and a test AST-scans this module's own source
for an inline query that skipped the guard. A query written elsewhere is caught by
review, not by a check, so do not read this paragraph as a safety net.

**The interface** is get_profile, get_practised_languages, get_progress, get_lesson,
get_lesson_content, get_language_catalog, record_result and store_is_available, plus the
types in models.py. A hosted backend implements exactly these, and callers do not
change. Import them from the package (`reachy_language_tutor.learners`), never from
here. (This paragraph had fallen three functions behind the package before faceprints
were added; the package's `__all__` is the list a test actually checks.)

**Device-local, and not part of what a hosted backend implements**: get_faceprint,
save_faceprint and delete_faceprint, with Faceprint and SaveFaceprintOutcome. They are
here rather than in the list above because docs/plan.md says faceprints stay on the
robot -- a hosted backend must have no equivalent, and adding one would move face data
off the device.

**SQLite implementation detail**, which a hosted backend has no analogue for and
simply drops: connect, ensure_learner_database, EnsureResult, the SEED_* constants,
NEXT_LESSON_SQL, LEARNER_DB_FILENAME and learner_db_path_for_instance.

Connections are opened and closed inside a single call and never stored, cached, or
carried across an await. That is what makes these functions safe to call from async
tools: nothing is shared, so sqlite3's same-thread check can never fire.
"""

from __future__ import annotations
import os
import re
import json
import math
import stat
import time
import string
import struct
import logging
import sqlite3
import threading
from typing import Any
from pathlib import Path
from dataclasses import dataclass
from collections.abc import Sequence

from reachy_language_tutor.logging_safety import SafeToLog, log_safe
from reachy_language_tutor.learners.models import (
    OUTCOMES,
    Drill,
    Lesson,
    Faceprint,
    UsageNote,
    DialogueTurn,
    LessonSource,
    LessonAttempt,
    LessonContent,
    LearnerProfile,
    CatalogLanguage,
    LanguageProgress,
    PractisedLanguage,
    RecordResultOutcome,
    SaveFaceprintOutcome,
)


logger = logging.getLogger(__name__)

# Bumped when schema.sql gains a statement. _apply_schema re-runs the whole script on
# any database below this number, and every statement there is IF NOT EXISTS, so the
# bump is what carries a new TABLE out to a robot that already has a database. It
# cannot carry a new COLUMN on an existing table -- see the note in schema.sql.
# Version 2 added the lesson-content tables; version 3 added faceprints.
SCHEMA_VERSION = 3
# Bumped when the seed data changes -- including the converted lessons in
# converted_lessons.json, whose bytes are part of the fingerprint a test pins to this
# number. Version 3 gave every seeded lesson a provenance row; version 4 replaced the
# first six Italian lessons with units converted from a published course; version 5
# regrouped those lessons under a list of courses so a second language can be added,
# changing no lesson content at all.
# Version 6 carries the six converted Spanish Cycles and moves the six Spanish
# placeholders from 1-6 to 7-12 to make room for them, which is the same shape version
# 4 gave Italian. The tree passed through a 7 while those six landed one at a time;
# nothing shipped it -- HEAD was at 5 throughout -- so it was collapsed rather than
# recorded in SHIPPED_CATALOGS as a catalog some robot received. The only upgrade an
# installed robot takes is 5 to 6, and it is exercised end to end by
# test_converted_lessons.py::test_the_upgrade_every_installed_robot_will_actually_take.
SEED_VERSION = 6
LEARNER_DB_FILENAME = "learners.v1.sqlite3"
# The converted course material, beside this module and shipped as package data. Its
# bytes are part of the seeded catalog, so the shipped-catalog fingerprint covers the
# file itself -- editing a lesson without bumping SEED_VERSION fails the same test that
# catches editing SEED_LESSONS without bumping it.
CONVERTED_LESSONS_FILENAME = "converted_lessons.json"
SEED_VERSION_KEY = "seed_version"
# Which sample learners have ever been seeded. Kept separately from the learners
# table so the record survives the row being deleted -- see _seed().
SEEDED_LEARNERS_KEY = "seeded_learner_ids"

# A fixed instant rather than "now", so a freshly seeded database does not look
# like the sample learner practised at install time, and so tests can assert
# exact values. 2026-01-01T00:00:00Z in milliseconds.
_SEED_EPOCH_MS = 1767225600000
_DAY_MS = 86_400_000

_SCHEMA_LOCK = threading.Lock()

# Appended, never interleaved. get_language_catalog orders by name in SQL, so this
# tuple's order has no user-visible effect -- its only job is to feed the get_progress
# enum, whose FIRST entry must name a language a seeded learner has practised. Spanish
# stays first for that reason; see tools/get_progress.py.
SEED_LANGUAGES: tuple[tuple[str, str], ...] = (
    ("es", "Spanish"),
    ("fr", "French"),
    ("de", "German"),
    ("it", "Italian"),
    ("pt", "Portuguese"),
)

# (id, language_code, position, title, objective)
#
# This tuple's ORDER is not load-bearing, and that is a change worth recording rather
# than leaving for someone to rediscover. It used to be: _benign_args over in
# tests/test_tool_identity_boundary.py took enum[0] from the lesson_id enum that
# record_result declared, and that enum was pinned to this sequence. W16 deleted that
# tool -- the conversation no longer names a lesson at all -- so nothing reads the first
# entry here any more. The boundary suite's PINNED_LESSON names 'es-01-greetings' by id
# and its guard asserts catalog membership, not position. Verified by reordering this
# tuple and running the suite: only test_changing_the_seeded_catalog_requires_bumping_
# seed_version failed, and that fires on any catalog edit.
#
# What IS ordered is each lesson's `position` column, which is what decides the next
# lesson, and SEED_LANGUAGES above, whose first entry still has to name a language a
# seeded learner has practised.
#
# Italian starts at position 7 here, and that is not a gap: positions 1 to 6 belong to
# the lessons converted from a published course, which live in converted_lessons.json
# and are written by _seed_converted_lessons. Italian is the language that has real
# course material now, so the real material is what a learner meets first; these six
# are the objectives an AI wrote as a placeholder, which no teacher has reviewed. They
# keep their ids, so a learner who has already finished one still has.
SEED_LESSONS: tuple[tuple[str, str, int, str, str], ...] = (
    (
        "es-01-greetings",
        "es",
        7,
        "Greetings and goodbyes",
        "Greet someone, ask how they are, and say goodbye: hola, buenos días, ¿cómo estás?, adiós.",
    ),
    (
        "es-02-introductions",
        "es",
        8,
        "Introducing yourself",
        "Give your name and where you are from, and ask the same back: me llamo…, soy de…, ¿y tú?",
    ),
    (
        "es-03-numbers",
        "es",
        9,
        "Numbers one to twenty",
        "Count to twenty out loud and say your age and a phone number.",
    ),
    (
        "es-04-ordering-food",
        "es",
        10,
        "Ordering food and drink",
        "Order in a café and ask what something costs: quisiera…, ¿cuánto cuesta?",
    ),
    (
        "es-05-directions",
        "es",
        11,
        "Asking for directions",
        "Ask where a place is and follow a simple answer: ¿dónde está…?, a la derecha, a la izquierda.",
    ),
    (
        "es-06-daily-routine",
        "es",
        12,
        "Talking about your day",
        "Describe a typical day using present-tense verbs and times of day.",
    ),
    (
        "fr-01-greetings",
        "fr",
        1,
        "Greetings and politeness",
        "Greet someone and use bonjour, salut, s'il vous plaît, merci, au revoir.",
    ),
    (
        "fr-02-introductions",
        "fr",
        2,
        "Introducing yourself",
        "Give your name, age, and where you live: je m'appelle…, j'ai … ans, j'habite à…",
    ),
    ("fr-03-numbers", "fr", 3, "Numbers one to twenty", "Count to twenty out loud and say a price and a time."),
    (
        "fr-04-ordering-food",
        "fr",
        4,
        "At the café",
        "Order a drink and a pastry, then ask for the bill: je voudrais…, l'addition, s'il vous plaît.",
    ),
    (
        "fr-05-directions",
        "fr",
        5,
        "Getting around town",
        "Ask the way to the station and understand tout droit, à gauche, à droite.",
    ),
    (
        "fr-06-daily-routine",
        "fr",
        6,
        "Your daily routine",
        "Describe your morning with reflexive verbs: je me lève, je me prépare.",
    ),
    (
        "it-01-greetings",
        "it",
        7,
        "Greetings and goodbyes",
        "Greet someone, ask how they are, and say goodbye: ciao, buongiorno, come stai?, arrivederci.",
    ),
    (
        "it-02-introductions",
        "it",
        8,
        "Introducing yourself",
        "Give your name and where you are from, and ask the same back: mi chiamo…, sono di…, e tu?",
    ),
    ("it-03-numbers", "it", 9, "Numbers one to twenty", "Count to twenty out loud and say your age and a price."),
    (
        "it-04-ordering-food",
        "it",
        10,
        "At the bar",
        "Order a coffee and something to eat, then ask the price: vorrei…, quanto costa?",
    ),
    (
        "it-05-directions",
        "it",
        11,
        "Asking for directions",
        "Ask where a place is and follow a simple answer: dov'è…?, a destra, a sinistra.",
    ),
    (
        "it-06-daily-routine",
        "it",
        12,
        "Talking about your day",
        "Describe your morning with reflexive verbs: mi alzo, mi preparo.",
    ),
    (
        "de-01-greetings",
        "de",
        1,
        "Greetings and politeness",
        "Greet someone and use hallo, guten Tag, bitte, danke, auf Wiedersehen.",
    ),
    (
        "de-02-introductions",
        "de",
        2,
        "Introducing yourself",
        "Give your name, age, and where you live: ich heiße…, ich bin … Jahre alt, ich wohne in…",
    ),
    ("de-03-numbers", "de", 3, "Numbers one to twenty", "Count to twenty out loud and say a price and a time."),
    (
        "de-04-ordering-food",
        "de",
        4,
        "At the bakery",
        "Order a coffee and a pastry, then ask for the bill: ich hätte gern…, die Rechnung, bitte.",
    ),
    (
        "de-05-directions",
        "de",
        5,
        "Getting around town",
        "Ask the way to the station and understand geradeaus, links, rechts.",
    ),
    (
        "de-06-daily-routine",
        "de",
        6,
        "Your daily routine",
        "Describe your morning with separable verbs: ich stehe auf, ich ziehe mich an.",
    ),
    (
        "pt-01-greetings",
        "pt",
        1,
        "Greetings and goodbyes",
        "Greet someone, ask how they are, and say goodbye: olá, bom dia, como está?, adeus.",
    ),
    (
        "pt-02-introductions",
        "pt",
        2,
        "Introducing yourself",
        "Give your name and where you are from, and ask the same back: chamo-me…, sou de…, e tu?",
    ),
    ("pt-03-numbers", "pt", 3, "Numbers one to twenty", "Count to twenty out loud and say your age and a time."),
    (
        "pt-04-ordering-food",
        "pt",
        4,
        "At the café",
        "Order a coffee and a pastry, then ask the price: queria…, quanto custa?",
    ),
    (
        "pt-05-directions",
        "pt",
        5,
        "Asking for directions",
        "Ask where a place is and follow a simple answer: onde fica…?, à direita, à esquerda.",
    ),
    (
        "pt-06-daily-routine",
        "pt",
        6,
        "Talking about your day",
        "Describe your morning with reflexive verbs: levanto-me, preparo-me.",
    ),
)

# A deliberately neutral placeholder rather than a plausible human name, so nobody
# mistakes demo data for a real household member and a screenshot leaks nothing.
# What the thirty seeded lessons are: original material written for this app, not
# converted from anyone's course. The name a caller sees when it asks where a lesson
# came from.
SEED_LESSON_COURSE = "Reachy Mini language tutor starter catalog"

# (lesson_id, origin, course, module, unit, page)
#
# DERIVED from SEED_LESSONS rather than typed out beside it, and that is the point:
# "every lesson records its provenance" is then a property of the code rather than of
# thirty lines somebody has to keep in step. Adding a lesson above gives it a
# provenance row automatically; it cannot be forgotten, and the two lists cannot
# disagree about which lessons exist.
#
# module, unit and page are None because this material has no page to cite. The
# database refuses that combination for a converted lesson and refuses a page for an
# original one, so neither kind can be recorded as the other.
SEED_LESSON_SOURCES: tuple[tuple[str, str, str, None, None, None], ...] = tuple(
    (lesson_id, "written_for_this_app", SEED_LESSON_COURSE, None, None, None) for lesson_id, *_ in SEED_LESSONS
)

SEED_LEARNERS: tuple[tuple[str, str, int], ...] = (("sample-learner", "Sample Learner", _SEED_EPOCH_MS),)

# Seeded so both interesting progress states are demonstrable immediately:
# Spanish is part-way through with a partial attempt that must NOT advance the
# learner, and French has not been started at all.
SEED_RESULTS: tuple[tuple[str, str, str, int | None, int], ...] = (
    ("sample-learner", "es-01-greetings", "completed", 90, _SEED_EPOCH_MS + _DAY_MS),
    ("sample-learner", "es-02-introductions", "completed", 75, _SEED_EPOCH_MS + 2 * _DAY_MS),
    ("sample-learner", "es-03-numbers", "partial", 40, _SEED_EPOCH_MS + 3 * _DAY_MS),
)

# The lesson a learner should be offered next in one language: the lowest-positioned
# lesson with no 'completed' result. A 'partial' or 'skipped' attempt does not
# advance them. Deterministic because lessons.UNIQUE (language_code, position)
# forbids two lessons tying at the same position.
#
# Defined here as the single source of truth for the rule. The query interface that
# calls it is a later task; tests assert it directly against the seeded data.
NEXT_LESSON_SQL = """
SELECT l.id, l.position, l.title, l.objective
FROM lessons AS l
WHERE l.language_code = ?
  AND NOT EXISTS (
        SELECT 1 FROM lesson_results AS r
        WHERE r.lesson_id = l.id AND r.learner_id = ? AND r.outcome = 'completed'
      )
ORDER BY l.position
LIMIT 1
"""


@dataclass(frozen=True)
class EnsureResult:
    """Outcome of preparing the learner database."""

    path: Path
    ready: bool
    schema_applied: bool = False
    seeded: bool = False
    error: str | None = None


def learner_db_path_for_instance(instance_path: str | Path | None = None) -> Path:
    """Return the learner database path for this app instance.

    A value Path() cannot accept raises ValueError rather than the TypeError Path
    itself raises. That one word is what makes every public entry point absorb it:
    ValueError is already in each of their handlers, so all five answer with their own
    contract value -- None, an empty tuple, False, a reason code -- instead of three of
    them promising not to raise and then raising. TypeError deliberately keeps its
    ordinary meaning, so a genuine bug in a row converter still surfaces as one rather
    than being swallowed as a silent absence.
    """
    if instance_path is not None:
        refusal = _cannot_be_a_path(instance_path)
        if refusal is not None:
            raise _StoreRefusal(f"instance_path must be a path, not {refusal}")
        return Path(instance_path).expanduser() / LEARNER_DB_FILENAME

    data_home = os.getenv("XDG_DATA_HOME")
    data_root = Path(data_home).expanduser() if data_home else Path.home() / ".local" / "share"
    return data_root / "reachy_language_tutor" / LEARNER_DB_FILENAME


def _converted_lessons_json() -> str:
    """Return the bundled converted-lesson content as text.

    Read from the file at runtime for the same reason schema.sql is: it ships as
    package data, so it is a file on disk rather than a Python literal, and a wheel
    that failed to include it must fail loudly here rather than seed an empty catalog.
    """
    return (Path(__file__).resolve().parent / CONVERTED_LESSONS_FILENAME).read_text(encoding="utf-8")


# What this file may be, stated as the shape that is ACCEPTED. A list of the ways a
# file can be wrong is only ever as complete as the last person to read one, so this
# names the single shape that loads and refuses everything else with the same sentence.
# Widening it -- a sixth key on a course, a second key beside 'courses' -- means editing
# these constants, which is a deliberate act somebody can be asked about.
_CONVERTED_FILE_KEYS = frozenset({"_about", "courses"})
_CONVERTED_COURSE_KEYS = frozenset({"name", "language_code", "rights", "source_sha256", "lessons"})
_CONVERTED_LESSON_KEYS = frozenset(
    {"id", "position", "title", "objective", "source", "dialogue_title", "turns", "notes", "drills"}
)
_CONVERTED_SOURCE_KEYS = frozenset({"module", "unit", "page"})
_CONVERTED_LESSON_LISTS = ("turns", "notes", "drills")
_CONVERTED_TURN_KEYS = frozenset({"speaker", "text"})
# 'kind' is the only one the seeder indexes; the rest it reaches for with .get, so a
# drill may legitimately carry a subset. Naming the whole permitted set anyway is what
# stops a misspelt 'english_glos' being silently dropped into a NULL column.
_CONVERTED_DRILL_KEYS = frozenset({"kind", "target_text", "english_gloss", "cue", "expected_response"})
_CONVERTED_FILE_SHAPE = (
    "the converted-lesson file must be a JSON object whose 'courses' is a list, where every course is an "
    "object holding exactly 'name', 'language_code', 'rights', 'source_sha256' and a 'lessons' list; an "
    "optional '_about' may sit beside 'courses', and nothing else may"
)
_CONVERTED_LESSON_SHAPE = (
    "every converted lesson must be an object holding exactly 'id', 'position', 'title', 'objective', "
    "'dialogue_title', a 'source' of 'module', 'unit' and 'page', and lists for 'turns', 'notes' and 'drills' "
    "-- where every turn is a 'speaker' and a 'text', every note is a string, and every drill has a 'kind'"
)


def _converted_courses() -> tuple[Any, ...]:
    """Return every converted course in file order, each still owning its own lessons.

    This is the function that answers "which course does this lesson belong to", and it
    answers it structurally: you never hold a lesson without its course, because you
    reach the lesson THROUGH the course. That is what lets a second language be added to
    the file -- the language code and the course name are per-course facts, and anything
    writing them must take them from the course that owns the lesson it is writing.

    The shape check here is deliberately wider than this module's usual habit of
    validating only what it indexes, and what it adds is the file's SKELETON -- which
    keys exist -- while what a value may CONTAIN stays the STRICT tables' business. Two
    concrete failures are why it goes as far as it does, and both were reproduced rather
    than reasoned about:

    * A missing key raises KeyError deep in the seeder, and KeyError is not in the tuple
      ensure_learner_database absorbs, so it escapes a function documented as never
      raising. True of a course missing 'name', and equally of a lesson missing 'source'
      or a 'source' missing 'page' -- which is why the check descends into the lesson
      rather than stopping at the course. Guarding the course alone was the "fix the
      class, not the member" mistake this codebase keeps paying for.
    * A key of the wrong TYPE can seed silently. 'notes' given as the string "abc"
      enumerates to three per-character notes and lands three rows of content nobody
      wrote in a lesson a child is read aloud. Requiring a list is what stops that, and
      no CHECK constraint can, because each character is a perfectly valid note.

    Requiring 'rights' and 'source_sha256', which the seed never reads, is the same
    argument in its quietest form: without them a course seeds with no rights position
    and no record of the file it came from.
    """
    parsed = json.loads(_converted_lessons_json())
    if not isinstance(parsed, dict):
        raise _StoreRefusal(f"{_CONVERTED_FILE_SHAPE}; this file's top level is {type(parsed).__name__}")
    if "courses" not in parsed:
        raise _StoreRefusal(f"{_CONVERTED_FILE_SHAPE}; this file has no 'courses'")
    if not _CONVERTED_FILE_KEYS.issuperset(parsed):
        # Counted rather than named. The realistic accident is a half-migrated file --
        # 'courses' added, 'lessons' left behind at the top level -- and "this file's
        # top level is dict" is a true sentence about the CORRECT file too, so it points
        # at nothing. A count points at the fault; the key names stay out, because
        # _StoreRefusal is SafeToLog and is rendered in full wherever it is logged.
        unexpected = len(set(parsed) - _CONVERTED_FILE_KEYS)
        raise _StoreRefusal(f"{_CONVERTED_FILE_SHAPE}; this file has {unexpected} unexpected key(s) beside it")

    courses = parsed["courses"]
    if not isinstance(courses, list):
        raise _StoreRefusal(f"{_CONVERTED_FILE_SHAPE}; its 'courses' is {type(courses).__name__}")

    for index, course in enumerate(courses):
        if not isinstance(course, dict) or set(course) != _CONVERTED_COURSE_KEYS:
            raise _StoreRefusal(f"{_CONVERTED_FILE_SHAPE}; course {index} is not")
        if not isinstance(course["lessons"], list):
            raise _StoreRefusal(f"{_CONVERTED_FILE_SHAPE}; course {index} has a 'lessons' that is not a list")

        for position, lesson in enumerate(course["lessons"]):
            _refuse_unless_lesson_shaped(lesson, index, position)

    # A course's NAME is what its lessons are seeded under and what the approved-unit
    # control is keyed by, so two blocks sharing a name is not a tidiness problem: a
    # course copied from another and left with the template's name inherits every unit
    # approval a person granted the original, and unreviewed material reaches a learner
    # under a name somebody vouched for. Measured on the real file before this check
    # existed: a clone of the Italian block with language_code 'es' seeded cleanly and
    # passed both directions of that control.
    names = [course["name"] for course in courses]
    if len(set(names)) != len(names):
        raise _StoreRefusal(f"{_CONVERTED_FILE_SHAPE}; two courses share a name, and a name has to identify one")

    # Lesson ids are the primary key the seeder upserts on, so a duplicate across two
    # courses silently overwrites rather than colliding -- the second course's lesson
    # wins and the first's disappears with no error anywhere.
    lesson_ids = [str(lesson["id"]) for course in courses for lesson in course["lessons"]]
    if len(set(lesson_ids)) != len(lesson_ids):
        raise _StoreRefusal(f"{_CONVERTED_FILE_SHAPE}; two lessons share an id, and an id has to identify one")

    return tuple(courses)


def _refuse_unless_lesson_shaped(lesson: Any, course_index: int, lesson_index: int) -> None:
    """Refuse a lesson whose skeleton is not the one the seeder walks.

    Split out so the nesting in _converted_courses stays readable, and it goes all the
    way down to the leaves rather than stopping at the lesson's own keys. Stopping short
    is what this guard was written to fix and then repeated one level lower: a turn
    without 'speaker' raises KeyError exactly as a lesson without 'source' did.

    The line it does NOT cross is CONTENT. Whether a drill is a whole one of its kind,
    whether a string is blank, whether a kind is one the tutor can run -- all of that is
    enforced by CHECK constraints in schema.sql, and restating it here would give the
    two somewhere to disagree. What is checked here is only what SQLite cannot see: a
    key that is absent before any statement runs, and a value whose type would enumerate
    into rows nobody wrote.
    """
    where = f"course {course_index}, lesson {lesson_index}"

    if not isinstance(lesson, dict) or set(lesson) != _CONVERTED_LESSON_KEYS:
        raise _StoreRefusal(f"{_CONVERTED_LESSON_SHAPE}; {where} is not")

    source = lesson["source"]
    if not isinstance(source, dict) or set(source) != _CONVERTED_SOURCE_KEYS:
        raise _StoreRefusal(f"{_CONVERTED_LESSON_SHAPE}; the 'source' of {where} is not")

    for key in _CONVERTED_LESSON_LISTS:
        if not isinstance(lesson[key], list):
            raise _StoreRefusal(f"{_CONVERTED_LESSON_SHAPE}; the '{key}' of {where} is {type(lesson[key]).__name__}")

    for index, turn in enumerate(lesson["turns"]):
        if not isinstance(turn, dict) or set(turn) != _CONVERTED_TURN_KEYS:
            raise _StoreRefusal(f"{_CONVERTED_LESSON_SHAPE}; turn {index} of {where} is not a speaker and a text")

    for index, note in enumerate(lesson["notes"]):
        if not isinstance(note, str):
            raise _StoreRefusal(f"{_CONVERTED_LESSON_SHAPE}; note {index} of {where} is {type(note).__name__}")

    for index, drill in enumerate(lesson["drills"]):
        if not isinstance(drill, dict) or "kind" not in drill or not _CONVERTED_DRILL_KEYS.issuperset(drill):
            raise _StoreRefusal(f"{_CONVERTED_LESSON_SHAPE}; drill {index} of {where} is not")


def _converted_lessons() -> tuple[Any, ...]:
    """Return the converted lessons as plain dictionaries, in file order.

    A FLAT view across every course, which makes it the wrong function for anything that
    writes a language code or a course name: a lesson returned here is detached from the
    course that owns it, and with more than one course in the file there is no longer a
    single right answer to "which course was that". _seed_converted_lessons walks
    _converted_courses() for exactly that reason. This view is still the right one for
    asking what the catalog contains, which is what its callers do.

    Validation is _converted_courses' business, not this function's: the single
    statement below calls it, so every skeleton check runs before a lesson is returned
    here. What the content may CONTAIN is the database's business -- every string lands
    in a STRICT table behind CHECK constraints that refuse a blank line, an unknown
    drill kind or a half-filled drill -- and duplicating those rules in Python would
    give the two somewhere to disagree. A file that fails either check fails the seed
    transaction, which ensure_learner_database reports rather than raises.

    Typed `Any` rather than `dict[str, object]` for that same reason, and it is a
    deliberate choice rather than a shrug: `object` would make every `lesson["turns"]`
    and `source["page"]` a type error under the strict checking this package declares,
    and the honest fix is not thirty isinstance calls restating constraints the database
    already enforces -- it is to say that the shape of this file is checked by the schema
    it is loaded into.
    """
    return tuple(lesson for course in _converted_courses() for lesson in course["lessons"])


def _schema_sql() -> str:
    """Read the bundled DDL.

    Uses a path relative to this module rather than importlib.resources. Reachy Mini
    apps install unzipped, so the simpler form is sufficient; importlib.resources
    would be the zip-safe alternative if that ever changes. The file only ships
    because pyproject.toml's [tool.setuptools.package-data] lists learners/*.sql --
    without that entry this works from a source checkout and fails in a built wheel.
    """
    return (Path(__file__).resolve().parent / "schema.sql").read_text(encoding="utf-8")


def connect(instance_path: str | Path | None = None, *, create: bool = False) -> sqlite3.Connection:
    """Open a connection to the learner database.

    With create=False a missing database raises sqlite3.OperationalError instead of
    silently materialising an empty file, which is what plain sqlite3.connect does.
    Foreign keys are enabled here because SQLite does not enforce them otherwise, and
    this is the only place the database is opened.
    """
    # resolve() before as_uri(): a relative instance_path would otherwise raise
    # ValueError out of as_uri() before SQLite is ever consulted, which is not the
    # failure this function documents.
    path = learner_db_path_for_instance(instance_path).resolve()
    if create:
        connection = sqlite3.connect(path)
    else:
        connection = sqlite3.connect(f"{path.as_uri()}?mode=rw", uri=True)

    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    # Zero a page when it is freed, rather than returning it to the freelist with its
    # contents intact. Without this a deleted faceprint's bytes stay in the file:
    # measured, the packed float32 vector and the model name were both still
    # byte-recoverable after delete_faceprint returned 1 and a wal_checkpoint(TRUNCATE).
    #
    # ON rather than FAST, and the choice was measured rather than argued. Both scrub
    # this case; over 300 write-and-delete cycles in WAL with synchronous=NORMAL the
    # three settings came out at 5.0 ms (OFF), 4.9 ms (FAST) and 4.9 ms (ON) as a TOTAL
    # across all 300 -- 0.017, 0.016 and 0.016 ms per cycle, which is indistinguishable.
    # The unit basis is stated because an earlier version of this comment gave a bare
    # figure that read as per-operation and was not.
    #
    # The write amplification this pragma is known for needs a delete volume this app
    # does not have, so the stronger guarantee costs nothing here. Measured on dev
    # hardware (Mac, SSD), not on the Wireless model's storage -- re-measure there if
    # this choice is ever load-bearing.
    connection.execute("PRAGMA secure_delete = ON")
    _restrict_permissions(path)
    return connection


# The mode the learner database and its companions are kept at. Owner-only, because the
# file holds face templates for a household: the threat is another local account, an
# unencrypted backup, or the SD card out of a Reachy Wireless -- none of which goes
# through the learner-scoping guard at all.
_OWNER_ONLY = 0o600

# Which permission failures this process has already reported, as (file, error type)
# pairs. A set rather than a single flag, because one boolean covering every path and
# every error type silences the SECOND distinct failure: measured, a real chmod failure
# on a different database at a different path, after the flag was set, logged nothing at
# all. Keyed per file so a first failure on a new one still speaks, and per error type
# so a read-only mount turning into a permissions error is not mistaken for a repeat.
#
# Paths live in this set but never leave it -- it is de-duplication state, not a log.
_PERMISSION_FAILURES_REPORTED: set[tuple[str, str]] = set()


def _restrict_permissions(path: Path) -> None:
    """Make the database and its WAL companions owner-only, reporting any failure.

    Applied on EVERY connect, not only on create, so a database written by an earlier
    version at 0644 is tightened the next time the app opens it rather than staying
    readable forever.

    The -wal and -shm files matter as much as the main one and are easy to forget: a
    0600 database beside a 0644 write-ahead log protects nothing, because the WAL holds
    the pages that have not been checkpointed yet.

    Never raises -- every caller is a store function that promises not to. But it does
    not swallow either: a filesystem that cannot chmod is reported, because an
    unreported failure to tighten permissions on biometric data is worse than a visible
    one. The log line carries the error type only; the path can name a person's home
    directory and the no-PII rule covers this module.
    """
    # -journal as well as the WAL pair. PRAGMA journal_mode = WAL can fail to take on a
    # filesystem without shared memory, and SQLite then falls back to a rollback journal
    # at <db>-journal -- which holds freed pages exactly as the WAL does, and which a
    # loop naming only the two WAL suffixes would leave world-readable.
    for suffix in ("", "-wal", "-shm", "-journal"):
        candidate = Path(f"{path}{suffix}")
        try:
            if candidate.exists() and stat.S_IMODE(candidate.stat().st_mode) != _OWNER_ONLY:
                candidate.chmod(_OWNER_ONLY)
        except OSError as exc:
            # Once per (file, error type), not once per connect. Every store read opens
            # its own connection, so a database this process can never chmod -- owned by
            # another account, or on a read-only mount -- would otherwise warn several
            # times per conversation turn, and a warning repeating that often is one an
            # operator filters out, taking the real signal with it. The pitfall asks
            # that the failure not be SWALLOWED; it is reported, once per thing that
            # failed rather than once per process.
            already_reported = (str(candidate), type(exc).__name__)
            if already_reported not in _PERMISSION_FAILURES_REPORTED:
                _PERMISSION_FAILURES_REPORTED.add(already_reported)
                logger.warning(
                    "Could not restrict permissions on %s%s: %s (further failures on this file are suppressed)",
                    LEARNER_DB_FILENAME,
                    suffix,
                    type(exc).__name__,
                )
        else:
            # A file that chmods cleanly forgets its past failures, so a mount that was
            # read-only for a while and then recovered reports again if it recurs --
            # rather than staying silent for the life of the process.
            _PERMISSION_FAILURES_REPORTED.difference_update(
                {key for key in _PERMISSION_FAILURES_REPORTED if key[0] == str(candidate)}
            )


def _apply_schema(connection: sqlite3.Connection) -> bool:
    """Apply the DDL when the database predates the current schema version."""
    current: int = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if current >= SCHEMA_VERSION:
        return False

    connection.executescript(_schema_sql())
    # Pragmas cannot take bound parameters, so this is the one unavoidable
    # interpolation in the module. int() on a module constant makes it structurally
    # impossible for anything caller-supplied to reach the statement.
    connection.execute(f"PRAGMA user_version = {int(SCHEMA_VERSION)}")
    return True


def _seed_version(connection: sqlite3.Connection) -> int:
    """Return the seed version recorded in the database, or 0 when unseeded."""
    row: sqlite3.Row | None = connection.execute(
        "SELECT value FROM schema_meta WHERE key = ?", (SEED_VERSION_KEY,)
    ).fetchone()
    if row is None:
        return 0
    try:
        return int(row["value"])
    except (TypeError, ValueError):
        return 0


def _split_legacy_seeded_ids(raw: str) -> set[str]:
    """Parse the comma-joined form this record used before it became a JSON array."""
    return {part for part in raw.split(",") if part}


def _seeded_learner_ids(connection: sqlite3.Connection) -> set[str]:
    """Return the sample learner ids this database has ever seeded.

    Survives the learner row being deleted, which is the point: it is how a household's
    deletion is made permanent across seed-version bumps.
    """
    row: sqlite3.Row | None = connection.execute(
        "SELECT value FROM schema_meta WHERE key = ?", (SEEDED_LEARNERS_KEY,)
    ).fetchone()
    if row is None:
        return set()

    raw = str(row["value"]).strip()
    if not raw:
        return set()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Written by a version of this code that comma-joined the ids. Reading it is
        # what keeps an already-seeded installation's deletions permanent across the
        # upgrade; treating it as unreadable would re-enable the resurrection here.
        return _split_legacy_seeded_ids(raw)

    if isinstance(parsed, list) and all(isinstance(item, str) for item in parsed):
        return {item for item in parsed if item}

    if isinstance(parsed, str):
        # A single id written whole, as JSON. It is NOT comma-split, and that is the
        # difference between this arm and the JSONDecodeError one above: inside JSON a
        # comma in a string is data, not a separator. Splitting here would fragment
        # "smith, john" into two ids matching no learner -- the exact failure the array
        # encoding exists to prevent, and the array arm already refuses to do it, so
        # doing it here would make the same id survive one spelling and not the other.
        # Comma-splitting belongs only where the raw text really is the legacy form.
        return {parsed} if parsed else set()

    if isinstance(parsed, (int, float)) and not isinstance(parsed, bool):
        # A number is the opposite case: the RAW text is the id, and the parsed value
        # is a lossy rendering of it. str(json.loads(x)) is not x for any literal whose
        # Python repr differs from its stored spelling -- "1e5" comes back "100000.0"
        # and "1.50" comes back "1.5". Each is a DIFFERENT id, so the stored one is
        # dropped, and dropping an id here re-seeds a learner a household deleted.
        # bool is excluded explicitly because it subclasses int: without that, a stored
        # "true" yielded {"True"} instead of reaching the warning below.
        #
        # The cost of that exclusion, taken deliberately: a legacy bare id spelled
        # exactly "true", "false" or "null" parses as a JSON bool or None, reaches
        # neither arm, and is dropped rather than recovered from raw. A bare boolean in
        # this record is far likelier to be corruption than an id, and it now degrades
        # LOUDLY, which is the module's rule; before, it degraded silently into an id
        # matching nobody. Ids here are slugs, so no shipped id can be spelled that way.
        #
        # "Refused" understates it, so say the whole thing: _seed rewrites this record
        # from the set it just read, in the same transaction, so an unreadable value is
        # not merely ignored for one read -- it is overwritten and the original text is
        # gone before anyone sees the warning. That is true of every branch that
        # degrades, not only this one.
        # raw is non-empty here: the empty and whitespace-only cases returned above.
        return {raw}

    # Neither shape. Degrading to an empty set is the PERMISSIVE direction - it would
    # let a deleted learner be re-seeded - so say so loudly rather than failing quietly.
    logger.warning(
        "Unreadable %s in the learner database; treating it as empty, which allows sample learners to be seeded again",
        SEEDED_LEARNERS_KEY,
    )
    return set()


def _seed(connection: sqlite3.Connection) -> bool:
    """Insert or converge the seed data when it predates the current seed version."""
    if _seed_version(connection) >= SEED_VERSION:
        return False

    with connection:
        # Reference data the app owns: converge it, so a corrected lesson title
        # reaches installations that already seeded an earlier version.
        connection.executemany(
            "INSERT INTO languages (code, name) VALUES (?, ?) ON CONFLICT(code) DO UPDATE SET name = excluded.name",
            SEED_LANGUAGES,
        )
        # HIGHEST POSITION FIRST, and this ordering is load-bearing rather than tidy.
        # UNIQUE (language_code, position) is checked per statement, not at commit, so
        # a lesson moving UP into a place its neighbour has not vacated yet fails --
        # even though the end state is perfectly valid. Ascending order, es-01 moves
        # 1 -> 2 while es-02 still holds 2, and the seed dies on a robot in a house.
        #
        # That is why "renumbering is only safe upward" (docs/converting-a-course.md
        # step 7) was not the whole rule. Italian got away with an ascending upsert
        # because it shifted six placeholders by six, so every destination was already
        # free. Spanish shifts six by one, every destination is occupied, and the
        # measured result was an IntegrityError on the UPGRADE path while a fresh seed
        # passed -- the worst shape of failure, because only households see it.
        #
        # Descending makes the shift self-clearing: 6 -> 7 first (7 free), then 5 -> 6
        # (just vacated), and so on down. It costs one sort and removes the dependency
        # on the shift being larger than the block.
        connection.executemany(
            "INSERT INTO lessons (id, language_code, position, title, objective) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "language_code = excluded.language_code, position = excluded.position, "
            "title = excluded.title, objective = excluded.objective",
            sorted(SEED_LESSONS, key=lambda row: row[2], reverse=True),
        )
        # Converged with the lessons themselves, in the same transaction and by the
        # same rule: a lesson whose provenance is corrected must reach the robots that
        # already seeded the wrong one, and a lesson and its provenance must never
        # arrive separately.
        connection.executemany(
            "INSERT INTO lesson_sources (lesson_id, origin, course, module, unit, page) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(lesson_id) DO UPDATE SET "
            "origin = excluded.origin, course = excluded.course, "
            "module = excluded.module, unit = excluded.unit, page = excluded.page",
            SEED_LESSON_SOURCES,
        )

        _seed_converted_lessons(connection)

        # Learner-owned rows. Two separate guards, because "the row is absent" has two
        # very different meanings.
        #
        # A learner already seeded once is NEVER seeded again, even on a version bump
        # and even though the row is gone -- absent means the household deleted them,
        # and re-inserting would resurrect a person who asked to be forgotten.
        # ON CONFLICT DO NOTHING cannot express this: it does not fire on a deleted
        # row. So the fact that we seeded them is recorded separately and outlives the
        # row itself.
        already_seeded = _seeded_learner_ids(connection)
        new_learners = [row for row in SEED_LEARNERS if row[0] not in already_seeded]
        connection.executemany(
            "INSERT INTO learners (id, display_name, created_at) VALUES (?, ?, ?) ON CONFLICT(id) DO NOTHING",
            new_learners,
        )

        # A learner who has any result has practised, so none of the sample results may
        # be written over their history. Decided once, before inserting anything --
        # checking per row would let the first insert satisfy the guard for every later
        # row and seed exactly one result.
        practised: set[str] = set()
        for learner_id in {learner_id for learner_id, *_ in SEED_RESULTS}:
            existing: sqlite3.Row | None = connection.execute(
                "SELECT 1 FROM lesson_results WHERE learner_id = ? LIMIT 1", (learner_id,)
            ).fetchone()
            if existing is not None:
                practised.add(learner_id)

        skip_results = practised | already_seeded
        connection.executemany(
            "INSERT INTO lesson_results (learner_id, lesson_id, outcome, score, recorded_at) VALUES (?, ?, ?, ?, ?)",
            [row for row in SEED_RESULTS if row[0] not in skip_results],
        )

        # Written unconditionally, not only when a learner row was actually inserted:
        # the invariant is "every sample learner ever seeded is recorded", and a
        # conditional write would leave the record absent on a database that seeded
        # under a version which skipped it - re-enabling resurrection exactly once.
        # JSON rather than a comma-joined string so an id containing a comma cannot
        # fragment into pieces that match no real learner.
        connection.execute(
            "INSERT INTO schema_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (
                SEEDED_LEARNERS_KEY,
                json.dumps(sorted(already_seeded | {row[0] for row in SEED_LEARNERS})),
            ),
        )

        # Written in the same transaction as the rows it describes, so a crash
        # mid-seed leaves the marker unset and the work re-runs safely.
        connection.execute(
            "INSERT INTO schema_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (SEED_VERSION_KEY, str(SEED_VERSION)),
        )
    return True


def _seed_converted_lessons(connection: sqlite3.Connection) -> None:
    """Write the lessons converted from a published course, and everything they hold.

    Runs inside _seed's transaction, after the app-written lessons, so a failure here
    rolls the whole seed back and leaves the version marker unmoved -- the work re-runs
    on the next start rather than leaving half a lesson in the catalog.

    Content is REPLACED rather than upserted row by row. A corrected unit may have
    fewer drills than the one it replaces, and an upsert keyed on position would leave
    the extra ones behind for ever -- a drill nobody wrote, in a lesson somebody
    corrected. Deleting first is safe here in a way it would never be for learner data:
    every row involved is app-owned catalog material, identical in every household.

    Every course in the file is written, and each lesson takes its language code and its
    course name from the course that OWNS it rather than from any single global block --
    which is what lets the file hold more than one language. All of them go in under
    _seed's one transaction, so a malformed course late in the list rolls back the
    courses written before it.
    """
    for course in _converted_courses():
        for lesson in course["lessons"]:
            lesson_id = str(lesson["id"])
            source = lesson["source"]

            connection.execute(
                "INSERT INTO lessons (id, language_code, position, title, objective) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "language_code = excluded.language_code, position = excluded.position, "
                "title = excluded.title, objective = excluded.objective",
                (lesson_id, course["language_code"], lesson["position"], lesson["title"], lesson["objective"]),
            )
            connection.execute(
                "INSERT INTO lesson_sources (lesson_id, origin, course, module, unit, page) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(lesson_id) DO UPDATE SET "
                "origin = excluded.origin, course = excluded.course, "
                "module = excluded.module, unit = excluded.unit, page = excluded.page",
                (
                    lesson_id,
                    "converted_from_course",
                    course["name"],
                    source["module"],
                    source["unit"],
                    source["page"],
                ),
            )

            connection.execute("DELETE FROM lesson_dialogues WHERE lesson_id = ?", (lesson_id,))
            connection.execute("DELETE FROM lesson_dialogue_turns WHERE lesson_id = ?", (lesson_id,))
            connection.execute("DELETE FROM lesson_notes WHERE lesson_id = ?", (lesson_id,))
            connection.execute("DELETE FROM lesson_drills WHERE lesson_id = ?", (lesson_id,))

            title = lesson["dialogue_title"]
            if title is not None:
                connection.execute("INSERT INTO lesson_dialogues (lesson_id, title) VALUES (?, ?)", (lesson_id, title))
            connection.executemany(
                "INSERT INTO lesson_dialogue_turns (lesson_id, position, speaker, text) VALUES (?, ?, ?, ?)",
                [
                    (lesson_id, position, turn["speaker"], turn["text"])
                    for position, turn in enumerate(lesson["turns"], start=1)
                ],
            )
            connection.executemany(
                "INSERT INTO lesson_notes (lesson_id, number, text) VALUES (?, ?, ?)",
                [(lesson_id, number, note) for number, note in enumerate(lesson["notes"], start=1)],
            )
            connection.executemany(
                "INSERT INTO lesson_drills "
                "(lesson_id, position, kind, target_text, english_gloss, cue, expected_response) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        lesson_id,
                        position,
                        drill["kind"],
                        drill.get("target_text"),
                        drill.get("english_gloss"),
                        drill.get("cue"),
                        drill.get("expected_response"),
                    )
                    for position, drill in enumerate(lesson["drills"], start=1)
                ],
            )


def ensure_learner_database(instance_path: str | Path | None = None) -> EnsureResult:
    """Create and seed the learner database if needed, and report what happened.

    Safe to call on every start. Once the database is current this costs a connection
    open, a few pragmas, one integer header read and one indexed single-row lookup --
    no DDL parsing and no writes, which matters on the Wireless model's weak hardware.

    Never raises -- the app must start even when learner storage is unusable. A missing
    directory is created; an unreadable or corrupt database is logged and reported as
    ready=False with the file left untouched, because deleting it would destroy real
    learner progress.
    """
    # Deriving the path is itself fallible: Path.home() raises RuntimeError when the
    # home directory cannot be determined, and a non-path argument raises TypeError. So
    # it happens inside the try, and `path` is only known after it succeeds.
    path: Path | None = None
    connection: sqlite3.Connection | None = None
    try:
        path = learner_db_path_for_instance(instance_path)
        with _SCHEMA_LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            connection = connect(instance_path, create=True)
            schema_applied = _apply_schema(connection)
            seeded = _seed(connection)
    except (sqlite3.Error, OSError, ValueError, TypeError, OverflowError, RuntimeError) as exc:
        # OverflowError for the reason _READER_ABSORBS names it: sqlite3 raises it while
        # BINDING an int outside _SQLITE_INT_MIN.._SQLITE_INT_MAX, before the database
        # sees the statement, and it subclasses ArithmeticError, so it is in none of the
        # others. The readers absorbed it and this function did not -- the same
        # unswept-sibling shape as D10 and D14 -- so a seed file carrying a huge int in
        # any bound leaf raised straight out of a function whose docstring says it never
        # does. Measured with a 583-mutation sweep over the shipped file: 19 escapes, all
        # OverflowError, and none once it is named here.
        #
        # The path is absent from the format string AND from the exception: it can name
        # a person's home directory, and _restrict_permissions states that rule for this
        # module. Both halves are needed and an earlier version of this comment claimed
        # the first while the second leaked -- OSError embeds its filename in __str__,
        # so this line printed the household directory until _log_safe stopped it.
        logger.warning("The learner database is unavailable: %s", _log_safe(exc))
        return EnsureResult(path=path or Path(LEARNER_DB_FILENAME), ready=False, error=str(exc))
    finally:
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error as close_exc:
                # An unguarded close() here would propagate and discard the result the
                # except branch just returned.
                # Routed through _log_safe like every sibling arm. A sqlite3.Error is
                # rendered in full either way, but "this one is safe" decided per line
                # is exactly how the OSError above reached a log.
                logger.warning("Failed to close the learner database: %s", _log_safe(close_exc))

    if schema_applied or seeded:
        # The path is absent here for the same reason it is absent from the warning
        # branch above: it can name a person's home directory, and _restrict_permissions
        # states that rule for this module. The filename is fixed and carries no
        # information, so what is worth logging on a first run is what HAPPENED.
        logger.info(
            "Learner database ready (schema_applied=%s, seeded=%s)",
            schema_applied,
            seeded,
        )
    return EnsureResult(path=path, ready=True, schema_applied=schema_applied, seeded=seeded)


def utc_now_ms() -> int:
    """Return the current time in milliseconds since the epoch."""
    return int(time.time() * 1000)


# --------------------------------------------------------------------------------
# The query interface
#
# Everything below is the public contract the conversation tools call. Callers import
# it from the package, not from this module, so that the storage engine can change
# underneath them.
# --------------------------------------------------------------------------------

# What a SQLite INTEGER column can hold. Beyond this the driver raises OverflowError
# while binding, before the database ever sees the statement.
_SQLITE_INT_MIN = -(2**63)
_SQLITE_INT_MAX = 2**63 - 1

# What a reader absorbs instead of raising. Their contract is a value, never an
# exception: the caller is a conversation tool, and an exception there ends the turn.
#
# OverflowError is the odd one and is named rather than left to be inherited. sqlite3
# raises it while BINDING an int outside the range above -- before the database sees
# the statement -- and it subclasses ArithmeticError, so it is in none of the other
# three. It is therefore a CALLER error, where the other three are storage failures.
#
# Collapsing the two into one answer here is not the blurring store_is_available
# exists to prevent. That distinction matters because a broken store says None about a
# learner who really exists. A value the driver cannot bind cannot name any learner, so
# "no such learner" is the TRUE answer rather than a fallback -- and store_is_available
# still truthfully reports the store healthy, which is what a caller asks next.
#
# RuntimeError is here for a different reason than the rest, and not for OverflowError's:
# Path.home() raises it when the home directory cannot be determined, which is the
# DEFAULT path branch, taken whenever no instance_path is passed. ensure_learner_database
# has caught it since it was written; these readers did not, so a robot service started
# without HOME ended the conversation turn instead of reporting an unreadable store.
# TypeError stays out on purpose: the row converters call int() and str() on column
# values, so absorbing it would turn a genuine bug there into a silent absence.
_READER_ABSORBS = (sqlite3.Error, OSError, ValueError, OverflowError, RuntimeError)

# What a faceprint reader must absorb on top of the above, and it is not a refinement.
# struct.error subclasses Exception DIRECTLY -- its mro is (error, Exception,
# BaseException, object), measured, not assumed -- so it is in neither sqlite3.Error
# nor ValueError and _READER_ABSORBS does not catch it. A stored row whose blob length
# disagrees with its dimension would therefore raise straight through a reader that
# promises never to raise. The schema's own CHECK should mean that row cannot exist;
# this is the belt to that brace, because "cannot happen" is not a thing to stake a
# conversation turn on.
_FACEPRINT_ABSORBS = (*_READER_ABSORBS, struct.error)

# One float32 per element, little-endian. Pinned here, in the "<" of the format string
# below, and again by the length CHECK in schema.sql -- three places, because a silent
# change of byte order turns every stored faceprint into a different person's numbers
# while every test that only round-trips through this module keeps passing.
_VECTOR_BYTES_PER_ELEMENT = 4
_VECTOR_FORMAT_PREFIX = "<"

# The bounds a faceprint must fit. They are mirrored by CHECK constraints in schema.sql
# and a test parses that file and asserts the two agree, so neither can drift.
_MAX_VECTOR_DIMENSION = 1024
_MAX_MODEL_NAME = 128

# The characters an embedding model's name may contain. An allow-list, mirrored by a
# GLOB in schema.sql and pinned against it by a test: this is the clause that stops the
# only caller-supplied TEXT column in the faceprints table holding a filesystem path.
_MODEL_NAME_CHARACTERS = frozenset(string.ascii_letters + string.digits + "._-")


class _StoreRefusal(SafeToLog, ValueError):
    """A refusal this module built itself, safe to log in full.

    Every message raised as one of these is assembled from type(value).__name__ -- see
    _cannot_be_a_path and its siblings, which return a type and never a value. Carrying
    that as a TYPE is what lets log_safe render these in full while a stdlib ValueError,
    which quotes whatever it could not convert, is reduced to its class name.

    Subclasses ValueError so that every `except ValueError` and every _READER_ABSORBS
    catch in this module keeps working unchanged.
    """


# The rule itself lives in logging_safety, because it was obeyed here and broken in
# memory.py -- see that module's docstring. This name is kept because eighteen call
# sites and the structural guard in tests/test_learner_store.py both use it.
_log_safe = log_safe


# What the lesson catalog can possibly hold, from schema.sql's own CHECK on
# languages.code: lowercase, and 2 to 8 characters. A value outside that cannot be a
# catalog code, so its absence is a fact about the VALUE and not about the store.
_CATALOG_CODE_MIN, _CATALOG_CODE_MAX = 2, 8


def _cannot_be_a_path(value: object) -> str | None:
    """Say why this value could never be a filesystem path, or None if it might.

    The type, never the value: a path carries a username, and on this robot that is a
    household member's name.
    """
    if not isinstance(value, (str, os.PathLike)):
        return f"{type(value).__name__}, not a str or os.PathLike"
    return None


def _cannot_name_a_learner(value: object) -> str | None:
    """Say why this value could never equal a stored learner id, or None if it might.

    Not a type check, deliberately. Learner ids are TEXT, and SQLite applies the
    column's affinity to a bound number, so 42 finds the learner whose id is "42" and
    an int at the 64-bit boundary finds its own text spelling -- those are real
    lookups and this task's edge cases require them to keep working.

    What cannot work is a value that can never COMPARE equal to TEXT however the store
    is filled. A BLOB never equals TEXT and NULL never equals anything, so bytes and
    None are guaranteed non-matches: get_profile(b"sample-learner") answered a silent
    None for a learner who exists, and get_progress answered a populated result
    claiming no lessons completed for a learner with a real history. That second one
    is the worse failure, because the database is meant to be the source of truth for
    progress and a quiet zero would have the tutor re-teach finished lessons.

    Each reason names a shape, never the value.
    """
    if value is None:
        return "None, and NULL never compares equal to a stored id"
    if isinstance(value, float) and math.isnan(value):
        # Same refusal, one type further out. sqlite3 binds NaN as SQL NULL -- typeof()
        # says "null" -- so it is the NULL case above wearing a float. float("inf") is
        # NOT: it binds as REAL and takes TEXT affinity to "Inf", which is a real lookup.
        return "not a number, and sqlite3 binds NaN as NULL"
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"{type(value).__name__}, and a BLOB never compares equal to TEXT"
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            return "not encodable as UTF-8"
    return None


def _cannot_be_a_catalog_code(value: object) -> str | None:
    """Say why this value could never name a language row, or None if it might.

    Tests the property that matters -- can this name a catalog row -- rather than a
    proxy for it. Two earlier attempts tested proxies and both let a class through:
    bindability let every wrong TYPE past, and the type alone let "ES" and " es " past.
    Each returned a silent None for a language this robot does teach.

    Each reason names a shape, never the value.
    """
    if not isinstance(value, str):
        return f"{type(value).__name__} is not a string"
    if value != value.lower():
        return "not lowercase, and the catalog's CHECK stores only lowercase codes"
    if value != value.strip() or any(ch.isspace() or ord(ch) < 0x20 for ch in value):
        # The class the CHECK cannot close for us. lower(code) rules out "ES"; nothing
        # rules out " es", "es\n" or "es\x00", which are lowercase, in range, and bind
        # cleanly -- so each matched nothing and answered a silent None for a language
        # this robot teaches. Refused rather than stripped: repairing the caller's value
        # invisibly would leave the tool layer no signal that what it sent was malformed.
        return "padded or contains a control character"
    if not _CATALOG_CODE_MIN <= len(value) <= _CATALOG_CODE_MAX:
        return f"{len(value)} characters, outside the catalog's 2 to 8"
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        # A str the driver cannot bind. Refused HERE so it is named as the caller
        # error it is; left to the handler below it would be logged as a failed
        # lookup, which is the mirror of the mislabelling this guard replaced.
        return "not encodable as UTF-8"
    return None


def _cannot_name_a_lesson(value: object) -> str | None:
    """Say why this value could never name a lesson row, or None if it might.

    A lesson id is not a catalog code -- it is 13+ characters and carries a language
    prefix, so _cannot_be_a_catalog_code's 2-to-8 length rule is the wrong guard. What
    matters is the same class it protects against: a value the database ACCEPTS as a
    binding but that can never match, which comes back as a silent "no such lesson"
    indistinguishable from a genuine absence.

    So this closes that class rather than the two spellings that are easiest to name.
    Padding and control characters bind cleanly and match nothing, exactly as a bad
    type does.

    It lives here, rather than inline in one reader, because every reader that takes a
    lesson id has to refuse the same values. Two copies of a rule is how a guard and
    its sibling drift apart, and a reader whose guard is narrower answers a silent
    absence for content that is really there.

    Each reason names a shape, never the value.
    """
    if not isinstance(value, str):
        return f"{type(value).__name__} is not a string"
    if not value:
        return "empty"
    if value != value.strip() or any(ch.isspace() or ord(ch) < 0x20 for ch in value):
        return "padded or contains a control character"
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        # Both sibling refusals -- _cannot_name_a_learner and _cannot_be_a_catalog_code
        # -- already close this, and this one did not, because it was extracted from a
        # reader written before the rule existed. A lone surrogate is a str the driver
        # cannot bind: refused HERE it is named as the caller error it is, while left to
        # the handler below it is logged as a failed lookup or a storage failure.
        return "not encodable as UTF-8"
    return None


def _cannot_be_an_embedding_model(value: object) -> str | None:
    """Say why this value could never name an embedding model, or None if it might.

    The same class the sibling guards close, applied to the one column whose value a
    caller invents rather than looks up: a value the database would ACCEPT but that
    cannot mean anything later. A blank name binds cleanly and makes every faceprint
    it labels uncomparable.

    Unlike its siblings this one ends in an allow-list of permitted characters, which
    is what stops the only caller-supplied TEXT column in the faceprints table holding
    a filesystem path -- and which subsumes their UTF-8 test, since every permitted
    character is ASCII, so a lone surrogate is refused by the character rule.

    Each reason names a shape, never the value -- this string reaches a log line.
    """
    if not isinstance(value, str):
        return f"{type(value).__name__} is not a string"
    if not value.strip():
        return "blank"
    if len(value) > _MAX_MODEL_NAME:
        return f"longer than {_MAX_MODEL_NAME} characters"
    if not set(value) <= _MODEL_NAME_CHARACTERS:
        # Named as the permitted set, never as the offending character: that character
        # is part of the value, and this string reaches a log line.
        #
        # This also subsumes the UTF-8 test its sibling guards carry. Every permitted
        # character is ASCII and therefore encodable, so a lone surrogate is refused
        # here rather than by a later encode() -- and a second arm for it would be
        # unreachable code asserting a path this function does not have.
        return "not made only of letters, digits, dot, underscore and hyphen"
    return None


def _pack_vector(values: object) -> bytes | None:
    """Pack a face vector into little-endian float32 bytes, or None if it cannot be.

    An allow-list, not a list of bad inputs: the permitted shape is a non-empty
    sequence of at most _MAX_VECTOR_DIMENSION real numbers that struct can represent
    as float32, and anything else is refused. str and bytes are excluded before the
    length test because both are sequences and neither is a vector.

    A numpy ndarray is NOT a Sequence and is refused, which the first face-embedding
    caller will meet immediately -- every such library returns one. That is deliberate
    rather than an oversight: numpy is not a declared dependency of this package and
    the store layer should not acquire one, so the conversion belongs at the call site
    (`vector.tolist()`). It fails with a reason code rather than silently, which is the
    direction to fail in.

    bool is excluded explicitly, and it is the one refusal that is not obvious.
    struct.pack("<f", True) does not raise -- it silently packs 1.0 (measured) -- so a
    vector of flags would be stored as a face rather than refused.

    struct itself is the arbiter of what float32 can hold, rather than a range invented
    here: it raises struct.error for a value of the wrong type and OverflowError for one
    too large, and those two are the whole refusal. NaN and infinity pack cleanly and
    are accepted; they are meaningless as a faceprint but they are not this function's
    to judge, and a matcher comparing them will find no match, which is the safe
    direction.
    """
    if isinstance(values, (str, bytes, bytearray)) or not isinstance(values, Sequence):
        return None
    if not 1 <= len(values) <= _MAX_VECTOR_DIMENSION:
        return None
    if any(isinstance(value, bool) for value in values):
        return None
    try:
        return struct.pack(f"{_VECTOR_FORMAT_PREFIX}{len(values)}f", *values)
    except (struct.error, OverflowError):
        return None


# The three tables that hold anything about a person, and the column in each that says
# which person. Everything else in this schema -- languages, lessons, schema_meta --
# is shared reference data that no filter has to constrain.
#
# faceprints joined this set in the same change that created it, which is the only
# order that is safe: _learner_scoped RAISES on a statement naming a personal table it
# has not been told about, so a table added here late is a table whose statements were
# unscoped for as long as it took somebody to notice.
#
# This is the rule's own copy, and it lives here because this module is where the rule
# lives. The AST guard in the tests reuses this function rather than restating it.
_PERSONAL_TABLES: dict[str, str] = {
    "learners": "id",
    "lesson_results": "learner_id",
    "faceprints": "learner_id",
}

# The columns that say which learner a row belongs to. Writing one re-attributes the
# row, which no amount of filtering on the way in can make safe.
_LEARNER_COLUMNS = frozenset(_PERSONAL_TABLES.values())

# The keywords that introduce a relation. A personal table named anywhere else is in a
# position this rule has not been taught to read, and is refused rather than passed
# over -- a check that ignores what it cannot account for is the defect being fixed.
_RELATION_KEYWORDS = frozenset({"FROM", "JOIN", "INTO", "UPDATE"})

# The keywords that end one clause and begin the next. A learner filter only proves
# something in a clause that chooses rows, so only WHERE and a join's ON count.
_CLAUSE_KEYWORDS = frozenset(
    {
        "SELECT",
        "FROM",
        "WHERE",
        "GROUP",
        "ORDER",
        "HAVING",
        "LIMIT",
        "OFFSET",
        "ON",
        "SET",
        "VALUES",
        "JOIN",
        "INTO",
        "UPDATE",
        "DELETE",
        "INSERT",
        "USING",
        "RETURNING",
    }
)

# A word that can follow a relation name without being its alias.
_NOT_AN_ALIAS = _CLAUSE_KEYWORDS | frozenset(
    {
        "AS",
        "INNER",
        "LEFT",
        "RIGHT",
        "FULL",
        "OUTER",
        "CROSS",
        "NATURAL",
        "AND",
        "OR",
        "NOT",
        "UNION",
        "EXCEPT",
        "INTERSECT",
        "WITH",
    }
)

# Constructs that change what a filter constrains in ways this rule does not work out.
# Each is refused with its reason. A statement this rule cannot judge is exactly the
# statement the substring test used to wave through.
_UNJUDGEABLE_WORDS: dict[str, str] = {
    "UNION": "a compound query's other legs carry no filter of their own",
    "EXCEPT": "a compound query's other legs carry no filter of their own",
    "INTERSECT": "a compound query's other legs carry no filter of their own",
    "WITH": "a common table expression renames relations, which this rule does not follow",
}

# One SQL token. Comments and string literals are matched first so that neither can
# contribute a word to anything below: "-- learner_id = ?" is text the database never
# reads, and 'lesson_results' in quotes names no table.
_SQL_TOKEN = re.compile(
    r"(?P<comment>--[^\n]*|/\*.*?\*/)"
    r"|(?P<string>'(?:[^']|'')*')"
    r"|(?P<word>[A-Za-z_][A-Za-z_0-9]*)"
    r"|(?P<number>[0-9]+(?:\.[0-9]+)?)"
    r"|(?P<operator><>|<=|>=|!=)"
    r"|(?P<other>\S)",
    re.DOTALL,
)


def _sql_tokens(sql: str) -> list[str]:
    """Split a statement into tokens, dropping comments and hiding literal text.

    Every character is accounted for: the last alternative matches any single
    non-whitespace character, so nothing is skipped silently. That matters more for a
    rule that reads structure than it did for one that searched for a substring -- a
    token this misses is a shape this misreads.
    """
    tokens: list[str] = []
    for match in _SQL_TOKEN.finditer(sql):
        if match.lastgroup == "comment":
            continue
        tokens.append("<literal>" if match.lastgroup == "string" else match.group())
    return tokens


def _is_a_name(token: str) -> bool:
    """Report whether a token could be an identifier rather than punctuation."""
    return token[:1].isalpha() or token.startswith("_")


def _addressed_as(tokens: list[str], words: list[str], index: int) -> str:
    """Return the name a relation reference is addressed by: its alias, else itself."""
    if index + 2 < len(tokens) and words[index + 1] == "AS":
        return tokens[index + 2].lower()
    if index + 1 < len(tokens) and _is_a_name(tokens[index + 1]) and words[index + 1] not in _NOT_AN_ALIAS:
        return tokens[index + 1].lower()
    return tokens[index].lower()


def _bracket_map(tokens: list[str], words: list[str]) -> tuple[list[tuple[int, int]], dict[int, int]]:
    """Give every token its bracket depth and the subquery it belongs to, plus the nesting.

    The subquery is an IDENTITY, not a depth. A counter cannot tell two sibling
    subqueries apart, and a filter written in one would then be credited to a relation
    read in the other: `SELECT (SELECT group_concat(learner_id) FROM lesson_results),
    (SELECT 1 FROM (SELECT ? AS learner_id) WHERE learner_id = ?)` reads every learner
    while the second subquery's filter appears to scope the first's relation.

    Bracket depth is counted separately because brackets group predicates as well as open
    subqueries, and only a subquery moves a filter out of reach of the rows around it.
    `WHERE (learner_id = ?)` is the same statement as `WHERE learner_id = ?`; `WHERE NOT
    EXISTS (SELECT ... WHERE learner_id = ?)` is not.

    No clause is inferred here. An earlier version carried a single flat `clause`
    variable that was never restored when a bracket closed, so a subquery's WHERE leaked
    into everything after the closing bracket. Clauses are found by token instead, in
    _clause_region, which has no state to leak.
    """
    rows: list[tuple[int, int]] = []
    opened_subquery: list[bool] = []
    scope: list[int] = [0]
    encloses: dict[int, int] = {0: 0}
    next_scope = 1
    for index, token in enumerate(tokens):
        if token == "(":
            starts_subquery = index + 1 < len(tokens) and words[index + 1] == "SELECT"
            opened_subquery.append(starts_subquery)
            if starts_subquery:
                encloses[next_scope] = scope[-1]
                scope.append(next_scope)
                next_scope += 1
        elif token == ")" and opened_subquery:
            if opened_subquery.pop():
                scope.pop()
        rows.append((len(opened_subquery), scope[-1]))
    return rows, encloses


def _is_visible_from(encloses: dict[int, int], relation_scope: int, filter_scope: int) -> bool:
    """Report whether a relation's subquery is the filter's own, or encloses it."""
    at = filter_scope
    while True:
        if at == relation_scope:
            return True
        if at == 0:
            return False
        at = encloses[at]


def _clause_region(
    tokens: list[str], words: list[str], rows: list[tuple[int, int]], index: int
) -> tuple[list[int], int, int]:
    """Return the tokens belonging to the clause opened at index, with its two depths.

    The clause ends where the next clause keyword appears at the same bracket level, or
    where the bracket enclosing it closes. Anything more deeply bracketed -- a subquery,
    a grouped predicate, an insert's column list -- belongs to the clause rather than
    ending it.
    """
    level, sub_depth = rows[index]
    members: list[int] = []
    for ahead in range(index + 1, len(tokens)):
        if rows[ahead][0] < level or (rows[ahead][0] == level and words[ahead] in _CLAUSE_KEYWORDS):
            break
        members.append(ahead)
    return members, level, sub_depth


def _unwrapped(predicate: list[str]) -> list[str]:
    """Drop brackets that enclose the whole predicate, which change nothing about it."""
    while len(predicate) >= 2 and predicate[0] == "(" and predicate[-1] == ")":
        depth = 0
        for position, token in enumerate(predicate):
            depth += token == "("
            depth -= token == ")"
            if depth == 0 and position < len(predicate) - 1:
                return predicate
        predicate = predicate[1:-1]
    return predicate


def _learner_filters(
    tokens: list[str], words: list[str], rows: list[tuple[int, int]]
) -> tuple[set[tuple[str, str, int]], set[tuple[str, int]], set[tuple[str, int]], str | None]:
    """Collect the WHERE conjuncts that ARE a learner filter, and nothing else.

    A conjunct counts only when the whole of it is `alias.column = ?` or `column = ?`.
    That is an allow-list, deliberately: the first version of this named the ways a
    filter can be present without constraining anything -- NOT, CASE -- and a list like
    that can never be finished. `IIF(learner_id = ?, 1, 1)`, `max(learner_id = ?, 1)`,
    `(learner_id = ?) = 0` and `learner_id = ? = 0` all defeated it, and each returns
    rows belonging to somebody else. Requiring the conjunct to BE the filter refuses
    every one of them, including the ones nobody has thought of.

    Only a WHERE clause is read. A join's ON constrains whichever side of an outer join
    is not the preserved one, and this rule does not work out which that is, so a filter
    written there has to move to the WHERE clause.
    """
    qualified: set[tuple[str, str, int]] = set()
    bare: set[tuple[str, int]] = set()
    mentioned: set[tuple[str, int]] = set()
    for index in range(len(tokens)):
        if words[index] != "WHERE":
            continue
        if index and tokens[index - 1] == "(":
            # An aggregate's `FILTER (WHERE ...)` is the one construct that spells
            # "(WHERE", and its predicate chooses what the aggregate accumulates rather
            # than which rows the statement reads -- so counting it as a filter credits
            # the relation with a constraint the database never applies. Refused by shape
            # rather than by naming FILTER: a row-choosing WHERE is never adjacent to an
            # opening bracket, because a subquery's WHERE is separated from its "(" by
            # SELECT ... FROM ..., so this covers any future clause of the same shape.
            return set(), set(), set(), "a WHERE straight after an opening bracket does not choose the rows read"
        members, level, sub_depth = _clause_region(tokens, words, rows, index)
        # AND binds tighter than OR, so `a = ? AND b OR c` is `(a = ? AND b) OR c` and
        # the filter constrains nothing. Splitting on AND alone cannot see that, so an
        # OR at this clause's own level is refused. Deeper down it is inside a bracket
        # and cannot reach past it, which is why a benign `AND (x OR y)` still passes.
        if any(words[at] == "OR" and rows[at][0] == level for at in members):
            return (
                set(),
                set(),
                set(),
                "a WHERE clause with a top-level OR does not constrain what it looks like it does",
            )
        # BETWEEN spells its own AND, and that AND is not a conjunction. Splitting on it
        # reads `NOT score BETWEEN 0 AND learner_id = ?` as two conjuncts, the second of
        # which looks exactly like a learner filter -- while the database binds it as the
        # BETWEEN's upper bound and returns every row. BETWEEN is the only SQLite operator
        # that overloads AND this way, which is why naming it is not the start of a
        # denylist: it is the complete set.
        if any(words[at] == "BETWEEN" and rows[at][0] == level for at in members):
            return (
                set(),
                set(),
                set(),
                "a WHERE clause with a top-level BETWEEN spells its own AND, which is not a conjunction",
            )
        conjunct: list[str] = []
        conjuncts: list[list[str]] = []
        for at in members:
            if words[at] == "AND" and rows[at][0] == level:
                conjuncts.append(conjunct)
                conjunct = []
                continue
            conjunct.append(tokens[at])
            if rows[at][1] == sub_depth:
                # Same subquery only -- identity, not depth. A token one subquery down belongs to that
                # query's WHERE, not this one, and crediting it here would report a
                # filter as badly written when in truth it constrains something else.
                mentioned.add((tokens[at].lower(), sub_depth))
        conjuncts.append(conjunct)
        for predicate in (_unwrapped(each) for each in conjuncts):
            if (
                len(predicate) == 5
                and predicate[1] == "."
                and predicate[3] == "="
                and predicate[4] == "?"
                and _is_a_name(predicate[0])
                and _is_a_name(predicate[2])
            ):
                qualified.add((predicate[0].lower(), predicate[2].lower(), sub_depth))
            elif len(predicate) == 3 and predicate[1] == "=" and predicate[2] == "?" and _is_a_name(predicate[0]):
                bare.add((predicate[0].lower(), sub_depth))
    return qualified, bare, mentioned, None


def _personal_relations(
    tokens: list[str], words: list[str], rows: list[tuple[int, int]], write_target_exempt: bool
) -> tuple[list[tuple[str, str, int]], list[int], str | None]:
    """Return the personal relations a statement reads, every relation's depth, and any refusal."""
    for index in range(len(tokens)):
        if words[index] not in _RELATION_KEYWORDS:
            continue
        members, level, _ = _clause_region(tokens, words, rows, index)
        # `FROM a, b` names a relation in a position the loop below does not read, which
        # would leave it out of the count the unqualified-filter rule depends on. Refused
        # rather than counted: a comma join is a spelling, and the JOIN spelling of the
        # same query is read correctly.
        if any(tokens[at] == "," and rows[at][0] == level for at in members):
            return [], [], "it lists relations separated by a comma, which this rule does not read"

    relations: list[tuple[str, str, int]] = []
    relation_depths: list[int] = []
    names_a_personal_table = False
    for index, token in enumerate(tokens):
        before = words[index - 1] if index else ""
        after = words[index + 1] if index + 1 < len(tokens) else ""
        sub_depth = rows[index][1]
        if before in _RELATION_KEYWORDS and _is_a_name(token):
            relation_depths.append(sub_depth)
        elif before in _RELATION_KEYWORDS and token == "(":
            # A derived table -- `FROM (SELECT ...)`. It names no relation this rule
            # can read, but it IS one, and leaving it out of the count would let a
            # bare `learner_id = ?` be trusted where two relations are in scope.
            relation_depths.append(rows[index - 1][1])
        if token.lower() not in _PERSONAL_TABLES or after == ".":
            continue
        if before not in _RELATION_KEYWORDS:
            return [], [], f"it names {token.lower()} in a position this rule cannot read"
        names_a_personal_table = True
        if before == "INTO" and write_target_exempt:
            # The insert's own row, scoped by the column it writes first -- a separate
            # rule, because an insert has no WHERE clause. Only the insert branch may
            # set this; anything else naming a personal table after INTO is a write this
            # rule has not been taught, and is refused below like any other.
            continue
        relations.append((_addressed_as(tokens, words, index), token.lower(), sub_depth))
    if not names_a_personal_table:
        return [], [], "it names no personal relation, so there is nothing here for this rule to prove"
    return relations, relation_depths, None


def _unreadable(tokens: list[str], words: list[str]) -> str | None:
    """Say why a statement's text cannot be read at all, or None when it can.

    Every check here is about the TEXT rather than about scoping, so every statement
    passes through it -- an insert included. An insert that cannot be read is no more
    judgeable than a select that cannot be.

    The unterminated block comment is the one worth naming. SQLite ends an unterminated
    `/*` at the end of the input and discards everything after it, so
    `WHERE lesson_id = ? /* AND learner_id = ?` reaches the database with no learner
    filter at all, while a reader that does not know this sees one. That is the only
    direction that matters: the rule seeing MORE than the database does is how a filter
    counts while constraining nothing.
    """
    if "'" in tokens:
        return "it has an unterminated string literal, so its text cannot be read"
    if any(token == "/" and tokens[index + 1] == "*" for index, token in enumerate(tokens[:-1])):
        return "it has an unterminated block comment, and SQLite discards everything after one"
    if ";" in tokens:
        return "it is more than one statement"
    if any(token in ('"', "`", "[", "]") for token in tokens):
        # SQLite has three identifier quotes and this lexer knows none of them, so a
        # quote inside one pairs differently here than in the database -- which hides
        # live SQL as a literal and lets a top-level OR through unseen. Refused rather
        # than lexed: no registered statement uses one, and teaching the lexer three
        # more quoting forms re-opens the question at the next one.
        return "it quotes an identifier, which this rule does not read"
    for word, why in _UNJUDGEABLE_WORDS.items():
        if word in words:
            return f"it uses {word}, and {why}"
    return None


def _unconstrained_personal_relation(sql: str, write_target_exempt: bool = False) -> str | None:
    """Say why a statement cannot be proved scoped to one learner, or None when it can.

    The question is not whether a learner filter appears anywhere. That was the
    substring test this replaces, and it had no notion of WHICH rows a filter
    constrains: a self-join, an OR-widened predicate, an unfiltered UNION leg and a
    correlated-subquery DELETE all satisfied it while reading every learner.

    What is asked here is whether EVERY personal relation the statement names is
    constrained by a conjunct that IS a learner filter for it -- addressed to that
    relation, in a WHERE clause, in that relation's own subquery, and forming the
    whole of the conjunct rather than sitting inside a larger expression. A statement
    naming lesson_results twice needs two filters, and a filter on `r` says nothing
    about `r2`.

    Anything this cannot work out is refused rather than accepted, and the direction is
    deliberate: a statement wrongly refused stops the module importing and gets
    rewritten, while one wrongly accepted reads another household member's data and
    nobody finds out.
    """
    tokens = _sql_tokens(sql)
    words = [token.upper() for token in tokens]
    unreadable = _unreadable(tokens, words)
    if unreadable is not None:
        return unreadable

    rows, encloses = _bracket_map(tokens, words)
    relations, relation_depths, refused = _personal_relations(tokens, words, rows, write_target_exempt)
    if refused is not None:
        return refused

    # An UPDATE has a WHERE and a SET, and until now only the WHERE was read. That let
    # `UPDATE lesson_results SET learner_id = 'bob' WHERE learner_id = ?` through: the
    # filter is real, it constrains exactly one learner's rows, and the statement then
    # hands those rows to somebody else. Proving which rows a write touches says nothing
    # about who it attributes them to -- the same asymmetry the insert rule exists for,
    # in the one statement that has both halves.
    for index in range(len(tokens)):
        if words[index] != "SET":
            continue
        members, level, sub_depth = _clause_region(tokens, words, rows, index)
        assigned = [
            at
            for at in members
            if tokens[at].lower() in _LEARNER_COLUMNS
            and rows[at][0] == level
            and at + 1 < len(tokens)
            and tokens[at + 1] == "="
        ]
        if assigned:
            return "it writes the column that says which learner the row belongs to"
        if any(tokens[at].lower() in _LEARNER_COLUMNS and rows[at][1] == sub_depth for at in members):
            # A learner column at the SET's OWN subquery that is not that clean `col = `
            # assignment -- a column-list `SET (learner_id, x) = (...)`, a CASE that names
            # it, or a bare copy `SET x = learner_id`. An assignment target always sits at
            # the statement's own query level, so anything shaped like one here could
            # re-attribute the row, and this rule does not parse SET grammar finely enough
            # to prove it will not. Refused, but not as a write: saying it WRITES the column
            # would be false for a read, and D11 already split those messages so an author
            # is not sent after the wrong fix.
            #
            # A learner column that appears ONLY inside a nested subquery is excluded here,
            # and that is the D17 narrowing. Refusing it was collateral -- a catalog lookup
            # `SET lesson_id = (SELECT id FROM lessons ...)` names `id`, and the best-score
            # cache `SET score = (SELECT max(z.score) ... WHERE z.learner_id = ?)` names
            # `learner_id`, both READS the old check refused table-blind. The accept set was
            # measured rather than reasoned about: test_the_set_narrowing_moves_only_reads
            # reconstructs the pre-D17 region-wide check and diffs it against this one, and
            # every statement that moves refuse->accept is a nested-subquery read that leaves
            # a two-learner database's rows unchanged under execution -- no assignment moves.
            # (D11's own baseline: 48 statements were refused solely by this branch, only 12
            # of them re-attributing a row.) It is safe to stop refusing the reads because a
            # personal relation inside that subquery is still constrained by _personal_relations
            # a few lines down -- an unconstrained or literal-targeted one is refused there, by
            # name; this sweep was never what made those safe.
            return "a SET names a learner column at the statement's own level, where this rule cannot tell an assignment from a read"
    qualified, bare, mentioned, refused = _learner_filters(tokens, words, rows)
    if refused is not None:
        return refused

    addressed = [alias for alias, _, _ in relations]
    for alias, table, depth in relations:
        if addressed.count(alias) > 1:
            return f"two relations are both called {alias}, so a filter naming it constrains neither"
        column = _PERSONAL_TABLES[table]
        if (alias, column, depth) in qualified:
            continue
        # An unqualified filter names no relation, so it proves something only where one
        # relation is visible: those at its own depth and those enclosing it. With two in
        # scope, `learner_id = ?` does not say which of them it constrains.
        if sum(1 for at in relation_depths if _is_visible_from(encloses, at, depth)) == 1 and (column, depth) in bare:
            continue
        if (column, depth) in bare:
            # The filter is there and is the right one; what is missing is which relation
            # it names. Saying "nothing constrains this" would send the author looking for
            # a filter they already wrote, and the obvious wrong fix for that is to widen
            # the predicate until something passes.
            return (
                f"{column} = ? does not say which relation it constrains, and more than one is in "
                f"scope here -- write {alias}.{column} = ? instead"
            )
        readable_elsewhere = (column, depth) in bare or any(
            other == column and at == depth for _, other, at in qualified
        )
        if (column, depth) in mentioned and not readable_elsewhere:
            # The column is in this query's own WHERE clause but not as a conjunct of
            # its own -- bracketed together with something else, spelled `? = learner_id`,
            # or using a placeholder this rule does not read. Saying nothing constrains the
            # statement would be false there and would send the author the wrong way.
            #
            # Only when no READABLE filter on that column exists at this depth. A
            # self-join has one, correctly written, that simply names the other relation;
            # there the honest message is that nothing constrains THIS relation.
            return (
                f"a filter on {column} is in the WHERE clause but not as a conjunct of its own, so this "
                f"rule cannot tell what it constrains -- write {alias}.{column} = ? as its own conjunct"
            )
        as_written = table if alias == table else f"{table} (as {alias})"
        return f"nothing constrains {as_written} to one learner"
    return None


def _insert_is_attributed(sql: str, tokens: list[str], words: list[str]) -> str | None:
    """Say why an insert cannot be trusted to write one learner's row, or None when it can.

    This accepts ONE shape: `INSERT INTO <personal table> (learner_id, ...)` followed by
    VALUES or a SELECT this module can itself prove scoped. Naming the accepted shape
    rather than the forbidden ones is the same choice _learner_filters makes, and for
    the same reason -- the version that listed forbidden spellings missed `REPLACE INTO`,
    which is SQLite's documented alias for `INSERT OR REPLACE` and overwrites whoever
    already holds the row. A list of what is forbidden can always be one entry short.
    """
    if words[:2] != ["INSERT", "INTO"]:
        return "only a plain INSERT INTO is scoped by the column it writes first"
    if "CONFLICT" in words:
        return "an ON CONFLICT clause can rewrite a row this statement did not create"
    if "(" not in tokens:
        return "it names no column list, so nothing says which learner the row belongs to"
    opening = tokens.index("(")
    if tokens[opening + 1 : opening + 2] != ["learner_id"]:
        return "learner_id is not the first column it writes"
    depth = 0
    closing = opening
    for position in range(opening, len(tokens)):
        depth += tokens[position] == "("
        depth -= tokens[position] == ")"
        if depth == 0:
            closing = position
            break
    follows = words[closing + 1] if closing + 1 < len(words) else ""
    if follows not in ("VALUES", "SELECT"):
        return "its column list is followed by something other than VALUES or a SELECT"
    # Both forms go through the read rule, not just the SELECT. A VALUES list reads too
    # the moment it contains a scalar subquery, and writing learner_id first says nothing
    # about the rows that subquery reaches: `VALUES (?, (SELECT lesson_id FROM
    # lesson_results WHERE learner_id <> ? LIMIT 1), ...)` copies another household
    # member's history into this learner's. Checking only the SELECT form was an
    # asymmetry with no reason behind it, and that is what it cost.
    return _unconstrained_personal_relation(sql, write_target_exempt=True)


def _learner_scoped(sql: str) -> str:
    """Return the statement, refusing at import time one that is not learner-scoped.

    Learner scoping is the boundary that stops one household member's data reaching
    another. A missing filter should not be a review comment -- it should stop the
    module from importing at all, which is what this does.

    A read is scoped when every personal relation it names is constrained by a WHERE
    conjunct that IS a learner filter for it; see _unconstrained_personal_relation. A
    write is scoped by naming the learner id as the first column it writes, which is the
    equivalent guarantee for an insert: the row cannot be attributed to anyone else. The
    two are separate branches on purpose -- an insert has no WHERE clause, so one rule
    cannot serve both. What they share is _unreadable, because a statement whose text
    cannot be read is unjudgeable whichever branch it belongs to.

    Where a statement cannot be judged it is REFUSED, not accepted. Every bypass this
    rule has had -- and there have been eighteen -- was a statement it accepted while
    misreading it, never one it knowingly let through.
    """
    tokens = _sql_tokens(sql)
    words = [token.upper() for token in tokens]

    unreadable = _unreadable(tokens, words)
    if unreadable is not None:
        raise ValueError(f"a learner-scoped statement has to be one this rule can read: {unreadable}")

    if words[:1] in (["INSERT"], ["REPLACE"]):
        refusal = _insert_is_attributed(sql, tokens, words)
        if refusal is not None:
            raise ValueError(f"a learner-scoped write must attribute its row to one learner: {refusal}")
        return sql

    refusal = _unconstrained_personal_relation(sql)
    if refusal is not None:
        raise ValueError(f"a learner-scoped statement must constrain every personal relation it reads: {refusal}")
    return sql


_PROFILE_SQL = _learner_scoped("SELECT id, display_name, created_at FROM learners WHERE learners.id = ?")
_LEARNER_EXISTS_SQL = _learner_scoped("SELECT 1 FROM learners WHERE learners.id = ? LIMIT 1")
_COMPLETED_IDS_SQL = _learner_scoped(
    "SELECT DISTINCT r.lesson_id FROM lesson_results AS r "
    "JOIN lessons AS l ON l.id = r.lesson_id "
    "WHERE r.learner_id = ? AND l.language_code = ? AND r.outcome = 'completed'"
)
_ATTEMPTS_SQL = _learner_scoped(
    "SELECT r.learner_id, r.lesson_id, r.outcome, r.score, r.recorded_at "
    "FROM lesson_results AS r JOIN lessons AS l ON l.id = r.lesson_id "
    "WHERE r.learner_id = ? AND l.language_code = ? "
    "ORDER BY r.recorded_at DESC, r.id DESC"
)
_INSERT_ATTEMPT_SQL = _learner_scoped(
    "INSERT INTO lesson_results (learner_id, lesson_id, outcome, score, recorded_at) VALUES (?, ?, ?, ?, ?)"
)
# One statement rather than one per language: the tutor asks this in the voice loop on
# a weak onboard computer, and a hosted backend answers it in a single request. It
# leads on lesson_results so idx_lesson_results_learner_lesson carries the filter.
#
# A skipped lesson is declined, not practised, so those rows are excluded and a
# language the learner has only ever skipped does not appear here at all. Telling
# someone they have been working on a language they turned down is worse than saying
# nothing. Skips stay in the table and still count against the next lesson.
_PRACTISED_LANGUAGES_SQL = _learner_scoped(
    "SELECT g.code, g.name, COUNT(*) AS attempts, "
    "COUNT(DISTINCT CASE WHEN r.outcome = 'completed' THEN r.lesson_id END) AS completed "
    "FROM lesson_results AS r "
    "JOIN lessons AS l ON l.id = r.lesson_id "
    "JOIN languages AS g ON g.code = l.language_code "
    "WHERE r.learner_id = ? AND r.outcome <> 'skipped' "
    "GROUP BY g.code, g.name ORDER BY g.name"
)

# Catalog statements are deliberately NOT learner-scoped: lessons and languages are
# shared reference data, not personal data. The guard above covers the two tables
# that hold anything about a person.
_LANGUAGE_SQL = "SELECT code, name FROM languages WHERE code = ?"
# has_material is DERIVED here rather than stored, so a conversion landing flips it
# with no code change and nothing to remember. It asks the three content tables
# directly: a language has material when ANY of its lessons has a dialogue turn, a
# usage note or a drill. A lesson row on its own is a title and an objective, which is
# a syllabus entry rather than something to teach from.
_LANGUAGE_CATALOG_SQL = """
    SELECT
        languages.code,
        languages.name,
        EXISTS (
            SELECT 1 FROM lessons
            WHERE lessons.language_code = languages.code
              AND (
                  EXISTS (SELECT 1 FROM lesson_dialogue_turns WHERE lesson_dialogue_turns.lesson_id = lessons.id)
                  OR EXISTS (SELECT 1 FROM lesson_notes WHERE lesson_notes.lesson_id = lessons.id)
                  OR EXISTS (SELECT 1 FROM lesson_drills WHERE lesson_drills.lesson_id = lessons.id)
              )
        ) AS has_material
    FROM languages
    ORDER BY languages.name
"""
_LESSONS_SQL = (
    "SELECT id, language_code, position, title, objective FROM lessons WHERE language_code = ? ORDER BY position"
)
_LESSON_EXISTS_SQL = "SELECT 1 FROM lessons WHERE id = ? LIMIT 1"
_LESSON_BY_ID_SQL = "SELECT id, language_code, position, title, objective FROM lessons WHERE id = ?"

# The content of one lesson, read as five small statements rather than one join.
#
# A join across four optional one-to-many tables multiplies their rows together, so the
# caller would have to undo the product to get four lists back -- and a lesson with no
# drills would lose its turns to an inner join or need an outer one per table. Five
# indexed lookups on the same primary key are cheap here: the whole catalog is a few
# hundred rows, and each statement reads one lesson's worth.
#
# Note this does NOT depend on how large the deployment gets. A robot's database holds
# the shipped catalog plus ONE household, so its row count is the same whether twenty
# robots exist or twenty thousand -- an earlier version of this comment cited a fleet
# size, which was the wrong quantity for the claim it was supporting.
#
# Ordered in SQL, never in Python. Each ORDER BY names the column its table's primary
# key makes unique, so "in order" has exactly one meaning and cannot tie.
_LESSON_SOURCE_SQL = "SELECT lesson_id, origin, course, module, unit, page FROM lesson_sources WHERE lesson_id = ?"
_DIALOGUE_TITLE_SQL = "SELECT title FROM lesson_dialogues WHERE lesson_id = ?"
_DIALOGUE_TURNS_SQL = "SELECT position, speaker, text FROM lesson_dialogue_turns WHERE lesson_id = ? ORDER BY position"
_LESSON_NOTES_SQL = "SELECT number, text FROM lesson_notes WHERE lesson_id = ? ORDER BY number"
_LESSON_DRILLS_SQL = (
    "SELECT position, kind, target_text, english_gloss, cue, expected_response "
    "FROM lesson_drills WHERE lesson_id = ? ORDER BY position"
)
# Not a question about any learner: it asks whether the store can be read at all, so
# the cheapest catalog row is enough and there is nothing here to scope.
_STORE_READABLE_SQL = "SELECT 1 FROM languages LIMIT 1"

# The faceprint statements. Three, and there is no fourth: replacing a faceprint is the
# DELETE and the INSERT below run in one transaction, because _learner_scoped refuses
# both spellings of an upsert. Measured against the guard rather than assumed --
# INSERT OR REPLACE is refused because "only a plain INSERT INTO is scoped by the column
# it writes first", and ON CONFLICT because it "can rewrite a row this statement did not
# create". Reaching for either one is how a writer ends up unscoped.
_FACEPRINT_SQL = _learner_scoped(
    "SELECT learner_id, embedding_model, dimension, vector, created_at FROM faceprints WHERE faceprints.learner_id = ?"
)
# learner_id first, which is not cosmetic: the guard scopes a write by the column it
# writes first, and any other order is refused.
_INSERT_FACEPRINT_SQL = _learner_scoped(
    "INSERT INTO faceprints (learner_id, embedding_model, dimension, vector, created_at) VALUES (?, ?, ?, ?, ?)"
)
_DELETE_FACEPRINT_SQL = _learner_scoped("DELETE FROM faceprints WHERE faceprints.learner_id = ?")

_LEARNER_SCOPED_SQL: tuple[str, ...] = (
    # NEXT_LESSON_SQL is scoped too ("r.learner_id = ?"), so it is registered rather
    # than exempted -- an exemption would be a precedent for skipping the next one.
    _learner_scoped(NEXT_LESSON_SQL),
    _PROFILE_SQL,
    _LEARNER_EXISTS_SQL,
    _COMPLETED_IDS_SQL,
    _ATTEMPTS_SQL,
    _INSERT_ATTEMPT_SQL,
    _PRACTISED_LANGUAGES_SQL,
    _FACEPRINT_SQL,
    _INSERT_FACEPRINT_SQL,
    _DELETE_FACEPRINT_SQL,
)


def _profile_from_row(row: sqlite3.Row) -> LearnerProfile:
    """Build a learner profile from one database row."""
    return LearnerProfile(
        id=str(row["id"]),
        display_name=str(row["display_name"]),
        created_at=int(row["created_at"]),
    )


def _faceprint_from_row(row: sqlite3.Row) -> Faceprint:
    """Build a faceprint from one database row, unpacking the vector back to floats.

    The unpack mirrors _pack_vector exactly -- same byte order, same width -- and the
    dimension comes from the row rather than from the blob's length, so a row whose two
    disagree raises struct.error here instead of returning a quietly truncated face.
    The schema's length CHECK should make that impossible; _FACEPRINT_ABSORBS is what
    stops it ending a conversation turn if it ever is not.
    """
    dimension = int(row["dimension"])
    blob = bytes(row["vector"])
    return Faceprint(
        learner_id=str(row["learner_id"]),
        embedding_model=str(row["embedding_model"]),
        dimension=dimension,
        vector=struct.unpack(f"{_VECTOR_FORMAT_PREFIX}{dimension}f", blob),
        created_at=int(row["created_at"]),
    )


def _lesson_from_row(row: sqlite3.Row) -> Lesson:
    """Build a lesson from one database row."""
    return Lesson(
        id=str(row["id"]),
        language_code=str(row["language_code"]),
        position=int(row["position"]),
        title=str(row["title"]),
        objective=str(row["objective"]),
    )


def _lesson_source_from_row(row: sqlite3.Row) -> LessonSource:
    """Build a lesson's provenance record from one database row.

    module, unit and page stay None rather than becoming "None" or 0: which of them
    carry a value is what tells a caller whether this lesson can be looked up on a
    page, and a placeholder would read later as a citation nobody can follow.
    """
    module, unit, page = row["module"], row["unit"], row["page"]
    return LessonSource(
        lesson_id=str(row["lesson_id"]),
        origin=str(row["origin"]),
        course=str(row["course"]),
        module=None if module is None else str(module),
        unit=None if unit is None else str(unit),
        page=None if page is None else int(page),
    )


def _turn_from_row(row: sqlite3.Row) -> DialogueTurn:
    """Build one dialogue turn from one database row."""
    return DialogueTurn(
        position=int(row["position"]),
        speaker=str(row["speaker"]),
        text=str(row["text"]),
    )


def _note_from_row(row: sqlite3.Row) -> UsageNote:
    """Build one usage note from one database row."""
    return UsageNote(number=int(row["number"]), text=str(row["text"]))


def _drill_from_row(row: sqlite3.Row) -> Drill:
    """Build one drill from one database row.

    The four content columns stay None where the row leaves them None, because which
    ones are filled is what says how this drill is run -- a repetition drill with an
    empty-string cue would look to a caller like a cue-response drill with no question.
    """
    target_text, english_gloss = row["target_text"], row["english_gloss"]
    cue, expected_response = row["cue"], row["expected_response"]
    return Drill(
        position=int(row["position"]),
        kind=str(row["kind"]),
        target_text=None if target_text is None else str(target_text),
        english_gloss=None if english_gloss is None else str(english_gloss),
        cue=None if cue is None else str(cue),
        expected_response=None if expected_response is None else str(expected_response),
    )


def _attempt_from_row(row: sqlite3.Row) -> LessonAttempt:
    """Build a lesson attempt from one database row."""
    score = row["score"]
    return LessonAttempt(
        learner_id=str(row["learner_id"]),
        lesson_id=str(row["lesson_id"]),
        outcome=str(row["outcome"]),
        score=None if score is None else int(score),
        recorded_at=int(row["recorded_at"]),
    )


def store_is_available(instance_path: str | Path | None = None) -> bool:
    """Report whether the learner store can currently be read.

    The readers answer None both for "no such thing" and for "cannot look it up", which
    a caller must not conflate when telling a person what is true. This is how they tell
    the two apart, without either reader having to raise.

    One thing it deliberately does NOT report as breakage: an instance_path that is not
    a path. That is a caller error on a store which may be perfectly healthy, and saying
    False about it would be the same wrong-bucket move this function exists to prevent
    -- so it is refused separately, with its own message, before anything is opened.
    """
    refusal = None if instance_path is None else _cannot_be_a_path(instance_path)
    if refusal is not None:
        logger.warning("The instance path is not a path: %s", refusal)
        return False

    connection: sqlite3.Connection | None = None
    try:
        connection = connect(instance_path)
        connection.execute(_STORE_READABLE_SQL).fetchone()
        return True
    except _READER_ABSORBS as exc:
        logger.warning("The learner store is not readable: %s", _log_safe(exc))
        return False
    finally:
        if connection is not None:
            connection.close()


def get_profile(learner_id: str, *, instance_path: str | Path | None = None) -> LearnerProfile | None:
    """Return the learner's profile, or None when it cannot be produced.

    None means there is no such learner -- unless the store is unreadable, in which
    case a warning is logged and this also returns None. Call store_is_available when
    the difference matters; the two situations should not be described the same way to
    a person.
    """
    refusal = _cannot_name_a_learner(learner_id)
    if refusal is not None:
        logger.warning("Could not read a learner id: %s", refusal)
        return None

    connection: sqlite3.Connection | None = None
    try:
        connection = connect(instance_path)
        row: sqlite3.Row | None = connection.execute(_PROFILE_SQL, (learner_id,)).fetchone()
        return None if row is None else _profile_from_row(row)
    except _READER_ABSORBS as exc:
        # Never the learner id: these are personal data and this is a log line.
        logger.warning("Could not read a learner profile: %s", _log_safe(exc))
        return None
    finally:
        if connection is not None:
            connection.close()


def get_practised_languages(
    learner_id: str, *, instance_path: str | Path | None = None
) -> tuple[PractisedLanguage, ...]:
    """Return the languages this learner has actually worked on, ordered by name.

    A language appears once the learner has really attempted it, finished or not.
    Lessons they skipped do not count: declining a lesson is not practice, so a
    language they have only skipped is absent here and the tutor may offer it as new.

    Empty means they have practised nothing -- and, because an unreadable store also
    yields empty after logging a warning, call store_is_available before telling a
    person they have never practised.
    """
    refusal = _cannot_name_a_learner(learner_id)
    if refusal is not None:
        logger.warning("Could not read a learner id: %s", refusal)
        return ()

    connection: sqlite3.Connection | None = None
    try:
        connection = connect(instance_path)
        rows = connection.execute(_PRACTISED_LANGUAGES_SQL, (learner_id,)).fetchall()
        return tuple(
            PractisedLanguage(
                code=str(row["code"]),
                name=str(row["name"]),
                attempts=int(row["attempts"]),
                completed=int(row["completed"]),
            )
            for row in rows
        )
    except _READER_ABSORBS as exc:
        # Never the learner id: these are personal data and this is a log line.
        logger.warning("Could not read a learner's practised languages: %s", _log_safe(exc))
        return ()
    finally:
        if connection is not None:
            connection.close()


def get_lesson(lesson_id: str, *, instance_path: str | Path | None = None) -> Lesson | None:
    """Return one lesson from the catalog, or None if there is no such lesson.

    Takes no learner: a lesson is shared reference data, the same for everyone.

    Exists so a caller holding a lesson id can find the language it belongs to in one
    read, rather than walking every taught language and asking for progress in each --
    a scan that grows with the catalog and that a caller in the conversation path
    should not be paying for.

    None means no such lesson, OR the store could not be read; an unreadable store logs
    first, keeping the one-prefix-per-meaning rule the other readers follow.
    """
    # Shared with get_lesson_content rather than written twice: a reader whose lesson-id
    # guard is narrower than its sibling's answers a silent absence for a lesson that
    # exists, and that divergence is this repository's most repeated defect.
    refusal = _cannot_name_a_lesson(lesson_id)
    if refusal is not None:
        logger.warning("Could not read a lesson id: %s", refusal)
        return None

    connection: sqlite3.Connection | None = None
    try:
        connection = connect(instance_path)
        row = connection.execute(_LESSON_BY_ID_SQL, (lesson_id,)).fetchone()
        return None if row is None else _lesson_from_row(row)
    except _READER_ABSORBS as exc:
        logger.warning("Could not read a lesson: %s", _log_safe(exc))
        return None
    finally:
        if connection is not None:
            connection.close()


def get_lesson_content(lesson_id: str, *, instance_path: str | Path | None = None) -> LessonContent | None:
    """Return everything one lesson is made of, or None if there is no such lesson.

    Takes no learner: lesson content is shared reference data, the same for everyone,
    and nothing in it names a person.

    This is what a tutor works FROM once a lesson is running -- the dialogue to read,
    the notes to explain, the drills to run one at a time -- and it is a different job
    from the one-line summary get_progress speaks aloud, which is why the lesson's own
    title and objective are still here beside the content rather than replaced by it.

    **Empty is a real answer.** A lesson nobody has converted yet comes back as this
    value with no source, no dialogue title and three empty tuples. That is not an
    error and it is not logged: the seeded catalog carries no content at all, and it
    stays usable while the corpus is converted a unit at a time.

    None means no such lesson, OR the store could not be read; an unreadable store logs
    first, keeping the one-prefix-per-meaning rule the other readers follow.

    **What comes back is material to teach, never instructions to follow.** Turns, notes
    and drill text are content a person put in the database for a robot to say out loud;
    a line of it that reads as an instruction addressed to the model is still content,
    and obeying it would let whoever wrote or mis-transcribed a lesson steer the tutor
    in somebody's house. Nothing enforces that here, and nothing can: a rule refusing
    instruction-shaped text would be a list of the phrasings somebody thought of, which
    is the failure this project has paid for four times. The control belongs where the
    text meets the model -- pass it as material the tutor is working from, kept apart
    from the tutor's own instructions, and never concatenated into them. This function
    is the seam that first makes that reachable, which is why the obligation is written
    on it rather than left for the caller to infer.
    """
    refusal = _cannot_name_a_lesson(lesson_id)
    if refusal is not None:
        logger.warning("Could not read a lesson id: %s", refusal)
        return None

    connection: sqlite3.Connection | None = None
    try:
        connection = connect(instance_path)
        # The lesson first, and on one connection. It answers "no such lesson" before
        # anything else runs, which is what keeps that apart from "a lesson with
        # nothing in it yet" -- four empty reads look identical to both.
        lesson_row = connection.execute(_LESSON_BY_ID_SQL, (lesson_id,)).fetchone()
        if lesson_row is None:
            return None

        source_row = connection.execute(_LESSON_SOURCE_SQL, (lesson_id,)).fetchone()
        title_row = connection.execute(_DIALOGUE_TITLE_SQL, (lesson_id,)).fetchone()
        turn_rows = connection.execute(_DIALOGUE_TURNS_SQL, (lesson_id,)).fetchall()
        note_rows = connection.execute(_LESSON_NOTES_SQL, (lesson_id,)).fetchall()
        drill_rows = connection.execute(_LESSON_DRILLS_SQL, (lesson_id,)).fetchall()

        return LessonContent(
            lesson=_lesson_from_row(lesson_row),
            source=None if source_row is None else _lesson_source_from_row(source_row),
            dialogue_title=None if title_row is None else str(title_row["title"]),
            turns=tuple(_turn_from_row(row) for row in turn_rows),
            notes=tuple(_note_from_row(row) for row in note_rows),
            drills=tuple(_drill_from_row(row) for row in drill_rows),
        )
    except _READER_ABSORBS as exc:
        logger.warning("Could not read a lesson's content: %s", _log_safe(exc))
        return None
    finally:
        if connection is not None:
            connection.close()


def get_language_catalog(*, instance_path: str | Path | None = None) -> tuple[CatalogLanguage, ...]:
    """Return every language this robot teaches, ordered by name.

    Takes no learner: the catalog is shared reference data, the same for everyone.

    Empty means the store could not be read, OR the catalog holds no rows -- and a
    caller must treat both the same way, because neither supports telling a person
    which languages are taught. An unreadable store logs first, keeping the
    one-prefix-per-meaning rule the other readers follow; an empty table is silent.

    This exists so a caller can decide "not taught here" from evidence. get_progress
    answers None for a language it does not teach, for a store it cannot read, and
    for an argument it will not accept -- so a caller reading that None as absence is
    guessing. A language missing from a NON-EMPTY catalog is a fact instead.
    """
    connection: sqlite3.Connection | None = None
    try:
        connection = connect(instance_path)
        rows = connection.execute(_LANGUAGE_CATALOG_SQL).fetchall()
        catalog = tuple(
            CatalogLanguage(
                code=str(row["code"]),
                name=str(row["name"]),
                has_material=bool(row["has_material"]),
            )
            for row in rows
        )
        # Shape, never a value: two counts, and no language name either -- a catalog
        # is not personal data but this reader follows the same rule as the others.
        language_count = len(catalog)
        # NOT split_catalog_by_material, deliberately, and the honest reason is not
        # the first one I wrote down. D35 routed every other site through that helper
        # and tried this one too; the log guard refused
        # `len(split_catalog_by_material(catalog)[0])`, because a name interpolated
        # into a log line must be built from a producer on _SAFE_PRODUCERS. I recorded
        # that as "the guard forbids routing this site through the helper", and review
        # showed it is false: binding `with_material, _ = split_catalog_by_material(...)`
        # and then `len(with_material)` passes unchanged, since the intermediate is not
        # a permitted log name and so is never inspected.
        #
        # Which is exactly why the copy stays. That form does not satisfy the guard,
        # it LAUNDERS the value past it -- an unchecked intermediate one line above a
        # log call -- and doing that to save a duplicated comprehension would trade a
        # real control for tidiness. Widening _SAFE_PRODUCERS instead is the trade
        # W39 already got wrong: it added `sum`, and review found a working bypass the
        # same day. The count never leaves this line, and the duplication is cheap.
        with_material_count = len([entry for entry in catalog if entry.has_material])
        logger.info("Catalog read: languages=%d with_material=%d", language_count, with_material_count)
        return catalog
    except _READER_ABSORBS as exc:
        # No learner is bound here, but _log_safe stays: an exception's text can carry
        # a path, and this follows the same rule as every other reader regardless.
        logger.warning("Could not read the language catalog: %s", _log_safe(exc))
        return ()
    finally:
        if connection is not None:
            connection.close()


def get_progress(
    learner_id: str, language_code: str, *, instance_path: str | Path | None = None
) -> LanguageProgress | None:
    """Return the learner's standing in one language, or None if it is not taught here.

    None means this robot has no such language, so the tutor should say so rather than
    offer a lesson. A language that is taught but never practised comes back populated
    with an empty history and the first lesson as next -- a very different answer, and
    the tutor says something different about it.

    None is doing more than one job. It also comes back when the store cannot be read,
    and when the language code is not a string -- and neither of those supports a claim
    about the lesson catalog.

    The rule that separates them is silence, not store_is_available. **A genuine
    absence logs nothing. Every other None logs a warning first.** So a caller about to
    tell a person "I do not teach that language" must know that this call was quiet. One
    prefix per meaning, and each means only that one thing:

      "Could not read a language code"  -- the code could not name a catalog row.
      "Could not read a learner id"     -- the id could not name any learner.
      "Could not read learner progress" -- the lookup itself failed; the store is at
                                           fault rather than either argument.

    An earlier round had the guard and the handler sharing that last prefix, so a
    caller error on a healthy store read as breakage -- the mirror of the mislabelling
    the round before it removed. Distinct prefixes are what keep the mapping honest.

    The rule assumes ensure_learner_database reported ready=True. A languages table
    that exists but holds no rows satisfies store_is_available's probe, and then every
    language is a silent absence -- true of the table as it stands, and still the wrong
    thing to tell a person. That state is reported one layer up, at seeding, and an app
    that serves anyway has already ignored the answer.

    store_is_available is deliberately NOT that test. It binds no caller value, so it
    answers True when the argument is the problem -- reading True as confirmation that
    the absence is real is exactly the confident falsehood this warns about.

    An unknown learner gets a fresh start rather than an error: the lesson catalog is
    not personal data, so there is nothing to withhold.
    """
    # Refused here, before the connection is opened, because the values that make this
    # function lie are ones the database ACCEPTS. A BLOB never equals TEXT, NULL never
    # equals anything, 42 and True take TEXT affinity and become "42" and "1", and "ES"
    # is simply not a spelling the catalog's CHECK permits -- every one of them binds
    # cleanly, matches nothing, and used to return a SILENT None for a language this
    # robot does teach. Refusing before the connection also keeps a genuine storage
    # failure falling through to the handler that describes one.
    #
    # Still None and still no exception: the contract is unchanged.
    refusal = _cannot_be_a_catalog_code(language_code)
    if refusal is not None:
        logger.warning("Could not read a language code: %s", refusal)
        return None

    # Refused for the same reason and with the same force. This reader's answer for an
    # unknown learner is a populated "fresh start", so a learner id that can never
    # match does not merely say the wrong thing -- it says a learner with real history
    # has completed nothing, which is the one claim the database exists to settle.
    refusal = _cannot_name_a_learner(learner_id)
    if refusal is not None:
        logger.warning("Could not read a learner id: %s", refusal)
        return None

    connection: sqlite3.Connection | None = None
    try:
        connection = connect(instance_path)
        language: sqlite3.Row | None = connection.execute(_LANGUAGE_SQL, (language_code,)).fetchone()
        if language is None:
            return None

        lessons = [_lesson_from_row(row) for row in connection.execute(_LESSONS_SQL, (language_code,))]
        completed_ids = {
            str(row["lesson_id"]) for row in connection.execute(_COMPLETED_IDS_SQL, (learner_id, language_code))
        }
        attempts = tuple(
            _attempt_from_row(row) for row in connection.execute(_ATTEMPTS_SQL, (learner_id, language_code))
        )
    except _READER_ABSORBS as exc:
        # Never the learner id: these are personal data and this is a log line.
        logger.warning("Could not read learner progress: %s", _log_safe(exc))
        return None
    finally:
        if connection is not None:
            connection.close()

    completed = tuple(lesson for lesson in lessons if lesson.id in completed_ids)
    remaining = tuple(lesson for lesson in lessons if lesson.id not in completed_ids)
    return LanguageProgress(
        learner_id=learner_id,
        language_code=str(language["code"]),
        language_name=str(language["name"]),
        completed=completed,
        remaining=remaining,
        # The same rule NEXT_LESSON_SQL states, evaluated from rows already fetched.
        # A test pins the two together so they cannot drift.
        next_lesson=remaining[0] if remaining else None,
        attempts=attempts,
    )


def record_result(
    learner_id: str,
    lesson_id: str,
    outcome: str,
    *,
    score: int | None = None,
    recorded_at: int | None = None,
    instance_path: str | Path | None = None,
) -> RecordResultOutcome:
    """Record one attempt at a lesson, reporting the outcome rather than raising.

    Never raises. A bad value from the conversation must not end the turn, so every
    failure comes back as a reason code the caller can turn into something sayable.
    """
    if outcome not in OUTCOMES:
        # Case-sensitive on purpose: silently lowercasing a model's guess would record
        # something it did not mean.
        return RecordResultOutcome(recorded=False, reason="invalid_outcome")
    # The same rule get_lesson and get_lesson_content refuse on, applied to the third
    # function in this module that takes a lesson id. Without it the parameter was the
    # odd one out: outcome, score and recorded_at each answer a caller error with a
    # caller-error code, while a lesson id of the wrong shape either bound and missed
    # (unknown_lesson, but silently) or failed to bind at all and came back as
    # storage_unavailable -- telling a caller the robot is broken when it sent nonsense.
    #
    # unknown_lesson is the honest code rather than a new one: a value that cannot
    # compare equal to any stored id names no lesson, which is exactly what the reader
    # below would have reported had the value survived to reach it.
    #
    # learner_id deliberately does NOT get a guard here, and the reason is not that its
    # ids are safer. It is that the lookup below already answers honestly for them: a
    # learner id that cannot match anything reaches _LEARNER_EXISTS_SQL, finds nothing,
    # and comes back as unknown_learner -- which is the accurate code. Its rule also
    # permits an int, because SQLite applies the column's TEXT affinity to a bound
    # number and 42 really does find the learner whose id is "42", so it could not
    # refuse the types this guard refuses even if it ran. The one value that used to
    # escape that reasoning was a lone surrogate, which failed at bind time and was
    # reported as storage_unavailable; it is caught below, at the bind, for reasons
    # given there.
    refusal = _cannot_name_a_lesson(lesson_id)
    if refusal is not None:
        logger.warning("Could not record an attempt: the lesson id was %s", refusal)
        return RecordResultOutcome(recorded=False, reason="unknown_lesson")
    # isinstance before the comparison: the caller is an LLM tool layer, so `score`
    # can be any JSON value. Comparing a str against an int would raise TypeError
    # straight through the conversation loop, which is the thing this function exists
    # to prevent. bool is excluded because it is a subclass of int and True is not a
    # score anyone meant.
    if score is not None and (not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 100):
        return RecordResultOutcome(recorded=False, reason="invalid_score")
    # The same shape as the check above, and for a sharper reason. Unchecked, a str or
    # a float reached the STRICT column and came back as rejected_by_database, and a
    # list or dict failed to bind and came back as storage_unavailable -- both of which
    # tell a caller the robot is broken when it sent nonsense. Worse, True is an int
    # subclass with no CHECK constraint to stop it, so a bool was RECORDED, silently
    # timestamping the attempt 1ms after the epoch.
    #
    # No SEMANTIC range, deliberately: the schema puts no CHECK on recorded_at the way
    # it does on score, so a "is this a sensible date" rule invented here would live in
    # one place while score's lives in two. If one is ever wanted it belongs beside
    # score's CHECK, where every writer inherits it. So 0, a negative value and a
    # far-future value are all accepted.
    #
    # The 64-bit bound is a different thing and is not optional. It is what the column
    # can represent at all, and without it an int passes the isinstance check and then
    # raises OverflowError at bind time -- not sqlite3.Error, not ValueError, so
    # nothing below catches it and it travels up through the conversation loop. That is
    # precisely what this function promises never to do.
    if recorded_at is not None and (
        not isinstance(recorded_at, int)
        or isinstance(recorded_at, bool)
        or not _SQLITE_INT_MIN <= recorded_at <= _SQLITE_INT_MAX
    ):
        return RecordResultOutcome(recorded=False, reason="invalid_recorded_at")

    when = utc_now_ms() if recorded_at is None else recorded_at
    connection: sqlite3.Connection | None = None
    try:
        connection = connect(instance_path)
        try:
            exists = connection.execute(_LEARNER_EXISTS_SQL, (learner_id,)).fetchone()
        except UnicodeEncodeError as exc:
            # The other half of the class the lesson-id guard above closes, reached the
            # only way it still can. A lone surrogate is a str the driver cannot bind,
            # and it named no learner -- storage_unavailable said the robot was broken
            # when it had been sent nonsense.
            #
            # Caught HERE rather than refused at the top, and that placement is the
            # whole of it. connect() has already succeeded, so this can only be the
            # bind, which means a surrogate in the instance PATH cannot be mislabelled
            # as a learner problem by this arm. And the refusal still goes through the
            # handler, so _log_safe is still what stops UnicodeEncodeError naming the
            # offending character and its index -- a fragment of a learner's id in a
            # log file. A test pins this function as one of the two sinks where that
            # matters; refusing before the bind would have quietly retired it.
            logger.warning("Could not record a lesson attempt: %s", _log_safe(exc))
            return RecordResultOutcome(recorded=False, reason="unknown_learner")
        if exists is None:
            return RecordResultOutcome(recorded=False, reason="unknown_learner")
        if connection.execute(_LESSON_EXISTS_SQL, (lesson_id,)).fetchone() is None:
            return RecordResultOutcome(recorded=False, reason="unknown_lesson")

        with connection:
            connection.execute(_INSERT_ATTEMPT_SQL, (learner_id, lesson_id, outcome, score, when))
    except (sqlite3.IntegrityError, OverflowError) as exc:
        # Routed through _log_safe like every sibling arm, which is a consistency fix
        # rather than a measured leak: SQLite names the constraint, never the bound
        # value, so nothing escapes today. An inconsistent sibling is how the no-PII
        # rule recurs, and this module is where that rule lives.
        # The schema's own constraints, as a backstop to the checks above. OverflowError
        # joins them because it is the one refusal that comes from the driver rather
        # than the database and is in neither sqlite3.Error nor ValueError: the check
        # above should mean it never fires, and if it ever does, a reason code is still
        # better than an exception ending the turn. Neither message carries the value.
        logger.warning("The learner database refused an attempt: %s", _log_safe(exc))
        return RecordResultOutcome(recorded=False, reason="rejected_by_database")
    except _READER_ABSORBS as exc:
        logger.warning("Could not record a lesson attempt: %s", _log_safe(exc))
        return RecordResultOutcome(recorded=False, reason="storage_unavailable")
    finally:
        if connection is not None:
            connection.close()

    return RecordResultOutcome(
        recorded=True,
        attempt=LessonAttempt(
            learner_id=learner_id,
            lesson_id=lesson_id,
            outcome=outcome,
            score=score,
            recorded_at=when,
        ),
    )


def get_faceprint(learner_id: str, *, instance_path: str | Path | None = None) -> Faceprint | None:
    """Return this learner's faceprint, or None when it cannot be produced.

    None means they have none -- unless the store is unreadable, in which case a
    warning is logged and this also returns None. Call store_is_available when the
    difference matters, exactly as with get_profile: "nobody has enrolled" and "the
    robot cannot read its database" must not be said to a person the same way.
    """
    refusal = _cannot_name_a_learner(learner_id)
    if refusal is not None:
        logger.warning("Could not read a learner id: %s", refusal)
        return None

    connection: sqlite3.Connection | None = None
    try:
        connection = connect(instance_path)
        row: sqlite3.Row | None = connection.execute(_FACEPRINT_SQL, (learner_id,)).fetchone()
        return None if row is None else _faceprint_from_row(row)
    except _FACEPRINT_ABSORBS as exc:
        # Never the learner id and never the vector: both are personal data and this is
        # a log line. _log_safe is what keeps the exception from quoting either.
        logger.warning("Could not read a faceprint: %s", _log_safe(exc))
        return None
    finally:
        if connection is not None:
            connection.close()


def save_faceprint(
    learner_id: str,
    embedding_model: str,
    vector: Sequence[float],
    *,
    instance_path: str | Path | None = None,
) -> SaveFaceprintOutcome:
    """Store this learner's faceprint, replacing any they already have.

    Never raises, for the reason record_result does not: this is called from a tool
    layer driven by a model, and a bad value must come back as a reason code rather
    than end the conversation turn.

    One faceprint per learner, so a second one replaces the first. There is no caller
    -supplied clock: created_at is stamped here, because a timestamp a caller chooses
    is a field a caller can get wrong on biometric data, and nothing needs it.
    """
    refusal = _cannot_be_an_embedding_model(embedding_model)
    if refusal is not None:
        logger.warning("Could not store a faceprint: the embedding model was %s", refusal)
        return SaveFaceprintOutcome(saved=False, reason="invalid_model")

    blob = _pack_vector(vector)
    if blob is None:
        # The shape only: a vector is personal data, so neither its values nor its
        # length go anywhere near this line.
        logger.warning("Could not store a faceprint: the vector was not a packable face vector")
        return SaveFaceprintOutcome(saved=False, reason="invalid_vector")
    dimension = len(blob) // _VECTOR_BYTES_PER_ELEMENT

    when = utc_now_ms()
    connection: sqlite3.Connection | None = None
    try:
        connection = connect(instance_path)
        try:
            exists = connection.execute(_LEARNER_EXISTS_SQL, (learner_id,)).fetchone()
        except UnicodeEncodeError as exc:
            # The same arm record_result carries, for the same reason: a lone surrogate
            # is a str the driver cannot bind and it names no learner, so unknown_learner
            # is the honest code and storage_unavailable would say the robot is broken.
            logger.warning("Could not store a faceprint: %s", _log_safe(exc))
            return SaveFaceprintOutcome(saved=False, reason="unknown_learner")
        if exists is None:
            return SaveFaceprintOutcome(saved=False, reason="unknown_learner")

        # DELETE then INSERT, in one transaction, because the guard refuses both
        # spellings of an upsert -- see the note beside the statements themselves. The
        # transaction is what makes "replace" atomic: without it a failed insert would
        # leave the learner with no faceprint at all, which is worse than the old one.
        with connection:
            connection.execute(_DELETE_FACEPRINT_SQL, (learner_id,))
            connection.execute(_INSERT_FACEPRINT_SQL, (learner_id, embedding_model, dimension, blob, when))
    except (sqlite3.IntegrityError, OverflowError) as exc:
        # The schema's constraints as a backstop to the checks above, and the one
        # refusal that can still arrive from a race: a learner deleted between the
        # existence check and the insert fails the foreign key here.
        logger.warning("The learner database refused a faceprint: %s", _log_safe(exc))
        return SaveFaceprintOutcome(saved=False, reason="rejected_by_database")
    except _FACEPRINT_ABSORBS as exc:
        logger.warning("Could not store a faceprint: %s", _log_safe(exc))
        return SaveFaceprintOutcome(saved=False, reason="storage_unavailable")
    finally:
        if connection is not None:
            connection.close()

    return SaveFaceprintOutcome(
        saved=True,
        faceprint=Faceprint(
            learner_id=learner_id,
            embedding_model=embedding_model,
            dimension=dimension,
            # Unpacked from the bytes actually stored, not echoed back from the argument.
            # float32 cannot hold every float the caller may pass, so echoing would
            # report values this database does not contain.
            vector=struct.unpack(f"{_VECTOR_FORMAT_PREFIX}{dimension}f", blob),
            created_at=when,
        ),
    )


def delete_faceprint(learner_id: str, *, instance_path: str | Path | None = None) -> int | None:
    """Remove this learner's faceprint, returning how many rows went, or None.

    A count, never a name: 0 means they had none, 1 means the row is gone, and None
    means the store could not be read and nothing can be promised either way. That
    distinction is the whole point -- erasure is a promise this app makes to a
    household, and "I could not tell" must never be reported as "it is gone".

    Exactly what 1 promises, because the difference matters for biometric data: the row
    is removed, no reader can reach it again, and the bytes are not being kept. It is
    NOT a promise that the bytes have already left the file by the time this returns.

    connect() sets PRAGMA secure_delete so a freed page is zeroed as it is written, and
    this checkpoints before returning so that write actually happens -- measured,
    without the checkpoint the delete sat in the WAL while the main file still held the
    vector and the model name in full, with this function already returning 1.

    The checkpoint is PASSIVE, so it yields rather than waiting on another connection:
    an erase can never block on somebody else's reader. The price is that ONE ordinary
    open read transaction is enough to defer it -- measured, not supposed: with a second
    connection sitting in BEGIN + SELECT, wal_checkpoint(PASSIVE) returned
    (busy=0, log_frames=2, checkpointed=0), copying nothing while reporting no
    contention, and the packed vector and the model name were both still recoverable
    from the main file. Heavy load is not required; a single idle reader does it.

    A deferral is therefore reported rather than silent (see below), and it is
    temporary: the bytes go at the next checkpoint no reader is pinning. Measured, as
    soon as that reader let go, both the vector and the model name were gone from the
    file.

    A learner id that could never name anybody is answered with 0 rather than a
    refusal, because that is the truthful answer: no such row existed to remove.
    """
    refusal = _cannot_name_a_learner(learner_id)
    if refusal is not None:
        logger.warning("Could not read a learner id: %s", refusal)
        return 0

    connection: sqlite3.Connection | None = None
    try:
        connection = connect(instance_path)
        with connection:
            cursor = connection.execute(_DELETE_FACEPRINT_SQL, (learner_id,))
        # Ship the delete into the main database before reporting it done.
        #
        # Without this the promise is conditional on nobody else holding a connection,
        # and measured, that condition fails in the obvious way: with a second
        # connection open the delete stays in the WAL, the main file still holds the
        # original page, and the packed vector AND the model name were both fully
        # recoverable from learners.v1.sqlite3 while this function had already returned
        # 1. secure_delete zeroes a page when it is WRITTEN, and a checkpoint is what
        # writes it. Measured cost: none worth naming. delete_faceprint runs at a
        # median 0.660 ms with this line and 0.664 ms with it removed, over 300 samples
        # each -- the checkpoint is inside the noise of the call it protects. (An
        # earlier version of this comment reported 0.73-1.18 ms as the checkpoint's
        # cost; that was the whole call, not this line's share of it.)
        #
        # PASSIVE rather than TRUNCATE: a passive checkpoint yields to readers instead
        # of waiting on them, so an erase can never block on somebody else's open
        # connection. The cost is that it can copy nothing, and SAY NOTHING about it --
        # measured, one open read transaction gets (busy=0, log_frames=2,
        # checkpointed=0), so busy is 0 and a busy-flag check would miss it entirely.
        #
        # So compare the two counts instead, and report a deferral. The row is gone
        # either way -- this does not change what is returned -- but "the bytes are
        # still in the file for now" is exactly the thing that must not be silent on
        # biometric data. Counts only: no path, no learner id, no vector.
        checkpoint = connection.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        if checkpoint is not None:
            _, log_frames, checkpointed = (int(value) for value in tuple(checkpoint)[:3])
            if checkpointed < log_frames:
                logger.warning(
                    "A faceprint was deleted but its pages are still in the write-ahead "
                    "log: %d of %d frames checkpointed. They leave the database file at "
                    "the next checkpoint no reader is holding open.",
                    checkpointed,
                    log_frames,
                )
        return int(cursor.rowcount)
    except _FACEPRINT_ABSORBS as exc:
        logger.warning("Could not delete a faceprint: %s", _log_safe(exc))
        return None
    finally:
        if connection is not None:
            connection.close()
