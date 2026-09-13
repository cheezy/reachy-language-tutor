# Curation log: Spanish — FSI FAST (selected), FSI Basic Volume 1 (evaluated)

> This log records rights positions for **two** courses. The selection is the **FAST**;
> Basic was evaluated first, on a false premise, and its record is kept rather than
> deleted. Where a section below says "the source" unqualified it predates the change —
> the sections that matter have been scoped by name.

What was chosen, what was checked, and what was decided, for the Spanish conversion.
Written in the order the work happens, so the sections below the rights position are
empty until the tasks that fill them run.

The method is [converting-a-course.md](converting-a-course.md). The worked example on
another language is [curation-log-italian-fast.md](curation-log-italian-fast.md); where
this log is terse, that one has the longer version of the same argument.

**Status: the source is settled on the FAST; its rights work is started, not finished.**
An earlier draft of this log chose Basic on the false ground that no Spanish FAST exists
— it does, and the error is described below. A rights position is recorded for **both**
courses. Basic's is the more thoroughly evidenced and is not withdrawn; it is superseded
as a *selection*, not as a record. The FAST's is thinner and has two named gaps that W35
must close before it screens a single unit. Nothing has been screened, read, curated or
seeded.

---

## The source (Basic — evaluated, not selected)

| | |
|---|---|
| Course | FSI Spanish Basic Course, Volume 1 — Student Text, Units 1-15 |
| Published | Foreign Service Institute, Department of State, Washington D.C., **1961** |
| First printed | 1957 (this is the second printing; the preface says so) |
| Named on the title page | Robert P. Stockwell, J. Donald Bowen, Ismael Silva-Fuenzalida |
| Pages | 700 |
| File | `Fsi-SpanishBasicCourse-Volume1-StudentText.pdf`, 19,066,937 bytes |
| SHA-256 | `4838a312cbd5cdf5103a57deb0d0bc75012ede7f79f5cb56bf829970983e18e9` |
| Fetched from | `https://fsi-language-courses-media.nyc3.cdn.digitaloceanspaces.com/languages/Spanish/Basic/Volume%201/Fsi-SpanishBasicCourse-Volume1-StudentText.pdf` |
| Scanned | 2007, Adobe Acrobat 8.13 Paper Capture Plug-in (per the PDF metadata) |
| Fetched | 2026-09-13 |

### The two candidates, and why this section replaced a wrong one

**An earlier draft of this log asserted "There is no Spanish FAST course to choose", and
that was false.** It was contradicted by the very command the log cited as its evidence.
The listing was parsed for directory prefixes only, and Spanish is the one language of
the five whose FAST is stored as a **loose object** rather than a `FAST/` directory:

```
languages/Spanish/Fsi-SpanishFamiliarizationAndShort-termTraining.pdf
```

FAST stands for **F**amiliarization **A**nd **S**hort-**T**erm training. The filename is
the course name spelled out, which is why reading only directories missed it — and why
the same bug was harmless for German, Italian and Portuguese, whose FAST courses are
directories. The error propagated into `plan.md`, where it did real damage: it replaced
a *correct* coverage list with an incorrect one while claiming to have fixed an error.
Both are now corrected, and the enumeration there parses both halves of the response.

| | Basic, Volume 1 | FAST |
|---|---|---|
| File | `Fsi-SpanishBasicCourse-Volume1-StudentText.pdf` | `Fsi-SpanishFamiliarizationAndShort-termTraining.pdf` |
| Bytes | 19,066,937 | 21,136,441 |
| SHA-256 | `4838a312…983e18e9` | `cd603ff9…1e8fd050` |
| Pages | 700 | 588 |
| Published | 1961 (2nd printing of 1957) | 1983 (title page, read as an image) |
| Author (metadata) | Foreign Service Institute | Foreign Service Institute |
| Encrypted / JavaScript | no / no | no / no |
| Rights position | **recorded below, the fuller of the two** | **recorded below, thinner, two gaps open** |

