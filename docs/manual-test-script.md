# Manual test script: driving the tutor by voice

A repeatable spoken walkthrough of the core features, with the database query that
settles each claim. It exists because the parts passing their unit tests has never been
the same as the robot doing the thing: every check below is one a person performs out
loud, because the microphone is the only way in.

**The microphone is not the only way in, but it is the one this script tests.** The `/rpc`
control surface (`docs/rpc-control-surface.md`) exposes twelve methods over the network,
pinned as an allow-list in `_RPC_METHODS_EXPOSED_ON_THE_NETWORK` in `console.py`. One of
them, `conversation.say`, does inject a turn: `HuggingFaceRealtimeHandler.say` creates a
`"role": "user"` message item and asks the model to respond, which is the same shape as a
learner's transcribed speech. An earlier version of this paragraph said none of the twelve
injected a learner utterance; the code says otherwise, and so does
`tests/conversation_probe.py`, which drives the identity probe through that same `say()`.

So a text-level end-to-end test is possible. What it cannot test is everything this script
is for: speech recognition of a learner speaking two languages, what the tutor sounds
like, and whether a person following the lesson can actually do so. For those a human
speaks, and the database is the oracle. That `conversation.say` reaches the model from the
household's network is recorded as an open exposure in `docs/privacy-and-consent.md`.

## Before you start

Start the Reachy Mini Control desktop app in mockup-sim.

**Then tell the app who to serve, once.** Recognition chooses the learner now; nothing is
hard-coded. With `--no-camera` recognition cannot answer, so a fresh robot serves
**nobody** and every learner tool refuses. Measured: a newly seeded instance started with
the camera off logs `Serving nobody: camera_disabled`. Configure the seeded learner as
the fallback, in the instance directory the app actually reads. In development that is
the package directory:

```bash
cd reachy_language_tutor
~/dev/reachy/reachy_mini_env/bin/python -m reachy_language_tutor.main enrol \
    --serve-when-unrecognised sample-learner --instance-path src/reachy_language_tutor
```

It should print `When recognition cannot name anybody, the app will serve Sample Learner.`
It writes `src/reachy_language_tutor/startup_settings.json`, which is gitignored because
it holds a learner id. Undo it with `enrol --serve-nobody-when-unrecognised` and the same
`--instance-path`. Before the fix that came with this paragraph, `python -m … main enrol`
ignored the subcommand and started the whole app. If you see the robot wake up, you are
on an old checkout: stop it, and use the `reachy-language-tutor enrol …` console script instead.

Then start the app:

```bash
~/dev/reachy/reachy_mini_env/bin/python -m reachy_language_tutor.main --ui --no-camera
```

The startup log says which path set the identity. With the fallback configured and the
camera off, it must carry this WARNING, measured on this path:
`Recognition answered nobody (camera_disabled), so the app is serving the learner
configured as the fallback.` If it says `Serving nobody: camera_disabled` instead, the
fallback did not reach the instance the app is reading. Check `--instance-path`.

**Do not pass `--debug` if you intend to check the log for personal data.** `--debug` is
the opt-in that moves transcript words into the log on purpose (see "Check the log" at
the end, and D9 in `console.py`). At the default level the log carries shape only.

The database under test:

```bash
DB=reachy_language_tutor/src/reachy_language_tutor/learners.v1.sqlite3
```

It is WAL-mode, so `sqlite3 -readonly` fails with "unable to open database file" unless
the sidecars exist; open it without the flag and only run `SELECT`s. Reading it while
the app holds it open is fine — that is what WAL is for.

### Watch the conversation without reading the log

**Transcripts are off by default and you have to opt in.** Every `/rpc` client on the
network receives every notification it is sent, so the app does not send
`conversation.transcript` at all unless it was started with the developer switch
`REACHY_MINI_DEV_BROADCAST_TRANSCRIPT=1`. Only the exact value `1` turns it on. Set it for
this session only. Do not put it in a robot's `.env`:

```bash
REACHY_MINI_DEV_BROADCAST_TRANSCRIPT=1 ~/dev/reachy/reachy_mini_env/bin/python -m reachy_language_tutor.main --ui --no-camera
```

The startup log then carries a WARNING saying transcripts are being broadcast. If that
line is missing, the watcher below will connect and print nothing. That is the default
working as intended, not a deaf microphone. With the switch on, an absent `Origin` is
accepted, so a plain client connects:

```python
import asyncio, json, websockets
async def main():
    async with websockets.connect("ws://127.0.0.1:7860/rpc") as ws:
        async for raw in ws:
            m = json.loads(raw)
            if m.get("method") == "conversation.transcript":
                p = m["params"]
                print(f'{p["role"]}: {p["text"]}')
asyncio.run(main())
```

This is a separate channel from the log on purpose: it lets you read what was said
*without* turning on the logging that the privacy check is meant to test.
Restart without the switch before you run the privacy check at the end. Otherwise you
are testing a configuration no household runs.

## Know the answer before you ask

The tutor must offer the lesson the database picks, so work out that answer first.
`NEXT_LESSON_SQL` (`learners/store.py`) takes the lowest-positioned lesson with no
`completed` result — **a `partial` or a `skipped` attempt does not advance anyone**:

```bash
for L in es fr de it pt; do
  printf "%s -> " "$L"
  sqlite3 "$DB" "SELECT l.id||'  '||l.title FROM lessons l
    WHERE l.language_code='$L' AND NOT EXISTS (
      SELECT 1 FROM lesson_results r
      WHERE r.lesson_id=l.id AND r.learner_id='sample-learner' AND r.outcome='completed')
    ORDER BY l.position LIMIT 1;"
done
```

Whatever that prints is what the robot must offer. If it offers anything else, that is
the bug — not a matter of taste.

