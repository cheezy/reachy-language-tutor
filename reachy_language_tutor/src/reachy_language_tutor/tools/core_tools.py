import abc
import sys
import json
import asyncio
import inspect
import logging
import importlib
import threading
import traceback
import importlib.util
from types import ModuleType
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Callable, ClassVar, Optional, Sequence, TypedDict
from pathlib import Path
from dataclasses import field, dataclass

from reachy_mini import ReachyMini
from reachy_language_tutor.utils import describe_json_for_log
from reachy_language_tutor.config import config, list_tool_module_names
from reachy_language_tutor.mcp_client import McpToolTimeoutError, McpToolInvocationError
from reachy_language_tutor.tool_spaces import build_remote_client, read_installed_tool_spaces
from reachy_language_tutor.profile_store import DEFAULT_PROFILE_NAME
from reachy_language_tutor.lesson_session import LessonSessionHolder
from reachy_language_tutor.profile_toolsets import read_profile_tool_names
from reachy_language_tutor.tools.tool_constants import SystemTool


if TYPE_CHECKING:
    from reachy_language_tutor.mcp_client import RemoteMcpToolClient
    from reachy_language_tutor.tools.background_tool_manager import BackgroundToolManager


logger = logging.getLogger(__name__)


class MissingToolFileError(FileNotFoundError):
    """Raised when a requested tool file is absent on disk."""


class SealedDependencyError(AttributeError):
    """Raised when something tries to repoint a dependency that startup fixed."""


class CurrentLearnerIsReadOnlyError(SealedDependencyError):
    """Raised when something tries to repoint who the app is serving after startup."""


class RunningLessonIsReadOnlyError(SealedDependencyError):
    """Raised when something tries to swap the holder of the running lesson."""


# Interpolates nothing, for the same reason the learner refusal does not: this text is
# rendered into a tool error and handed to the model.
_RUNNING_LESSON_IS_FIXED = (
    "ToolDependencies.lesson_session is fixed when the dependencies are built. The holder is what changes -- "
    "open() and clear() move the lesson inside it -- while the holder itself is bound at startup to the one "
    "learner the app is serving, so nothing reachable from a conversation can swap in a holder carrying a "
    "lesson nobody started."
)


