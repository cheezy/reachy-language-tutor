# Curation log: FSI Metropolitan French FAST, Student Text

What was taken from this course, what was changed on the way, and what was left behind.

A judgement nobody can review is not a judgement. This file exists so that a person can
read a line a robot says in somebody's house, find the page it came from, and see what
was done to it in between. Every claim here was checked in the session that made the
change; where something was **not** checked, it says so.

The method these decisions were applied with — the commands, the page-image check, the
order of the steps — is in [converting-a-course.md](converting-a-course.md). This file is
the record of the decisions themselves.

**Status: Steps 0 to 6 of the method are done — the course is chosen, fetched and
checked, the rights position is written, all 40 lessons are screened and accounted for,
six were shortlisted, and five of those six are converted and shipped.** Unit 12 was
dropped during conversion; the reason is below, under "What did not ship, and why".

## The source

| | |
|---|---|
| Course | *Metropolitan French — Familiarization & Short-term Training* |
| Publisher | Foreign Service Institute, U.S. Department of State |
| Authors (title page) | Marie-Charlotte Iszkowski, with the editorial assistance of Hedy A. St. Denis |
| Year | 1984 (printed on the title page) |
| File | `Fsi-MetropolitanFrenchFast-StudentText.pdf` |
| Fetched from | `https://fsi-language-courses-media.nyc3.cdn.digitaloceanspaces.com/languages/French/Metropolitan%20FAST/Fsi-MetropolitanFrenchFast-StudentText.pdf` |
| Fetched | 2026-09-17 |
| Size | 27,543,145 bytes, 625 pages |
| SHA-256 | `0068620b78cdf6aebce53aa8008aea0050b31175ca4b76d2fedca97eea343ddf` |

**There is no volume division.** Unlike Italian and Portuguese FAST, which are split into
volumes, each French FAST course is published as a single Student Text. "Volume 1" would
have been an invention, so the table does not name one.

**The integrity check, item by item.** A `HEAD` was issued before anything was downloaded
and returned `HTTP/2 200`, `content-type: application/pdf`, `content-length: 27543145`,
`last-modified: Wed, 28 Jul 2021 22:27:43 GMT` and
`etag: "824c077a3e438db8c42d341d775a6f48"`. The file was then fetched and came to
27,543,145 bytes on disk. Its first eight bytes are `%PDF-1.6` (`255044462d312e36`), and
`file --mime-type` agrees with the header. `pdfinfo` reports
`Author: Foreign Service Institute` — not a reseller — with
`Title: FSI - Metropolitan French FAST - Student Text`, `Subject: French Language`,
`Pages: 625`, `Encrypted: no`, `JavaScript: no`, `Tagged: no`, `Suspects: no`,
`Form: none`, PDF version 1.6. The producer string is
`Adobe Acrobat 8.12 Paper Capture Plug-in`, which is the OCR step that produced the text
layer; `CreationDate` is 24 May 2008 and `ModDate` 1 September 2008, both of which are
dates of the scan, not of the course.

**The listing, the header and the bytes agree three ways.** The bucket listing for this
key reports `Size 27543145`, the same ETag, and `LastModified 2021-07-28T22:27:43.238Z`,
matching the `HEAD` and the file on disk. All of the above ran **before** the first
`pdftotext`; nothing was parsed until the magic bytes and `JavaScript: no` had been seen.

**On the page count, what was available to check against.** 625 pages is well above the
"around 450 pages and 17 or 18 lessons" that Step 0 of the method offers as the shape of
a FAST volume — but that figure describes *a volume* of a volume-split course, and this
course is not split. The table of contents lists **40 lessons**, and the CDN directory
carries 40 accompanying audio files, which is the only corroboration available: `plan.md`'s
coverage table records directory names, not page counts, so **the page count is recorded,
not corroborated**.

## The choice: Metropolitan French, and why not the alternatives

`plan.md` records that French has two FAST courses and that nothing had yet decided
between them, leaving that decision to this task. It was decided on the front matter of
both, read as page images, after fetching and integrity-checking both files.

**The two candidates, both examined:**

| | Metropolitan FAST | Sub-Saharan FAST |
|---|---|---|
| File | `Fsi-MetropolitanFrenchFast-StudentText.pdf` | `Fsi-Sub-saharanFrenchFast-StudentText.pdf` |
| Size | 27,543,145 bytes, 625 pages | 21,663,614 bytes, 529 pages |
| SHA-256 | `0068620b78cdf6aebce53aa8008aea0050b31175ca4b76d2fedca97eea343ddf` | `a6227040ecf6cb6c42ac7d40aed5e091fcb5badeba6752cfd60b3035681ba3df` |
| Title page | 1984, by Marie-Charlotte Iszkowski | 1983, by Sanda Huffman, Earl W. Stevick, Aristide Pereira, Francis Toffa |

**Metropolitan was chosen because of who the course is for.** Its introduction describes
drawing on "the expertise of our native-speaking French staff, and also on that of others
who have lived and worked in France, Belgium, and Switzerland". Sub-Saharan's introduction
describes "our francophone African staff" and revisions based on comments "at francophone
posts in Africa". Both are perfectly good French; they are aimed at different places.

The app already ships a French syllabus — `fr-01-greetings` through `fr-06-daily-routine`
in `docs/learner-database.md` and in `learners/store.py` — and it is European-register
French: ordering in a café and asking for `l'addition`, asking the way and understanding
`tout droit, à gauche, à droite`. Metropolitan French's lesson list matches that closely:
its lessons include taking the métro and buying tickets, meeting a friend in a café, the
train station, and shopping at the bakery, delicatessen, greengrocer and butcher.
Choosing the course whose setting the existing lessons already assume means curation
will not have to quietly relocate every dialogue.

That is a fit argument, not a quality argument, and the other course is not being
criticised. If the app's syllabus were ever aimed at francophone Africa, Sub-Saharan FAST
would be the better source and this decision should be revisited.

**Why not the other French material the bucket carries** (all six directories were listed
before choosing):

- **Headstart For Belgium** — excluded by the decision recorded in `plan.md`: Headstart is
  Defense Language Institute material written for service personnel. That decision was
  made once, with reasons, and this task did not reopen it.
- **Basic (Revised), volumes 1–2** — permitted by Step 0, and not rejected on quality.
  FAST was preferred because this app teaches short spoken exchanges to beginners, which
  is what a Familiarization and Short-Term Training course is built out of. Spanish
  reached the same conclusion by a longer road: `curation-log-spanish.md` is titled
  "FSI FAST (selected), FSI Basic Volume 1 (evaluated)" and records that Basic was
  evaluated first, on a false premise, before the selection settled on the FAST. That
  is a precedent for preferring FAST, not a rule — the choice is still made per course.
