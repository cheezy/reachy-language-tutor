"""The stand-ins this suite uses for "a language this robot does not teach".

Several tests need a language the catalog does not carry: to prove get_progress
answers a quiet None rather than an error, that the tool says which languages ARE
taught, and that a well-formed code which simply matches no row is handled like
any other absence.

Those tests used to spell that as German and Portuguese. That worked only while
the catalog happened not to teach them, which is a statement about today's seed
data rather than about the test -- and the day a task added German it broke
eight tests for placeholder reasons (eleven in all, counting the row-count and
catalog literals), threw ``UNIQUE constraint failed: languages.name``, and hid a
Portuguese sibling behind an earlier failure. The tests were naming a FORBIDDEN value, and a
list of forbidden values is only ever as good as the last person to read it.

So they name a permitted one instead. ``zxx`` is ISO 639-2/639-3 for "no
linguistic content / not applicable" -- not an unassigned code that might later
be allocated to a real language, but a code that MEANS there is no language
here. It can never collide with a catalog entry, because nothing would ever
teach it.

``test_learner_schema.py::test_the_untaught_placeholders_are_never_taught`` is
what keeps that true: it fails the day the seed teaches any of these.
"""

# A language the catalog does not teach, and never will.
UNTAUGHT_CODE = "zxx"

# Distinct from UNTAUGHT_CODE on purpose. One test draws a line between "a
# language we really do not teach" and "a legal-shaped code that is simply
# absent"; collapsing them would lose that distinction. qaa is the first of the
# ISO 639-2 private-use range, so it is equally safe from a future catalog.
UNTAUGHT_CODE_ABSENT = "qaa"

# The spoken name a person would say out loud, for the tool layer, which takes a
# name rather than a code. A constructed language is one a language tutor for
# children will not be adding.
UNTAUGHT_NAME = "Klingon"

# The languages.name value for a row a test inserts by hand. languages.name is
# UNIQUE, so this must never collide with a seeded display name.
UNTAUGHT_LANGUAGE_ROW_NAME = "No linguistic content"