@dataclass
class ToolDependencies:
    """External dependencies injected into tools."""

    reachy_mini: ReachyMini
    movement_manager: Any  # MovementManager from moves.py
    # Optional deps
    instance_path: str | Path | None = None
    camera_enabled: bool = False
    motion_duration_s: float = 1.0
    go_to_sleep: Callable[[], dict[str, Any]] | None = None
    # Who the app is serving. Application startup sets this once (see main.py); nothing
    # reachable from a conversation may write it, which is the whole point -- the LLM
    # sees a tool's parameters_schema, never these dependencies. None means nobody is
    # identified, and a tool must refuse rather than guess: guessing would serve one
    # household member another person's data. Sealed after construction: see below.
    current_learner_id: str | None = None

    # What lesson is running, held beside who the app is serving and sealed with it.
    # The holder is mutable; this reference to it is not. open() and clear() move the
    # lesson inside the holder, which is the whole of the mutation this design wants,
    # so sealing the reference costs nothing and closes the one thing the holder's own
    # guards cannot reach.
    #
    # What they cannot reach: LessonSessionHolder.read_for proves the LEARNER dimension
    # only. It checks that a session was opened for the learner asking, and it has no
    # way to attest that the LESSON was chosen by the app rather than echoed out of a
    # conversation. So a holder swapped wholesale for one bound to the same learner but
    # carrying a lesson nobody started would read back clean -- which is exactly the
    # harm lesson_session.py exists to prevent. An earlier version of this comment cited
    # the read guard as if it covered both dimensions and left the reference writable on
    # the strength of that; it covers one, and the seal is the other.
    #
    # default_factory, so the field is never None and no tool needs an "is there a
    # holder" branch on a path where the answer must never be ambiguous. It binds to
    # nobody, and a holder bound to nobody pins nothing.
    lesson_session: LessonSessionHolder = field(default_factory=LessonSessionHolder)

    # Not fields: ClassVar is excluded from dataclasses.fields(), so repr, __eq__,
    # asdict and every test deriving identity fields from fields() are untouched.
    # __init__ assigns each field through __setattr__ while the seal is still the
    # class-level False; __post_init__ runs after the last one and shadows the flag on
    # the instance. That is what draws the line between "still constructing" and
    # "mutating afterwards" -- a frozen=True blanket ban would break run(), which
    # legitimately assigns deps.go_to_sleep once startup is further along.
    #
    # Two ways to lose this, both outside what __setattr__ can refuse. A subclass that
    # overrides __post_init__ without calling super() never installs the seal at all.
    # And rebinding _SEALED_ATTRIBUTES or __setattr__ on the CLASS disarms it for every
    # instance, because this hook governs instances rather than the class object. A
    # test in test_current_learner.py scans the package for both.
    # __class__ is here because reassigning it swaps in a different __setattr__ on the
    # SAME object every module is already holding, after which the field is writable
    # again. Nothing legitimately reclasses a dependencies bundle.
    # The allow-list names ITSELF. Without that, shadowing it on the instance is an
    # ordinary write this hook permits, and the next line writes the field -- the guard
    # would be exactly one assignment away from being switched off. It is also read off
    # the class in both hooks below, so a shadow cannot answer for what is protected.
    _SEALED_ATTRIBUTES: ClassVar[frozenset[str]] = frozenset(
        # __dict__ is here for the sharpest reason of all: replacing it wholesale is an
        # ordinary attribute write this hook SEES and would otherwise permit, and the
        # replacement carries no seal flag -- so the guard would not merely be bypassed,
        # it would be switched off for every write afterwards.
        {
            "current_learner_id",
            "lesson_session",
            "_identity_sealed",
            "_SEALED_ATTRIBUTES",
            "__class__",
            "__dict__",
        }
    )
    _identity_sealed: ClassVar[bool] = False

    def __post_init__(self) -> None:
        """Seal the identity once construction has assigned every field."""
        # object.__setattr__, not self._identity_sealed = True, which __setattr__ would
        # refuse the moment this line had already run once.
        object.__setattr__(self, "_identity_sealed", True)

    def __setattr__(self, name: str, value: Any) -> None:
        """Refuse to repoint a sealed dependency after the bundle is built."""
        # The allow-list is read through the __class__ closure cell, not through
        # type(self): `type` is a module global, and a guard must not consult
        # anything an attacker can rebind. That is the mistake this file made
        # three times -- the allow-list, the instance dict, and then this.
        if self._identity_sealed and name in __class__._SEALED_ATTRIBUTES:
            # No value is interpolated, deliberately. _dispatch_tool_call turns a tool's
            # exception into {"error": f"{type(e).__name__}: {e}"} and hands that to the
            # model, so an id in this text would be a learner id echoed to the LLM.
            # The attribute NAME is not a value -- it is a field of this class, fixed at
            # source -- so choosing the refusal by it discloses nothing.
            if name == "lesson_session":
                raise RunningLessonIsReadOnlyError(_RUNNING_LESSON_IS_FIXED + " It cannot be reassigned in place.")
            raise CurrentLearnerIsReadOnlyError(
                "ToolDependencies.current_learner_id is fixed when the dependencies are built, and so is the "
                "seal that keeps it that way. Who the app is serving is decided once, at application startup, "
                "so nothing reachable from a conversation can repoint it at another household member. Pass "
                "current_learner_id to ToolDependencies(...) at construction, or build a new ToolDependencies "
                "-- it cannot be reassigned in place."
            )
        super().__setattr__(name, value)

    def __delattr__(self, name: str) -> None:
        """Refuse to delete the current learner, or the seal that protects it.

        Deletion is part of the same attribute protocol as assignment and has to be
        refused with it. `del deps._identity_sealed` would otherwise remove the
        instance flag, leave the class-level False in its place, and make an ordinary
        `deps.current_learner_id = ...` land on the very next line.
        """
        # The allow-list is read through the __class__ closure cell, not through
        # type(self): `type` is a module global, and a guard must not consult
        # anything an attacker can rebind. That is the mistake this file made
        # three times -- the allow-list, the instance dict, and then this.
        if self._identity_sealed and name in __class__._SEALED_ATTRIBUTES:
            if name == "lesson_session":
                raise RunningLessonIsReadOnlyError(_RUNNING_LESSON_IS_FIXED + " It cannot be deleted either.")
            raise CurrentLearnerIsReadOnlyError(
                "ToolDependencies.current_learner_id is fixed when the dependencies are built, and so is the "
                "seal that keeps it that way. It cannot be deleted any more than it can be reassigned: who the "
                "app is serving is decided once, at application startup. Build a new ToolDependencies if a "
                "different learner is being served."
            )
        super().__delattr__(name)


