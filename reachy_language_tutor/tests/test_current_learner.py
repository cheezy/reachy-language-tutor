"""Guard the trust anchor: who the app thinks it is serving.

CLAUDE.md: "The app sets the current learner ID from recognition. Tools must never
accept a learner's identity from the conversation, so nobody can talk their way into
another person's profile." ToolDependencies is what makes that enforceable -- the LLM
is shown a tool's parameters_schema and never the dependencies behind it.

Enforcement is in two layers now, and it is worth being precise about where each stops.
At runtime, ToolDependencies seals current_learner_id at the end of __init__. Assignment
and deletion are both refused -- deps.current_learner_id = ..., setattr(deps, FIELD, ...),
del deps.current_learner_id, and the same three against the seal flag and __class__ all
raise CurrentLearnerIsReadOnlyError instead of taking effect, from anywhere in the process --
including the three modules that hold a deps reference for a whole session,
conversation_handler.py, huggingface_realtime.py and tools/background_tool_manager.py.
Construction is untouched, an unset field is as sealed as a populated one, and every
other field stays mutable: run() still assigns deps.go_to_sleep after startup.

The seal protects its own machinery, which is the part that is easy to get wrong: the
allow-list names ITSELF, and is read from the class rather than the instance, so an
instance shadow of it is refused rather than quietly answering for what is protected.
Without that the guard sat one ordinary assignment away from switching itself off.

The allow-list also names __dict__ and __class__, for a reason worth stating: replacing
either is an ordinary attribute write the hooks SEE, and permitting it would not merely
be bypassed -- a replacement dict carries no seal flag, so the guard would switch itself
off for every write afterwards.

What the seal cannot reach is anything that gets to the instance dict WITHOUT going
through those two hooks: object.__setattr__(deps, FIELD, ...) and its exact twin
object.__delattr__, deps.__dict__[FIELD] = ..., vars(deps)[FIELD] = ..., any mutating
call on that dict, and deleting from it. A test below pins that a __dict__ write really
does land, so this paragraph cannot quietly outrun the code. Rebinding the machinery on
the CLASS (_SEALED_ATTRIBUTES, __setattr__, __delattr__, __new__, __init__,
__getattribute__) disarms it for every instance at once, which an instance hook cannot
refuse because the write never touches an instance. __new__ leads that list because it
is the earliest link in the install chain and suppresses every link after it: a __new__
returning a non-instance means CPython never calls __init__, so __post_init__ never
runs and the seal is never armed -- and it is the constructor in main.py, not an
attacker, that then hands the unsealed object to every tool.

The install chain has ONE OWNER PER LINK, named here so the list can be checked rather
than trusted. This is deliberately not a claim that the chain is closed -- an earlier
draft said that, and it was wrong by one link. Reading outward from the seal:
object.__setattr__ (immutable at the C level: `object` refuses attribute assignment),
the global `object` it is reached through (flagged), __post_init__ (flagged), __init__
(flagged), __new__ (flagged), the metaclass __call__, reachable only by rebinding
__class__ (flagged) -- and earlier than all of them, the NAME the construction site
resolves. build_tool_dependencies imports ToolDependencies from core_tools INSIDE the
function, so core_tools.ToolDependencies is itself a link, of the same module-global
family as `object` in the bullet below: rebind it and main.py builds an imposter, after
which no later link runs at all. It is flagged, but flagging is not closure.

That is where this chain bottoms out, and it bottoms out in DISCLOSURE rather than
coverage, for a reason that generalises: Python has no unrebindable module global, so
the last link of any chain like this one is always a name in somebody's namespace. Each
element above is therefore in one of three buckets -- flagged, immutable in CPython, or
a module global -- and the third cannot be closed, only stated. Whoever can reach into
it already has object.__setattr__(deps, FIELD, ...) outright, which is why the whole
family is low rather than a live hole.

Two scoping notes, so this is not read as more than it is. It describes the path
build_tool_dependencies takes, NOT every instance in the process: ToolDependencies
.__new__(ToolDependencies) mints an unsealed, field-less object, which matters only if
something then hands it to the session holders -- the REPLACING bullet below, not a new
route. copy.copy(deps) is safe by contrast, because _identity_sealed lives in the
instance dict and the copy therefore arrives sealed.

The refusal path's own reads are covered the same way: _identity_sealed and
__getattribute__ and __dict__ are listed, and so are CurrentLearnerIsReadOnlyError and
super. __class__._SEALED_ATTRIBUTES deserves a precise word, because the obvious reason
is the wrong one: the implicit closure cell IS writable -- cell_contents has been
settable since 3.7 -- so "not a rebindable name" would be false. What closes it is the
zero-arg super() on the last line of the same method, which resolves against that SAME
cell: super(X, self) requires isinstance(self, X), so any X whose _SEALED_ATTRIBUTES is
empty enough to pass the check then raises TypeError instead of performing the write.
Swapping the cell turns a refusal into a different refusal. Verified by doing it.
__getattr__ is deliberately absent: it fires only when normal lookup FAILS, which for
_identity_sealed requires deleting it from the class first, and the delete arm already
flags that.

The source guard below covers those, in the spellings it can see, and it now reads the
whole application package rather than the tools package alone. It is receiver-based
rather than spelling-based: any mutating call whose receiver OR argument is an instance
dict is flagged, whatever it carries, because enumerating spellings is a game the reader
of this file should not have to win.

What escapes BOTH layers, stated so nobody has to discover it:
  - An ALIAS. `borrowed = deps.__dict__` then `borrowed.update(...)` writes the same
    dict, and the scan cannot follow it -- the receiver is an ordinary name by then,
    and a source rule has no dataflow. Binding an instance dict is not itself flagged,
    because console.py legitimately does `vars(self.handler)` to read one.
  - A write whose attribute name or dict key is computed at runtime.
  - A binding context the scan does not enumerate. It covers assignment, augmented and
    annotated assignment, for, async for, with, comprehensions and delete -- which is
    every one Python has today, but it IS an enumeration, and the last two were added
    only after a review found them missing.
  - A subclass. Overriding __post_init__ without calling super() means the seal is
    never installed; overriding __setattr__ or emptying _SEALED_ATTRIBUTES in the class
    body disarms it just as well. The scan flags all three when the base is spelled
    ToolDependencies AND the override is a direct class-body statement -- not when the
    subclass reaches the base through an alias, nor when the override is nested inside
    an `if` in the body.
  - REBINDING A MODULE GLOBAL THE SEAL ITSELF CONSULTS. This is a class of disarm
    distinct from the class-attribute rebinds above, because it never touches the class
    OR the instance. __post_init__ installs the flag through `object.__setattr__`, and
    `object` is an ordinary global of core_tools: rebind it to a stand-in whose
    __setattr__ does nothing and the flag is never written, the class-level False
    answers every later check, and the field is writable for the life of the process --
    not bypassed, never armed. CurrentLearnerIsReadOnlyError is the same shape on the
    refusal path: a stand-in whose __init__ walks sys._getframe(1).f_locals performs the
    write it was raised to prevent, and the caller can swallow it. The scan flags both
    spellings, so this bullet is the honest half rather than the whole story: an
    attacker who can rebind a core_tools global can already call
    object.__setattr__(deps, FIELD, ...) outright, so no new power is gained. It is
    unreachable today only by ORDERING -- main.py builds the dependencies before any
    file-backed tool module is exec'd -- and that ordering is incidental, not enforced.
    Milestone 4's recognition flow is exactly the shape that would invert it.
    Early-binding `object.__setattr__` as a __post_init__ default would move the
    dependency rather than remove it: __defaults__ is rebindable too, and unlike the
    global it is NOT something the scan sees.
  - REPLACING the dependencies rather than writing them -- a module holding a deps
    reference for the session can assign a freshly built ToolDependencies over it and
    repoint every later turn. The seal permits that by design; its own error message
    says so. Not reachable from a tool today, because tools get deps and never the
    holder -- but it is the shape milestone 4's recognition path will want, and it is
    the thing to fence when that lands.
  - Anything outside src/reachy_language_tutor/ -- an external file-backed tool, a
    remote MCP server -- which the scan never reads.

The scan is deliberately object-blind: it flags a mutating write to ANY instance dict,
not only to a deps one, because narrowing it to a receiver spelled `deps` would buy a
cosmetic false-positive fix and sell a real bypass. Two shapes therefore fire on code
that is innocent in isolation -- a dict legitimately keyed "current_learner_id", and
any `__dict__` mutation at all. Both are rare enough to be worth a conversation rather
than a looser rule.

Neither layer sees a tool that takes an identity under an unrelated spelling ("who",
"person") and passes it to a reader. That is what test_tool_identity_boundary.py
attacks, and what the positive sourcing assertion there is the static counterpart to.
"""

