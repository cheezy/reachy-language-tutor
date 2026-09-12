---
name: sweeping-a-fix
description: Use before closing ANY bug fix, to find the siblings that share the same mechanism. The most common defect on this board is a fix applied to one member of a set while its siblings kept the bug — six of twenty-two, including one bug found three times in a row.
---

# Sweeping a fix

Before you close a fix, find everything that shares its mechanism. This is the single most
repeated defect class in this repository, and it is the cheapest to prevent — usually one
`grep`.

## What it looks like when you skip it

One bug, found three separate times, each costing a whole task:

1. **W5** promised `record_result` never raises, type-checked `score`, and left the sibling
   parameter `recorded_at` unchecked → **D5**.
2. **D5** fixed that class in `record_result` and left three readers raising `OverflowError`
   on an out-of-range id → **D10**.
3. **D10** widened those three readers so a caller-supplied *learner id* could not escape as
   an exception, and left the same three raising on a bad *instance path* — "by the same
   mechanism" → **D14**.

And the same shape elsewhere: **D3** (the no-PII-in-logs rule obeyed in `store.py` but not in
the conversation loop one layer up), **D6** (every statement a named constant except one, so
the structural guard never saw it), **D12** (a hole closed in the AST guard but not in the
function it reuses).

## The sweep

Ask these four, and answer each with a command rather than from memory:

1. **Other parameters of the same function.** Fixed one argument's validation? What about its
   siblings? `def record_result(learner_id, lesson_id, outcome, score, recorded_at)` — a fix
   to one of those is a hypothesis about the other four.
2. **Other members of the same module or set.** Fixed one reader, one statement, one route,
   one handler? List them all and check each:
   ```bash
   grep -n 'def \|rpc.register(\|@rpc.method(' <module>
   ```
3. **Other callers of the same sink.** Fixed the value going in? Who else writes to that sink?
   `grep -rn '_persist_env_values' src/` is what proved D19 had exactly one reachable caller —
   and knowing that was what made the fix defensible.
4. **The same rule one layer up and one layer down.** A convention obeyed in the store and
   broken in the loop above it is D3. Conventions do not stop at a module boundary.

## Closing it out

Either fix the siblings in the same change, or state in `completion_notes` which ones you
found and why they are out of scope. "I did not look" is the answer that produced D5, D10 and
D14 in sequence.

If a sibling is a genuinely separate piece of work, file it — but say in the note that it is a
sibling of what you just fixed and name the mechanism they share, so the next person does not
rediscover the relationship.
