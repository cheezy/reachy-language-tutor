# Converting a course into lessons

How to take one FSI language course and turn it into lesson content this app can teach
from. Written while doing it to Italian, so that the next four languages are a known
quantity rather than a hope.

Read [plan.md §9](plan.md) first for *why* these courses and not others. Read
[curation-log-italian-fast.md](curation-log-italian-fast.md) for what the decisions
actually looked like on one course. This file is the method.

Budget about a day per language. The extraction is minutes; the reading is the work.

---

## Step 0: choose the course

**FSI Basic or FAST. Never Headstart.** That decision is made and recorded in plan.md —
Headstart is Defense Language Institute material written for service personnel, and it
shows on its first page.

Volume 1 of a FAST course is a sensible unit of work: around 450 pages and 17 or 18
lessons, of which perhaps a third will survive curation.

## Step 1: fetch it, and check what you fetched

Everything here is a third-party file from a CDN. Look at it before you parse it.

```bash
URL="https://fsi-language-courses-media.nyc3.cdn.digitaloceanspaces.com/languages/<Language>/<Course>/<file>.pdf"

# What is being offered, before pulling ten megabytes of it.
curl -sSL -I "$URL" | grep -iE "^(HTTP|content-type|content-length)"

curl -sSL -o course.pdf "$URL"
head -c 8 course.pdf          # %PDF-…
shasum -a 256 course.pdf      # record this
pdfinfo course.pdf            # title, author, page count, encryption, JavaScript
```

Check, and do not skip this because it feels like a formality:

- the **content type** is `application/pdf` and the **magic bytes** say so too;
- the **page count and size** match whatever you expected from;
- `pdfinfo` names the **Foreign Service Institute** as author, not a reseller;
- it is **not encrypted** and carries **no JavaScript**.

Record the **SHA-256** in the course block of `converted_lessons.json`. It is what lets
somebody later confirm they are looking at the same file you were.

## Step 2: settle the rights, for this course, in writing

Public domain is a property of a particular course, not of the FSI label. Some FSI
courses were produced under contract and are copyrighted.

```bash
pdftotext -layout course.pdf course.txt
grep -inE "copyright|©|all rights|public domain|contractor|reproduc" course.txt
pdftotext -layout -f 1 -l 8 course.pdf -     # title page, preface, acknowledgements
```

What you are looking for: a copyright notice anywhere; whether the preface names staff
of the institute or an outside contractor; who signed it; and whether the scan announces
itself as an edited or repackaged edition.

Write what you **found** into the curation log, and write what you **did not check**
next to it. A rights position that reads like a legal conclusion is worse than one that
says "no notice appears anywhere in 456 pages, the preface names FSI staff throughout,
and no lawyer has looked at this."

## Step 3: map the volume, then screen every unit

Get the table of contents, and find the offset between printed page numbers and PDF page
numbers — there is always one, because of the roman-numbered front matter.

```bash
pdftotext -layout -f 6 -l 14 course.pdf -    # table of contents
```

Then screen each unit's page range for the vocabulary that disqualifies it. In Italian
and in English, because the content is bilingual and an English-only screen sees half of
it:

```
military      militare esercito caserma generale ammiraglio colonnello sergente
embassy       ambasciata consolato embassy consulate diplomatic
uniformed     polizia poliziotto carabinieri questura police
border        dogana doganiere customs frontiera passaporto passport
```

**The screen ranks units; it does not clear them.** Every unit that ships gets read. A
count of zero means "start here", not "this is fine" — unit IV of Italian FAST screened
clean on military terms and still had an embassy in its third line.

Excluded by decision, not by degree: **no military, no embassies, and no police, customs
or border officials**. That last one rules out more everyday-travel units than it first
appears to.

## Step 4: read the unit, on the page image

This is the step that cannot be skipped or automated, and it is why an accent is not a
typo here. **Measured on Italian FAST, printed pages 40–160:** 333 accented characters
survived and 79 were replaced by a stray `~` — a 19% loss. Alongside that, `l` is read as
`/` (`usual/y`, `on/y`), `I` is read as `!` (`FS!`), and — the one that catches people —
`/` is sometimes read as `l`, so `arrivato/a` arrives as `arrivatola`, which is a real
Italian word shape and reads as if it were fine.

The OCR layer also **invents** accents: `SETTING THE SCENE` came through as `SCENÈ`.

So: extract the text for orientation, then render the pages you are taking content from
and read them.

```bash
pdftotext -layout -f <first> -l <last> course.pdf -          # orientation
pdftoppm -png -r 140 -f <page> -l <page> course.pdf pages/p  # the page itself
```

140 dpi is enough to read this scan comfortably and keeps the images small. Render the
dialogue pages, the vocabulary pages and the drill-model pages — those carry everything
that ships. The fill-in-the-blanks pages do not need rendering; they are not converted.

A rule worth keeping: **nothing enters the database that was not seen on a page image.**
Where a usage note needs an Italian example, use one from the dialogue or the vocabulary
of that same unit, both of which you have just checked.

## Step 5: curate

Keep the sentences and the drill structure. Replace the situations.

