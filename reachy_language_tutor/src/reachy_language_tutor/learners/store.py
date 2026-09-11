"""Learner database: path derivation, connections, schema application, and seeding.

This module owns the SQLite file and nothing else yet. The query interface the
conversation tools will call (profiles, progress, recording results) is a separate
task and belongs here too when it lands -- all SQL in this app must stay inside this
package so the storage layer can be swapped for a hosted backend later without
touching any caller.
"""

from __future__ import annotations
import os
import json
import time
import logging
import sqlite3
import threading
from pathlib import Path
from dataclasses import dataclass


logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
SEED_VERSION = 1
LEARNER_DB_FILENAME = "learners.v1.sqlite3"
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

SEED_LANGUAGES: tuple[tuple[str, str], ...] = (
    ("es", "Spanish"),
    ("fr", "French"),
)

# (id, language_code, position, title, objective)
SEED_LESSONS: tuple[tuple[str, str, int, str, str], ...] = (
    (
        "es-01-greetings",
        "es",
        1,
        "Greetings and goodbyes",
        "Greet someone, ask how they are, and say goodbye: hola, buenos días, ¿cómo estás?, adiós.",
    ),
    (
        "es-02-introductions",
        "es",
        2,
        "Introducing yourself",
        "Give your name and where you are from, and ask the same back: me llamo…, soy de…, ¿y tú?",
    ),
    (
        "es-03-numbers",
        "es",
        3,
        "Numbers one to twenty",
        "Count to twenty out loud and say your age and a phone number.",
    ),
    (
        "es-04-ordering-food",
        "es",
        4,
        "Ordering food and drink",
        "Order in a café and ask what something costs: quisiera…, ¿cuánto cuesta?",
    ),
    (
        "es-05-directions",
        "es",
        5,
        "Asking for directions",
        "Ask where a place is and follow a simple answer: ¿dónde está…?, a la derecha, a la izquierda.",
    ),
    (
        "es-06-daily-routine",
        "es",
        6,
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
)

# A deliberately neutral placeholder rather than a plausible human name, so nobody
# mistakes demo data for a real household member and a screenshot leaks nothing.
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
    """Return the learner database path for this app instance."""
    if instance_path is not None:
        return Path(instance_path).expanduser() / LEARNER_DB_FILENAME

    data_home = os.getenv("XDG_DATA_HOME")
    data_root = Path(data_home).expanduser() if data_home else Path.home() / ".local" / "share"
    return data_root / "reachy_language_tutor" / LEARNER_DB_FILENAME


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
    return connection


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

    if isinstance(parsed, (str, int, float)):
        # A legacy single id that happens to be valid JSON on its own, e.g. "123".
        # Split the PARSED value, not the raw text: a quoted JSON string would otherwise
        # keep its quotes and yield an id matching no real learner.
        return _split_legacy_seeded_ids(parsed if isinstance(parsed, str) else str(parsed))

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
        connection.executemany(
            "INSERT INTO lessons (id, language_code, position, title, objective) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "language_code = excluded.language_code, position = excluded.position, "
            "title = excluded.title, objective = excluded.objective",
            SEED_LESSONS,
        )

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
    except (sqlite3.Error, OSError, ValueError, TypeError, RuntimeError) as exc:
        logger.warning("Learner database at %s is unavailable: %s", path or "<unresolved path>", exc)
        return EnsureResult(path=path or Path(LEARNER_DB_FILENAME), ready=False, error=str(exc))
    finally:
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error as close_exc:
                # An unguarded close() here would propagate and discard the result the
                # except branch just returned.
                logger.warning("Failed to close the learner database: %s", close_exc)

    if schema_applied or seeded:
        logger.info(
            "Learner database ready at %s (schema_applied=%s, seeded=%s)",
            path,
            schema_applied,
            seeded,
        )
    return EnsureResult(path=path, ready=True, schema_applied=schema_applied, seeded=seeded)


def utc_now_ms() -> int:
    """Return the current time in milliseconds since the epoch."""
    return int(time.time() * 1000)