### The decision: FAST, and why

**Take the FAST, on content fit.** The rights comparison is a trade rather than a win,
and an earlier draft of this section called it "clearly better", which it is not.

**Content fit — this is the clear part.** Its table of contents is organised by CYCLE
around situations: "Getting started in country", "At the Hotel" (addressing the bellhop,
placing an outside call), "At the Restaurant" (ordering a meal and tipping), "Changing
money". That is the shape Italian FAST gave this app and the shape the lesson tables are
built for. Basic is *guided imitation* and pattern drills designed for six hours of
daily class under a supervising linguist — convertible, but further from what a robot in
a house can run.

**Rights — a trade, stated in both directions.** All quotations below are from the text
layer, reconstructed: the layer renders "Romance **Ianguages**", "**Ma.ny nembers** of
FSI' s staff", "Language **Iaboretory**", and this log corrects them here rather than
printing them clean and calling them quotations.

*Better than Basic, on one rule applied to both:* the FAST places **one of its four
title-page authors** inside FSI by job title — Vicente Arbeláez, "Spanish Section Acting
Head in the Department of Romance Languages at the Foreign Service Institute (FSI)".
Basic places **none of its three title-page linguists**, and they are its open question.

That is the whole of the credit, and an earlier draft of this line claimed more: it said
Basic "states no individual's employment at all", which is false and is refuted by this
same log further down — Basic's preface names five people inside FSI's Spanish staff
(Ulsh, Beym, Rauscher, Segreda, Montero). Both courses carry a named FSI-staff roster,
so a roster earns neither a point over the other; the FAST's Stephen Zappala appears in
one just as Basic's five do. (Zappala is also "Chairman of the Department of Romance
Languages" — attached to FSI by the preceding sentence about Arbeláez rather than by its
own words.) Scoring the same evidence as a credit for the chosen course and as nothing
for the rejected one is how a record turns into advocacy.

*Worse than Basic, and it is the same kind of question:* three of the four title-page
authors — Lily Bean, C. Cleland Harris, Marisa Kenney-López — are covered only by the
blanket "Many members of FSI's staff contributed to this effort", which is the same
blanket-to-individual inference the Basic section below refuses to make for its three
linguists. And the title page names **Leonor Paine** as a Principal Consultant; the
preface gives her the title "principal consultant" and the role "field testing both the
original and the revised versions", and **does not include her in the FSI-staff roster
it writes out by name**. An earlier draft of this section said the preface "places both
inside FSI"; the page places Zappala inside it and leaves Paine out of the one list that
would have settled it. The page gives her **two** roles, not one: field testing on its
own would not be authorship, but "principal consultant" comes with no described scope of
contribution at all, so nothing on the page bounds it. An earlier draft said the
exposure was bounded; it is not — and
the question is open and belongs beside Basic's.

**What was checked on the FAST:**

- **Integrity, before parsing**: content-type `application/pdf`, magic bytes `%PDF-1.6`,
  21,136,441 bytes on disk matching `content-length`, `pdfinfo` Author "Foreign Service
  Institute", Title "FSI Spanish Familiarization and Short-Term Training", 588 pages,
  not encrypted, no JavaScript. SHA-256 `cd603ff9aa799acce2296c1bff1a0e36bc0816750cbad4cd7f5236881e8fd050`.
- **Notice search, both tiers**: the method's six literals return **zero** hits, and the
  damage-tolerant matcher returns **zero** for `copyright` (`reserved` twice, `licens`
  and `contract` in ordinary course content). **Do not read that as a better result than
  Basic's.** Basic's two hits were the ordinary word "reproduce", so the difference
  carries no rights signal. More importantly the instrument is *weaker* here, measured:
  the FAST's text layer is **49.3% crude / 47.9% refined** tilde damage against Basic's
  38.9% / 35.6% — about a quarter worse. And its damage modes include inserted spaces
  and inserted punctuation inside words ("Ma.ny nembers of FSI' s staff"), which is
  exactly what this log records the matcher as missing. The confusion set behind that
  matcher was calibrated on Basic's scan and was never re-derived for this one.
