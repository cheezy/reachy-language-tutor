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

Three markers, and they are not read at the same time. The first two are gates,
checked before any work on every call. The third is read only once a gate has already
opened — inside the re-seed itself — so it costs nothing on the ordinary path.

| Marker | Stored in | Governs | Read |
|---|---|---|---|
| Schema version | `PRAGMA user_version` | Whether the DDL needs applying | Every call |
| Seed version | `schema_meta.seed_version` | Whether seed data needs writing | Every call |
| Seeded learners | `schema_meta.seeded_learner_ids` | Which sample learners must never be seeded again | Only when re-seeding |

Once the two gates are current, startup costs a connection open, a few pragmas, one
integer header read, and one indexed single-row lookup — no DDL parsing and no writes.
That matters: the deployment target is the Wireless model, whose onboard computer is
weak. It is also why the third marker is read where it is: moving it onto the fast path
would add a lookup to every start for a value only the re-seed uses.

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

A single id that happens to be valid JSON on its own is read two different ways, and
the asymmetry is deliberate. A quoted string (`"alice"`) is read from the PARSED value,
because keeping the quotes would yield an id matching no learner. A number (`1e5`,
`1.50`) is read from the RAW TEXT, because the parsed value is a lossy rendering of it:
`str(json.loads("1e5"))` is `"100000.0"`, a different id, so the stored one would be
dropped and that learner seeded again. A stored boolean is neither, and reaches the
warning — which it did not before, because Python's `bool` subclasses `int`.

**Comma-splitting applies only to the non-JSON branch.** Inside JSON a comma in a
string is data, not a separator, so neither the array nor the quoted-string form is
split: `["smith, john"]` and `"smith, john"` both read back as the one id. The quoted
form used to split, which meant the same id survived one spelling and fragmented in
the other. One consequence of excluding `bool`: a legacy bare id spelled exactly
`true`, `false` or `null` parses as a JSON boolean or null, reaches neither scalar arm,
and is refused with a warning rather than recovered. Ids here are slugs, and a bare
boolean in this record is far likelier to be corruption than a name.

**A refusal is not reversible.** Every degrading branch warns and returns what it could
read, and the seed pass then rewrites this record from exactly that set, in the same
transaction — so the unreadable text is erased before anyone reads the warning. There is
no later opportunity to repair a corrupt record by hand. That is the real cost of the
permissive direction here, and it applies to every branch that degrades, not only to the
boolean one.

To ship a catalog change: edit the `SEED_*` constants in `store.py` and bump
`SEED_VERSION`. To change the schema: edit `schema.sql`, bump `SCHEMA_VERSION`, and add
a migration branch for the older version.

## When things go wrong

A missing directory is created. A corrupt or unreadable database is **logged and
reported, never deleted** — it may hold real progress that a human can still recover.
`ensure_learner_database()` returns `ready=False` with the error rather than raising,
so the app starts and can say learner data is unavailable instead of crashing.

## The query interface

Everything above describes the data. This is how the application reaches it.

**Import from the package, never from the storage module:**

```python
from reachy_language_tutor.learners import (
    get_profile,
    get_practised_languages,
    get_progress,
    record_result,
    store_is_available,
)
```

What that package exports is the whole vocabulary a caller needs. It deliberately does
not export the connection helper, the bootstrap function, or the seed constants — those
are SQLite's business, and a hosted backend would have no equivalent.

| Function | Returns | Meaning of the empty answer |
|---|---|---|
| `get_profile(learner_id, *, instance_path=None)` | `LearnerProfile \| None` | `None` = no such learner, **or** the store is unreadable, **or** the learner id was refused |
| `get_practised_languages(learner_id, *, instance_path=None)` | `tuple[PractisedLanguage, ...]` | `()` = nothing practised, **or** every attempt was `skipped`, **or** the store is unreadable, **or** the learner id was refused |
| `get_progress(learner_id, language_code, *, instance_path=None)` | `LanguageProgress \| None` | `None` = that language is not taught, **or** the store is unreadable, **or** either argument was refused |
| `record_result(learner_id, lesson_id, outcome, *, score=None, recorded_at=None, instance_path=None)` | `RecordResultOutcome` | never raises; see the reason codes below |
| `store_is_available(instance_path=None)` | `bool` | `False` = the store could not be read, **or** `instance_path` itself was refused. It binds no caller value into SQL, so it is **not** a test of whether a *learner id or language code* was refused |

