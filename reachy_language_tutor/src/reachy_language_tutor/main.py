"""Entrypoint for the Reachy Mini conversation app."""

from __future__ import annotations
import sys
import time
import asyncio
import logging
import argparse
import threading
import traceback
from typing import TYPE_CHECKING, Any, Optional
from pathlib import Path
from datetime import datetime, timezone
from collections.abc import Callable, Awaitable

from fastapi import FastAPI, Request, Response

from reachy_mini import ReachyMini, ReachyMiniApp
from reachy_language_tutor import app_lifecycle
from reachy_language_tutor.utils import (
    parse_args,
    setup_logger,
    log_connection_troubleshooting,
)
from reachy_language_tutor.logging_safety import log_safe


if TYPE_CHECKING:
    from reachy_language_tutor.console import LocalStream
    from reachy_language_tutor.tools.core_tools import ToolDependencies


# MILESTONE 4 REPLACED THE LINE THAT WAS HERE. It bound
# HARDCODED_CURRENT_LEARNER_ID to the seeded learner's id, and the app served exactly
# that one person. Recognition chooses now, and a test asserts no learner id is
# hard-coded anywhere in this package at all -- which is why this comment describes
# the deleted line instead of quoting it. Quoting it put the literal back, and that
# test caught it immediately, which is the test working.
#
# How loudly to say each answer. Keyed by disposition, total over
# RECOGNITION_DISPOSITIONS and pinned as total by a test, so a disposition added
# upstream cannot arrive here as a silent KeyError-shaped default.
#
# store_unreadable is ERROR because it is a fault in the robot somebody has to fix.
# declined_uncalibrated is WARNING and deliberately loud: the robot recognised
# somebody and refused to act on it, which an operator watching a demo fail deserves
# to be told rather than left to infer. The ordinary "nobody was there" answers are
# INFO -- they are the expected state of a robot in an empty room.
_DISPOSITION_LEVEL: dict[str, int] = {
    "identified": logging.INFO,
    "override": logging.WARNING,
    "declined_uncalibrated": logging.WARNING,
    "store_unreadable": logging.ERROR,
    "camera_disabled": logging.INFO,
    "no_camera": logging.INFO,
    "no_frame": logging.INFO,
    "no_face": logging.INFO,
    "several_faces": logging.INFO,
    "not_confident": logging.INFO,
    "frame_unreadable": logging.INFO,
    "recognition_unavailable": logging.INFO,
    "nobody_enrolled": logging.INFO,
    "no_one_close_enough": logging.INFO,
    "too_close_to_call": logging.INFO,
    "not_recognised": logging.INFO,
}


def _start_inactivity_timeout_thread(
    timeout_minutes: float,
    stream_manager: LocalStream,
    logger: logging.Logger,
    app_stop_event: threading.Event | None,
    go_to_sleep: Callable[[], dict[str, Any]] | None = None,
) -> threading.Thread:
    """Start a daemon that puts the app to sleep after inactivity."""
    timeout_seconds = timeout_minutes * 60.0

    def poll_inactivity_timeout() -> None:
        logger.info("App inactivity timeout enabled: %.1f minutes.", timeout_minutes)
        while app_stop_event is None or not app_stop_event.is_set():
            elapsed = stream_manager.seconds_since_activity()
            if elapsed >= timeout_seconds:
                logger.info("No activity for %.1f minutes; going to sleep.", elapsed / 60.0)
                try:
                    if go_to_sleep is not None:
                        go_to_sleep()
                    else:
                        stream_manager.close()
                except Exception as e:
                    logger.error("Error while going to sleep after inactivity timeout: %s", log_safe(e))
                    try:
                        stream_manager.close()
                    except Exception as close_error:
                        logger.error(
                            "Error while closing stream manager after inactivity timeout: %s", log_safe(close_error)
                        )
                return
            time.sleep(1.0)

    thread = threading.Thread(target=poll_inactivity_timeout, daemon=True)
    thread.start()
    return thread


