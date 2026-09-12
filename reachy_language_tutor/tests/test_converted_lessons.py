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
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from reachy_language_tutor.learners import store
from reachy_language_tutor.learners.models import DRILL_KINDS


# The units a person read and approved for conversion, by the roman numeral the course
# itself uses. Six of eighteen in Volume 1: the rest were set in an embassy, at a
# border, or at a currency desk, or leant on an official in uniform, and the curation
# log says which and why. Shipping a seventh means adding it here first.
APPROVED_UNITS = frozenset({"IV", "VI", "IX", "XIII", "XV", "XVII"})

DOCS = Path(__file__).resolve().parents[2] / "docs"
CURATION_LOG = DOCS / "curation-log-italian-fast.md"
METHOD = DOCS / "converting-a-course.md"


@pytest.fixture
def instance(tmp_path: Path) -> Path:
    """Return a prepared learner database at a temporary instance path."""
    assert store.ensure_learner_database(tmp_path).ready is True
    return tmp_path


def _converted_ids() -> list[str]:
    return [str(lesson["id"]) for lesson in store._converted_lessons()]


def _shipped_text(instance_path: Path) -> str:
    """Every string a learner could hear from a converted lesson, read back from disk.

    From the DATABASE rather than from the JSON file, because the database is what the
    tutor reads: a fault that the loader introduced between the two would be invisible
    to a test that checked the file.
    """
    pieces: list[str] = []
    for lesson_id in _converted_ids():
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
    assert len(ids) == 6, "six units were converted; the file should still hold six"

    for lesson_id in ids:
        content = store.get_lesson_content(lesson_id, instance_path=instance)

        assert content is not None, f"{lesson_id} did not reach the database"
        assert content.lesson.language_code == "it"
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
        for drill in content.drills:
            if drill.kind != "cue_response":
                continue
            # An unresolved either/or: "X o Y?" offers a free choice, so one branch
            # cannot be the only answer.
            if re.search(r"\w\s+o\s+\w[^?]*\?\s*$", drill.cue):
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
    permitted_slashes = {"s/he", "americano/a", "and/or"}
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
    for lesson_id in _converted_ids():
        source = store.get_lesson_content(lesson_id, instance_path=instance).source

        assert source is not None
        assert source.origin == "converted_from_course"
        assert source.unit in APPROVED_UNITS, f"{lesson_id} cites unit {source.unit}, which nobody approved"
        assert source.course == "FSI Italian FAST, Volume 1"
        assert source.module and source.page and source.page > 0


def test_the_approved_units_are_all_used(instance: Path) -> None:
    """The other direction: the list describes what shipped, rather than outliving it.

    An allow-list that slowly fills with units nobody ships any more stops being a
    record of a decision and becomes a list of permissions nobody is using.
    """
    cited = {store.get_lesson_content(lesson_id, instance_path=instance).source.unit for lesson_id in _converted_ids()}

    assert cited == APPROVED_UNITS


def test_the_provenance_is_enough_to_find_the_page_again(instance: Path) -> None:
    """Provenance is for checking a suspect line, so it has to be specific enough.

    Course, volume, unit AND page. Three of the four would leave somebody leafing
    through a 456-page scan, which is how a correction stops being worth making.
    """
    for lesson_id in _converted_ids():
        source = store.get_lesson_content(lesson_id, instance_path=instance).source

        assert "FSI" in source.course and "Italian" in source.course
        assert source.module == "Volume 1"
        assert 1 <= source.page <= 456, "a page inside the volume this course actually has"