Those "or"s are why the next section exists: only the log tells them apart — and not
even the log separates a learner who has practised nothing from one who has only ever
declined. Declining is not practice, so a language they have only skipped is absent from
`get_practised_languages`, silently and by design, which is what lets the tutor offer it
as new.

On every function that takes a learner, `learner_id` comes first and `instance_path` is
keyword-only and last, so it can never be passed by accident into the language slot.
`store_is_available` is the exception and takes no learner at all: its `instance_path`
is its only parameter and may be passed positionally.

### Absence is not the same as breakage

`None` carries three meanings, not two: the thing genuinely does not exist, the store
could not be read, or the reader refused the argument shape it was given. A caller that
treats `None` as "does not exist" will tell someone "I don't teach German" when the
database is broken, or when the tool layer sent a language code the reader refused — a
confident falsehood either way.

**`store_is_available(instance_path)` is deliberately NOT the test for this.** It binds
no caller value, so it answers `True` when the argument was the problem, and a caller
reading that `True` as confirmation gets exactly the falsehood above. Use it for what it
is: a probe of whether the store itself can be read.

The rule that separates the three is **silence**. A genuine absence logs nothing; every
other empty answer — `None`, or `()` from `get_practised_languages` — logs a warning
first.

The prefixes are per reader, not global. A refused argument is named the same way
everywhere: `Could not read a learner id`, and from `get_progress` also `Could not read
a language code`. A failed lookup is named by the reader it failed in — `Could not read
a learner profile`, `Could not read a learner's practised languages`, or `Could not read
learner progress` — so a caller watching only the last of those misses breakage in the
other two. `record_result` needs none of this: it already reports `storage_unavailable`
explicitly.

### The three empty answers, which are not the same thing

This distinction is the reason `get_progress` returns an optional value rather than an
always-populated one:

| Situation | Result | What the tutor should say |
|---|---|---|
| Language taught, never practised | populated; `completed` empty, `next_lesson` is lesson 1 | "You haven't started French — shall we?" |
| Language not taught here | `None`, **and nothing logged** | "I don't teach German yet." |
| Language taught, no lessons written | populated; `remaining` empty, `next_lesson` is `None` | "I teach it, but I have nothing prepared." |
| Every lesson completed | `remaining` empty, `completed` full | "You've finished Spanish." |

The "nothing logged" in the second row is load-bearing, not decoration: `None` also
comes back when the store is unreadable or an argument was refused, and saying "I don't
teach German yet" on either of those is the confident falsehood the section above is
about. Only silence distinguishes them.

An **unknown learner** gets a populated fresh start rather than an error — the lesson
catalog is not personal data, so there is nothing to withhold. Recording a result is
where an unknown learner is caught and reported, because that is the operation that
must not silently appear to succeed.

### Recording a result

`record_result` never raises. A wrong word from the conversation must not end the turn,
so every failure comes back as a code:

| `reason` | Cause |
|---|---|
| `invalid_outcome` | Not one of `completed`, `partial`, `skipped`. Case-sensitive — silently lowercasing a guess would record something the model did not mean. |
| `invalid_score` | A score outside 0–100, or one that is not a whole number. Checked by type before it is compared, so a value of any shape lands here rather than raising. A `bool` lands here too, deliberately: Python's `bool` is a subclass of `int`, so without an explicit exclusion `True` would be accepted as a score of 1. |
| `invalid_recorded_at` | An explicit timestamp that is not a whole number of milliseconds, or one outside the 64-bit range a SQLite `INTEGER` can hold. No judgement is made about the date itself: `0`, a negative value and a far-future value are all accepted, because the schema puts no range on this column. |
| `unknown_learner` | No such learner. Nothing is written. |
| `unknown_lesson` | No such lesson. Nothing is written. |
| `rejected_by_database` | A constraint refused the row — a backstop behind the checks above. |
| `storage_unavailable` | The database could not be opened or written. |

Outcomes are validated in Python **and** constrained in the schema. That is defence in
depth, not duplication: the constraint protects against any future writer, while the
Python check turns a hallucinated outcome into a reason code instead of an exception
travelling up through the conversation loop.