import ast
import logging
import traceback
import pkgutil
import importlib
import dataclasses
from typing import Any
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from reachy_language_tutor import main, tools, learners
from reachy_language_tutor.learners import store
from reachy_language_tutor.tools.core_tools import ToolDependencies


FIELD = "current_learner_id"
# Every field the seal names, derived from the seal rather than hand-listed. The scan
# below protects all of them, so a field added to _SEALED_ATTRIBUTES inherits the whole
# rule -- the exotic spellings included -- instead of getting whichever half its author
# remembered. W14 added lesson_session and a security review found the asymmetry:
# object.__setattr__(bundle, "lesson_session", ...) went around the runtime seal AND
# past a scan that only knew one name, while the identical shape for the learner id was
# caught. That is the sibling-parity defect CLAUDE.md records as the most repeated one
# on this board, so the fix is the inversion rather than a second name in a second list.
SEALED_FIELDS = frozenset(
    f.name for f in dataclasses.fields(ToolDependencies) if f.name in ToolDependencies._SEALED_ATTRIBUTES
)
# The machinery the seal is made of. Rebinding any of these on the CLASS turns the seal
# off for every instance at once, which __setattr__ cannot refuse because it governs
# instances rather than the class object.
SEAL_MACHINERY = frozenset(
    {
        "_SEALED_ATTRIBUTES",
        "_identity_sealed",
        "__setattr__",
        "__post_init__",
        # __new__ is FIRST because it is the earliest link: type.__call__ calls
        # cls.__init__ only if cls.__new__ returned an instance of cls, so a __new__
        # handing back something else suppresses __init__, __post_init__ and the seal
        # install in one move -- and main.py's own legitimate ToolDependencies(...)
        # then returns the unsealed object that every tool holds for the session.
        # __init__ rebound means __post_init__ never runs and nothing is ever sealed;
        # __getattribute__ rebound makes the seal's own predicate read False.
        "__new__",
        "__init__",
        "__getattribute__",
        "__class__",
        # The deletion half of the seal. Disarming it alone is sufficient: del the flag
        # becomes permitted, and the assignment half unlocks with it.
        "__delattr__",
        # Replacing or deleting the instance dict takes the seal flag with it.
        "__dict__",
        # Module globals the guard must never consult. `type` was one until the hooks
        # moved to the __class__ closure cell; flagging it keeps that from regressing.
        "type",
        "super",
        # `object` is the one the seal still DOES consult: __post_init__ installs the
        # flag through object.__setattr__, so rebinding it to a no-op means the seal is
        # never armed rather than bypassed -- every later write is then ordinary and
        # permitted. The exception class is the same family on the refusal path: a
        # stand-in whose __init__ walks the caller's frame performs the write it was
        # raised to prevent. Both are low: rebinding a core_tools global already grants
        # object.__setattr__(deps, ...) directly. They are flagged because the scan was
        # naming the two globals the guard stopped consulting and omitting the one it
        # does, which is a dishonest list rather than a short one.
        "object",
        "CurrentLearnerIsReadOnlyError",
        # The construction site's own name, and the EARLIEST link of all. main.py's
        # build_tool_dependencies imports ToolDependencies inside the function body, so
        # every construction resolves core_tools.ToolDependencies at call time: rebind
        # it and main.py builds an imposter, after which no link in the chain runs.
        "ToolDependencies",
    }
)
# Methods that mutate a mapping in place. Reaching the instance dict and calling one of
# these is the same write as a subscript, in a spelling a per-shape rule keeps missing.
MUTATING_METHODS = frozenset(
    {
        "update",
        "setdefault",
        "pop",
        "popitem",
        "clear",
        "__setitem__",
        # The dunder spellings of the same writes. __init__ on a live dict re-populates
        # it; __ior__ is |=; __setstate__ restores one wholesale.
        "__delitem__",
        "__ior__",
        "__init__",
        "__setstate__",
    }
)
# The same writes spelled as plain functions, where the dict is an ARGUMENT rather than
# the receiver: dict.update(deps.__dict__, ...), operator.setitem(vars(deps), ...).
MUTATING_FUNCTIONS = MUTATING_METHODS | {"setitem", "delitem", "setattr", "delattr", "__setattr__", "__delitem__"}
IDENTITY_PARAMETERS = {"user_id", "profile_id", "person_id"}
SEEDED_LEARNER = store.SEED_LEARNERS[0][0]


