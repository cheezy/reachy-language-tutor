"""Render an exception for a log line without letting it quote a value.

One rule, one place. It lived in `learners/store.py` and was obeyed there while
`memory.py` -- a sibling holding the same household's data -- logged a home directory
path and a raw `OSError` on two lines. That is D3's shape for the third time in this
repository: a convention honoured in one module and broken in the one beside it. A
module that logs an exception near personal data imports `log_safe` from here rather
than deciding for itself.

The rule is an ALLOW-LIST, because the deny-list version leaked. It rendered every
exception in full except `UnicodeError`, chosen because almost every exception these
readers absorb is value-free. `Almost` is the word that failed: `OSError` embeds its
filename in `__str__`, so a warning that carefully did not interpolate the path printed

    The learner database is unavailable: [Errno 17] File exists:
    '/Users/.../alice-smith-household/instance'

-- a household's name in a log file on a robot in their house, which is CWE-532.
Measured on a real blocked path, not reasoned about.
"""

from __future__ import annotations
import sqlite3


class SafeToLog(Exception):
    """Marker for an exception whose message was built from types, never from values.

    A module raising a refusal it worded itself -- "instance_path must be a path, not
    int" -- knows that message carries no personal data. Nothing else does, so the
    knowledge is carried by the TYPE rather than by a comment nobody can check.
    """


# The families rendered in full, because none can quote a caller-supplied value. Each
# was checked against the real thing rather than assumed:
#
#   sqlite3.Error   names schema objects and conditions, never the bound value -- a
#                   UNIQUE violation on a learner id reports "UNIQUE constraint failed:
#                   t.id", a CHECK reports the constraint expression, a type mismatch
#                   reports the column.
#   OverflowError   one constant string, "Python int too large to convert to SQLite
#                   INTEGER". The offending int does not appear in it.
#   RuntimeError    raised here only where a module worded it or Path.home() failed;
#                   neither names a path.
#   SafeToLog       the marker above.
#
# Deliberately absent: plain ValueError and TypeError. A stdlib conversion quotes what
# it could not convert ("invalid literal for int() with base 10: 'alice'"), and that is
# the caller's value. UnicodeEncodeError is absent for the same reason -- its message
# carries the offending character and its index within the learner id.
_RENDERED_IN_FULL: tuple[type[BaseException], ...] = (
    sqlite3.Error,
    OverflowError,
    RuntimeError,
    SafeToLog,
)


def log_safe(exc: BaseException) -> object:
    """Render this exception for a log line: in full if it cannot quote a value, else its class."""
    # OSError first, and before the allow-list, because it must never reach it: its
    # errno is the diagnosis (17 is "it exists", 13 is "permission") and its filename
    # is somebody's home directory.
    if isinstance(exc, OSError):
        return f"{type(exc).__name__}(errno={exc.errno})"
    if isinstance(exc, _RENDERED_IN_FULL):
        return exc
    return type(exc).__name__
