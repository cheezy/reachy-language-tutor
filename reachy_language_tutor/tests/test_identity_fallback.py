"""Serving somebody when recognition cannot say who, without letting talk decide.

THE TRAP THIS FEATURE IS BUILT AROUND. The natural implementation -- the robot asks
"who is practising?", the person answers, the model passes the name to a tool -- is
the exact breach the rest of this codebase exists to prevent. A spoken name is not
authentication and no confirmation step makes it one, so the selection happens
somewhere the conversation has no route to: an instance-local settings file, written
by an operator at the device, read once at startup.

WHAT THIS FILE PROVES, as opposed to what the docstrings assert:

  * the fallback serves the configured learner when recognition answers nobody, and
    only then -- a recognised learner is never overridden;
  * unset, the answer is still nobody, so nothing is invented;
  * the disposition says which path set the identity, so a setting is never mistaken
    for a recognition;
  * a fallback naming somebody who has since been forgotten serves nobody;
  * and no tool gained an identity-shaped parameter, with the conversation's whole
    import closure checked for a route to either the setting or this module.

The last one is the only security property the fallback actually has, which is why it
is checked structurally rather than described.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

import pytest

from reachy_language_tutor import main, current_learner
from reachy_language_tutor.learners import store
from reachy_language_tutor.current_learner import RECOGNITION_DISPOSITIONS, RecognitionOutcome
from reachy_language_tutor.startup_settings import read_startup_settings, set_fallback_learner


SOMEBODY = "Perpetua Wainscot"


@pytest.fixture()
def instance(tmp_path: Path) -> Path:
    """A prepared learner database in its own instance directory."""
    assert store.ensure_learner_database(tmp_path).ready is True
    return tmp_path


def _a_learner(instance_path: Path, name: str = SOMEBODY) -> str:
    """A household member with a profile and no faceprint.

    No faceprint deliberately: this is the household that declined face recognition,
    which is the one the fallback exists for.
    """
    agreed = store.record_consent(
        name,
        scope="local_profile",
        statement_id="test.v1",
        statement_text="Wording used by the tests.",
        granted_by="the_person_themselves",
        granted_via="operator_at_the_robot",
        instance_path=instance_path,
    )
    assert agreed.recorded is True and agreed.learner_id is not None
    return agreed.learner_id


# ------------------------------------------------ the fallback does its job


def test_with_no_fallback_configured_the_answer_is_still_nobody(instance: Path) -> None:
    """The default, and it is the safe one: nothing configured invents nobody."""
    outcome = current_learner.recognise_current_learner(media=None, camera_enabled=False, instance_path=instance)

    assert outcome.learner_id is None
    assert outcome.disposition == "camera_disabled", (
        "the recognition answer must pass through untouched when no fallback is set"
    )


def test_a_configured_fallback_is_served_when_recognition_answers_nobody(instance: Path) -> None:
    """Acceptance criterion 1: a defined behaviour instead of an inert surface."""
    learner_id = _a_learner(instance)
    set_fallback_learner(instance, learner_id)

    outcome = current_learner.recognise_current_learner(media=None, camera_enabled=False, instance_path=instance)

    assert outcome.learner_id == learner_id
    assert outcome.disposition == "configured_fallback"


def test_the_disposition_says_the_identity_came_from_a_setting(instance: Path) -> None:
    """Acceptance criterion 6, and it is a machine code rather than a log sentence.

    An operator reading a log must never mistake a configured fallback for a
    recognition, and anything switching on the outcome must be able to tell too --
    so the distinction lives in the disposition, not only in prose.
    """
    learner_id = _a_learner(instance)
    set_fallback_learner(instance, learner_id)

    outcome = current_learner.recognise_current_learner(media=None, camera_enabled=False, instance_path=instance)

    assert outcome.disposition in RECOGNITION_DISPOSITIONS
    assert outcome.disposition != "identified", "a setting must not present itself as a recognition"
    # And it is loud: the same level the development override gets, for the same reason.
    assert main._DISPOSITION_LEVEL["configured_fallback"] == logging.WARNING


def test_the_operator_is_warned_and_the_learner_id_never_reaches_the_log(
    instance: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Visible, and still no personal data in the log line."""
    learner_id = _a_learner(instance)
    set_fallback_learner(instance, learner_id)

    with caplog.at_level(logging.DEBUG):
        current_learner.recognise_current_learner(media=None, camera_enabled=False, instance_path=instance)

    assert caplog.records, "nothing was captured, so the assertions below prove nothing"
    surface = " ".join(f"{record.getMessage()} {record.msg} {record.args}" for record in caplog.records)
    assert "serving the learner configured" in surface, "the fallback was silent"
    assert learner_id not in surface
    assert SOMEBODY not in surface


