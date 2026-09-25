# Curation log: FSI Italian FAST, Volume 1

What was taken from this course, what was changed on the way, and what was left behind.

A judgement nobody can review is not a judgement. This file exists so that a person can
read a line a robot says in somebody's house, find the page it came from, and see what
was done to it in between. Every claim here was checked in the session that made the
change; where something was **not** checked, it says so.

The method these decisions were applied with — the commands, the page-image check, the
order of the steps — is in [converting-a-course.md](converting-a-course.md). This file is
the record of the decisions themselves.

## The source

| | |
|---|---|
| Course | *Italian FAST — Familiarization and Short-Term Training*, Volume 1 |
| Publisher | Foreign Service Institute, U.S. Department of State |
| Year | 1992 |
| File | `FSI - Italian Familiarization and Short term Training - Volume 1.pdf` |
| Retrieved from | `fsi-language-courses-media.nyc3.cdn.digitaloceanspaces.com/languages/Italian/FAST/` |
| Size | 11,526,230 bytes, 456 pages |
| SHA-256 | `8c2182b7963f55ba805320488ef1d465d41afb490e7aea53abf774edc09c03d3` |

The size and page count were compared against what the task recorded before anything was
parsed, and both matched. The file is a scan with an OCR text layer; `pdfinfo` reports
the producer as "Adobe Acrobat 9.1 Paper Capture Plug-in", which is that OCR step.

## Rights: what was actually checked

**Checked, in the file itself:**

- **No copyright notice appears anywhere in the 456 pages.** Searched the extracted text
  for `copyright`, `©`, `all rights` and `reproduc`. The only hits were the digitiser's
  note (below) and one false positive inside an OCR error.
- **The preface names its authors and they are FSI staff**, supervised by FSI staff, and
  it is signed by *Mark C. Lissfelt, Dean, School of Language Studies, Foreign Service
  Institute, Department of State*. The work was prepared inside the institute, which is
  what matters: the exception the task warns about is a course produced under contract,
  and this one reads as in-house throughout.
- **The document metadata names the Foreign Service Institute as author** and the title
  matches the printed title page.
- **The scan carries a digitiser's note:** "The original photographs have been removed
  for this public domain version of the book." So this PDF is not a byte-for-byte
  facsimile — images were taken out. Nothing was added, and no text was edited, as far
  as can be told from the text layer.

**Not checked, and worth saying plainly:**

- No legal opinion was obtained, and none of the above is one.
- Whether any individual contributor's work was assigned or licensed separately was not
  investigated; the question does not arise from anything visible in the document.
- The removed photographs are the one place a third party's rights could have sat, and
  they are exactly what is absent from the file. No photograph was used here.

The position recorded in `converted_lessons.json` is the summary of this section, and it
is deliberately a description of what was found rather than a conclusion about the law.

## Conventions applied to every unit

These are not per-unit judgements; they are the consequences of a printed course being
read aloud by a robot, and they were applied the same way everywhere.

| Source | Becomes | Why |
|---|---|---|
| `signor(in)a/signore` | `signore` | A typographic shorthand a reader resolves silently. Spoken, it is gibberish. |
| `signor (last name)` | a name, or `signore` | A blank for the reader's own name cannot be said aloud. |
| `arrivato/a`, `pronto/a`, `stanco/a` | the masculine form | Same reason. The usage notes explain the agreement instead, which is where a spoken course can teach it. |
| `Impieg:`, `Comm.:`, `Parruc:` | `Impiegato`, `Commesso`, `Parrucchiere` | Abbreviated speaker labels in a printed script. The tutor says who is speaking. |
| lire amounts | euro, same digits | Italy replaced the lira in 2002. The digits are kept and the thousands dropped, so `120.000 lire` becomes `120 euro`. That is not the same number and not the same spoken line — *centoventimila lire* against *centoventi euro* — and it is the one place a price is a plausible figure today rather than the source's. |
| `... (lire)` blanks | a spoken amount | The source leaves the price blank for the instructor to fill. A blank cannot be spoken, so one was chosen. |
| instructions to the instructor | dropped | "With the instructor taking the part of the Italian" describes a classroom. The tutor *is* the instructor. |
| Fill-in-the-Blanks exercises | dropped | They are a writing exercise built on a printed page. Nothing is lost: they drill the same dialogue the tutor already has. |

