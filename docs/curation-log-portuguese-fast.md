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

**Status: Steps 0 to 3 of the method are done — the course is chosen, fetched and
checked, the rights position is written, the volume is mapped and every lesson is
screened and judged. Six lessons are shortlisted. Per-unit curation, the
`converted_lessons.json` entry and the `APPROVED_UNITS` registration have not been
started, and no Portuguese lesson has been converted.**

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
twenty-one third-party photographs whose obligations nobody has looked at — **and five
song lyrics reproduced in full, which the screening section below found and this
section did not.** Two are credited to Antonio Carlos Jobim and Vinícius de Moraes, one
to André Filho; see "What reading found that the screen cannot see". Nothing in the
rights work above looked for text that was not the course's own, which is the gap that
section closes.

That is a description of what was and was not found. It is deliberately not a
conclusion about the law, and it is not a finding that the course is clear.

## Screening the volume

Step 3 of the method, and it is two jobs. Map the volume so a printed page number can
be turned into a PDF page number. Then screen every lesson for the vocabulary that
disqualifies it — in Portuguese and in English, because the content is bilingual and an
English-only screen sees half of it — and **read** whatever the screen did not
disqualify. The screen ranks; it does not clear.

### The page map, and why there is no single offset

The method says to find "the offset between printed page numbers and PDF page numbers —
there is always one." **For this volume there is no single offset, and looking for one
would have produced a wrong map.** The table of contents says so itself: *"Each lesson
is paginated separately. On tapes, lessons are referred to by lesson number and page
number."* Printed pages are `lesson.page` — `6.1`, `12.22` — and each of the twelve
lessons restarts at 1, so there are twelve offsets.

| Lesson | Title | Printed | PDF | Pages | Offset |
|---|---|---|---|---|---|
| 1 | Checking in | 1.1–1.31 | 18–48 | 31 | +17 |
| 2 | Ordering Breakfast | 2.1–2.25 | 50–74 | 25 | +49 |
| 3 | Making a Long Distance Call | 3.1–3.30 | 76–105 | 30 | +75 |
| 4 | Inquiring about your Laundry | 4.1–4.24 | 106–129 | 24 | +105 |
| 5 | Checking for Messages | 5.1–5.26 | 130–155 | 26 | +129 |
| 6 | Asking for Directions | 6.1–6.29 | 156–184 | 29 | +155 |
| 7 | Asking for Directions (Inside a Building) | 7.1–7.27 | 186–212 | 27 | +185 |
| 8 | Taking a Taxi | 8.1–8.23 | 214–236 | 23 | +213 |
| 9 | Answering the Phone | 9.1–9.26 | 238–263 | 26 | +237 |
| 10 | Leaving a Message | 10.1–10.23 | 264–286 | 23 | +263 |
| 11 | Making an Appointment | 11.1–11.17 | 288–304 | 17 | +287 |
| 12 | Ordering Lunch | 12.1–12.22 | 306–327 | 22 | +305 |

PDF page = printed page + the lesson's offset. 303 of the 329 pages are lesson pages;
the remaining 26 are front matter and the image appendix.

**The map was built from the file rather than from the table of contents, and then
checked against it.** Every page carries its `lesson.page` marker in the footer; those
were extracted and the spans derived from them, and the resulting lengths match the
table of contents for all twelve lessons. Of the ten blank pages the rights section
above counts, **eight are section dividers standing before a lesson** — PDF 17, 49,
75, 185, 213, 237, 287 and 305, the first of which sits before Lesson 1 rather than
between two lessons. Where a lesson is preceded by a divider its offset is that blank
page's own number, one more than the previous lesson's last page: Lesson 1 ends at PDF
48 and Lesson 2's offset is 49. The four lessons with no divider before them — 4, 5, 6
and 10 — take the previous lesson's last page as their offset. The other two blanks,
PDF 3 and 5, are in the front matter.

