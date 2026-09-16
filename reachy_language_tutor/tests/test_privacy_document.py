"""The privacy document must keep saying what the code does.

A privacy document that overstates the code is worse than none, because a household
relies on it. Every specific claim in docs/privacy-and-consent.md was checked against
the implementation in the session that wrote it -- but "checked when written" decays,
and the task that commissioned the document names exactly that decay as an edge case:
a claim that was true when written and is falsified by a later change.

So the claims that CAN be pinned mechanically are pinned here. Two kinds:

  * the consent wording quoted in the document must be the wording the code shows,
    character for character, so the two cannot drift apart;
  * a handful of factual claims -- nothing expires, erasure does not reach remembered
    facts, both files are owner-only, recognition is not switched on -- are re-measured
    here, so a later change that falsifies the document fails a test instead of quietly
    making it a lie.

What this CANNOT pin is the prose: whether "what has not been done" is still complete,
and whether the wording is meaningful to a real reader. Both are named in the document
as unchecked, and neither is checkable here.
"""

from __future__ import annotations
import re
import stat
import textwrap
from pathlib import Path

import pytest

from reachy_language_tutor import memory
from reachy_language_tutor.faces import THRESHOLD_CALIBRATED, consent_statement
from reachy_language_tutor.learners import LOCAL_PROFILE_STATEMENT, store
from reachy_language_tutor.startup_settings import set_fallback_learner


DOCUMENT = Path(__file__).resolve().parents[2] / "docs" / "privacy-and-consent.md"
PLAN = Path(__file__).resolve().parents[2] / "docs" / "plan.md"


def _document() -> str:
    assert DOCUMENT.exists(), f"{DOCUMENT} is missing; the privacy document is a deliverable"
    return DOCUMENT.read_text(encoding="utf-8")


def _quoted_notices(text: str) -> list[str]:
    """Return the blockquoted notices, unwrapped back to the paragraphs behind them.

    The document wraps long lines to stay readable, so a character-for-character
    comparison has to undo that first. Paragraphs are joined on single spaces, which is
    exactly what the wrapping split them on.
    """
    notices = []
    for block in re.findall(r"(?:^> ?.*\n)+", text, flags=re.M):
        paragraphs = []
        for paragraph in re.split(r"\n>\s*\n", block.strip("\n")):
            lines = [line.lstrip("> ").rstrip() for line in paragraph.splitlines()]
            paragraphs.append(" ".join(line for line in lines if line))
        notices.append("\n\n".join(paragraphs))
    return notices


# ------------------------------------------------- the wording cannot drift


def test_the_document_quotes_both_notices_exactly_as_the_code_shows_them() -> None:
    """The unit test the task asks for, over both scopes rather than one.

    A household is shown the constant; a lawyer, or the household later, reads the
    document. If those two texts differ, the document is evidence of something nobody
    was told -- which is the same failure the stored statement_text exists to prevent,
    one layer up.
    """
    notices = _quoted_notices(_document())

    assert len(notices) == 2, f"expected the two notices as blockquotes, found {len(notices)}"
    assert notices[0] == consent_statement(), (
        "the face-recognition wording in the document is not what the enrolment flow shows"
    )
    assert notices[1] == LOCAL_PROFILE_STATEMENT, (
        "the no-face wording in the document is not what the registration flow shows"
    )


def test_the_unwrapping_would_notice_a_changed_sentence() -> None:
    """The comparison above is only worth as much as its normalisation.

    Joining wrapped lines could paper over a real edit -- so this plants one and checks
    the machinery reports a difference rather than smoothing it away.
    """
    real = consent_statement()
    tampered = real.replace("It keeps the numbers.", "It keeps the pictures.")
    assert tampered != real, "the sentence this test plants is not in the notice any more"

    wrapped = "\n>\n".join(
        "\n".join("> " + line for line in textwrap.wrap(paragraph, width=86)) for paragraph in tampered.split("\n\n")
    )

    assert _quoted_notices(wrapped + "\n") == [tampered]
    assert _quoted_notices(wrapped + "\n") != [real]


# --------------------------------------- the factual claims, re-measured