**The target-language sentences were not rewritten.** The 1992 Italian is idiomatic
today, and rewriting it would throw away the reason for using a professionally built
course at all. Where a situation had to change, the change is a noun, not a sentence.

**Usage notes were adapted rather than transcribed.** The source's notes address a
student holding a book ("the le used on line 2", "see appendix A"). They were rewritten
to be said out loud, keeping the substance. Every Italian example quoted inside a note
is a word or phrase that appears in the same unit's verified dialogue or vocabulary, so
no unchecked Italian entered the database through a note.

**No dialogue was given a title.** FSI units do not title their dialogues, and inventing
one would be content nobody wrote. `dialogue_title` is therefore null for all six.

## What shipped, unit by unit

### `it-fast-01-what-time-is-it` — unit IV, printed page 83

*Getting the local time.* A driver takes you from the airport and you compare the time
with Boston.

- **Replaced:** `all'Ambasciata` → `alla stazione`, in the driver's question and in the
  vocabulary. The grammar point is the `a` + article contraction, and `alla stazione`
  keeps it exactly while putting the car somewhere a family would go.
- **Replaced:** `da Washington` → `da New York` in the driver's question. Washington is
  where a posting comes from, and leaving it in kept the arrival-at-post premise alive
  after the embassy noun had gone. New York and Boston are two ordinary American cities
  with the same offset from Rome, so the answer `No, da Boston` and the six-hour
  difference both still work.
- **Kept:** Boston and the six-hour difference. The time difference is the whole point of
  the unit, and a household with relatives abroad has exactly this conversation.
- **Assessed and kept:** the driver, the car at the exit and the choice of hotel. Without
  Washington and the embassy this is somebody being driven from an airport to a hotel,
  which is travel — one of the situations the task names as wanted. Recorded because a
  reviewer read the same scene as an arrival at post, and that reading is reasonable
  enough to be worth answering in writing rather than by silence.
- **Drills:** the time-telling model (Model 2, printed page 90) was taken as
  cue-response pairs, and the "where are you arriving from" substitutions from the same
  page as two more. Both were read off the page image.

### `it-fast-02-room-service` — unit VI, printed page 124

*At the hotel II.* A bellhop brings the cases up; you call room service.

- **Nothing removed for content.** This unit screened clean: no military, embassy or
  official vocabulary anywhere in its twenty-one pages.
- **Assessed and kept:** `Sono appena arrivato, ho soltanto dollari... va bene?` — a
  guest who has just arrived at a hotel and has no local cash. The same reasoning as unit
  IV: checking into a hotel is travel, and the line teaches `appena` and `soltanto` in a
  sentence people still say.
- **Replaced in the drills:** `le sigarette` → `le chiavi`, and `la patente` (driving
  licence) → `la borsa`. Both are cued by the same question and take the same feminine
  agreement the drill teaches, so the grammar is untouched; cigarettes are not early
  vocabulary for a child and a driving licence is not a thing a child owns.
- **Not converted:** the illustrated luggage vocabulary on printed page 128
  (`sacca da viaggio`, `borsa da viaggio`, `valigia per abiti`) is a labelled picture. The
  words are legible but the picture is what distinguishes them, and the tutor cannot show
  it. `la valigia` alone was kept, with an English gloss in place of the picture.

### `it-fast-03-taxi-and-haircut` — unit IX, printed page 197

*Going by taxi*, and a second scene at the hairdresser's.

- **Replaced:** `Ecco l'Ambasciata Americana` does not appear in the dialogue itself but
  in the unit's variants, which were not converted. The dialogue needed no change.
- **Changed:** the two `... (lire)` blanks became `Dodici euro` and `Ecco quindici euro`.
  The amounts are invented: the source leaves them blank for the instructor to fill, and
  a blank cannot be spoken.