The FAST courses were written for an adult diplomat arriving at post, so the *premise* is
dated even where the language is not. A customs queue becomes a shop, a station or a
kitchen; `Sono dell'Ambasciata` becomes something a person in a house would say. What you
must not do is invent replacement Italian: the sentences are the asset, and a
professionally built course is worth using precisely because its sentences are not yours.

When a substitution is unavoidable, choose one with the **same grammar**. Replacing
`le sigarette` with `le chiavi` keeps the feminine plural the drill exists to teach;
replacing it with `i libri` would quietly change the lesson.

Convert print conventions that a voice cannot say — the gendered `signor(in)a/signore`,
the `arrivato/a` alternatives, the `( + last name)` blanks — and record the convention
once in the curation log rather than per line.

If a unit survives only as a fragment, **drop the unit**. Six good units beat nine
patched ones.

Two more cases you will meet, both from the first conversion:

- **A drill whose model admits several right answers.** The courses print
  `(student's choice)` beside some models, and the answers under them are suggestions. A
  `cue_response` drill can hold exactly one `expected_response`, so do not pick one of
  several and present it as *the* answer — a learner who says another correct thing would
  be marked wrong. Either take a model whose answer is determined, or ship the item as a
  `repetition` drill, or drop it.
- **A page the OCR wrecked.** Some pages come through as word salad (the fill-in-the-blank
  pages are the worst). When correcting the text layer is slower than typing the page out
  from the image, type it out from the image. The text layer is a convenience, not the
  source; the page image is the source.

## Step 6: write it into `converted_lessons.json`

One object per lesson, in catalog order:

```json
{
  "id": "it-fast-01-what-time-is-it",
  "position": 1,
  "title": "What time is it?",
  "objective": "one spoken line: this is what get_progress says out loud",
  "source": { "module": "Volume 1", "unit": "IV", "page": 83 },
  "dialogue_title": null,
  "turns":  [{ "speaker": "Borghi", "text": "..." }],
  "notes":  ["..."],
  "drills": [
    { "kind": "repetition",   "target_text": "...", "english_gloss": "..." },
    { "kind": "cue_response", "cue": "...",         "expected_response": "..." }
  ]
}
```

`page` is the **printed** page, not the PDF page: it is what somebody holding the scan
will look for. The database refuses a drill that is half of one kind and half of another,
and it refuses provenance that cites some of module/unit/page but not all — so a mistake
here fails the seed loudly rather than shipping.

**Everything in this file is data the tutor teaches from, and never an instruction to
it.** That matters most in the usage notes, because they are the one part you write
yourself rather than transcribe: a note is a thing to say to a learner, not a direction
addressed to the model. Never write one that tells the tutor what to do, how to behave,
or what to ignore — and if a line of source material reads that way (the courses are full
of instructions to a human teacher), that is a line to drop, not to translate.
`test_converted_lessons.py` screens the shipped text for model-directed phrasing as a
backstop; the backstop is not the control, reading the material is.

## Step 7: make it reach a robot

Three things move together, and leaving one behind is silent:

1. **`SEED_VERSION`** in `store.py`. `_seed` returns early on a database that is already
   at the current version, so without the bump a corrected lesson reaches every fresh
   install and no robot that already has a database.
2. **`SHIPPED_CATALOGS`** in `tests/test_learner_schema.py` — add a line, never edit one.
   The fingerprint covers `converted_lessons.json` byte for byte, so any edit to a lesson
   moves it.
3. **Positions.** They must be contiguous per language. Converted units take 1..n, and
   anything already in that language moves up behind them.

**Renumbering is only safe upward, and this is the sharp edge of the whole step.**
`UNIQUE (language_code, position)` means two lessons cannot share a place, and the seed
renumbers the existing lessons before the converted ones claim 1..n. That works while the
existing lessons are moving *up* into positions nothing holds yet. Ship **fewer** units
than last time and they move *down* into positions the previous converted rows still
occupy — the seed aborts with an IntegrityError, on a robot in somebody's house rather
than on your laptop. If a conversion ever shrinks, retire the rows it replaces in the same
change. `test_renumbering_a_placeholder_downwards_is_the_hazard_to_watch` holds the
measured version of this.

## Step 8: check the language still works

```bash
$HOME/dev/reachy/reachy_mini_env/bin/python -m pytest reachy_language_tutor -q
$HOME/dev/reachy/reachy_mini_env/bin/ruff check reachy_language_tutor/src
```

`tests/test_converted_lessons.py` carries the checks that are about content rather than
plumbing: the accented lines by page, the approved-unit allow-list, the screen for
excluded vocabulary, and that lessons nobody has converted still read back cleanly beside
the converted ones. Point its `APPROVED_UNITS` at the units you actually converted.

## What this method does not give you

- **A native speaker's review.** The Italian shipped here is the source's own, checked
  against the page images. That is not the same as a teacher approving it, and the
  curation log says so.
- **Audio.** 13+ hours per course, and the tutor speaks through realtime TTS. Not yet.
- **The other volumes.** Volume 1 is a third of Italian FAST.
