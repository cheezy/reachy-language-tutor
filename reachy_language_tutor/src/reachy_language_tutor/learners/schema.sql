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
-- Deliberately minimal: no email, no locale, and no face data of any kind. docs/plan.md
-- says only names, emails and learning progress leave the home, and that a learner must
-- be able to delete their data. Nothing on the robot needs an email, so this schema
-- stores none -- and deletion is one DELETE, because everything else cascades from here,
-- the faceprints table below included.
--
-- display_name carries a byte bound as well as the non-empty test, and the reason is
-- the NUL mechanism the faceprints table below documents at length: length() and
-- trim() stop at the first NUL, so 'A' + NUL + '/Users/secret/kid-face.jpg' + 5000
-- more characters satisfies length(trim(...)) > 0 as a one-character name and is
-- stored whole. Measured on this table, which is why the bound is here.
--
-- What it closes and what it does not, stated rather than implied. The byte bound
-- refuses the unbounded case; a SHORT hidden path still fits under 200 bytes and is
-- still accepted. The ASCII-only rule that closes it completely on embedding_model
-- would be wrong here, because this column holds a person's name -- accents,
-- non-Latin scripts, apostrophes and combining marks are all real names, and all were
-- verified to still pass.
--
-- The durable guard therefore belongs in the writer, and as of enrolment (W28) it
-- exists: store._cannot_be_a_display_name refuses, by Unicode general category, every
-- character that is not a letter, a mark, a digit or one of five name punctuation
-- marks. That is an allow-list, so it closes the NUL case, the control-character case
-- and the bidi-override case together, and it is the ONLY rule that reaches a robot
-- which already has a database -- CREATE TABLE IF NOT EXISTS is a no-op against an
-- existing table, so nothing added below this line reaches one.
--
-- instr() is added here anyway, for fresh installs, and the choice of function is the
-- whole point: length(), trim() and replace() all stop at the first NUL and so cannot
-- see the hidden tail, while instr(display_name, char(0)) reads the whole value and
-- returns 4 for 'Ana' + NUL + a path. Measured against this exact DDL before it was
-- written. It is a backstop to the writer's guard and not a substitute for it: SQLite
-- cannot express a Unicode category, so a lone non-breaking space and an embedded
-- newline still satisfy every clause here and are refused only in Python.
CREATE TABLE IF NOT EXISTS learners (
  id           TEXT PRIMARY KEY,
  display_name TEXT NOT NULL CHECK (length(trim(display_name)) > 0
                                    AND length(CAST(display_name AS BLOB)) BETWEEN 1 AND 200
                                    AND instr(display_name, char(0)) = 0),
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

-- A person's faceprint: numeric face data, and never an image or a path to one.
--
-- docs/plan.md promises faceprints are numeric face data, never photos, and that they
-- stay on the device. This table is where that promise is kept or quietly broken, so
-- the column list is the whole of it: an id, the model that produced the numbers, how
-- many numbers there are, the numbers, and when. No image, no crop, no thumbnail, and
-- no path to a file on disk -- a path column is how "we only store numbers" becomes
-- untrue without anybody editing the sentence.
--
-- What this does NOT claim: that a face cannot be reconstructed from these numbers.
-- The published template-inversion work says an approximate face image often can be
-- recovered from an embedding given the model, so a faceprint is biometric data to be
-- guarded, not a one-way hash to be relaxed about. Saying otherwise here would be the
-- sentence a later reader leans on when deciding whether one may leave the device. A test reads PRAGMA table_info and
-- pins the permitted columns as an allow-list, so a later column fails it whatever it
-- is called.
--
-- One row per learner, keyed on the person. Several faceprints per person would match
-- better across angles, and it is deliberately not what this does:
--   * erasure (the promise a household can act on) is then exactly one row, and
--     "did it work" is a count of 0 or 1 rather than a set somebody has to reason about;
--   * the learner-scoping guard in store.py refuses both spellings of an upsert --
--     measured, not assumed: INSERT OR REPLACE is refused because "only a plain INSERT
--     INTO is scoped by the column it writes first", and ON CONFLICT because it "can
--     rewrite a row this statement did not create" -- so replacing a faceprint is a
--     DELETE and an INSERT in one transaction either way. With one row per learner that
--     has a single deterministic meaning: the new one replaces the old.
-- If W26 ever needs several angles, it arrives as a NEW TABLE, because CREATE TABLE IF
-- NOT EXISTS is a no-op against an existing table and ALTER is outside the allow-list a
-- test pins this file to. That is the same escape hatch lesson_dialogues took.
--
-- The vector is little-endian IEEE-754 binary32 -- struct.pack("<{n}f") -- so its byte
-- length is exactly dimension * 4, which is what the CHECK below tests. Byte order is
-- pinned in three places on purpose: the "<" in store.py, a test asserting [1.0] stores
-- b"\x00\x00\x80?" rather than b"?\x80\x00\x00", and that CHECK. The bound on dimension
-- keeps the blob small; it is a bound, not a proof of what the bytes mean, and the
-- column allow-list and the floats-only writer are what actually carry that.
--
-- embedding_model is stored WITH the vector because a faceprint computed by one model
-- is meaningless to another. Without it a model upgrade silently compares incomparable
-- numbers and matches the wrong member of a household.
--
-- Its character set is an allow-list -- letters, digits, dot, underscore, hyphen --
-- and that is the clause that finishes the no-paths promise.
--
-- The byte-length clause in front of it is what makes that allow-list mean anything,
-- and it is not decoration. SQLite's length(), trim() and GLOB are NUL-terminated on
-- TEXT: measured here, 'arc' + a NUL + '/Users/secret/kid-face.jpg' + 5000 more
-- characters is reported by length() as 3, passes BETWEEN 1 AND 128, and passes the
-- GLOB as containing no forbidden character -- and the whole 5030-character value,
-- path intact, was stored and read back. One byte stepped over all three clauses.
-- length(CAST(x AS BLOB)) counts real bytes, so requiring it to equal length(x) says
-- positively what is permitted: exactly this many single-byte ASCII characters, with
-- nothing hiding behind a terminator. This is the D19 shape -- a guard that did not
-- mean the same thing as the rule it restated -- and the Python copy of the rule had
-- been refusing the same value all along, which is what made it invisible. It is the only
-- caller-supplied TEXT in this table, and 128 characters is ample room for
-- "/Users/someone/child.jpg", so without this the promise rested on nobody choosing to
-- write one. Naming the permitted characters refuses every spelling of a path at once,
-- where a rule listing "/" and "~" and ".." would be as complete as the last person to
-- think about it.
--
-- The cost, stated rather than discovered later: a HuggingFace-style id with a slash
-- ("deepinsight/arcface") is refused too. That is the intended reading -- an identifier
-- here is a NAME, not a location, and the slash is exactly what makes it a location.
--
-- Each IS NOT NULL beside a trim() is load-bearing, for the reason lesson_sources gives
-- above and measured again here: NULL satisfies a bare CHECK. NOT NULL is declared as
-- well and fires first, so these clauses are the belt to that brace -- and a test that
-- means to exercise the CHECK has to strip the NOT NULL, or it names the wrong
-- constraint and passes for the wrong reason.
CREATE TABLE IF NOT EXISTS faceprints (
  learner_id      TEXT    PRIMARY KEY REFERENCES learners(id) ON DELETE CASCADE,
  embedding_model TEXT    NOT NULL CHECK (embedding_model IS NOT NULL
                                          AND length(CAST(embedding_model AS BLOB)) = length(embedding_model)
                                          AND length(trim(embedding_model)) BETWEEN 1 AND 128
                                          AND embedding_model NOT GLOB '*[^A-Za-z0-9._-]*'),
  dimension       INTEGER NOT NULL CHECK (dimension IS NOT NULL AND dimension BETWEEN 1 AND 1024),
  vector          BLOB    NOT NULL CHECK (vector IS NOT NULL AND length(vector) = dimension * 4),
  created_at      INTEGER NOT NULL
) STRICT;


-- One row per act of permission, and the table enrolment writes BEFORE it computes a
-- faceprint. The ordering is not a convention here: the faceprints INSERT below draws
-- its rows FROM this table, so a faceprint for a learner with no consent row inserts
-- nothing at all. That is what makes an interrupted enrolment safe -- the state it can
-- leave behind is "a learner who agreed and has no faceprint", never "a faceprint
-- nobody agreed to".
--
-- Append-only. A row records that somebody said yes, at a moment, to a specific
-- wording, and it is never edited to say something else. There is deliberately no row
-- shape for "no": a refusal writes nothing, which is why declining at any step leaves
-- the database byte-identical.
--
-- statement_id AND statement_text, both, deliberately. An id alone would let a later
-- edit to the constant in faces/enrollment.py silently rewrite what a household was
-- told -- the record would then answer a question about today rather than about the
-- day they agreed. Storing the words is what makes the record readable back.
--
-- granted_by is a ROLE and never a second person's name. Recording which adult
-- consented for a child would add personal data about somebody who is not a learner
-- here and was never asked; the household gets an answer to "who agreed" that is
-- honest about what this prototype actually knows.
--
-- ON DELETE CASCADE costs the audit trail, and that is the intended trade rather than
-- an oversight: docs/learner-database.md promises erasure, and keeping evidence about
-- a person after they have asked to be forgotten is the wrong side of it.
CREATE TABLE IF NOT EXISTS consents (
  id             INTEGER PRIMARY KEY,
  learner_id     TEXT    NOT NULL REFERENCES learners(id) ON DELETE CASCADE,
  -- WHAT was agreed to, as a machine code. An allow-list of one today; widening it is
  -- a decision somebody can be asked about rather than housekeeping.
  scope          TEXT    NOT NULL CHECK (scope IN ('face_recognition')),
  statement_id   TEXT    NOT NULL CHECK (statement_id IS NOT NULL
                                         AND length(trim(statement_id)) BETWEEN 1 AND 64
                                         AND statement_id NOT GLOB '*[^A-Za-z0-9._-]*'),
  -- instr() rather than length(), for the reason the learners comment above measures:
  -- length() stops at the first NUL and would not see a hidden tail here either.
  statement_text TEXT    NOT NULL CHECK (statement_text IS NOT NULL
                                         AND length(trim(statement_text)) BETWEEN 1 AND 4000
                                         AND instr(statement_text, char(0)) = 0),
  granted_by     TEXT    NOT NULL CHECK (granted_by IN ('the_person_themselves',
                                                        'an_adult_of_the_household')),
  -- HOW it was obtained, and this column is the security claim: an operator, in
  -- person, at this robot. There is no conversational path and no network one.
  granted_via    TEXT    NOT NULL CHECK (granted_via IN ('operator_at_the_robot')),
  -- Epoch milliseconds UTC, stamped by the store. Never caller-supplied, for the
  -- reason save_faceprint gives: a clock a caller chooses is a field a caller can get
  -- wrong on a record about biometric data.
  granted_at     INTEGER NOT NULL,
  -- Present in the FIRST version of this table on purpose. A column added later never
  -- reaches a robot that already has a database, so a withdrawal column introduced
  -- when it is finally needed would reach only fresh installs. The faceprint gate
  -- below already honours it; the writer that sets it is a later task.
  withdrawn_at   INTEGER CHECK (withdrawn_at IS NULL OR withdrawn_at >= granted_at)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_consents_learner_scope ON consents (learner_id, scope);