@pytest.fixture
def instance(tmp_path: Path) -> Path:
    """A prepared learner database at a temporary instance path."""
    assert store.ensure_learner_database(tmp_path).ready is True
    return tmp_path


def _deps(**overrides: Any) -> ToolDependencies:
    """Dependencies with the two required fields stubbed, as the existing tests do."""
    return ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock(), **overrides)


def _tool_module_paths() -> list[Path]:
    """Every module in the tools package, as source files to parse.

    iter_modules does not enumerate __init__.py, so it is added explicitly -- it is
    inside the package and could write the field like any other module there.
    """
    paths = [Path(module.module_finder.path) / f"{module.name}.py" for module in pkgutil.iter_modules(tools.__path__)]
    paths += [Path(root) / "__init__.py" for root in tools.__path__]
    assert paths, "found no tool modules to check; the guard would pass vacuously"
    return paths


# --- The field exists and means what it says ---------------------------------------


def test_tool_dependencies_carries_the_current_learner_id() -> None:
    """Tools need somewhere trustworthy to read identity from; this is that place."""
    fields = {f.name: f for f in dataclasses.fields(ToolDependencies)}
    assert FIELD in fields
    assert fields[FIELD].type == str | None
    assert fields[FIELD].default is None


def test_the_field_is_unset_by_default() -> None:
    """Unset must mean nobody, never a real learner -- a wrong identity is the harm."""
    assert _deps().current_learner_id is None


# --- The write is refused at runtime, not merely noticed later -------------------------


def _refusal(deps: ToolDependencies, write: Any) -> Any:
    """Run a write that must be refused, and return the exception it raised.

    Asserts on the exception's class NAME rather than importing the class: another test
    module purges and re-imports the tools package, which can leave a module-level class
    reference bound to a dead object and make an isinstance check quietly wrong.
    """
    with pytest.raises(AttributeError) as excinfo:
        write()
    assert type(excinfo.value).__name__ == "CurrentLearnerIsReadOnlyError"
    return excinfo.value


def test_the_identity_cannot_be_reassigned_after_construction() -> None:
    """A test that notices a write afterwards is not the same as a write that fails."""
    deps = _deps(current_learner_id=SEEDED_LEARNER)

    _refusal(deps, lambda: setattr(deps, FIELD, "somebody-else"))

    assert deps.current_learner_id == SEEDED_LEARNER


def test_an_unset_identity_is_as_sealed_as_a_populated_one() -> None:
    """Serving nobody is a real startup outcome, so None must not be a writable hole."""
    deps = _deps()
    assert deps.current_learner_id is None

    _refusal(deps, lambda: setattr(deps, FIELD, "somebody-else"))

    assert deps.current_learner_id is None


def test_the_seal_itself_cannot_be_lifted_through_the_attribute_protocol() -> None:
    """Otherwise the guard is one assignment away from being turned off."""
    deps = _deps(current_learner_id=SEEDED_LEARNER)

    _refusal(deps, lambda: setattr(deps, "_identity_sealed", False))

    assert deps.current_learner_id == SEEDED_LEARNER


def test_the_identity_cannot_be_deleted_either() -> None:
    """Deleting the field would reset the trust anchor while the seal still read True."""
    deps = _deps(current_learner_id=SEEDED_LEARNER)

    _refusal(deps, lambda: delattr(deps, FIELD))

    assert deps.current_learner_id == SEEDED_LEARNER


def test_the_seal_cannot_be_deleted_to_unlock_the_field() -> None:
    """The sharpest version: delete the flag and an ordinary assignment lands next line.

    Deletion is the same attribute protocol as assignment, so refusing one without the
    other left the whole seal one `del` away from being lifted.
    """
    deps = _deps(current_learner_id=SEEDED_LEARNER)

    _refusal(deps, lambda: delattr(deps, "_identity_sealed"))
    _refusal(deps, lambda: setattr(deps, FIELD, "somebody-else"))

    assert deps.current_learner_id == SEEDED_LEARNER


def test_the_instance_cannot_be_reclassed_out_of_its_seal() -> None:
    """Reclassing swaps in a different __setattr__ on the object everyone is holding."""

    class Unsealed(ToolDependencies):
        pass

    deps = _deps(current_learner_id=SEEDED_LEARNER)

    _refusal(deps, lambda: setattr(deps, "__class__", Unsealed))

    assert deps.current_learner_id == SEEDED_LEARNER


def test_an_unsealed_field_can_still_be_deleted() -> None:
    """Only the identity and its machinery are sealed; the rest behaves normally."""
    deps = _deps(current_learner_id=SEEDED_LEARNER)
    deps.go_to_sleep = MagicMock()

    del deps.go_to_sleep

    assert deps.go_to_sleep is None