- **The title page read as an image**: SPANISH / Familiarization and Short-Term Training
  / the four named authors / Principal Consultants / the FSI seal / FOREIGN SERVICE
  INSTITUTE / U.S. DEPARTMENT OF STATE / 1983. No notice.

**What was NOT checked on the FAST, and it is less than Basic got:**

- **Only the title page was read as an image.** 587 of 588 pages were not, and unlike
  Basic no verso or final leaf was examined — the final leaf is a map of Spanish-speaking
  countries, from the text layer, so the conventional back-page notice slot is not the
  same here, but it was not looked at.
- **The preface authorship passage was read from the TEXT LAYER, not the page image.**
  Given what this log records about text layers, that is corroboration and not the
  standard the Basic section was eventually held to.
- The same open question in its milder form: "Principal Consultants" is a word that
  invites the contractor question, and the preface answers it by placement rather than
  by stating employment.

**So: the selection is settled on FAST and the rights work on it is started, not
finished.** W35 must close the two gaps above — verso and final leaf as images, and the
authorship passage re-quoted from the page — before it screens a single unit. That is a
smaller job than it sounds and it is named here so it cannot be skipped.

The alternatives the method excludes by decision remain excluded: the three Headstarts.
Programmatic and Secretarias were not examined.

**What Basic would have cost, had it been the one chosen.** (An earlier draft presented this as
the cost of an unavoidable choice; it is not unavoidable.)
Italian FAST is a 1992 course built around situational dialogues. Spanish Basic is a
1957/1961 course built around *guided imitation* and pattern drills (the text layer
renders the heading `GUIIED IMITATION`; the correction is this log's, not the page's —
page 0.3 was rendered but never read), and its preface is
explicit that it is designed to be taught by a native speaker under the supervision of a
linguist, with six hours of class drill a day. The drill structure should convert well —
that is the part this app can actually run. The situations may be thinner on the ground
than they were in FAST, and the register is thirty years older. W35 will find out.

---

## Rights: what was actually checked

**Read this section knowing what the instrument is.** The evidence below is an
exact-string search over an OCR text layer that this same log measures as badly damaged
(39% of accent pairs destroyed, and see the worked example three bullets down), plus
three pages read as images. Those are different strengths of evidence and the bullets
say which is which.

**Checked, in the file itself:**

- **No copyright notice was found. Here is exactly how far that reaches, measured.**
  Three searches were run over the text layer, and the third is the only one worth
  weight. (i) The method's six literals — `copyright`, `©`, `all rights`,
  `public domain`, `contractor`, `reproduc`: two hits, both the ordinary word
  "reproduce". (ii) Short fragments — `opyri`, `opyr`, `erved`, `ublic.{0,2}domain`,
  `ontract`, `icens`, `permis`, literal `(c)`: every hit read in context, all ordinary
  course content (Spanish grammatical *contractions* `al`/`del`, "Oh. All right." in a
  dialogue, "reserved for drilling", `(c)` twice as a drill label). **That second set is
  weaker than it looks** — `opyri` contains `opyr`, so they are one anchor and not two.
  (iii) A matcher modelling the confusions this scan actually makes (`o`→`0`, `y`→`v`,
  `r`→`n`, `i`→`l`, `t`→`f` …) and allowing any one character to be *deleted*, run for
  `copyright`, `reserved`, `licens`, `contract`. **Zero matches for `copyright`.**

  What (iii) does and does not cover, measured against `Copyright 1961 by …` rather
  than asserted: it catches every one of the nine single-character **deletions**, and
  catches `C0pyright`, `Copvright`, `Copyrlght`, `Copyrigbt`, `Copright`, `COPYRIGHT`.
  It **misses** an inserted space (`Cop yright`), a bare `(c) 1961`, and 229 of 315
  arbitrary single-character substitutions — because it models plausible OCR confusions,
  not every possible corruption. Deletion is the damage mode this document actually
  exhibits ("Schoo of anpua~es wo d"), which is why (iii) is worth running; it is not a
  guarantee, and one badly-placed substitution still defeats it.

  **So the greps are corroboration, not the load-bearing evidence. The page images are.**
