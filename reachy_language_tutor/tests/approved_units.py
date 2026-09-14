"""Which units of which course a person read and approved, and the check that uses it.

This is the control that decides what a child actually hears. Content may only come
from a unit on this list, so a unit nobody reviewed cannot ship by being overlooked --
it has to be added here first, by someone who can be asked why. The vocabulary screens
in test_converted_lessons.py are a BACKSTOP behind it, and that file says so: a list of
words to refuse is only ever as complete as the last person to think about it.

It lives in its own module for two reasons, and the second is the load-bearing one.

* Two test files need it -- the one that checks the shipped catalog and the one that
  checks the courses machinery -- and a test file importing another test file works
  here only because tests/ has no __init__.py. That is true today and is not a thing to
  rely on: it breaks under importmode=importlib, and a collection error in one file
  would cascade into the other. profile_lock.py and untaught_language.py are the
  existing shape for a value several tests share.
* The CHECK travels with the list. Keying the list by course fixed one half of the
  problem; the other half is that a caller can still ask the wrong question of it --
  test the numeral against the union of every course's units, say, and a second course's
  Unit IV is admitted again while the list itself looks perfectly correct. Exporting a
  function rather than only a mapping means there is one place that decides what
  "approved" means, and both callers go through it.

**Keyed by course, not by numeral alone.** Every FSI volume has a Unit IV. While the
converted-lesson file held one course a bare numeral identified a unit; the moment it
could hold two, a flat set of numerals would let a course nobody reviewed ship on the
strength of Italian's approval. A course absent from this mapping has approved nothing
-- the safe default, and the one that needs no maintaining.
"""

from types import MappingProxyType
from collections.abc import Mapping


# Six of eighteen units in Italian FAST Volume 1. The rest were set in an embassy, at a
# border, or at a currency desk, or leant on an official in uniform, and
# docs/curation-log-italian-fast.md says which and why. Shipping a seventh means adding
# it here first.
# A read-only view, not a plain dict. It used to be a frozenset -- immutable at its one
# level -- and keying it by course made the OUTER container mutable for the first time.
# Two test modules import this by reference, so one of them could widen the other's
# guarantee with a single item assignment and every shape test would stay green.
APPROVED_UNITS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "FSI Italian FAST, Volume 1": frozenset({"IV", "VI", "IX", "XIII", "XV", "XVII"}),
        # Six of 38 Cycles in the Spanish FAST. That volume screens far harder than
        # Italian's did -- 29 of its 38 Cycles carry embassy, military, uniformed or
        # border material, which is what a course written for diplomats arriving at
        # post looks like -- and docs/curation-log-spanish.md says which and why.
        # This list says what SHIPPED, not what passed a screen, so a seventh Cycle
        # means curating it first.
        "FSI Spanish Familiarization and Short-Term Training": frozenset(
            {"2", "5", "10", "14", "25", "38"}
        ),
    }
)


def approval_refusal(course: str, unit: str) -> str | None:
    """Return why this unit may not ship, or None when somebody approved it.

    The single place that decides what "approved" means. An unknown COURSE is refused
    before its unit is even looked at, which is what makes this an allow-list: absence
    is a refusal rather than an empty set of restrictions.

    Returns a reason rather than raising or returning a bool so a caller's assertion
    message can say which course and which unit, and so a test for the refusal can
    assert on the reason rather than on "something was falsy".
    """
    if course not in APPROVED_UNITS:
        return f"{course!r} is a course nobody approved units for"
    if unit not in APPROVED_UNITS[course]:
        return f"unit {unit!r} of {course!r} is not on the approved list for that course"
    return None