def resolve_current_learner_id(
    instance_path: str | Path | None,
    logger: logging.Logger,
    *,
    media: Any | None = None,
    camera_enabled: bool = False,
) -> str | None:
    """Decide which learner the app is serving, or None when that cannot be trusted.

    Serving the wrong person their housemate's data needs a wrong *identity*; None
    cannot cause it, because every reader refuses an unknown learner. So this never
    aborts startup -- a corrupt database in someone's home would brick the robot for
    no security gain. It is loud at the identity boundary and permissive at the
    process boundary: the learner surface goes dead and says why.

    WHEN RECOGNITION RUNS: once, here, in the calling thread, at the moment the app
    decides who it is serving. Not on a timer and not per frame -- one frame is
    pulled, compared and dropped. current_learner.py's docstring carries the full
    reasoning, including why once-per-process and continuous are both wrong and what
    that leaves unsolved.

    The identity recognition offers is still checked against the database before it
    is served. That check is near-unreachable for a recognised learner, whose
    faceprint has a foreign key to their row -- but it is exactly what validates the
    development override, which is the untrusted input on this path.
    """
    from reachy_language_tutor.learners import get_profile, store_is_available
    from reachy_language_tutor.current_learner import recognise_current_learner

    try:
        outcome = recognise_current_learner(media=media, camera_enabled=camera_enabled, instance_path=instance_path)
        if outcome.learner_id is None:
            # The disposition, never an id -- that is the whole point of the code.
            logger.log(
                _DISPOSITION_LEVEL.get(outcome.disposition, logging.INFO),
                "Serving nobody: %s",
                outcome.disposition,
            )
            return None
        if get_profile(outcome.learner_id, instance_path=instance_path) is not None:
            return outcome.learner_id
        # Only now pay for the second query, to say which of the two failures it was.
        if store_is_available(instance_path):
            logger.error("The configured learner is not in the learner database; serving nobody.")
        else:
            logger.error("The learner store is unreadable; serving nobody until it is repaired.")
    except Exception as e:  # never block startup on learner storage
        # The type and the frames, NEVER the instance. str(e) is attacker- and
        # author-controlled: the store absorbs sqlite3.Error, OSError and ValueError
        # today, so no learner id can reach here -- but that is a property of the
        # store's internals, not of this line, and one raise ValueError(f"... {id}")
        # added under get_profile would start rendering a recognized person's
        # identifier into an ERROR log with nothing failing. This is the same
        # redaction core_tools._dispatch_tool_call applies for the same reason.
        # A traceback's frames are file and line only, so they carry no values.
        frames = " <- ".join(
            f"{frame.filename.rsplit('/', 1)[-1]}:{frame.lineno}" for frame in traceback.extract_tb(e.__traceback__)
        )
        logger.error("Could not establish who the app is serving; serving nobody: %s at %s", type(e).__name__, frames)
    return None


def build_tool_dependencies(
    robot: ReachyMini,
    movement_manager: Any,
    instance_path: str | Path | None,
    camera_enabled: bool,
    logger: logging.Logger,
) -> ToolDependencies:
    """Build the dependency bundle the tools receive, including who the app is serving.

    Extracted from run() so the wiring can be tested without a robot: this is the one
    place the current learner is set, and a test that cannot reach it cannot prove it.
    """
    from reachy_language_tutor.lesson_session import LessonSessionHolder
    from reachy_language_tutor.tools.core_tools import ToolDependencies

    # Resolved once and used twice, deliberately: the holder is bound to the SAME
    # learner the dependencies are sealed to. Calling the resolver a second time would
    # leave two answers that could in principle differ, and a holder bound to somebody
    # other than the learner the app is serving is precisely the state this design
    # exists to make unrepresentable.
    # Resolved ONCE, and now that has teeth: the call reads the camera, so a second
    # call would be a second frame and a second chance to answer differently.
    current_learner_id = resolve_current_learner_id(
        instance_path,
        logger,
        media=getattr(robot, "media", None),
        camera_enabled=camera_enabled,
    )

    return ToolDependencies(
        reachy_mini=robot,
        movement_manager=movement_manager,
        instance_path=instance_path,
        camera_enabled=camera_enabled,
        current_learner_id=current_learner_id,
        # Named here rather than left to the field's default_factory, because this is
        # the one wiring site -- the same argument this function's docstring makes for
        # the current learner. The factory is the fail-closed invariant for every other
        # construction path (it binds to nobody, and a holder bound to nobody pins
        # nothing); this line is the one a reader looking for "where does the app decide
        # what is running, and for whom" will find. It starts empty: a robot that has
        # just booted is not in the middle of a lesson.
        lesson_session=LessonSessionHolder(current_learner_id),
    )


