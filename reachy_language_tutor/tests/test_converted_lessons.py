"""The lessons converted from a published course, and the claims made about them.

W23 built somewhere to put lesson content. This is about what was actually put there:
six units of FSI Italian FAST, corrected against the page images and curated for a
robot that sits in a family's house.

Three kinds of claim are checked here, and they are not equally strong.

* **What the content IS.** It round-trips through the store, its accents survived, and
  a cue-response drill keeps its question and its answer apart. Straightforward.
* **Where it came FROM.** Every converted lesson cites a unit on a list of units a
  person reviewed, and nothing ships from a unit that is not on that list. This is the
  real control over what reaches a child, and it is an allow-list: adding a unit means
  editing the list, which is a deliberate act somebody can be asked about.
* **What it does NOT contain.** A screen for military, embassy and uniformed-authority
  vocabulary. This one is a BACKSTOP and is named as such: a list of words to refuse is
  only ever as complete as the last person to think about it, which is why it is the
  second line here and not the first. It catches a careless edit, not a determined one.

The conversion method, and every judgement made on the way, are in
docs/converting-a-course.md and docs/curation-log-italian-fast.md.
"""

import re
import json
import hashlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from approved_units import APPROVED_UNITS, approval_refusal

from reachy_language_tutor.learners import store
from reachy_language_tutor.learners.models import DRILL_KINDS


# The control itself lives in approved_units.py, beside the function that applies it,
# so that the list and the meaning of "approved" cannot drift apart -- see that module
# for why it is keyed by course rather than by roman numeral alone.

DOCS = Path(__file__).resolve().parents[2] / "docs"
CURATION_LOG = DOCS / "curation-log-italian-fast.md"

# One log per course, because the judgement recorded in a log is about a particular
# volume. Keyed by the course name in converted_lessons.json, the same key
# approved_units.py uses, so a course cannot be half-registered: a course with no
# entry here fails the accounting test below rather than being skipped by it.
CURATION_LOGS = {
    "FSI Italian FAST, Volume 1": DOCS / "curation-log-italian-fast.md",
    "FSI Spanish Familiarization and Short-Term Training": DOCS / "curation-log-spanish.md",
    "FSI Brazilian Portuguese FAST, Volume I": DOCS / "curation-log-portuguese-fast.md",
}

# The word that offers a free choice, per language. Portuguese is the reason this is a
# table rather than a literal: Italian and Spanish "o" IS "or", but Portuguese "o" is
# the definite article and its "or" is "ou", so one literal cannot serve all three.
# A language absent from here is a REFUSAL rather than a skip -- see the guard below.
EITHER_OR_WORD = {"it": "o", "es": "o", "pt": "ou"}


def _language_of(lesson_id: str) -> str:
    """Return the language code a converted lesson id begins with."""
    return lesson_id.split("-", 1)[0]


# What each course has actually CONVERTED, pinned so a lesson cannot appear or vanish
# without someone saying so here. Not derived from the file -- deriving it would make
# the assertion a tautology.
# Drills already shipped whose cue IS their expected response. An ALLOW-LIST, not a
# deny-list: anything not named here must not answer itself, so a new offender fails
# closed. It exists because parametrising the end-to-end test below over every
# converted language found one that had been invisible while only Italian ran that
# path, and weakening the assertion to accommodate it would have thrown away the reason
# the assertion was written. D37 fixes the drill and empties this.
#
# Two assertions read this list, and the difference matters. The end-to-end test reaches
# only each language's FIRST converted unit, because that is the one a learner is
# offered -- three of the eighteen. test_no_converted_drill_answers_itself below reads
# every drill of every unit straight out of the file, and IT is what makes "a new
# offender fails closed" true of the catalog rather than of three units. An earlier
# version of this comment claimed the wider guarantee while only the narrower assertion
# existed.
KNOWN_SELF_ANSWERING_DRILLS = {
    ("es-fast-01-getting-started-in-class", "Buenos días."),
}

CONVERTED_PER_COURSE = {
    "FSI Italian FAST, Volume 1": 6,
    "FSI Spanish Familiarization and Short-Term Training": 6,
    "FSI Brazilian Portuguese FAST, Volume I": 6,
}
METHOD = DOCS / "converting-a-course.md"


@pytest.fixture
def instance(tmp_path: Path) -> Path:
    """Return a prepared learner database at a temporary instance path."""
    assert store.ensure_learner_database(tmp_path).ready is True
    return tmp_path


def _lowest_placeholder(instance_path: Path, language_code: str) -> str:
    """Return this language's first lesson that was NOT converted from a course.

    That is the row the renumbering moved off position 1, so it is the one whose move
    back down collides. Looked up rather than spelled, because spelling it is how the
    same assertion ends up naming one language for ever.
    """
    converted = set(_converted_ids(language_code))
    connection = store.connect(instance_path)
    try:
        rows = connection.execute(
            "SELECT id FROM lessons WHERE language_code = ? ORDER BY position", (language_code,)
        ).fetchall()
    finally:
        connection.close()
    placeholders = [str(row["id"]) for row in rows if str(row["id"]) not in converted]
    assert placeholders, f"{language_code} has no unconverted lesson, so nothing can collide downward"
    return placeholders[0]


def _converted_language_codes() -> list[str]:
    """Every language code that ships converted units, read from the file.

    Derived rather than listed. A test that names its languages covers the ones whoever
    wrote it was thinking about, which is how the renumbering hazard below stayed an
    Italian-only claim through two more courses; a test that asks the file covers the
    next course on the day it lands.
    """
    return sorted({str(course["language_code"]) for course in store._converted_courses()})


def _converted_ids(language_code: str | None = None) -> list[str]:
    """Ids of converted lessons, for one language or for every course.

    The unscoped form is right for the screens -- model-directed phrasing, vocabulary
    -- which must cover every shipped line whatever course it came from. The scoped
    form is for the assertions that are ABOUT a course: its ordering, its digest, its
    unit count. Those read as global claims while the file held one course, and a
    second course is what tells them apart.
    """
    if language_code is None:
        return [str(lesson["id"]) for lesson in store._converted_lessons()]
    return [
        str(lesson["id"])
        for course in store._converted_courses()
        if course["language_code"] == language_code
        for lesson in course["lessons"]
    ]


def _rows_per_table(instance_path: Path, language_code: str | None = None) -> tuple[int, int, int]:
    """How many turns, notes and drills the converted lessons actually seeded.

    Scopable by language for the same reason _converted_ids is: an unqualified count
    is a claim about every course at once, which stops being a witness for any one of
    them as soon as there are two.
    """
    scope = (
        "SELECT lesson_id FROM lesson_sources WHERE origin = 'converted_from_course'"
        if language_code is None
        else "SELECT s.lesson_id FROM lesson_sources AS s JOIN lessons AS l ON l.id = s.lesson_id "
        "WHERE s.origin = 'converted_from_course' AND l.language_code = ?"
    )
    args = () if language_code is None else (language_code,)
    connection = store.connect(instance_path)
    try:
        return tuple(  # type: ignore[return-value]
            connection.execute(f"SELECT COUNT(*) FROM {table} WHERE lesson_id IN ({scope})", args).fetchone()[0]
            for table in ("lesson_dialogue_turns", "lesson_notes", "lesson_drills")
        )
    finally:
        connection.close()


def _shipped_text(instance_path: Path, language_code: str | None = None) -> str:
    """Every string a learner could hear from a converted lesson, read back from disk.

    From the DATABASE rather than from the JSON file, because the database is what the
    tutor reads: a fault that the loader introduced between the two would be invisible
    to a test that checked the file.
    """
    pieces: list[str] = []
    for lesson_id in _converted_ids(language_code):
        content = store.get_lesson_content(lesson_id, instance_path=instance_path)
        assert content is not None
        pieces.append(content.lesson.title)
        pieces.append(content.lesson.objective)
        pieces.append(content.dialogue_title or "")
        pieces.extend(f"{turn.speaker}: {turn.text}" for turn in content.turns)
        pieces.extend(note.text for note in content.notes)
        for drill in content.drills:
            pieces.extend(
                part for part in (drill.target_text, drill.english_gloss, drill.cue, drill.expected_response) if part
            )
    return "\n".join(pieces)


# ------------------------------------------------------------------ what shipped


