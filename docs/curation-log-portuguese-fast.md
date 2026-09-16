# Curation log: FSI Brazilian Portuguese FAST, Volume I

What was taken from this course, what was changed on the way, and what was left behind
will live here once curation happens. Today the file records two things only: where the
course came from, and what its rights position is.

A judgement nobody can review is not a judgement. This file exists so that a person can
read a line a robot says in somebody's house, find the page it came from, and see what
was done to it in between. Every claim here was checked in the session that wrote it;
where something was **not** checked, it says so, in the same voice and at the same
length.

`docs/converting-a-course.md` is the method. This file is the record of the decisions
themselves.

**Status: Steps 0, 1 and 2 of the method are done — the course is chosen, fetched and
checked, and the rights position below is written. Mapping the volume, screening units,
per-unit curation and the `converted_lessons.json` entry are out of scope for the task
that wrote this and have not been started. No Portuguese lesson has been converted.**

## The source

| | |
|---|---|
| Course | *Brazilian Portuguese — Familiarization & Short-Term Training*, Volume I |
| Publisher | Foreign Service Institute, U.S. Department of State |
| Authors (title page) | Neire Barim de Souza Johnson, Stephen Zappala |
| Year | **not printed anywhere in the file** — see "No publication year is printed" below |
| File | `FSI - Portuguese FAST - Volume 1.pdf` |
| Fetched from | `https://fsi-language-courses-media.nyc3.cdn.digitaloceanspaces.com/languages/Portuguese/FAST/Volume%201/FSI%20-%20Portuguese%20FAST%20-%20Volume%201.pdf` |
| Fetched | 2026-09-15 |
| Size | 16,298,883 bytes, 329 pages |
| SHA-256 | `a918ee73bfdf44cbc1370f279b738360262b465841a602e8a2e792d48daff59a` |

**The integrity check, item by item.** A `HEAD` was issued before anything was
downloaded and returned `HTTP/2 200`, `content-type: application/pdf`,
`content-length: 16298883`, `last-modified: Sun, 15 Aug 2021 17:32:27 GMT` and
`etag: "c6f6f8df5cd8ab5d47c24f03e7df46ee"`. The file was then fetched and came to
16,298,883 bytes on disk. Its first eight bytes are `%PDF-1.6`
(`255044462d312e36`), and `file --mime-type` agrees with the header. `pdfinfo` reports
`Author: Foreign Service Institute` — not a reseller — with `Title: FSI - Portuguese
FAST - Volume 1`, `Subject: Portuguese language`, `Pages: 329`, `Encrypted: no`,
`JavaScript: no`, `Tagged: no`, `Suspects: no`, PDF version 1.6.

**On the size and page count, what was actually available to check against.** The
bucket listing for this key reports `Size 16298883` and the same ETag and
last-modified as the `HEAD`, so the listing, the header and the bytes on disk agree
three ways. There was **no catalogue page count to compare 329 against** — `plan.md`'s
coverage table records only which course directories exist for Portuguese, not their
sizes — so the page count is recorded, not corroborated. That is a weaker check than
the one the Italian log describes, and saying so is the point of writing it down.

**Scripts and embedded actions.** `pdfinfo` reports no JavaScript, and that was
corroborated directly rather than taken on trust: every Flate-compressed stream in the
file was inflated — 459 of its 814 streams — and the whole byte-space, compressed and
not, was searched for action tokens. The 355 that do not inflate are the scan's image
data: 318 JBIG2, 32 JPEG, one CCITT fax, and four whose filter this check did not
identify. `/JavaScript`,
`/JS`, `/Launch`, `/OpenAction`, `/AA`, `/EmbeddedFile`, `/RichMedia`, `/SubmitForm` and
`/GoToR` all occur **zero** times. `pdfinfo` reports `Form: AcroForm`, but the form
carries no fields: `/Widget` occurs zero times too. The only action objects in the file
are URI links, and there are 36 distinct targets — 31 Wikimedia Commons file pages and five
`creativecommons.org` addresses (four versioned licence deeds and one bare
`http://creativecommons.org/`), and nothing pointing anywhere else. What those
links are doing in an FSI course is a rights matter, and it is the largest thing in the
next section.