**Checked by opening a page, not by arithmetic.** PDF page 156 was rendered and read:
it is printed page `6. 1`, the São Paulo opener of Lesson 6. Three more boundaries were
confirmed the same way — PDF 18 is `1. 1`, PDF 304 is `11. 17`, PDF 327 is `12.22`.

**On naming.** The task that produced this section asks for units named by "the roman
numeral the course itself uses". **This course uses no roman numerals for its lessons.**
It prints `LESSON 8`, `LESSON 12`, and footers `6. 1` and `12.22`. Units are therefore
named here by the arabic lesson number the course itself prints, which is also how
`tests/approved_units.py` already keys the Spanish course.

### The screen, published so the counts reproduce

The method's exclusion table is given in Italian and English. **There is no Portuguese
row anywhere in `converting-a-course.md`**, so these terms were derived, and they are
printed here because a count nobody can reproduce is not a count.

Matching is on **stems**, not whole words, and is **accent-insensitive**, because this
is an OCR text layer that drops and mangles accents:

| Family | Portuguese and English stems |
|---|---|
| `mil` | militar, exercito, quartel, quarteis, soldado, marinha, aeronautica, tropa, sargento, coronel, tenente, almirante, forcas armadas, armada, arma de fogo, guerra, caserna, batalhao, regimento, patente · military, army, navy, air force, soldier, barracks, sergeant, colonel, admiral, lieutenant, troops, armed forces, warfare, regiment, battalion |
| `emb` | embaixad, consulad, consul, diplomat, chancelaria, adido, legacao · embassy, consulate, chancery, attache, foreign service, ministry of foreign |
| `unif` | polici, policia, delegacia, delegado, guarda, fiscal, inspetor, inspector, autoridade, agente da lei, oficia, xerife, patrulha · police, officer, constable, patrol, sheriff, authorit, official |
| `bord` | alfandeg, fronteir, passaport, imigra, despachante, visto de, declarar na, isencao, franquia · border, frontier, customs, passport, visa, immigration, declare, duty-free, duty free, port of entry |

Four terms were kept **out** of the pattern on purpose, following the rule the Spanish
log records after its own screen over-fired:

- **`geral` / `general`** — an ordinary Portuguese adverb stem; `generalmente` would
  score every lesson. Spanish excluded its equivalent for the same reason.
- **`capitao` / `captain`** — also a ship's or a team's captain; it would have ranked the
  taxi lesson as military.
- **`servico`** — `serviço militar` matters, but `serviço` alone is room service and
  laundry service, which is most of this volume.
- **bare `visto`** — the past participle of *ver*, "seen". Only `visto de` (entry visa)
  is screened.

Those omissions cost nothing measurable here: across the volume, `servico` and bare
`visto` add no hit the pattern misses, `capita` finds only "private capital", and the
ranks the pattern genuinely does not name — Major, Marechal, Fuzileiro Naval — occur
only in Lesson 9, which the screen and the reading both caught anyway.

**How a match is classified.** Each match is judged by the **word it sits inside**, not
by the line around it. That matters, and the first version of this section got it wrong
in both directions: an accented host (`consultórios`) slipped past the filter, and an
innocent word elsewhere on the line (`aguardando`) cancelled a real hit (`Consul Geral`).
The hosts treated as innocent, each read on the page first, are: `consultorio(s)`,
`consultoria`, `televisao`, `visao`, `avisar` and its forms, `bordered`, `attached`, and
`aguardar` and its forms. Two whole lines are excepted because the host word is itself
a proper name: the surnames *Silveira* and *Guerra*, two adjacent columns of a
pronunciation list at printed 11.8, and the CMTC
trolleybus paint scheme *"white, navy and blue"*.

**One of those two exceptions did not fire when it was first written, and the reason is
worth keeping.** The OCR renders the surname column with run-together spacing —
`Silveira          Guerra` — while the rule tested for the single-spaced literal, so it
never matched and Lesson 11's single `mil` hit was counted as a military term rather
than as the surname it is. The published rule was not the rule that ran. It is fixed by
collapsing runs of whitespace in the line before the test, and it is recorded because
this repository has a name for it: a guard has to mean the same thing as the code it
protects. The table below is the run after the fix; Lesson 11 moves from 13 lines to
12.