def test_the_converted_lessons_reach_the_database(instance: Path) -> None:
    """The whole point, asserted end to end: six units, in order, with their content."""
    ids = _converted_ids()
    by_course = {course["name"]: len(course["lessons"]) for course in store._converted_courses()}
    assert by_course == CONVERTED_PER_COURSE, (
        "a course gained or lost a converted lesson; update CONVERTED_PER_COURSE in the "
        "same change, and its curation log with it"
    )
    assert len(ids) == sum(CONVERTED_PER_COURSE.values())

    for lesson_id in ids:
        content = store.get_lesson_content(lesson_id, instance_path=instance)

        assert content is not None, f"{lesson_id} did not reach the database"
        assert content.lesson.language_code in {c["language_code"] for c in store._converted_courses()}
        assert content.turns, f"{lesson_id} has no dialogue"
        assert content.notes, f"{lesson_id} has no usage notes"
        assert content.drills, f"{lesson_id} has no drills"
        assert content.source is not None, f"{lesson_id} has no provenance"


def test_a_converted_unit_round_trips_whole(instance: Path) -> None:
    """One unit, compared field by field against the file it was loaded from.

    Counting rows would pass over a loader that wrote every turn as the same speaker,
    so this compares the text itself, in order.
    """
    source = next(lesson for lesson in store._converted_lessons() if lesson["id"] == "it-fast-02-room-service")
    content = store.get_lesson_content("it-fast-02-room-service", instance_path=instance)

    assert [(turn.speaker, turn.text) for turn in content.turns] == [
        (turn["speaker"], turn["text"]) for turn in source["turns"]
    ]
    assert [note.text for note in content.notes] == list(source["notes"])
    assert [drill.kind for drill in content.drills] == [drill["kind"] for drill in source["drills"]]
    assert [note.number for note in content.notes] == list(range(1, len(source["notes"]) + 1))


def test_a_cue_response_drill_keeps_its_question_and_its_answer_apart(instance: Path) -> None:
    """What makes a drill checkable rather than merely sayable.

    A drill whose cue and answer had been joined into one string would still read back
    fine as text; only asking for them separately shows they were kept apart.
    """
    content = store.get_lesson_content("it-fast-01-what-time-is-it", instance_path=instance)
    cue_response = [drill for drill in content.drills if drill.kind == "cue_response"]

    assert cue_response, "this unit's drills include the checkable kind"
    for drill in cue_response:
        assert drill.cue and drill.expected_response
        assert drill.cue != drill.expected_response
        assert drill.expected_response not in drill.cue, "the answer is not sitting inside the question"
        assert drill.target_text is None and drill.english_gloss is None

    asked = {drill.cue: drill.expected_response for drill in cue_response}
    assert asked["È l'una?"] == "No, è l'una e cinque."


def test_no_cue_response_drill_has_more_than_one_right_answer(instance: Path) -> None:
    """An edge case the task names, and one this conversion got wrong before it got right.

    A `cue_response` drill holds exactly one `expected_response`, so it may only ask a
    question whose answer follows from the cue. The shipped drills once broke that three
    ways: an either/or question answered with one branch (`Liscia o gassata?` -- both are
    correct); a cue naming a place the learner could not know (`Dove metto la matita?`,
    where the set answered `sul tavolo`, `lì`, `sul letto` and `nell'armadio` with nothing
    to choose between them); and two models with opposite rules merged into one list, so
    `Sono le nove?` wanted `e cinque` while the identically shaped `Sono le tre?` wanted
    `meno cinque`. Every one of those marks a learner wrong for saying something right.

    The method doc says what to do about each -- fold the stimulus into the cue, re-ship
    it as a repetition drill, or drop it -- and this is the backstop that notices when
    somebody does not.
    """
    offenders: list[str] = []
    for lesson_id in _converted_ids():
        content = store.get_lesson_content(lesson_id, instance_path=instance)
        # Checked per LESSON, not per cue_response drill. Inside the drill loop this
        # could never fire for a language that ships no cue_response drill at all --
        # which is Portuguese today -- so the refusal would have been unreachable for
        # the very language it was written for.
        code = _language_of(lesson_id)
        assert code in EITHER_OR_WORD, (
            f"{code!r} ships converted lessons but has no word in EITHER_OR_WORD, so no "
            f"cue of its would be screened for an unresolved choice. Add its word for 'or'."
        )
        either_or = EITHER_OR_WORD[code]
        for drill in content.drills:
            if drill.kind != "cue_response":
                continue
            # An unresolved either/or: "X or Y?" offers a free choice, so one branch
            # cannot be the only answer. The word for "or" is per-language and this is
            # not cosmetic: Italian and Spanish "o" IS "or", but Portuguese "o" is the
            # definite article and its "or" is "ou", so the Italian literal would fire
            # on every ordinary article and still miss the real Portuguese case.
            if re.search(rf"\w\s+{either_or}\s+\w[^?]*\?\s*$", drill.cue):
                offenders.append(f"{lesson_id}: {drill.cue}")

    assert offenders == [], f"a cue offers a choice but only one branch is accepted: {offenders}"


def test_a_drill_that_asks_where_says_where(instance: Path) -> None:
    """The second shape of the same defect, pinned separately because it reads as fine.

    `Dove metto la matita?` looks like a well-formed drill. It is not: the answer the set
    wants is a particular place, and no place is in the question. What the drill is
    really teaching is the pronoun -- `la` against `le` -- so the place belongs in the
    cue, where it stops being a guess.
    """
    for lesson_id in _converted_ids():
        content = store.get_lesson_content(lesson_id, instance_path=instance)
        for drill in content.drills:
            if drill.kind != "cue_response" or not drill.cue.lower().startswith("dove metto"):
                continue
            place = drill.expected_response.split("metta", 1)[-1].strip(" .")
            assert place and place in drill.cue, (
                f"{lesson_id}: {drill.cue!r} expects {drill.expected_response!r}, "
                "but the place it wants is not in the question"
            )


def test_a_repetition_drill_keeps_the_term_and_its_meaning_apart(instance: Path) -> None:
    """The pitfall in the task's own words: the pair is two fields, never one."""
    for lesson_id in _converted_ids():
        content = store.get_lesson_content(lesson_id, instance_path=instance)
        for drill in (drill for drill in content.drills if drill.kind == "repetition"):
            assert drill.target_text and drill.english_gloss
            assert drill.cue is None and drill.expected_response is None


def test_every_drill_is_of_a_kind_the_tutor_can_run(instance: Path) -> None:
    """No third kind crept in that nothing knows how to speak."""
    kinds = {
        drill.kind
        for lesson_id in _converted_ids()
        for drill in store.get_lesson_content(lesson_id, instance_path=instance).drills
    }

    assert kinds <= set(DRILL_KINDS)
    assert kinds == set(DRILL_KINDS), "both kinds are represented, so both are exercised"


# ------------------------------------------------------------------ the accents