def test_every_delete_the_database_can_run_is_one_of_the_erasures_a_person_asked_for() -> None:
    """The document says nothing expires. Checked as an allow-list, not a word search.

    The first version of this scanned for "DELETE FROM" and a timestamp column on the
    same line. A review ran that predicate against eight plausible retention sweeps and
    it caught one: the multi-line implicit-concatenation form this codebase itself uses
    for _DELETE_LEARNER_SQL walked through, as did a triple-quoted statement, a sweep on
    granted_at, an age filter computed in Python, and a TTL calling delete_faceprint.
    A deny-list of one spelling, guarding the claim a household relies on -- the exact
    shape this repository's own rule names, one level up from the code it was written
    for.

    So this names what deletion is PERMITTED to be: three named constants, each an
    erasure somebody asked for, and no DELETE written inline. _DELETE_FACEPRINT_SQL is
    executed twice -- by delete_faceprint and by the replace inside save_faceprint --
    so three constants, four uses.

    WHAT IT STILL DOES NOT CATCH, because a second review planted these and two got
    through: an annotated assignment (now caught) and an inline execute (now caught),
    but NOT a sweep that selects stale ids in Python and then passes them to the
    already-permitted _FORGET_LEARNER_SQL. That changes no SQL at all, so nothing here
    moves. The document's claim is therefore about SQL-level retention and is written
    that way; a Python-level sweep would need a reader to notice it.
    """
    import ast

    source = Path(store.__file__).resolve().read_text(encoding="utf-8")
    tree = ast.parse(source)

    # Assign AND AnnAssign: `_TTL: str = "DELETE FROM ..."` is an AnnAssign and an
    # earlier version of this walk never visited one, so an annotated sweep was
    # invisible to the guard. Both are collected, and any DELETE that is NOT bound to
    # a name is reported separately below -- an inline statement handed straight to
    # execute() needs no constant at all, which is the other way round this went.
    personal = {table.upper() for table in store._PERSONAL_TABLES}

    def _deletes_a_person(text: str) -> bool:
        upper = text.upper()
        return "DELETE FROM" in upper and any(table in upper for table in personal)

    deleting = set()
    bound: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
            continue
        text = ast.get_source_segment(source, node.value) or ""
        if not _deletes_a_person(text):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        deleting |= {t.id for t in targets if isinstance(t, ast.Name)}
        # The literal inside this assignment is accounted for by the name it binds,
        # so it must not also be reported as an inline statement below.
        bound |= {id(inner) for inner in ast.walk(node.value)}

    # Anything left that deletes a person and is NOT bound to a name. An inline
    # statement handed straight to execute() needs no constant at all, which is how a
    # sweep could sit outside an allow-list built only from assignments. Course
    # content is deleted inline on purpose and holds nobody, so the rule is scoped to
    # the module's own personal tables rather than to the words DELETE FROM.
    inline = [
        f"line {node.lineno}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and _deletes_a_person(node.value)
        and id(node) not in bound
    ]

    assert inline == [], (
        "a DELETE touching personal data is written inline rather than bound to a named "
        f"constant, so the allow-list below cannot see it: {inline}"
    )

    assert deleting == {
        "_DELETE_FACEPRINT_SQL",  # stop recognising me, and the replace in save_faceprint
        "_FORGET_LEARNER_SQL",  # forget me entirely
        "_DELETE_LEARNER_SQL",  # undo an enrolment that did not finish
    }, (
        "the set of DELETE statements changed. Every one of them is an erasure somebody "
        f"asked for; if a new one deletes by age, docs/privacy-and-consent.md is wrong. Got: {sorted(deleting)}"
    )

    # And none of them is conditional on a time column, which is what a sweep needs.
    for name in deleting:
        statement = getattr(store, name).upper()
        for column in ("CREATED_AT", "RECORDED_AT", "GRANTED_AT"):
            assert column not in statement, f"{name} now filters on {column}, so something deletes by age"


def test_the_only_thing_that_does_expire_is_recorded(tmp_path: Path) -> None:
    """One store DOES drop data on its own, and the document has to say which.

    add_memory_fact keeps the newest MAX_FACTS and discards the rest, with no operator
    involved -- so "nothing expires" was true of the database and false of the memory
    file. Measured here rather than read off the constant, because the constant being 60
    does not prove the eviction happens.
    """
    for index in range(memory.MAX_FACTS + 5):
        memory.add_memory_fact(tmp_path, f"fact number {index}")

    kept = memory.list_memory_facts(tmp_path)
    assert len(kept) == memory.MAX_FACTS, f"expected the store to cap at {memory.MAX_FACTS}, kept {len(kept)}"
    texts = " ".join(fact.text for fact in kept)
    assert "fact number 0" not in texts, "the oldest fact survived, so nothing was evicted"
    assert f"fact number {memory.MAX_FACTS + 4}" in texts, "the newest fact was not kept"

    document = _document()
    assert str(memory.MAX_FACTS) in document, (
        f"the memory store evicts at {memory.MAX_FACTS} facts and the document does not say so"
    )