### What the screen found, and what reading found after it

Three numbers per lesson. **Raw** is every match. **FP** is the matches thrown out
because the host word is innocent. **Lines** is the distinct lines that remain — the
unit of work for whoever has to replace them.

| Lesson | Title | Printed | mil | emb | unif | bord | Raw | FP | Lines | Outcome |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Checking in | 1.1–1.31 | 1 | 41 | 0 | 13 | 55 | 0 | 46 | dropped |
| 2 | Ordering Breakfast | 2.1–2.25 | 0 | 8 | 0 | 6 | 14 | 1 | 10 | **SHORTLIST** |
| 3 | Making a Long Distance Call | 3.1–3.30 | 0 | 14 | 0 | 6 | 20 | 1 | 13 | dropped |
| 4 | Inquiring about your Laundry | 4.1–4.24 | 0 | 6 | 0 | 8 | 14 | 3 | 10 | dropped |
| 5 | Checking for Messages | 5.1–5.26 | 0 | 1 | 0 | 2 | 3 | 1 | 2 | **SHORTLIST** |
| 6 | Asking for Directions | 6.1–6.29 | 0 | 0 | 0 | 4 | 4 | 2 | 2 | **SHORTLIST** |
| 7 | Directions (Inside a Building) | 7.1–7.27 | 0 | 17 | 0 | 1 | 18 | 10 | 5 | **SHORTLIST** |
| 8 | Taking a Taxi | 8.1–8.23 | 1 | 3 | 0 | 0 | 4 | 1 | 3 | **SHORTLIST** |
| 9 | Answering the Phone | 9.1–9.26 | 20 | 8 | 5 | 4 | 37 | 6 | 26 | dropped |
| 10 | Leaving a Message | 10.1–10.23 | 9 | 38 | 1 | 1 | 49 | 2 | 32 | dropped |
| 11 | Making an Appointment | 11.1–11.17 | 1 | 12 | 1 | 0 | 14 | 1 | 12 | dropped |
| 12 | Ordering Lunch | 12.1–12.22 | 0 | 2 | 0 | 0 | 2 | 0 | 2 | **SHORTLIST** |

**Lesson 7 is why the screen is a ranking and not a verdict.** It scores 18 raw — the
fifth highest in the volume — and **nine** of those matches are the word `consultório`,
"doctor's office", which contains `consul`. Read on the page, Lesson 7 is a person
looking for a lawyer's office in a twenty-five-storey building and giving up on the
lift. It is one of the cleanest units here, and a screen read as a verdict would have
dropped it for a word that means the opposite of what the pattern was looking for.

**And the same unit shows the screen's other failure mode.** Twice — on PDF 190 and
PDF 199 — the OCR renders the word split across a space, as `consu Itórios` and
`consu Itório`. Those are a tenth and eleventh occurrence and the pattern does not see
either. A stem list misses what the scan breaks, which is the same reason the rights
section above had to read pages as images.

The other false positives the published pattern produces, all read in context:
`televisão` and `avisar` matching `visa`; the magazine *Visão*; `bordered by Avenida
Atlântica` matching `border`; `attached to words` matching `attache`; `aguardando`
matching `guarda`; the trolleybus *"white, navy and blue"*; and the two adjacent
surnames *Silveira* and *Guerra*. There are 28 of them in the volume out of 234
matches; the remaining 206 matches fall on 163 distinct lines.

**Nothing in this volume scored zero.** The method's instruction is to re-read a
zero-scoring unit specifically for a miss; no such unit exists here, so that check was
run against the lowest instead. Three lessons tie at two distinct lines — 5, 6 and 12
— and all three were read end to end; Lesson 12 is also the lowest on raw matches, at
two against Lesson 5's three.
What it found is in "What reading found that the screen cannot see" below, and it is
the most useful thing in this section.

### The shortlist — six lessons, each read in full