# Lines carrying an accent that changes the word, each checked against the page image
# named beside it. An accent is not a typo in a language course: "e" is "and" and "è"
# is "is", so a lost one teaches a child a different sentence.
ACCENTED_LINES = [
    # Brazilian Portuguese. The sharpest case in the programme, because the fault and
    # the diacritic are the same character: this scan drops an accent and leaves a stray
    # tilde, and the tilde IS a Portuguese letter, so a dropped accent lands on a word
    # that still reads as Portuguese -- mao for mão, sao for são, tao for tão. Each line
    # below was read off its page image at 140 dpi.
    (
        "pt-fast-01-ordering-breakfast",
        "O senhor quer mandar o café da manhã para o quarto 318, por favor?",
        "unit 2, printed page 2.2",
    ),
    (
        "pt-fast-02-checking-for-messages",
        "Não, só espero que ele telefone outra vez amanhã.",
        "unit 5, printed page 5.2",
    ),
    (
        "pt-fast-03-asking-for-directions",
        "Umas três quadras, mais ou menos. Então, a senhora vai reto até chegar na Rua "
        "Formosa. A Avenida São João fica a três quadras da Rua Formosa. É uma avenida "
        "larga de mão dupla.",
        "unit 6, printed page 6.4",
    ),
    (
        "pt-fast-04-finding-an-office",
        "Este prédio é tão grande. Eu não sei porque não há um quadro no saguão.",
        "unit 7, printed page 7.2",
    ),
    (
        "pt-fast-05-taking-a-taxi",
        "Dá, mas não adianta. A esta hora o trânsito está engarrafado na cidade inteira. "
        "Tem que ter paciência. Onde a senhora quer descer?",
        "unit 8, printed page 8.2",
    ),
    (
        "pt-fast-06-ordering-lunch",
        "É um sanduíche quente de bife ou presunto, com queijo e tomate. É muito bom.",
        "unit 12, printed page 12.2",
    ),
    # Spanish. The scan this course came from is 47.9% accent-damaged -- worse than
    # Italian's -- and Spanish is where it bites hardest: año and ano are different
    # words, and sí/si, él/el, tú/tu, más/mas each turn on one acute.
    ("es-fast-01-getting-started-in-class", "Buenos días, señora.", "Cycle 2, printed page 7"),
    ("es-fast-02-at-the-restaurant", "¿Dónde quiere sentarse?", "Cycle 5, printed page 57"),
    ("es-fast-02-at-the-restaurant", "¿Cómo quiere su bistec?", "Cycle 5, printed page 57"),
    (
        "es-fast-03-getting-around-inside",
        "No, éste es el quinto piso y el consultorio del Doctor Cardona está en el cuarto piso.",
        "Cycle 10, printed page 120",
    ),
    (
        "es-fast-03-getting-around-inside",
        "Está en el séptimo piso, número 718. Doble a la derecha al salir del ascensor.",
        "Cycle 10, printed page 121",
    ),
    (
        "es-fast-04-the-familiar-form",
        "Es muy fácil; sólo tienes que decir estás en vez de está.",
        "Cycle 14, printed page 177",
    ),
    ("es-fast-04-the-familiar-form", "Porque nunca aprendí a tutear en español.", "Cycle 14, printed page 177"),
    (
        "es-fast-05-shopping-at-the-market",
        "Sí, quiero comprar algunas artesanías. ¿Adónde me aconsejas que vaya?",
        "Cycle 25, printed page 342",
    ),
    (
        "es-fast-06-household-repairs",
        "Es que tuvimos una avería eléctrica en casa. A la hora que iba a salir se fundieron los fusibles "
        "y fue necesario que llamáramos a un electricista.",
        "Cycle 38, printed page 526",
    ),
    ("it-fast-01-what-time-is-it", "No, da Boston. A proposito, che ora è?", "unit IV, printed page 84"),
    (
        "it-fast-01-what-time-is-it",
        "Già, è vero, tra Boston e Roma c'è una differenza di sei ore!",
        "unit IV, printed page 84",
    ),
    (
        "it-fast-02-room-service",
        "Sì, vorrei anche un'altra coperta e degli attaccapanni.",
        "unit VI, printed page 125",
    ),
    (
        "it-fast-02-room-service",
        "C'è una coperta nell'armadio, Le faccio portare subito gli attaccapanni.",
        "unit VI, printed page 125",
    ),
    ("it-fast-03-taxi-and-haircut", "Più o meno un'ora. Si accomodi qui, prego.", "unit IX, printed page 199"),
    (
        "it-fast-04-shopping-for-clothes",
        "Sì, mi piace, ma è un po' stretta sui fianchi.",
        "unit XIII, printed page 302",
    ),
    ("it-fast-05-eating-out", "Ma certamente. Ecco il menù. Vogliono ordinare adesso?", "unit XV, printed page 356"),
    (
        "it-fast-06-phone-call-about-a-flat",
        "È in via Lombardia 2, al quinto piano. È molto luminoso perché non ci sono palazzi davanti e molte "
        "finestre danno su un giardino pubblico.",
        "unit XVII, printed page 412",
    ),
]


@pytest.mark.parametrize(("lesson_id", "line", "page"), ACCENTED_LINES, ids=[row[2] for row in ACCENTED_LINES])
def test_an_accented_line_survived_the_conversion(instance: Path, lesson_id: str, line: str, page: str) -> None:
    """Specific lines, not a character count, and each one checked against its page.

    The OCR layer loses about a fifth of this scan's accented characters, so a test
    that only counted them could pass on a lesson where every one had been dropped and
    a different one invented. These are the exact sentences, from the exact pages.
    """
    content = store.get_lesson_content(lesson_id, instance_path=instance)
    spoken = [turn.text for turn in content.turns]

    assert line in spoken, f"the line from {page} is not what reached the database"


def test_the_scan_s_own_faults_did_not_reach_the_database(instance: Path) -> None:
    """The three OCR faults measured on this course, screened for in what shipped.

    Measured over printed pages 40-160 of the source this session: 79 accented
    characters replaced by a stray '~', 194 words where 'l' was read as '/', and 8
    places where 'I' became '!'. The first two are what the conversion exists to
    correct; this asserts none of them travelled through.
    """
    text = _shipped_text(instance)

    assert "~" not in text, "a tilde is what this scan leaves behind where an accent was"
    assert not re.search(r"[A-Za-zÀ-ÿ]![A-Za-zÀ-ÿ]", text), "an exclamation mark inside a word is an OCR 'I'"

    # A slash inside a word is the 'l' fault -- except where the source really means
    # one. Named individually rather than waved through as a class, because "americano/a"
    # is a real spelling and "usual/y" is a defect, and only a person can tell them apart.
    permitted_slashes = {
        "s/he",
        "americano/a",
        "and/or",
        # printed on Brazilian Portuguese FAST pages 7.5 and 12.6, as glosses
        "what/which",
        "leaving/getting",
        "hall/corridor",
        "is/was",
    }
    for word in re.findall(r"[A-Za-zÀ-ÿ]+/[A-Za-zÀ-ÿ]+", text):
        assert word in permitted_slashes, f"{word!r} looks like the scan's 'l' read as a slash"


def test_no_print_convention_reached_a_line_the_tutor_has_to_say(instance: Path) -> None:
    """The course is a printed book and says things a voice cannot.

    signor(in)a/signore, arrivato/a and ( + last name) are typographic shorthand for a
    reader to resolve silently. Spoken aloud they are gibberish, so the conversion
    resolves them and this is what stops one slipping back in.
    """
    text = _shipped_text(instance)

    assert "(in)" not in text, "the signor(in)a/signore convention has to be resolved before it is spoken"
    assert "last name" not in text, "a placeholder for the reader's own name cannot be read aloud"
    assert not re.search(r"\b(?:arrivato|andato|pronto|stanco)/a\b", text), "a written gender alternative"


# ------------------------------------------------------- where the content came from


def test_every_converted_lesson_cites_a_unit_that_was_reviewed(instance: Path) -> None:
    """The control that actually decides what a child hears.

    An allow-list, and the direction matters: content may only come from a unit on this
    list, so a unit nobody reviewed cannot ship by being overlooked -- it has to be
    added here first, by someone who can be asked why.
    """
    # Which course each lesson came from, taken from the file's own nesting rather than
    # from one hardcoded name. That is what makes this check say "the seeded provenance
    # names the course that OWNS this lesson" instead of "the catalog is still Italian".
    # The WHOLE provenance each lesson declares in the file, not just its course. The
    # unit is the second half of the approval key, so checking the course per lesson
    # while taking the unit on trust would let a seeding fault cite an approved numeral
    # for content that came from a unit nobody read -- and every check here would stay
    # green. Pinned generically rather than per course, so it holds for course two.
    owner = {
        str(lesson["id"]): (
            course["name"],
            lesson["source"]["module"],
            lesson["source"]["unit"],
            lesson["source"]["page"],
        )
        for course in store._converted_courses()
        for lesson in course["lessons"]
    }

    for lesson_id in _converted_ids():
        source = store.get_lesson_content(lesson_id, instance_path=instance).source

        assert source is not None
        assert source.origin == "converted_from_course"
        assert (source.course, source.module, source.unit, source.page) == owner[lesson_id], (
            f"{lesson_id} is seeded under provenance its own course block does not declare"
        )
        refusal = approval_refusal(source.course, source.unit)
        assert refusal is None, f"{lesson_id} may not ship: {refusal}"
        assert source.module and source.page and source.page > 0


def test_the_approved_units_are_all_used(instance: Path) -> None:
    """The other direction: the list describes what shipped, rather than outliving it.

    An allow-list that slowly fills with units nobody ships any more stops being a
    record of a decision and becomes a list of permissions nobody is using.
    """
    cited: dict[str, set[str]] = {}
    for lesson_id in _converted_ids():
        source = store.get_lesson_content(lesson_id, instance_path=instance).source
        cited.setdefault(source.course, set()).add(source.unit)

    assert cited == {course: set(units) for course, units in APPROVED_UNITS.items()}