- **Kept:** the hairdresser's scene entire. Waiting your turn somewhere busy is exactly
  the everyday situation this conversion is looking for.

### `it-fast-04-shopping-for-clothes` — unit XIII, printed page 301

*Shopping for clothes.* Sixteen turns, a shop assistant and a tailor.

- **Nothing removed for content.** This unit also screened clean.
- **Changed:** the prices. `la giacca 120.000, il completo 230.000, più le modifiche
  18.000 lire` became `120 euro`, `230 euro` and `18 euro` — the digits kept, the
  thousands dropped. Read as lire they are 1992 prices in a currency that no longer
  exists; read as euro they are plausible today. Be clear about what that costs: the
  spoken line changes, because *centoventimila* and *centoventi* are not the same word,
  and the prices are now plausible figures rather than the source's own converted.
- **Kept:** the tailor, the alterations and the fitting on Thursday. `Le prendo le
  misure` and `può ritornare giovedì` are ordinary shop Italian.

### `it-fast-05-eating-out` — unit XV, printed page 352

*Eating out I.* Ordering for a table of friends.

- **Removed:** the wine. The waiter's `Vino bianco, rosso o vino della casa?` became
  simply `E da bere cosa porto?`, the order for two bottles of red became one bottle of
  mineral water, and `la lista dei vini` came out of the waiter's line offering the menu.
  The exchange that follows — `Liscia o gassata?` — is untouched and is one of the most
  useful things in the unit. This is a robot talking to children in their homes;
  ordering wine is not a skill it should be drilling.
- **Kept:** every dish. `bucatini all'amatriciana`, `pescespada ai ferri`,
  `bocconcini di vitello`, `insalata mista` — food is the subject and the vocabulary is
  good.
- **Not converted:** the regional dish list printed across page 357 as a table of small
  type. It is reference material for a reader, not something a tutor can say.

### `it-fast-06-phone-call-about-a-flat` — unit XVII, printed page 408

*Phone call.* You ring about a flat advertised to let, and the owner describes it.

- **Changed:** the caller's name. The source prints a brace — *il signor / la signora /
  la signorina ( + last name)* — for the student to fill in. The dialogue needs a name to
  be spoken, so it is `il signor Bianchi`.
- **Kept:** everything else, including the address, the fifth floor and the windows
  looking onto a public garden. Finding somewhere to live is a family situation, and the
  room vocabulary is among the most useful in the volume.

## What did not ship, and why

Twelve of the eighteen units in Volume 1 were not converted. Dropping a unit is a
decision too, and the ones dropped for content are the whole reason this task was framed
as curation rather than ingestion.

| Unit | Title | Why not |
|---|---|---|
| Preliminary | Cultural notes | Notes on people, history, geography and government. Reading, not a dialogue. |
| I | At the airport I | Arrival formalities. Screened 18 embassy and 12 official hits. |
| II | At the airport II | The customs queue the task names. Excluded by the uniformed-authority rule. |
| III | At the exchange office | Changing money at a currency desk: 22 embassy and 20 official hits, the highest in the volume. Also an errand a child does not run. |
| V | At the hotel I | Checking in with an embassy booking. The premise is the diplomatic post itself. |
| VII | At the hotel III | Same premise, plus official vocabulary. |
| VIII | At the hotel IV | Same. Four hotel units is also more hotel than a course needs. |
| X | Getting around | Buses and the underground, but the trips are to and from official addresses. |
| XI | At the embassy I | Excluded by title. |
| XII | At the embassy II | Excluded by title. |
| XIV | At the shoe store | Nine official hits in a unit about shoes; the shopping ground is already covered by XIII. |
| XVI | Eating out II | Curatable, and a good candidate for next time. Left out to keep this first conversion to six units done properly rather than nine done quickly. |

The screen that produced those counts is a search for military, embassy and
uniformed-authority vocabulary in Italian and English across each unit's pages. **It is a
list of words, so it is a starting point and not a verdict** — every unit that shipped was
also read. The control that decides what ships is the approved-unit list in
`tests/test_converted_lessons.py`, which a person has to edit deliberately.

