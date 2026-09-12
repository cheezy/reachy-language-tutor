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

-- ------------------------------------------------------------------ lesson content
--
-- Everything below is what a lesson is MADE OF, as opposed to how it is listed.
-- A lesson row stays what it was -- id, language, position, title, objective -- because
-- that one-line summary is what get_progress speaks aloud, and it is a different job
-- from running the lesson.
--
-- All of it is OPTIONAL. The thirty seeded lessons carry no content and must keep
-- working while the corpus is converted a unit at a time, so "no content" is an empty
-- read rather than an error.
--
-- Each table keys on lesson_id and cascades from lessons, so removing a lesson removes
-- its content. None of it is personal data: this is shared catalog material, identical
-- for every learner, and nothing here may ever name one. That is why these tables are
-- absent from _PERSONAL_TABLES in store.py and why their statements are not
-- learner-scoped -- the same standing the lessons and languages tables have.
--
-- Which answers the first question five new tables raise: forgetting a household is
-- still one statement. Nothing below cascades from a person -- it all cascades from a
-- lesson -- so deleting that person's row still removes everything the database holds
-- about them, and none of these rows was ever theirs to delete.
--
-- Why new tables rather than new columns on lessons: schema changes reach a robot that
-- already has a database by re-running this script under a bumped SCHEMA_VERSION, and
-- every statement here is IF NOT EXISTS. A CREATE TABLE IF NOT EXISTS with extra
-- columns is a no-op against an existing table, so a column added to `lessons` would
-- silently never arrive in the field. ALTER is not available either -- a test pins this
-- file to an allow-list of PRAGMA, CREATE TABLE and CREATE INDEX. New tables arrive;
-- new columns on old tables do not.

-- Where a lesson came from, so a correction can be checked against the source and a
-- rights question answered without re-deriving it.
--
-- Two origins, and the CHECK makes each one's shape the only shape it can have. A
-- lesson written for this app has no page to cite, and recording "module 0, page 0"
-- for it would be a fabrication that later reads as a citation; a lesson converted
-- from a published course has all four, or the citation cannot be followed back.
-- Adding a third origin without giving it a shape clause below refuses every row of
-- that origin, which is the direction to fail in.
--
-- `course` is required of BOTH, which is deliberate rather than an oversight: naming
-- this app's own catalog is not a citation to anybody else's work, and "what is this
-- lesson part of" has a truthful answer either way. It is the page reference that
-- must not be invented, and that is what the branches below govern.
--
-- Each IS NOT NULL is load-bearing and cannot be dropped in favour of the trim() test
-- beside it. length(trim(NULL)) is NULL, not 0, and a CHECK is satisfied by NULL as
-- readily as by true -- so a converted lesson missing its unit would be ACCEPTED by
-- the obvious shorter spelling of this rule. Measured, not assumed.
CREATE TABLE IF NOT EXISTS lesson_sources (
  lesson_id TEXT    PRIMARY KEY REFERENCES lessons(id) ON DELETE CASCADE,
  origin    TEXT    NOT NULL CHECK (origin IN ('written_for_this_app', 'converted_from_course')),
  course    TEXT    NOT NULL CHECK (length(trim(course)) > 0),
  module    TEXT,
  unit      TEXT,
  page      INTEGER,
  CHECK (
    (origin = 'written_for_this_app'
       AND module IS NULL AND unit IS NULL AND page IS NULL)
    OR
    (origin = 'converted_from_course'
       AND module IS NOT NULL AND length(trim(module)) > 0
       AND unit   IS NOT NULL AND length(trim(unit))   > 0
       AND page   IS NOT NULL AND page > 0)
  )
) STRICT;

-- The dialogue's own title, in the target language -- "Saluti e presentazioni", not
-- the lesson's English title. At most one per lesson, and separate from the turns so
-- that a lesson may have turns before anyone has titled them, or a title while the
-- turns are still being checked against the scan.
--
-- A one-column table reads as over-normalisation, so the reason is worth stating: the
-- title cannot live on `lessons`, because a column added there reaches no robot that
-- already has a database. It is a table because it could not be a column.
CREATE TABLE IF NOT EXISTS lesson_dialogues (
  lesson_id TEXT PRIMARY KEY REFERENCES lessons(id) ON DELETE CASCADE,
  title     TEXT NOT NULL CHECK (length(trim(title)) > 0)
) STRICT;

-- The dialogue itself: one row per turn, in order, each with the speaker who says it.
--
-- PRIMARY KEY (lesson_id, position) is what makes the order unambiguous rather than
-- merely recorded -- two turns cannot tie, so ORDER BY position has exactly one answer.
-- The same reasoning as lessons UNIQUE (language_code, position).
--
-- speaker is a label from the source ("Capitano", "Signora Rossi"), never a learner:
-- these rows are the same for every household.
CREATE TABLE IF NOT EXISTS lesson_dialogue_turns (
  lesson_id TEXT    NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
  position  INTEGER NOT NULL CHECK (position > 0),
  speaker   TEXT    NOT NULL CHECK (length(trim(speaker)) > 0),
  text      TEXT    NOT NULL CHECK (length(trim(text)) > 0),
  PRIMARY KEY (lesson_id, position)
) STRICT;

-- Numbered usage and grammar notes on the dialogue, in English. The number is the
-- source's own numbering, which is what a person checking against the page needs, and
-- it doubles as the order.
CREATE TABLE IF NOT EXISTS lesson_notes (
  lesson_id TEXT    NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
  number    INTEGER NOT NULL CHECK (number > 0),
  text      TEXT    NOT NULL CHECK (length(trim(text)) > 0),
  PRIMARY KEY (lesson_id, number)
) STRICT;

-- The drills, in order, each carrying its type.
--
-- Two types, because these are the two that are runnable as speech:
--
--   repetition   -- the tutor says target_text, the learner repeats it, and
--                   english_gloss is what it means. Two fields, never one: the tutor
--                   needs to say the target and explain the gloss separately.
--   cue_response -- the tutor says cue, the learner answers, and expected_response is
--                   the right answer. That is what makes this type CHECKABLE, and it
--                   is the reason the column exists at all.
--
-- The second CHECK is an allow-list of row SHAPES, not a list of bad combinations: a
-- row must BE one of the two named shapes or it is refused. A type added to the first
-- CHECK without a shape clause here can store no rows, so the failure is a refusal
-- rather than a half-filled drill the tutor cannot run.
CREATE TABLE IF NOT EXISTS lesson_drills (
  lesson_id         TEXT    NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
  position          INTEGER NOT NULL CHECK (position > 0),
  kind              TEXT    NOT NULL CHECK (kind IN ('repetition', 'cue_response')),
  target_text       TEXT,
  english_gloss     TEXT,
  cue               TEXT,
  expected_response TEXT,
  PRIMARY KEY (lesson_id, position),
  CHECK (
    (kind = 'repetition'
       AND target_text   IS NOT NULL AND length(trim(target_text))   > 0
       AND english_gloss IS NOT NULL AND length(trim(english_gloss)) > 0
       AND cue IS NULL AND expected_response IS NULL)
    OR
    (kind = 'cue_response'
       AND cue               IS NOT NULL AND length(trim(cue))               > 0
       AND expected_response IS NOT NULL AND length(trim(expected_response)) > 0
       AND target_text IS NULL AND english_gloss IS NULL)
  )
) STRICT;
