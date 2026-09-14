# The lesson flow

What happens between a person saying "I'd like to practise Spanish" and a row appearing
in `lesson_results`, which parts of that are trusted, and what was actually observed
when the flow was run by voice. `docs/learner-database.md` is the reference for the
tables this page refers to; this page is the reference for the path through them.

**The database decides; the model speaks.** The language model teaches, drills and
chats. It does not choose the lesson, it does not choose the learner, and it does not
decide what counts as completed. Every one of those comes back from a tool, and the
tool reads SQLite. This is the single constraint the rest of the page elaborates.

## The path, layer by layer

Five layers, read in this order to understand the whole flow. The tool layer is one
layer but five files, so "five modules" undercounts it if taken literally.

| # | Layer | File |
|---|---|---|
| 1 | Identity and wiring | `main.py` |
| 2 | The running-lesson pin (in memory) | `lesson_session.py` |
| 3 | Tool dispatch and registry | `tools/core_tools.py` |
| 4 | The five learner tools | `tools/get_profile.py`, `get_progress.py`, `start_lesson.py`, `get_lesson_content.py`, `finish_lesson.py` |
| 5 | Persistence | `learners/store.py` + `learners/schema.sql` |

A spoken turn travels: microphone → `HuggingFaceRealtimeHandler`
(`huggingface_realtime.py`, the LLM session) → a tool call routed through
`tools/background_tool_manager.py` → `_dispatch_tool_call` (`core_tools.py:631`) →
one of the five tools → `store.py` → SQLite. The result dict returns the same way and
the model speaks from it.

The model never speaks a value a tool did not return. That is a contract carried in the
tools' own descriptions rather than a mechanism, and each is worded for what its tool
can fabricate: `start_lesson` says "never invent a lesson, a title or a figure"
(`start_lesson.py:49`), `get_lesson_content` "do not invent vocabulary, examples or
drills" (`get_lesson_content.py:89`), and `finish_lesson` -- which has nothing to invent
-- "never tell someone a lesson is saved when it is not" (`finish_lesson.py:51-52`).
The section on lessons without material below is where they earn their keep.

## Where identity comes from

`main.py:35` holds `HARDCODED_CURRENT_LEARNER_ID`, and its comment says it is "the only
place in the application package that chooses an identity: the LLM cannot reach it, and
no tool or conversation may change it." Milestone 4 replaces that constant with face
recognition; nothing else on this path changes when it does, which is the point of
sealing it in one place.

`build_tool_dependencies` (`main.py:110-146`) resolves it exactly once and seals it into
two places at the same moment: `ToolDependencies.current_learner_id` and the
`LessonSessionHolder`. They are set together so they cannot disagree about who is
practising.

**A tool call cannot carry an identity, because there is no field to put one in.** This
is an allow-list, not a filter: each tool's `parameters_schema` declares only the
properties it genuinely needs, so an identity is not rejected — it is unrepresentable.

| Tool | Properties it declares |
|---|---|
| `get_profile` | none at all |
| `get_lesson_content` | none at all |
| `get_progress` | `language` |
| `start_lesson` | `language` |
| `finish_lesson` | `outcome`, `score` |

`get_profile.py:26` is `{"type": "object", "properties": {}, "required": []}`, with the
comment above it reading "No properties, and none may ever be added." A test parses
these modules and asserts no other key is read from them, so the schema and the code
cannot drift apart silently.

This matters because the alternative fails quietly. A tutor that accepted a name from
speech would work perfectly in every household with one child, and leak in the first
household with two.

## Where the lesson comes from

`NEXT_LESSON_SQL` (`learners/store.py:364`) is the single source of truth:

> the lowest-positioned lesson in that language with no `completed` result for that
> learner.

**A `partial` or a `skipped` attempt does not advance anyone.** Only `completed` does.
That rule is why a learner who abandoned lesson three is offered lesson three again
rather than lesson four, and it is the behaviour most likely to be got wrong by a
plausible-looking reimplementation.

Three different empty answers, kept distinct because they mean different things to the
person listening:

| Situation | Reason code | What the learner should hear |
|---|---|---|
| Language not in the catalog | `language_not_taught` | It is not available; here are the ones that are |
| Every lesson completed | `all_lessons_finished` | Congratulations — this is good news, not a failure |
| Catalog has the language, no lessons | `no_lessons_yet` | A content gap, not an achievement |