**The scan.** `pdfinfo` gives the producer as "Adobe Acrobat 9.33 Paper Capture
Plug-in" — that is the OCR step — with a creation date of 2010-07-04 and a modification
date of 2010-08-06. Those dates belong to the digitisation, not to the course.

### Why FAST, and why Volume I

The CDN prefix `languages/Portuguese/` was listed rather than read out of `plan.md`,
because that table says of itself that it is a snapshot of a mutable third-party bucket
and should be re-run. The listing returns **144 keys in exactly three directories** —
`FAST`, `Programmatic` and `Spanish to Portuguese` — with no loose objects sitting
directly under the language prefix. The listing was taken **without** a delimiter, so
it enumerates every key beneath the prefix rather than only the directory names — which
is the precaution `docs/curation-log-spanish.md` records the reason for, its own FAST
having been a loose object that a directories-only listing missed.

**There is no FSI "Basic" course for Portuguese.** Step 0 of the method says "FSI Basic
or FAST. Never Headstart," and for this language that resolves to FAST by elimination
rather than by a close call. Headstart does not arise either: the bucket holds none for
Portuguese, and `plan.md`'s own coverage row lists none, unlike the Italian and German
rows beside it.

FAST ships as two volumes and no more, and Volume I was taken as "a sensible unit of
work" in Step 0's own words — the same unit Italian was taken in. (Spanish is not a
parallel: its FAST is a single file with no volumes at all.)

The two alternatives, named and rejected:

- **Programmatic** is not what Step 0 asks for, and it is published as 52 separate
  component PDFs — units, introductions, an instructor's manual and a vocabulary —
  alongside two combined volumes, rather than as one course-sized book.
- **Spanish to Portuguese** is a bridge course for people who already speak Spanish,
  and so not a beginner course. The Portuguese syllabus this app already carries starts
  at `pt-01-greetings` and runs to `pt-06-daily-routine`
  (`learners/store.py:306-335`) — a beginner is who it is written for.

**On the variety.** The catalog this app shows a learner advertises plain "Portuguese"
(`reachy_language_tutor/src/reachy_language_tutor/learners/store.py:129`, and the same
bare label in the `get_progress` and `start_lesson` tool enums). Every Portuguese course
line in the bucket is Brazilian, the audio files sitting beside this PDF in the same
directory are named `FSI - Brazilian Portuguese FAST - Lesson 01A.mp3` and so on,
and the title page of this volume says
**BRAZILIAN PORTUGUESE** in the largest type on the page. So the material a Portuguese
learner would get is Brazilian, and nothing a learner or a parent can see says so. That
is a statement of fact about the source and the label; **it is not fixed here**, and the
task that wrote this file could not fix it — its scope was one document. See "What this
log does not settle" at the end.

## Rights: what was actually checked

**Checked, in the file itself:**

- **The method's own six literals return no copyright notice.** `converting-a-course.md`
  gives the search as `copyright|©|all rights|public domain|contractor|reproduc`, and
  that exact line was run over the extracted text layer. It returns **20 hits, and all
  20 are the term `public domain`**. Per term: `copyright` 0, `©` 0, `all rights` 0,
  `contractor` 0, `reproduc` 0. Every one of the 20 was read, and every one is the
  words "Public Domain" or "US Public Domain" in an image credit — either a caption
  under a photograph in the body, or a licence cell in the image appendix described
  below. There is no copyright notice among them and no mention of a contractor.

- **A short-fragment search, because a literal is only as good as the scan.** Counts
  below are occurrences, not matching lines, and every hit was read: `opyri` 0,
  `opyr` 0, `erved` 10 (two hotel-reservation drill lines — "reserved a room",
  "reserved for a room used for sleeping purposes" — and eight of the ordinary verb
  "served" in the food and culture notes), `ublic.{0,3}domain` 20
  (the same credit captions), `ontract` 7 (every one the word *contraction* in a note
  about Portuguese grammar — `desse`, `daqui`, `do`, `da`, `deste`), `icens` 21
  (**all 21 inside the image appendix** — twenty `licenses/by…` URLs and the table's
  own "License" column header; zero anywhere else),
  `permis` 5 (three Portuguese `permissão` and two English `permission`, all in the
  same asking-permission drill), literal `(c)` 1
  (a drill label, "(c) para quantas pessoas"), `printin` 0, `edition` 0, `reissu` 0.

