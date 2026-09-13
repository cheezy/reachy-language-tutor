# Reachy Language Tutor

A [Reachy Mini](https://www.pollen-robotics.com/reachy-mini/) app that teaches languages as a
spoken conversation. It loads a learner's profile, knows which languages they have worked on,
and runs their next lesson out loud — reacting with expressive movement as they go.

**Where it is right now:** the conversation, the lesson flow, the learner database and the
expressive reactions all work. Face recognition is *built but not yet wired in* — the
`faces/` package turns a frame into a faceprint and matches one against a household, and the
`faceprints` table stores them, but the current learner is still a constant in `main.py`.
Connecting the camera to it, and then to identity, is the milestone in progress.

The target deployment is unrelated households, each with their own robot, and the count is
deliberately left open — it could be a handful or a few thousand. The *shape* is what drives
almost every decision here, not the number: nobody administers these machines, so everything
runs on the robot today, a failure in somebody's living room has to degrade rather than break,
and face data stays on the device permanently. A later milestone moves *learning progress* to a hosted backend and routes LLM
calls through a proxy — names, emails and progress may leave the home; faceprints never do.

---

## How it works

```
      camera ┈┈► faces/ ┈┈► "who is this?" ┈┈┐         ( ┈┈► = built, not yet wired )
                                             ▼
                                         learners/ ──► "what have they done?"
                                             │
   microphone ──► conversation loop ◄─────────┘
                        │
                        ├──► tools/     the model asks the database questions
                        └──► moves.py   expressive reaction: nods, antennas, emotions
```

**The database is the source of truth for progress.** The language model teaches and chats;
it never decides what counts as completed. Anything the tutor says about what a learner has
finished comes from SQLite, through a small set of tools.

**The model is never told who it is talking to.** Identity is set by the app — from a
constant today, from recognition once that is wired — and the learner tools take no identity
argument from the conversation, so nobody can talk their way into another person's profile. `docs/identity-boundary-probe.md`
is the suite that attacks that boundary on every run.

## What is here today

| | |
|---|---|
| Languages | French, German, Italian, Portuguese, Spanish |
| Lessons | 36 seeded; 6 carry full converted content (dialogue, notes, drills) |
| Learner database | SQLite, schema version 3 — `learners`, `lesson_results`, `faceprints` and the lesson catalog |
| Tools the model can call | 19, covering profile, progress, lessons, movement and memory |
| Tests | 1486 passing, 30 skipped |

Lesson content is converted from public-domain Foreign Service Institute courses. The method
— including what gets excluded and why — is written down in `docs/converting-a-course.md`,
and the decisions made on the first course are in `docs/curation-log-italian-fast.md`.

## Privacy posture

This runs in homes, with children, and holds biometric data. The commitments are structural
rather than aspirational, and the places they are enforced are named:

- **A faceprint is numbers and nothing else.** No image, no crop, no thumbnail, and no path
  to one. The permitted columns are pinned as an allow-list by a test that reads the table's
  own schema, so a later column fails it whatever it is called.
- **No learner's name, id or transcript reaches a log.** Enforced in `learners/store.py`, in
  the `faces/` package by two structural tests, and checked one layer up after it was broken
  there once.
- **Erasure removes the bytes, not just the row — and says so only as far as it can.**
  `delete_faceprint` returns a count, never a name, and distinguishes "they had none"
  from "I could not tell". The database sets `PRAGMA secure_delete`, so a freed page is
  zeroed as it is written, and the delete is checkpointed before the call returns —
  otherwise it would sit in the write-ahead log while the main file still held the
  original page, which is measurably what happened before. Asserted against the file,
  not the table. The one thing a returned `1` does *not* claim is that the bytes have
  already gone: the checkpoint yields to readers instead of blocking an erase on
  somebody else's connection, so an open reader defers it to the next checkpoint — which
  the code reports rather than passing over in silence. `docs/learner-database.md` has
  the measurement. The file is owner-only (0600), companions included.
- **Face recognition is not a lock.** There is no liveness check: a photograph or a screen
  counts as the person if the detector accepts it. The matching threshold has not been
  measured against real faces, `faces.THRESHOLD_CALIBRATED` says so at runtime, and
  `scripts/calibrate_faceprints.py` measures it against your own photos outside the repo.

## Getting started

Full instructions, including the SDK/daemon version trap that will otherwise bite you, are in
**[docs/SETUP.md](docs/SETUP.md)**. The short version:

```bash
# Python 3.11+, and a virtualenv the Reachy Mini SDK is installed into
$HOME/dev/reachy/reachy_mini_env/bin/python -m pytest reachy_language_tutor -q
$HOME/dev/reachy/reachy_mini_env/bin/ruff check reachy_language_tutor/src
```

The app runs against the Reachy Mini Control desktop app in simulation, and on a real robot.
Secrets live in `.env` and are never committed; LLM keys are never shipped inside the app.

## Repository layout

```
reachy_language_tutor/src/reachy_language_tutor/
    learners/     the SQLite store, its schema, and the data contract
    faces/        embedding and matching -- no camera, no database, pure computation
    tools/        what the model may call, one file per tool
    audio/  moves.py  personality.py  ...   the conversation and expression layers
docs/             the reasoning behind the decisions, not just the decisions
scripts/          operator utilities that run outside the test suite
```

## Documentation

| Document | What it is for |
|---|---|
| [docs/plan.md](docs/plan.md) | The planning notes: the concept, the hardware, the costs, the risks |
| [docs/SETUP.md](docs/SETUP.md) | Getting a development environment that actually works |
| [docs/learner-database.md](docs/learner-database.md) | Every table, what it deliberately does not store, and the query interface |
| [docs/converting-a-course.md](docs/converting-a-course.md) | Turning a published course into lessons this app can teach |
| [docs/identity-boundary-probe.md](docs/identity-boundary-probe.md) | How the identity boundary is attacked on every run |
| [docs/rpc-control-surface.md](docs/rpc-control-surface.md) | What the robot exposes on the network, and what it refuses |

## Working on this

`CLAUDE.md` carries the engineering rules, and they are not general advice — each one names
defects in this repository that cost real rework. The ones that come up most:

- **Name what is PERMITTED, never what is forbidden.** Deny-lists have failed here repeatedly;
  inverting to an allow-list closed the whole family each time.
- **Fix the class, not the member you were shown.** The most common defect on this board is a
  fix applied to one member of a set while its siblings kept the bug.
- **A guard must mean the same thing as the code it protects.**
- **Execute it; do not reason about it.** Every bypass and regression found here was found by
  running something, and none by reading code.
- **A test must fail for the reason it names.** Revert the fix and confirm the failure is the
  one you claim.

The test suite carries a collection floor: a full run must collect at least the number
recorded in `.stride.md`, enforced both inside the run and by `scripts/check_test_floor.py`
outside it, so a test file can never silently stop being collected.
