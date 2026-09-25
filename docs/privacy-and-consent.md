# Privacy and consent: what this robot actually does

What is captured, what is stored, where it stays, how long it lives, and how to erase it.

Every specific claim below was checked against the code in the session that wrote it, by
running it rather than by reading it. Where something was **not** checked, or is not
done at all, it says so — the shape
[curation-log-italian-fast.md](curation-log-italian-fast.md) uses, for the same reason:
silence in a privacy document reads as assurance.

This describes a **prototype**. No lawyer has reviewed it. See
[What has not been done](#what-has-not-been-done), which is not an appendix.

---

## The wording a person is shown

Two things can be agreed to, and each has its own wording, because showing somebody one
notice and storing their yes against the other would make the stored record false.

### Before a face is enrolled

This is `CONSENT_STATEMENT` in `faces/enrollment.py`, id `face_recognition.v4`. It is
shown in full, and the operator must type the word `yes` — not a `[y/N]` default, because
a default that means yes is not consent.

> This robot can learn to recognise your face, so it knows whose lessons to teach.
>
> If you agree, it will take a few pictures with its camera and turn them into a list of
> numbers. It keeps the numbers. It does not keep the pictures, not even for a moment.
>
> Along with the numbers it saves the name you are being enrolled under, today's date,
> these words you are reading now, and whether you agreed yourself or an adult agreed
> for you. It does not save the name of the adult.
>
> The numbers never leave this robot. No copy of them is sent anywhere. They can tell
> you apart from other people in this household. They are not a lock, and they are not
> proof of who is in the room.
>
> Other things do leave, and you should know this before you agree. While this robot is
> switched on, it sends what its microphone hears to a language service on the internet
> -- your voice, and the voice of anyone else in the room. It sends the name it calls
> you by, how your lessons are going, and anything it has been asked to remember about
> you. Anything else it looks up about you in order to teach you goes to that service
> too. Sometimes it takes a picture to see what is in front of it, and that goes to the
> same service too. This robot does not keep those pictures. What that company does with
> any of it is their decision and not this robot's.
>
> You can say no now, or stop it while it is taking pictures, and it undoes what it
> saved. If it cannot finish undoing that while its screen is still open, it will say so
> there and tell whoever is running it how to remove you. The one thing it cannot undo is
> being switched off, losing power or being forced to quit at that moment: then your name
> and this agreement can stay on it, without any numbers.
>
> Afterwards you can ask whoever set this robot up to delete the numbers, and they will
> be deleted. They will need the learner id this robot shows them when you are enrolled,
> so ask them to keep it. The name and this agreement are kept, so there is always a
> record of what you were told and when.
>
> If this is a child, an adult of the household has to agree for them, here, in person.

### Before a learning record is kept without a face

`LOCAL_PROFILE_STATEMENT` in `learners/models.py`, id `local_profile.v1`, shown by
`enrol --without-face`. Deliberately shorter, because it covers less.

> This robot can keep a record of your language learning on this device, so it knows
> which lessons you have done and what to teach next.
>
> If you agree, it saves the name you are being registered under, today's date, these
> words you are reading now, and whether you agreed yourself or an adult agreed for you.
> It does not save the name of the adult.
>
> It does NOT use the camera and does NOT keep any face data. This robot will not
> recognise you: whoever sets it up chooses who it is serving.
>
> The record stays on this robot. You can ask whoever set this robot up to show you what
> is stored, or to delete all of it, and it is gone.

**Neither wording can drift from the code.** Each is pinned by a SHA-256 digest —
`CONSENT_STATEMENT_DIGESTS` and `LOCAL_PROFILE_STATEMENT_DIGESTS` — and a test fails if a
sentence changes without the digest moving. That rule exists because the face wording's v2
was edited twice with the id never moving, and only a review noticed.

**v4 narrowed a promise v3 could not keep.** v3 said "stop at any point before it
finishes, and nothing is kept". Measured: an enrolment ended by SIGTERM or SIGHUP — `kill`,
or closing the terminal or SSH session it runs in — left the person's learner row and
consent row behind, printed nothing and named no id; only Ctrl-C was undone. SIGTERM and
SIGHUP now run the same undo as Ctrl-C, and a test sends each to a real enrolment and
checks the database is back where it started. No code can undo a SIGKILL or a power cut
after the agreement is written, so v4 says so instead of promising it.

---

## What is stored, exactly

Everything personal lives in **three files** in the app's instance directory.

### `learners.v1.sqlite3`

| table | what it holds about a person |
|---|---|
| `learners` | `id` (a random uuid4 hex, derived from neither the name nor the clock), `display_name`, `created_at` |
| `faceprints` | `learner_id`, `embedding_model`, `dimension`, `vector`, `created_at` — one row per person, at most |
| `consents` | `learner_id`, `scope`, `statement_id`, `statement_text`, `granted_by`, `granted_via`, `granted_at`, `withdrawn_at` |
| `lesson_results` | which lesson, the outcome, a score, when |

The other eight tables — `languages`, `lessons`, `lesson_dialogues`,
`lesson_dialogue_turns`, `lesson_notes`, `lesson_drills`, `lesson_sources`, `schema_meta`
— are course material and are identical in every household. A test derives that list from
`schema.sql`, so a new table cannot appear without this accounting failing.

**The faceprint is not a picture.** It is `dimension` float32 numbers, little-endian,
stored as a BLOB whose length the schema requires to equal `dimension * 4`. The model is
`opencv_sface_2021dec_fp32`.

The notice promises the pictures are not kept, and two guards cover that claim — stated
with their limits, because an earlier draft of this very paragraph said "no image is
written anywhere by this package at any point", which is wider than what is checked:

- an enrolment run is executed with `builtins.open` intercepted, and nothing is opened
  for writing. This covers **Python-level writers only** — a C-extension writer such as
  `cv2.imwrite` never touches `builtins.open` and would pass straight through.
- that native path is closed separately by `_PERMITTED_CALLS` in
  `tests/test_face_matching.py`, an allow-list over every call in the whole `faces`
  package, so an image writer fails by not being on the list rather than by having been
  foreseen. **The list matches call names**, so on its own it could be walked round by
  renaming — measured: `from cv2 import imwrite as create` followed by `create(...)`, and
  `detect = open` followed by `detect(path, "wb")`, both passed it. A second test now
  refuses any binding of a permitted name other than a `def`, a `class` or an import
  under its own name, so a renamed writer fails there instead.

Together those cover the code that handles frames. Neither is a statement about every
line of the app.

**`statement_text` stores the wording itself, not just its id**, so the record answers
what a person was told *on the day* rather than what the constant says today.

### `memory.v1.json`

Separate from the database, and **not learner-scoped**: it holds short facts the robot was
asked to remember, for the household as a whole. A fact can name somebody and say
something about them. This matters for erasure — see below. It also means one learner's
facts reach another learner's session. `prompts.py` puts every stored fact in front of
the session instructions whoever is being served, and the `forget` tool returns the other
facts matching its query, measured with two learners sharing one instance. Neither tool is
in the tutor's locked profile today, so nothing shipped writes this file. It is still an
**open item**, not a fixed one: enabling `remember` would share one learner's facts with
every other learner.

### `startup_settings.json`

Holds the startup profile, the voice, and — since the app gained a way to serve somebody
when recognition cannot — the **learner id** of the person to serve. That id names a
person to anybody who can also read the database.

### All three are owner-only

All three are `-rw-------`, measured on a live instance directory. The database is
restricted by `learners/store.py`'s `_restrict_permissions`, which also covers the `-wal`,
`-shm` and `-journal` companions; the memory file and the settings file are created with
`os.open` at that mode, so their contents never exist at the umask default.

**Two corrections are recorded here rather than quietly applied**, because this document's
whole claim is that its claims were checked. An earlier draft said "two files" and "both
are owner-only" — `startup_settings.json` was the third, and it was at `-rw-r--r--`,
holding a learner id, in the same directory as two files this same change had just made
owner-only. And an earlier fix to the memory file chmod'd *after* writing, while this
document claimed the contents were "never world-readable even briefly"; measured under
umask 022, the temp file sat at `-rw-r--r--` for the window between the two calls. Both
are now created with the mode rather than corrected afterwards. A test races a watcher
against roughly a hundred writes and never observes another mode — though only the memory
file is written through a temp path at all, so the race covers that one and the settings
file is covered by the final-mode check beside it. An earlier draft said "200 writes" and
implied the race covered both.

The threat is a shared machine, an unencrypted backup, or the SD card out of a Reachy
Wireless — none of which goes through any of the app's own access rules.

---

## Where it stays

**Faceprints never leave the robot, and this is structural rather than a promise.**

- No tool exposed to the language model imports anything from the `faces` package, and no
  tool returns a value named `vector`, `embedding` or `faceprint`. A test walks every tool
  module and asserts both.
- The only readers of a stored vector in the whole app are `current_learner.py`, which
  passes it to the matcher, and `faces/matching.py`, which is arithmetic. Neither touches
  the network.
- Enrolment is a CLI subcommand, not a tool, and is deliberately absent from the
  LAN-reachable `/rpc` surface.

### What else is reachable from the home network

**This section was rewritten after a review proved its first version wrong in both
directions**, so read it as the corrected account rather than the original one. The first
version was written from `rpc-control-surface.md` instead of from `console.py`: it listed
a method the code refuses, omitted eight it exposes, and — the serious one — said no
learner data crosses the surface, which is false.

On a Reachy Mini Wireless the app listens on **`0.0.0.0:7860`** — every device on the
household Wi-Fi — and it has to: the desktop dashboard loads the app at the robot's LAN
address and kills it after 60 seconds if it cannot. There is **no credential**, because
nothing the SDK carries to the app could supply one; `rpc-control-surface.md` records that
in full. An `Origin` check refuses a cross-origin browser page and, stated plainly, does
not stop a direct caller on the LAN, which can send whatever headers it likes.

**The conversation is no longer broadcast to your Wi-Fi, unless a developer switches it
on.** `/rpc` is not request-and-response only: the server *broadcasts* notifications to
every attached websocket, and `conversation.transcript` carries the verbatim speech —
what the learner said and what the tutor replied. Until this was fixed, a peer that
presented no credential, sent no `Origin` header and called no method still received
them. A review proved it live, watching `{"role": "user", "text": "my name is Alice
Zebediah and I am seven"}` arrive on such a connection, and it was measured again by
dialling a server's LAN address. The earlier version of this section said nothing could
prevent it because no credential is available. That was wrong: the fix needs no
credential. The server now sends only the notifications `console.py` names
(`_NOTIFICATIONS_SENT_ON_THE_NETWORK`: turn, activity, phase, audio level, carrying machine
codes and numbers only). It withholds `conversation.transcript` unless the app was
started with `REACHY_MINI_DEV_BROADCAST_TRANSCRIPT=1`, a development switch that logs a
warning on every start and must never be set on a robot in somebody's home. The settings
UI never displayed transcripts, so it shows nothing less.

**Which methods a caller may invoke** is an allow-list — `_RPC_METHODS_EXPOSED_ON_THE_NETWORK`
in `console.py` — and anything outside it, including any method added later, is replaced
by a refusal at registration. A test derives this table from that frozenset, so the
document cannot drift from it:

| method | what it does | read? |
|---|---|---|
| `conversation.say` | **injects a turn the model treats as the learner speaking** — see below | no |
| `conversation.interrupt` | cuts it off mid-sentence | no |
| `conversation.status` | whether it is connected and listening | yes |
| `conversation.mic` | whether the microphone is muted | yes |
| `personalities.list` / `personalities.all` / `personalities.load` / `personalities.avatar` | the tutor personas, **including their full system-prompt instructions** | yes |
| `voices.list` / `voices.current` | the available and selected voices | yes |
| `tool_spaces.list` | which Hugging Face Space tool sources are installed | yes |
| `profile_tools.get` | which tools a profile has enabled | yes |

**The microphone cannot be turned on remotely.** `conversation.mic` is read-only and
refuses a `muted` parameter outright rather than ignoring it — it used to accept one and
was deliberately neutered, because no check here can tell the dashboard from anything else
on the network.

**`backend.config` is refused over the network**, and the first version of this document
wrongly listed it as available. It can repoint the speech connection at another host, and
its host check accepts any DNS name or IP rather than only Hugging Face — so had it been
reachable, a LAN caller could have redirected where the microphone audio goes. It is not
reachable. The protection is that every writer is gated off the network entirely, not that
its arguments are validated.

**No method returns a name, an id, a faceprint or any progress**, and enrolment and
erasure are not exposed. That is a narrower statement than the one this section used to
make, and it is the one the code supports: the leak is the broadcast above, not the
methods.

**Other things do leave, and the notice says so** because a review found the earlier
wording claimed nothing left at all while `get_profile` was returning a display name to
the model. What goes to the hosted language service: microphone audio, the display name,
lesson progress, remembered facts, and — when a tool takes one — a camera frame. A test
derives that list from the tool code rather than from the sentence, so the notice fails
when the data flow changes.

---

## What the logs contain

Logs go to **stderr**. The app installs no file handler, so it writes no log file of its
own; what happens to that stream is whoever launched the robot's business.

**A learner's name, id or transcript must never reach a log** is a standing rule in this
project, and it is enforced rather than remembered: `learners/store.py` binds every value
that reaches a log line to a producer a static check can prove yields a shape rather than
a value, and the tool layer redacts tool calls and results by kind, so a payload whose
producer failed is redacted rather than falling through to cleartext.

**There are two deliberate exceptions, and both are demoted rather than redacted.**
Blanket redaction would destroy what the console is for, so at **INFO** — the default —
each is logged as its *shape*, and at **DEBUG** — which is `--debug`, an opt-in on a
robot somebody is debugging — the words themselves are logged:

| what | at INFO | at DEBUG |
|---|---|---|
| the transcript — the learner's speech and the tutor's reply | who spoke and how much | the words, truncated at 500 characters |
| the `remember` and `forget` tools' own text | the shape of the fact or query | the words, truncated at 120 characters |

**The first row was false until a later fix, and is recorded as such.** The console's
DEBUG line truncates at 500 characters, but `huggingface_realtime.py` wrote its own DEBUG
copies of the user transcript, the partial transcript, the assistant transcript and the
response text, whole — measured, a 1,598-character transcript appeared untruncated beside
the console's truncated line. Those lines now log the shape only, so the console's is the
one copy, and a test drives the handler with a sentinel past character 1,500 to keep it so.

The second one is the more personal of the two, and `remember.py` says so where it makes
the choice: "this tool's own description tells the model to store a name, so its argument
is the most reliably personal string in the app." An earlier version of this section
named only the transcript.

So: running with `--debug` and capturing stderr records what was said in the room and
what the robot was asked to remember, including any name in either. That is the honest
statement of it, and it is a choice rather than an oversight.

---

## How long it lives

**Nothing in the database expires.** A faceprint, a name, an agreement and a lesson
history live until somebody deletes them. Checked as an allow-list rather than by
searching for a sweep: every `DELETE` the store can execute is one of three named
statements — the two erasures a person can ask for, and the undo for a half-finished
enrolment — none of them filters on a time column, and none is written inline where an
allow-list of constants could not see it. One of the three, the faceprint delete, also
runs as the *replace* inside enrolment, so three constants and four uses.

**The limit of that check**, since an earlier draft claimed more: it is a statement about
SQL. A sweep that selected stale ids in Python and passed them to the existing erasure
statement would change no SQL and pass. Nothing like that exists today; a reader is what
would catch one.

**One thing does expire, and it is the remembered facts.** `memory.v1.json` keeps the
newest **60** and silently discards the oldest beyond that, with no operator involved.
Measured. So the sentence above is about the database and not about everything.

---

## How to erase it

Three operator commands, and they are deliberately different requests.

| ask | command | what goes | what stays |
|---|---|---|---|
| stop recognising me | `enrol --forget ID` | the faceprint | the person, their agreement, their lesson history |
| forget me entirely | `enrol --forget-everything ID` | the learner row, faceprint, agreement and every lesson result | nothing in the database |
| undo a half-finished enrolment | `enrol --remove ID` | the person — **only** if they have no lesson history | — |

`--forget-everything` is one `DELETE`; consents, faceprints and lesson results follow by
`ON DELETE CASCADE`.

**It does print the person's name**, and an earlier draft of this document said it
"reports counts and never a name", which was false. The distinction it garbled is worth
keeping: the store's **log line** carries neither a name nor an id — nor the counts,
which a previous version of this sentence said it did; measured, it reads "A household
member was forgotten; the counts are on the returned outcome", and the counts reach only
the terminal — so an audit trail of a deletion does not record who was deleted, but the
**operator's terminal**
prints `Forgot <name> completely.`, deliberately, because an erasure that cannot say who
it erased is not much of a confirmation. A terminal is not a log; redirecting that output
to a file writes the name into it, and the subcommand's own help says so.

**Do the bytes actually go?** Measured, across `learners.v1.sqlite3`, its `-wal` and its
`-shm`: after a full erasure the packed vector, the display name and the learner id are
absent from all three. `secure_delete` zeroes a freed page when it is *written*, and
emptying the write-ahead log is what writes it. `VACUUM` is not required.

Three things about that are worth stating precisely, because each was got wrong first:

- **A database is three files.** An earlier measurement searched only the main one,
  found nothing and concluded the data was gone — while the vector, the name and the id
  were sitting in a 49 KB `-wal`. The checkpoint is `TRUNCATE` for that reason.
- **A reader can defer it.** If another connection holds a read transaction the log
  cannot be emptied; the command says so on screen rather than reporting success, and
  the bytes go at the next checkpoint nobody is holding open.
- **A crash before the checkpoint leaves them until the next open.** Measured by killing
  a process between the delete and the checkpoint. The window is bounded by the next time
  anything opens the database.

**What erasure does NOT remove, measured — two things, not one.**

First, the learner id in `startup_settings.json` if that person was configured as the one
to serve when recognition cannot answer. A review measured the id still sitting there
after `--forget-everything` reported success. **Now fixed**: the command clears the
setting when it names the person being erased, and leaves it alone when it names somebody
else. The resolver already refused a dangling id, so this was about not keeping the
identifier rather than about correctness.

Second, and still open: anything the robot was asked to remember.
`memory.v1.json` is not touched by any of the three commands, so a fact naming an erased
person survives. A household can ask the robot to forget a specific fact in conversation,
but "forget me entirely" does not clear it. **This is a real gap, not a subtlety** — it is
listed below rather than left here.

---

## What has not been done

Recorded beside what has, because a household reading only the good half would be misled.

**No lawyer has reviewed any of this.** `plan.md` says to consult a privacy lawyer about
in-home enrolment of minors before any public launch. That has not happened. This document
is the thing you would hand them.

**There is no liveness check, and it is a decision rather than an oversight.**
`faces/__init__.py` says so in as many words. A printed photograph held up to the camera
is not distinguished from a person. This is why the notice says the numbers "are not a
lock, and they are not proof of who is in the room", and why recognition may only ever
choose whose lessons to load — never authorise anything. It must not be extended to
anything that authorises without a liveness check first.

**Recognition is not switched on yet.** `THRESHOLD_CALIBRATED` is `False`, so no identity
is ever set from a face today. The matching threshold has not been measured on real
hardware.

**Erasure does not reach remembered facts.** As measured above, `memory.v1.json` survives
every erasure command. Somebody asking to be forgotten is not fully forgotten. The fix is
small and is not in this task.

**Nothing verifies that an adult is an adult.** Enrolment records
`the_person_themselves` or `an_adult_of_the_household` as a **role**, chosen explicitly
every time with no default, and physical presence at the robot is the entire control.
Which adult is deliberately not recorded — they are not a learner here, and naming them
would store personal data about somebody who was never asked. `learners` has no age
field, so the robot does not know who is a child and cannot enforce anything itself.

**There is no upgrade from a no-face registration to face recognition.** Measured:
`record_consent` always inserts a learner as well, so reusing an existing id is refused
and omitting it silently creates a *second* household member with the same name. Adding
face recognition later means registering somebody again, and their history does not come
with them.

**Nothing happens on resale or a change of household.** There is no factory-reset
command, no "wipe this robot" path, and nothing prompts anybody. A robot handed to a new
household arrives with the previous one's names, faceprints, lesson histories and
remembered facts intact, and the only way to clear them is to erase each person by id or
delete the files by hand. **This is the largest open item in this document.**

**A withdrawal column exists and nothing writes to it.** `consents.withdrawn_at` is
honoured by the faceprint gate — a withdrawn consent stops a faceprint being stored — but
no code path sets it yet. Withdrawing consent today means deleting the faceprint.

**Anyone on the household Wi-Fi can speak to the robot as the current learner.** This
replaces an earlier entry that said only "make the robot speak" and that "no learner data
crosses that surface". Both were wrong. `conversation.say` does not recite text. It
injects the text into the live model session as a *user* turn, the same message a spoken
sentence becomes. Measured: a call from an anonymous `/rpc` peer produced exactly that.
The model may then use any of its tools for whoever the app is serving: read their name
and progress aloud, start a lesson, record a lesson result. It cannot reach a different
learner, because no tool takes an identity. The reply is spoken aloud in the room. With
transcripts withheld (above), it no longer comes back to the caller as text unless the
developer switch is on. That a live model does call a writer tool from such a turn was
not measured for this entry. The port must be open for the dashboard, and there is no
credential available to the app. The method stays exposed only because nobody has
verified whether the dashboard depends on it. **This is the largest unresolved exposure
in the app.** Removing it is a decision for the maintainer. The microphone cannot be
turned on remotely.

**Remembered facts are capped, not kept.** The 61st fact silently evicts the oldest. That
is the one thing in the app that discards data without anybody asking.

**Nobody outside the project has read the consent wording.** The task that wrote this
asks for that check. It has not been done, so whether the wording is *meaningful* rather
than merely *obtained* is untested with a real reader.

---

## Where the claims come from

| claim | checked against |
|---|---|
| what each table holds | `learners/schema.sql` |
| the faceprint is numbers, never an image | `schema.sql`'s `faceprints`, and the two guards named above |
| what reaches a log | `console.py`'s `log_handler_message`, and the store's log-argument guard |
| what is reachable on the LAN | `console.py`'s `_RPC_METHODS_EXPOSED_ON_THE_NETWORK` and `rpc-control-surface.md` |
| all three files are owner-only | measured on a real instance directory, with the temp-file window raced |
| faceprints never leave | the tool import closure and the egress test |
| nothing expires | searched for a retention path; there is none |
| erasure counts and bytes | measured, including the `-wal` and `-shm` companions |
| memories survive erasure | measured |
| no liveness | `faces/__init__.py` |
| consent roles | `learners/models.py`, `consents.granted_by` |

The mechanism behind all of it is in
[learner-database.md](learner-database.md); the commitments this is measured against are
in [plan.md](plan.md).