def test_the_provenance_is_enough_to_find_the_page_again(instance: Path) -> None:
    """Provenance is for checking a suspect line, so it has to be specific enough.

    Course, volume, unit AND page. Three of the four would leave somebody leafing
    through a 456-page scan, which is how a correction stops being worth making.
    """
    for lesson_id in _converted_ids():
        source = store.get_lesson_content(lesson_id, instance_path=instance).source

        # Specific enough to find the page again, asked of EVERY course: a named
        # course, a named module within it, and a page number that could be turned to.
        # The Italian-only facts are checked once below rather than asserted of every
        # course, because the cheapest way to make a hardcoded course name pass for a
        # second course is to delete it -- which is the widening this file exists to
        # prevent.
        assert source.course and source.course.strip(), "provenance names the course"
        assert source.module and source.module.strip(), "and the module within it"
        assert source.page >= 1, "and a printed page somebody could turn to"

    italian = store.get_lesson_content("it-fast-01-what-time-is-it", instance_path=instance).source
    assert italian is not None
    assert "FSI" in italian.course and "Italian" in italian.course
    assert italian.module == "Volume 1"
    assert 1 <= italian.page <= 456, "a page inside the volume this course actually has"


def test_nothing_military_or_official_survived_the_curation(instance: Path) -> None:
    """A BACKSTOP, not the control. The control is the approved-unit list above.

    This is a list of words to refuse, and such a list is only ever as complete as the
    last person to read it -- which is exactly why it is not what this file relies on.
    It is here because it is cheap and it catches the realistic failure: somebody edits
    one line of an approved unit and puts a customs officer back into it.

    The terms are the ones the task names, plus the Italian AND Spanish for each. The
    Spanish half was missing while six Spanish Cycles shipped, which is the same
    one-language blindness this docstring already warned about for English -- and it
    mattered more here, because the Spanish volume is the one where 29 of 38 Cycles
    carry embassy, military, uniformed or border material.

    "general" and "oficial" are deliberately NOT on this list. W35 measured them:
    widening to them matched "generalmente" and made the screen worse, which is the
    standing argument for why a deny-list is a backstop and never the control.
    """
    text = _shipped_text(instance).lower()

    forbidden = {
        "military": [
            "militare",
            "esercito",
            "caserma",
            "generale",
            "ammiraglio",
            "colonnello",
            "sergente",
            "militar",
            "ejército",
            "cuartel",
            "soldado",
            "coronel",
            "sargento",
            "exército",
            "quartel",
            "marinha",
            "aeronáutica",
            "tenente",
            "almirante",
            "forças armadas",
            "guerra",
        ],
        "embassy": [
            "ambasciata",
            "ambassador",
            "consolato",
            "embassy",
            "consulate",
            "diplomatic",
            "embajada",
            "embajador",
            "consulado",
            "cónsul",
            "vicecónsul",
            "sección consular",
            "embaixada",
            "embaixador",
            "embaixatriz",
            "adido",
            "chancelaria",
            "seção consular",
        ],
        "uniformed authority": [
            "polizia",
            "poliziotto",
            "carabinier",
            "questura",
            "police",
            "policía",
            "policia",
            "comisaría",
            "polícia",
            "policial",
            "delegacia",
            "delegado",
        ],
        "border": [
            "dogana",
            "doganiere",
            "customs",
            "frontiera",
            "passaporto",
            "passport",
            "guardia di finanza",
            "aduana",
            "aduanero",
            "pasaporte",
            "frontera",
            "inmigración",
            "alfândega",
            "alfandegário",
            "passaporte",
            "fronteira",
            "imigração",
            "despachante",
        ],
    }
    for category, terms in forbidden.items():
        found = [term for term in terms if term in text]
        assert found == [], f"{category} vocabulary reached the shipped lessons: {found}"


def test_nothing_in_the_shipped_text_is_addressed_to_the_model(instance: Path) -> None:
    """The second BACKSTOP in this file, and it is one for the same reason as the first.

    Lesson content is model-facing: the tutor will read these turns, notes and drills
    while holding its own instructions, and a line that reads as a direction rather than
    as material is a line that could steer it. The control is that a person reads every
    unit — the source is full of instructions written for a human teacher, and those are
    dropped rather than translated. This is the cheap net under that, catching the
    realistic slip: a usage note written in the imperative, addressed to whoever is
    reading it, rather than to a learner.

    A phrase list cannot be complete, which is exactly why it is not what this relies on.
    """
    text = _shipped_text(instance).lower()

    directed_at_the_model = [
        "system prompt",
        "ignore the",
        "ignore all",
        "ignore previous",
        "you are an",
        "you are a helpful",
        "as an ai",
        "disregard",
        "your instructions",
        "new instructions",
        "override",
    ]
    found = [phrase for phrase in directed_at_the_model if phrase in text]

    assert found == [], f"a shipped line reads as a direction rather than as material: {found}"


def test_the_file_s_own_preamble_is_never_stored(instance: Path) -> None:
    """The content file explains itself at the top; none of that is lesson material.

    `_about` is prose for whoever opens the file, and it talks ABOUT the rule that
    nothing here is addressed to the model. If the loader ever swept it into the database
    it would become the one thing in the catalog that is addressed to a reader, which is
    the failure the key exists to describe.
    """
    preamble = " ".join(json.loads(store._converted_lessons_json())["_about"])
    assert preamble, "the file does carry a preamble, or this test guards nothing"

    text = _shipped_text(instance)

    assert "Edit this file" not in text
    assert "never instructions" not in text


def test_the_rights_position_is_recorded_rather_than_assumed(instance: Path) -> None:
    """Public domain is a property of a course, not of the FSI label.

    The file has to carry what was actually checked about THIS course, and the curation
    log has to say what was not. An empty or breezy claim here would be the "never write
    a claim you have not verified" rule broken in the one place it costs most.
    """
    courses = store._converted_courses()
    assert courses, "a file with no courses has no rights position to check"

    # Every course, not just the one this repository happens to ship today: rights are
    # settled per course, so a second course added without its own position is exactly
    # the omission this has to catch.
    for course in courses:
        assert len(course["rights"]) > 120, f"{course['name']}: a rights position needs saying, not asserting"
        assert course["source_sha256"], f"{course['name']}: the file it was read from is identified"
        assert len(course["source_sha256"]) == 64, f"{course['name']}: a SHA-256 is 64 hex characters"

    italian = next(course for course in courses if course["language_code"] == "it")
    assert "Foreign Service Institute" in italian["rights"]
    assert "1992" in italian["rights"]


# -------------------------------------------------------------- alongside the rest


def test_no_converted_drill_answers_itself() -> None:
    """Every cue-response drill in every course, not the three a learner is offered first.

    A cue_response drill exists so the tutor has an answer to check. One whose cue IS
    its expected response gives it nothing to mark -- it is a repetition drill wearing
    the wrong kind, and the database will happily store it, because the CHECK constraint
    polices which FIELDS a kind carries and not whether the two differ.

    This reads the file rather than the database and covers all eighteen units. The
    end-to-end test below catches the same fault, but only in the first unit of each
    language, which is how the one offender here sat in a shipped course unnoticed.
    """
    offenders = []
    for lesson in store._converted_lessons():
        for drill in lesson["drills"]:
            if drill.get("kind") != "cue_response":
                continue
            if drill.get("cue") != drill.get("expected_response"):
                continue
            if (lesson["id"], drill["cue"]) in KNOWN_SELF_ANSWERING_DRILLS:
                continue
            offenders.append(f"{lesson['id']}: {drill['cue']!r}")

    assert not offenders, (
        "these cue-response drills answer themselves, so a tutor has nothing to mark: "
        f"{offenders}. Each is a repetition drill wearing the wrong kind -- change the "
        "drill. KNOWN_SELF_ANSWERING_DRILLS is for an offender already filed as a "
        "defect, and adding to it without filing one is how this fault shipped before."
    )