def test_erasure_still_does_not_reach_remembered_facts(tmp_path: Path) -> None:
    """The document records this as a real gap. If it is ever closed, say so there.

    Written to fail in BOTH directions: it is a measurement of current behaviour, and
    the message says what to do when the behaviour changes, because a gap quietly fixed
    leaves the document understating the app -- which is a smaller harm than
    overstating it, but still a document that is wrong.
    """
    assert store.ensure_learner_database(tmp_path).ready is True
    agreed = store.record_consent(
        "Zebediah Quixotic",
        scope="local_profile",
        statement_id="test.v1",
        statement_text="Wording used by the tests.",
        granted_by="the_person_themselves",
        granted_via="operator_at_the_robot",
        instance_path=tmp_path,
    )
    assert agreed.recorded is True and agreed.learner_id is not None
    memory.add_memory_fact(tmp_path, "Zebediah Quixotic is scared of the dentist")
    remembered = memory.memory_path_for_instance(tmp_path)
    assert "Zebediah" in remembered.read_text(encoding="utf-8"), "the fact never reached the file"

    assert store.forget_learner_entirely(agreed.learner_id, instance_path=tmp_path).erased is True

    assert remembered.exists() and "Zebediah" in remembered.read_text(encoding="utf-8"), (
        "erasure now clears remembered facts -- good, and docs/privacy-and-consent.md still "
        "lists that as an open gap, so update it and delete this test"
    )
    assert "does NOT remove" in _document(), "the document no longer records this gap"


def test_every_file_the_app_writes_beside_the_database_is_owner_only(tmp_path: Path) -> None:
    """The document says all three are -rw-------. Measured, over whatever is there.

    NOT a list of the files to check. The first version of this test named two, and the
    third -- startup_settings.json, holding a learner id -- was at 0o644 while sitting
    in the same directory; reverting its fix left this test green, which is how a guard
    that enumerates its own subjects fails. So it exercises the three writers and then
    checks EVERY file that appears, whatever it is called.
    """
    assert store.ensure_learner_database(tmp_path).ready is True
    agreed = store.record_consent(
        "Zebediah Quixotic",
        scope="local_profile",
        statement_id="test.v1",
        statement_text="Wording used by the tests.",
        granted_by="the_person_themselves",
        granted_via="operator_at_the_robot",
        instance_path=tmp_path,
    )
    assert agreed.recorded is True and agreed.learner_id is not None
    memory.add_memory_fact(tmp_path, "a fact naming somebody")
    set_fallback_learner(tmp_path, agreed.learner_id)

    written = sorted(path for path in tmp_path.iterdir() if path.is_file())
    assert len(written) >= 3, f"expected at least the three files, found {[p.name for p in written]}"

    offenders = {
        path.name: oct(stat.S_IMODE(path.stat().st_mode))
        for path in written
        if stat.S_IMODE(path.stat().st_mode) != 0o600
    }
    assert offenders == {}, (
        f"these files in the instance directory are not owner-only, and the document says they are: {offenders}"
    )


def test_no_file_is_ever_briefly_readable_on_its_way_to_disk(tmp_path: Path) -> None:
    """The "even briefly" claim, which was false the first time it was written.

    The MEMORY file is written to a temp path and renamed; the settings file is not --
    it is written straight to its final path. Both now create with os.open at 0o600, so
    neither exists at the umask default even briefly, and the temp file is the one with
    a window to race. An earlier version of this docstring said both were renamed, and
    the watcher only ever matched the memory temp, so the settings half of the claim
    rested on the final-mode check in the test above rather than on this race.
    """
    import os
    import threading

    previous = os.umask(0o022)
    try:
        seen: list[int] = []
        stop = threading.Event()

        def watch() -> None:
            while not stop.is_set():
                for path in tmp_path.glob(".*"):
                    try:
                        seen.append(stat.S_IMODE(path.stat().st_mode))
                    except OSError:
                        pass

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        try:
            for index in range(50):
                memory.add_memory_fact(tmp_path, f"fact number {index}")
                set_fallback_learner(tmp_path, f"id-{index}")
        finally:
            stop.set()
            watcher.join(timeout=2)

        exposed = sorted({mode for mode in seen if mode != 0o600})
        assert exposed == [], f"a temp file was observable at {[oct(m) for m in exposed]}, not owner-only"
    finally:
        os.umask(previous)


def test_the_document_says_recognition_is_not_switched_on_while_that_is_true() -> None:
    """A claim with an expiry date, so it fails the day somebody flips the flag."""
    said = "`THRESHOLD_CALIBRATED` is `False`" in _document()

    if THRESHOLD_CALIBRATED:
        assert not said, "recognition is calibrated now, so docs/privacy-and-consent.md is out of date"
    else:
        assert said, "the document no longer records that recognition is switched off"


# ----------------------------------------------------- it must be findable


def test_the_plan_links_to_the_document() -> None:
    """The last acceptance criterion. A document nobody can find is not a document."""
    plan = PLAN.read_text(encoding="utf-8")

    assert "privacy-and-consent.md" in plan, "docs/plan.md does not link the privacy document"
    privacy_section = plan.index("### Privacy")
    next_section = plan.index("\n## ", privacy_section)
    assert "privacy-and-consent.md" in plan[privacy_section:next_section], (
        "the link exists but not in plan.md's privacy section, which is where somebody looks"
    )