def test_a_recognised_learner_is_never_replaced_by_the_fallback(instance: Path, monkeypatch) -> None:
    """The fallback is a fallback. It must not outrank an answer recognition gave.

    Patched at _recognise rather than faked further down, because what is being
    pinned here is the ORDER in recognise_current_learner: a configured fallback and
    a real identification at the same time must resolve to the identification.
    """
    recognised = _a_learner(instance, "Zebediah Quixotic")
    other = _a_learner(instance)
    set_fallback_learner(instance, other)
    monkeypatch.setattr(
        current_learner, "_recognise", lambda **_: (RecognitionOutcome(recognised, "identified"), None)
    )

    outcome = current_learner.recognise_current_learner(media=None, camera_enabled=False, instance_path=instance)

    assert outcome.learner_id == recognised
    assert outcome.disposition == "identified"


# Every "nobody" answer recognition can give, split by whether the fallback may follow it.
# Spelled out HERE rather than derived from _FALLBACK_MAY_APPLY, so a disposition moved
# between the two sets in current_learner.py fails a test until somebody decides it again,
# and a new disposition upstream fails the totality check below.
_FALLBACK_SERVED_ON = frozenset(
    {
        "camera_disabled",
        "no_camera",
        "no_frame",
        "frame_unreadable",
        "recognition_unavailable",
        "nobody_enrolled",
        "no_face",
        "not_confident",
    }
)
_FALLBACK_REFUSED_ON = frozenset(
    {
        "several_faces",
        "too_close_to_call",
        "no_one_close_enough",
        "not_recognised",
        "store_unreadable",
        "declined_uncalibrated",  # served only when the withheld match IS the fallback
    }
)


def test_every_nobody_answer_is_decided_one_way_or_the_other() -> None:
    """Totality: a disposition added upstream must be classified before this passes."""
    nobody = set(RECOGNITION_DISPOSITIONS) - {"identified", "override", "configured_fallback"}

    assert _FALLBACK_SERVED_ON | _FALLBACK_REFUSED_ON == nobody
    assert not _FALLBACK_SERVED_ON & _FALLBACK_REFUSED_ON
    assert current_learner._FALLBACK_MAY_APPLY == _FALLBACK_SERVED_ON


def test_the_fallback_is_served_when_recognition_learned_nothing(instance: Path, monkeypatch) -> None:
    """No image, no usable face, nobody enrolled: the household this setting exists for."""
    learner_id = _a_learner(instance)
    set_fallback_learner(instance, learner_id)

    for disposition in sorted(_FALLBACK_SERVED_ON):
        monkeypatch.setattr(
            current_learner, "_recognise", lambda _d=disposition, **_: (RecognitionOutcome(None, _d), None)
        )
        outcome = current_learner.recognise_current_learner(media=None, camera_enabled=False, instance_path=instance)
        assert outcome.learner_id == learner_id, f"{disposition} did not reach the fallback"
        assert outcome.disposition == "configured_fallback"