class ToolSpec(TypedDict):
    """Function-calling spec for a tool, in the OpenAI-compatible shape."""

    type: Literal["function"]
    name: str
    description: str
    parameters: dict[str, Any]  # arbitrary JSON Schema


class Tool(abc.ABC):
    """Base abstraction for tools used in function-calling.

    Each tool must define:
      - name: str
      - description: str
      - parameters_schema: Dict[str, Any]  # JSON Schema

    Tools may override:
      - needs_response: bool = True  # set False to skip the spoken follow-up after this tool runs
    """

    _auto_register: ClassVar[bool] = True
    needs_response: ClassVar[bool] = True
    # May this tool's top-level result keys be named in a log? True here because a
    # tool defined in this tree composes its own return dict, so its keys are our
    # schema. A tool that returns somebody ELSE's payload verbatim must set this
    # False -- RemoteMcpTool does, and so must any future wrapper around a hosted
    # backend or an external service.
    #
    # THIS DEFAULT IS OPT-OUT, and the rule does not rely on it alone. An earlier
    # comment here claimed the marker made trust opt-in; review measured that and it
    # was false -- a subclass that sets nothing still inherits True. What the marker
    # buys is that a tool can now opt OUT without subclassing RemoteMcpTool, which
    # the subclass test it replaced could not express. The reachable population this
    # default would otherwise trust -- external tool FILES loaded from
    # TOOLS_DIRECTORY -- is excluded by provenance in log_trust_for_resolved_tool.
    log_keys_are_ours: ClassVar[bool] = True

    name: str
    description: str
    parameters_schema: Dict[str, Any]

    def spec(self) -> ToolSpec:
        """Return the function spec for LLM consumption."""
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters_schema,
        }

    @abc.abstractmethod
    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Async tool execution entrypoint."""
        raise NotImplementedError


ALL_TOOLS: Dict[str, Tool] = {}
_TOOLS_SIGNATURE: tuple[str, str, str | None, bool, str | None] | None = None
_TOOLS_INSTANCE_PATH: str | Path | None = None
_LOADED_TOOL_CLASS_CACHE: Dict[tuple[str, str], List[type[Tool]]] = {}
_REMOTE_TOOL_RETRY_DELAY_S = 0.25
_TOOLS_LOCK = threading.RLock()
_EXTERNAL_TOOL_MODULE_NAMESPACE = "reachy_language_tutor._external_tools"
# Where a tool has to be DEFINED for its result keys to count as our schema.
# Deliberately narrower than the package: _external_tools sits under
# reachy_language_tutor too, so a prefix of the package alone would admit the
# very tools this excludes.
_OUR_TOOLS_NAMESPACE = "reachy_language_tutor.tools."


class RemoteMcpTool(Tool):
    """Adapter exposing one remote MCP tool through the local Tool interface."""

    _auto_register: ClassVar[bool] = False
    # Returns dict(result) from a third-party Space, so the envelope keys belong to
    # whoever wrote it and may be anything -- a learner's name among them.
    log_keys_are_ours: ClassVar[bool] = False

    def __init__(
        self,
        *,
        slug: str,
        name: str,
        description: str,
        parameters_schema: Dict[str, Any],
        client_tool_name: str,
        client: "RemoteMcpToolClient",
    ) -> None:
        """Store the resolved local/remote names and the shared MCP client."""
        self.name = name
        self.description = description
        self.parameters_schema = parameters_schema
        self._space_slug = slug
        self._client_tool_name = client_tool_name
        self._client = client
        self._registry_source = f"space:{slug}:{client_tool_name}"

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Invoke the underlying remote MCP tool."""
        try:
            result = await self._client.call_tool(self._client_tool_name, kwargs)
        except McpToolTimeoutError:
            # Timeout subclasses the retryable error, but retrying it would just double the wait.
            raise
        except McpToolInvocationError as exc:
            # The type, not the text. A remote server's error is written in response to
            # a call whose ARGUMENTS were the learner's data, so its message can quote
            # them back. The two identifiers before it are ours and are what triage
            # needs. Same rule as the dispatch error below; only the retry's failure
            # reaches that one, so this line needs its own.
            logger.warning(
                "Remote MCP tool failed once; retrying %s from %s: %s",
                self.name,
                self._space_slug,
                type(exc).__name__,
            )
            await asyncio.sleep(_REMOTE_TOOL_RETRY_DELAY_S)
            result = await self._client.call_tool(self._client_tool_name, kwargs)
        payload = dict(result)
        if payload.get("namespaced_tool_name") == self._client_tool_name:
            payload["namespaced_tool_name"] = self.name
        payload.setdefault("tool_space_slug", self._space_slug)
        return payload