@pytest.mark.parametrize(
    "open_item",
    [
        "No lawyer has reviewed",
        "no liveness check",
        "resale",
        "Nothing verifies that an adult is an adult",
    ],
)
def test_the_document_still_records_what_has_not_been_done(open_item: str) -> None:
    """Criterion 4, as four separate assertions so a deletion names itself.

    These are the four the task lists by name. Deleting one would make the document
    read as fuller assurance than the app has earned, which is the specific harm the
    'say what was not done' rule exists to prevent.
    """
    assert open_item in _document(), f"the document no longer records: {open_item}"


def test_the_document_accounts_for_every_table_in_the_schema() -> None:
    """The claim that the other tables are course material must cover all of them.

    An earlier draft enumerated seven and the schema creates eight -- nothing false was
    said about the missing one, but "the other tables" reads as a complete accounting,
    and completeness is the whole point of a section somebody audits to find out which
    tables can hold personal data. Derived from schema.sql so the next table cannot be
    added without this failing.
    """
    import re

    schema = (Path(store.__file__).resolve().parent / "schema.sql").read_text(encoding="utf-8")
    tables = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", re.sub(r"--[^\n]*", " ", schema)))
    assert len(tables) >= 12, f"only {len(tables)} tables found, so this scan proves nothing"

    document = _document()
    missing = sorted(table for table in tables if f"`{table}`" not in document)

    assert missing == [], f"the document does not account for these tables: {missing}"


def test_the_document_states_what_reaches_a_log_at_each_level() -> None:
    """The task asks the document to say what the logs actually contain.

    The nuance is the whole value: the store and the tool layer are guarded, and
    transcript is a deliberate exception that is demoted rather than redacted. A
    document that said only "names never reach a log" would be wrong at DEBUG.
    """
    document = _document()

    assert "stderr" in document, "the document does not say where logs go"
    assert "DEBUG" in document and "INFO" in document, "the document does not distinguish the two levels"
    # The demotion is real: the words are logged at debug, the shape at info.
    console = (Path(store.__file__).resolve().parents[1] / "console.py").read_text(encoding="utf-8")
    assert "logger.debug(" in console and "content if len(content) < 500" in console, (
        "the transcript demotion this document describes is no longer in console.py"
    )


def test_the_document_lists_exactly_the_methods_reachable_over_the_network() -> None:
    """The /rpc table, derived from the allow-list rather than transcribed beside it.

    The first version of that table was written from docs/rpc-control-surface.md
    instead of from console.py: it listed backend.config, which the code refuses over
    the network, and omitted eight methods it exposes. Both directions were wrong in a
    table the document introduces as the authoritative enumeration, so the table is
    pinned to the frozenset in both directions here.
    """
    from reachy_language_tutor.console import _RPC_METHODS_EXPOSED_ON_THE_NETWORK

    exposed = set(_RPC_METHODS_EXPOSED_ON_THE_NETWORK)
    assert len(exposed) >= 10, f"the allow-list is unexpectedly small ({len(exposed)}), so this proves nothing"

    document = _document()
    missing = sorted(name for name in exposed if f"`{name}`" not in document)
    assert missing == [], f"the document omits methods a LAN caller can invoke: {missing}"

    # And the other direction: a method named as reachable that the code refuses.
    import re

    from reachy_language_tutor import console

    claimed = {
        name
        for name in re.findall(r"`([a-z_]+\.[a-z_]+)`", document)
        if name.split(".")[0] in {"conversation", "personalities", "voices", "tool_spaces", "profile_tools", "backend"}
    }
    registered = {
        name for name in claimed if hasattr(console, "_RPC_METHODS_EXPOSED_ON_THE_NETWORK")
    }  # every name the document mentions in method form
    over_claimed = sorted(name for name in registered - exposed if f"| `{name}`" in document)

    assert over_claimed == [], (
        f"the document's table rows name methods the code refuses over the network: {over_claimed}"
    )


def test_the_document_records_that_the_transcript_is_broadcast() -> None:
    """The exposure a review proved live, which the first version denied.

    /rpc broadcasts conversation.transcript to every attached websocket, so a peer that
    calls no method still receives the learner's speech verbatim. The document said "no
    learner data crosses this surface at all". This pins the correction in place, and
    pins that the broadcast it describes is still what the code does.
    """
    document = _document()

    assert "broadcast" in document.lower(), "the document no longer records the transcript broadcast"
    assert "conversation.transcript" in document

    console = (Path(store.__file__).resolve().parents[1] / "console.py").read_text(encoding="utf-8")
    assert "conversation.transcript" in console, (
        "console.py no longer broadcasts conversation.transcript -- if the exposure is gone, "
        "update docs/privacy-and-consent.md and delete this test"
    )