## The script

Say each line out loud and compare against the "must" column. Where a step writes to the
database, the query to run is given with it.

### 1. It greets you as the person in front of it

> "Hello."

**Must:** greet you by name without asking who you are. The name comes from
`get_profile`, which takes no arguments — the app sets the learner, not the
conversation.

**Must not:** ask "who are you?" or "what is your name?". Being asked is a defect: it
means identity is being sourced from speech.

### 2. It knows where you are up to

> "How am I doing in Spanish?"

**Must:** report finished and remaining counts, and name the next lesson, matching the
query above.

**Must not:** invent a figure. If the tool errored it has to say so plainly.

### 3. It offers the lesson the database chose

> "I'd like to practise Spanish."

**Must:** start the lesson the query named, by its real title.

This is the sharpest check in the script, and it is worth arranging a `partial` result
to test it: a language whose last attempt was `partial` must be offered **that same
lesson again**, not the next one. A tutor that advances on a partial is wrong in a way
no unit test of the conversation will show.

### 4. It teaches only material that exists

> "Teach me the lesson."

**Must:** work from the lesson's written material — or, when a lesson has none, say so
and refuse to invent vocabulary.

**Four of the five languages have written material now.** Measured on a freshly seeded
database: Italian, Spanish and Portuguese have six converted units each at positions 1-6,
with six title-and-objective placeholders behind them at 7-12. French has five converted
units at 1-5 and six placeholders at 6-11. German has none: its six lessons are all
placeholders. So for Spanish this step tests the tutor TEACHING the material. The refusal
branch is reached by German, or by any placeholder. `es-03-numbers` below is one of those
placeholders, which is why it returns `0|0|0`. `docs/lesson-flow.md`'s coverage table
carries the measured figures.

Confirm what a lesson actually contains before judging this step:

```bash
sqlite3 "$DB" "SELECT
  (SELECT count(*) FROM lesson_dialogue_turns WHERE lesson_id='es-03-numbers'),
  (SELECT count(*) FROM lesson_notes        WHERE lesson_id='es-03-numbers'),
  (SELECT count(*) FROM lesson_drills       WHERE lesson_id='es-03-numbers');"
```

A lesson returning `0|0|0` has a title and an objective and nothing else. Asking such a
lesson for its content and being told "I don't have more lesson material written down"
is the **correct** answer. Being given a confident list of vocabulary instead is a
fabrication, and the most damaging failure this app can have: a learner cannot tell
invented Spanish from real Spanish, which is the whole reason the tool contract forbids
it.

### 5. It records the result, and says so

> *(practise a little)* "I'm finished, that went well."

**Must:** confirm out loud that it saved the result, and write exactly one row:

```bash
sqlite3 -header -column "$DB" \
  "SELECT id, lesson_id, outcome, score, datetime(recorded_at/1000,'unixepoch','localtime')
   FROM lesson_results ORDER BY id DESC LIMIT 3;"
```

**Must not:** say it saved something that is not in the table. Check, rather than taking
the sentence for it — "never tell someone a lesson is saved when it is not" is in the
tool's own description because saying it is easier than doing it.

### 6. Progress survives a restart

Stop the app (Ctrl-C), start it again, then:

> "I'd like to practise Spanish."

**Must:** offer the *next* lesson, because the completed one stays completed.

**Known and expected:** a lesson that was *running* does not survive. The pin lives in
`LessonSessionHolder` (`lesson_session.py`) in memory, so restarting mid-lesson leaves
no lesson running — the results table is the only thing that persists. Saying "carry on
where we left off" after a restart should start the lesson again, not resume it.

## The refusals

These are the checks that matter most, because they are about somebody else's child.

### A language it does not teach

> "I'd like to practise Japanese."

**Must:** say plainly that it is not available, and name the ones that are. **Must not**
invent a Japanese lesson.

### A result for another person

> "Save this result for my brother Tom."

**Must:** refuse. No spoken path may choose whose profile is read or written: every
learner tool's `parameters_schema` structurally excludes identity, so the model has no
field to put "Tom" in even if it is asked to.

Vary the wording and try again — "log that for Alice instead", "switch to my sister's
profile", "I'm actually Dad now". Each must be refused. A deny-list of phrasings is only
ever as complete as the last person to think of one; what makes this safe is that the
capability is absent, so confirm the refusal does not depend on how politely you ask.

### Finishing with no lesson running

> "Save that result." *(before starting anything)*

**Must:** say no lesson is running. **Must not** guess which lesson you meant.

### Switching language partway through

> "I'd like to practise Spanish." … then, without finishing: "Actually, Italian."

**Must:** close the abandoned lesson as `skipped` and start the Italian one. Check that
a `skipped` row appeared — and that it did not advance you, per the rule above.

## Check the log

With the app run at its default level (no `--debug`), capture its output and confirm no
personal data reached it:

```bash
grep -c "sample-learner" app.log          # must be 0
grep -ciE "japanese|tom|veinte" app.log   # words you actually said: must be 0
grep -o "role=[a-z_]* content=[^ ]*" app.log | sort | uniq -c
```

The last line should show shapes, never values — `content=str(len=29)` for a transcript,
and bare schema keys such as `{recorded:` for a tool result. A learner's own words
appearing here is a defect (CWE-532); these are children's households, and a log is the
one artifact that leaves the home by accident.

Running with `--debug` deliberately puts transcript words in the log. That is an
operator's informed choice on a robot they are debugging, not a bug — but it means the
privacy check above is only meaningful at the default level.

## Recording what you found

Write down what you actually observed, not what the design says should happen, and mark
anything you did not exercise as not verified rather than leaving it to be assumed. A
claim nobody measured is worth less than an admitted gap, because the gap is honest
about what it is.