def test_the_allow_list_cannot_be_shadowed_to_unlock_the_field() -> None:
    """The sharpest bypass of all: the guard was one assignment from switching itself off.

    __setattr__ decides what is protected by consulting _SEALED_ATTRIBUTES. Until that
    name was in the set it guards, shadowing it on the instance was an ordinary write
    the hook permitted -- and the very next line wrote the field.
    """
    deps = _deps(current_learner_id=SEEDED_LEARNER)

    _refusal(deps, lambda: setattr(deps, "_SEALED_ATTRIBUTES", frozenset()))
    _refusal(deps, lambda: delattr(deps, "_SEALED_ATTRIBUTES"))
    _refusal(deps, lambda: setattr(deps, FIELD, "somebody-else"))

    assert deps.current_learner_id == SEEDED_LEARNER


def test_the_instance_dict_cannot_be_replaced_wholesale() -> None:
    """The sharpest shape: a write the hook SEES, which would take the seal with it.

    `deps.__dict__ = {...}` is an ordinary attribute assignment, so __setattr__ runs --
    and the replacement carries no seal flag, so the class-level False takes over and
    every later write is permitted too. Not a bypass of the guard; a write the guard
    had to be told to refuse.
    """
    deps = _deps(current_learner_id=SEEDED_LEARNER)

    _refusal(deps, lambda: setattr(deps, "__dict__", {FIELD: "somebody-else"}))
    _refusal(deps, lambda: delattr(deps, "__dict__"))
    _refusal(deps, lambda: setattr(deps, FIELD, "somebody-else"))

    assert deps.current_learner_id == SEEDED_LEARNER


def test_the_refusal_names_no_learner() -> None:
    """The dispatcher puts a tool's exception text into the dict it returns to the model.

    So this message reaches the LLM. It explains the invariant; it must not identify
    anybody while doing so.
    """
    deps = _deps(current_learner_id=SEEDED_LEARNER)

    message = str(_refusal(deps, lambda: setattr(deps, FIELD, "somebody-else")))

    assert SEEDED_LEARNER not in message
    assert "somebody-else" not in message
    assert "fixed when the dependencies are built" in message


def test_the_write_scan_covers_every_name_the_seal_protects() -> None:
    """The union of the two lists must account for the whole seal, with none left over.

    SEALED_FIELDS is derived from dataclasses.fields, so a future entry in
    _SEALED_ATTRIBUTES that is NOT a field -- a ClassVar, a property -- would fall into
    neither list and its writes would be missed in silence. That is the same asymmetry
    the derivation was introduced to fix, one level up: the derivation closed the
    family it could see, and this asserts there is nothing it cannot see.

    Stated as a covering rule rather than an equality, because SEAL_MACHINERY
    deliberately names more than the seal does -- __new__, __init__, __getattribute__
    and the module globals a guard must not consult are all shapes the scan flags
    without _SEALED_ATTRIBUTES listing them.
    """
    uncovered = set(ToolDependencies._SEALED_ATTRIBUTES) - SEALED_FIELDS - SEAL_MACHINERY

    assert uncovered == set(), uncovered


def test_every_other_field_is_still_writable_after_construction() -> None:
    """Only what the seal names is sealed. Derived from it, not hand-listed.

    run() assigns deps.go_to_sleep well after startup has built the dependencies, so a
    blanket freeze would break the app at the point it tries to become interruptible.

    The exception list is read from _SEALED_ATTRIBUTES rather than naming FIELD, so a
    field added to the seal is excused here automatically and this test keeps saying
    what it means -- "everything the seal does not name stays writable" -- instead of
    quietly becoming a second, staler copy of the seal's contents. W14 added
    lesson_session to that set, which is what made the difference visible.
    """
    deps = _deps(current_learner_id=SEEDED_LEARNER)

    for field in dataclasses.fields(ToolDependencies):
        if field.name in ToolDependencies._SEALED_ATTRIBUTES:
            continue
        setattr(deps, field.name, getattr(deps, field.name))

    sentinel = MagicMock()
    deps.go_to_sleep = sentinel
    assert deps.go_to_sleep is sentinel


def test_a_write_around_the_attribute_protocol_still_lands() -> None:
    """The honest boundary of the seal, pinned so the docstring cannot outrun it.

    __setattr__ is what the seal hooks, so anything that does not go through it still
    writes the field. This is not a defect to fix here -- it is the reason the source
    guard below exists and scans the whole application package.
    """
    deps = _deps(current_learner_id=SEEDED_LEARNER)

    deps.__dict__[FIELD] = "somebody-else"

    assert deps.current_learner_id == "somebody-else"


# --- Startup populates it ------------------------------------------------------------


def test_resolving_the_current_learner_returns_the_seeded_learner(instance: Path) -> None:
    """The hard-coded learner resolves against a prepared database."""
    resolved = main.resolve_current_learner_id(instance, logging.getLogger(__name__))
    assert resolved == main.HARDCODED_CURRENT_LEARNER_ID


def test_app_startup_builds_dependencies_carrying_a_valid_seeded_learner(instance: Path) -> None:
    """The wiring end to end: what startup builds names a learner that really exists."""
    from reachy_language_tutor.learners import get_profile

    deps = main.build_tool_dependencies(
        robot=MagicMock(),
        movement_manager=MagicMock(),
        instance_path=instance,
        camera_enabled=False,
        logger=logging.getLogger(__name__),
    )
    assert deps.current_learner_id == store.SEED_LEARNERS[0][0]
    # Populated is not enough; it has to be somebody.
    assert get_profile(deps.current_learner_id, instance_path=instance) is not None


# --- Nothing in the tools package writes it ------------------------------------------


def _application_package_paths() -> list[Path]:
    """Every source file in the application package, tools and everything else alike.

    Rooted at the package, never the repo: tests/ contains deliberately broken tools
    that write the field on purpose, and a repo-rooted scan would fail on them.
    """
    paths = sorted(Path(main.__file__).resolve().parent.rglob("*.py"))
    assert paths, "found no application modules to check; the guard would pass vacuously"
    return paths


def _is_instance_dict(node: ast.expr | None) -> bool:
    """Report whether an expression IS an instance dict, however it was reached.

    Object-blind on purpose. Narrowing this to a receiver spelled `deps` would buy a
    cosmetic false-positive fix and sell a real bypass, because `d = deps.__dict__`
    then `d.update(...)` reaches the same dict under another name.
    """
    if isinstance(node, ast.Attribute) and node.attr == "__dict__":
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id == "vars":
            return True
        # getattr(deps, "__dict__") is the same object under a third spelling.
        if node.func.id == "getattr" and len(node.args) > 1:
            key = node.args[1]
            return isinstance(key, ast.Constant) and key.value == "__dict__"
    return False