`recorded_at` is checked in Python for a different reason, worth stating because the
obvious assumption is wrong: **a `STRICT` column is not a type gate.** SQLite accepts
any `TEXT` or `REAL` that converts losslessly, so before this check `"1700000000000"`,
`"00042"`, `" 42"`, `"1e3"`, `3.0` and `True` were all silently coerced to integers and
written — rows that look legitimate for ever. What it does refuse is anything that will
not convert: a lossy float (`3.5`), and text that is not a number at all (`"yesterday"`,
`"42abc"`, `""`). That is the trap — the refusals are the cases you would think to test,
and only an unbindable type (a list, a dict) reached `storage_unavailable`. The caller
is an LLM tool layer, where a JSON number often arrives as a float and a timestamp
often arrives as a string, so these are the realistic inputs rather than the exotic
ones.

The 64-bit bound is a separate matter and is not a style choice. An `int` outside it
satisfies `isinstance` and then raises `OverflowError` as `sqlite3` *binds* it, before
the database sees the statement — and `OverflowError` is neither `sqlite3.Error` nor
`ValueError`, so nothing caught it and it travelled up through the conversation loop.
`record_result` promises never to raise; that promise needs this bound to be true.

Attempts are append-only. Recording the same lesson twice leaves two rows.

### Learner scoping

Every query that touches personal data filters by learner id, and every write names it
as its first column. Two different things enforce that, and they cover different
statements — which matters, because only one of them is an import-time guarantee.

Every module-level statement that touches personal data is passed through
`_learner_scoped`, which refuses one it cannot prove reaches a single learner. Those
really do fail as the module loads, so the whole suite fails at once instead of one
household member's data quietly reaching another. The catalog statements are deliberately not wrapped —
see the paragraph below — so "every module-level statement" would be the wrong reading.

A statement written inline inside a function body can still be wrapped — `_learner_scoped`
refuses it just the same, at call time. What inline loses is the *timing*: nothing makes
the wrapping happen, so an author who simply does not call it gets no refusal at all.
Those statements are covered by a test that reads this module's own source and reports
any `execute()` argument it cannot prove scoped — a test rather than an import-time
guarantee, so it catches them when the suite runs rather than when the module loads.

**What both halves catch** is a statement whose personal relations are not all
constrained to one learner. That covers the plain omission — `SELECT outcome FROM
lesson_results` is refused at import and reported by the test — and it covers the
widenings that defeated the older substring rule: a self-join on a personal table, an
`OR`-widened predicate, an unfiltered `UNION` leg, a `DELETE` whose only filter sits in
a subquery, a filter inside a `NOT EXISTS` or `IN (...)`, a filter written only in a
join's `ON`, `WHERE NOT learner_id = ?`, and a filter wrapped in `CASE`, `IIF` or any
other call. Each of those names a learner filter somewhere in its text while reading
everyone, and each is refused. So is an `UPDATE ... SET outcome = (SELECT 'x' WHERE
1 = 1), learner_id = ?` — a statement with no `WHERE` clause at all that reassigns every
row in `lesson_results` to one learner.

Neither half is a proof, though, and the holes are worth knowing by name. The first one
is the one this document is read for.

**Both halves read `store.py` and nothing else.** `_learner_scoped` is only ever applied
to this module's own statements, and the test points its scan at `store.__file__`. So a query
touching personal data written in a tool module, a route, or anywhere else is caught by
review and by nothing mechanical — the opening sentence of this section is a statement
about the queries in this module, not a property the application enforces wherever you
put one. Put the query here, or accept that nothing will stop you.

Both consult the same rule, so it is one rule read at two times rather than two
checks. It reads which relations a statement names and demands a learner filter
that actually constrains each of them: a `WHERE` conjunct, written in that relation's
own subquery, that **is** `alias.column = ?` rather than merely containing it — and it
refuses a shape it cannot read rather than passing it over.

Requiring the whole conjunct is an allow-list, and that is the point. The version of this
rule that instead listed the ways a filter can be present without constraining anything
was defeated by `IIF(learner_id = ?, 1, 1)`, by `max(learner_id = ?, 1)`, by
`(learner_id = ?) = 0` and by `learner_id = ? = 0` — each of which returns somebody
else's rows, and the first of which is SQLite's own documented equivalent of the `CASE`
form that list had already been taught. A list of what is forbidden can always be one
entry short; a list of what is permitted cannot. Two limits follow from that, and they pull in opposite directions.

It proves the **shape** of a filter and never its value. `r.learner_id = ?` says a
parameter constrains that relation; nothing here says the application binds the learner
standing in front of the robot. That is the app's job, and this rule does not check it.