def test_get_progress_still_summarises_a_converted_lesson_in_one_line(instance: Path) -> None:
    """The pitfall: content is a different job from the one-line summary.

    A converted lesson has a dialogue, notes and eighteen drills. What get_progress
    hands the tutor is still a title and one sentence, and it must stay that way -- the
    tutor speaks this aloud while telling a learner what is next.
    """
    progress = store.get_progress("sample-learner", "it", instance_path=instance)
    first = progress.next_lesson

    assert first is not None
    assert first.id == "it-fast-01-what-time-is-it"
    assert first.title and "\n" not in first.title
    assert first.objective and "\n" not in first.objective
    assert len(first.objective) < 200, "one spoken line, not a lesson plan"
    assert not hasattr(first, "turns"), "the summary carries no content; that is what get_lesson_content is for"


def test_lessons_nobody_has_converted_still_work_beside_the_converted_ones(instance: Path) -> None:
    """Half a catalog is the normal state and has to be a working state.

    Some languages hold converted units in front of lessons that are still only a title
    and an objective; others hold nothing but the latter. Every one of them has to read
    back cleanly, or converting a language would break the ones still waiting.

    (This docstring used to name the languages, and said "Spanish holds nothing but the
    latter" through two more conversions. The assertion below said the same thing in a
    comment. Both were false the day the Spanish course landed, so neither names a
    language now -- the split is read from the catalog at the bottom of this test.)
    """
    converted = set(_converted_ids())
    progress = store.get_progress("sample-learner", "it", instance_path=instance)
    unconverted = [lesson for lesson in progress.remaining if lesson.id not in converted]

    assert unconverted, "Italian still has lessons nobody has converted"
    for lesson in unconverted:
        content = store.get_lesson_content(lesson.id, instance_path=instance)

        assert content is not None, f"{lesson.id} stopped reading back once its neighbours got content"
        assert (content.turns, content.notes, content.drills) == ((), (), ())
        assert content.source is not None and content.source.origin == "written_for_this_app"

    # A language nobody has converted at all, read from the catalog rather than named:
    # the point of this line is that the unconverted case still works, so it has to keep
    # finding an unconverted language as the converted ones accumulate.
    untouched = sorted(set(dict(store.SEED_LANGUAGES)) - set(_converted_language_codes()))
    assert untouched, "every language is converted; this assertion no longer has a subject"
    for code in untouched:
        progress = store.get_progress("sample-learner", code, instance_path=instance)
        assert progress is not None and progress.next_lesson is not None, (
            f"{code} has no converted units and still has to work"
        )


def test_the_italian_catalog_is_ordered_and_leads_with_the_converted_units(instance: Path) -> None:
    """A learner meets the reviewed material first, and the order has no holes."""
    progress = store.get_progress("sample-learner", "it", instance_path=instance)
    positions = [lesson.position for lesson in progress.remaining]
    converted = set(_converted_ids("it"))

    assert positions == list(range(1, len(positions) + 1)), "contiguous, so 'the next lesson' is unambiguous"
    leading = [lesson.id for lesson in progress.remaining[: len(converted)]]
    assert set(leading) == converted, "the converted units are the first thing a learner is offered"


def test_the_italian_course_reads_back_exactly_as_it_shipped(instance: Path) -> None:
    """What a learner hears in Italian, pinned so a refactor cannot quietly move it.

    Captured when W40 regrouped the file into a list of courses. That change had to
    alter the file's BYTES -- the catalog fingerprint covers them -- while changing no
    lesson, so "unchanged" needed a witness that looks at rows rather than at the file.
    The digest is that witness: it covers every string a learner could hear, read back
    from the database, so a re-wrapped line, a reordered turn or a lost accent moves it.

    A failure here is not a formatting nit. It means the seeded Italian content differs
    from what was reviewed against the page images, and the right response is to find
    out what moved, not to update the constant.
    """
    assert [
        (lesson_id, content.lesson.position, content.source.module, content.source.unit, content.source.page)
        for lesson_id in sorted(_converted_ids("it"))
        for content in [store.get_lesson_content(lesson_id, instance_path=instance)]
    ] == [
        ("it-fast-01-what-time-is-it", 1, "Volume 1", "IV", 83),
        ("it-fast-02-room-service", 2, "Volume 1", "VI", 124),
        ("it-fast-03-taxi-and-haircut", 3, "Volume 1", "IX", 197),
        ("it-fast-04-shopping-for-clothes", 4, "Volume 1", "XIII", 301),
        ("it-fast-05-eating-out", 5, "Volume 1", "XV", 352),
        ("it-fast-06-phone-call-about-a-flat", 6, "Volume 1", "XVII", 408),
    ]

    assert _rows_per_table(instance, "it") == (82, 42, 113), "the six units' turns, notes and drills, as curated"

    digest = hashlib.sha256(_shipped_text(instance, "it").encode("utf-8")).hexdigest()
    assert digest == "d14f06db92fb34b2a383198309238ea65cb6736e43b7b5d3c11381b5568286af", (
        "every spoken string of the Italian course, and one of them has changed"
    )


def test_the_converted_content_reaches_a_robot_that_already_has_a_database(tmp_path: Path) -> None:
    """The integration test the task names, and the failure it exists to prevent.

    A robot in a house already has a database seeded at an older version. Correcting a
    line in the converted lessons reaches it only if SEED_VERSION moves -- _seed returns
    early otherwise -- so this rewinds a seeded database and asserts the content lands.
    """
    assert store.ensure_learner_database(tmp_path).ready is True
    connection = store.connect(tmp_path)
    try:
        connection.execute("DELETE FROM lesson_drills")
        connection.execute(
            "UPDATE schema_meta SET value = ? WHERE key = ?", (str(store.SEED_VERSION - 1), store.SEED_VERSION_KEY)
        )
        connection.commit()
    finally:
        connection.close()

    before = store.get_lesson_content("it-fast-05-eating-out", instance_path=tmp_path)
    assert before.drills == (), "the rewind has to actually remove them, or this proves nothing"

    assert store.ensure_learner_database(tmp_path).seeded is True

    after = store.get_lesson_content("it-fast-05-eating-out", instance_path=tmp_path)
    assert after.drills, "a corrected lesson never reached the robot"
    assert len(after.drills) == len(
        next(lesson for lesson in store._converted_lessons() if lesson["id"] == "it-fast-05-eating-out")["drills"]
    )