- **A damage-tolerant matcher, and it returns zero for `copyright`.** The matcher models
  the confusions this class of scan makes (`o`→`0`, `y`→`v`, `r`→`n`, `i`→`l`, `t`→`f`,
  `e`→`c`, `h`→`b`, `s`→`5`, `u`→`n`, `a`→`o`, `g`→`9`, `q`→`g`) at every position and
  additionally allows any one character to be deleted, case-insensitively.
  **`copyright`: zero matches.** Also `printing` 0, `reprint` 0 and `superintendent` 0 —
  this volume carries no Government Printing Office imprint, which is a difference from
  the Spanish Basic course and nothing more than a difference. `reserved` returns 5,
  and here are all five: the two reservation drills above, "called to reserve", a
  grammar note on using a present-tense form "to indicate reserve or deference", and an
  advertisement on page 44, which the text layer renders as
  `RESERVE"JÁ E TENHA}Q% DE DESCONTO,`. Read as a page image it is a Hotel Fazenda de
  Vieira advertisement saying "RESERVE JÁ E TENHA 20% DE DESCONTO" — "book now and get
  20% off" — with the OCR having eaten the `2` and turned the `0%` into `}Q%`.
  `contract` returns 11: the seven grammatical contractions above plus four lines about
  social and eye *contact*.

  `licens` returns 38, and they divide exactly: 21 are in the appendix (twenty URLs and
  its "License" column header), 14 are the
  Portuguese `Com licença` ("excuse me") of the asking-directions lessons, and 3 are
  fuzzy false positives — the matched substrings are `lices` from "slices", `liens`
  from "Brasiliense", and `icers` from "O fficers", as the OCR splits it. `rights`
  returns 50, and every one was read: 36 are the ordinary word "right", 9 are "night",
  3 are "sightseeing" and "lights", 1 is "nights", and **the single literal `rights` in
  the whole volume is a culture note about traffic in São Paulo** — "pedestrians have
  no rights". `permission` returns 2.

  **What that matcher does and does not reach, measured rather than asserted.** Against
  a `Copyright` control it catches all nine single-character deletions, and catches
  `C0pyright`, `Copvright`, `Copyrlght`, `Copyrigbt`, `Copright`, `CopyrIght`,
  `C0pynght` and `COPYRIGHT`. It **misses** `Cop yright` (an inserted space), a bare
  `(c) 1961`, `Cop0right`, `Copyxight`, `Cqpyright`, and **233 of 315** arbitrary
  single-character substitutions — because it models plausible OCR confusions, not every
  possible corruption. A zero from it is worth having and is not proof.

- **So the pages were read as images, and that is the load-bearing evidence.** A notice
  set as an image is invisible to every search above. Five pages were read at the
  conventional notice locations: the printed
  cover (FSI seal, "FOREIGN SERVICE INSTITUTE / U. S. DEPARTMENT OF STATE", no
  publisher, no notice, no year); the title page (course title, both named authors, the
  FSI seal dated 1946, the same two institutional lines — **no copyright notice, no
  publisher imprint and no year**); **the verso of the title page, where a notice
  conventionally sits, which is completely blank**; the preface; and the final leaf,
  which is the end of the image appendix and carries no colophon.