**The rule, stated so it can be argued with.** A lesson is approved when **(a)** its
dialogue — the part a robot would actually speak with a learner — is clear of all four
families; **(b)** its premise, the *Setting the Scene* paragraph, is clear of them too;
and **(c)** every remaining contaminated line is individually enumerated below, with
**where it sits** recorded, because a line of running prose in a Cultural Note costs
more to replace than an item in a drill list. It is not a threshold on the count, and the
table proves it in the sharpest possible way: **Lesson 4 is dropped at 10 lines and
Lesson 2 is approved at the same 10** — an exact tie with opposite outcomes, decided by
clause (b). Lesson 3 is dropped at 13 despite a dialogue as clean as any approved
lesson's.

Every approved lesson was read page by page, all **152** pages. **One** dropped
lesson, Lesson 3, was read in full as well, which makes **182 of the volume's 303
lesson pages read end to end**.

**Lesson 12 — Ordering Lunch (printed 12.1–12.22, PDF 306–327). 2 lines, both drills.**
A person orders a sandwich and a juice in a *lanchonete*, is told the roast beef has run
out, asks what a *bauru* is, and pays at the till. It carries a full menu as additional
vocabulary — juices, sandwiches, ice creams — and a Cultural Note on Brazilian dining
etiquette and on how to offer and refuse food politely. The two lines: *"Como é o
Embaixador?"* as a Language Note example of *Como é …?*, and *"… à festa da embaixada?"*
as one item of **four** in a Brief Exchange — the others being a
*reunião*, a *cerveja* and *comer mais um pouco*.

**Lesson 6 — Asking for Directions (6.1–6.29, PDF 156–184). 2 lines, one drill item.**
A person asks a passer-by the way to a bank and is walked through it street by street.
Streets, turnings, blocks, landmarks, distances. The two lines are one drill item that
the page layout splits across two lines — *"… alfândega?"* and its gloss *"(customs)"*
— the customs house appearing once among **five** places in drill 8, the others being a
church, a *largo*, a museum and a theatre. **But see the next section: Lesson 6 carries
something in its Cultural Notes that this count does not reach.**

**Lesson 5 — Checking for Messages (5.1–5.26, PDF 130–155). 2 lines, both drills.**
A person collects a phone message at a hotel desk and finds the caller's number was not
written down. It carries the volume's most extensive telling-the-time material, and
three closing sets of everyday expressions — an unheaded run of reassurances, then
*Expressing Relief* and *Expressing Surprise*. (The matching *Expressing disagreement*
set is Lesson 8's, at printed 8.23, and *Expressing Agreement* is Lesson 7's at 7.26.)
The two lines: *consulado* as one response in a list drill, and *o passaporte* as one
item in a singular/plural drill.

**Lesson 8 — Taking a Taxi (8.1–8.23, PDF 214–236). 3 lines: 2 drills and 1 note.**
A person hails a taxi in rush hour, discusses the traffic, gets out at a square and
tells the driver to keep the change. Transport vocabulary, money, *estar com pressa /
fome / frio*. Two drill items — *"… à festa da embaixada?"* and *"Você vai ao
consulado?"* — and, **not a drill item**, a clause of running prose in the Cultural Note
at printed 8.21: *"The Consulate General in Rio recommends that persons arriving at
Rio's Galeão airport use taxis operating on fixed fees, paid in advance."* That one is
prose in a section the pipeline carries, so it is a rewrite rather than a substitution.

**Lesson 7 — Directions Inside a Building (7.1–7.27, PDF 186–212). 5 lines, all
drills.** A person looks for a lawyer's office in a big building and takes the stairs.
Floors and ordinals, professions, *este/esse/aquele*. Five drill lines: the ambassador's
office as one of six floor-directory items, a *consulado* pair in a where-is drill,
*embaixatriz ou embaixadora?* and *cônsul/consulesa?* in two profession drills, and the
`(consul)` gloss printed on its own line beneath the second of those — the same layout
split Lesson 6 has, counted the same way.
**`embaixatriz` is glossed "ambassador's wife" and is a dated-premise problem in its own
right, separate from the embassy question.**