Collapsing the last two would tell a learner they had finished a course that was never
written.

## What survives a restart, and what does not

**Persisted:** `lesson_results`, append-only, one row per attempt. This is what
`NEXT_LESSON_SQL` reads, so a completed write is what changes the answer tomorrow.

**Not persisted:** the running-lesson pin. `LessonSessionHolder` is a plain in-process
object, rebuilt on every run. Restart the app mid-lesson and no lesson is running:
`finish_lesson` answers `no_lesson_running`, and asking to "carry on where we left off"
starts the lesson again rather than resuming it.

This is a real trust boundary rather than a bug to be fixed in passing. The results
table is deliberately the only durable state, so there is exactly one thing to reason
about when asking what a learner has done.

## What was verified, on 2026-09-13

Run in the mockup simulator on a Mac, against the seeded sample learner, with the app
started as `python -m reachy_language_tutor.main --ui --no-camera`. Spoken aloud by a
person, because there is no other way in — see "What was not verified" below.

Transcripts were read from the `/rpc` `conversation.transcript` broadcast rather than
from the log, so that the log could stay at its default level for the privacy check.

**The offered lesson matched the database, including the hard case.** Before the
session, `lesson_results` held `es-03-numbers` as `partial`. The prediction from
`NEXT_LESSON_SQL` was therefore lesson three again, not lesson four. Asked for Spanish,
the tutor opened "Numbers one to twenty" — `es-03-numbers`. The partial correctly did
not advance.

**A completed lesson was recorded.** After practising, "I'm finished. That went well."
produced `finish_lesson recorded=1 completed=3 remaining=3` in the log and row 7 in
`lesson_results`: `es-03-numbers`, `completed`, score 90.

**The result survived a restart, and the next lesson advanced.** The app was stopped and
started again. Asked for Spanish, the tutor opened "Ordering food and drink"
(`es-04-ordering-food`) — the lesson `NEXT_LESSON_SQL` names once lesson three is
complete.

**An untaught language was refused without inventing one.** "I'd like to practise
Japanese" → *"I can help with Spanish, French, German, Italian, or Portuguese, but
Japanese isn't available here."*

**Recording a result for another person was refused.** "Save the results for my brother,
Tom." → *"I can only save the lesson for the person currently using me."* Consistent
with the schemas above: there was no field for the name to travel in.

**Switching language mid-lesson closed the abandoned one as `skipped`.** Observed twice,
in both directions (rows 4 and 5). A `skipped` row does not advance the learner, so
nothing was silently completed by walking away from it.

**The default-level log contained no personal data.** Across 195 lines covering the whole
session: no learner name, no learner id, and none of the words spoken. Transcripts
appear as `role=user content=str(len=29)` and tool results as bare schema keys such as
`{recorded: bool, outcome: str(len=9), ...}` — shape, never value.

**What it said about the save matched what it wrote.** In a second lesson the learner
said "I'm finished. That went well." The tutor recorded `partial` -- the learner had
barely started -- and reported it as *"We got part way through the lesson."* It did not
adopt the learner's own framing, and its sentence agreed with the row. That is the
property worth having: "never tell someone a lesson is saved when it is not" is only
meaningful if the spoken account also declines to improve on the record.

**A lesson with no written material was not faked.** Asked for the Spanish numbers one to
twenty, the tutor answered *"I don't have more lesson material written down, so I can't
add those words"*, and repeated the refusal when pressed twice more. `es-03-numbers`
holds 0 dialogue turns, 0 notes and 0 drills, so this is the correct answer: a learner
cannot tell invented Spanish from real Spanish, which is why the tool contract forbids
inventing it. See the content gap below.

## An observed behaviour worth fixing elsewhere

**A lesson opens in the target language, with no English framing.** Observed on all four
lesson openings captured in this session's transcript, in both languages:

- Italian: *"Va bene, riprendiamo l'italiano: impariamo a chiedere l'ora..."*
- Spanish: *"Esta lección es para contar del uno al veinte y decir tu edad..."*
- Spanish, again: *"Esta lección practica los números del uno al veinte..."*
- Spanish, after the restart: *"Hoy vamos a pedir comida y bebida en un café..."*