A sharper form of the same gap, worth naming because it is not obvious — and **kept
deliberately**, under D16, rather than left unnoticed: a statement may carry **more than
one** learner parameter, and the rule does not require them to name the same learner. `SELECT l.id, r.learner_id FROM learners AS l JOIN lesson_results AS r ON 1
WHERE l.id = ? AND r.learner_id = ?` is accepted, and binding two different ids returns
two different people. Every relation is constrained — the rule's contract is kept — but
"constrained to one learner each" is weaker than "constrained to the same learner". No
statement here has two learner parameters and the store's functions take a single
`learner_id`, so nothing reaches this today; a "compare with a household member" or
"merge two profiles" feature is the shape that would, and it should be read as writing a
cross-learner statement deliberately rather than as passing this guard.

**Why it is kept rather than closed.** Refusing every statement with two learner filters
would also reject the legitimate two-table read this rule is documented as accepting —
`... JOIN learners AS b ON b.id = a.learner_id WHERE a.learner_id = ? AND b.id = ?` — and
that refusal only buys something once a cross-learner feature exists to guard against.
Whether such a feature is wanted is a product question, not a rule question, so the rule
was left alone and the limit written down.
`test_the_rule_constrains_each_relation_and_not_all_to_one_learner` pins three shapes that
reach this limit by different routes: the join above, a scalar subquery filtered on one
learner beside an outer query filtered on another, and an `UPDATE ... FROM` whose target
and source rows are attributed to different people. So this text cannot drift away from the
code the way the scoping section did before D11. If that test starts failing, someone has
strengthened the rule and this paragraph is what needs updating.

**What the rule accepts as a filter** is narrow, and worth stating before the refusals,
because most of them follow from it: a `WHERE` conjunct that **is** `alias.column = ?`,
written in that relation's own subquery — or bare `column = ?` when exactly one relation
is visible from where it is written. A subquery is identified, not counted by depth, so a
filter in one subquery cannot scope a relation read in its sibling. Brackets
enclosing the whole conjunct are stripped first, so `WHERE (r.learner_id = ?)` is the
same statement as `WHERE r.learner_id = ?` and is accepted.

It therefore refuses plenty of statements that are perfectly well scoped. Known refusals,
as of this change — read this as what has been found, not as a closed list, because the
rule refuses by default and nobody has enumerated every shape it cannot read:

- an unqualified `learner_id = ?` where more than one relation is in scope. **This is the
  one a developer hits first**, and the refusal says to write `r.learner_id = ?`, because
  telling someone nothing constrains a statement they did filter sends them looking for
  the wrong thing.
- a filter spelled any other way: `? = learner_id`, `learner_id IS ?`, or a placeholder
  written `?1` or `:learner_id`.
- a filter in an `ON` clause rather than a `WHERE`, and a join scoped transitively through
  `ON b.id = a.learner_id`.
- a CTE; a compound query (`UNION`, `EXCEPT`, `INTERSECT`); a `FROM a, b` comma join.
- a quoted identifier in any of SQLite's three spellings — `"x"`, `` `x` ``, `[x]` — and a
  schema-qualified name like `main.lesson_results`.
- an `OR` at a `WHERE` clause's own level, even one that never touches the learner filter,
  and a `BETWEEN` at that level for the same reason — `BETWEEN x AND y` spells its own
  `AND`, which is not a conjunction. Bracketing fixes both: `WHERE learner_id = ? AND
  (score BETWEEN 0 AND 100)` is accepted.
- a `WHERE` whose whole conjunction is bracketed — `WHERE (learner_id = ? AND outcome =
  'completed')` — because the filter is then not a conjunct of its own.
- a bare `learner_id = ?` in a query that also has a derived table (`FROM (SELECT ...)`),
  because a derived table counts as a relation in scope and the filter then names none of
  them. Qualify it.
- a `WHERE` written straight after an opening bracket, which in SQLite means an
  aggregate's `FILTER (WHERE ...)`. That predicate chooses what the aggregate
  accumulates, not which rows the statement reads, so counting it would credit a
  constraint the database never applies. Refused by shape rather than by name, so any
  future clause spelled the same way is refused too.
- every write that is not a plain `INSERT INTO <table> (learner_id, ...)`: `REPLACE INTO`,
  `INSERT OR REPLACE`, `INSERT OR IGNORE`, and any `ON CONFLICT` clause.