**Lesson 2 — Ordering Breakfast (2.1–2.25, PDF 50–74). 10 lines on printed
2.12–2.17: nine in drills and exchanges, one in a Pronunciation Practice word
list.** A person calls the front desk and orders
breakfast to the room. It is the most contaminated of the six and it is here because
the rule above is not a threshold: its dialogue and its premise are both clear, and
every one of the ten lines is a substitutable item. It also carries what the rest of
the shortlist does not — the cardinal numbers to 1000, the days of the week and the
Brazilian date order, the regular and irregular verb paradigms, and a full page of
courtesy exchanges — *Parabéns*, *Benvindo/a*, *Que prazer em vê-lo/la*,
*Obrigado/a*, *O prazer é meu* — **and two that are not that at all; see printed
2.25 below**. The ten: the consulate's address, a *passaporte diplomático*, the
consulate's telephone number twice, a Double Exchange pair built on knowing the
embassy's and the consulate's numbers, *"abre as malas na alfândega?"* with its
*(customs)* gloss, two *passaportes* in a signing drill, and — the one that is not a
drill — the bare word `consulado` in the Pronunciation Practice word list on printed
2.12, where it is being used to practise a vowel.

### Why each dropped lesson was dropped

**Lesson 1, Checking in (46).** The dialogue is the disqualification, not the drills:
*"A senhora é do Consulado Americano?" — "Sou, sim."*, and then *"com o desconto do
consulado"* and *"Seu passaporte, por favor."* A consulate employee checking in on a
consular rate. This is the Identity defect `plan.md` names, in the first line a learner
would hear.

**Lesson 3, Making a Long Distance Call (13). Read in full, dropped on its premise.**
*Setting the Scene* reads **"You wish to advise the DCM\* that you have arrived in
Brazil"**, footnoted *"Deputy Chief of Mission at an Embassy"*. That fails clause (b),
and it fails it in the same way Lesson 4 does. Everything else about the unit argues for
it: the dialogue is a telephone operator placing a person-to-person call and is entirely
clean, it is the only place in the volume where a learner **gives their own name**
(*"Meu nome é …"*, *"Eu me chamo …"*), and its Cultural Notes on Brazilian naming,
envelopes and salutations are among the best in the book. Its other twelve lines are
three examples in the contraction table (*do consulado*, *nos consulados*, *na
embaixada*), two drill items, and six passports. **If the shortlist proves too thin at
curation, this is the unit to reconsider first, and reconsidering it means rewriting a
premise rather than swapping lines.**

**Lesson 4, Inquiring about your Laundry (10).** *Setting the Scene* is an introductory
meeting with the Consul General — clause (b) again. The drills answer the telephone as
*"Aqui é do Consulado Americano"* and *"Aqui é da Embaixada Americana"*, and one runs
*"o consulado vai mandar o visto"* — the consulate will send the visa.

**Lesson 9, Answering the Phone (26).** The Additional Vocabulary is a cabinet list
including *Ministro da Aeronáutica*, *Ministro da Marinha*, *Ministro do Exército* and
*Chefe do Gabinete Militar*, followed by *Oficiais-Generais das Forças Armadas* and a
rank ladder — Brigadeiro, General, Coronel, Tenente, Sargento, Soldado, Almirante. Its
Cultural Note is *"Norms for addressing officials of the government, the church and the
private sector"*. Military and officials, by decision.

**Lesson 10, Leaving a Message (32).** The dialogue is *"o Adido Financeiro James Weber
da Embaixada Americana"* telephoning a ministry. `adido` occurs eighteen times in the
unit. One page carries the *Quartel General do Estado Maior das Forças Armadas*, captioned
as the place "where military parades take place".

**Lesson 11, Making an Appointment (12).** The dialogue is making an appointment with
the *Adido Comercial*; the Additional Vocabulary is the sections of a mission — *Seção
Consular, Cultural, Política, Econômica, Comercial*.