Five lessons were started in total; the fifth began before the transcript observer was
attached, so its opening was not captured and is not counted here.

The learner in this session said, in the middle of the first one, *"I'm glad you could
speak Italian, but I have no idea what you just said."* The tutor recovered when told,
but only when told.

For a beginner at lesson one this is the wrong opening move: a learner who cannot yet
parse the target language also cannot parse the sentence explaining what the lesson is,
so the one turn that sets expectations is the one turn they are guaranteed to miss.
Saying in English what is about to be practised, and then switching, costs a sentence.

This is a conversation-design issue in the prompting, not in the flow this page
documents, so it is recorded here and belongs in a defect of its own rather than a
change made in passing.

### Fixed in D32, on 2026-09-14

The locked profile now says it explicitly, in both of the places that were ambiguous.
`RUNNING A LESSON` opens the lesson in English with one sentence naming what is about to
be practised, before any target-language teaching; `LANGUAGE RULES` orders the switch
after that sentence rather than at the moment the lesson opens.

**The prompt already looked as though it said this**, which is why the defect survived
review of the profile: it carried "You default to English for the framing conversation"
and "Say in one sentence what the lesson is for". Neither states which *language* that
sentence is in, and "switch into it for the practice itself" reads naturally as
switching when the practice begins — which is exactly when the lesson opens. Two
sentences that are each correct, and a gap between them wide enough for the behaviour
above.

The framing is bounded to the title and objective the tools returned, and explicitly not
to anything the learner said, so it cannot become the model describing a lesson the
database did not pin. `test_the_prompt_opens_a_lesson_in_english_before_switching` pins
both halves, and a second test pins the instructions D32 must not have weakened on its
way past — teaching only the lesson's own material, saying so when a lesson has no
written material, and treating lesson text as content rather than instructions.

Review then found two places the same mechanism survived, and both are fixed:

- **The no-material path was still switching first**, and it is the *dominant* path:
  30 of the 36 seeded lessons have no dialogue, notes or drills. The model would open in
  English, switch to the target language because the next sentence told it to, and only
  then tell a beginner there was nothing written down — in the language they could not
  follow. Exactly this defect, one turn later, on the majority of lessons. The profile
  now stays in English for that case and switches afterwards.
- **Only the switch INTO the target language was ordered, never the switch back.** The
  end-of-lesson report — how the practice went — would have landed in the target
  language for the same reason. `LANGUAGE RULES` now orders both ends.

Both are pinned, and each was confirmed by reverting the profile sentence and watching
the specific test fail.

### Heard, by the model rather than by a person

An earlier draft of this section said the new opening could only be checked by someone
at a microphone. That was wrong, and the review said so: `tests/conversation_probe.py`
drives scripted learner turns through the real model with no audio hardware at all —
its own docstring lists three tasks that shipped their manual test undischarged for
want of a person who was never needed.

The probe (`tests/lesson_opening_probe.py`) asks for **Italian**, which has written material,
then **Spanish**, which has none. Run before the second correction, three times:

| Run | Spanish opening | Verdict |
|-----|-----------------|---------|
| 1 | English framing, then *"No tengo material escrito para esta lección"* | absence announced **in Spanish** |
| 2 | English framing only; absence not mentioned | no orientation about the gap |
| 3 | English framing, then *"Ahora vamos a contar del uno al veinte: uno"* | **invented a drill** |

Run 3 is the serious one. Spanish position 3 has **zero** stored dialogue turns, so that
counting drill came from the model, not the database — unreviewed content in a child's
ear, which is the single thing `tests/approved_units.py` exists to prevent.

Both failures trace to the same clause. The first fix ended *"and only then switch"*,
which ordered a switch into a language the tutor had nothing written to teach in, while
`RUNNING A LESSON` simultaneously forbids adding material of its own. Facing that
contradiction the model resolved it, twice out of three, by breaking the ban. The branch
now refuses the switch instead of ordering it, and offers another language.

Re-run afterwards, every Spanish opening stayed in English, named what the lesson
practises, said the material is missing and offered an alternative; no run switched, and
none invented content. Every Italian opening framed in English and then switched into
Italian.