def _flatten(targets: list[ast.expr]) -> list[ast.expr]:
    """Expand tuple, list and starred assignment targets into the things they bind.

    `deps.__dict__["current_learner_id"], _ = attacker, None` is an ast.Tuple, which
    matches neither arm below unless it is unpacked first.
    """
    flat: list[ast.expr] = []
    for target in targets:
        if isinstance(target, (ast.Tuple, ast.List)):
            flat += _flatten(list(target.elts))
        elif isinstance(target, ast.Starred):
            flat += _flatten([target.value])
        else:
            flat.append(target)
    return flat


def _field_write_offenders(source: str, label: str) -> list[str]:
    """Return every write to a sealed field this rule can see in one module.

    Covers the shapes the runtime seal cannot: the seal hooks __setattr__, so anything
    that goes around the attribute protocol -- an instance-dict subscript, vars(), an
    explicit object.__setattr__, a dict update -- lands at runtime and can only be
    caught here.

    "Can see" is the honest verb. A key computed at runtime, or a dict built elsewhere
    and passed to update(), is invisible to any source rule; the module docstring says
    so rather than letting this helper's name imply otherwise.
    """
    offenders: list[str] = []
    for node in ast.walk(ast.parse(source)):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = _flatten(list(node.targets))
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign, ast.For, ast.AsyncFor, ast.comprehension)):
            # `for deps.__dict__["x"] in [...]` binds through a loop target, and a
            # comprehension carries the same .target one node further out -- both are
            # ordinary writes an Assign-only enumeration never sees. Comprehension
            # targets leak to the enclosing scope in Python 3, so the write is durable.
            targets = _flatten([node.target])
        elif isinstance(node, ast.withitem):
            # `with cm() as deps.current_learner_id:` binds too.
            targets = _flatten([node.optional_vars]) if node.optional_vars else []
        elif isinstance(node, ast.ClassDef) and any(
            (isinstance(base, ast.Name) and base.id == "ToolDependencies")
            or (isinstance(base, ast.Attribute) and base.attr == "ToolDependencies")
            for base in node.bases
        ):
            # A subclass disarms by DEFINING, not by assigning: overriding __setattr__
            # or __post_init__, or emptying _SEALED_ATTRIBUTES in the class body. None
            # of those is an attribute write, so no rule above would see them.
            for statement in node.body:
                defined = ""
                if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    defined = statement.name
                elif isinstance(statement, (ast.Assign, ast.AnnAssign)):
                    for bound in _flatten(list(getattr(statement, "targets", [])) or [statement.target]):
                        if isinstance(bound, ast.Name) and bound.id in SEAL_MACHINERY:
                            defined = bound.id
                if defined in SEAL_MACHINERY:
                    offenders.append(
                        f"{label} line {statement.lineno}: subclass redefines {defined}, disarming the seal"
                    )
            continue
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("exec", "eval"):
            # Opaque to any source rule: the name is a literal, but the parse is
            # deferred. There are none in the package, so this costs nothing.
            offenders.append(f"{label} line {node.lineno}: {node.func.id}() hides its writes from this scan")
            continue
        elif isinstance(node, ast.Delete):
            for target in _flatten(list(node.targets)):
                if isinstance(target, ast.Attribute) and (
                    target.attr in SEAL_MACHINERY or target.attr in SEALED_FIELDS
                ):
                    offenders.append(f"{label} line {target.lineno}: deletes {target.attr}, disarming the seal")
                elif isinstance(target, ast.Subscript) and _is_instance_dict(target.value):
                    # del deps.__dict__["_identity_sealed"] unseals through the dict.
                    offenders.append(f"{label} line {target.lineno}: deletes from an instance dict")
            continue
        elif isinstance(node, ast.Call):
            # A computed attribute name defeats a constant check, so flag every setattr
            # on deps and let a legitimate future use be reviewed.
            first = node.args[0] if node.args else None
            named = node.args[1] if len(node.args) > 1 else None
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            if name in ("setattr", "__setattr__", "delattr", "__delattr__"):
                sealed_name = isinstance(named, ast.Constant) and (
                    named.value in SEALED_FIELDS or named.value in SEAL_MACHINERY
                )
                installs_the_seal = (
                    isinstance(named, ast.Constant)
                    and named.value == "_identity_sealed"
                    and len(node.args) > 2
                    and isinstance(node.args[2], ast.Constant)
                    and node.args[2].value is True
                )
                if isinstance(first, ast.Name) and first.id == "self" and (not sealed_name or installs_the_seal):
                    # Writing to your own instance is legitimate in general -- a frozen
                    # dataclass elsewhere does it -- but NOT for the identity or the
                    # machinery, or the exemption becomes the bypass. The seal's own
                    # install is carved out precisely: that one name, set to True.
                    continue
                if isinstance(first, ast.Name) and first.id == "deps":
                    offenders.append(f"{label} line {node.lineno}: {name} on deps")
                elif isinstance(named, ast.Constant) and named.value in SEALED_FIELDS:
                    offenders.append(f"{label} line {node.lineno}: {name} of {named.value}")
                elif isinstance(named, ast.Constant) and named.value in SEAL_MACHINERY:
                    offenders.append(f"{label} line {node.lineno}: {name} of {named.value}, disarming the seal")
            elif name in MUTATING_FUNCTIONS and any(_is_instance_dict(argument) for argument in node.args):
                # The dict passed in rather than called on: dict.update(deps.__dict__,
                # ...), operator.setitem(vars(deps), ...). Gated on the function name so
                # a plain read like logger.debug(vars(obj)) stays innocent.
                offenders.append(f"{label} line {node.lineno}: mutates an instance dict through {name}()")
            elif name in MUTATING_METHODS and _is_instance_dict(getattr(node.func, "value", None)):
                # Any mutating call on an instance dict, whatever it carries: the
                # argument spellings are endless (a dict literal, keywords, a list of
                # pairs, a variable), so the receiver is what gets flagged.
                offenders.append(f"{label} line {node.lineno}: mutates an instance dict through {name}()")
            continue
        for target in targets:
            if isinstance(target, ast.Attribute) and target.attr in SEALED_FIELDS:
                offenders.append(f"{label} line {target.lineno}: assigns {target.attr}")
            elif _is_instance_dict(target) or _is_instance_dict(getattr(target, "value", None)):
                # deps.__dict__ |= {...}, deps.__dict__[...] = ..., vars(deps)[...] = ...
                offenders.append(f"{label} line {target.lineno}: writes an instance dict, going around the seal")
            elif isinstance(target, ast.Attribute) and target.attr in SEAL_MACHINERY:
                # Rebinding the machinery on the class disarms the seal process-wide,
                # and __setattr__ cannot refuse it: it governs instances, not the class.
                offenders.append(f"{label} line {target.lineno}: rebinds {target.attr}, disarming the seal")
            # deps.__dict__["current_learner_id"] = ... and vars(deps)[...] = ...
            elif isinstance(target, ast.Subscript):
                key = target.slice
                if isinstance(key, ast.Constant) and key.value in SEALED_FIELDS:
                    offenders.append(f"{label} line {target.lineno}: writes {key.value} through a subscript")
    return offenders