### What reading found that the screen cannot see

This is the part the four families do not reach, and it is why the method says to read.

**Lesson 6's Cultural Notes are about child pickpockets and road deaths, and this is
the most important thing in this section.** Printed 6.28 opens: *"'Trombadinhas' —
Brazilian pickpockets à la Oliver Twist range in age from seven to twenty. Usually,
these youthful street operators are poorly dressed and wear tennis shoes for quick
getaways … the trombadinhas dash off into the middle of the streets, risking their
lives darting around cars."* The same page continues *"O pedestre não tem vez"* —
pedestrians have no rights — with *"about 400 traffic accidents per day, resulting in
about 61,000 injuries and 3,000 deaths a year"*, streets *"very dangerous to cross"*,
and *"it takes real acrobatics to get to the other side of the street alive"*. The
exclusion screen scores this page **zero**; nothing in the four families names it.

**It is worse than the two items below, and the reason is mechanical.** Those are photo
captions, and the conversion pipeline takes dialogue, notes and drills — so captions do
not travel. **A Cultural Note does.** Lesson 6 is approved above on two drill lines, and
that count does not describe this page. **Whoever curates Lesson 6 has to decide about
printed 6.28 explicitly; it is not covered by "replace two drill items".**

**Lesson 5 carries two photograph captions about child poverty.** Printed 5.21: *"An
ice-cream vendor. Children in Brazil start working at a very young age. The money is
brought home to help the family."* Printed 5.22: *"A common scene in Rio de Janeiro — a
barefoot-child brings home the groceries as he passes through the favelas. In the
background an improvised water system is the only source of water available. Houses are
made out of scraps of tin, cardboard and newspaper."* Captions on substituted
photographs, so the pipeline would not carry them — a property of the pipeline, not a
judgement that the text is fine.

**Alcohol appears in two approved lessons, in five places in one of them.** Lesson 12:
*"Para mim, um chope"* (draft beer) at printed 12.14, *"Uma cervejinha bem gelada, por
favor"* at 12.19, *cerveja* in two drills at 12.11 and 12.16, and — the one that is
spirits rather than beer, and sits in a Thought Translation exercise the learner is
asked to render into Portuguese — *"Would you like to order a cocktail while you're
waiting?"* at 12.17. Lesson 2's Cultural Note describes the hotel minibar's *"liquor,
beer, soft drinks"* and recommends *"a refreshing cold Brazilian beer … when you walk
in from the hot carioca sun"*. Ordinary in an adult course; a decision for whoever
converts it, not a screen hit.

**Lesson 2's courtesy page teaches condolences, and this log points the next task
straight at it.** Printed 2.25 is twelve exchanges long, and ten of them are what
the name says — *Parabéns!*, *Benvindo/a!*, *Divirta-se!*, *Que prazer em vê-lo/la!*
The other two are **_"Meus pêsames!/Meus sentimentos!"_ glossed *"(My condolences!)"*
and _"Estimo as suas melhoras!"_ glossed *"(Hope you get better!)"*.** The screen scores
the page zero and no exclusion family reaches it.

**What makes this worth its own entry is not the content but where this log sends
people.** The shortlist has no introductions unit, so both the handoff below and the
Lesson 2 entry above nominate this page as the material for `pt-01-greetings` — a
child's first spoken Portuguese lesson. A curator following that instruction and
reading "courtesy page" would assemble it from a list that includes offering
condolences on a death. **That needs an explicit decision at curation, the same as
printed 6.28, and not a line swap.** It is the only bereavement content in the approved
152 pages.

**And one line about what children drink, on that same Lesson 2 page.** Printed 2.24,
in running prose in the Cultural Note: *"Even children drink café com leite as well as
cafezinhos (sweetened, strong black coffee served in demitasses)."* It is the only
child-directed consumption claim in the approved set, it is prose rather than a drill
item, and the page it sits on is one this log already quotes — so it was read, and
reporting it is the point.