def test_nothing_military_or_official_survived_the_curation(instance: Path) -> None:
    """A BACKSTOP, not the control. The control is the approved-unit list above.

    This is a list of words to refuse, and such a list is only ever as complete as the
    last person to read it -- which is exactly why it is not what this file relies on.
    It is here because it is cheap and it catches the realistic failure: somebody edits
    one line of an approved unit and puts a customs officer back into it.

    The terms are the ones the task names, plus the Italian for each, since the content
    is Italian and an English-only screen would see none of it.
    """
    text = _shipped_text(instance).lower()

    forbidden = {
        "military": ["militare", "esercito", "caserma", "generale", "ammiraglio", "colonnello", "sergente"],
        "embassy": ["ambasciata", "ambassador", "consolato", "embassy", "consulate", "diplomatic"],
        "uniformed authority": ["polizia", "poliziotto", "carabinier", "questura", "police"],
        "border": ["dogana", "doganiere", "customs", "frontiera", "passaporto", "passport", "guardia di finanza"],
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
    course = store._converted_lesson_course()

    assert "Foreign Service Institute" in course["rights"]
    assert "1992" in course["rights"]
    assert len(course["rights"]) > 120, "a rights position needs saying, not asserting"
    assert course["source_sha256"], "the file it was read from is identified"
    assert len(course["source_sha256"]) == 64


# -------------------------------------------------------------- alongside the rest


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

    Italian now holds six converted units and six lessons that are still only a title
    and an objective, and Spanish holds nothing but the latter. Every one of them has to
    read back cleanly, or converting a language would break the four still waiting.
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

    spanish = store.get_progress("sample-learner", "es", instance_path=instance)
    assert spanish is not None and spanish.next_lesson is not None, "an unconverted language still works"


def test_the_italian_catalog_is_ordered_and_leads_with_the_converted_units(instance: Path) -> None:
    """A learner meets the reviewed material first, and the order has no holes."""
    progress = store.get_progress("sample-learner", "it", instance_path=instance)
    positions = [lesson.position for lesson in progress.remaining]
    converted = set(_converted_ids())

    assert positions == list(range(1, len(positions) + 1)), "contiguous, so 'the next lesson' is unambiguous"
    leading = [lesson.id for lesson in progress.remaining[: len(converted)]]
    assert set(leading) == converted, "the converted units are the first thing a learner is offered"


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


def test_the_upgrade_every_installed_robot_will_actually_take(tmp_path: Path) -> None:
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
    converted = _converted_ids()

    connection = store.connect(tmp_path)
    try:
        # Wind the catalog back to what shipped before this change: the six placeholders
        # on 1 to 6, and no converted lessons at all.
        connection.execute("DELETE FROM lessons WHERE id IN (%s)" % ",".join("?" * len(converted)), converted)
        connection.execute("UPDATE lessons SET position = position - 6 WHERE language_code = 'it'")
        connection.execute(
            "UPDATE schema_meta SET value = ? WHERE key = ?", (str(store.SEED_VERSION - 1), store.SEED_VERSION_KEY)
        )
        connection.commit()

        before = {
            str(row["id"]): int(row["position"])
            for row in connection.execute("SELECT id, position FROM lessons WHERE language_code = 'it'").fetchall()
        }
    finally:
        connection.close()

    assert before == {
        "it-01-greetings": 1,
        "it-02-introductions": 2,
        "it-03-numbers": 3,
        "it-04-ordering-food": 4,
        "it-05-directions": 5,
        "it-06-daily-routine": 6,
    }, "the rewind has to produce the real pre-conversion layout, or this test proves nothing"

    # A learner who had finished a lesson before the upgrade, to prove the renumbering
    # does not disturb what they did.
    assert store.record_result("sample-learner", "it-02-introductions", "completed", instance_path=tmp_path).recorded

    result = store.ensure_learner_database(tmp_path)

    assert result.ready is True, f"the upgrade aborted: {result.error}"
    assert result.seeded is True

    progress = store.get_progress("sample-learner", "it", instance_path=tmp_path)
    assert [lesson.id for lesson in progress.completed] == ["it-02-introductions"], "history survived"
    assert progress.next_lesson.id == converted[0], "and the converted units now lead the catalog"

    # Over the WHOLE catalog, not just what is left to do: the finished lesson is not in
    # `remaining`, so checking that alone would report a hole in the numbering where a
    # learner's own progress is.
    catalog = sorted(lesson.position for lesson in progress.completed + progress.remaining)
    assert catalog == list(range(1, len(catalog) + 1)), "no position was left doubled or vacant"


def test_renumbering_a_placeholder_downwards_is_the_hazard_to_watch(tmp_path: Path) -> None:
    """Why the upgrade above works, stated as the condition it actually depends on.

    It is NOT that the seed is clever about ordering. It is that this particular
    renumbering moves placeholders UPWARD, into positions nothing holds yet. A later
    conversion that ships FEWER units would move them downward into positions the old
    converted rows still occupy, and UNIQUE (language_code, position) would abort the
    seed on a live robot rather than on anybody's laptop.

    This runs that collision deliberately, so the constraint is a measured fact rather
    than a worry in a document. converting-a-course.md carries the rule that follows.
    """
    import sqlite3

    assert store.ensure_learner_database(tmp_path).ready is True

    connection = store.connect(tmp_path)
    try:
        with pytest.raises(sqlite3.IntegrityError) as raised:
            # A placeholder moving down onto a position a converted lesson still holds.
            connection.execute("UPDATE lessons SET position = 1 WHERE id = 'it-01-greetings'")
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


def test_a_learner_part_way_through_italian_keeps_their_history(instance: Path) -> None:
    """The edge case the conversion could plausibly break.

    Results point at lesson ids. The six placeholder lessons kept theirs when they were
    renumbered, so somebody who finished one before the conversion still has, and the
    catalog around them changing does not rewrite what they did.
    """
    recorded = store.record_result("sample-learner", "it-03-numbers", "completed", instance_path=instance)
    assert recorded.recorded is True

    progress = store.get_progress("sample-learner", "it", instance_path=instance)

    assert [lesson.id for lesson in progress.completed] == ["it-03-numbers"]
    assert progress.next_lesson.id == "it-fast-01-what-time-is-it", "still offered the first unfinished lesson"


# ------------------------------------------------- how far a learner can actually get


@pytest.mark.asyncio
async def test_a_learner_is_offered_a_converted_lesson_and_can_finish_it(instance: Path) -> None:
    """How far the tutor can take somebody through a converted unit today, exactly.

    **Read the limit of this test before trusting it.** It runs the real dispatch path
    the realtime session uses, and it shows that a learner asking for Italian is offered
    a converted unit, that the app pins it, and that finishing it is recorded against
    it. What it does NOT show -- because nothing in the app can do it yet -- is the
    tutor speaking that unit's dialogue, notes or drills. No tool reads lesson content;
    that is W18's job, and until it lands the tutor still improvises from the objective
    line while the real material sits in the database underneath it.

    So this is the honest half of the task's "end to end in the simulator" criterion:
    the half that can be executed rather than asserted. The other half is named in the
    completion notes and in the curation log rather than quietly left out.
    """
    from reachy_language_tutor.tools import core_tools
    from reachy_language_tutor.lesson_session import LessonSessionHolder
    from reachy_language_tutor.tools.core_tools import ToolDependencies

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

    progress = await call("get_progress", {"language": "Italian"})
    assert "error" not in progress
    assert progress["next_lesson"]["id"] == "it-fast-01-what-time-is-it", (
        "the first thing an Italian learner is offered is a converted unit"
    )

    started = await call("start_lesson", {"language": "Italian"})
    assert started["started"] is True
    assert started["lesson"]["title"] == "What time is it?"

    pinned = deps.lesson_session.read_for(learner)
    assert pinned is not None and pinned.lesson_id == "it-fast-01-what-time-is-it"

    saved = await call("finish_lesson", {"outcome": "completed", "score": 80})
    assert saved["recorded"] is True
    assert saved["lesson_title"] == "What time is it?"

    after = await call("get_progress", {"language": "Italian"})
    assert after["completed_count"] == progress["completed_count"] + 1
    assert after["last_completed"] == "What time is it?"

    # And the limit itself, asserted rather than described: the content the learner just
    # "completed" was never reachable by any tool in that conversation.
    content = store.get_lesson_content("it-fast-01-what-time-is-it", instance_path=instance)
    assert content.turns and content.drills, "the material exists in the database"
    tools_dir = Path(core_tools.__file__).resolve().parent
    # rglob and a broader match than one function name: W18 could land in a subpackage,
    # or reach the content through another accessor, and either way the stated limit
    # would be stale while this still passed.
    reaches_content = re.compile(r"get_lesson_content|lesson_content|lesson_drills|lesson_dialogue_turns")
    readers = [
        path.name for path in tools_dir.rglob("*.py") if reaches_content.search(path.read_text(encoding="utf-8"))
    ]
    assert readers == [], (
        "a tool now reads lesson content -- W18 has landed, so this test's stated limit is "
        "stale and the end-to-end claim can finally be made properly"
    )


# ------------------------------------------------------------- the written record


def test_the_curation_log_accounts_for_every_lesson_that_shipped(instance: Path) -> None:
    """A judgement nobody can review is not a judgement -- the task's own words.

    Every shipped lesson has to appear in the log by id, so a reader can find what was
    replaced in it and why.
    """
    log = CURATION_LOG.read_text(encoding="utf-8")

    for lesson_id in _converted_ids():
        assert lesson_id in log, f"{lesson_id} shipped with no curation record"


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