def test_no_module_in_the_application_package_writes_the_current_learner() -> None:
    """Widened past the tools package: three other modules hold a deps reference.

    conversation_handler.py, huggingface_realtime.py and background_tool_manager.py all
    hold one for the life of a session, and none of them was in scope before. main.py's
    legitimate write is a constructor keyword, which is a Call argument rather than an
    assignment target, so it needs no exclusion -- the rule's shape already only fires
    on writes after the object exists.
    """
    offenders: list[str] = []
    for path in _application_package_paths():
        offenders += _field_write_offenders(path.read_text(encoding="utf-8"), path.name)

    assert not offenders, "the application package must never write the current learner: " + "; ".join(offenders)


def test_the_widened_scan_reaches_past_the_tools_package() -> None:
    """The modules that hold a deps reference must actually be in scope, not assumed."""
    scanned = {path.name for path in _application_package_paths()}

    for expected in ("main.py", "conversation_handler.py", "huggingface_realtime.py", "background_tool_manager.py"):
        assert expected in scanned, f"{expected} is not scanned, so a write there would go unnoticed"


def test_the_write_rule_recognises_every_shape_it_claims_to() -> None:
    """The rule's own self-test: each violating shape flags, each legitimate one does not."""
    violations = (
        "deps.current_learner_id = x",
        "deps.current_learner_id += x",
        "deps.current_learner_id: str = x",
        'setattr(deps, "current_learner_id", x)',
        "setattr(deps, name, x)",
        'object.__setattr__(deps, "current_learner_id", x)',
        'deps.__dict__["current_learner_id"] = x',
        'vars(deps)["current_learner_id"] = x',
        'deps.__dict__.update({"current_learner_id": x})',
        'vars(deps).update({"current_learner_id": x})',
        'deps.__dict__["current_learner_id"], _ = attacker, None',
        "ToolDependencies._SEALED_ATTRIBUTES = frozenset()",
        "ToolDependencies.__setattr__ = object.__setattr__",
        "del ToolDependencies.__setattr__",
        "ToolDependencies._identity_sealed = False",
        # The builtin-function spellings of the same two routes. These take a different
        # AST path from the statement forms, which is exactly how they were missed.
        'setattr(ToolDependencies, "_SEALED_ATTRIBUTES", frozenset())',
        'setattr(ToolDependencies, "__setattr__", object.__setattr__)',
        'delattr(ToolDependencies, "__setattr__")',
        "deps.__dict__.update(current_learner_id=x)",
        "vars(deps).update(current_learner_id=x)",
        'deps.__dict__ |= {"current_learner_id": x}',
        'deps.__dict__.update([("current_learner_id", x)])',
        'deps.__dict__ = {"current_learner_id": x}',
        "deps.__dict__.clear()",
        # Reaching into an instance dict to write ANYTHING is the bypass shape, so the
        # field it carries is not what decides this.
        'deps.__dict__.update({"instance_path": x})',
        # The argument-position spellings, where the dict is passed rather than called on.
        'dict.update(deps.__dict__, {"current_learner_id": x})',
        'operator.setitem(deps.__dict__, "current_learner_id", x)',
        'dict.__setitem__(vars(deps), "current_learner_id", x)',
        'getattr(deps, "__dict__").update(current_learner_id=x)',
        # The self-write spellings the receiver-only exemption used to let through.
        'object.__setattr__(self, "current_learner_id", attacker)',
        'object.__setattr__(self, "_identity_sealed", False)',
        # Reclassing, and the class-level rebinds that stop the seal ever installing.
        "deps.__class__ = Unsealed",
        "ToolDependencies.__new__ = _unsealed_new",
        "class Sub(ToolDependencies):\n    def __new__(cls, *a, **k):\n        return object()",
        "ToolDependencies.__init__ = _noseal_init",
        "ToolDependencies.__getattribute__ = _lying_getattribute",
        # A subclass disarms by defining rather than assigning.
        "class Sub(ToolDependencies):\n    def __setattr__(self, name, value):\n        pass",
        "class Sub(ToolDependencies):\n    _SEALED_ATTRIBUTES = frozenset()",
        'exec("deps.current_learner_id = attacker")',
        # In-place mutators that are not spelled like writes.
        'vars(deps).__ior__({"current_learner_id": x})',
        "dict.__init__(vars(deps), current_learner_id=x)",
        'vars(deps).__delitem__("current_learner_id")',
        'del deps.__dict__["_identity_sealed"]',
        'del vars(deps)["current_learner_id"]',
        "del deps.current_learner_id",
        # The deletion twins of spellings already pinned on the assignment side.
        'object.__delattr__(deps, "_identity_sealed")',
        'object.__delattr__(deps, "current_learner_id")',
        "ToolDependencies.__delattr__ = object.__delattr__",
        "del ToolDependencies.__delattr__",
        "class Sub(ToolDependencies):\n    def __delattr__(self, name):\n        pass",
        # The allow-list shadow: the hook permitted this until it named itself.
        "deps._SEALED_ATTRIBUTES = frozenset()",
        # Replacing the whole instance dict: a write the hook SEES, and which would
        # take the seal flag with it.
        'deps.__dict__ = {"current_learner_id": x}',
        "del deps.__dict__",
        # Binding contexts other than assignment.
        'for deps.__dict__["current_learner_id"] in [x]:\n    pass',
        "with ctx() as deps.current_learner_id:\n    pass",
        '[0 for deps.__dict__["current_learner_id"] in [x]]',
        "[0 for deps.current_learner_id in [x]]",
        '{k: 0 for deps.__dict__["current_learner_id"] in [x]}',
        # The guard must consult nothing an attacker can rebind.
        "core_tools.type = _fake",
        # ...and the seal's INSTALL consults one, which is a disarm rather than a bypass.
        "core_tools.object = _fake",
        "core_tools.ToolDependencies = _imposter",
        "core_tools.CurrentLearnerIsReadOnlyError = _frame_walking_writer",
    )
    for source in violations:
        assert _field_write_offenders(source, "probe"), f"this write was not flagged: {source}"

    legitimate = (
        "ToolDependencies(current_learner_id=resolve(instance_path, logger))",
        "learner_id = deps.current_learner_id",
        'return {"current_learner_id": learner_id}',
        "deps.go_to_sleep = go_to_sleep_and_stop_app",
        "handler_state = vars(self.handler)",
        "for value in vars(module).values():\n    pass",
        # The seal's own install, and a frozen dataclass elsewhere writing its own fields.
        'object.__setattr__(self, "_identity_sealed", True)',
        'object.__setattr__(self, "alias", alias)',
        "logger.debug(vars(obj))",
    )
    for source in legitimate:
        assert not _field_write_offenders(source, "probe"), f"this was flagged and should not be: {source}"


