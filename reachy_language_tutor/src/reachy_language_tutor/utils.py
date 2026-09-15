from __future__ import annotations
import json
import logging
import argparse
import warnings
from typing import Any, Optional


def parse_args() -> tuple[argparse.Namespace, list]:  # type: ignore
    """Parse command line arguments."""
    parser = argparse.ArgumentParser("Reachy Mini Conversation App")
    parser.add_argument("--no-camera", default=False, action="store_true", help="Disable camera usage")
    parser.add_argument(
        "--ui",
        default=False,
        action="store_true",
        help="Serve the web UI at http://127.0.0.1:7860/, in addition to console mode",
    )
    parser.add_argument("--debug", default=False, action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--robot-name",
        type=str,
        default=None,
        help="[Optional] Robot name to target. Must match the daemon's --robot-name when connecting to a specific robot, mainly useful for development with multiple robots.",
    )
    subparsers = parser.add_subparsers(dest="command")
    tool_spaces_parser = subparsers.add_parser("tool-spaces", help="Manage installed Hugging Face Space tool sources")
    tool_spaces_subparsers = tool_spaces_parser.add_subparsers(dest="tool_spaces_command", required=True)

    add_parser = tool_spaces_subparsers.add_parser("add", help="Install one Space tool source by slug")
    add_parser.add_argument("space_slug", help="Hugging Face Space slug in the form owner/space-name")
    add_parser.add_argument(
        "--install-only",
        action="store_true",
        default=False,
        help="Install the Space without enabling its tools in any profile.",
    )
    add_parser.add_argument(
        "--profile",
        dest="profile",
        default=None,
        metavar="PROFILE",
        help="Enable tools in this profile instead of the active profile.",
    )

    remove_parser = tool_spaces_subparsers.add_parser("remove", help="Remove one installed Space tool source")
    remove_parser.add_argument("space_slug", help="Installed Hugging Face Space slug in the form owner/space-name")

    tool_spaces_subparsers.add_parser("list", help="List installed Space tool sources")

    # Enrolment is a SUBCOMMAND and not a tool, and that is the whole security point
    # rather than a packaging preference: it creates an identity, and the model must
    # never be able to do that. Reaching it needs a shell on the machine the robot is
    # running on. It is deliberately absent from the /rpc surface too, which is
    # LAN-reachable and cannot be authenticated.
    enrol_parser = subparsers.add_parser(
        "enrol",
        help="Enrol a household member's face, in person, after recording their consent",
        # The warning handle_enrol_command's docstring says is here. It was not, until
        # a review looked for it. Every branch of this subcommand prints a household
        # member's name -- deliberately, because a consent read-back or an erasure
        # that cannot say WHO is not worth much -- and a terminal is not a log, but a
        # redirect turns one into the other.
        epilog=(
            "This subcommand prints a household member's name to the terminal, which is "
            "how it confirms who was enrolled, read back or erased. Redirecting or piping "
            "its output writes that name into whatever file you send it to."
        ),
    )
    enrol_parser.add_argument(
        "--name",
        dest="enrol_name",
        default=None,
        metavar="NAME",
        help="The name this person goes by, in their own script.",
    )
    # No default, deliberately, and the requirement is enforced in the command rather
    # than by argparse -- `required=True` here would make `--show` unusable, since
    # reading back what somebody agreed to needs no new consent. This is the app's
    # answer to who may consent for a child: the question is answered every time by
    # whoever is standing at the robot, never inherited from a default nobody reads.
    # `choices` is what makes a mis-typed role a parse error rather than a bad row.
    enrol_parser.add_argument(
        "--consent-from",
        dest="consent_from",
        choices=("the-person-themselves", "an-adult-of-the-household"),
        default=None,
        help="Who is giving permission. Required unless --show is used.",
    )
    enrol_parser.add_argument(
        "--show",
        dest="show_learner",
        default=None,
        metavar="LEARNER_ID",
        help="Print what this learner has agreed to, and exit. Writes nothing.",
    )
    # The route the consent notice promises. Without it, "you can ask whoever set this
    # robot up to delete all of it" was a sentence with no code behind it --
    # delete_faceprint existed and had no caller anywhere in the app.
    enrol_parser.add_argument(
        "--forget",
        dest="forget_learner_id",
        default=None,
        metavar="LEARNER_ID",
        help="Delete this learner's faceprint. Their lesson history is kept.",
    )
    # The remedy for an enrolment whose rollback failed. --forget cannot do it: that
    # deletes a faceprint, and such a learner has none by construction. This calls
    # forget_learner, which removes the learner, their consent and any faceprint --
    # and refuses anybody with lesson history, in the statement itself.
    enrol_parser.add_argument(
        "--remove",
        dest="remove_learner_id",
        default=None,
        metavar="LEARNER_ID",
        help="Remove a learner who has no lesson history, with their consent record. Undoes a half-finished enrolment.",
    )
    # The second erasure, and deliberately a separate flag from --forget. "Stop
    # recognising me" and "forget me" are different requests and a person will want
    # each without the other; one flag doing both would cost somebody a year of
    # learning to turn off a camera feature.
    enrol_parser.add_argument(
        "--forget-everything",
        dest="forget_everything_id",
        default=None,
        metavar="LEARNER_ID",
        help="Erase this person completely: faceprint, agreement, lesson history and their record.",
    )
    # WHO THE ROBOT SERVES WHEN RECOGNITION CANNOT SAY. An operator flag, writing an
    # instance-local settings file, and that placement IS the security property: the
    # conversation has no tool that takes a path and none that takes an identity, so
    # a spoken name can never reach this. It is a configuration rather than an
    # authentication, and the help text says so rather than letting the flag imply
    # more than it does.
    # THE HOUSEHOLD THAT DECLINES FACE RECOGNITION, which could not otherwise be in
    # the database at all: the only way to create a learner is to consent to
    # something, and the only thing to consent to was face recognition. Declining
    # meant no profile and no way to use the app, which is the opposite of what an
    # opt-in is for. This records the other scope and never opens the camera.
    enrol_parser.add_argument(
        "--without-face",
        dest="without_face",
        action="store_true",
        help="Register this person with a learning record only. No camera, no face data.",
    )
    enrol_parser.add_argument(
        "--serve-when-unrecognised",
        dest="fallback_learner_id",
        default=None,
        metavar="LEARNER_ID",
        help=(
            "Serve this learner when recognition cannot name anybody. A setting, not a check: "
            "in a home where more than one person uses the robot it will serve this person to "
            "whoever sits down, so use recognition there instead."
        ),
    )
    enrol_parser.add_argument(
        "--serve-nobody-when-unrecognised",
        dest="clear_fallback_learner",
        action="store_true",
        help="Clear the fallback, so the app serves nobody when recognition cannot name anybody.",
    )
    enrol_parser.add_argument(
        "--instance-path",
        dest="instance_path",
        default=None,
        metavar="DIR",
        help="The app instance directory holding the learner database.",
    )
    return parser.parse_known_args()