def test_the_fallback_is_refused_on_evidence_of_somebody_else(instance: Path, monkeypatch) -> None:
    """Two faces, a face tied to nobody or to two people, a faulted comparison: serve nobody.

    Measured before the allow-list: every one of these resolved to the configured learner,
    so a child the camera had just failed to tell apart from a sibling was served the
    sibling's records.
    """
    learner_id = _a_learner(instance)
    set_fallback_learner(instance, learner_id)

    for disposition in sorted(_FALLBACK_REFUSED_ON):
        monkeypatch.setattr(
            current_learner, "_recognise", lambda _d=disposition, **_: (RecognitionOutcome(None, _d), None)
        )
        outcome = current_learner.recognise_current_learner(media=None, camera_enabled=False, instance_path=instance)
        assert outcome.learner_id is None, f"{disposition} served the fallback"
        assert outcome.disposition == disposition, "a refused fallback must pass recognition's answer on untouched"


def _stub_pipeline(monkeypatch, *, enrolled: tuple[str, ...], match) -> None:
    """Stub the pipeline at current_learner's own imports, so the REAL _recognise runs."""
    from types import SimpleNamespace

    monkeypatch.setattr(
        current_learner, "capture_frame", lambda media, camera_enabled: SimpleNamespace(usable=True, frame=object())
    )
    monkeypatch.setattr(
        current_learner, "describe_face", lambda frame: SimpleNamespace(usable=True, vector=(0.1, 0.2, 0.3))
    )
    monkeypatch.setattr(
        current_learner,
        "get_enrolled_faceprints",
        lambda instance_path: tuple(
            SimpleNamespace(
                learner_id=who, embedding_model=current_learner.EMBEDDING_MODEL_ID, dimension=3, vector=(0.1, 0.2, 0.3)
            )
            for who in enrolled
        ),
    )
    monkeypatch.setattr(current_learner, "match_faceprint", lambda vector, household, embedding_model: match)
    monkeypatch.setattr(current_learner, "THRESHOLD_CALIBRATED", False)


def test_a_withheld_match_to_somebody_else_refuses_the_fallback(instance: Path, monkeypatch) -> None:
    """The measured case: recognition matched B, withheld it, and the app served A."""
    from reachy_language_tutor.faces.matching import MatchOutcome

    configured = _a_learner(instance)
    somebody_else = _a_learner(instance, "Zebediah Quixotic")
    set_fallback_learner(instance, configured)
    _stub_pipeline(
        monkeypatch, enrolled=(configured, somebody_else), match=MatchOutcome(matched=True, learner_id=somebody_else)
    )

    outcome = current_learner.recognise_current_learner(media=object(), camera_enabled=True, instance_path=instance)
    served = main.resolve_current_learner_id(
        instance, logging.getLogger(__name__), media=object(), camera_enabled=True
    )

    assert outcome.learner_id is None
    assert outcome.disposition == "declined_uncalibrated"
    assert served is None


def test_a_withheld_match_to_the_fallback_learner_is_served(instance: Path, monkeypatch) -> None:
    """Both sources agree, so the setting is served -- and still labelled a setting."""
    from reachy_language_tutor.faces.matching import MatchOutcome

    configured = _a_learner(instance)
    somebody_else = _a_learner(instance, "Zebediah Quixotic")
    set_fallback_learner(instance, configured)
    _stub_pipeline(
        monkeypatch, enrolled=(configured, somebody_else), match=MatchOutcome(matched=True, learner_id=configured)
    )

    outcome = current_learner.recognise_current_learner(media=object(), camera_enabled=True, instance_path=instance)

    assert outcome.learner_id == configured
    assert outcome.disposition == "configured_fallback"