**Two approved lessons reproduce a song lyric in full, and this one is a rights matter
rather than a content one.** Printed 5.25 is a Cultural Note carrying the complete
lyric of *"A felicidade"*, credited on the page to **Antonio Carlos Jobim e Vinicius de
Morais**. Printed 6.1 opens Lesson 6 with eight lines of *"São Paulo da Garoa"*. Three
more sit in dropped lessons: *"Cidade Maravilhosa"* credited to **André Filho** at
printed 1.2, *"Garota de Ipanema"* credited to **Vinícius de Moraes – Tom Jobim** at
3.13, and an uncredited carnaval song at 4.15. **Five lyrics in the volume, two of them
in units this log approves.** These sit in Cultural Notes and lesson openers, which the
pipeline carries — the photo-caption exemption does not reach them — and the rights
section above inventories only photographs, so nothing else in this log would have
caught them. **Whether any of the five is in copyright was not checked, and nothing
here is a view on it.**

**One footnote about slavery, in an approved lesson.** Printed 12.14 glosses the
*feijoada* drill: *"Feijoada is the name of a Brazilian dish introduced in the
northeast of Brazil by black slaves."* It is a footnote on a drill page, so unlike the
Lesson 5 captions the pipeline would carry it. Not disqualifying, and not something to
transcribe into a child's lesson without someone having decided how to say it.

**Dated practicalities, measured.** Prices are in **Cr$** — the cruzeiro, which Brazil
replaced in 1994 — on **nine** pages including the Lesson 1 room rate. Lesson 3 teaches
the public telephone in *fichas*, the grooved tokens that bought three minutes, and the
*orelhão*. Lesson 5's message vocabulary includes **telex**. Lesson 8 describes the
CMTC trolleybus fleet and *"a two-door Volkswagen with no right front seat"* as the
commonest cab in Brazil. None of this is disqualifying; all of it has to be rewritten
rather than transcribed.

**A human instructor, everywhere.** Every one of the twelve lessons instructs the
learner to repeat after *your teacher*, to have the teacher select items orally, and to
enact the dialogue with the teacher; each lesson ends with *"Instructor interviews"* and
*"Briefings for your instructor"*. Counted per lesson as the strings `your teacher`
and `instructor` together, the total runs from six — Lessons 4, 5, 7, 8 and 9 — to ten,
in Lessons 1 and 2. The robot **is** that instructor, so this framing is rewritten in every
unit that ships.

**A wider net was run over the approved units, and found nothing.** Because the stem
list is derived rather than given, the six approved lessons were re-screened against a
much broader vocabulary — ranks (marechal, brigadeiro, fuzileiro, marinheiro),
institutions (ministério, palácio, congresso, prefeitura, governo, tribunal), uniformed
roles (farda, uniforme, bombeiro, detetive, delegado, juiz, vigia), weapons and war
(fuzil, bomba, desfile, alistamento), and border and identity terms (aduana, vistoria,
estrangeiro, cidadania, identidade, checkpoint). It returns only place names and
city-facts prose: *Praça da Bandeira*, *Praça das Bandeiras*, *Palácio Tiradentes*,
*Avenida Presidente Vargas*, *"Governor's palace"* as an example landmark in the taxi
note, the word *crime* in a São Paulo city-facts list and again in a pronunciation
minimal-pair set. Nothing in any named family.

**A coverage gap, and it is the one that matters for this app.** The six approved
lessons cover numbers to a thousand, days of the week and dates, telling the time,
directions, floors and professions, transport and money, ordering food, and a page of
courtesy exchanges. **What they do not contain is an introductions lesson**, and the
only place in the volume where a learner gives their own name is Lesson 3, which is
dropped. The app's Portuguese syllabus opens at `pt-01-greetings`. Either that first
lesson is assembled from what the approved units carry (*Bom dia*, *Com licença*,
*Obrigado/a*, *Por favor*, *Pois não*, *Não há de quê*, and Lesson 2's courtesy page —
**which is not wholly courtesy; see printed 2.25**),
or Lesson 3's premise is rewritten and the unit brought back. This log does not decide
it.