# The --ui development server binds here. Loopback, not 0.0.0.0: this server mounts the
# JSON-RPC control surface from console.py, whose methods make the robot speak and mute or
# UNMUTE its microphone, and it carries no authentication of any kind. On a Reachy Mini
# Wireless sitting on a family's Wi-Fi, binding every interface hands those to anyone on
# the network. README_OLD.md has always documented this as 127.0.0.1; the code did not
# agree, and this is the code agreeing.
#
# This is NOT the address the app binds when the robot daemon launches it. That one is
# derived by the SDK from ReachyLanguageTutor.custom_app_url below -- see the comment
# there before changing either.
UI_BIND_HOST = "127.0.0.1"


# The two spellings of the consent roles: hyphens on the command line because that is
# what a person types, underscores in the database because that is what the column
# holds. Mapped in one place so neither side has to know about the other's style.
_CONSENT_ROLE_ARGUMENTS = {
    "the-person-themselves": "the_person_themselves",
    "an-adult-of-the-household": "an_adult_of_the_household",
}


def handle_enrol_command(args: argparse.Namespace) -> int:
    """Enrol a household member, in person, after showing them what they are agreeing to.

    AN OPERATOR COMMAND, NOT A TOOL, and the distinction is the security boundary this
    app is built around: the model never supplies an identity, so it must not be able
    to create one either. Running this needs a shell on the machine. Nothing here is
    registered on the /rpc surface, which is LAN-reachable and cannot be authenticated.

    IT WRITES NO IMAGE. It borrows the media handle for the length of the capture,
    turns frames into numbers, and closes the handle on the way out.

    IT PRINTS A NAME, and that is a considered departure from
    scripts/calibrate_faceprints.py, which prints none. There, names are incidental to
    a threshold table; here the name IS the record being confirmed, and a consent
    read-back that cannot say who consented does not meet what the household is owed.
    The LOGGER still never receives a name -- print to an operator's terminal is not
    logging -- but an operator who pipes this into a shared file has put personal data
    there, so the help text says so.
    """
    from reachy_language_tutor.faces import enrol, consent_statement
    from reachy_language_tutor.learners import (
        get_profile,
        get_consents,
        forget_learner,
        delete_faceprint,
        store_is_available,
        forget_learner_entirely,
    )

    # Bootstrap and the path helper come from the storage module rather than the
    # package boundary, which deliberately publishes neither -- the same import run()
    # already makes below, for the same reason.
    from reachy_language_tutor.learners.store import ensure_learner_database, learner_db_path_for_instance

    instance_path = args.instance_path
    database = learner_db_path_for_instance(instance_path)
    # Printed before anything else, every time. An operator running this from a
    # terminal and an app launched by the daemon can easily be looking at two
    # different files, and the failure has no symptom until recognition never works
    # for the person who was enrolled into the wrong one.
    print(f"Learner database: {database}")
    if not database.exists():
        print("No learner database there yet. Start the app once first, or pass --instance-path.")
        return 1

    def _named_learner(learner_id: str):
        """Look a learner up, telling 'no such learner' apart from 'cannot read'.

        get_profile returns None for BOTH, and its own docstring says the two must
        not be described to a person the same way. Telling a household their child is
        not in the database, when the truth is that the database could not be opened,
        is the exact conflation delete_faceprint's None return exists to avoid -- and
        a pre-check that collapses them undoes that care one layer up. One helper
        rather than the same three lines in each branch, because this was already the
        same bug twice.
        """
        profile = get_profile(learner_id, instance_path=instance_path)
        if profile is not None:
            return profile
        if store_is_available(instance_path):
            print("No such learner.")
        else:
            print("The learner database could not be read, so nothing can be promised either way.")
        return None

    if getattr(args, "forget_everything_id", None) is not None:
        # THE SECOND ERASURE. --forget removes the numbers and keeps the person;
        # this removes the person. Both exist because they are different requests,
        # and which one happened is said out loud below rather than left to be
        # inferred from silence.
        profile = _named_learner(args.forget_everything_id)
        if profile is None:
            return 1
        # Read BEFORE the erasure, because afterwards there is deliberately nobody
        # to ask. Printed to the operator's terminal, which is not a log -- the
        # store's own log line carries no counts and no name at all.
        display_name = profile.display_name
        outcome = forget_learner_entirely(args.forget_everything_id, instance_path=instance_path)
        if outcome is None:
            print("The learner database could not be read, so nothing can be promised either way.")
            return 1
        if not outcome.erased:
            print("No such learner. Nothing was erased.")
            return 1
        print(f"Forgot {display_name} completely.")
        print(
            f"  removed: {outcome.learners} person, {outcome.faceprints} faceprint, "
            f"{outcome.consents} agreement, {outcome.results} lesson result(s)"
        )
        if outcome.pages_still_in_the_log:
            # Said plainly, because the promise is about the data and not the row.
            print("  NOTE: their pages are still in the database's write-ahead log.")
            print("  They leave the file at the next checkpoint no reader is holding open.")
        else:
            # Names both files, because both were measured. An earlier version of
            # this sentence said "the database file" while the write-ahead log still
            # held the vector and the name -- a promise wider than the measurement
            # behind it, which is the one thing this project forbids writing down.
            print("  Nothing of theirs is left in the database file or its write-ahead log.")
        return 0

    if getattr(args, "remove_learner_id", None) is not None:
        # Removes the learner, their consent and any faceprint -- the remedy for an
        # enrolment whose rollback failed, which --forget cannot clear because such a
        # learner has no faceprint to delete.
        profile = _named_learner(args.remove_learner_id)
        if profile is None:
            return 1
        removed = forget_learner(args.remove_learner_id, instance_path=instance_path)
        if removed is None:
            print("The learner database could not be read, so nothing can be promised either way.")
            return 1
        if removed == 0:
            print(f"{profile.display_name} was not removed. A learner with lesson history is kept deliberately.")
            return 1
        print(f"Removed {profile.display_name}, their agreement and any faceprint.")
        return 0

    if getattr(args, "forget_learner_id", None) is not None:
        # The promise in CONSENT_STATEMENT, kept. A security review found the sentence
        # "you can ask ... to delete all of it" had no invocable route behind it:
        # delete_faceprint was exported and never called from anywhere in the app, so
        # a household asking for their child's faceprint to be removed could only be
        # served by somebody opening the SQLite file by hand.
        profile = _named_learner(args.forget_learner_id)
        if profile is None:
            return 1
        removed = delete_faceprint(args.forget_learner_id, instance_path=instance_path)
        if removed is None:
            print("The learner database could not be read, so nothing can be promised either way.")
            return 1
        if removed == 0:
            print(f"{profile.display_name} had no faceprint. Nothing to delete.")
            return 0
        # Said plainly, because this is the sentence the household was promised. The
        # count is what delete_faceprint returns; it erases and checkpoints the WAL
        # before reporting, so the bytes are gone from the file and not merely
        # unreachable.
        print(f"Deleted the faceprint for {profile.display_name}.")
        print("  This robot can no longer recognise them. Their lesson history is untouched.")
        print("  Their record of having agreed is kept, so there is still an answer to what was agreed and when.")
        return 0

    if args.show_learner is not None:
        profile = _named_learner(args.show_learner)
        if profile is None:
            return 1
        consents = get_consents(args.show_learner, instance_path=instance_path)
        if consents is None:
            print("The learner database could not be read.")
            return 1
        print(f"\n{profile.display_name} has agreed to {len(consents)} thing(s).\n")
        for record in consents:
            when = datetime.fromtimestamp(record.granted_at / 1000, tz=timezone.utc).isoformat()
            standing = "withdrawn" if record.withdrawn_at is not None else "standing"
            print(f"  {record.scope} ({standing})")
            print(f"    agreed by : {record.granted_by}")
            print(f"    agreed via: {record.granted_via}")
            print(f"    agreed at : {when}")
            print(f"    wording   : {record.statement_id}")
            # The words as they stood on the day, not the constant as it reads today.
            for line in record.statement_text.splitlines():
                print(f"      | {line}")
            print()
        return 0

    if args.enrol_name is None:
        print("Nobody to enrol: pass --name.")
        return 1
    if args.consent_from is None:
        # No default, deliberately. This is the question about who may consent for a
        # child, and it is asked every time rather than inherited.
        print("Say who is giving permission: --consent-from the-person-themselves|an-adult-of-the-household")
        return 1
    granted_by = _CONSENT_ROLE_ARGUMENTS[args.consent_from]

    ensure_learner_database(instance_path)

    print()
    print(consent_statement())
    print()
    # A typed word, never a bare [y/N]. A default that means yes is not consent, and
    # the Cadillac Fairview finding docs/plan.md cites was about consent that was
    # technically obtained and not meaningful.
    answer = input('Type "yes" to agree, or anything else to stop: ').strip().lower()
    if answer != "yes":
        print("Nothing was recorded.")
        return 2

    robot = None
    try:
        robot = ReachyMini()
        media = robot.media
        outcome = enrol(
            args.enrol_name,
            media,
            granted_by=granted_by,
            camera_enabled=not args.no_camera,
            instance_path=instance_path,
        )
    except Exception as exc:
        # The shape only: this line can reach a log, and the exception from a media
        # backend can carry a device path.
        print(f"Could not reach the robot's camera ({type(exc).__name__}). Nothing was enrolled.")
        return 1
    finally:
        if robot is not None:
            # Mirrors run()'s shutdown finally. Closing media also closes the audio
            # device, which is why it is done here at the end of the command rather
            # than anywhere inside the faces package.
            try:
                robot.media.close()
            except Exception:
                pass
            try:
                robot.client.disconnect()
            except Exception:
                pass

    if not outcome.enrolled:
        print(f"Not enrolled: {outcome.error}")
        if outcome.learner_id is not None:
            # The rollback_failed sentence points at "the learner id printed below",
            # and this is that line. Without it the remedy named a value the operator
            # had never been shown, and there is no way to list learners -- so the
            # orphan would have been unreachable from the app surface entirely.
            print(f"  learner id: {outcome.learner_id}")
        return 1

    print(f"Enrolled {args.enrol_name}.")
    print(f"  learner id: {outcome.learner_id}")
    print("  Their faceprint is on this robot only. Delete it at any time on request.")
    return 0


