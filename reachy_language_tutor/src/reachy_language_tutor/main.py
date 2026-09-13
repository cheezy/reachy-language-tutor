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


# MILESTONE 4 REPLACES THIS LINE. Until face recognition exists the app serves exactly
# one seeded learner. This is the only place in the application package that chooses an
# identity: the LLM cannot reach it, and no tool or conversation may change it.
HARDCODED_CURRENT_LEARNER_ID = "sample-learner"


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


def resolve_current_learner_id(instance_path: str | Path | None, logger: logging.Logger) -> str | None:
    """Decide which learner the app is serving, or None when that cannot be trusted.

    Serving the wrong person their housemate's data needs a wrong *identity*; None
    cannot cause it, because every reader refuses an unknown learner. So this never
    aborts startup -- a corrupt database in someone's home would brick the robot for
    no security gain. It is loud at the identity boundary and permissive at the
    process boundary: the learner surface goes dead and says why.
    """
    from reachy_language_tutor.learners import get_profile, store_is_available

    try:
        if get_profile(HARDCODED_CURRENT_LEARNER_ID, instance_path=instance_path) is not None:
            return HARDCODED_CURRENT_LEARNER_ID
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
    current_learner_id = resolve_current_learner_id(instance_path, logger)

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