def test_the_withheld_match_reaches_no_log_and_no_repr(
    instance: Path, monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The withheld id is compared and dropped; it is never on the outcome or in a log."""
    from reachy_language_tutor.faces.matching import MatchOutcome

    configured = _a_learner(instance)
    somebody_else = _a_learner(instance, "Zebediah Quixotic")
    set_fallback_learner(instance, configured)
    _stub_pipeline(
        monkeypatch, enrolled=(configured, somebody_else), match=MatchOutcome(matched=True, learner_id=somebody_else)
    )

    with caplog.at_level(logging.DEBUG):
        outcome = current_learner.recognise_current_learner(
            media=object(), camera_enabled=True, instance_path=instance
        )

    assert caplog.records, "nothing was captured, so the assertions below prove nothing"
    surface = " ".join(f"{record.getMessage()} {record.msg} {record.args}" for record in caplog.records)
    assert "fallback was not served" in surface, "the refusal was silent"
    for identifier in (somebody_else, configured):
        assert identifier not in surface
        assert identifier not in repr(outcome)
        assert identifier not in str(vars(outcome))


def test_recognition_raising_still_reaches_the_fallback(instance: Path, monkeypatch) -> None:
    """The exception path is a way of answering nobody too, and it is easy to miss."""
    learner_id = _a_learner(instance)
    set_fallback_learner(instance, learner_id)

    def explode(**_: object):
        raise RuntimeError("the camera stack fell over")

    monkeypatch.setattr(current_learner, "_recognise", explode)

    outcome = current_learner.recognise_current_learner(media=None, camera_enabled=False, instance_path=instance)

    assert outcome.learner_id == learner_id
    assert outcome.disposition == "configured_fallback"


# ------------------------------------------------------------ it degrades well


def test_a_fallback_naming_a_forgotten_learner_serves_nobody(instance: Path) -> None:
    """The edge case the task names, and it is answered by the resolver's own check.

    A person can be forgotten long after the setting is written, so the id is never
    validated at write time -- a check there would prove nothing here. The resolver
    checks it against the database on every startup, which is the same check that
    validates the development override.
    """
    learner_id = _a_learner(instance)
    set_fallback_learner(instance, learner_id)
    assert store.forget_learner_entirely(learner_id, instance_path=instance).erased is True

    served = main.resolve_current_learner_id(instance, logging.getLogger(__name__), camera_enabled=False)

    assert served is None, "a fallback pointing at somebody who was forgotten must serve nobody"
    # The setting is left in place on purpose: the resolver refusing it is the
    # control, and silently rewriting an operator's configuration at startup would
    # be a worse surprise than an ignored setting.
    assert read_startup_settings(instance).fallback_learner == learner_id


def test_an_unreadable_settings_file_serves_nobody_rather_than_raising(instance: Path) -> None:
    """A robot in somebody's home must not be bricked by a corrupt settings file."""
    (instance / "startup_settings.json").write_text("{ this is not json", encoding="utf-8")

    outcome = current_learner.recognise_current_learner(media=None, camera_enabled=False, instance_path=instance)

    assert outcome.learner_id is None


def test_configuring_the_fallback_keeps_the_operators_other_settings(instance: Path) -> None:
    """write_startup_settings replaces the whole file, so this could silently clear them."""
    from reachy_language_tutor.startup_settings import write_startup_settings

    write_startup_settings(instance, profile="a-profile", voice="a-voice")
    learner_id = _a_learner(instance)

    set_fallback_learner(instance, learner_id)

    settings = read_startup_settings(instance)
    assert settings.profile == "a-profile"
    assert settings.voice == "a-voice"
    assert settings.fallback_learner == learner_id


def test_clearing_the_fallback_returns_the_app_to_serving_nobody(instance: Path) -> None:
    """Withdrawable, like everything else here."""
    learner_id = _a_learner(instance)
    set_fallback_learner(instance, learner_id)

    set_fallback_learner(instance, None)

    assert read_startup_settings(instance).fallback_learner is None
    outcome = current_learner.recognise_current_learner(media=None, camera_enabled=False, instance_path=instance)
    assert outcome.learner_id is None


# --------------------------------------- the conversation cannot reach any of it


def test_no_tool_gained_an_identity_shaped_parameter() -> None:
    """Acceptance criterion 2, checked against the existing allow-list.

    Imported from the locked-profile suite rather than restated, so this cannot
    drift from the control it is claiming to rely on.
    """
    from test_locked_profile import PERMITTED_TOOL_PARAMETERS

    # What makes a parameter identity-shaped is that it could name a PERSON. An
    # earlier version of this test used `endswith("_id")` and flagged `tool_id`,
    # which names a tool -- a heuristic wide enough to fail on a parameter that was
    # never the hazard teaches the next reader to loosen it, which is worse than
    # having none. So: words that could carry a person.
    person_shaped = ("learner", "person", "who", "display_name", "user", "child", "member")
    identity_shaped = {
        name for name in PERMITTED_TOOL_PARAMETERS if any(word in name.lower() for word in person_shaped)
    }

    assert identity_shaped == set(), f"a tool parameter can now carry an identity: {sorted(identity_shaped)}"
    # The allow-list is the control, so its SIZE is pinned too: this test would pass
    # just as happily against a vocabulary somebody had quietly doubled.
    assert len(PERMITTED_TOOL_PARAMETERS) == 14, (
        f"the permitted parameter vocabulary changed size: {sorted(PERMITTED_TOOL_PARAMETERS)}"
    )


def _every_module_named(tree: ast.AST) -> set[str]:
    """Every module a file's imports could bind, in every spelling.

    NOT a set of node shapes to look for -- that is the deny-list this started as,
    and a review measured what it missed: matching ImportFrom.module and Import
    alias names caught `from pkg.mod import thing` and `import pkg.mod`, while
    `from pkg import mod`, the same with an alias, and the identity module by that
    spelling all walked straight through. Three of five, including the most ordinary
    one, in the guard behind this task's second acceptance criterion.

    So every import is expanded into the full set of dotted module paths it could
    name, and the caller intersects. A spelling nobody thought of resolves to the
    same dotted name as the ones they did.
    """
    named: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                # `import a.b.c` binds a, and makes a.b and a.b.c reachable.
                named |= {".".join(parts[: i + 1]) for i in range(len(parts))}
        elif isinstance(node, ast.ImportFrom) and node.module:
            named.add(node.module)
            # `from a.b import c` may be importing the MODULE a.b.c, and the source
            # cannot tell that from importing a name out of a.b. Both are counted.
            named |= {f"{node.module}.{alias.name}" for alias in node.names}
    return named


def test_no_tool_can_reach_the_fallback_setting_or_the_identity_module() -> None:
    """The one security property the fallback has, checked rather than asserted.

    The fallback is safe because the conversation has no route to the value -- not
    because a name is confirmed, and not because a docstring says so. This walks the
    same tool import closure the enrolment suite uses and refuses a route to either
    the module that reads the setting or the module that decides identity.
    """
    from test_face_enrollment import _tool_import_closure

    closure = _tool_import_closure()
    assert closure, "the tool closure is empty, so this proves nothing"

    forbidden = {"reachy_language_tutor.startup_settings", "reachy_language_tutor.current_learner"}
    reached: dict[str, set[str]] = {}
    for module, tree in closure.items():
        names = _every_module_named(tree) & forbidden
        if names:
            reached[module] = names

    assert reached == {}, f"tool code can reach the identity surface: {reached}"


def test_the_settings_writer_is_not_reachable_from_the_conversation() -> None:
    """Reading would be bad enough; writing would let the conversation choose."""
    from test_face_enrollment import _tool_import_closure

    writers = {"set_fallback_learner", "write_startup_settings"}
    offenders: dict[str, set[str]] = {}
    for module, tree in _tool_import_closure().items():
        used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} & writers
        used |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)} & writers
        if used:
            offenders[module] = used

    assert offenders == {}, f"tool code names a settings writer: {offenders}"