- **And every page that the text layer could not see was accounted for.** Ten pages
  yield no extractable text at all — 3, 5, 17, 49, 75, 185, 213, 237, 287 and 305 — the
  likeliest place for an image-only notice to hide, though not the only one. All ten
  were rendered and measured for ink, and the measurement is stated so that it
  reproduces: rendered at 65 dpi, where poppler lays down a uniform background of 254
  rather than 255, counting subpixels that differ from that background. Eight pages
  carry **zero**. Pages 185 and 287 carry **9 and 12** — three and four pixels, one
  speck each — and both were re-rendered at 150 dpi and read as single scanner specks
  on otherwise empty pages. (An earlier draft of this line said "nine and six" and
  called the test "non-white". Six is what page 287 gives at a threshold of "darker
  than 240", which the line did not state; the finding is the same either way, but the
  number was not reproducible as written.) **Every one of the 329 pages therefore
  either carries extractable text that was searched, or was measured and read as
  blank.**

  What that does *not* cover, and it belongs here rather than being left to inference:
  319 pages carry text, and **314 of them were searched as text but never looked at as
  images**. A notice stamped or pasted beside body text would not have been caught.
  The five that were: the cover, the title page, the preface and the final leaf — four
  of the five conventional notice locations, the fifth being the blank verso, which
  carries no text — plus page 44, read later to settle what the advertisement above
  actually says. **Eight pages were looked at as images altogether**: those five, the
  verso, and the two speckled pages. Fifteen were *rendered*; the other seven were
  ink-measured and not looked at, which for a page that measures identical to its own
  background is the stronger check rather than the weaker one.

- **The preface reads as in-house work, but "FSI staff throughout" would overstate it,
  and the count is worth having.** Twenty-two people are named. **Two** carry a title
  with the institute in it: John McClelland, "Head of the FSI Audio-Visual Unit", and
  Mark C. Lissfelt, who signs in manuscript as **Dean, School of Language Studies,
  Foreign Service Institute, United States Department of State**. **Four more** carry a
  job or course title with no employer named — Neire Barim Johnson, "Portuguese Language
  and Culture Instructor", who wrote the course; Stephen Zappala, "Chairman of the
  Department of Romance Languages", who supervised it and wrote the Language Notes and
  Thought Translation sections; Jane Kamide, "Section Head"; and Martha Gowland,
  "Consultant for the text… FAST Course coordinator". **The remaining sixteen** are
  given a task and no title at all: the three editors, the assisting typist, the two
  studio technicians, the two artists — Anne Meagher-Cook, who did the art work, and
  Roberto Kamide — and the **eight** voicing artists and field testers not already
  counted above: Carmen Alves, Cassio Castro Miranda, Marcio Dos Santos, Zoe Green,
  Silede Gross, Walber Marinho, Maria Ryan and Marisa Werlang. That is 3 + 1 + 2 + 2 +
  8 = 16. The roles overlap in the preface itself, which is why the sixteen is a set
  rather than a sum of role counts: the page names four field testers and ten voicing
  artists, three people do both, so those two groups are eleven people — and three of
  the eleven (Neire Johnson, Stephen Zappala, Jane Kamide) carry titles and are already
  among the four. Eleven minus three is the eight named above.

  What does place the work inside the institute is not the roster. **One** sentence
  gives a location: the recordings "were made in the Foreign Service Institute (FSI)
  Language Laboratory studio". The art-work sentence gives none. It reads "The art work
  was done by Anne Meagher-Cook, with contributions from Roberto Kamide, in consultation
  with John McClelland, Head of the FSI Audio-Visual Unit" — so it does say *by whom*,
  and neither of those two is placed inside the institute by anything on the page; the
  only institute connection it draws is a consultation with a unit head, which is
  weaker evidence of in-house production than work done in an institute studio.

  The same Dean signed the Italian FAST, and `curation-log-spanish.md` records the same
  Stephen Zappala as Chairman of the Department of Romance Languages on the Spanish
  FAST.

  An earlier draft of this bullet called Martha Gowland "the exception" — the one name
  not covered by an institute title. That was wrong, and wrong in the direction that
  flatters the rights position: she is one of twenty, and better identified than most,
  since she at least carries a programme title. **The contractor question the method
  warns about is not one line. It is the twenty people whose employer the page never
  states — the sixteen with no title and the four, Gowland included, whose title names
  no employer — and this log resolves none of them in either direction.**