@pytest.mark.parametrize(
    ("code", "placeholders", "finished", "start_version"),
    [
        (
            "it",
            [
                "it-01-greetings",
                "it-02-introductions",
                "it-03-numbers",
                "it-04-ordering-food",
                "it-05-directions",
                "it-06-daily-routine",
            ],
            "it-02-introductions",
            str(store.SEED_VERSION - 1),
        ),
        (
            "es",
            [
                "es-01-greetings",
                "es-02-introductions",
                "es-03-numbers",
                "es-04-ordering-food",
                "es-05-directions",
                "es-06-daily-routine",
            ],
            "es-02-introductions",
            str(store.SEED_VERSION - 1),
        ),
        (
            "pt",
            [
                "pt-01-greetings",
                "pt-02-introductions",
                "pt-03-numbers",
                "pt-04-ordering-food",
                "pt-05-directions",
                "pt-06-daily-routine",
            ],
            "pt-02-introductions",
            # NOT SEED_VERSION - 1. A robot that never took the Spanish upgrade still
            # carries 5, and reseeding jumps it straight to 7 in one pass rather than
            # stepping through 6. Every rewind in this suite uses SEED_VERSION - 1, so
            # bumping the version moved them all forward and left the oldest shipped
            # catalog covered by nothing -- which is what this case is for.
            "5",
        ),
    ],
)
def test_the_upgrade_every_installed_robot_will_actually_take(
    tmp_path: Path, code: str, placeholders: list[str], finished: str, start_version: str
) -> None:
    """The one upgrade path nothing else here constructs, and the only one that is real.

    Every other "already seeded" test in this suite rewinds the version marker on a
    database that is ALREADY laid out the way this change leaves it -- placeholders at
    7 to 12, converted units at 1 to 6. No robot is in that state. A robot in a house is
    in the state before the conversion: `it-01-greetings` sitting on position 1, which is
    the position `it-fast-01-what-time-is-it` is about to claim, under a UNIQUE that
    makes two lessons unable to share it.

    So this builds that layout and runs the upgrade against it. It passes because the
    placeholders are upserted to their new positions BEFORE the converted lessons claim
    the old ones -- which is a fact about the order of two statements in _seed, and was
    worth executing rather than reasoning about.
    """
    assert store.ensure_learner_database(tmp_path).ready is True
    # SCOPED to this language. Unscoped, the rewind deleted every converted lesson in
    # the file and then moved only one language's placeholders back, leaving the other
    # language with six vacant positions at the front -- a layout no robot has ever
    # been in, which is the exact unreality this docstring condemns.
    converted = _converted_ids(code)
    assert len(converted) == 6, f"{code} should have six converted units"

    connection = store.connect(tmp_path)
    try:
        # Wind the catalog back to what shipped before this change: the six placeholders
        # on 1 to 6, and no converted lessons at all.
        connection.execute("DELETE FROM lessons WHERE id IN (%s)" % ",".join("?" * len(converted)), converted)
        connection.execute("UPDATE lessons SET position = position - 6 WHERE language_code = ?", (code,))
        connection.execute("UPDATE schema_meta SET value = ? WHERE key = ?", (start_version, store.SEED_VERSION_KEY))
        connection.commit()

        before = {
            str(row["id"]): int(row["position"])
            for row in connection.execute(
                "SELECT id, position FROM lessons WHERE language_code = ?", (code,)
            ).fetchall()
        }
    finally:
        connection.close()

    assert before == dict(zip(placeholders, range(1, 7))), (
        "the rewind has to produce the real pre-conversion layout, or this test proves nothing"
    )

    # A learner who had finished a lesson before the upgrade, to prove the renumbering
    # does not disturb what they did.
    assert store.record_result("sample-learner", finished, "completed", instance_path=tmp_path).recorded

    # Read what this learner had finished BEFORE the upgrade rather than pinning a
    # literal: Spanish carries seeded history that Italian does not, and the claim
    # being made is that the renumbering disturbs nothing, not that any one list holds.
    done_before = [
        lesson.id for lesson in store.get_progress("sample-learner", code, instance_path=tmp_path).completed
    ]
    assert finished in done_before

    result = store.ensure_learner_database(tmp_path)

    assert result.ready is True, f"the upgrade aborted: {result.error}"
    assert result.seeded is True

    progress = store.get_progress("sample-learner", code, instance_path=tmp_path)
    assert [lesson.id for lesson in progress.completed] == done_before, "history survived"
    assert progress.next_lesson.id == converted[0], "and the converted units now lead the catalog"

    # Over the WHOLE catalog, not just what is left to do: the finished lesson is not in
    # `remaining`, so checking that alone would report a hole in the numbering where a
    # learner's own progress is.
    catalog = sorted(lesson.position for lesson in progress.completed + progress.remaining)
    assert catalog == list(range(1, len(catalog) + 1)), "no position was left doubled or vacant"


def test_the_spanish_catalog_is_ordered_and_leads_with_the_converted_units(instance: Path) -> None:
    """The Italian assertion's missing sibling.

    Italian had this check, a shipped-text digest and a row-count pin; Spanish shipped
    six lessons with none of the three. The catalog fingerprint notices an UNANNOUNCED
    edit to the seed constants; none of these notices a WRONG one, which is a different
    question and the one a learner is exposed to.
    """
    # Over the WHOLE catalog, not just what is left. Unlike Italian, the seeded learner
    # HAS Spanish history -- two placeholders completed -- so `remaining` has holes in
    # it where their own progress is, and a contiguity check over it would be checking
    # the fixture rather than the catalog.
    progress = store.get_progress("sample-learner", "es", instance_path=instance)
    catalog = sorted(progress.completed + progress.remaining, key=lambda lesson: lesson.position)
    converted = set(_converted_ids("es"))

    assert [lesson.position for lesson in catalog] == list(range(1, len(catalog) + 1)), (
        "contiguous, so 'the next lesson' is unambiguous"
    )
    assert {lesson.id for lesson in catalog[: len(converted)]} == converted, (
        "the converted units are the first thing a learner is offered"
    )


def test_the_spanish_course_reads_back_exactly_as_it_shipped(instance: Path) -> None:
    """What a learner hears in Spanish, pinned so a refactor cannot quietly move it.

    A failure here is not a formatting nit. It means the seeded Spanish content differs
    from what was read against the page images, and the right response is to find out
    what moved rather than to update the constant. The scan behind this course is 47.9%
    accent-damaged, so a lost accent is the likeliest thing to move and the hardest to
    see by eye.
    """
    assert [
        (lesson_id, content.lesson.position, content.source.module, content.source.unit, content.source.page)
        for lesson_id in sorted(_converted_ids("es"))
        for content in [store.get_lesson_content(lesson_id, instance_path=instance)]
    ] == [
        ("es-fast-01-getting-started-in-class", 1, "Single volume", "2", 7),
        ("es-fast-02-at-the-restaurant", 2, "Single volume", "5", 57),
        ("es-fast-03-getting-around-inside", 3, "Single volume", "10", 120),
        ("es-fast-04-the-familiar-form", 4, "Single volume", "14", 177),
        ("es-fast-05-shopping-at-the-market", 5, "Single volume", "25", 342),
        ("es-fast-06-household-repairs", 6, "Single volume", "38", 526),
    ]

    assert _rows_per_table(instance, "es") == (58, 47, 91), "the six Cycles' turns, notes and drills, as curated"

    digest = hashlib.sha256(_shipped_text(instance, "es").encode("utf-8")).hexdigest()
    assert digest == "3b0b767dce156ef18b245f361518b77715860e1c2659a9f18aac70391849338e", (
        "the Spanish a learner hears has changed; find out what moved before touching this line"
    )


def test_the_portuguese_catalog_is_ordered_and_leads_with_the_converted_units(instance: Path) -> None:
    """The third course, given the check the first two have.

    Spanish shipped without this and the omission had to be caught later; adding it in
    the same change that ships the course is the point.
    """
    progress = store.get_progress("sample-learner", "pt", instance_path=instance)
    catalog = sorted(progress.completed + progress.remaining, key=lambda lesson: lesson.position)
    converted = set(_converted_ids("pt"))

    assert [lesson.position for lesson in catalog] == list(range(1, len(catalog) + 1)), (
        "contiguous, so 'the next lesson' is unambiguous"
    )
    assert {lesson.id for lesson in catalog[: len(converted)]} == converted, (
        "the converted units are the first thing a learner is offered"
    )


def test_the_portuguese_course_reads_back_exactly_as_it_shipped(instance: Path) -> None:
    """What a learner hears in Portuguese, pinned so a refactor cannot quietly move it.

    A failure here is not a formatting nit. Every one of these strings was typed off a
    page image rather than lifted from the OCR text layer, and the reason is that this
    scan's damage is invisible in Portuguese: a dropped accent leaves a stray tilde and
    the tilde is a real letter. If this digest moves, find out what moved before
    touching the line.
    """
    assert [
        (lesson_id, content.lesson.position, content.source.module, content.source.unit, content.source.page)
        for lesson_id in sorted(_converted_ids("pt"))
        for content in [store.get_lesson_content(lesson_id, instance_path=instance)]
    ] == [
        ("pt-fast-01-ordering-breakfast", 1, "Volume I", "2", 2),
        ("pt-fast-02-checking-for-messages", 2, "Volume I", "5", 2),
        ("pt-fast-03-asking-for-directions", 3, "Volume I", "6", 4),
        ("pt-fast-04-finding-an-office", 4, "Volume I", "7", 2),
        ("pt-fast-05-taking-a-taxi", 5, "Volume I", "8", 2),
        ("pt-fast-06-ordering-lunch", 6, "Volume I", "12", 2),
    ]

    assert _rows_per_table(instance, "pt") == (78, 40, 144), "the six lessons' turns, notes and drills, as curated"

    digest = hashlib.sha256(_shipped_text(instance, "pt").encode("utf-8")).hexdigest()
    assert digest == "de6caee7f0c6e3aaa3207b2d78fa58ce073b52ccfc8d830c4a0845265f3b4d3d", (
        "the Portuguese a learner hears has changed; find out what moved before touching this line"
    )


def _positions(connection, language_code: str) -> dict[str, int]:
    """Every lesson id in one language, with the position it holds."""
    return {
        str(row["id"]): int(row["position"])
        for row in connection.execute("SELECT id, position FROM lessons WHERE language_code = ?", (language_code,))
    }