### What this screening did NOT do

- **It did not curate anything.** No lesson was rewritten, no dialogue turn extracted,
  no drill converted. The shortlist is six lesson numbers and the reasons for them.
- **It did not read the five outright-dropped lessons end to end.** Lessons 1, 4, 9, 10
  and 11 were screened, and every screen hit in each was read in its page context, and
  the pages the quotations above come from were read in full. That is enough to drop a
  unit whose dialogue or premise carries the family, and it is not enough to have judged
  everything else in it. Lessons 2 and 3 **were** read end to end, because their
  dialogues are clean and the count alone should not have decided them.
- **The first version of this section claimed a full read it had not done.** It said all
  127 pages of a five-lesson shortlist were read page by page; in fact two pages of
  Lesson 6 and six of Lesson 8 had not been opened, and one of the two unread Lesson 6
  pages is printed 6.28 — the trombadinhas note above, the worst content in the
  approved set. It is recorded here rather than quietly fixed, because a reading claim
  is exactly the kind of claim this log exists to make checkable.
- **It did not check OCR fidelity.** The accents in the text layer are damaged in places
  — the screen is accent-insensitive for that reason, and `consu Itórios` on PDF 190
  shows the scan breaking a word in half — and no measurement of how bad it is was made.
  That belongs to the curation task, and it is the failure mode `plan.md` calls the worst
  one for a language course.
- **It did not settle the variety anywhere a learner can see.** The course is Brazilian
  Portuguese and the app's catalog says "Portuguese". The lesson objectives written for
  these six units have to say Brazilian; see "What this log does not settle".
- **It did not verify the derived Portuguese stems against a second source.** They were
  derived here and published above so that somebody can disagree with a specific term
  rather than with the result.
- **It did not check the copyright status of the five song lyrics.** They are recorded
  above with the credits the pages carry, and that is all: nobody looked up any of the
  five, and the rights section's position was formed before they were found.
- **It did not populate `APPROVED_UNITS`.** That registry's own test asserts its entries
  are cited by shipped lessons, so registering six units before a Portuguese lesson
  exists would fail it. The shortlist lives here until curation.

## What this log does not settle

Two things are named above that a later task has to carry, and neither is done:

1. **The learner-visible variety label.** This course is Brazilian Portuguese and the
   app's catalog says "Portuguese". The label lives in
   `learners/store.py:129` and in the `get_progress` and `start_lesson` tool enums. This
   file is a developer document; a learner and a parent never see it, so mentioning the
   variety here does not discharge the requirement that they be told.
2. **Steps 4 onward** — per-unit curation of the six shortlisted lessons, the
   `converted_lessons.json` entry including the `rights` value it has to carry, and the
   `APPROVED_UNITS` registration in `reachy_language_tutor/tests/approved_units.py`.
   That registry is deliberately **not** populated here: its own test asserts the
   entries match units actually cited by shipped lessons, so registering six units
   before a single Portuguese lesson exists would fail it. When the `rights` value is
   written it should be a summary of "Rights: what was actually checked": a description
   of what was found, not a conclusion about the law.
3. **Where the first Portuguese lesson comes from.** The shortlist has no
   introductions unit: the only place in the volume where a learner gives their own
   name is Lesson 3, which is dropped on its embassy premise. `pt-01-greetings` is the
   app's first Portuguese lesson today. Whoever curates has to decide whether it is
   assembled from what the approved units carry — including Lesson 2's courtesy page,
   with the caveat recorded at printed 2.25 —
   or whether Lesson 3's premise is rewritten and the unit brought back. This log does
   not decide it.
4. **Printed 6.28 and printed 2.25.** Two pages of approved lessons need an explicit
   decision at curation rather than a line swap, and neither is reachable by any
   exclusion family: Lesson 6's Cultural Note about child pickpockets and road deaths,
   and the two bereavement exchanges on Lesson 2's courtesy page — which is the page
   item 3 above sends a curator to. Both are recorded under "What reading found that
   the screen cannot see".