def test_the_locked_profile_still_forbids_asking_who_somebody_is() -> None:
    """Security consideration 5: the fallback must not require the tutor to ask.

    The profile already tells the tutor it is TOLD who it is talking to. A fallback
    that needed the robot to ask would have contradicted the one instruction that
    keeps a spoken name out of the identity path, so this pins that the wording is
    still there after the change.
    """
    profile = Path(__file__).resolve().parents[1] / "profiles" / "_reachy_language_tutor_locked" / "profile.md"
    words = profile.read_text(encoding="utf-8").lower()

    assert "never ask" in words or "do not ask" in words, (
        "the locked profile no longer tells the tutor not to ask who somebody is"
    )


# ------------------------------- the household that declined face recognition


def test_a_person_can_be_registered_with_no_face_data_at_all(instance: Path) -> None:
    """Acceptance criterion 5, and before this task it was not possible.

    The only way to create a learner is record_consent, and the only thing to consent
    to was face recognition -- so declining meant having no profile, which is the
    opposite of what an opt-in is for.
    """
    learner_id = _a_learner(instance)

    assert store.get_profile(learner_id, instance_path=instance) is not None
    assert store.get_faceprint(learner_id, instance_path=instance) is None
    agreements = store.get_consents(learner_id, instance_path=instance)
    assert [record.scope for record in agreements] == ["local_profile"]