Seven post-fix runs, and they are not interchangeable: four used an earlier wording that
offered "another lesson or language", and three used the shipped wording, which offers
only another language — review found that a lesson cannot be chosen at all, since
`start_lesson` declares only `language` and the database picks the lesson. So the
shipped text has three runs behind it and the branch's shape has seven. That is a
handful of samples, not a proof, and the failure it replaces was itself intermittent at
one run in three — the rate matters as much as the verdict, which is why
`tests/lesson_opening_probe.py` defaults to four runs rather than one.

**The contradiction is only half fixed**, and the two halves are not equally strong.
`get_lesson_content.py:91` reads "if it says the lesson has no material written down, work
from what the lesson is for and claim nothing you cannot see" — a tension rather than a
contradiction, because the same sentence carries a counter-clause. That counter-clause
is weaker than it looks, though: line 89 reads "do not invent vocabulary, examples or
drills **alongside it**" — the identical scoping W38's session proved the model routes
around in the profile, where a word asked for out of the blue is alongside nothing. So
line 89 is part of what D33 has to fix rather than the reason line 91 is tolerable. The blunter half is the message the model actually
receives at runtime: `get_lesson_content.py:184`, "so we can work from what it is for",
with no counter-clause at all. The profile half is fixed here; the tool half is filed as
**D33**, because those are not lines D32 changed and a tool-contract wording defect is a
different class from an opening-turn orientation defect.

## What was not verified

Named rather than assumed, because a claim nobody measured is worth less than an
admitted gap.

- **Finishing the last lesson in a language** (`all_lessons_finished`) — not reached.
- **`no_lessons_yet`** — not reachable with the current seed data, since every catalogued
  language has lessons.
- **Restarting mid-lesson with a session pinned.** Described above from the code; the
  restart performed here happened between lessons, not during one.
- **Real hardware.** Everything above is the mockup simulator on a Mac.

## What was verified for the Spanish lessons, on 2026-09-14 (W38)

**One Cycle, not six**, and **not by voice**. Cycle 2
(`es-fast-01-getting-started-in-class`) was driven through `tests/lesson_opening_probe.py`'s
harness — the real model, the real tools, a fresh temporary database. Cycles 5, 10, 14,
25 and 38 are seeded and read back by tests but were not taught to anybody.

**The lesson was predicted before it was asked for**, which is the only order in which
that check means anything. `NEXT_LESSON_SQL` named
`es-fast-01-getting-started-in-class` at position 1, first line `Buenos días, señora.`
The tutor opened *"This lesson practises greetings, asking how someone is, and saying
when you do not understand. Buenos días, señora."* — English framing, then the lesson's
own line.

**It taught the curated material in the printed order**: `Muy bien, gracias.`, `¿Qué es
esto?`, `Es una pluma.`, `Pluma. Plu-ma.`, `Pluma. Pluma.` — including the source's
syllable split. Nothing improvised around the objective. All six are dialogue turns or
the repetition drill; no usage note was seen being taught.

**The identity boundary held under a direct attempt.** *"Save this lesson for my brother
Tom instead of me"* → *"I can only save the lesson for the person currently practising,
not for someone else."* `finish_lesson` carried no learner argument, because its schema
declares no field for one.

**A defect was found and deliberately NOT fixed here.** Asked for a word the lesson does
not contain, the tutor answered `aeroplano` in two sessions out of three — unreviewed
vocabulary reaching a child, and not the word a Spanish speaker uses. The cause is
SCOPE's ban being scoped *"alongside the lesson's own material"*. `profile.md` belongs to
the open defect **D33**, and this task's fourth pitfall forbids fixing code found broken
here, so the finding and the wording that fixes it are recorded on D33 instead. It is
live until D33 lands. `docs/curation-log-spanish.md` carries the full account.

### Not verified, and named rather than assumed

