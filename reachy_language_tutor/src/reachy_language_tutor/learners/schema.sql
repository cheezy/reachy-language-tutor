-- Learner database schema for the Reachy Mini language tutor.
--
-- DDL ONLY. Seed rows live in store.py as Python tuples fed through executemany()
-- with ? placeholders, because Connection.executescript() cannot bind parameters --
-- any seed row written here would have to be interpolated text.
--
-- Every table is STRICT (SQLite >= 3.37) so column types are actually enforced.
-- Every statement is IF NOT EXISTS so re-applying this script is a no-op.
--
-- Foreign keys are only enforced when a connection sets PRAGMA foreign_keys = ON.
-- store.py's connect() does that for every connection; nothing else opens this
-- database. The pragma below documents the intent but does not carry over.

PRAGMA foreign_keys = ON;

-- Bookkeeping for the seed data version. The schema version itself lives in
-- PRAGMA user_version, which is readable on a brand-new file before any table
-- exists, so there is no chicken-and-egg at first start.
CREATE TABLE IF NOT EXISTS schema_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
) STRICT;

-- One row per person who practises on this robot.
-- Deliberately minimal: no email, no faceprint, no locale. docs/plan.md says only
-- names, emails and learning progress leave the home, and that a learner must be able
-- to delete their data. Nothing on the robot needs an email, so this schema stores
-- none -- and deletion is one DELETE, because everything else cascades from here.
CREATE TABLE IF NOT EXISTS learners (
  id           TEXT PRIMARY KEY,
  display_name TEXT NOT NULL CHECK (length(trim(display_name)) > 0),
  created_at   INTEGER NOT NULL
) STRICT;

-- The languages the tutor can teach.
CREATE TABLE IF NOT EXISTS languages (
  code TEXT PRIMARY KEY CHECK (code = lower(code) AND length(code) BETWEEN 2 AND 8),
  name TEXT NOT NULL UNIQUE
) STRICT;

-- The ordered lesson catalog, one row per lesson.
--
-- UNIQUE (language_code, position) is what makes "the next lesson" unambiguous
-- rather than merely ordered: two lessons cannot tie at the same position, so
-- ORDER BY position LIMIT 1 has exactly one answer. Never rely on insertion
-- order or on sorting by title.
CREATE TABLE IF NOT EXISTS lessons (
  id            TEXT PRIMARY KEY,
  language_code TEXT    NOT NULL REFERENCES languages(code) ON DELETE CASCADE,
  position      INTEGER NOT NULL CHECK (position > 0),
  title         TEXT    NOT NULL CHECK (length(trim(title)) > 0),
  objective     TEXT    NOT NULL CHECK (length(trim(objective)) > 0),
  UNIQUE (language_code, position)
) STRICT;

-- An append-only log of lesson attempts, not one row per (learner, lesson).
-- Retries and partial attempts stay visible, and recording a result is a plain
-- INSERT with no upsert logic. At ~20 households this table stays small.
--
-- Note: UNIQUE (language_code, position) on lessons already materialises the
-- index that orders the catalog. Do not add a second index for it -- that would
-- double write cost on weak flash for no benefit.
CREATE TABLE IF NOT EXISTS lesson_results (
  id          INTEGER PRIMARY KEY,
  learner_id  TEXT    NOT NULL REFERENCES learners(id) ON DELETE CASCADE,
  lesson_id   TEXT    NOT NULL REFERENCES lessons(id)  ON DELETE CASCADE,
  outcome     TEXT    NOT NULL CHECK (outcome IN ('completed', 'partial', 'skipped')),
  score       INTEGER CHECK (score IS NULL OR score BETWEEN 0 AND 100),
  recorded_at INTEGER NOT NULL
) STRICT;

-- Covers the "has this learner completed this lesson?" lookup that decides the
-- next lesson.
CREATE INDEX IF NOT EXISTS idx_lesson_results_learner_lesson
  ON lesson_results (learner_id, lesson_id);