- **Three pages were READ as images**, because a notice set as an image is invisible to
  every search above: the title page, its verso — where a 1961 notice would
  conventionally sit, and which carries only "For sale by the Superintendent of
  Documents, U.S. Government Printing Office / Washington 25, D.C. - Price per set of 2
  Volumes, $7.50" (the slash is a line break on the page) — and the final leaf, which is index matter closing with the GPO
  imprint. **Two preface pages were read as well** (0.1 and 0.2), because the rights
  position quotes them and a quotation belongs to the page rather than to the OCR of it.
  Nine pages were *rendered* — the printed cover, the title page, its verso, five
  front-matter pages and the final leaf — and **five were read**: title page, verso,
  preface 0.1, preface 0.2, final leaf. **6 read of 700 (page 11 as well, see below); see "Not checked".**
- **The title page reads**: Great Seal of the United States, "FOREIGN SERVICE
  INSTITUTE", "WASHINGTON, D.C.", "1961", "DEPARTMENT OF STATE", with Robert P.
  Stockwell, J. Donald Bowen and Ismael Silva-Fuenzalida named above it.
- **The preface says the work was prepared in-house.** Read from the page image
  (preface p. 0.2): "Foreign Service Institute - Spanish Basic Course was originally
  prepared by the Spanish staff of the Foreign Service Institute under the supervision
  of the linguists whose names appear on the title page. In addition the following
  members of the Spanish staff have made special contributions to the book:" — then
  Linguistic staff: Jack L. Ulsh, Richard Beym, Dorothy Rauscher; Instructional staff:
  Guillermo Segreda, Hugo Montero U. The same page says the manual "has been prepared
  and reviewed by members of fifteen different Spanish-speaking countries".

  This is the load-bearing sentence of the whole rights position, so it is quoted from
  the page and not from the text layer, which renders it "Spanish **starf**". An
  earlier draft of this log printed the corrected form silently, as though the text
  layer had said "staff"; that is the same fault as the two below, and it is why every
  quotation in this section now names its source.

- **The document metadata names the Foreign Service Institute as Author**, with Title
  "FSI - Spanish Basic Course - Volume 1 - Student Text" matching the printed title page.
- **The last page carries a GPO imprint.** Read from the page image: "U. S. GOVERNMENT
  PRINTING OFFICE : 1961 O - 596435" — a letter **O** before the number, matching the O
  in OFFICE. The text layer renders the same line "Il' U. S. GOVERNMENT PRINTING
  OFF**l**CE: 1961 **0**- 596435", with a lowercase L in OFFICE and a digit zero. An
  earlier draft quoted a hybrid of the two, which matched neither source.

- **This is the second printing of a 1957 text, and the page says so plainly.** Read
  from the page image (preface p. 0.1): "Foreign Service Institute - Spanish Basic
  Course was first printed in 1957. The Foreign Service Institute School of Languages
  would have preferred to revise the text for this second printing, but the requirements
  of day to day training have forced a postponement of the task." So it announces itself
  as an *unrevised reprint of the original* — the opposite of the repackaged edition
  pitfall 1 warns about, which is a derivative work carrying its own copyright. No
  publisher, press or company name appears other than FSI and the GPO.

  **The text layer of that same sentence is why the searches here were widened.** It
  renders as "The Foreign Service Institute Schoo of anpua~es wo d have preferred to
  revise the text for this second printin**r**" — so a grep for `second printing`
  returns nothing, and an earlier draft of this log offered that zero as evidence the
  scan announced no printing history. The page says the opposite. A zero from this text
  layer is not evidence of absence, and this is the measured proof of it.