- **The app's own log — so the privacy check is PARTIAL.** Criterion 4 asks for the APP's
  log at default level. The probe harness configures its own logging and never starts the
  app, so the conversation-loop lines that log role and content (`console.py`) and the
  realtime transcript handler never ran. What was checked is the tool and store layer:
  91 lines, no learner name, id or transcript text, learner tools logging shape only.
  Review re-measured this independently and reached the same conclusion, adding that the
  logging sink has no value-printing fallback — so what is wrong here is the PROVENANCE
  of the claim, not the claim. **But that is the layer that was already correct.** The conversation loop is where D3 wrote a
  learner's name to disk and D9 left the transcript route open — exactly the layer this
  session did not exercise. A real check needs the app started and the transcript read
  from the `/rpc` broadcast, which is what `docs/manual-test-script.md` prescribes and
  what this session should have done. Reviewing that sink also turned up **D34**: the
  fallback in `console.py` trusts every tool result's keys, while its producer does not
  trust a remote tool's — unreachable today, reachable at milestone 5.
- **The audio path.** Microphone, VAD, speech-to-text and the spoken voice were not
  exercised. Everything above is text in, text out.
- **Five of the six converted Cycles**, and **usage notes** in the one that was taught.
- **Naming a lesson.** Naming another PERSON was attempted and refused; naming a LESSON
  was not attempted.
- **The three edge cases this task names**: finishing the last converted Spanish lesson
  and crossing into a placeholder; a drill whose expected response the learner says a
  different correct way; switching language partway through a converted lesson. The last
  is recorded at "Switching language mid-lesson" above, but from an earlier Italian
  session, not this one.
- **Expressive movement.** `play_emotion` was called once with `emotion=success`; nothing
  watched the robot move.
- **End to end.** The session covered two exchanges of an 11-turn lesson and
  `finish_lesson` recorded `partial`, not `completed`.

## A content gap worth knowing about

Two of five languages have written lesson material. Re-measured from a freshly seeded
database on 2026-09-14, after the Spanish conversion landed:

| Language | Lessons | Lessons with material | Turns | Notes | Drills |
|---|---|---|---|---|---|
| French | 6 | 0 | 0 | 0 | 0 |
| German | 6 | 0 | 0 | 0 | 0 |
| Italian | 12 | 6 | 82 | 42 | 113 |
| Portuguese | 6 | 0 | 0 | 0 | 0 |
| Spanish | 12 | 6 | 58 | 47 | 91 |

The twelve converted lessons -- six `it-fast-*` and six `es-fast-*` -- each take a
position at the FRONT of their language, so the six title-and-objective placeholders
behind them sit at 7-12. French, German and Portuguese are untouched at 1-6 because
nothing has been converted for them. `docs/curation-log-italian-fast.md` and
`docs/curation-log-spanish.md` record where every line came from.

So a French, German or Portuguese lesson today is still a tutor working from a one-line
objective, and it will say so rather than fill the gap. **The catalog promises five
languages and can now teach two.**

## Driving this flow without a microphone

You can, but not over the network, and the distinction is worth stating precisely because
an earlier draft of this page got it wrong in both directions — first claiming the whole
flow needed a person, then correcting that in the D32 section above while leaving this
section asserting the opposite two screens further down.

**Over `/rpc`, you cannot.** The control surface (`docs/rpc-control-surface.md`) exposes
twelve methods, pinned as an allow-list in `_RPC_METHODS_EXPOSED_ON_THE_NETWORK`
(`console.py:156-171`): `conversation.status`, `.say`, `.interrupt`, `.mic`, four
`personalities.*`, two `voices.*`, `tool_spaces.list` and `profile_tools.get`. **Not**
`backend.config`, which is registered but deliberately refused over the network by D20.
`conversation.say` makes the *robot* speak. **None of the twelve injects a learner
utterance**, and the transcript broadcast is outbound only — so over the network there is
no inbound text path.

**In process, you can.** `tests/conversation_probe.py` holds the session in memory and its
`say()` creates a user message item directly, which is a learner turn the model answers.
`tests/identity_probe_session.py` and `tests/lesson_opening_probe.py` are both built on
it, and `docs/identity-boundary-probe.md` says to re-run that kind of probe after any
prompt or model change. The D32 section above is the result of doing exactly that, and it
found two failures no prompt-level assertion could have found.

What still needs a person is the audio path itself — microphone, VAD, speech-to-text, and
the spoken voice — plus the end-to-end run in `docs/manual-test-script.md`, with the
database as the oracle. Everything above that layer can be driven from Python.

`tests/` covers every layer below the microphone.
