"""Give a test a clean tool registry without creating a second ``Tool`` class.

Three test files need ``core_tools`` to rebuild its registry from scratch: one
exercises external profiles, one profile-load resilience, one Tool Space runtime.
Each did it by popping every ``reachy_language_tutor.tools.*`` module out of
``sys.modules`` and re-importing.

That is the defect. Re-importing ``core_tools`` executes it again, which creates a
SECOND ``Tool`` base class. Every module-scope ``from ... import core_tools``
elsewhere in the suite is already bound to the first one, and
``_load_enabled_tools`` filters candidates with ``issubclass(value, Tool)`` -- so
against the wrong ``Tool`` it matches nothing and returns an empty list. The loader
reports that as "the profile declares unknown tools": a message about the profile,
for a fault that has nothing to do with the profile, in whichever file happens to
run next. Seven test files grew in-function ``core_tools`` imports to dodge it.

Two theories were tested and are recorded here because both are wrong and both are
tempting:

* **Restoring ``sys.modules`` afterwards.** Measured: fixes the pair it targets and
  breaks thirteen tests elsewhere. Putting an old module object back does not
  un-bind the references other modules already hold.
* **Re-pointing the parent package's ``core_tools`` attribute.** Measured: a no-op.
  ``importlib.import_module`` already sets that attribute as part of importing, so
  it is never the half that is stale.

The reference held by another module's globals is the thing that goes stale, and
nothing done after the fact can reach it. So the second module is never created.
The registry is reset in place instead: caches cleared and the signature dropped,
which is exactly what ``initialize_tools`` checks before deciding whether to
rebuild.

External tool modules ARE still discarded. They are file-backed, several tests
deliberately re-execute them under changed configuration, and they define no base
class -- so unlike ``core_tools`` they can be re-imported harmlessly.
"""

import sys
import importlib
from types import ModuleType


_CORE_TOOLS = "reachy_language_tutor.tools.core_tools"
_EXTERNAL_PREFIX = "reachy_language_tutor._external_tools."


def reload_tools_package() -> ModuleType:
    """Return core_tools with its registry reset, and no second module in existence."""
    for module_name in list(sys.modules):
        if module_name.startswith(_EXTERNAL_PREFIX):
            sys.modules.pop(module_name, None)

    core_tools = importlib.import_module(_CORE_TOOLS)

    # Everything initialize_tools consults when deciding whether it may skip a rebuild.
    # Dropping the signature is what makes the next call actually rebuild; clearing the
    # caches is what makes it re-read the tool classes rather than reuse them.
    core_tools._LOADED_TOOL_CLASS_CACHE.clear()
    core_tools._LOADED_REMOTE_TOOL_CACHE.clear()
    core_tools.ALL_TOOLS = {}
    core_tools._TOOLS_SIGNATURE = None
    core_tools._TOOLS_INSTANCE_PATH = None
    return core_tools
