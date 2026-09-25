"""Helpers for persisting UI-selected startup profile and voice settings."""

from __future__ import annotations
import os
import json
import logging
from pathlib import Path
from dataclasses import dataclass


logger = logging.getLogger(__name__)

STARTUP_SETTINGS_FILENAME = "startup_settings.json"

# Matching learners/store.py's _OWNER_ONLY and memory.py's, because this file joined
# them in holding something that names a person.
_OWNER_ONLY = 0o600


@dataclass(frozen=True)
class StartupSettings:
    """Instance-local startup settings selected out of band, never by the conversation.

    `fallback_learner` is the learner the app serves when recognition cannot name
    anybody. It lives HERE, in a file on disk, for one reason: nothing the model says
    can write it. The conversation has no tool that takes a path, no tool that takes
    an identity, and no way to reach this module at all -- so the only way this value
    changes is somebody with the device editing it, which is the boundary the whole
    app is built around. current_learner.py carries what it is and is not worth.
    """

    profile: str | None = None
    voice: str | None = None
    fallback_learner: str | None = None


def _normalize_optional_text(value: object) -> str | None:
    """Return a stripped string or None for empty/non-string values."""
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _startup_settings_path(instance_path: str | Path | None) -> Path | None:
    """Return the startup settings JSON path for an instance directory."""
    if instance_path is None:
        return None
    return Path(instance_path) / STARTUP_SETTINGS_FILENAME


def read_startup_settings(instance_path: str | Path | None) -> StartupSettings:
    """Read startup settings from an instance-local JSON file."""
    settings_path = _startup_settings_path(instance_path)
    if settings_path is None or not settings_path.exists():
        return StartupSettings()

    # The TYPE only, on both lines, and neither the path nor the payload. The path is the
    # instance directory, which can name a household; the payload is this file's content,
    # which holds a learner id since fallback_learner landed. Measured before this: a
    # settings file holding a bare JSON list put `['<learner id>']` into a WARNING, and a
    # truncated one put the full path under the instance directory there. An OSError's
    # own str() embeds the path too, which is why the exception is not rendered either.
    try:
        payload = json.loads(settings_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to read the startup settings: %s", type(exc).__name__)
        return StartupSettings()

    if not isinstance(payload, dict):
        logger.warning("Ignoring the startup settings: the file holds a %s, not an object", type(payload).__name__)
        return StartupSettings()

    return StartupSettings(
        profile=_normalize_optional_text(payload.get("profile")),
        voice=_normalize_optional_text(payload.get("voice")),
        fallback_learner=_normalize_optional_text(payload.get("fallback_learner")),
    )


def write_startup_settings(
    instance_path: str | Path | None,
    *,
    profile: str | None,
    voice: str | None,
    fallback_learner: str | None = None,
) -> None:
    """Persist startup settings in an instance-local JSON file.

    THIS WRITES THE WHOLE FILE, so every caller passes every field it means to keep.
    That is why fallback_learner is a keyword with a default rather than something
    inferred: a function that silently merged would hide which caller owns which
    field, and this file now holds an identity.

    An earlier version of this docstring called the resulting behaviour deliberate --
    that omitting the keyword CLEARS the fallback, so saving a voice turns it off.
    The reasoning was backwards: preserving what an operator configured is the status
    quo, not a re-assertion, and the effect was that changing the voice in the
    settings UI silently stopped the robot serving anybody. Both UI callers now read
    the value back and pass it, the way one of them already did for the profile.
    """
    settings_path = _startup_settings_path(instance_path)
    if settings_path is None:
        return

    settings = StartupSettings(
        profile=_normalize_optional_text(profile),
        voice=_normalize_optional_text(voice),
        fallback_learner=_normalize_optional_text(fallback_learner),
    )
    if settings.profile is None and settings.voice is None and settings.fallback_learner is None:
        try:
            settings_path.unlink()
        except FileNotFoundError:
            return
        return

    payload: dict[str, str] = {}
    if settings.profile is not None:
        payload["profile"] = settings.profile
    if settings.voice is not None:
        payload["voice"] = settings.voice
    if settings.fallback_learner is not None:
        payload["fallback_learner"] = settings.fallback_learner

    # OWNER ONLY, for the reason the learner database and the memory file are: since
    # fallback_learner landed, this file can hold a learner id, which names a person
    # to anybody who can also read the database. A review found it at 0o644 while its
    # two siblings in the same directory were 0o600 -- the unswept-sibling shape this
    # repository pays for more than any other, and it was unswept in the same change
    # that fixed one of them.
    #
    # Created with the mode rather than chmod'd after, so there is no window in which
    # the contents exist at the umask default.
    descriptor = os.open(settings_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _OWNER_ONLY)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(f"{json.dumps(payload, indent=2, sort_keys=True)}\n")
    # O_CREAT sets the mode only on creation, so an existing file keeps whatever it
    # had -- including one written by a version of this function that predates this.
    try:
        os.chmod(settings_path, _OWNER_ONLY)
    except OSError as exc:
        logger.warning("Could not restrict permissions on the startup settings: %s", type(exc).__name__)


def load_startup_settings_into_runtime(instance_path: str | Path | None) -> StartupSettings:
    """Load instance-local startup settings when no explicit profile override is set."""
    from reachy_language_tutor.config import LOCKED_PROFILE, set_custom_profile

    if LOCKED_PROFILE is not None:
        return StartupSettings()

    settings_path = _startup_settings_path(instance_path)
    settings = read_startup_settings(instance_path)
    if settings_path is None or not settings_path.exists():
        if os.getenv("REACHY_MINI_CUSTOM_PROFILE"):
            return StartupSettings(voice=settings.voice)

    set_custom_profile(settings.profile)
    return settings


def set_fallback_learner(instance_path: str | Path | None, learner_id: str | None) -> None:
    """Set, or clear with None, the learner served when recognition cannot answer.

    Reads the current settings and writes them back with this one field changed, so
    configuring who the robot falls back to does not silently drop the operator's
    profile and voice. write_startup_settings replaces the whole file, which is why
    the read is here rather than left to each caller to remember.

    THE ID IS NOT VALIDATED HERE, and that is on purpose. A learner can be forgotten
    long after this is written, so a check at write time would prove nothing at read
    time and would read like a guarantee it cannot make. The value is checked where
    it is USED, against the database, on every startup.
    """
    current = read_startup_settings(instance_path)
    write_startup_settings(
        instance_path,
        profile=current.profile,
        voice=current.voice,
        fallback_learner=learner_id,
    )