def test_a_language_already_renumbered_once_does_not_move_again(tmp_path: Path) -> None:
    """The third edge case the task names, and the path the NEXT conversion takes.

    Italian was renumbered when it was converted: its placeholders went to 7-12 and six
    converted units took 1-6. Spanish was then converted, which renumbers Spanish -- and
    must leave Italian exactly where it is. Nothing tested that. The two upgrade cases
    that existed both start from a language at 1-6, so a seed that re-applied its shift
    to an already-shifted language would have moved Italian to 13-18 and passed both.

    This constructs the real shape: Italian already at 7-12, Spanish rewound to the
    layout it had before its own conversion, and the version marker rewound so the seed
    actually runs. Then it asserts Spanish moves and Italian does not.

    **What this can and cannot catch, measured rather than assumed.** Positions in
    SEED_LESSONS are ABSOLUTE -- each lesson is upserted to the position it declares --
    so a seed cannot re-apply a relative shift to an already-shifted language. A first
    draft of this test asserted only that Italian was unchanged across the upgrade,
    which given absolute positions is close to unfalsifiable: simulating the defect by
    shifting Italian's declared positions to 13-18 moved the baseline too, so
    before == after still held and only the precondition tripped.

    So the Italian layout is PINNED here rather than merely compared with itself. That
    is what makes it fail for the reason it names: an edit that shifts Italian while
    converting another language -- the realistic human error, since the constants are
    hand-written -- fails on the pin. Shifting Italian's declared positions to 13-18
    was confirmed to fail this assertion.
    """
    assert store.ensure_learner_database(tmp_path).ready is True
    spanish_converted = _converted_ids("es")

    connection = store.connect(tmp_path)
    try:
        # Rewind SPANISH only, to how it looked before its conversion. Italian is left
        # in its already-renumbered state, which is the whole point of this test.
        connection.execute(
            "DELETE FROM lessons WHERE id IN (%s)" % ",".join("?" * len(spanish_converted)), spanish_converted
        )
        connection.execute("UPDATE lessons SET position = position - 6 WHERE language_code = 'es'")
        connection.execute(
            "UPDATE schema_meta SET value = ? WHERE key = ?", (str(store.SEED_VERSION - 1), store.SEED_VERSION_KEY)
        )
        connection.commit()
        italian_before = _positions(connection, "it")
        assert _positions(connection, "es") == {
            f"es-0{n}-{name}": n
            for n, name in enumerate(
                ["greetings", "introductions", "numbers", "ordering-food", "directions", "daily-routine"], 1
            )
        }, "the rewind has to produce the real pre-Spanish layout, or this test proves nothing"
        # PINNED, not merely remembered. See the docstring: comparing Italian with
        # itself cannot fail while positions are declared absolutely.
        assert italian_before == {
            "it-fast-01-what-time-is-it": 1,
            "it-fast-02-room-service": 2,
            "it-fast-03-taxi-and-haircut": 3,
            "it-fast-04-shopping-for-clothes": 4,
            "it-fast-05-eating-out": 5,
            "it-fast-06-phone-call-about-a-flat": 6,
            "it-01-greetings": 7,
            "it-02-introductions": 8,
            "it-03-numbers": 9,
            "it-04-ordering-food": 10,
            "it-05-directions": 11,
            "it-06-daily-routine": 12,
        }, "Italian must start this test ALREADY renumbered, with its converted units leading"
    finally:
        connection.close()

    result = store.ensure_learner_database(tmp_path)
    assert result.ready is True, f"the upgrade aborted: {result.error}"

    connection = store.connect(tmp_path)
    try:
        assert _positions(connection, "it") == italian_before, (
            "Italian moved while another language was being converted. A language "
            "renumbered by an earlier conversion must not be shifted again by a later "
            "one -- its placeholders already sit behind its converted units and there "
            "is nothing to make room for."
        )
        spanish_after = _positions(connection, "es")
    finally:
        connection.close()

    assert spanish_after["es-01-greetings"] == 7, "Spanish placeholders should have moved up behind its units"
    assert sorted(spanish_after.values()) == list(range(1, len(spanish_after) + 1)), "no gap and no collision"


@pytest.mark.parametrize("code", _converted_language_codes())
def test_renumbering_a_placeholder_downwards_is_the_hazard_to_watch(tmp_path: Path, code: str) -> None:
    """Why the upgrade above works, stated as the condition it actually depends on.

    It is NOT that the seed is clever about ordering. It is that this particular
    renumbering moves placeholders UPWARD, into positions nothing holds yet. A later
    conversion that ships FEWER units would move them downward into positions the old
    converted rows still occupy, and UNIQUE (language_code, position) would abort the
    seed on a live robot rather than on anybody's laptop.

    This runs that collision deliberately, so the constraint is a measured fact rather
    than a worry in a document. converting-a-course.md carries the rule that follows.

    Parametrised over every converted language rather than asserted once about Italian.
    The constraint is per language -- UNIQUE (language_code, position) -- so an Italian
    collision is evidence about Italian, and Spanish and Portuguese each went through
    this renumbering afterwards with nothing measuring it.

    The upward half is here for a reason too: without it this test would still pass if
    every UPDATE on this table raised, which would make it a witness for nothing.
    """
    import sqlite3

    assert store.ensure_learner_database(tmp_path).ready is True
    placeholder = _lowest_placeholder(tmp_path, code)

    connection = store.connect(tmp_path)
    try:
        with pytest.raises(sqlite3.IntegrityError) as raised:
            # A placeholder moving down onto a position a converted lesson still holds.
            connection.execute("UPDATE lessons SET position = 1 WHERE id = ?", (placeholder,))
        connection.rollback()

        # And upward, into a position nothing holds: this must NOT raise.
        connection.execute("UPDATE lessons SET position = 99 WHERE id = ?", (placeholder,))
    finally:
        connection.rollback()
        connection.close()

    assert "lessons.language_code, lessons.position" in str(raised.value)


def test_re_seeding_replaces_content_rather_than_piling_it_up(tmp_path: Path) -> None:
    """A corrected unit with FEWER drills must not leave the extra ones behind.

    Upserting each row by position would: the row at position nineteen of the old
    version stays for ever, in a lesson somebody corrected. The seed deletes a lesson's
    content before writing it, and this is what proves that rather than assuming it.
    """
    assert store.ensure_learner_database(tmp_path).ready is True
    first = store.get_lesson_content("it-fast-03-taxi-and-haircut", instance_path=tmp_path)

    connection = store.connect(tmp_path)
    try:
        connection.execute(
            "UPDATE schema_meta SET value = ? WHERE key = ?", (str(store.SEED_VERSION - 1), store.SEED_VERSION_KEY)
        )
        connection.commit()
    finally:
        connection.close()
    assert store.ensure_learner_database(tmp_path).seeded is True

    second = store.get_lesson_content("it-fast-03-taxi-and-haircut", instance_path=tmp_path)

    assert len(second.turns) == len(first.turns)
    assert len(second.drills) == len(first.drills)
    assert [drill.cue for drill in second.drills] == [drill.cue for drill in first.drills]


@pytest.mark.parametrize("code", _converted_language_codes())
def test_a_learner_part_way_through_a_converted_language_keeps_their_history(instance: Path, code: str) -> None:
    """The edge case the conversion could plausibly break.

    Results point at lesson ids. The six placeholder lessons kept theirs when they were
    renumbered, so somebody who finished one before the conversion still has, and the
    catalog around them changing does not rewrite what they did.

    Parametrised because this was an Italian-only claim while three languages had been
    renumbered. Nothing about it is Italian: the ids, the placeholder and the expected
    next lesson are all read from the catalog rather than spelled, so the case a fourth
    course adds arrives already covered.
    """
    converted = set(_converted_ids(code))
    progress = store.get_progress("sample-learner", code, instance_path=instance)
    # Read rather than assumed: the seeded learner has history in one language and none
    # in the others, so a literal list here would be a claim about the fixture, and the
    # placeholder this finishes has to be one they have not already finished.
    before = [lesson.id for lesson in progress.completed]
    unfinished = [lesson for lesson in progress.remaining if lesson.id not in converted]
    assert unfinished, f"{code} has no unfinished placeholder left to finish"
    placeholder = sorted(unfinished, key=lambda lesson: lesson.position)[0].id
    remaining_converted = sorted(
        (lesson.position, lesson.id) for lesson in progress.remaining if lesson.id in converted
    )
    first_converted = remaining_converted[0][1]

    recorded = store.record_result("sample-learner", placeholder, "completed", instance_path=instance)
    assert recorded.recorded is True

    progress = store.get_progress("sample-learner", code, instance_path=instance)

    assert [lesson.id for lesson in progress.completed] == before + [placeholder]
    assert progress.next_lesson.id == first_converted, "still offered the first unfinished lesson"