def test_a_local_profile_yes_is_not_a_face_recognition_yes(instance: Path) -> None:
    """The two scopes are not interchangeable, and the DATABASE is what enforces it.

    Not a check in Python that could be forgotten at a second call site: the faceprint
    insert selects its rows from consents filtered to face_recognition, so somebody
    who agreed only to a learning record cannot be given a faceprint at all.
    """
    learner_id = _a_learner(instance)

    stored = store.save_faceprint(
        learner_id, "opencv_sface_2021dec_fp32", tuple(0.5 for _ in range(128)), instance_path=instance
    )

    assert stored.saved is False
    assert stored.reason == "no_consent"
    assert store.get_faceprint(learner_id, instance_path=instance) is None


def test_a_household_with_no_recognition_can_complete_a_lesson(instance: Path) -> None:
    """The integration case the testing_strategy names, end to end.

    Registered without a face, served by the fallback, taught, and their result
    recorded against the identity the fallback chose -- which is the whole feature
    working rather than each half of it passing separately.
    """
    learner_id = _a_learner(instance)
    set_fallback_learner(instance, learner_id)

    served = main.resolve_current_learner_id(instance, logging.getLogger(__name__), camera_enabled=False)
    assert served == learner_id, "the household could not be served at all"

    progress = store.get_progress(served, "it", instance_path=instance)
    assert progress is not None and progress.next_lesson is not None
    lesson = progress.next_lesson

    recorded = store.record_result(served, lesson.id, "completed", instance_path=instance)
    assert recorded.recorded is True, recorded.reason

    after = store.get_progress(served, "it", instance_path=instance)
    assert lesson.id in [done.id for done in after.completed], "the lesson was not recorded as done"
    assert len(after.attempts) == 1


# ------------------------------------------------ the migration carries consent


