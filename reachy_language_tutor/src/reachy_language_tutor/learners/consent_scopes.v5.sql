-- Rebuild `consents` so its scope CHECK admits 'local_profile'.
--
-- WHY A FILE AND NOT A STRING IN store.py. Every statement touching personal data
-- lives outside the module's inline surface, so the module's own guard can prove
-- what it reads; DDL inlined in Python is exactly what that guard refuses, and it
-- refused this. schema.sql sets the precedent and this follows it. It ships for the
-- same reason schema.sql does: pyproject's package-data lists learners/*.sql.
--
-- WHY A REBUILD. Re-running schema.sql cannot change a CHECK on a table that already
-- exists -- every statement there is IF NOT EXISTS -- so without this a household
-- that upgraded would have their consent rejected by the database with no way to
-- tell why, while a fresh install worked. SQLite's own documented procedure, in its
-- order: create the replacement, copy, drop, rename, recreate the index.
--
-- The rows are consent records about people. They are copied column for column,
-- never regenerated, and their ids come with them, so what somebody agreed to and
-- when survives the rebuild unchanged.
--
-- Foreign keys must be OFF around this, and the caller does that OUTSIDE any
-- transaction, because the pragma is silently a no-op inside one.
--
-- ONE TRANSACTION, AND THE FIRST VERSION HAD NONE. A review reproduced what that
-- cost: a crash between the DROP and the RENAME left the rows in an orphaned
-- consents_migrated, schema.sql then recreated `consents` EMPTY under IF NOT EXISTS,
-- and the database came up ready=True stamped at version 5 -- every consent record
-- destroyed, with the faceprints left intact, so the device would hold face data and
-- no record of what anybody agreed to. A crash after the CREATE instead wedged every
-- later startup on "table consents_migrated already exists", permanently.
--
-- SQLite makes DDL transactional, so BEGIN/COMMIT here is what makes the rebuild
-- all-or-nothing: an interrupted migration rolls back to the v4 table with every row
-- still in it, and the next startup simply tries again. executescript() COMMITs any
-- pending transaction before it runs, so this BEGIN is the outermost one.
--
-- The DROP IF EXISTS recovers a database already wedged by an interrupted run of the
-- version of this file that had no transaction.
--
-- IMMEDIATE, not a plain BEGIN, because two processes can run this at once: the app
-- starting and the enrol command both call ensure_learner_database, and the lock
-- store.py holds around it serialises threads, not processes. A plain BEGIN is
-- deferred -- the DROP IF EXISTS below takes a read snapshot, and when the CREATE then
-- needs to write after another process has committed, SQLite answers "database is
-- locked" at once rather than waiting out the busy timeout, because waiting cannot
-- make a stale snapshot current. Measured: six concurrent upgrades of a version-4
-- database, twelve times over, failed 5 of 72 with a plain BEGIN and 0 of 72 with this.
-- The failure rolled back cleanly, so nothing was lost -- but the start reported the
-- store unusable, and taking the write lock first is what the rebuild needed anyway.
BEGIN IMMEDIATE;

DROP TABLE IF EXISTS consents_migrated;

CREATE TABLE consents_migrated (
  id             INTEGER PRIMARY KEY,
  learner_id     TEXT    NOT NULL REFERENCES learners(id) ON DELETE CASCADE,
  scope          TEXT    NOT NULL CHECK (scope IN ('face_recognition', 'local_profile')),
  statement_id   TEXT    NOT NULL CHECK (statement_id IS NOT NULL
                                         AND length(trim(statement_id)) BETWEEN 1 AND 64
                                         AND statement_id NOT GLOB '*[^A-Za-z0-9._-]*'),
  statement_text TEXT    NOT NULL CHECK (statement_text IS NOT NULL
                                         AND length(trim(statement_text)) BETWEEN 1 AND 4000
                                         AND instr(statement_text, char(0)) = 0),
  granted_by     TEXT    NOT NULL CHECK (granted_by IN ('the_person_themselves',
                                                        'an_adult_of_the_household')),
  granted_via    TEXT    NOT NULL CHECK (granted_via IN ('operator_at_the_robot')),
  granted_at     INTEGER NOT NULL,
  withdrawn_at   INTEGER CHECK (withdrawn_at IS NULL OR withdrawn_at >= granted_at)
) STRICT;

INSERT INTO consents_migrated
  (id, learner_id, scope, statement_id, statement_text, granted_by, granted_via,
   granted_at, withdrawn_at)
SELECT id, learner_id, scope, statement_id, statement_text, granted_by, granted_via,
       granted_at, withdrawn_at
FROM consents;

DROP TABLE consents;
ALTER TABLE consents_migrated RENAME TO consents;
CREATE INDEX IF NOT EXISTS idx_consents_learner_scope ON consents (learner_id, scope);

COMMIT;