- **French Phonology** (student text and instructor's manual) — a pronunciation manual,
  not a lesson course.
- **Bridges** and **Le Monde Francophone** — not examined beyond their presence in the
  listing. They were not needed once a FAST course was in hand, and no claim is made here
  about what they contain.

## Rights: what was actually checked

**Checked, in the file itself:**

- **No copyright notice was found by any search or page read performed here.** That is a
  statement about what was done, not about all 625 pages; the measured limits further
  down say how much of the book those searches can actually speak for. The literal search
  over the extracted text returned 4 hits in total, and every one was read in its page
  context. Per term: `copyright` 0, `all rights` 0, `public domain` 0, `contractor` 0,
  `©` 1, `reproduc` 3. The three `reproduc` hits are ordinary course content — the
  teacher's introduction explaining that FAST activities "don't require them to reproduce
  it", a note on Manet "reproducing it in attitudes, shapes, and colors", and a culture
  note calling the Hôtel de Ville "an elegant reproduction" of its predecessor.
- **The single `©` is not a notice.** It sits on PDF page 202 as the OCR fragment
  `©_ .. SNl:f`. That page was rendered and read as an image: it is the first page of
  Lesson 12, and the fragment is the OCR mis-reading the RER/RATP line roundels printed
  on a Paris métro map. There is no copyright notice on the page.
- **A damage-tolerant search was run as well, because a literal is only as good as the
  scan.** It models the confusions this kind of scan makes (o/0, y/v, r/n, i/l, t/f, e/c,
  h/b, s/5, u/n, a/o, g/9, q/g) at every position and additionally allows any one
  character to be deleted. Over the extracted text it returned **zero** line-hits for
  `copyright`, `contractor`, `printing`, `reprint`, `reissu` and `superintendent`. It
  returned hits for `contract` (16), `reserved` (4), `licens` (11), `rights` (73),
  `permission` (1) and `edition` (26); those were sampled and are the matcher reaching
  ordinary words — `contact` and `contraction`, reserved seats on a train, driving
  licences and license plates, "right" as in correct, and `editorial`/`edition` in the
  introduction's own account of its revision.
- **The matcher was calibrated in this session, and its limits measured rather than
  assumed.** Against the control string `Copyright 1961 by the Superintendent of
  Documents. All rights reserved.` it caught **all 9** single-character deletions of
  `Copyright`, and caught `C0pyright`, `Copvright`, `Copyrlght`, `Copyrigbt`, `Copright`
  and `COPYRIGHT`. It **missed** `Cop yright` (an inserted space) and `(c) 1961`. Of the
  315 arbitrary single-character substitutions in `Copyright`, it **missed 233 and caught
  82**. So a badly-placed single character would still hide a notice from every search in
  this log.
- **Because of that, the greps are corroboration and the page images are the evidence.**
  Eleven pages were rendered at 110 dpi and read, one by one: the printed cover (p. 1),
  the title page (p. 2), **the title page verso (p. 3), which carries a running head and
  the folio `ii` and is otherwise blank** — the conventional location for a copyright
  notice, and there is nothing on it — the introduction (p. 4), pp. 5 to 8, which are the
  blank folio `iv` and the three pages of the table of contents, the métro-map page
  (p. 202), and the final two leaves (pp. 624 and 625), **both blank**. The front-matter
  range is the `-f 1 -l 8` the method gives literally.
- **Every page whose text layer is empty was read as an image.** Exactly 2 of the 625
  pages extract no text at all — pages 624 and 625 — and both were rendered and read.
- **The pages that are almost all picture were hunted out and read too, because a text
  search cannot reach them.** A page carrying a full-page photograph extracts only its
  running head and folio, so it is invisible to every grep above — and a photo credit or
  rights line is exactly the sort of thing that would be printed on one. The text layer
  was split per page and every page holding fewer than 80 non-whitespace characters was
  listed: **32 of 625**. Four of those were already among the eleven above; the other 28
  were rendered. Six turned out to carry a full-page photograph — **pp. 111, 201, 225,
  235, 287 and 387** — and **all six were read as images**. Each is a street or shopfront
  scene (a taxi rank, a café awning, a dry cleaner's, a butcher's, the Hôtel de Ville),
  and **not one carries a credit line, a source line or any rights notice**: running head
  and folio only. That is consistent with the introduction's statement that the text
  photographs were taken by Marie-Charlotte Iszkowski herself.
- So **17 pages in total were read as images**: pp. 1–8, 111, 201, 202, 225, 235, 287,
  387, 624 and 625.
- **The introduction names its people and they are institute people, and it is signed.**
  The field-test version was "prepared in 1981 by Marie-Charlotte Iszkowski and Lydie
  Stefanopoulos", with editorial and technical guidance from Hedy A. St. Denis and Earl
  W. Stevick, and the final version written by Iszkowski in 1983. It describes "our
  native-speaking French staff", tapes recorded "in the FSI recording studio", artwork
  assisted by "the FSI audio-visual section", and a cover photograph "provided by the FSI
  Overseas Briefing Center". It is signed by *Jack Mendelsohn, Dean, School of Language
  Studies, Foreign Service Institute, Department of State*. **No outside contractor is
  named anywhere in it.** The work reads as in-house throughout, which is what matters:
  the exception the method warns about is a course produced under contract.
- **The document metadata agrees with the printed page.** `pdfinfo` names the Foreign
  Service Institute as author, and its title matches the title printed on p. 2.
- **Nothing found here announces the scan as an edited or repackaged edition.** On the
  seventeen pages read as images there is no digitiser's note, no publisher's imprint other
  than FSI's, and no "second printing" or "revised edition" statement — and the fuzzy
  search for `printing`, `reprint` and `reissu` returned nothing. The cover and title
  page still carry their photographs, so unlike the Italian scan — where the digitiser
  removed the photographs and said so — nothing here has been taken out that the file
  admits to.

**Not checked, and worth saying plainly:**

- **No legal opinion was obtained, and none of the above is one.** Every line in this
  section is a description of what a command printed or what a page showed.
- **The 22 remaining near-blank pages were measured, not read.** Of the 28 low-text pages
  rendered, six were the photographs read above; the other 22 were judged near-blank from
  the size of the rendered image rather than by being looked at. That is a proxy, and a
  weaker check than reading them. It was used to decide which pages deserved an image
  read, not to establish that those 22 carry nothing.
- **The low-text census used a threshold, and a threshold can miss.** 80 non-whitespace
  characters is an arbitrary line. A picture page carrying a caption as well as a running
  head could sit above it and never enter the candidate list.
- **608 of the 625 pages were never read as page images.** Seventeen were, and they are
  listed above. The rest were searched only through the OCR text layer, and the measured
  miss rate above says what that is worth. A notice printed on a page whose OCR mangled
  it, away from the front matter and the final leaf, would not have been found.
- **Whether the named individuals were FSI employees or engaged some other way was not
  established.** The introduction describes them as staff and the work as in-house, and
  nothing in the document suggests otherwise — but the document is the only thing that
  was consulted, and personnel records were not.
- **The OCR confusion set is inherited, not re-derived on this scan.** It was calibrated
  on the Spanish Basic scan in earlier work and reused here; it was measured against a
  control string in this session, but it was not re-derived from samples of this file's
  own damaged lines.
- **The text layer is damaged in the ordinary way of a 2008 paper capture** — the
  extracted text contains `sorne` for `some`, `1eeks` for `leeks`, `Il` for `11`. No
  search over it is reliable, which is why the conventional notice locations were read as
  images instead.
- **No archival copy of the file was kept.** It came from a mutable third-party bucket
  with no object versioning. The SHA-256 above pins the bytes and the fetch date says
  when they were seen, but if the bucket changes there is nothing here to compare against.
- **The Sub-Saharan French FAST file was checked only as far as this decision needed.**
  Its integrity was verified and its front matter read; its rights position was **not**
  settled, and its literal copyright search — which returned 1 hit, the same `reproduce
  it` sentence from the shared teacher's introduction — was not followed up with the
  image reads that would be needed to make a claim about it. If that course is ever used,
  Step 2 must be run against it properly.
- **The 40 accompanying audio files were not fetched, examined or ingested.** Nothing in
  this log says anything about them.
- **No entry was made in `converted_lessons.json`.** The method says to record the SHA-256
  there, but that file's entries describe converted courses, and nothing has been
  converted yet. The digest is recorded here instead; the entry belongs to the task that
  first converts a lesson.

The position recorded here is deliberately a description of what was found rather than a
conclusion about the law.

## Screening the volume

Step 3 of the method, applied to all 40 lessons. The screen ranks; it does not clear.
Every lesson that survived the screen was read, and reading is what decided.

### The page map, and there is one offset plus a per-lesson folio

The front matter runs PDF pages 1 to 49 and is numbered in roman — PDF 49 carries the
printed folio `xlviii`, and PDF 50 opens Lesson 1. So **lesson content begins at PDF 50,
an offset of 49.**

That single number is not enough to cite a page, because this course does not paginate
continuously: each lesson restarts at page 1 and its folios read `lesson-page` — `12-1`,
`25-14`. A citation therefore needs the lesson's own start page, and the table below
gives all 40. **PDF page = (the lesson's start page) + (printed page − 1).**

The lesson starts were not read off the running heads, and that is worth recording,
because the obvious method fails here. The OCR renders `LESSON 11` as `LESSON Il` — a
capital I and a lowercase l — on 13 of Lesson 11's 14 pages, and it mangles the head
outright on PDF 84, 414, 456, 457 and 510. A run keyed on the running head reported
Lesson 11 as a single page. The starts below were derived from the **folio** at the foot
of each page instead, which survived where the head did not, and then checked for
contiguity: the 40 ranges are monotonic, adjacent and cover PDF 50–587 without a gap.

### The screen, published so the counts reproduce

**The method's exclusion table holds Italian and English terms only.** Step 8 of
`converting-a-course.md` warns what happens if that is not noticed: a screen with no
terms in the target language "will pass green having inspected nothing". The French
terms below were written for this run. Matching is on stems, over text lowercased and
stripped of accents, so `frontière` and `frontiere` match alike — which matters on this
scan, whose OCR often sets a circumflex where the printed page has a grave (`biêre` for
`bière`, `service êtranger` for `étranger`). Accent-stripping absorbs that. It does not
absorb a letter the OCR got wrong outright.

The script itself lives in a session scratchpad and is **not** kept in this repository, so
the instrument is reproduced here in full rather than referenced. These are the four
families:

```
mil   militaire armee\b caserne soldat general\b generaux colonel sergent capitaine
      amiral lieutenant commandant guerre \bmarine\b \barmy\b \bmilitary\b barracks
      \bsoldier \bcolonel \bsergeant \badmiral \bnavy\b \bwar\b
emb   ambassade ambassadeur consulat\b \bconsul\b diplomat chancellerie \bembassy
      \bconsulate \bdiplomatic \battache\b "service etranger" "foreign service"
      "affaires etrangeres" "corps diplomatique" \bambassador \bchancer \bcaptain\b
unif  police policier gendarme commissariat \bflic "agent de police" \bpoliceman
      \bpolicemen \bcop\b "prefecture de police" \bofficier \bofficer
bord  douane douanier frontiere passeport \bpassport \bcustoms\b \bborder\b \bfrontier
      \bvisa\b "carte de sejour" immigration
```

and these are **all thirteen** false-positive patterns, which count a hit as discounted
rather than deleting it, so nothing disappears silently:

```
en general            \bgeneralement          \bin general\b        \bgenerally\b
\bgeneral idea         \bgeneral\b(?= (?:store|delivery|practitioner))
police d(?:'|e )assurance                     \bofficer?\b(?= (?:building|hours))
\bbureau\b              \bvisage               \bdevisag
bleu marine           navy blue
```

**Two defects in the first version of this screen were found in review, and both are
recorded rather than quietly corrected.**

The first was a guard that did not mean what its description said. The log described a
false-positive rule as catching "`marine` in `bleu marine`", but the pattern implemented
was `\bmarine\b(?= (?:bleu|blue))` — which requires `marine` to come *before* `bleu` and
therefore cannot match the French phrase at all. No count in this volume moved, because
the phrase does not occur in any of the 40 units, but a guard whose text and behaviour
disagree is the failure `CLAUDE.md` names from D19. The pattern is now the literal
`bleu marine`.

The second changed real numbers. **This is a Foreign Service Institute course, and the
first screen had no term for the Foreign Service itself.** `service étranger` and
`foreign service` were absent from the `emb` family. Worse, they are *multi-word*, and
the first screen searched line by line — while the course sets French and English in two
columns, so a term that wraps inside one column has the opposing column's text sitting
between its halves:

```
     Il est entré dans le service            He entered the Foreign
     étranger.                               Service.
```

No per-line search can see either phrase there, and neither can a naive
collapse-the-whitespace search, because collapsing interleaves the columns. The screen
now rebuilds each column as its own stream and runs the multi-word patterns over it.

**Be precise about what that bought, because it is less than it looks.** Running the
multi-word patterns both ways over every lesson, the column rebuild recovers exactly
**two** hits in the whole volume that the per-line pass could not reach, and both are in
Lesson 29 — the pair quoted above. Everywhere else a multi-word term happened to fit on
one line and was already counted. So the rebuild is worth keeping, because those two hits
are in a lesson that was shortlisted, but it is not a large correction and this log does
not present it as one.

**Lesson 29 therefore no longer scores zero.** The first published version of this table
recorded it at 0; it is 1. That is what moved it off the shortlist — see below.

**A third defect, found in the same review round and fixed the same way.** The `emb`
family held the French `ambassadeur` but not the English `ambassador`, and this course is
bilingual on facing columns — so the English gloss of a word already on the list went
uncounted. It occurs **20 times, on 20 lines, across 14 lessons**, one of which, Lesson 17, was
shortlisted: `I never speak to the ambassador.` Adding `\bambassador` (with `\bchancer`
and `\bcaptain\b`, neither of which occurs) moved **14 table rows** — fewer than the 20
lines, because in Lessons 26, 32 and 33 the ambassador line already carried another hit
and so added no new `Lines` row. Lesson 17 went from 4 to 6 and Lesson 32 from 42 to 44,
and the zero-scoring set was unchanged. The lesson generalises: **for
every term in a family, check whether its translation is there too.** That check is what
found this, and it is the sweep the two earlier fixes should have included.

### What the screen found

`Raw` is every match; `FP` the hit lines discounted as innocent; `Lines` the distinct
lines carrying a hit, which is the unit of work for whoever has to replace them. These
are the figures from the final screen, after all three corrections above.

| Lesson | Title | Printed | PDF | mil | emb | unif | bord | Raw | FP | Lines | Outcome |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Arrival — Meeting M. Bertrand | 1-1 – 1-12 | 50–61 | 0 | 15 | 0 | 1 | 16 | 0 | 15 | dropped |
| 2 | Greeting M. Bertrand — Finding a Taxi | 2-1 – 2-10 | 62–71 | 0 | 17 | 0 | 0 | 17 | 0 | 15 | dropped |
| 3 | In an Embassy Car — Making Conversation with the Driver | 3-1 – 3-12 | 72–83 | 2 | 11 | 0 | 0 | 13 | 0 | 13 | dropped |
| 4 | Finding the Embassy — Getting your Bearings | 4-1 – 4-14 | 84–97 | 0 | 26 | 0 | 0 | 26 | 0 | 24 | dropped |
| 5 | A Taxi Trip to the Hotel — Getting your Baggage | 5-1 – 5-14 | 98–111 | 0 | 12 | 0 | 0 | 12 | 0 | 11 | dropped |
| 6 | At the Hotel — Checking in | 6-1 – 6-16 | 112–127 | 1 | 11 | 2 | 0 | 14 | 0 | 13 | dropped |
| 7 | At the Hotel — Making a Long Distance Call | 7-1 – 7-16 | 128–143 | 0 | 12 | 5 | 0 | 17 | 0 | 15 | dropped |
| 8 | At the Hotel — Complaining about your Room | 8-1 – 8-16 | 144–159 | 0 | 11 | 0 | 0 | 11 | 0 | 8 | dropped |
| 9 | At the Train Station — Going to Versailles | 9-1 – 9-16 | 160–175 | 0 | 23 | 0 | 0 | 23 | 0 | 22 | dropped |
| 10 | At the Airport — Meeting a Relative | 10-1 – 10-12 | 176–187 | 0 | 14 | 0 | 0 | 14 | 0 | 11 | dropped |
| 11 | No Taxis — Looking for the Metro | 11-1 – 11-14 | 188–201 | 0 | 9 | 14 | 0 | 23 | 0 | 14 | dropped |
| 12 | Taking the Metro — Getting your Tickets | 12-1 – 12-12 | 202–213 | 0 | 2 | 0 | 0 | 2 | 0 | 2 | shortlisted, then dropped at conversion |
| 13 | Meeting a Friend in a Café | 13-1 – 13-12 | 214–225 | 0 | 8 | 0 | 0 | 8 | 0 | 5 | dropped |
| 14 | Taking Clothes to the Dry Cleaner | 14-1 – 14-10 | 226–235 | 0 | 7 | 0 | 0 | 7 | 0 | 6 | **SHORTLIST** |
| 15 | Shopping for Food — At the Bakery | 15-1 – 15-14 | 236–249 | 0 | 4 | 0 | 0 | 4 | 0 | 3 | **SHORTLIST** |
| 16 | Shopping for Food — At the Delicatessen | 16-1 – 16-14 | 250–263 | 1 | 11 | 0 | 0 | 12 | 0 | 12 | dropped |
| 17 | Shopping for Food — At the Greengrocer | 17-1 – 17-12 | 264–275 | 0 | 6 | 0 | 0 | 6 | 0 | 3 | **SHORTLIST** |
| 18 | Shopping for Food — At the Butcher Shop | 18-1 – 18-12 | 276–287 | 0 | 5 | 0 | 0 | 5 | 0 | 4 | dropped |
| 19 | Shopping in a Department Store | 19-1 – 19-12 | 288–299 | 1 | 2 | 0 | 0 | 3 | 1 | 3 | dropped |
| 20 | Getting Stuck in a Traffic Jam | 20-1 – 20-14 | 300–313 | 4 | 2 | 0 | 0 | 6 | 3 | 6 | dropped |
| 21 | At the Restaurant — Ordering your Meal | 21-1 – 21-14 | 314–327 | 1 | 0 | 0 | 0 | 1 | 1 | 1 | dropped |
| 22 | At the Restaurant — Choosing the Wines | 22-1 – 22-14 | 328–341 | 0 | 15 | 0 | 1 | 16 | 0 | 12 | dropped |
| 23 | Buying Medicine at the Pharmacy | 23-1 – 23-14 | 342–355 | 1 | 8 | 0 | 0 | 9 | 0 | 5 | dropped |
| 24 | Calling a Doctor | 24-1 – 24-18 | 356–373 | 0 | 17 | 0 | 0 | 17 | 0 | 11 | dropped |
| 25 | At the Tobacconist — Buying Postcards | 25-1 – 25-14 | 374–387 | 2 | 0 | 0 | 0 | 2 | 0 | 2 | dropped |
| 26 | Planning an Outing to the Movies | 26-1 – 26-14 | 388–401 | 3 | 8 | 0 | 0 | 11 | 1 | 6 | dropped |
| 27 | Planning an Evening at the Opera | 27-1 – 27-12 | 402–413 | 0 | 7 | 0 | 0 | 7 | 0 | 4 | dropped |
| 28 | Going to Dinner in a French Home | 28-1 – 28-16 | 414–429 | 1 | 6 | 0 | 0 | 7 | 1 | 5 | dropped |
| 29 | Conversing with your Friends during Dinner | 29-1 – 29-12 | 430–441 | 0 | 1 | 0 | 0 | 1 | 0 | 1 | dropped |
| 30 | Getting your Hair Cut | 30-1 – 30-14 | 442–455 | 2 | 3 | 0 | 0 | 5 | 0 | 5 | dropped |
| 31 | At a Department Store — Buying an Electrical Appliance | 31-1 – 31-14 | 456–469 | 0 | 3 | 0 | 0 | 3 | 0 | 2 | dropped |
| 32 | Answering the Phone at the Embassy | 32-1 – 32-14 | 470–483 | 2 | 40 | 2 | 0 | 44 | 0 | 27 | dropped |
| 33 | Making an Appointment | 33-1 – 33-12 | 484–495 | 0 | 9 | 0 | 0 | 9 | 0 | 6 | dropped |
| 34 | Greeting a Visitor at the Embassy | 34-1 – 34-14 | 496–509 | 0 | 4 | 0 | 0 | 4 | 0 | 2 | dropped |
| 35 | Getting a Traffic Ticket | 35-1 – 35-14 | 510–523 | 0 | 20 | 10 | 3 | 33 | 0 | 26 | dropped |
| 36 | Dealing with a Minor Traffic Accident | 36-1 – 36-16 | 524–539 | 1 | 7 | 3 | 1 | 12 | 0 | 8 | dropped |
| 37 | Finding an Apartment | 37-1 – 37-14 | 540–553 | 0 | 3 | 0 | 0 | 3 | 0 | 2 | dropped |
| 38 | Accepting a Delivery | 38-1 – 38-12 | 554–565 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **SHORTLIST** |
| 39 | Calling a Locksmith | 39-1 – 39-12 | 566–577 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **SHORTLIST** |
| 40 | Responding to Emergencies | 40-1 – 40-10 | 578–587 | 0 | 6 | 6 | 0 | 12 | 0 | 7 | dropped |

**A note on unit naming.** The Italian log names units by roman numeral because Italian
FAST does. This course numbers its lessons in arabic — `LESSON 12`, folio `12-1` — so
this log uses arabic throughout. Naming them `XII` would be this log's invention, not the
course's.

### The shortlist — six lessons, each read in full

**Five of these six shipped.** Unit 12 was dropped during conversion, for a reason that
only appears once you try to make a lesson out of it; see "What did not ship, and why"
below. This section is left as the screening produced it rather than rewritten, because
what it records is what was true at the end of screening.

| # | Lesson | Printed | PDF | Raw | Why it was approved |
|---|---|---|---|---|---|
| 1 | **12** Taking the Metro — Getting your Tickets | 12-1 – 12-12 | 202–213 | 2 | Buying `un carnet` at the ticket window. The whole dialogue is four lines and a shrug from the clerk; the functional notes on tickets, carnets and the métro map are the substance. Maps directly onto `fr-05-directions`. |
| 2 | **14** Taking Clothes to the Dry Cleaner | 14-1 – 14-10 | 226–235 | 7 | An errand a child recognises: handing over clothes, counting items, collecting them later. Its grammar point is `depuis` — how long something has been going on. |
| 3 | **15** Shopping for Food — At the Bakery | 15-1 – 15-14 | 236–249 | 4 | Asking for bread by name (`une baguette, un bâtard, un pain de campagne`), then a price and change. Numbers and money in a setting that needs no relocation. |
| 4 | **17** Shopping for Food — At the Greengrocer | 17-1 – 17-12 | 264–275 | 6 | Parsley, a lettuce, a pound of tomatoes, a kilo of apples. Quantities and refusing politely (`je ne mange jamais d'ail`). The cleanest shopping dialogue in the volume. |
| 5 | **38** Accepting a Delivery | 38-1 – 38-12 | 554–565 | 0 | Delivery men bring a wardrobe, the lift is too small, it goes up the stairs, you say where to put it and sign for it. Domestic, concrete, and entirely free of the course's diplomatic premise. |
| 6 | **39** Calling a Locksmith | 39-1 – 39-12 | 566–577 | 0 | You are locked out and telephone a locksmith: saying where you are and being told what it will cost and how long it will take. (The unit as printed also asks the learner for their own name — see the strip list below; that is curation work, not a reason for approval.) Scores zero on every family, and carries no `ambassadeur`, no `ambassador` and no body-measurement drill — true of four of the six (12, 14, 38, 39); only 15 and 17 fail it. |

**What every one of these six still needs.** Four of the six carry screen hits — 12, 14,
15 and 17 — and in all four every hit is an embassy reference sitting in a **grammar-drill
substitution slot**: `Je vais à l'ambassade`, `Vous ____ à l'ambassade. (être)`,
`Depuis combien de temps êtes-vous à l'ambassade?`, `I never speak to the ambassador.`
None is in a dialogue; each is a noun in a frame built to teach something else. That is
the Identity defect `plan.md` names, and it is the same one-word replacement Italian made
when `all'Ambasciata` became `alla stazione`: the grammar point survives the swap
untouched. Lessons 38 and 39 have no hits at all.

**And these things in the kept set that no exclusion family has a term for**, found by
reading and recorded so curation strips them on the same pass. **This list is what reading
found, not a guarantee of what is there** — an earlier draft of it recorded body weight and
missed the body-height drill sitting on the same page, so treat it as a floor:

- **Two units tell the learner to say their own name aloud, and one to give an address.**
  Lesson 14's dialogue prints `Votre nom, s'il vous plaît?` and then, on its own line, the
  instruction **`(Give your name and spell it.)`**. Lesson 39's has `L'employé: Votre nom?`
  answered by a blank the learner fills, directly beneath a scripted home address with
  floor and door. This is the same class as `Combien pesez-vous?`, which this log already
  treats as decision-grade — it is one of the reasons Lesson 29 was dropped — and it is
  sharper here, because this app takes identity from face recognition and **must never
  accept it from the conversation**. A tutor that prompts a child for their name and
  address is asking for exactly the data the design keeps out of the dialogue, and this
  repository has shipped a learner's name to a log once already. The fix is cheap and
  costs nothing pedagogically: script an invented name, as the course already scripts the
  address. Neither unit is disqualified, and neither of the other four asks for either.
- **A body-measurement thread runs through Lessons 15 and 17 — weight *and* height.**
  Lesson 15's metric-conversion exercise is built on it: seven lines of `Vous pesez 130
  livres`, `127`, `162`, `170`, `175`, `118`, `138`, and immediately below them five of
  `Vous mesurez 5'7"`, `4'9"`, `5'2"`, `5'8"`, `5'10"`. Lesson 17 has the pair too — "If
  you are 5'2", 5'7", or 6'2"…" as question 1 and "If you weigh 130, 125, or 147 lbs.…"
  as question 2 of the same block. A robot that tutors children in their homes should not
  be running drills about their bodies, and the conversion practice survives intact if
  the numbers become parcels, luggage or furniture — Lesson 38's wardrobe, measured in
  metres, is the in-volume precedent. **This is also why Lesson 29 is no longer
  shortlisted**: its version is `Combien pesez-vous?`, a question the learner answers
  *aloud*, which would have the tutor eliciting a child's weight.
- **Lesson 17 — one alcohol drill line.** `Elle vient de prendre une biêre.`, a `venir de`
  exercise rendered as "She has just had a beer." Same class as the item Lesson 13 was
  rejected for, so consistency requires it be named rather than passed over because the
  unit is otherwise good.
- **Lesson 17 — six franc prices** in its ACTIVITY block (`1 kilo de pommes Golden: 7 F`
  and five more), which the franc census below had missed.
- **Lesson 12 — one tobacconist mention.** A functional note explains that métro tickets
  are sold "at tobacconists' (Tabac)". Lesson 25 was dropped for a tobacconist *setting*;
  this is a passing reference in a note, but it is the same word and it is recorded.
- **Lesson 39 — the vocabulary list glosses `nu (f, nue)` as "nude".** The source means
  `pieds nus`, barefoot, and the setting is a man locked out without his shoes; but the
  gloss as printed is the bare adjective, in the `A CLOSER LOOK` list every learner is
  drilled through. Nudity is a decided concern in this log — it is one of the reasons
  Lesson 29 was dropped — so the fix is to gloss the phrase, not the word.
- **Lesson 39 — an illness thread.** The caretaker `est malade, il est à l'hôpital`, and a
  present-tense `être` drill runs the learner through `Je suis à l'hôpital`, `Elle … à
  l'hôpital`, `Nous sommes à l'hôpital`. Lessons 23 and 24 were dropped for illness, so
  this is named here on the same standard. The grammar point survives a one-word change of
  location, exactly as the embassy drill slots do.
- **Lesson 39 — two smaller items.** `Il y a une fuite de gaz` in the More Words list, and
  a `Vocabulaire supplémentaire` block that casts the learner as a parent employing a
  babysitter and prints two telephone numbers.
- **Lesson 39's tail, and the dated items.** The caller is barefoot, the fee is 400 francs,
  and one drill line sends the learner to the `bar tabac`.

### Why each dropped lesson was dropped

Thirty-four lessons were dropped. Every one is listed.

| Lesson | Why not |
|---|---|
| 1 Arrival — Meeting M. Bertrand | Arrival at post. 15 embassy hits, and the dialogue is being met by an embassy driver and taken through police control and customs. |
| 2 Greeting M. Bertrand — Finding a Taxi | 17 embassy hits. The premise is the same arrival, continued. |
| 3 In an Embassy Car — Making Conversation with the Driver | Embassy in the title. 13 hits. |
| 4 Finding the Embassy — Getting your Bearings | Embassy in the title, and the highest embassy count outside Lesson 32 at 26. The directions are directions *to the embassy*. |
| 5 A Taxi Trip to the Hotel — Getting your Baggage | 12 embassy hits; the arrival sequence again. |
| 6 At the Hotel — Checking in | 11 embassy hits, and the premise is a diplomat's temporary lodging at post. |
| 7 At the Hotel — Making a Long Distance Call | 12 embassy, 5 uniformed. Calling the office from the hotel. |
| 8 At the Hotel — Complaining about your Room | 11 embassy hits. |
| 9 At the Train Station — Going to Versailles | 23 embassy hits — the drills are saturated with them. |
| 10 At the Airport — Meeting a Relative | 14 embassy hits. |
| 11 No Taxis — Looking for the Metro | 14 uniformed hits: the dialogue is asking a *policeman* for directions. |
| 13 Meeting a Friend in a Café | **Dropped on reading, not on its score.** See below. |
| 16 Shopping for Food — At the Delicatessen | 11 embassy hits and 24 franc prices; the delicatessen is covered better by 15 and 17. |
| 18 Shopping for Food — At the Butcher Shop | Clean dialogue, but the cultural note dwells on brains, sweetbreads and tripe on open display, and a third food-shopping lesson adds little next to 15 and 17. |
| 19 Shopping in a Department Store | Thin dialogue; the department store returns in 31. |
| 20 Getting Stuck in a Traffic Jam | 4 military hits, 3 of them the false-positive `en général`; dropped for content, not score — a traffic jam is a poor teaching setting for a beginner. |
| 21 At the Restaurant — Ordering your Meal | **Dropped on reading, not on its score.** See below. |
| 22 At the Restaurant — Choosing the Wines | A lesson about choosing wine. |
| 23 Buying Medicine at the Pharmacy | Medicines and symptoms; not early vocabulary for a child. |
| 24 Calling a Doctor | As above, with illness as the whole premise. |
| 25 At the Tobacconist — Buying Postcards | The setting is a tobacconist, with 20 tobacco references, and the Arc de Triomphe note describes the tomb of the unknown soldier. |
| 26 Planning an Outing to the Movies | 11 hits including 8 embassy; the plan-making is done better by 29. |
| 27 Planning an Evening at the Opera | 7 embassy hits; as above. |
| 28 Going to Dinner in a French Home | 7 hits and an aperitif-led opening; 29 covers the same evening without them. |
| 29 Conversing with your Friends during Dinner | **Dropped on reading, after first being shortlisted.** Its dialogue is good, but it carries `Combien pesez-vous?` as a spoken Practice question, three Foreign Service references, and a Cultural Notes block of about 120 lines — roughly a sixth of the unit — naming a firing-squad painting (*The Execution of Emperor Maximilian*), *L'Absinthe*, a bar scene and two paintings notorious for nudity. Lesson 18 was dropped partly for a cultural note about tripe; this one is worse, and consistency required the same answer. Lesson 39 took its place. |
| 30 Getting your Hair Cut | The setting names an embassy colleague, and the celebrity notes cover Simone de Beauvoir's war and a politician deported to Germany. |
| 31 At a Department Store — Buying an Electrical Appliance | 3 embassy hits including a drill line placing the embassy seven miles from the station; the premise is replacing a hairdryer after a 200-franc haircut. |
| 32 Answering the Phone at the Embassy | Embassy in the title; 44 hits, the highest in the volume — the next highest is Lesson 35 at 33. Consuls, cultural attachés, a diplomat's spouse. |
| 33 Making an Appointment | 9 embassy hits; the appointment is an office one. |
| 34 Greeting a Visitor at the Embassy | Embassy in the title. **Its score is only 4** — a good illustration that a low count does not mean a clean unit. |
| 35 Getting a Traffic Ticket | 10 uniformed and 3 border hits: the dialogue is being stopped by police. |
| 36 Dealing with a Minor Traffic Accident | Police, insurance and a collision. |
| 37 Finding an Apartment | 3 hits only, and the dialogue is sound, but the premise is an estate agent, a rent in francs and a newspaper small ad — a long way from a beginner's first lessons. |
| 40 Responding to Emergencies | Emergencies, with 6 uniformed hits. |

### What reading found that the screen cannot see

This is the part the method insists on, and it earned its place twice.

**Lesson 21 is the case the method warns about, exactly.** It scores **one raw hit**, tied with
Lesson 29 for the lowest of any lesson that scored at all, and that hit is `In general` —
a false positive, so its effective score is nothing. Only Lessons 38 and 39 score lower,
at zero. By the screen, 21 is as clean as a lesson with any hit can be. Read, its dialogue opens:

> *Nous allons commencer par un apéritif. Pierre, que prenez-vous?* — *Un Pernod.* —
> *Alors, un Pernod et un whisky à l'eau pour moi.*

The first thing the learner is taught to say is an order for two alcoholic drinks. The
unit also reproduces La Coupole's full 1983 menu as a page image. Nothing in the four
exclusion families is present, and nothing was ever going to be: the screen has no term
that could have caught this. **A count of zero means "start here", not "this is fine."**

**Lesson 13 is the same failure with more at stake**, because it was the best thematic
match in the volume for `fr-04-ordering-food` — meeting a friend in a café. It screens 8,
all of them embassy drill slots. Read, it is an alcohol lesson: a café tariff listing
beers, whiskies and cognac; a functional note explaining how to order a beer (`un demi`,
`une pression`); a vocabulary list carrying `une bière`, `un ballon de rouge`, `un verre
de vin blanc`; and the partitive article — the lesson's actual grammar point — taught
through `Je bois du vin. / Je ne bois pas d'alcool.` Thirty-one references in total. The
café unit this app most wanted is the one reading rejected.

**The zero-scoring lessons were re-read specifically for a miss, and the result is
recorded whichever way it fell.** On the final screen **two** lessons score zero on all
four families — 38 and 39 — and both are shortlisted. Lesson 29 scored zero on the first
version of the screen, and is the reason the screen was corrected twice.

- **Lesson 29 — the re-read that paid for itself, and then cost the lesson its place.**
  It scored zero and it was not clean. Reading it produced three Foreign Service
  references that no pattern could match, a spoken `Combien pesez-vous?`, and the Cultural
  Notes block described in the drop table. The first two of those are what drove the two
  screen corrections above. It is the volume's own proof that **a count of zero means
  "start here", not "this is fine"** — and, having read it, the honest conclusion was to
  drop it rather than shortlist it with a strip list longer than any other unit's.
- **Lesson 38 — read end to end, and it is the cleanest unit in the volume.** Three dated
  elements rather than one: a 30-franc tip; a functional note explaining that `camarade`
  "doesn't necessarily refer to a member of the Communist Party, as the English cognate
  does", which is a 1984 Cold-War aside; and a note about showing an I.D. card to collect
  a registered letter. None is in an exclusion family and none disqualifies the lesson.
- **Lesson 39 — read end to end, and promoted onto the shortlist.** Three one-line
  problems, listed above. Nothing from the four families, no `ambassador` in either
  language, and no body-weight drill.

**Three further things run through the volume**, shortlisted ones included, and they are
curation work rather than grounds for rejection. Only the first is true of every lesson;
the counts for the other two are given rather than implied:

- **A human instructor.** Every lesson tells the learner to listen to a tape, compare
  notes with classmates and ask their teacher — matching `your teacher`, `the instructor`,
  `your classmates`, `votre professeur` and `tape`, that is between 4 and 18 such
  references each,
  in all 40. This is the fourth category `plan.md` names, and the tutor *is* that
  instructor, so the framing has to be rewritten rather than transcribed.
- **Francs.** Prices are in francs and centimes. Counting the word *and* the `7 F` price
  notation the course also uses, **15 of the 40 lessons** carry them: 5, 6, 15, 16, 17,
  18, 19, 25, 29, 30, 31, 34, 37, 38, 39. This number was wrong twice before it was right.
  A first pass matched only the word and returned 13, missing shortlisted Lesson 17 and
  its six `N F` prices — a census meant to catch dated content in units bound for
  curation, missing one of them. A second pass added the notation as `\b\d+\s*f\b` and
  returned 16, because that pattern matches the `2 f` in `le 2 février 1812`. Lesson 21
  has no franc token, no centime and no price notation; it is not in the set. France
  adopted the euro in 2002.
- **A 1983–84 Paris.** Telephone numbers in the old format, a `Le Figaro` small ad, shop
  names and a transport authority described as it then was.

### What the shortlist covers, and the two things it does not

The shortlist was checked against what a beginner actually needs first, rather than
accepted because it is what happened to survive.

**Covered, with one qualification.** *Greetings* — three of the six use the full
`Bonjour Madame/Monsieur`: Lessons 14, 15 and 38, in a shop or at a door, which is the
politeness `fr-01-greetings` asks for. Lesson 17 opens with a greeting too, but the
shorter `Bonjour, bonjour.` Only Lesson 15 also closes with `Au revoir`. The remaining
two are not greeting lessons at all: Lesson 12 is a four-line exchange at a ticket
window, and Lesson 39 is a telephone call that opens with the problem. So the shortlist
supplies greetings from four of six units, the full polite form from three, and
leave-taking from one. *Numbers and money* — Lesson 15 counts pastries
and makes change from a 500-franc note, Lesson 17 works in `une livre` and `un kilo`,
Lesson 14 counts shirts and ties, Lesson 12 buys a book of ten tickets, and Lesson 39 is
quoted a price and a waiting time. *Everyday
exchanges* — asking for a thing, being offered another, refusing politely, agreeing a
time, saying where to put something.

**Two gaps, and they are gaps in the source, not in the screen.**

- **Introducing yourself** (`fr-02-introductions` — `je m'appelle`, `j'ai … ans`,
  `j'habite à…`) is not in any shortlisted unit. This course teaches it in Lessons 1 and
  2, where the person you introduce yourself to is an embassy colleague meeting your
  plane — which is exactly why those lessons are dropped. The curation task will have to
  build introductions from the greeting exchanges that are here, or take them from
  another course.
- **Daily routine with reflexive verbs** (`fr-06-daily-routine` — `je me lève`,
  `je me prépare`) is not covered either. FAST is built out of transactions with
  strangers, and a morning at home is not one of them.

Recording these now is cheaper than discovering them mid-curation, and neither is an
argument for stretching the shortlist: the units that would have filled these slots are
the disqualified ones.

### What this screening did NOT do

- **The published screen is the corrected one, and the first was wrong twice.** Review
  caught a false-positive pattern that could not match the phrase it documented, and a
  missing `emb` term for the Foreign Service — the institution that wrote this course —
  compounded by a per-line search that cannot see a multi-word term wrapped inside one
  column of a bilingual page. The counts in the table above are from the corrected screen;
  Lesson 29 moved from 0 to 1 and off the shortlist, Lesson 32 from 40 to 44, and 14 rows
  moved again when the English `ambassador` was added. Anyone comparing against an earlier
  draft of this file will find different numbers, and the final ones are these.
- **Multi-word terms are still weaker than single-word ones.** Seven of the screen's
  patterns are multi-word — `service etranger`, `foreign service`, `affaires etrangeres`,
  `corps diplomatique`, `agent de police`, `prefecture de police` and `carte de sejour` —
  and all seven depend on the column rebuild, which is a heuristic: it splits on a run of
  three or more spaces, so a page whose columns are set differently, or whose OCR has run
  them together, will defeat it.
- **The vocabulary was wrong three times, and the third was found by a sweep the first two
  should have included.** A false-positive pattern that could not match its own
  description; a missing term for the institution that wrote the course; and the English
  half of a French word already on the list. Each was found by review rather than by
  writing the list more carefully, which is the honest reason this log insists the screen
  ranks and the reading decides.
- **It did not read all 40 lessons end to end.** Every lesson was screened, and its hit
  lines were read in context. The six shortlisted lessons and the near-miss candidates
  (13, 18, 21, 25, 29, 30, 37, 38, 39) were read as continuous text. The remainder — the
  clearly disqualified arrival, hotel, embassy and police lessons — were judged on their
  title, their setting, their hit lines and their counts, not on a full reading.
- **It read the OCR text layer, not the pages.** The rights section measures what that
  layer is worth: a damage-tolerant matcher over it still missed 233 of 315
  single-character substitutions. A disqualifying word whose OCR is mangled would not
  have been counted. The page images were not consulted for this step, and a lesson
  carrying a photograph of, say, a police officer would not register at all.
- **The exclusion vocabulary is a ranking instrument and is not complete.** It was written
  for this run and has never been calibrated against a known-bad French corpus. Both real
  findings above — the Pernod and the café tariff — were found by reading, and no
  expansion of the word list would have caught either.
- **It did not populate `APPROVED_UNITS`.** The shortlist lives here until curation,
  following the precedent the Portuguese log sets.
- **The "roughly a third survives" expectation does not transfer.** It was written from
  Italian's six of eighteen. This volume has 40 lessons, so a third would be thirteen —
  more than the curation task can absorb, and more than the volume can honestly supply
  once the arrival, hotel, embassy, police and medical sequences are removed — that much
  the 34-row drop table above supports on its own. Six is what survived reading, not a
  target: Lesson 39 was held back as a reasonable seventh rather than added to round the
  number up. Italian, Spanish and Portuguese each shipped six as well, but that is a
  coincidence worth noticing and not a reason, and it played no part in this decision.

## Conventions applied to every unit

Recorded once here rather than repeated against every line they touched.

- **The page image is the source, and the text layer is only orientation.** Every line
  that ships was read off a page rendered at 140 dpi, and every accented character in it
  was confirmed against a crop of the same page rendered at 300 dpi. That is not
  ceremony on this scan: the OCR sets a circumflex where the page has an acute or a
  grave, so the text layer offers `rêpond` for `répond` and `biêre` for `bière`. Both
  are real French letters, so nothing downstream would have flagged either.
- **A spoken greeting replaces a printed one.** The course prints `Bonjour
  Madame/Monsieur.` — a convention a reader resolves silently and a voice cannot say at
  all. Every occurrence ships as `Bonjour Madame.`, and the note in Lesson 14 tells the
  learner that either address is normal. The same rule removes the brace-and-`or`
  alternatives the course prints for a learner's turn: the first branch ships and the
  second is recorded here rather than spoken.
- **A gender marker becomes an article.** The vocabulary lists print `vêtement (le)`.
  A tutor cannot say a parenthesis, so these ship as `le vêtement`, which teaches the
  gender by carrying it.
- **Francs become euros, and nothing else moves.** Prices are the one dated element that
  appears inside a sentence rather than beside it. `Ça fait 14 francs cinquante` ships as
  `Ça fait 14 euros cinquante`: one noun for another of the same number and gender, so
  the sentence and its grammar are untouched. The amounts are left exactly as printed.
- **Lines addressed to a human teacher are dropped, never translated.** *Listen to the
  tape*, *Compare notes with your classmates*, *Ask your teacher about anything you still
  don't understand* appear in every unit. The tutor **is** that teacher, so translating
  one would put a directive into the material it teaches from.
- **Nothing that asks a child for their own details ships.** Lesson 14 prints `Votre nom,
  s'il vous plaît?` followed by the instruction `(Give your name and spell it.)`, and
  Lesson 39 prints `Votre nom?` with a blank for the learner to fill. Both were cut. This
  app takes identity from face recognition and never from the conversation, and a tutor
  that prompts a child for their name is asking for exactly what that design keeps out of
  the dialogue. The scripted street address in Lesson 39 is kept: it is printed, it is
  fictional, and it is not the learner's.
- **Every drill ships as a repetition drill.** Not one cue in these five units has a
  single determinate answer — the baker offers three kinds of loaf, the greengrocer
  offers garlic you may accept or refuse, and Lesson 12's practice page says outright
  *Choose an answer or make up your own*. A learner who gave a different correct answer
  would be marked wrong, so none of them may be a `cue_response`. This is the same
  conclusion the Brazilian Portuguese conversion reached, for the same reason.
- **Usage notes are written, not transcribed, and they address the learner.** The
  course's own functional notes are written to an adult at a diplomatic post and often
  explain Paris rather than French. What ships is a note about the language, in English,
  said to the person learning it.

## What shipped, unit by unit

Five lessons, in catalog order. Every printed page named here was rendered and read.

### `fr-fast-01-at-the-dry-cleaner` — unit 14, printed pages 14-1 to 14-3

*Handing over clothes to be cleaned.* You greet the shopkeeper, say what you want done,
count what you are leaving, and agree when to collect it.

- **Kept whole:** the dialogue from `Bonjour Madame.` to `C'est parfait.`, seven turns.
- **Replaced:** `Bonjour Madame/Monsieur.` → `Bonjour Madame.`, per the convention above.
- **Dropped:** the closing `Votre nom, s'il vous plaît?` and the instruction
  `(Give your name and spell it.)` that follows it, both on printed page 14-2.
- **Dropped:** the alternative learner turn `J'ai cette robe, ces deux jupes, et mon
  chemisier.`, which the course prints in a brace as an either/or with the turn that
  ships. Nothing is wrong with it; a lesson cannot say both.
- **Drills:** twelve, all repetition — eight sentences from the dialogue and four
  vocabulary items from the `A CLOSER LOOK` list on printed page 14-3.

### `fr-fast-02-at-the-bakery` — unit 15, printed pages 15-1 to 15-2

*Buying bread.* Asking for `un pain` turns out not to be enough, because bread has
names; then the price, and a note that will not be changed.

- **Kept whole:** all twelve turns, including the ending, in which the baker has no
  change and the buyer leaves the pastries behind. It is not a tidy ending and it is a
  real exchange.
- **Replaced:** `francs` → `euros`, three times in the dialogue (`14 francs` and
  `500 francs` twice), amounts unchanged.
- **Replaced:** `Bonjour Madame/Monsieur.` → `Bonjour Madame.`
- **Dropped:** the metric-conversion drill on printed pages 15-11 and 15-12 — seven lines
  converting the learner's weight and five converting their height. Same reason as
  Lesson 3's.
- **Drills:** twelve, all repetition.

### `fr-fast-03-at-the-greengrocer` — unit 17, printed pages 17-1 to 17-2

*Fruit and vegetables by weight.* Asking for quantities, and refusing garlic twice.

- **Kept whole:** eleven turns. The seller's `Ne touchez pas à la marchandise` is kept
  rather than softened; it is what the stall-holder says, and the note explains why.

  An earlier version of this lesson shipped ten turns, because two learner turns had
  been spliced into one — `Non, non, merci. Et je voudrais un kilo de Golden.` lost its
  ending, `Hum! Et un kilo de raisins et deux pamplemousses.` lost its opening, and the
  seller's line between them was left with no reply. The result was a French sentence
  that is nowhere on the page, which is the one thing this whole method exists to
  prevent. It was caught in review, and the page is restored above.
- **Dropped:** the `venir de` practice line `Elle vient de prendre une bière.` on printed
  page 17-8. The grammar point is taught by the two other examples printed beside it.
- **Dropped:** the metric-conversion exercise on printed page 17-10, whose two questions
  convert the learner's own height and then their own weight. A tutor in a child's home
  does not ask for either.
- **Drills:** twelve, all repetition.

### `fr-fast-04-a-delivery-arrives` — unit 38, printed pages 38-1 to 38-2

*A wardrobe is delivered.* The lift is too small, it goes up the stairs, and you say
where to put it and sign for it.

- **Kept whole:** ten turns.
- **Replaced:** `Bonjour Madame/Monsieur.` → `Bonjour Madame.`
- **Dropped:** the closing stage direction `(Vous donnez 30 francs environ pour les
  deux.)` on printed page 38-2, which is narration rather than a line anybody says.
- **Rewritten, not dropped:** the functional note on printed page 38-2 about `camarade`.
  The page explains that the word "doesn't necessarily refer to a member of the Communist
  Party, as the English cognate does". What ships says what the word *does* mean and keeps
  the contrast, because the English cognate is the confusion a learner brings. An earlier
  version of the shipped note dropped the page's *necessarily* and so claimed more than
  the source; it now carries the hedge the page carries.
- **Drills:** twelve, all repetition.

### `fr-fast-05-locked-out` — unit 39, printed pages 39-1 to 39-2

*Telephoning for help.* You are shut out of your flat and you call a locksmith.

- **Kept whole:** seven turns, including the answering machine, which is the only
  recorded voice in the five units.
- **Replaced:** `400 francs` → `400 euros`.
- **Dropped:** `Votre nom?` and the blank beneath it.
- **Dropped:** the vocabulary gloss `nu (f, nue) — nude` on printed page 39-5. The source
  means `pieds nus`, barefoot; the bare adjective glossed that way is not a word to teach
  a child out of its phrase.
- **Dropped:** the `être` conjugation drill on printed page 39-7. Its printed stem is
  `Il est à l'hôpital.`; the learner supplies `Je`, `Elle`, `Nous` and `Vous` forms into
  blanks. Also the caretaker's illness in the setting on printed page 39-1. Lessons 23 and 24 were dropped at screening for
  illness, and the same standard applies to a drill inside a unit that shipped.
- **Dropped:** the `Vocabulaire supplémentaire` block, which opens on printed page 39-9
  and casts the learner as a parent employing a babysitter; its two telephone numbers are
  printed on 39-10.
- **Drills:** twelve, all repetition.

## What did not ship, and why

| Unit | Why not |
|---|---|
| 12 Taking the Metro — Getting your Tickets | **Approved at screening, dropped at conversion.** Its conversation is two lines long, and the course says why: *"There's only a very short conversation in this lesson because people seem to speak as little as possible in the metro."* That is a fragment, and the method says to drop a fragmentary unit rather than patch one. It is also the one unit this app could not have taught: a lesson is only recorded as done once enough of it has been said **and** the learner has answered back at least three times, and a two-turn dialogue cannot supply that. The rest of the unit is good material — the ordinal numbers on printed page 12-6 are the best number drill in the volume — and a later task that wants a numbers lesson should come back for them. |

Unit 12 has been removed from the approved-unit list in `tests/approved_units.py`, because
that list is the set of units a shipped lesson may cite and nothing cites unit 12 now. The
screening record above still shows it shortlisted, which is the honest history: it passed
the screen, it passed reading, and it failed conversion.

The other 34 units of the volume were dropped at screening, and each is accounted for in
the table further up this file.

## Text that nobody printed

Everything a learner hears in these five lessons is either a sentence printed in the
course or an English note written here. The two categories are kept apart on purpose.

**Printed in the course, and unchanged except where this log says otherwise:** every
French sentence in every `turns` entry, and every `target_text` in every drill. Where a
drill's target is shorter than a dialogue line, it is a phrase lifted whole from that
line or from the unit's own vocabulary list — never a sentence assembled here.

**Written here, and in English only:** every `objective`, every `title`, every
`english_gloss`, and all 26 usage notes. No French was invented to fill them. Where a
note needed a French example, it quotes one that is already in that unit.

**Where a shipped French string is permitted to come from** — an allow-list, because a
list of exceptions is only ever as complete as the last person to check it:

1. A dialogue line, verbatim.
2. A vocabulary entry from that unit's `A CLOSER LOOK` or `More Words` list, with a
   printed gender marker turned into its article (`vêtement (le)` → `le vêtement`).
3. That unit's French `SETTING`, which is where `la boulangerie` comes from.
4. `francs` → `euros`, the one word-level substitution, recorded per lesson above.
5. A printed form collapsed to the one a voice can say: `Bonjour Madame/Monsieur.` →
   `Bonjour Madame.` in Lessons 1, 2 and 4, and the speaker label
   `L'employé de S.O.S.:` → `L'employé` in Lesson 5. This is the convention stated near
   the top of this section; it is repeated here because a list written to be exhaustive
   has to carry it, and three shipped turns depend on it.
6. An abbreviation expanded to what is said aloud: `S.V.P.?` → `s'il vous plaît?`, once,
   in a Lesson 5 drill.
7. A clause or phrase taken whole out of a dialogue line and drilled on its own, with its
   first letter capitalised where it now starts the line — `Qu'est-ce que vous avez?`,
   `et avec ça?`, `première porte à droite`. No rule covered these until review found
   them, which is the argument for listing the strings rather than trusting the rules.

**Every shipped string that is not a whole printed sentence, listed rather than
counted.** Of the 107 shipped French strings — 47 turns and 60 drill targets — **76 are
whole sentences exactly as printed in a dialogue line**: all 47 turns, plus 29 drill
targets that repeat one of those sentences entire. The other **31 drill targets are
shorter than a sentence**, and here they all are:

| Lesson | Shorter-than-a-sentence drill targets |
|---|---|
| 1 | `Qu'est-ce que vous avez?`, `J'ai ces deux costumes.`, `le vêtement`, `le costume`, `la cravate`, `la chemise` |
| 2 | `Je n'ai pas assez d'argent.`, `et avec ça?`, `la boulangerie`, `le pain de campagne` |
| 3 | `Combien de tomates?`, `Vous en voulez?`, `Je ne mange jamais d'ail.`, `Hum! Et un kilo de raisins et deux pamplemousses.`, `la laitue`, `le pamplemousse` |
| 4 | `Ah, oui! Entrez.`, `Je voudrais la mettre ici, dans ce coin.`, `l'armoire`, `l'ascenseur`, `le bon de livraison` |
| 5 | `Allô, oui. Je vous écoute.`, `Je ne peux pas rentrer.`, `J'ai besoin d'un serrurier.`, `Votre adresse, s'il vous plaît?`, `première porte à droite`, `On vous envoie une voiture dans 45 minutes environ.`, `la clef`, `le serrurier`, `l'appel`, `la porte` |

Each one is a clause or phrase taken whole out of a dialogue line under rule 7, or a
vocabulary entry under rule 2, or — for `la boulangerie` — the SETTING under rule 3, or
the one expansion under rule 6. **The list is given rather than a tally because a tally
is what went wrong here twice**: an earlier version of this paragraph named four such
strings when there were far more, and the version after it said sixteen because it
classified by final punctuation, which counts `et avec ça?` as a sentence.

Rule 6 is that Lesson 5 drill, `Votre adresse, s'il vous plaît?`: a tutor says the
phrase rather than the letters, and usage note 25 tells the learner that S.V.P. is how it
is written short.

## What the accents cost, measured on this course

Measured over what actually ships, not over the volume. The 47 shipped turns carry
**40 accented characters**: 18 graves, 10 acutes, 6 circumflexes and 6 cedillas, across
38 accented word-occurrences.

Every one of those was checked against a crop of its own page rendered at **300 dpi** —
ten crops, one for each of the ten printed pages the five dialogues span. That is
higher than the 140 dpi the method asks for and it was necessary: at 140 dpi an acute and
a circumflex over the same letter are hard to tell apart on this scan, and at 300 they
are not.

**The correction count: 27 of the 38 accented word-occurrences.** That is how many are
rendered with the wrong accent by the text layer somewhere in their own unit — `Très` as
`Três`, `après` as `aprês`, `désirez` as `dêsirez`, `ça` as `Ca`, `Voilà` as `Voilâ`,
`Sévigné` as `Sêvignê`, `première` as `premiêre`, and `à` as a bare `A` or `a`. Eleven
occurrences the text layer happens to get right. One — `très` in unit 17 — has no correct
rendering anywhere in its unit, so nothing short of the page would have settled it.

**The OCR text layer is wrong about `très` in all three places it ships.** The page
prints `Très` and `très` with a grave every time. The text layer renders the one on
printed page 14-1 as `Tr~s` — the accented letter replaced by a bare tilde — and the ones
on 14-2 and 17-2 as `Três` and `três`, with a circumflex. One loss and two swaps, and no
correct rendering among them.

It does spell `Très` correctly once, on 14-2 — but that occurrence is inside the
Fill-in-the-Blanks exercise, which does not ship. A spot-check that happened to land on
it would have licensed trusting the text layer for this word, and every shipped instance
would still have been wrong.

**Both failure modes are in this scan, and the earlier draft of this section named only
one.** Across the five shipped units the text layer carries **84 `~` characters**, spread
over 73 whitespace-separated tokens — 52 of those have the tilde sitting among letters,
where an accented character belongs, and the other 21 are tilde-only runs. Against that
it carries **141 circumflex characters**, most standing where the page has an acute or a
grave. So it **loses** accents and **swaps** them, in
roughly comparable quantity. The method's 19% figure is Italian's, measured on Italian
FAST over printed pages 40 to 160, and it counts losses; it is not a figure this course
can inherit in either direction.

The swap is the more dangerous of the two, because a `~` is visible as damage and
`três` is not. The same swap is in material that did not ship, which is how it was first
noticed: `rêpond` for `répond` in unit 12 and `biêre` for `bière` in a unit 17 drill.

**What this measurement does not cover.** It compares the shipped lines against the text
layer of their own units, and it speaks for those 47 turns only. The drill targets and
the vocabulary items were read on the same page images but are not in this count. No
claim is made about the accent fidelity of the other 35 units, which were never read at
300 dpi.

## Seeding, and the renumbering that is not the one the task expected

Step 7. The five converted lessons take positions 1 to 5, and the six placeholders move
up behind them.

**They move to 6-11, not 7-12, and that difference is the whole of this step.** Italian,
Spanish and Portuguese each shipped six units, so their placeholders moved 1-6 → 7-12 and
the two ranges never touched: whatever order the statements ran in, every destination was
already free. French ships five, so its placeholders move 1-6 → **6-11**, and the ranges
**overlap at position 6** — `fr-06-daily-routine` is sitting on it at the moment
`fr-01-greetings` has to claim it.

`UNIQUE (language_code, position)` is checked per statement rather than at commit, so that
collision is real. It is survivable only because `store._seed` moves the highest position
first: 6 → 11, then 5 → 10, down to 1 → 6, which lands on a position vacated one statement
earlier. That descending rule was written for Spanish and has been carried ever since;
**French is the first language whose upgrade actually depends on it** rather than merely
being consistent with it.

**What was executed, rather than reasoned about:**

- A database was seeded, wound back to the real pre-change state — version 8, the six
  placeholders on 1-6, no converted French rows, and a learner holding a completed result
  on `fr-02-introductions` — and then **the app was started against it**. It upgraded to
  version 9 in one pass, the eleven positions came out contiguous, the learner's completed
  result survived the move, and their next lesson became `fr-fast-01-at-the-dry-cleaner`.
- Reverting the `SEED_VERSION` bump alone fails exactly one test, and it fails naming the
  reason: *"SEED_VERSION is 8 but the newest shipped catalog is 9."*
- `test_renumbering_a_placeholder_downwards_is_the_hazard_to_watch` now runs for French as
  well, because it is parametrised over whichever languages ship converted units. Removing
  `UNIQUE (language_code, position)` from the schema makes it fail with
  *"DID NOT RAISE IntegrityError"* — so it is a live witness rather than a test that would
  pass on any database.
- A new case in `test_the_upgrade_every_installed_robot_will_actually_take` covers the
  overlapping range. Adding it took two hard-coded sixes out of that test, and its own
  rewind then hit the same collision in mirror image and had to be made self-clearing too.

**The task asked for 7-12.** That instruction was written expecting six converted units,
and following it literally would have left position 6 empty.

Be precise about what that breaks, because the obvious answer is wrong. `NEXT_LESSON_SQL`
orders by position and takes the first row, so it tolerates a gap: a 7-12 layout was built
and walked, and a learner still reached all eleven lessons in the right order. What a gap
actually breaks is the repository's own contiguity invariant, which is asserted in six
places, each checking `positions == list(range(1, len(positions) + 1))`:
`test_lesson_positions_are_contiguous_and_unique` in `test_learner_schema.py`, one
catalog-ordering test per converted language in `test_converted_lessons.py`, and the
upgrade test's own check that no position was left doubled or vacant. The one that would
actually catch a French gap is
`test_the_french_catalog_is_ordered_and_leads_with_the_converted_units`. So 6-11 is
required by a rule this repository enforces, not by the query behaving badly.
