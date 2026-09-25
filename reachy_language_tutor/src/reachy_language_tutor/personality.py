"""Personality profile data layer."""

import shutil
import logging
from typing import Literal, TypedDict
from pathlib import Path
from collections.abc import Iterable

from reachy_language_tutor.config import (
    USER_PERSONALITIES_DIRNAME,
    ProfileNameError,
    config,
    get_default_voice,
    list_tool_module_names,
)
from reachy_language_tutor.tool_spaces import read_installed_tool_spaces
from reachy_language_tutor.profile_store import (
    DEFAULT_PROFILE_NAME,
    ProfileFormatError,
    write_profile,
    list_profile_names,
    read_profile_from_directory,
    read_packaged_default_profile,
)
from reachy_language_tutor.logging_safety import log_safe
from reachy_language_tutor.profile_toolsets import (
    read_profile_toolsets,
    write_profile_toolsets,
    get_profile_toolsets_path,
    clear_profile_tool_override,
    profile_toolsets_transaction,
)
from reachy_language_tutor.tools.tool_constants import SystemTool


logger = logging.getLogger(__name__)


class AvailableTool(TypedDict):
    """Tool metadata used by personality configuration surfaces."""

    id: str
    kind: Literal["shared", "external", "tool_space"]
    source: str
    description: str


def _visible_profile_names(profiles_root: Path, prefix: str = "") -> list[str]:
    visible: list[str] = []
    for profile_name in list_profile_names(profiles_root):
        try:
            profile = read_profile_from_directory(profile_name, profiles_root / profile_name)
        except (FileNotFoundError, ProfileFormatError) as exc:
            logger.warning("Skipping invalid profile %r: %s", profile_name, log_safe(exc))
            continue
        if not profile.hidden:
            visible.append(f"{prefix}{profile_name}")
    return visible


def list_personalities() -> list[str]:
    """List available visible personality profile names."""
    names = [DEFAULT_PROFILE_NAME]
    names.extend(
        profile_name
        for profile_name in _visible_profile_names(config.PROFILES_DIRECTORY)
        if profile_name != DEFAULT_PROFILE_NAME
    )
    user_root = config.user_personalities_root()
    if user_root != config.PROFILES_DIRECTORY:
        names.extend(_visible_profile_names(user_root, f"{USER_PERSONALITIES_DIRNAME}/"))
    return names


def available_tool_catalog() -> list[AvailableTool]:
    """List configurable tools and their source."""
    catalog: dict[str, AvailableTool] = {}
    excluded_modules = {"__init__", "core_tools", "background_tool_manager", "tool_constants"}
    excluded_modules.update(tool.value for tool in SystemTool)
    for tool_name in list_tool_module_names(Path(__file__).parent / "tools"):
        if tool_name in excluded_modules:
            continue
        catalog[tool_name] = {
            "id": tool_name,
            "kind": "shared",
            "source": "Built-in",
            "description": "",
        }

    for tool_name in list_tool_module_names(config.TOOLS_DIRECTORY):
        catalog[tool_name] = {
            "id": tool_name,
            "kind": "external",
            "source": "External",
            "description": "",
        }

    try:
        for space in read_installed_tool_spaces(config.INSTANCE_PATH).spaces:
            for tool in space.tools:
                catalog[tool.local_name] = {
                    "id": tool.local_name,
                    "kind": "tool_space",
                    "source": space.slug,
                    "description": tool.description,
                }
    except (RuntimeError, ValueError) as exc:
        logger.warning("Failed to list installed Tool Space tools: %s", log_safe(exc))
    return [catalog[tool_id] for tool_id in sorted(catalog)]


def delete_personality(name: str) -> bool:
    """Delete a user-created personality without touching bundled profiles.

    Answers False for a name that is not a bare segment rather than raising:
    this function's contract is a boolean, and its RPC caller turns False into
    "not_deletable", which is the right answer for a name that can never name
    anything deletable.

    The containment check below is still load-bearing, but NOT for the reason an
    earlier revision of this docstring gave. It claimed the check caught a symlink
    pointing out of the root; resolve_profile_dir now performs that containment
    itself, so such a name raises before this function ever inspects the path.
    What this check actually does is the job the summary line names: a bundled
    profile resolves perfectly well, and is refused here because the user root is
    not among its parents. Deleting a shipped personality is not this function's
    business.
    """
    try:
        target = config.resolve_profile_dir(name).resolve()
    except ProfileNameError:
        return False
    user_root = config.user_personalities_root().resolve()
    if user_root not in target.parents:
        return False
    if not target.is_dir():
        return False
    shutil.rmtree(target)
    try:
        clear_profile_tool_override(name, config.INSTANCE_PATH)
    except (OSError, RuntimeError) as exc:
        logger.warning("Deleted personality %r but could not remove its tool override: %s", name, log_safe(exc))
    return True


def save_user_personality(
    name: str,
    instructions: str,
    voice: str | None = None,
    greeting: str | None = None,
    *,
    overwrite: bool = False,
    default_tools: Iterable[str] | None = None,
) -> str:
    """Save a custom personality with optional authored tool defaults."""
    profile_name = name.strip()
    # Name first. The instructions check quotes the name back, and unifying this
    # function onto resolve_profile_dir moved name validation later, so a hostile
    # name with empty instructions started getting echoed into an error the save
    # route returns as message=str(exc). Validating first restores the old order.
    selection = f"{USER_PERSONALITIES_DIRNAME}/{profile_name}"
    profile_directory = config.resolve_profile_dir(selection)
    if not instructions.strip():
        raise ValueError(f"Profile {profile_name!r} must have non-empty instructions.")

    # One rule, one place. This used to carry its own copy of the name allow-list
    # and then join the root itself, which meant the write path and the read path
    # each had a rule that could drift from the other. Building the directory
    # through resolve_profile_dir validates the name AND yields the same path the
    # readers will later resolve for this selection, by construction rather than
    # by two functions agreeing. ProfileNameError is a ValueError, so the route
    # above still maps it to "invalid_name".
    selected_voice = voice or get_default_voice()
    authored_tools = tuple(default_tools) if default_tools is not None else None
    with profile_toolsets_transaction():
        if profile_directory.exists() and not overwrite:
            raise FileExistsError(f"Personality {profile_name!r} already exists.")
        try:
            previous_profile = read_profile_from_directory(profile_name, profile_directory)
            profile_tools = previous_profile.default_tools
            hidden = previous_profile.hidden
        except FileNotFoundError:
            profile_tools = read_packaged_default_profile().default_tools
            hidden = False
        if authored_tools is not None:
            profile_tools = authored_tools

        toolsets_path = get_profile_toolsets_path(config.INSTANCE_PATH)
        toolsets_existed = toolsets_path.is_file()
        previous_toolsets = read_profile_toolsets(config.INSTANCE_PATH) if authored_tools is not None else None

        if authored_tools is not None:
            clear_profile_tool_override(selection, config.INSTANCE_PATH)

        try:
            write_profile(
                profile_name,
                profile_directory,
                instructions,
                profile_tools,
                voice=selected_voice,
                greeting=greeting,
                hidden=hidden,
                overwrite=overwrite,
            )
        except OSError:
            try:
                if toolsets_existed and previous_toolsets is not None:
                    write_profile_toolsets(config.INSTANCE_PATH, previous_toolsets)
                elif authored_tools is not None:
                    toolsets_path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning(
                    "Failed to restore profile toolsets after saving profile %r: %s", profile_name, log_safe(exc)
                )
            raise
    return selection
