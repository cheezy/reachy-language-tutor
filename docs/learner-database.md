# The learner database

Where learner profiles, the lesson catalog, and practice results live. This is the
reference for what the database holds and why; the schema itself is
`reachy_language_tutor/src/reachy_language_tutor/learners/schema.sql` and the code that
applies it is `store.py` beside it.

**The database is the source of truth for progress.** The language model teaches and
chats; it never decides what counts as completed. Anything the tutor says about what a
learner has finished comes from here.

## What it deliberately does not store

No faceprints, no locale, no audio, no transcripts — and no email address. `docs/plan.md`
says "Only names, emails, and learning progress leave the home", with faceprints staying
on the device; this schema deliberately goes further and stores no email at all, because
nothing on the robot needs one. Account-level identity is a hosted-backend concern for a
later milestone. What is left is a display name, a lesson catalog, and results.

Deleting a learner is a single statement, because everything else cascades from it:

```sql
DELETE FROM learners WHERE id = ?;
```

## Where the file lives

```
<instance_path>/learners.v1.sqlite3
```

`instance_path` is the per-installation directory the Reachy Mini SDK hands the app —
the same place `memory.py` keeps its JSON. When it is absent (standalone development
runs), the path falls back to `$XDG_DATA_HOME/reachy_language_tutor/` or
`~/.local/share/reachy_language_tutor/`.

The filename is versioned, following `memory.v1.json`. A future v2 that cannot be
migrated becomes a new file rather than a corrupted old one.

The database runs in WAL mode, so two sidecar files appear beside it —
`learners.v1.sqlite3-wal` and `learners.v1.sqlite3-shm`. They contain the
same personal data. All three are gitignored at both the repository root and the app
level; **never commit any of them.**

## Tables

### `learners` — one row per person who practises on this robot

| Column | Type | Null? | Meaning |
|---|---|---|---|
| `id` | TEXT | no | Primary key. A stable string assigned by the application, not a database counter — face recognition will supply this later, and the app sets it, never the conversation. |
| `display_name` | TEXT | no | What Reachy calls this person out loud. Must not be blank or whitespace. |
| `created_at` | INTEGER | no | When the profile was created, epoch milliseconds. |

### `languages` — what the tutor can teach

| Column | Type | Null? | Meaning |
|---|---|---|---|
| `code` | TEXT | no | Primary key. Lowercase short code, 2–8 characters (`es`, `fr`). |
| `name` | TEXT | no | The language's name in English, shown and spoken. Unique. |

### `lessons` — the ordered catalog

| Column | Type | Null? | Meaning |
|---|---|---|---|
| `id` | TEXT | no | Primary key. A readable slug such as `es-03-numbers`, stable across re-seeding. |
| `language_code` | TEXT | no | Which language this lesson belongs to. Cascades on delete. |
| `position` | INTEGER | no | Order within the language, starting at 1. **Unique per language** — see below. |
| `title` | TEXT | no | Short name for the lesson. |
| `objective` | TEXT | no | What the learner should be able to do afterwards, in plain language. |

### `lesson_results` — an append-only log of attempts

One row per attempt, **not** one row per learner-and-lesson. Retries and partial
attempts stay visible, and recording a result is a plain insert with no update logic.

| Column | Type | Null? | Meaning |
|---|---|---|---|
| `id` | INTEGER | no | Primary key, assigned by SQLite. |
| `learner_id` | TEXT | no | Who practised. Cascades on delete. |
| `lesson_id` | TEXT | no | Which lesson. Cascades on delete. |
| `outcome` | TEXT | no | One of `completed`, `partial`, `skipped`. Constrained, not free text. |
| `score` | INTEGER | **yes** | 0–100 when the tutor scored the attempt; null when it did not. |
| `recorded_at` | INTEGER | no | When the attempt ended, epoch milliseconds. |

### `schema_meta` — bookkeeping

A key/value table holding two rows:

| Key | Meaning |
|---|---|
| `seed_version` | Which version of the seed data this database has applied |
| `seeded_learner_ids` | JSON array of the sample learner ids this database has ever seeded |