# How deep to describe before giving up. Tool results are shallow; this only stops a
# pathological structure from turning one log line into a wall of text.
_LOG_DESCRIPTION_MAX_DEPTH = 3


def describe_for_log(value: Any, *, trust_keys: bool = False, _depth: int = 0) -> str:
    """Describe a value's shape for a log line without reproducing its contents.

    Tool results carry personal data -- a learner's name, their lesson results --
    and this app logs them one layer above the tools that produce them, where no
    amount of care inside a tool can help. So no value is ever rendered here: only
    its type, and how much of it there is. That leaves enough to debug dispatch
    (which keys came back, of what shape) and nothing worth reading off a screen.

    Keys are rendered only when a caller says they are OURS. On the way out of a tool
    they are schema -- "display_name", "languages" -- and naming them is most of the
    debug signal, so those callers pass trust_keys=True. On the way in they are
    composed by the model, so a key could be anything a person said out loud.

    The default is the safe one, and deliberately so: the wrong choice here is silent
    -- no error, no failing test, just a name in a log -- so it must be the one a
    caller has to ask for rather than the one they get by not thinking about it.

    Trust stops at the envelope. A caller vouches for the keys it put there itself,
    not for whatever a tool nested inside them: a Space tool's result carries keys a
    third party chose, and at milestone 5 the learner tools become exactly that shape
    when their results arrive from a hosted backend. So nesting is always described
    as untrusted -- and the top level is where the debug signal lives anyway.
    """
    if _depth > _LOG_DESCRIPTION_MAX_DEPTH:
        return "..."
    if value is None:
        return "None"
    if isinstance(value, bool):
        return "bool"  # before int: bool is a subclass of int
    if isinstance(value, (int, float)):
        return type(value).__name__
    if isinstance(value, str):
        return f"str(len={len(value)})"
    if isinstance(value, dict):
        if not value:
            return "{}"
        if not trust_keys:
            # The count and the value types, never the names. A model asked to call a
            # tool can put anything in a key, including something a person just said.
            types = ", ".join(describe_for_log(item, trust_keys=False, _depth=_depth + 1) for item in value.values())
            return f"{{{len(value)} keys: {types}}}"
        inner = ", ".join(f"{key}: {describe_for_log(item, _depth=_depth + 1)}" for key, item in value.items())
        return "{" + inner + "}"
    if isinstance(value, (list, tuple)):
        if not value:
            return "[]"
        return f"[{len(value)} x {describe_for_log(value[0], _depth=_depth + 1)}]"
    return type(value).__name__