- **The acknowledgements are inside the preface, and there is no separate section.** The
  table of contents lists exactly three items before the lessons — Preface, "FAST
  Methodology — Step Approach", and "Brazil — A brief Overview" — and the credits
  material ("Special thanks go to Zoe Green, Silede Gross, and Marisa Werlang…", "We are
  grateful to the many generations of language students…") sits in the preface itself.
  So the acknowledgements were read; they are the page already quoted.

- **This scan announces itself as an edited edition, and that is the largest finding
  here.** At the foot of the preface page, below the Dean's signature block, set in red
  where the whole rest of the page is black, is an unsigned line: *"Note - The original
  photographs have been replaced with equivalent Creative Commons photographs."* It was
  read as a page image and measured: rendered at 150 dpi and counting a pixel as red
  when R − max(G,B) > 60 and R > 110, there are 4,887 red pixels, all of them in rows
  1515–1537 of 1650 — a single line of text at 92% of the page height, and the only red
  ink anywhere on the page. Nothing in the file
  says **who** made the substitution, **when**, or under what authority.

  The volume closes with an "Appendix – List of Images" enumerating every substituted
  image: **31 rows**, each giving the PDF page, the document page, the Wikimedia Commons
  file, the licence, and any modification. Ten rows read "Public Domain", one reads
  "Attribution", and **twenty are Creative Commons** — 11 CC BY 2.0, 4 CC BY-SA 3.0, 3
  CC BY-SA 2.0 and 2 CC BY-SA 2.5. Four rows record a modification: "Face blurred",
  "Faces blurred", and "Cropped bottom of picture" twice. All 31 Commons file pages are
  live link annotations in the PDF, and the 31 annotation targets match the 31 table rows
  one for one — 29 of them identically, and the other two identically up to the point
  where the table's own column layout breaks the URL across a line.

  **Portuguese does not inherit Italian's position here, and the two notes differ in the
  word that matters.** Italian's note reads "The original photographs have been
  **removed** for this public domain version of the book." This one says they were
  **replaced**. Italian's log could say the photographs "are exactly what is absent from
  the file"; that sentence is false of this volume, which has twenty-one third-party
  photographs present in it — the twenty Creative Commons ones and the row labelled
  "Attribution".

  One observation about what the pipeline does with that, and it is an observation and
  not a conclusion: the conversion method takes dialogue turns, language notes and
  drills — text — and ships no images. Whether that bears on the obligations attached to
  those twenty-one photographs is a question this log does not answer.

- **The metadata names the institute twice and claims no rights anywhere.** The file
  carries **three** XMP packets, at 319, 3,059 and 3,863 bytes. The document-level one
  is the 3,863-byte packet (catalogue `/Metadata`), and it carries `dc:title`,
  `dc:description`, `pdf:Producer` and **`dc:creator` = "Foreign Service Institute"** —
  with no `dc:rights`, no `xmpRights` and no `dc:publisher`. The 319-byte packet is an
  empty Adobe stub with a single bare `rdf:Description`. The Info dictionary has no
  Copyright or Rights key. So the institute is named twice over, in Info `/Author` and
  in XMP `dc:creator`, and nothing in the metadata asserts a right in either direction.

  **The third packet is not the course's metadata at all.** The 3,059-byte packet is a
  photograph's: `tiff:Make` NIKON, `tiff:Model` COOLPIX P5000, `xmp:CreatorTool` "Adobe
  Photoshop CS3 Macintosh", `exif:DateTimeOriginal` 2008-02-12. It carries no licence or
  rights tag of its own. It belongs to one of the thirty-one substituted photographs —
  the very material this log calls its largest open question — though **which** of them,
  and therefore which licence row it falls under, was not established. It is recorded
  here because it is there, not because anything was learned from it. One `Exif` marker and
  four `JFIF` markers also appear in the image data; **that image-level metadata was not
  enumerated** (see below).

- **No publication year is printed.** Not on the cover, not on the title page, not on
  its verso, not in the preface. The only dating evidence bearing on the course text is
  the country-facts page — "Population: (1990) 150 million", "GNP (1988) $340 billion;
  per Capita GNP (1988)", "62.8 yrs. (1981)". Those statistics date the *content*; they
  are not a publication date. Two other dates in the file belong to the digitisation
  rather than the course, and are recorded here so they are not mistaken for it: the
  2010 CreationDate and ModDate in the PDF metadata, and a photo credit on page 238
  reading "Photo Credit: Nasa / Data: 17 November, 1990", which sits on one of the
  substituted images. The Year row in the table above is left unfilled deliberately.

**Not checked, and worth saying plainly:**

- No lawyer has looked at this, and none of the above is a legal opinion.
- **Who replaced the photographs, when, and with what authority.** The note is unsigned
  and undated and the file records nothing further. This is the single biggest gap in
  the section above, and it sits directly under the single biggest finding.
- The attribution and share-alike obligations of the **twenty-one** third-party rows —
  the twenty Creative Commons ones and the row the table labels simply "Attribution" —
  were not analysed, and none of the upstream Wikimedia pages was visited to confirm
  that the licence each row claims is the licence that image actually carries.
- **The employer of the sixteen contributors the preface names with a task and no
  title** — the editors, the typist, the studio technicians, the artists, and the
  voicing artists and field testers not otherwise counted — and of the four who carry a
  job title but no stated affiliation, Martha Gowland among them. Nothing was checked
  about any of them beyond what the page says.
- Whether any individual named contributor assigned or licensed their contribution to
  the institute, or was an employee. Unlike Italian, the question does arise here,
  because this volume has third-party material in it by its own account.
- **The metadata carried by the substituted photographs themselves.** One XMP packet of
  camera and editing metadata was found and is described above; one `Exif` marker and
  four `JFIF` markers also appear in the image data, and none of that image-level
  metadata was enumerated. It is a place a third party's name or a rights string could
  sit, and nobody has looked.
- **314 of the 319 text-bearing pages were searched as text but never inspected as
  images.** A notice set as an image on a page that also carries body text would not
  have been found. Five text-bearing pages were looked at, along with every text-free
  page; the rest were not.
- Volume II was not fetched, opened or examined at all. Nothing here is a statement
  about it.
- There was no catalogue page count to check 329 against, as the source section says.
- Whether the OCR text layer is faithful — in particular whether accents survived, which
  `plan.md` names as the worst failure mode for a language course. Screening is the next
  task's job and nothing here has measured it.
- The CDN object's last-modified date of 2021-08-15 is when this bucket received the
  file. Nothing checked here establishes the chain from the Foreign Service Institute to
  this bucket.

**Neither of the two things that would stop this conversion was found, and here is
that statement at its real strength rather than its most flattering one.** The stop
condition is the task's own — a copyright notice, or a course produced under contract;
the method's Step 2 sets out what to look for and names no stop.

No copyright notice appears in the 329 pages, under three successively looser text
searches plus eight pages looked at as images — the five conventional notice
locations, the two speckled ones, and page 44 — with every text-free page rendered and
measured blank, and 314 of the 319 text-bearing pages searched as text but never
looked at. The preface names twenty-two people; two
of them carry the institute in their title, the recordings are placed in an institute
studio by the page itself, and it is signed by an institute dean. The metadata names
the institute twice.

**Against that: twenty of the twenty-two have no employer stated on the page — sixteen
with a task and no title, four with a title that names no employer — and this log
resolves none of them.** That, and not any single line, is the contractor question left
open here. And the volume is an edited edition by its own unsigned admission, carrying
twenty-one third-party photographs whose obligations nobody has looked at.

That is a description of what was and was not found. It is deliberately not a
conclusion about the law, and it is not a finding that the course is clear.

## What this log does not settle

Two things are named above that a later task has to carry, and neither is done:

1. **The learner-visible variety label.** This course is Brazilian Portuguese and the
   app's catalog says "Portuguese". The label lives in
   `learners/store.py:129` and in the `get_progress` and `start_lesson` tool enums. This
   file is a developer document; a learner and a parent never see it, so mentioning the
   variety here does not discharge the requirement that they be told.
2. **Steps 3 onward** — mapping the volume, screening units, per-unit curation and the
   `converted_lessons.json` entry, including the `rights` value that entry has to carry.
   When that value is written, it should be a summary of the section above: a
   description of what was found, not a conclusion about the law.