- **The file is what it claims to be, checked BEFORE anything was parsed**:
  `content-type: application/pdf`, magic bytes `%PDF-1.6`, 19,066,937 bytes on disk
  exactly matching the `content-length` from a `HEAD` issued before the download, not
  encrypted, no JavaScript. Only `pdftotext`, `pdfinfo` and `pdftoppm` were ever run
  against it. The ordering matters as much as the result: these ran first, and the first
  `pdftotext` came after.

**Not checked, and worth saying plainly:**

- **No legal opinion was obtained, and none of the above is one.**
- **Whether the three linguists named on the title page were FSI employees or outside
  contractors was not established.** This is the exception the method exists to catch —
  some FSI courses were produced under contract — and it is the one question this log
  cannot answer from the document. What the document does say is that the *text* was
  "prepared by the Spanish staff of the Foreign Service Institute" (page image, preface
  p. 0.2) and that the named
  linguists *supervised*; whether a supervisor's contract assigned or reserved anything
  is not visible here. Nothing found suggests a contract; nothing found rules one out.
- **The absence of a copyright notice on a 1961 work is suggestive but was not relied
  on as a conclusion.** It is recorded because it is a fact about the page, not because
  a rule was applied to it.
- **The Center for Applied Linguistics** is named in the preface as the source of the
  accompanying tape recordings, which are explicitly *not* available from the GPO or FSI.
  That is a third party attached to the **audio**, which this project is not ingesting.
  No tape is used here, and none should be without settling that separately.
- **694 of the 700 pages were never read as images** (ten rendered, six read).
- **Whether an acknowledgements section exists was not established.** Pages 1-8 of the
  front matter were rendered and none is one; the contributor credits that would sit in
  such a section are in the preface, quoted above. But the front matter runs past page 8:
  9 and 10 continue the Introduction and 12 is the Table of Contents, all three
  identified from the text layer's running heads rather than read. Page 11 yields zero
  characters of text layer, which by this log's own argument is not evidence the page is
  empty — so it was rendered and looked at, and it is genuinely blank. The text layer returns zero for `acknowledg`, which by this
  log's own argument is corroboration and not evidence. An earlier draft stated the
  absence as a checked fact; it is a probable absence over an unexamined range. A copyright notice set as an
  image — on a scanned insert, a stamped library leaf, a plate — would be invisible to
  every search run here, and the six pages read were the title page, its verso, the two
  preface pages the rights position quotes, the final leaf and page 11. Four of those six
  were examined *for a notice* — the three conventional locations plus page 11, rendered
  precisely because a blank text layer is not evidence of a blank page. The
  unconventional locations are not covered at all.
- **The text layer is materially damaged and no search run here is reliable against it.**
  Measured, not estimated: the confusion-modelling search survives every single-character
  deletion but still misses an inserted space, a bare `(c)`, and 229 of 315 arbitrary
  single-character substitutions. **One badly-placed character is enough to hide a
  notice from every search in this log.** The `second printinr` bullet above is a
  measured instance of exactly that, found in this very document. Treat the text-layer
  result as corroboration of the image reads, never as independent evidence.
- **The page count was not cross-checked against anything.** 700 comes from `pdfinfo`
  alone; no catalogue entry or printed pagination was compared against it, so it
  confirms the file is intact but not that it is complete.
- **No archival copy was taken.** The origin is a mutable third-party bucket with no
  object versioning. The URL, byte count and SHA-256 above let somebody confirm they
  have the same file; if the object is replaced or removed, nothing here preserves the
  bytes this rights position was formed about.
- **Volumes 2-4 were not examined.** Only Volume 1 was fetched.

---

## What each scan will cost to read, measured

Both courses' text layers were measured with the same command. **The chosen course is
the more damaged one**, which is the opposite of what the selection might suggest and is
why this section names them rather than saying "the source".