The schema version itself is not here — it lives in SQLite's own `PRAGMA user_version`,
which is readable on a brand-new file before any table exists, so there is no
chicken-and-egg at first start.

`seeded_learner_ids` deliberately outlives the rows it names: it is what makes a
learner's deletion permanent, so it must still say "we seeded this person" after that
person's row is gone. See [Versioning and re-seeding](#versioning-and-re-seeding).

## How the tables relate

```
languages ──1:N──> lessons ──1:N──> lesson_results <──N:1── learners
   code              id                lesson_id            id
                     language_code     learner_id
```

Every foreign key is `ON DELETE CASCADE`. Deleting a learner removes their results;
retiring a language removes its lessons and their results.

> **Foreign keys only work because the code turns them on.** SQLite ignores foreign key
> constraints unless a connection issues `PRAGMA foreign_keys = ON`, and it is a silent
> no-op inside an open transaction. `store.connect()` sets it on every connection, and
> is the only way the database is opened. A test asserts enforcement is live, because a
> regression here would be invisible.

## How "next lesson" is decided

**The lowest-positioned lesson in that language with no `completed` result for that
learner.** A `partial` or `skipped` attempt does *not* advance them — they get the same
lesson again.

This is unambiguous rather than merely ordered because `lessons` carries
`UNIQUE (language_code, position)`: two lessons cannot tie for the same slot, so
"lowest position" always has exactly one answer. Never rely on insertion order or on
sorting by title.

The query lives in `store.NEXT_LESSON_SQL` as the single source of truth:

```sql
SELECT l.id, l.position, l.title, l.objective
FROM lessons AS l
WHERE l.language_code = ?
  AND NOT EXISTS (
        SELECT 1 FROM lesson_results AS r
        WHERE r.lesson_id = l.id AND r.learner_id = ? AND r.outcome = 'completed'
      )
ORDER BY l.position
LIMIT 1
```

It returns no row when the learner has finished the language, or when the language is
unknown.

## Seed data

Seeded so the app is demonstrable before any real learner exists.

### Languages

| Code | Name |
|---|---|
| `es` | Spanish |
| `fr` | French |

### Lessons (12 total)

| ID | Language | Position | Title | Objective |
|---|---|---|---|---|
| `es-01-greetings` | Spanish | 1 | Greetings and goodbyes | Greet someone, ask how they are, and say goodbye: hola, buenos días, ¿cómo estás?, adiós. |
| `es-02-introductions` | Spanish | 2 | Introducing yourself | Give your name and where you are from, and ask the same back: me llamo…, soy de…, ¿y tú? |
| `es-03-numbers` | Spanish | 3 | Numbers one to twenty | Count to twenty out loud and say your age and a phone number. |
| `es-04-ordering-food` | Spanish | 4 | Ordering food and drink | Order in a café and ask what something costs: quisiera…, ¿cuánto cuesta? |
| `es-05-directions` | Spanish | 5 | Asking for directions | Ask where a place is and follow a simple answer: ¿dónde está…?, a la derecha, a la izquierda. |
| `es-06-daily-routine` | Spanish | 6 | Talking about your day | Describe a typical day using present-tense verbs and times of day. |
| `fr-01-greetings` | French | 1 | Greetings and politeness | Greet someone and use bonjour, salut, s'il vous plaît, merci, au revoir. |
| `fr-02-introductions` | French | 2 | Introducing yourself | Give your name, age, and where you live: je m'appelle…, j'ai … ans, j'habite à… |
| `fr-03-numbers` | French | 3 | Numbers one to twenty | Count to twenty out loud and say a price and a time. |
| `fr-04-ordering-food` | French | 4 | At the café | Order a drink and a pastry, then ask for the bill: je voudrais…, l'addition, s'il vous plaît. |
| `fr-05-directions` | French | 5 | Getting around town | Ask the way to the station and understand tout droit, à gauche, à droite. |
| `fr-06-daily-routine` | French | 6 | Your daily routine | Describe your morning with reflexive verbs: je me lève, je me prépare. |