def test_no_tool_accepts_a_learner_identity_parameter() -> None:
    """Identity must arrive from app state, not from something the LLM can fill in."""
    offenders: list[str] = []
    for module in pkgutil.iter_modules(tools.__path__):
        loaded = importlib.import_module(f"reachy_language_tutor.tools.{module.name}")
        for obj in vars(loaded).values():
            schema = getattr(obj, "parameters_schema", None)
            if not isinstance(obj, type) or not isinstance(schema, dict):
                continue
            names = set(schema.get("properties", {})) | set(schema.get("required", []) or [])
            for name in names:
                if "learner" in name.lower() or name in IDENTITY_PARAMETERS:
                    offenders.append(f"{getattr(obj, 'name', obj.__name__)}.{name}")

    assert not offenders, "tools must not take a learner identity as an argument: " + "; ".join(offenders)


# --- Hard-coded in exactly one place --------------------------------------------------


def test_the_hardcoded_learner_id_matches_the_seeded_learner() -> None:
    """The literal is duplicated on purpose; this is what keeps the copy honest."""
    assert main.HARDCODED_CURRENT_LEARNER_ID == store.SEED_LEARNERS[0][0]


def test_the_learner_id_is_hardcoded_in_exactly_one_place() -> None:
    """Milestone 4 must change one line, so make "one line" a checked fact."""
    package = Path(main.__file__).resolve().parent
    seed_data = package / "learners" / "store.py"

    hits = [
        f"{path.relative_to(package)} x{path.read_text(encoding='utf-8').count(main.HARDCODED_CURRENT_LEARNER_ID)}"
        for path in sorted(package.rglob("*.py"))
        if path != seed_data and main.HARDCODED_CURRENT_LEARNER_ID in path.read_text(encoding="utf-8")
    ]
    assert hits == ["main.py x1"], f"the learner id should be chosen in one place only, found: {hits}"


# --- Both failure modes leave it unset ------------------------------------------------