# ------------------------------------------------- how far a learner can actually get


@pytest.mark.asyncio
@pytest.mark.parametrize("code", _converted_language_codes())
async def test_a_learner_is_offered_a_converted_lesson_and_can_finish_it(instance: Path, code: str) -> None:
    """A converted unit, end to end, through the real dispatch path.

    W24 shipped this test with half of it missing and said so: a learner was offered a
    converted unit and could finish it, but nothing in the app could read the unit's
    dialogue, notes or drills, so the tutor improvised from the objective line while the
    real material sat in the database underneath it. Its closing assertion was written
    to fail the day a tool reached that material, which is what W18 did.

    So the missing half is here now, asserted rather than promised: between opening the
    lesson and saving it, the same conversation reads back the unit's own turns, notes
    and drills. What is still NOT asserted anywhere is that a model teaches well from
    them -- that is the manual session, and no test claims it.

    Parametrised over every converted language. This was an Italian-only claim through
    two more conversions, and it is the one assertion that goes through the tools a
    robot actually calls rather than through the store -- so "a Portuguese learner is
    offered a converted unit first" was, until this ran, asserted about the lessons
    table and nowhere else. The language name, lesson id and title are all read from
    the catalog; nothing here is spelled per language.
    """
    from reachy_language_tutor.tools import core_tools
    from reachy_language_tutor.lesson_session import LessonSessionHolder
    from reachy_language_tutor.tools.core_tools import ToolDependencies

    language = dict(store.SEED_LANGUAGES)[code]
    expected = sorted(
        (lesson.position, lesson.id, lesson.title)
        for lesson in store.get_progress("sample-learner", code, instance_path=instance).remaining
        if lesson.id in set(_converted_ids(code))
    )[0]
    expected_id, expected_title = expected[1], expected[2]

    learner = store.SEED_LEARNERS[0][0]
    deps = ToolDependencies(
        reachy_mini=MagicMock(),
        movement_manager=MagicMock(),
        instance_path=instance,
        current_learner_id=learner,
        lesson_session=LessonSessionHolder(learner),
    )

    async def call(name: str, args: dict) -> dict:
        return await core_tools.dispatch_tool_call(name, json.dumps(args), deps)

    progress = await call("get_progress", {"language": language})
    assert "error" not in progress
    assert progress["next_lesson"]["id"] == expected_id, (
        f"the first thing a {language} learner is offered is a converted unit"
    )

    started = await call("start_lesson", {"language": language})
    assert started["started"] is True
    assert started["lesson"]["title"] == expected_title

    pinned = deps.lesson_session.read_for(learner)
    assert pinned is not None and pinned.lesson_id == expected_id

    # The half W24 could not run. Called between the open and the save, because that is
    # the only window in which a lesson is pinned -- which is itself the point.
    material = await call("get_lesson_content", {})
    assert material["have_content"] is True, material.get("reason")
    assert material["lesson"]["title"] == expected_title

    content = store.get_lesson_content(expected_id, instance_path=instance)
    assert content.turns and content.notes and content.drills, "the material exists in the database"

    # Compared against the database rather than against a copy of the expected text: the
    # claim is that the conversation reaches the unit that shipped, not that it reaches
    # some text somebody typed into this test.
    assert len(material["dialogue"]) == len(content.turns)
    assert material["dialogue"][0]["speaker"] == content.turns[0].speaker
    assert material["dialogue"][0]["text"] == content.turns[0].text
    assert len(material["notes"]) == len(content.notes)
    assert len(material["drills"]) == len(content.drills)

    # A cue-response drill is the one a tutor can mark, so it is the one worth pinning:
    # both halves present, and not the same string, or there is nothing to ask.
    #
    # Whether this unit HAS any is a property of the unit rather than an invariant, and
    # asserting it unconditionally is what made this test Italian-shaped: the Portuguese
    # course ships none on purpose, because every candidate cue in that volume admitted
    # more than one right answer. So the claim is the one that holds either way -- the
    # tutor sees exactly the cue-response drills the database holds, and each one it
    # sees is askable.
    shipped = [drill for drill in content.drills if drill.kind == "cue_response"]
    askable = [drill for drill in material["drills"] if drill["kind"] == "cue_response"]
    assert len(askable) == len(shipped), "the tutor sees a different set of cue-response drills than shipped"
    for drill in askable:
        # .get rather than [], so a drill rendered with the wrong kind's fields fails
        # saying which drill and what was missing instead of raising KeyError.
        assert drill.get("cue"), f"cue-response drill {drill['position']} has no cue to say: {sorted(drill)}"
        assert drill.get("expected_response"), f"cue-response drill {drill['position']} has no answer to check"
        if (expected_id, drill["cue"]) in KNOWN_SELF_ANSWERING_DRILLS:
            continue
        assert drill["cue"] != drill["expected_response"], (
            f"drill {drill['position']} of {expected_id} answers itself. If this is a drill a tutor "
            f"cannot mark, it is a repetition drill wearing the wrong kind -- fix the drill. Adding it "
            f"to KNOWN_SELF_ANSWERING_DRILLS is for a case already filed, and D37 is the only one."
        )

    # The id stays application state. Handing it back would let the next turn name a
    # lesson, which is the whole reason start_lesson does not return one either.
    assert expected_id not in json.dumps(material)

    saved = await call("finish_lesson", {"outcome": "completed", "score": 80})
    assert saved["recorded"] is True
    assert saved["lesson_title"] == expected_title

    after = await call("get_progress", {"language": language})
    assert after["completed_count"] == progress["completed_count"] + 1
    assert after["last_completed"] == expected_title

    # And the window closes with the lesson: saving it unpins it, so the material is no
    # longer readable and the tutor is told that rather than handed the last lesson again.
    closed = await call("get_lesson_content", {})
    assert closed["have_content"] is False
    assert closed["reason"] == "no_lesson_running"


# ------------------------------------------------------------- the written record


def test_the_curation_log_accounts_for_every_lesson_that_shipped(instance: Path) -> None:
    """A judgement nobody can review is not a judgement -- the task's own words.

    Every shipped lesson has to appear in the log by id, so a reader can find what was
    replaced in it and why.
    """
    for course in store._converted_courses():
        name = course["name"]
        assert name in CURATION_LOGS, f"{name!r} shipped with no curation log registered"
        log = CURATION_LOGS[name].read_text(encoding="utf-8")
        for lesson in course["lessons"]:
            lesson_id = str(lesson["id"])
            assert lesson_id in log, f"{lesson_id} shipped with no curation record in {CURATION_LOGS[name].name}"


def test_the_curation_log_accounts_for_the_units_that_did_not_ship(instance: Path) -> None:
    """Dropping a unit is a decision too, and the interesting one.

    Twelve of eighteen units in this volume were not converted. A log that recorded only
    the six that shipped would leave the actual editorial judgement unwritten.
    """
    log = CURATION_LOG.read_text(encoding="utf-8")

    for unit in ("XI", "XII", "I", "II", "III"):
        assert re.search(rf"\b{unit}\b", log), f"unit {unit} was not converted and the log does not say why"


def test_the_method_is_written_down_well_enough_to_repeat(instance: Path) -> None:
    """The acceptance criterion about the NEXT language, which is the point of the task.

    Not a word count for its own sake: the method has to name the tool that extracts the
    text, the check against the page image, and the rights step, or the next person
    re-derives all three.
    """
    method = METHOD.read_text(encoding="utf-8")
    # Hyphens and case folded out, so that spelling SHA-256 correctly in prose is not a
    # test failure. What is being asserted is that the step is described, not how the
    # acronym is typed.
    flattened = method.lower().replace("-", "")

    for expected in ("pdftotext", "pdftoppm", "sha256", "rights", "page image", "seed_version"):
        assert expected in flattened, f"the method does not mention {expected}"