### Sample learner

| ID | Display name | Created (epoch ms) |
|---|---|---|
| `sample-learner` | Sample Learner | `1767225600000` |

A deliberately neutral placeholder rather than a plausible human name, so nobody
mistakes demo data for a real household member and a screenshot leaks nothing.

### Sample results

| Learner | Lesson | Outcome | Score | Recorded (epoch ms) |
|---|---|---|---|---|
| `sample-learner` | `es-01-greetings` | `completed` | 90 | `1767312000000` |
| `sample-learner` | `es-02-introductions` | `completed` | 75 | `1767398400000` |
| `sample-learner` | `es-03-numbers` | `partial` | 40 | `1767484800000` |

Timestamps are fixed constants, not "now", so a freshly seeded database does not look
like the sample learner practised at install time.

**What this seed produces:**

- **Spanish** → next lesson is `es-03-numbers`. The learner completed lessons 1 and 2,
  and attempted lesson 3 with a `partial` outcome that does **not** advance them. This
  is the case most likely to be implemented wrong, so it is seeded to make the bug
  obvious immediately.
- **French** → next lesson is `fr-01-greetings`. Nothing attempted, so the language
  starts from the beginning.

## Versioning and re-seeding

Two independent markers, both checked before any work:

| Marker | Stored in | Governs |
|---|---|---|
| Schema version | `PRAGMA user_version` | Whether the DDL needs applying |
| Seed version | `schema_meta.seed_version` | Whether seed data needs writing |
| Seeded learners | `schema_meta.seeded_learner_ids` | Which sample learners must never be seeded again |

Once both are current, startup costs a connection open, a few pragmas, one integer
header read, and one indexed single-row lookup — no DDL parsing and no writes. That
matters: the deployment target is the Wireless model, whose onboard computer is weak.

Re-seeding is safe because the two kinds of row are treated differently:

- **App-owned reference data** (`languages`, `lessons`) is *converged* on a version
  bump, so a corrected lesson title reaches installations that already seeded.
- **Learner-owned rows** (`learners`, `lesson_results`) are never overwritten. A
  household may have renamed the sample learner or practised against it.

**A deleted learner is never resurrected**, including across seed-version bumps. Two
mechanisms are needed for that, and the second is the subtle one:

- Seeding is gated on the version marker rather than on whether a table looks empty, so
  the ordinary restart path never re-inserts anything.
- When the seed *does* re-run, `ON CONFLICT(id) DO NOTHING` is not enough — it does not
  fire for a row that was deleted, so it would happily re-insert the person. The ids of
  sample learners ever seeded are therefore recorded in `schema_meta.seeded_learner_ids`,
  which outlives the row itself, and a learner named there is never seeded again.

That is a privacy requirement, not an optimisation: a household that deletes someone must
not get them back when a catalog update ships.

**How that record is stored, and why it matters.** `seeded_learner_ids` is a **JSON
array**, written on every seed run rather than only when a learner row was actually
inserted. Both details exist because the failure mode here is silent:

- An earlier version comma-joined the ids. An id containing a comma would split into
  fragments matching no real learner, so the guard would quietly stop recognising that
  person and seed them again. A JSON array cannot fragment.
- Writing only when a row was inserted left the record absent on any database that
  seeded under a version which skipped it — re-enabling resurrection exactly once.

The reader still accepts the old comma-joined form, so upgrading an existing
installation keeps its record. A value that is neither shape is treated as empty **and
logged as a warning**, because empty is the permissive direction: it allows seeding to
happen again, which is precisely what this record exists to stop.

To ship a catalog change: edit the `SEED_*` constants in `store.py` and bump
`SEED_VERSION`. To change the schema: edit `schema.sql`, bump `SCHEMA_VERSION`, and add
a migration branch for the older version.

## When things go wrong

A missing directory is created. A corrupt or unreadable database is **logged and
reported, never deleted** — it may hold real progress that a human can still recover.
`ensure_learner_database()` returns `ready=False` with the error rather than raising,
so the app starts and can say learner data is unavailable instead of crashing.