def test_an_unknown_learner_leaves_the_field_unset(
    instance: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A configured learner who is not in the database serves nobody, loudly."""
    monkeypatch.setattr(main, "HARDCODED_CURRENT_LEARNER_ID", "no-such-learner")
    with caplog.at_level(logging.DEBUG, logger=__name__):
        resolved = main.resolve_current_learner_id(instance, logging.getLogger(__name__))

    assert resolved is None
    # The POSITIVE assertion stays at ERROR: the operator-facing message is supposed to
    # be an ERROR, and accepting it at any level would weaken what pitfall 4 protects.
    messages = [record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR]
    assert any("not in the learner database" in message for message in messages), messages
    # The NEGATIVE one does not. Personal data does not belong in a log line at ANY
    # level -- today's id is a placeholder, but milestone 4 replaces it with a
    # recognized person -- and a capture that reaches DEBUG while the assertion only
    # reads ERROR is a disagreement a review caught rather than a safety margin. This
    # reads the whole record for the same reason _log_surface does.
    surfaces = [_log_surface(record) for record in caplog.records]
    assert not any("no-such-learner" in surface for surface in surfaces), surfaces


def test_an_unreadable_store_leaves_the_field_unset(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A broken database must not be reported as "I do not know you"."""
    with caplog.at_level(logging.DEBUG, logger=__name__):
        resolved = main.resolve_current_learner_id(tmp_path, logging.getLogger(__name__))

    assert resolved is None
    messages = [record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR]
    assert any("unreadable" in message for message in messages), messages


def _log_surface(record: logging.LogRecord) -> str:
    """Everything a record carries that could later be rendered, not just the message.

    getMessage() alone is not the pin. A record holds `msg` and `args` separately and
    formats them lazily, so an id can sit in `args` and reach a handler, a formatter or
    a remote log sink that never called getMessage() in this process. Asserting on the
    formatted string alone would pass while the id still left the machine.

    Nor are msg and args the pin on their own, which a review caught and this helper
    would otherwise have hidden. `logger.exception(msg)` and `logger.error(msg,
    exc_info=e)` leave msg a constant and args empty while the LIVE exception rides in
    record.exc_info, and every handler in the chain renders it -- so the id ships while
    a msg/args/getMessage assertion stays green. That matters here more than it would
    elsewhere: logger.exception is this codebase's prevailing idiom (console.py,
    personality_routes.py, tool_space_routes.py, tool_settings.py, play_emotion.py and
    ~10 more), so it is exactly what someone asked to improve triage on this branch
    would reach for. extra={"learner_id": ...} is a third channel, landing straight in
    record.__dict__ and invisible to all of the above.

    record.name is a fifth channel and was found by asking for a fifth rather than by
    being surprised by one. logger.getChild(learner_id).error(...) puts the id in the
    LOGGER NAME, which every "%(name)s" formatter renders, while msg, args and exc_info
    stay clean -- the same geometry as the exc_info gap, one channel over. record.name
    is always a str, so it needs no guard.

    record.exc_text is the sixth, and weaker: a formatter caches the rendered traceback
    there, and today exc_info is still set whenever it is, so the arm above already
    covers it. It becomes independent only under QueueHandler.prepare and
    SocketHandler.makePickle, which clear exc_info after caching. Neither is on this
    path; it is here so the next reader does not have to rediscover why it is safe.

    So this reads the WHOLE record rather than the message channel. A pin that only
    covers the spelling in front of it is not a pin.
    """
    parts = [record.name, str(record.msg), str(record.args), record.getMessage()]
    if record.exc_info:
        parts.append("".join(traceback.format_exception(*record.exc_info)))
    if record.exc_text:
        parts.append(str(record.exc_text))
    if record.stack_info:
        parts.append(str(record.stack_info))
    # Whatever extra= attached is an ordinary attribute; diff against a bare record
    # rather than hand-listing the standard field names, which drift between versions.
    standard = logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys()
    parts.extend(f"{key}={value}" for key, value in record.__dict__.items() if key not in standard)
    return " | ".join(parts)


def test_an_exception_carrying_the_learner_id_does_not_put_it_in_a_log(
    instance: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The generic branch is the one that regresses, and this is the shape that does it.

    The store absorbs sqlite3.Error, OSError and ValueError today, so no id can reach
    this branch -- but that is a property of the store's internals, not of this code,
    and nothing made it fail if the store started raising. A validating profile
    constructor, a pydantic model, or one raise ValueError(f"... {learner_id}") under
    get_profile would render a real person's identifier into an ERROR log at milestone
    4, with no test noticing. So the exception is forced here rather than hoped against.
    """
    identifier = main.HARDCODED_CURRENT_LEARNER_ID

    def _raise_with_the_id_in_the_message(*_args: object, **_kwargs: object) -> None:
        raise ValueError(f"profile validation failed for learner {identifier}")

    monkeypatch.setattr(learners, "get_profile", _raise_with_the_id_in_the_message)
    with caplog.at_level(logging.DEBUG, logger=__name__):
        resolved = main.resolve_current_learner_id(instance, logging.getLogger(__name__))

    # Unchanged behaviour: still permissive at the process boundary.
    assert resolved is None
    # Every level, not just ERROR: the consideration says "at any level", and a future
    # logger.debug("resolving %s", id) on this path would emit no ERROR record at all
    # and leave a level-filtered assertion green.
    records = list(caplog.records)
    assert records, "the generic branch must still say something; silence is its own failure"
    leaked = [_log_surface(record) for record in records if identifier in _log_surface(record)]
    assert not leaked, f"a learner id reached a log record: {leaked}"
    # And the operator is not left with nothing: the type still names what went wrong.
    assert any("ValueError" in _log_surface(record) for record in records), [
        _log_surface(record) for record in records
    ]


def test_an_exception_from_the_availability_check_is_caught_the_same_way(
    instance: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The second query can raise too, and it is reached only when the first returns None."""
    identifier = main.HARDCODED_CURRENT_LEARNER_ID
    monkeypatch.setattr(main, "HARDCODED_CURRENT_LEARNER_ID", "no-such-learner")

    def _raise_with_the_id_in_the_message(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError(f"store handle lost while checking {identifier}")

    monkeypatch.setattr(learners, "store_is_available", _raise_with_the_id_in_the_message)
    with caplog.at_level(logging.DEBUG, logger=__name__):
        resolved = main.resolve_current_learner_id(instance, logging.getLogger(__name__))

    assert resolved is None
    # Every level, not just ERROR: the consideration says "at any level", and a future
    # logger.debug("resolving %s", id) on this path would emit no ERROR record at all
    # and leave a level-filtered assertion green.
    records = list(caplog.records)
    leaked = [_log_surface(record) for record in records if identifier in _log_surface(record)]
    assert not leaked, f"a learner id reached a log record: {leaked}"
    assert any("RuntimeError" in _log_surface(record) for record in records), [
        _log_surface(record) for record in records
    ]


def test_an_unexpected_error_still_returns_none_rather_than_aborting_startup(
    instance: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Pitfall 2: narrowing the catch must not turn a storage surprise into a dead robot.

    A corrupt database in someone's home bricking the robot is a worse outcome than a
    dead learner surface, so the catch stays broad and this pins it with an exception
    type the store has no reason to raise.
    """

    def _raise_something_unexpected(*_args: object, **_kwargs: object) -> None:
        raise MemoryError("out of memory")

    monkeypatch.setattr(learners, "get_profile", _raise_something_unexpected)
    with caplog.at_level(logging.DEBUG, logger=__name__):
        resolved = main.resolve_current_learner_id(instance, logging.getLogger(__name__))

    assert resolved is None


def test_startup_dependencies_are_unset_when_the_store_is_missing(tmp_path: Path) -> None:
    """The robot still starts; it is the learner surface that goes dead, not the app."""
    deps = main.build_tool_dependencies(
        robot=MagicMock(),
        movement_manager=MagicMock(),
        instance_path=tmp_path,
        camera_enabled=False,
        logger=logging.getLogger(__name__),
    )
    assert deps.current_learner_id is None