def test_an_existing_database_upgrades_without_losing_what_anybody_agreed_to(tmp_path: Path) -> None:
    """The rebuild touches consent records, so what it preserves is measured.

    Re-running schema.sql cannot change a CHECK on a table that already exists, so a
    robot that already has a database needs the rebuild -- and a rebuild that lost a
    row would destroy the evidence of what somebody was promised. The v4 shape is
    reconstructed here rather than mocked, because what is being tested is the
    upgrade an installed robot actually takes.
    """
    import sqlite3

    assert store.ensure_learner_database(tmp_path).ready is True
    learner_id = _a_learner(tmp_path, "Zebediah Quixotic")
    before = store.get_consents(learner_id, instance_path=tmp_path)
    assert before is not None and len(before) == 1

    # Put the database back to the v4 shape: the old CHECK, and the old version.
    database = store.learner_db_path_for_instance(tmp_path)
    connection = sqlite3.connect(database)
    try:
        connection.executescript(
            """
            PRAGMA foreign_keys = OFF;
            CREATE TABLE consents_v4 (
              id             INTEGER PRIMARY KEY,
              learner_id     TEXT    NOT NULL REFERENCES learners(id) ON DELETE CASCADE,
              scope          TEXT    NOT NULL CHECK (scope IN ('face_recognition')),
              statement_id   TEXT    NOT NULL,
              statement_text TEXT    NOT NULL,
              granted_by     TEXT    NOT NULL,
              granted_via    TEXT    NOT NULL,
              granted_at     INTEGER NOT NULL,
              withdrawn_at   INTEGER
            ) STRICT;
            INSERT INTO consents_v4 SELECT id, learner_id, 'face_recognition', statement_id,
                   statement_text, granted_by, granted_via, granted_at, withdrawn_at FROM consents;
            DROP TABLE consents;
            ALTER TABLE consents_v4 RENAME TO consents;
            PRAGMA user_version = 4;
            """
        )
        connection.commit()
    finally:
        connection.close()

    # The old shape really does refuse the new scope, or the upgrade below proves nothing.
    refused = store.record_consent(
        "Perpetua Wainscot",
        scope="local_profile",
        statement_id="test.v1",
        statement_text="Wording used by the tests.",
        granted_by="the_person_themselves",
        granted_via="operator_at_the_robot",
        instance_path=tmp_path,
    )
    assert refused.recorded is False and refused.reason == "rejected_by_database"

    assert store.ensure_learner_database(tmp_path).ready is True

    # The upgrade carried the row, column for column.
    after = store.get_consents(learner_id, instance_path=tmp_path)
    assert after is not None and len(after) == 1
    assert after[0].statement_text == before[0].statement_text
    assert after[0].granted_at == before[0].granted_at
    assert after[0].granted_by == before[0].granted_by
    # And the new scope is now accepted.
    assert _a_learner(tmp_path, "Perpetua Wainscot")


def test_the_local_profile_wording_and_its_id_cannot_drift_apart() -> None:
    """The face notice's protection, extended to the scope this task added.

    faces/enrollment.py pins its wording by digest because under v2 the words changed
    twice and only a review noticed. This notice is stored as statement_text on
    exactly the same terms -- it is the evidence of what a person was promised -- and
    it shipped with no pin until a review asked why the sibling protection had not
    moved with the scope.
    """
    import hashlib

    from reachy_language_tutor.learners import (
        LOCAL_PROFILE_STATEMENT,
        LOCAL_PROFILE_STATEMENT_ID,
        LOCAL_PROFILE_STATEMENT_DIGESTS,
    )

    digest = hashlib.sha256(LOCAL_PROFILE_STATEMENT.encode("utf-8")).hexdigest()

    assert LOCAL_PROFILE_STATEMENT_ID in LOCAL_PROFILE_STATEMENT_DIGESTS, (
        f"{LOCAL_PROFILE_STATEMENT_ID} has no recorded digest; add one"
    )
    assert LOCAL_PROFILE_STATEMENT_DIGESTS[LOCAL_PROFILE_STATEMENT_ID] == digest, (
        "the local-profile consent wording changed. If no household has agreed under "
        f"{LOCAL_PROFILE_STATEMENT_ID} yet, update its digest to {digest}. If one has, add a "
        "NEW id and digest instead -- a stored statement_text is the evidence of what somebody "
        "was told, and an id that names two different texts cannot answer who agreed to what."
    )


def test_the_local_profile_notice_promises_only_what_this_scope_does() -> None:
    """A notice must not describe the thing the person is declining.

    The whole reason this scope has its own wording is that showing somebody the
    face-recognition notice and storing their yes under another scope would make the
    stored evidence false. So the notice must not promise anything about faces, and
    must say plainly that the robot will not recognise them.
    """
    from reachy_language_tutor.learners import LOCAL_PROFILE_STATEMENT

    words = LOCAL_PROFILE_STATEMENT.lower()

    assert "will not recognise you" in words, "the notice does not say the robot will not recognise them"
    assert "does not" in words and "camera" in words, "the notice does not address the camera"
    # And it keeps the promise this app makes everywhere else about erasure.
    assert "delete all of it" in words