def main() -> None:
    """Entrypoint for the Reachy Mini conversation app."""
    args, _ = parse_args()
    if args.command == "tool-spaces":
        from reachy_language_tutor.tool_spaces import handle_tool_spaces_command

        logger = setup_logger(args.debug)
        try:
            raise SystemExit(handle_tool_spaces_command(args))
        except Exception as exc:
            logger.error("tool-spaces command failed: %s", log_safe(exc))
            raise SystemExit(1) from exc
    if args.command == "enrol":
        logger = setup_logger(args.debug)
        try:
            raise SystemExit(handle_enrol_command(args))
        except SystemExit:
            raise
        except Exception as exc:
            # No name and no path in this line: the argument that failed is a person's
            # name, and log_safe is what keeps the exception from quoting it.
            logger.error("enrol command failed: %s", log_safe(exc))
            raise SystemExit(1) from exc
    run(args)


def run(
    args: argparse.Namespace,
    robot: ReachyMini = None,
    app_stop_event: Optional[threading.Event] = None,
    settings_app: Optional[FastAPI] = None,
    instance_path: Optional[str] = None,
) -> None:
    """Run the Reachy Mini conversation app."""
    # Putting these dependencies here makes the dashboard faster to load when the conversation app is installed
    from reachy_language_tutor.moves import MovementManager
    from reachy_language_tutor.config import (
        HF_LOCAL_CONNECTION_MODE,
        set_instance_path,
        get_hf_connection_selection,
        resolve_app_timeout_minutes,
        refresh_runtime_config_from_env,
    )
    from reachy_language_tutor.startup_settings import (
        StartupSettings,
        load_startup_settings_into_runtime,
    )

    logger = setup_logger(args.debug)
    logger.info("Starting Reachy Mini Conversation App")
    set_instance_path(instance_path)
    startup_settings = StartupSettings()

    if instance_path is not None:
        try:
            from dotenv import load_dotenv

            env_path = Path(instance_path) / ".env"
            if env_path.exists():
                load_dotenv(dotenv_path=str(env_path), override=True)
                refresh_runtime_config_from_env()
                logger.info("Loaded instance configuration from the instance .env")
        except Exception as e:
            logger.warning("Failed to load instance configuration: %s", log_safe(e))

        try:
            startup_settings = load_startup_settings_into_runtime(instance_path)
        except Exception as e:
            logger.warning("Failed to load startup settings: %s", log_safe(e))

    try:
        from reachy_language_tutor.learners.store import ensure_learner_database

        ensure_learner_database(instance_path)
    except Exception as e:  # never block startup on learner storage
        logger.warning("Failed to prepare the learner database: %s", log_safe(e))

    try:
        from reachy_language_tutor.tools.play_emotion import warm_emotion_library

        # Paid once, here. The lesson reactions ask for this library and never build it,
        # because building it downloads a dataset and they run inside the voice loop --
        # so this is the call that stops a session's first reactions being dropped.
        warm_emotion_library()
    except Exception as e:  # never block startup on movement
        logger.warning("Failed to warm the emotion library: %s", log_safe(e))

    try:
        from reachy_language_tutor.faces import warm_face_models

        # Paid once, here, for the same reason as the emotion library: recognition
        # runs while the tool dependencies are built, and an unwarmed model would
        # make it answer recognition_unavailable for the whole session.
        #
        # Deliberately NOT gated on THRESHOLD_CALIBRATED. Gating would save a
        # download whose result is discarded while the flag is False -- and it would
        # mean the recognition pipeline never actually runs, so the flag would change
        # two things instead of one and there would be nothing to withhold.
        warm_face_models()
    except Exception as e:  # never block startup on recognition
        logger.warning("Failed to warm the face models: %s", log_safe(e))

    logger.info(
        "Configured Hugging Face realtime backend, connection mode: %s",
        get_hf_connection_selection().mode,
    )

    from reachy_language_tutor.console import LocalStream
    from reachy_language_tutor.conversation_handler import ConversationHandler

    if robot is None:
        try:
            robot_kwargs = {}
            if args.robot_name is not None:
                robot_kwargs["robot_name"] = args.robot_name

            logger.info("Initializing ReachyMini (SDK will auto-detect appropriate backend)")
            robot = ReachyMini(**robot_kwargs)

        except TimeoutError as e:
            logger.error("Connection timeout: Failed to connect to Reachy Mini daemon. Details: %s", log_safe(e))
            log_connection_troubleshooting(logger, args.robot_name)
            sys.exit(1)

        except ConnectionError as e:
            logger.error("Connection failed: Unable to establish connection to Reachy Mini. Details: %s", log_safe(e))
            log_connection_troubleshooting(logger, args.robot_name)
            sys.exit(1)

        except Exception as e:
            logger.error(f"Unexpected error during robot initialization: {type(e).__name__}: {e}")
            logger.error("Please check your configuration and try again.")
            sys.exit(1)

    app_lifecycle.wake_up_if_sleeping(robot, logger)

    movement_manager = MovementManager(current_robot=robot)

    deps = build_tool_dependencies(
        robot=robot,
        movement_manager=movement_manager,
        instance_path=instance_path,
        camera_enabled=not args.no_camera,
        logger=logger,
    )

    def build_handler(startup_voice: Optional[str] = None) -> ConversationHandler:
        """Build a Hugging Face realtime handler for the current runtime config."""
        from reachy_language_tutor.huggingface_realtime import HuggingFaceRealtimeHandler

        hf_connection_selection = get_hf_connection_selection()
        transport_label = (
            "Hugging Face direct websocket"
            if hf_connection_selection.mode == HF_LOCAL_CONNECTION_MODE and hf_connection_selection.has_target
            else "Hugging Face session proxy"
        )
        logger.info("Using Hugging Face realtime handler (%s)", transport_label)
        return HuggingFaceRealtimeHandler(
            deps,
            instance_path=instance_path,
            startup_voice=startup_voice,
        )

    handler = build_handler(startup_settings.voice)

    stream_manager: LocalStream | None = None
    own_ui_server = None

    effective_settings_app = settings_app
    if args.ui and settings_app is None:
        effective_settings_app = FastAPI()

        @effective_settings_app.middleware("http")
        async def _no_cache(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
            """Serve everything no-store so browsers don't keep stale UI modules."""
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            return response

    stream_manager = LocalStream(
        handler,
        robot,
        settings_app=effective_settings_app,
        instance_path=instance_path,
        handler_factory=build_handler,
        startup_voice=startup_settings.voice,
    )

    # The page is served immediately, so the API must be live before the slow startup work below.
    if effective_settings_app is not None:
        stream_manager._init_settings_ui_if_needed()

    go_to_sleep_lock = threading.Lock()
    go_to_sleep_requested = threading.Event()

    def go_to_sleep_and_stop_app() -> dict[str, Any]:
        """Put Reachy to sleep, then stop the current app."""
        if not go_to_sleep_lock.acquire(blocking=False):
            return {"status": "already_requested"}

        try:
            if go_to_sleep_requested.is_set():
                return {"status": "already_requested"}
            go_to_sleep_requested.set()

            logger.info("Going to sleep before stopping conversation app.")
            sleep_error: str | None = None

            try:
                robot.disable_wobbling()
            except Exception as e:
                logger.debug("Error disabling wobbling before sleep: %s", log_safe(e))

            movement_manager.stop(reset_to_neutral=False)

            try:
                robot.goto_sleep()
            except Exception as e:
                sleep_error = f"{type(e).__name__}: {e}"
                logger.error("Failed to move Reachy Mini to sleep pose: %s", log_safe(e))

            stop_current_app_requested = False
            if app_stop_event is None or not app_stop_event.is_set():
                stop_current_app_requested = app_lifecycle.request_stop_current_app(robot, logger)
            local_stop_requested = True
            if app_stop_event is not None:
                app_stop_event.set()
            else:
                try:
                    stream_manager.close()
                except Exception as e:
                    local_stop_requested = False
                    logger.error("Error while closing stream manager after go_to_sleep: %s", log_safe(e))

            result: dict[str, Any] = {
                "status": "sleeping" if sleep_error is None else "stop_requested",
                "stop_current_app_requested": stop_current_app_requested,
                "local_stop_requested": local_stop_requested,
            }
            if sleep_error is not None:
                result["error"] = f"go_to_sleep movement failed: {sleep_error}"
            return result
        finally:
            go_to_sleep_lock.release()

    deps.go_to_sleep = go_to_sleep_and_stop_app

    def run_go_to_sleep_tool() -> dict[str, Any]:
        return app_lifecycle.run_go_to_sleep_tool(deps, logger)

    if args.ui and settings_app is None and effective_settings_app is not None:
        import uvicorn

        own_ui_server = uvicorn.Server(
            uvicorn.Config(effective_settings_app, host=UI_BIND_HOST, port=7860, log_level="warning")
        )
        threading.Thread(target=own_ui_server.run, daemon=True, name="ui-server").start()
        logger.info("Web UI available at http://%s:7860", UI_BIND_HOST)

    try:
        app_lifecycle.initialize_tools_with_default_fallback(instance_path, logger)
    except Exception as e:
        logger.error("Failed to initialize tools: %s", log_safe(e))
        sys.exit(1)

    # Each async service → its own thread/loop
    movement_manager.start()
    # Audio-reactive head motion is driven by the daemon's wobbler, which
    # taps the media pipeline at push_audio_sample. The console stream pushes
    # assistant audio through that pipeline directly.
    robot.enable_wobbling()

    timeout_minutes = resolve_app_timeout_minutes()
    if timeout_minutes is not None:
        _start_inactivity_timeout_thread(timeout_minutes, stream_manager, logger, app_stop_event, run_go_to_sleep_tool)

    def poll_stop_event() -> None:
        """Poll the stop event to allow graceful shutdown.

        Deliberately does NOT put the robot to sleep: an external stop
        (mobile app, dashboard, app switch) means "stop this app", not
        "power the robot down" — the daemon returns it to the neutral
        pose afterwards, awake and ready for the next app. Sleeping is
        reserved for the explicit paths (the voice go_to_sleep tool and
        the inactivity timeout).
        """
        if app_stop_event is not None:
            app_stop_event.wait()

        logger.info("App stop event detected, shutting down...")
        try:
            stream_manager.close()
        except Exception as e:
            logger.error("Error while closing stream manager: %s", log_safe(e))

    if app_stop_event:
        threading.Thread(target=poll_stop_event, daemon=True).start()

    try:
        stream_manager.launch()
    except KeyboardInterrupt:
        logger.info("Keyboard interruption in main thread... closing server.")
    finally:
        if own_ui_server is not None:
            own_ui_server.should_exit = True

        # Stop the motion writes without changing the robot's posture. If
        # the shutdown came from the voice go_to_sleep tool the robot is
        # already in the sleep pose; on a plain stop it stays awake and
        # the daemon returns it to neutral once the process exits.
        movement_manager.stop(reset_to_neutral=False)
        try:
            robot.disable_wobbling()
        except Exception as e:
            logger.debug("Error disabling wobbling during shutdown: %s", log_safe(e))

        # Ensure media is explicitly closed before disconnecting
        try:
            robot.media.close()
        except Exception as e:
            logger.debug("Error closing media during shutdown: %s", log_safe(e))

        # prevent connection to keep alive some threads
        robot.client.disconnect()
        time.sleep(1)
        logger.info("Shutdown complete.")


class ReachyLanguageTutor(ReachyMiniApp):  # type: ignore[misc]
    """Reachy Mini Apps entry point for the conversation app."""

    # Loopback, and this literal is load-bearing in two ways.
    #
    # It is not a display string: the SDK urlparses it and binds uvicorn to its hostname
    # (reachy_mini/apps/app.py, wrapped_run), so this IS the address the app listens on
    # when the robot daemon launches it. That path is the one every deployed unit uses --
    # the --ui server above is guarded by `settings_app is None` and never runs there --
    # and console.py mounts the JSON-RPC surface onto it. Those methods make the robot
    # speak, UNMUTE its microphone, and rewrite the speech backend's host and port, with
    # no authentication. On 0.0.0.0 that was every device on the household Wi-Fi.
    #
    # It is also read out of this file as TEXT, by a regex taking the FIRST match
    # (reachy_mini/apps/sources/local_common_venv.py, _get_custom_app_url_from_file), so
    # an earlier assignment to this attribute anywhere above would shadow this one -- and
    # writing one in prose is enough to do it, which is not hypothetical: the first draft
    # of this very comment quoted the assignment syntax and the test below caught it
    # extracting the ellipsis as the robot's URL. That test pins that the first match in
    # the file is still the line directly beneath it.
    #
    # This value IS the bind address on a robot: the SDK urlparses it and hands the host to
    # uvicorn (reachy_mini/apps/app.py, wrapped_run).
    #
    # D15 set it to loopback to keep the control surface off the household LAN. That was
    # wrong, and the question the old comment here left open has since been answered by
    # reading the desktop dashboard's own bundle: in all three places it opens an app it
    # does `new URL(custom_app_url); hostname = LI()`, where LI() returns the robot's LAN
    # address whenever the connection is over wifi. The host in this literal is DISCARDED
    # and only the port survives, so on a Wireless unit the dashboard loads
    # http://<robot-lan-ip>:7860/ -- which a loopback-bound uvicorn refuses. Its liveness
    # hook then HEAD-polls that same URL and, after 60s of failure, calls stopCurrentApp.
    # So loopback did not harden the app, it killed it about a minute after start, on every
    # unit. It is invisible on a Mac because a non-wifi connection makes LI() "localhost".
    #
    # The dashboard therefore REQUIRES this port to be LAN-reachable, and no bind address
    # satisfies both that and the threat model. Authenticating instead is not available to
    # this app: there is no channel that reaches the dashboard's iframe without also
    # reaching anyone else on that network, and the SDK carries no credential to it.
    # docs/rpc-control-surface.md records all of it. So the exposure is answered where it
    # can be -- by what the reachable methods are allowed to DO (D20): the mic is read-only
    # and every writer on that surface -- the backend target included -- is refused outright.
    custom_app_url = "http://0.0.0.0:7860/"
    dont_start_webserver = False

    def run(self, reachy_mini: ReachyMini, stop_event: threading.Event) -> None:
        """Run the Reachy Mini conversation app."""
        asyncio.set_event_loop(asyncio.new_event_loop())

        args, _ = parse_args()

        instance_path = self._get_instance_path().parent
        run(
            args,
            robot=reachy_mini,
            app_stop_event=stop_event,
            settings_app=self.settings_app,
            instance_path=instance_path,
        )


if __name__ == "__main__":
    app = ReachyLanguageTutor()
    try:
        app.wrapped_run()
    except KeyboardInterrupt:
        app.stop()
