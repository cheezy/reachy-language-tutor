# Curation log: FSI Metropolitan French FAST, Student Text

What was taken from this course, what was changed on the way, and what was left behind.

A judgement nobody can review is not a judgement. This file exists so that a person can
read a line a robot says in somebody's house, find the page it came from, and see what
was done to it in between. Every claim here was checked in the session that made the
change; where something was **not** checked, it says so.

The method these decisions were applied with — the commands, the page-image check, the
order of the steps — is in [converting-a-course.md](converting-a-course.md). This file is
the record of the decisions themselves.

**Status: Steps 0 to 2 of the method are done — the course is chosen, fetched and
checked, and the rights position is written. No unit has been screened, read or
curated.** The unit-by-unit sections that the Italian, Portuguese and Spanish logs carry
are absent here because that work has not happened yet, not because it was skipped.

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