def test_saving_a_voice_or_a_personality_does_not_turn_the_fallback_off(
    instance: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The defect this file's author introduced, caught by asking who else writes the file.

    write_startup_settings replaces the whole file, and fallback_learner has a
    default -- so a UI caller that omits it clears the identity an operator
    configured, silently, and the robot serves nobody afterwards with no message
    anywhere. Both UI writers are exercised here through the console's own methods
    rather than through write_startup_settings directly, because it is the CALLERS
    that were wrong and a test of the writer would have passed throughout.
    """
    from unittest.mock import MagicMock

    from reachy_language_tutor.console import LocalStream

    learner_id = _a_learner(instance)
    set_fallback_learner(instance, learner_id)

    stream = MagicMock(spec=LocalStream)
    stream._instance_path = instance

    LocalStream._persist_voice_override(stream, "some-voice")
    assert read_startup_settings(instance).fallback_learner == learner_id, (
        "saving a voice cleared the configured fallback"
    )

    # LOCKED_PROFILE is set in this app, and _persist_personality returns before
    # writing when it is -- so without this the call below is a no-op and the test
    # passes having exercised nothing. Its own "the voice was not actually saved"
    # guard is what caught that. Patched to None so the path an unlocked install
    # takes is the one measured.
    monkeypatch.setattr("reachy_language_tutor.console.LOCKED_PROFILE", None)
    monkeypatch.setattr("reachy_language_tutor.console.set_custom_profile", lambda _profile: None)

    LocalStream._persist_personality(stream, "a-profile", "another-voice")
    settings = read_startup_settings(instance)
    assert settings.fallback_learner == learner_id, "saving a personality cleared the configured fallback"
    assert settings.voice == "another-voice", "the voice was not actually saved, so this proves nothing"

    # And the app still serves them afterwards, which is the consequence that matters.
    served = main.resolve_current_learner_id(instance, logging.getLogger(__name__), camera_enabled=False)
    assert served == learner_id


def test_every_caller_of_the_settings_writer_passes_the_fallback() -> None:
    """The footgun that produced the defect above, closed structurally.

    write_startup_settings replaces the whole file and fallback_learner has a
    default, so a caller that omits the keyword silently clears the configured
    identity. That is not a hypothetical: both console writers did exactly that, and
    the robot stopped serving anybody with no message anywhere.

    The default stays -- a function that merged silently would hide which caller owns
    which field -- so the discipline is enforced here instead: every call site names
    the keyword. A test of write_startup_settings itself would have passed the whole
    time, because the function was never what was wrong.
    """
    import ast as _ast

    source_root = Path(store.__file__).resolve().parents[1]
    offenders: dict[str, list[int]] = {}
    for path in source_root.rglob("*.py"):
        tree = _ast.parse(path.read_text(encoding="utf-8"))
        for node in _ast.walk(tree):
            if not isinstance(node, _ast.Call):
                continue
            called = node.func.id if isinstance(node.func, _ast.Name) else getattr(node.func, "attr", None)
            if called != "write_startup_settings":
                continue
            if "fallback_learner" not in {keyword.arg for keyword in node.keywords}:
                offenders.setdefault(str(path.relative_to(source_root)), []).append(node.lineno)

    assert offenders == {}, (
        "a caller of write_startup_settings omits fallback_learner, which silently clears "
        f"the configured identity: {offenders}"
    )
    # The scan must actually have found call sites, or it proves nothing.
    call_sites = sum(
        1
        for path in source_root.rglob("*.py")
        for node in _ast.walk(_ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, _ast.Call)
        and (node.func.id if isinstance(node.func, _ast.Name) else getattr(node.func, "attr", None))
        == "write_startup_settings"
    )
    assert call_sites >= 3, f"only {call_sites} call sites found, so this scan proves nothing"