_LOADED_REMOTE_TOOL_CACHE: Dict[tuple[str, str], RemoteMcpTool] = {}


def _load_module_from_file(module_name: str, file_path: Path) -> ModuleType:
    """Load a Python module from a file path."""
    if not file_path.is_file():
        raise MissingToolFileError(f"tool file not found at {file_path}")

    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if not (spec and spec.loader):
        raise ModuleNotFoundError(f"Cannot create spec for {file_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        # Avoid leaving a partially initialised module registered on failure
        sys.modules.pop(module_name, None)
        raise
    return module


def _format_error(error: Exception) -> str:
    """Format an exception for logging."""
    if isinstance(error, FileNotFoundError):
        return f"Tool file not found: {error}"
    if isinstance(error, ModuleNotFoundError):
        return f"Missing dependency: {error}"
    if isinstance(error, ImportError):
        return f"Import error: {error}"
    return f"{type(error).__name__}: {error}"


def _normalize_signature_path(value: str | Path | None) -> str | None:
    """Normalize a path-like value for registry invalidation and cache keys."""
    if value is None:
        return None
    try:
        return str(Path(value).expanduser().resolve())
    except Exception:
        return str(value)


def _tool_classes_from_module(module: ModuleType) -> List[type[Tool]]:
    """Return auto-registerable Tool classes defined directly in module."""
    tool_classes: List[type[Tool]] = []
    seen_class_ids: set[int] = set()
    for value in vars(module).values():
        if not inspect.isclass(value) or value.__module__ != module.__name__:
            continue
        try:
            is_tool_class = issubclass(value, Tool)
        except TypeError:
            continue
        if value is Tool or not is_tool_class or inspect.isabstract(value) or not value._auto_register:
            continue
        cls_id = id(value)
        if cls_id in seen_class_ids:
            continue
        seen_class_ids.add(cls_id)
        tool_classes.append(value)
    return tool_classes


def _load_cached_tool_classes(
    cache_key: tuple[str, str],
    load_module: Callable[[], ModuleType],
) -> tuple[List[type[Tool]], bool]:
    """Load tool classes once per source and return whether the cache was reused."""
    cached_classes = _LOADED_TOOL_CLASS_CACHE.get(cache_key)
    if cached_classes is not None:
        return cached_classes, True

    module = load_module()
    tool_classes = _tool_classes_from_module(module)
    _LOADED_TOOL_CLASS_CACHE[cache_key] = tool_classes
    return tool_classes, False


def _try_load_tool_classes(
    tool_name: str,
    module_path: str,
    fallback_directory: Path | None,
    file_subpath: str,
) -> tuple[str, List[type[Tool]], bool]:
    """Try to load tool classes: first via importlib, then from a configured external file."""
    try:
        return (
            "module",
            *_load_cached_tool_classes(
                ("module", module_path),
                lambda: importlib.import_module(module_path),
            ),
        )
    except ModuleNotFoundError:
        if fallback_directory is None:
            raise
        tool_file = fallback_directory / file_subpath
        return (
            "file",
            *_load_cached_tool_classes(
                ("file", _normalize_signature_path(tool_file) or str(tool_file)),
                lambda: _load_module_from_file(f"{_EXTERNAL_TOOL_MODULE_NAMESPACE}.{tool_name}", tool_file),
            ),
        )


def _build_tool_registry(
    tool_classes: List[type[Tool]],
    extra_tools: Sequence[Tool] | None = None,
) -> Dict[str, Tool]:
    """Instantiate tools and fail if duplicate Tool.name values are detected."""
    unique_classes: List[type[Tool]] = []
    seen_class_ids: set[int] = set()
    for cls in tool_classes:
        cls_id = id(cls)
        if cls_id in seen_class_ids:
            continue
        seen_class_ids.add(cls_id)
        unique_classes.append(cls)

    tool_instances: list[Tool] = []
    tool_instances.extend(cls() for cls in unique_classes)
    if extra_tools:
        tool_instances.extend(extra_tools)

    name_to_sources: Dict[str, List[str]] = {}
    for tool in tool_instances:
        source = getattr(tool, "_registry_source", f"{tool.__class__.__module__}.{tool.__class__.__name__}")
        name_to_sources.setdefault(tool.name, []).append(source)

    collisions = {tool_name: sources for tool_name, sources in name_to_sources.items() if len(sources) > 1}
    if collisions:
        details = "; ".join(f"{tool_name}: {sources}" for tool_name, sources in sorted(collisions.items()))
        raise RuntimeError(
            f"Duplicate Tool.name values detected while loading tools. Tool.name must be unique. Conflicts: {details}"
        )

    return {tool.name: tool for tool in tool_instances}


def _tool_registry_signature(instance_path: str | Path | None) -> tuple[str, str, str | None, bool, str | None]:
    """Return the runtime inputs that determine the active tool registry."""
    return (
        config.REACHY_MINI_CUSTOM_PROFILE or "default",
        _normalize_signature_path(config.PROFILES_DIRECTORY) or "",
        _normalize_signature_path(config.TOOLS_DIRECTORY),
        bool(config.AUTOLOAD_EXTERNAL_TOOLS),
        _normalize_signature_path(instance_path),
    )


# Registry & specs (dynamic)
def _read_profile_tool_names(instance_path: str | Path | None) -> list[str]:
    """Read enabled tool names from the active profile's effective toolset."""
    profile = config.REACHY_MINI_CUSTOM_PROFILE or DEFAULT_PROFILE_NAME
    logger.info("Loading tools for profile: %s", profile)
    try:
        tool_names = read_profile_tool_names(profile, instance_path)
    except (OSError, RuntimeError, ValueError) as exc:
        logger.error("Failed to read tools for profile %r: %s", profile, exc)
        raise RuntimeError(f"Failed to read tools for profile {profile!r}") from exc

    tool_names.extend(tool.value for tool in SystemTool if tool.value not in tool_names)

    if config.AUTOLOAD_EXTERNAL_TOOLS:
        discovered_external_tools = list_tool_module_names(config.TOOLS_DIRECTORY)
        extra_tools = [name for name in discovered_external_tools if name not in tool_names]
        if extra_tools:
            tool_names.extend(extra_tools)
            logger.info(
                "AUTOLOAD_EXTERNAL_TOOLS enabled: added %d external tool(s): %s",
                len(extra_tools),
                extra_tools,
            )

    logger.info("Found %d tools to load: %s", len(tool_names), tool_names)
    return tool_names


def _resolve_remote_tools(tool_names: list[str], instance_path: str | Path | None) -> list[RemoteMcpTool]:
    """Build Space tools enabled by the active profile from the cached install manifest, without any network calls."""
    remote_tools: list[RemoteMcpTool] = []
    for installed_space in read_installed_tool_spaces(instance_path).spaces:
        enabled_tool_names = {name for name in tool_names if name.startswith(f"{installed_space.alias}__")}
        if not enabled_tool_names:
            continue

        discovered_tool_names = {tool.local_name for tool in installed_space.tools}
        missing_tool_names = sorted(enabled_tool_names - discovered_tool_names)
        if missing_tool_names:
            logger.warning(
                "Tools enabled from '%s' are missing from the install manifest and will be skipped: %s. "
                "Re-run 'tool-spaces add %s' to refresh.",
                installed_space.slug,
                ", ".join(missing_tool_names),
                installed_space.slug,
            )

        client = build_remote_client(
            installed_space.alias,
            installed_space.mcp_url,
            private=installed_space.private,
            cached_tools=installed_space.tools,
        )
        for remote_tool in installed_space.tools:
            if remote_tool.local_name not in enabled_tool_names:
                continue
            cache_key = ("remote", f"{installed_space.slug}:{remote_tool.local_name}:{remote_tool.client_tool_name}")
            cached_tool = _LOADED_REMOTE_TOOL_CACHE.get(cache_key)
            if cached_tool is None:
                cached_tool = RemoteMcpTool(
                    slug=installed_space.slug,
                    name=remote_tool.local_name,
                    description=remote_tool.description,
                    parameters_schema=remote_tool.parameters_schema,
                    client_tool_name=remote_tool.client_tool_name,
                    client=client,
                )
                _LOADED_REMOTE_TOOL_CACHE[cache_key] = cached_tool
            remote_tools.append(cached_tool)

    return remote_tools


def _load_enabled_tools(tool_names: list[str], remote_tool_names: set[str]) -> List[type[Tool]]:
    """Load shared and external tools while skipping resolved remote tool IDs."""
    loaded_tool_classes: List[type[Tool]] = []

    for tool_name in tool_names:
        if tool_name in remote_tool_names:
            logger.info("✓ Registered remote tool: %s", tool_name)
            continue

        shared_module_path = f"reachy_language_tutor.tools.{tool_name}"
        try:
            source, tool_classes, reused_cache = _try_load_tool_classes(
                tool_name,
                module_path=shared_module_path,
                fallback_directory=config.TOOLS_DIRECTORY,
                file_subpath=f"{tool_name}.py",
            )
            loaded_tool_classes.extend(tool_classes)
            action = "Reused" if reused_cache else "Loaded"
            if source == "file":
                logger.info("✓ %s external tool: %s", action, tool_name)
            else:
                logger.info("✓ %s core tool: %s", action, tool_name)
        except (ModuleNotFoundError, FileNotFoundError):
            logger.warning("⚠️ Tool '%s' not found in shared or external tools", tool_name)
        except Exception as e:
            logger.error("❌ Failed to load tool '%s': %s", tool_name, _format_error(e))
            logger.error("  Module path: %s", shared_module_path)

    return loaded_tool_classes


def initialize_tools(instance_path: str | Path | None = None, *, force: bool = False) -> None:
    """Populate or refresh the active-profile tool registry.

    When ``force`` is true, file-backed tools are re-executed, while importable
    tool modules still follow normal ``importlib``/``sys.modules`` caching.
    """
    global ALL_TOOLS, _TOOLS_SIGNATURE, _TOOLS_INSTANCE_PATH

    with _TOOLS_LOCK:
        if force:
            _LOADED_TOOL_CLASS_CACHE.clear()
            _LOADED_REMOTE_TOOL_CACHE.clear()

        if instance_path is not None:
            _TOOLS_INSTANCE_PATH = instance_path
        effective_instance_path = _TOOLS_INSTANCE_PATH
        signature = _tool_registry_signature(effective_instance_path)

        if _TOOLS_SIGNATURE is not None and not force and signature == _TOOLS_SIGNATURE:
            logger.debug("Tools already initialized for active profile; skipping reinitialization.")
            return
        if _TOOLS_SIGNATURE is not None:
            logger.info("Reloading tool registry for active profile/configuration change.")

        tool_names = _read_profile_tool_names(effective_instance_path)
        remote_tools = _resolve_remote_tools(tool_names, effective_instance_path)
        remote_tool_names = {tool.name for tool in remote_tools}
        loaded_tool_classes = _load_enabled_tools(tool_names, remote_tool_names)
        tools = _build_tool_registry(
            loaded_tool_classes,
            extra_tools=remote_tools,
        )
        ALL_TOOLS = tools
        _TOOLS_SIGNATURE = signature

        for tool_name, tool in tools.items():
            logger.info("tool registered: %s - %s", tool_name, tool.description)


def get_tool_specs(exclusion_list: list[str] | None = None) -> list[ToolSpec]:
    """Get tool specs, optionally excluding some tools."""
    initialize_tools()
    exclusion_list = exclusion_list or []
    with _TOOLS_LOCK:
        return [tool.spec() for tool in ALL_TOOLS.values() if tool.name not in exclusion_list]


def log_trust_for_resolved_tool(tool: object) -> bool:
    """Decide whether THIS tool's result envelope keys may be logged -- the one rule.

    Takes the resolved Tool, never a name, and that is the entire point of D36. The
    name-based version asked the registry at dispatch while `_dispatch_tool_call`
    asked it again to get the callable, so the verdict and the tool that executed
    were two reads of a registry `initialize_tools(force=True)` can rebind between --
    reachable off-loop through `asyncio.to_thread`, so the window was wall-clock. A
    security review reproduced a learner's name rendering as a trusted envelope key
    through it. A verdict derived from the object cannot be about a different object.

    A locally-registered tool composes its own return dict here, so its top-level
    keys are this application's schema and are most of the dispatch signal an
    operator reads. A RemoteMcpTool returns dict(result) from a third-party Space, so
    those keys belong to whoever wrote it and a data-keyed envelope would print
    verbatim -- a learner's name among the possibilities, in a child's household.

    Nesting is a separate question and is never trusted: describe_for_log stops trust
    at the envelope, which is what covers a hosted backend's payload at milestone 5.

    NAMES WHAT IS PERMITTED, and that is not a stylistic preference here. While this
    rule took a name it could only ever be handed a registry value, so
    `tool is not None and not isinstance(tool, RemoteMcpTool)` was adequate. Taking
    the object exposed it as the deny-list it always was: a plain string is not None
    and not a RemoteMcpTool, so it answered YES. Caught by this task's own test, one
    line after CLAUDE.md records the same inversion costing four defects. The
    permitted set is "an instance of our Tool base that is not the remote subclass",
    so None from an unresolved name, a string, or anything that is not a Tool at all
    is refused without needing to be foreseen.

    TWO POSITIVE CONDITIONS, because one of them was not enough and review proved it.

    The first is PROVENANCE: the class must be defined under this package's own
    tools. `TOOLS_DIRECTORY` loads external tool FILES and their classes enter the
    registry as ordinary Tools, under `_EXTERNAL_TOOL_MODULE_NAMESPACE` -- so a
    file-backed proxy returning a third party's payload verbatim was trusted, which
    is reachable today by configuration rather than after some future refactor. A
    tool defined anywhere else is refused, which fails closed for a future in-tree
    module too.

    The second is the class MARKER, `log_keys_are_ours`. It lets a tool that wraps
    somebody else's service opt out without subclassing RemoteMcpTool -- which the
    subclass test this replaced could not express -- and `is True` refuses a
    subclass that sets it to something odd rather than accepting a truthy value.

    An earlier version of this docstring called the marker alone "opt-in". It is
    not: the base default is True, so a subclass that says nothing inherits trust.
    Review measured that and the claim is gone rather than the measurement.
    """
    if not isinstance(tool, Tool) or tool.log_keys_are_ours is not True:
        return False
    return type(tool).__module__.startswith(_OUR_TOOLS_NAMESPACE)


def get_tools() -> dict[str, Tool]:
    """Return a shallow snapshot of the active tool registry."""
    initialize_tools()
    with _TOOLS_LOCK:
        return dict(ALL_TOOLS)


# Dispatcher
def _safe_load_obj(args_json: str) -> Dict[str, Any]:
    try:
        parsed_args = json.loads(args_json or "{}")
        return parsed_args if isinstance(parsed_args, dict) else {}
    except Exception:
        # Shape, not text. This is the same model-composed payload the realtime layer
        # renders before logging; echoing it here would re-open that sink one layer
        # down, at WARNING, which --debug does not gate.
        logger.warning("bad args_json=%s", describe_json_for_log(args_json))
        return {}


async def _dispatch_tool_call(
    tool_name: str, args: Dict[str, Any], deps: ToolDependencies
) -> tuple[Dict[str, Any], Optional[Tool]]:
    """Run a tool and report BOTH its result and the tool object that produced it.

    The second half of that pair is D36. This function is the one place that resolves
    a name to a callable, so it is the only place that can say with certainty which
    object ran -- and the log-trust verdict has to be about that object, not about
    what the name resolved to at some other moment. Callers that do not care unwrap
    and discard it; `dispatch_tool_call` and `dispatch_tool_call_with_manager` keep
    their dict-only contracts for exactly that reason, because four test modules
    depend on them -- test_learner_tool_flow, test_converted_lessons,
    test_tool_space_runtime and the identity-boundary suite.
    """
    tool = get_tools().get(tool_name)
    if not tool:
        return {"error": f"unknown tool: {tool_name}"}, None
    try:
        return await tool(deps, **args), tool
    except asyncio.CancelledError:
        logger.info("Tool cancelled: %s", tool_name)
        return {"error": "Tool cancelled"}, tool
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        # The type and where it happened, never the message. An exception raised out
        # of a tool can carry a learner's name or their lesson result in its TEXT,
        # and this layer cannot tell which do -- but a traceback's frames are file,
        # line and function, which carry nothing a learner said. Keeping them answers
        # the triage question the type alone cannot: a tool reaches a store, an MCP
        # client and a movement manager, and "where" is most of the diagnosis. The
        # message still reaches the model, which already holds that data.
        frames = " <- ".join(
            f"{frame.filename.rsplit('/', 1)[-1]}:{frame.lineno}" for frame in traceback.extract_tb(e.__traceback__)
        )
        logger.error("Tool error in %s: %s at %s", tool_name, type(e).__name__, frames)
        return {"error": msg}, tool


async def dispatch_tool_call(tool_name: str, args_json: str, deps: ToolDependencies) -> Dict[str, Any]:
    """Dispatch a tool call by name with JSON args and dependencies."""
    result, _tool = await _dispatch_tool_call(tool_name, _safe_load_obj(args_json), deps)
    return result


async def dispatch_tool_call_with_manager(
    tool_name: str, args_json: str, deps: ToolDependencies, tool_manager: "BackgroundToolManager"
) -> Dict[str, Any]:
    """Dispatch a tool call, injecting a BackgroundToolManager into the args."""
    args = _safe_load_obj(args_json)
    args["tool_manager"] = tool_manager
    result, _tool = await _dispatch_tool_call(tool_name, args, deps)
    return result


async def dispatch_tool_call_reporting_tool(
    tool_name: str,
    args_json: str,
    deps: ToolDependencies,
    tool_manager: Optional["BackgroundToolManager"] = None,
) -> tuple[Dict[str, Any], Optional[Tool]]:
    """Dispatch a tool call and report the tool object that ran alongside its result.

    The entry the background manager uses, so it can take the log-trust verdict from
    the object rather than from the name (D36). `tool_manager` is injected when given,
    which is what `dispatch_tool_call_with_manager` does for system tools -- one
    function rather than a second reporting variant, so the two spellings of "run a
    tool" do not become four.
    """
    args = _safe_load_obj(args_json)
    if tool_manager is not None:
        args["tool_manager"] = tool_manager
    return await _dispatch_tool_call(tool_name, args, deps)