## Text that nobody printed

Every shipped line was checked against a page image, but "checked against the page" and
"printed on the page" are not the same claim, and the difference is worth setting out
rather than leaving for a reader to discover.

**Printed verbatim:** all six dialogues, every repetition drill except the two named
under the next heading, and the cue-response
drills taken straight from the units' printed models — the time-telling pairs in unit IV,
the `Dove metto...` pairs in unit VI.

**Recomposed from the unit's own printed sentences.** A few drill lines are formed the
way the exercise asks the learner to form them, out of words printed in the same unit,
but do not themselves appear on the page. For instance
`Preferisce una tinta unita o fantasia?` is printed, and `Preferisco una tinta unita.`
is the learner's half of that exchange. Unit IV's `meno` pairs are the same case: the
course prints Model 3 as an instruction to continue Model 2's sentences with `meno`, so
`Sono le tre meno cinque.` is the exercise being done rather than a line lifted from it.
Both ship as **repetition** drills, not cue-response answers — an earlier version of this
paragraph called them answers and quoted the second as `No, sono le tre meno cinque.`,
a string that is not in the shipped file.

**Invented outright, and this is the whole list:** the two taxi fares in unit IX
(`dodici euro`, `quindici euro`), and the caller's surname in unit XVII (`il signor
Bianchi`). In both places the source prints a blank for somebody to fill.

A mechanical cross-check was run to produce those three categories: every shipped line
was searched for in the course's own extracted text, with accents, case, punctuation and
the scan's `l`/`/` confusion folded away, and every line that did not match was looked at
individually. That check is also what caught a dish dropped by accident from the waiter's
recommendation in unit XV — `fegato di vitello alla veneta`, restored.

## What a learner can actually do with this, today

**Updated by W18.** What follows replaces this section's original text, which said the
tutor could not yet speak this material and that no tool read lesson content. Both were
true when W24 shipped and are false now; they are recorded here rather than deleted,
because the limit and the date it was lifted are both part of the account.

W18 added `get_lesson_content`, a parameter-free tool that reads the running lesson from
the pinned session, so the tutor now teaches from the converted unit's own dialogue, notes
and drills instead of improvising around its objective line. The locked profile tells it
to teach that material and not to invent vocabulary or examples alongside it. (That
"alongside" scoping is the defect D33 later fixed: a word asked for out of the blue is
alongside nothing, so the ban read as not applying. The profile's ban is unconditional
now — the sentence quoted here is what it said at the time of this curation, not what it
says today.)

`test_a_learner_is_offered_a_converted_lesson_and_can_finish_it` carries the whole claim
now rather than half of it: through the real dispatch path, a learner asking for Italian
is offered `it-fast-01-what-time-is-it`, the app pins it, the same conversation reads back
that unit's own turns, notes and drills, and finishing it records against it. The
assertion that used to state the limit — that no tool read content — is gone, having done
its job by failing the day W18 landed.

What is still **not** claimed by any test is that a model teaches *well* from this. That
is the manual session in the simulator, and it remains outstanding.

## What a reviewer should look at first

Being honest about where this is weakest:

1. **The Italian was not reviewed by a native speaker or a teacher.** It is the source's
   own Italian, checked character by character against the page images, which is a
   different claim from "a teacher approved it".
2. **The invented amounts** in unit IX and the caller's name in unit XVII are the only
   text here that nobody wrote before this conversion; a handful of drill answers are
   recomposed from the unit's own sentences, and the section above says which.
3. **The euro conversions keep the source's digits and drop its thousands.** They are
   plausible, not researched: 120 euro for a jacket is a reasonable price today, but it
   is neither the same number as 120.000 lire nor that price converted, and the spoken
   line changes with it.
4. **The usage notes are adaptations**, and adaptation is where a grammatical
   explanation can drift. Each one was written from the source's note on the same point,
   but the wording is not the source's.