| | accented survived | bare `~` | crude | refined |
|---|---|---|---|---|
| Basic (700 pp) | 14,743 | 9,389 | 38.9% | 35.6% |
| **FAST (588 pp)** | 6,881 | 6,704 | **49.3%** | **47.9%** |

"Refined" excludes tildes with no adjacent letter, which cannot be a stripped accent.
The section below was written about Basic and its argument applies with more force to
the FAST.

**The accent damage is worse than Italian's, and it lands somewhere worse.** Counted
over the whole 700-page text layer: **14,743 accented characters survived and 9,389 bare
`~` remain — 39% of that pair are tildes.** For comparison, `plan.md` §9 measured 19%
for Italian FAST and 38% for Italian Headstart, the course rejected partly for that
reason — **but those are not the same measurement.** Italian's figures come from samples
of 412 and 151 characters; this one is a census of all 24,132 across 700 pages. And this
metric counts only tilde-shaped damage, so an accent that vanished without leaving a `~`
is not in the 39% at all. **And the numerator over-counts as well**: 1,234 of the 9,389
tildes have no adjacent letter on either side, so they are free-floating scan marks
rather than accents stripped off a letter — excluding them gives 8,155/22,898 = 35.6%.
The error therefore runs in both directions and 39% is an estimate, not a bound. An
earlier draft called it a floor; that was not established. The operational conclusion
below is unaffected either way.

Spanish is the language where this matters most, and not only because of the count:

- `ñ` is a letter, not an accent on `n`. `año` and `ano` are different words, and the
  second one is not something a robot should say to a child.
- A stray `~` next to an `n` is the OCR having eaten exactly that letter.
- `sí`/`si`, `él`/`el`, `tú`/`tu`, `más`/`mas` all turn on a single acute accent.

**Consequence for W36: the text layer cannot be bulk-imported, and a spot check will not
be enough.** Every accented word in every shipped line has to be confirmed against the
page image, and the tilde count above says roughly two in five of them will be wrong.
Budget accordingly, or consider re-OCRing at higher quality with an accent-aware engine
before starting — that option was not tried here.

---

## What was decided about units

Nothing yet — W35 screens the volume. Scale, for planning only: the **FAST** is
organised into **Cycles** rather than units, and its contents run to 38 of them. (Basic,
not chosen, says "UNITS 1-15" on its title page.) The method's expectation is that
roughly a third survive curation, so budget for the screening being the larger job here
than it was for Italian's 18.

## Conventions applied to every unit

Nothing yet — W36.

## What shipped, unit by unit

Nothing yet — W36 and W37.

## What did not ship, and why

Nothing yet — W35 records the rejections.

## What a learner can actually do with this, today

Nothing. No Spanish lesson in the catalog carries content; a Spanish learner is still
offered the six title-and-objective placeholders. W38 closes this section.

## What a reviewer should look at first

Two things, in this order.

**Leonor Paine, on the chosen course.** The FAST's title page names her a Principal
Consultant; its preface gives her a title and a role and leaves her out of the
FSI-staff roster it writes by name. That is the open contractor question on the course
actually selected, and an earlier draft of this log closed it by inference. Beside it,
three of the four title-page authors are covered only by a blanket "many members of
FSI's staff". **Basic's three linguists** (Stockwell, Bowen, Silva-Fuenzalida) are the
same question on the course not chosen, and that bullet stands as recorded.

**How far the notice search actually reaches.** The headline is not "no notice exists"
but "no notice was found by a search designed to tolerate a damaged OCR layer, plus
three pages read as images". An earlier draft of this log did state the stronger claim,
and review caught it — along with a bullet that presented an OCR false negative
(`second printinr`) as affirmative evidence, while the source table two sections above
recorded the opposite. Both are corrected, and the episode is left in the log on purpose:
it is the clearest available argument for why a zero from this text layer proves nothing
on its own.