def describe_json_for_log(raw: Any, *, trust_keys: bool = False) -> str:
    """Describe a JSON-encoded payload's shape without reproducing its contents.

    Tool arguments arrive as a JSON string, and a tool's arguments carry a person's
    data as readily as its result does -- recording a lesson result means handing the
    outcome in, not getting it back -- so they are rendered the same way.

    A payload that does not parse is described as the string it is, never echoed.
    Falling back to the raw text would make this fail open exactly where the input is
    least trustworthy, which is the shape of defect this control exists to prevent.

    Keys are not rendered by default. The payload this is usually handed is composed
    by the model, so its key names are not schema the way a tool's own result keys
    are -- a key could carry whatever a person just said to the robot. A caller that
    knows it holds OUR json, a tool result rather than a tool call, passes
    trust_keys=True and gets the names back.
    """
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except Exception:
            # Deliberately broad: json.loads raises RecursionError on pathological
            # nesting, and a logging call must not take the event loop down with it.
            return describe_for_log(raw, trust_keys=trust_keys)
        return describe_for_log(parsed, trust_keys=trust_keys)
    return describe_for_log(raw, trust_keys=trust_keys)


def tool_call_message(lead: str, tool_name: str, args_json_str: str, tool_id: Any) -> dict[str, Any]:
    """Build the queue message announcing a tool call, already tagged for logging.

    Built in one place because the console's redaction keys off the message KIND, and
    an untagged message is indistinguishable from transcript -- so it would be logged
    in cleartext. Tagging at each call site makes that a thing every future producer
    has to remember; building the message here makes it a property of the message.
    """
    return {
        "role": "assistant",
        "content": f"{lead} {tool_name} with args {args_json_str}. The tool is now running. Tool ID: {tool_id}",
        "kind": "tool_call",
        "log_safe": (
            f"{lead} {tool_name} with args {describe_json_for_log(args_json_str)}. "
            f"The tool is now running. Tool ID: {tool_id}"
        ),
    }


def setup_logger(debug: bool) -> logging.Logger:
    """Setups the logger."""
    log_level = "DEBUG" if debug else "INFO"
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s:%(lineno)d | %(message)s",
        force=True,
    )
    logger = logging.getLogger(__name__)

    # Suppress WebRTC warnings
    warnings.filterwarnings("ignore", message=".*AVCaptureDeviceTypeExternal.*")
    warnings.filterwarnings("ignore", category=UserWarning, module="aiortc")

    # Tame third-party noise (looser in DEBUG)
    if log_level == "DEBUG":
        logging.getLogger("aiortc").setLevel(logging.INFO)
        logging.getLogger("aioice").setLevel(logging.INFO)
        logging.getLogger("openai").setLevel(logging.INFO)
        logging.getLogger("websockets").setLevel(logging.INFO)
    else:
        logging.getLogger("aiortc").setLevel(logging.ERROR)
        logging.getLogger("aioice").setLevel(logging.WARNING)
    return logger


def log_connection_troubleshooting(logger: logging.Logger, robot_name: Optional[str]) -> None:
    """Log troubleshooting steps for connection issues."""
    logger.error("Troubleshooting steps:")
    logger.error("  1. Verify reachy-mini-daemon is running")

    if robot_name is not None:
        logger.error(f"  2. Daemon must be started with: --robot-name '{robot_name}'")
    else:
        logger.error("  2. If daemon uses --robot-name, add the same flag here: --robot-name <name>")

    logger.error("  3. For wireless: check network connectivity")
    logger.error("  4. Review daemon logs")
    logger.error("  5. Restart the daemon")
