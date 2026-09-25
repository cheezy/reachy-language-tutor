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
import unicodedata
from types import MappingProxyType
from typing import Mapping, Sequence

from reachy_language_tutor.learners import CatalogLanguage


# Each taught language's name for itself, keyed by catalog code. An ALLOW-LIST of exact
# spellings, not a folding rule: "Español" is here and "Espanol" is not, and nothing is
# matched by similarity. A learner who says the language's own name was told "I do not
# teach that language" about a language the robot teaches -- measured for Español,
# Italiano, Français and "Brazilian Portuguese" -- which is the one mistake this module
# exists to prevent. Widening the set means adding a spelling here, deliberately.
#
# Keyed by code so an alias can only ever name a row the catalog actually holds: an
# endonym for a language the catalog does not list resolves to nothing, exactly as its
# English name would. Compared case-insensitively, like every other spelling here.
ENDONYMS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "de": frozenset({"Deutsch"}),
        "es": frozenset({"Español"}),
        "fr": frozenset({"Français"}),
        "it": frozenset({"Italiano"}),
        "pt": frozenset({"Português"}),
    }
)


def resolve_language(catalog: Sequence[CatalogLanguage], spoken: object) -> CatalogLanguage | None:
    """Return the catalog row this spoken language names, or None when none does.

    The catalog is what makes "I do not teach that" a fact rather than a guess, so a
    caller passes in the rows it read and gets back one of them or nothing -- never a
    code it then has to trust.

    Matches a code, a name or the language's own name from ENDONYMS,
    case-insensitively and ignoring surrounding space, because the model writes what it
    heard. Anything that is not a usable string
    matches nothing; a caller tells that apart from "not taught" by checking the
    argument itself before calling, which is what lets it answer "I did not catch
    that" without touching the store.
    """
    if not isinstance(spoken, str) or not spoken.strip():
        return None
    wanted = _folded(spoken.strip())
    return next(
        (entry for entry in catalog if wanted in _spellings(entry)),
        None,
    )


def _spellings(entry: CatalogLanguage) -> frozenset[str]:
    """Every spelling that names this catalog row, folded the way the input is."""
    permitted = {entry.code, entry.name, *ENDONYMS.get(entry.code, frozenset())}
    return frozenset(_folded(spelling) for spelling in permitted)


def _folded(text: str) -> str:
    """Case-fold, after composing accents, so "ç" typed as c + cedilla is still "ç".

    Composition is not folding: the accent survives, so "Francais" still matches
    nothing. It only makes two encodings of the same letter compare equal.
    """
    return unicodedata.normalize("NFC", text).casefold()
