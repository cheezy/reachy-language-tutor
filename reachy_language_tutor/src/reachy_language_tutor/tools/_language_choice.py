"""How a spoken language name becomes a catalog row, for every tool that asks.

A learner says "Spanish", or "spanish", or "es". Every tool that takes a language has
to turn that into a row the database really has, and the rule for doing so is the
security-relevant part: it decides whether the robot denies teaching a language it
teaches. get_progress already has a regression test for exactly that mistake.

So the rule lives here once rather than in each tool. Two copies would drift the day
somebody teaches one of them to fold the accent in "espanol" -- and CLAUDE.md records
that a fix applied to one member of a set while its siblings kept the bug is the most
repeated defect in this repository.

What does NOT live here is the catalog read itself. Each tool fetches the catalog
through its own module-level import, which is what lets its tests monkeypatch that one
function and prove the tool's own error branches. Three lines that cannot drift
silently are worth keeping in place; a matching rule that can is not.

Leading underscore, so config.list_tool_module_names never offers this as a selectable
tool, and no Tool subclass here, so the loader passes over it.
"""

from __future__ import annotations
from typing import Sequence

from reachy_language_tutor.learners import CatalogLanguage


def resolve_language(catalog: Sequence[CatalogLanguage], spoken: object) -> CatalogLanguage | None:
    """Return the catalog row this spoken language names, or None when none does.

    The catalog is what makes "I do not teach that" a fact rather than a guess, so a
    caller passes in the rows it read and gets back one of them or nothing -- never a
    code it then has to trust.

    Matches a code or a name, case-insensitively and ignoring surrounding space,
    because the model writes what it heard. Anything that is not a usable string
    matches nothing; a caller tells that apart from "not taught" by checking the
    argument itself before calling, which is what lets it answer "I did not catch
    that" without touching the store.
    """
    if not isinstance(spoken, str) or not spoken.strip():
        return None
    wanted = spoken.strip().casefold()
    return next(
        (entry for entry in catalog if wanted in (entry.code.casefold(), entry.name.casefold())),
        None,
    )