- an `UPDATE` whose `SET` writes the column that says which learner a row belongs to —
  `SET learner_id = ...` on `lesson_results`, `SET id = ...` on `learners`. Proving which
  rows a write touches says nothing about who it hands them to, and
  `UPDATE lesson_results SET learner_id = 'bob' WHERE learner_id = ?` has a filter that
  is entirely real. A merge-profiles feature that genuinely needs this should have to
  declare itself rather than inherit the read rule's silence.

An `OR` inside brackets is fine: `WHERE learner_id = ? AND (outcome = 'completed' OR
outcome = 'partial')` is accepted. Only a top-level `OR` is refused, because `AND` binds
tighter — `WHERE learner_id = ? AND outcome = 'a' OR 1 = 1` reads as
`(learner_id = ? AND outcome = 'a') OR 1 = 1` and returns every row.

Both insert forms are read, not just `INSERT ... SELECT`. A `VALUES` list reads too the
moment it holds a scalar subquery, and writing `learner_id` first says nothing about the
rows that subquery reaches: `VALUES (?, (SELECT lesson_id FROM lesson_results WHERE
learner_id <> ? LIMIT 1), ...)` copies another household member's history into this
learner's. That cost falls on legitimate work, and it is the direction chosen on
purpose — a refusal stops the module importing and gets fixed the same hour, while an
acceptance reads another household member's history and nobody finds out.

A runtime value is refused when the guard cannot reconstruct the statement's text from
the source — a bare name, a call and a concatenation with a name are all reported, and
so is an interpolated table name. What it CAN reconstruct it hands to the rule above,
which now reads the reconstructed shape: `f"... WHERE learner_id = ? OR {extra}"` is
refused, because the `OR` is in the literal text even though what it widens the
predicate with is not.

The hole that remains is the **value** hole, and it is a real one:
`f"... WHERE learner_id = ? AND id = {lesson}"` is accepted. The statement is genuinely
scoped to one learner, so the rule is not wrong about scoping — but the hole is an
injection point, and nothing here refuses it. Refusing every interpolated statement
would also refuse the scoped spellings the guard is required to keep accepting, so the
limit is stated rather than closed.

They still disagree in one direction: the test exempts a plain learner-creating
`INSERT INTO learners (id, ...)` before consulting the rule at all, on the grounds that
a statement writing one row under an id it supplies reaches nobody else's data, while
`_learner_scoped` refuses it — an insert is scoped there by writing `learner_id` first,
and this one writes `id`. So that statement is written inline rather than wrapped.

They no longer disagree about comments. The rule drops closed comments and hides string
literals before reading a statement, so `-- learner_id = ?` in a trailing comment is
refused at import as well as flagged by the test; the two read the same statement the
database would run. An **unterminated** `/*` is a different matter and is refused
outright: SQLite ends one at the end of the input and discards everything after it, so
`WHERE lesson_id = ? /* AND learner_id = ?` reaches the database with no learner filter
while a reader that does not know this sees one — the rule seeing more than the database
does is exactly how a filter counts while constraining nothing. That asymmetry was tracked as **D12**, and closing it here is what made the
rule's reading of structure sound — a comment could otherwise forge the shape the rule
now depends on.

Read this section as "one rule, checked at two times, that catches an unconstrained
personal relation and refuses what it cannot read" — not as "this cannot happen", and
not as "this does not help". The value hole above is the one it does not reach.

The lesson catalog and the language list are deliberately **not** learner-scoped. They
are shared reference data, and treating them as personal would be a false positive.

### Connections

One connection per call, opened and closed inside that call, never stored, cached, or
carried across an `await`. Because nothing is shared, SQLite's same-thread check can
never fire, and a tool that later wants to move a call off the event loop can wrap it
without this module changing. WAL mode means readers never block the writer; two
writers serialise on the busy timeout rather than failing.

There is deliberately no connection pool and no cached handle: a pool is exactly the
thing that lets a connection escape into another thread.

### What milestone 5 changes

| Stays | Goes |
|---|---|
| The five exported functions' signatures | The SQLite bodies behind them |
| Every type in `models.py` | `connect`, `ensure_learner_database`, the seed constants |
| The reason-code vocabulary | The local database file |

A hosted backend implements the same five exported functions over HTTPS and returns the
same types built from JSON. `storage_unavailable` already exists for the failure mode the
network introduces, so callers written today need no change when a request times out.
Callers that import from the package rather than the storage module do not move at all.
