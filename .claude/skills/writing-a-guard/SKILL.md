---
name: writing-a-guard
description: Use when writing or changing any rule that decides whether input is allowed — a validator, a scoping rule, an allow/deny check, a permission gate, or a sanitiser. Encodes what twenty-two defects on this board were made of (D4, D6, D11, D12, D15, D16, D17, D19, D20).
---

# Writing a guard

A guard is any rule that answers "may this through?" — `_learner_scoped`, `_is_an_hf_host`,
`_persist_env_values`'s line-break check, the `/rpc` method allow-list. Every one of those
shipped wrong at least once here. This is what actually worked, in order.

## 1. Enumerate the whole surface first

Before fixing what you were handed, list everything the rule governs, mechanically.

D20 was asked to neuter `conversation.mic` and `backend.config`. The surface was 21 methods
across four modules, ten of them writers — including one that installs a caller-named
Hugging Face Space as a callable tool. Two of ten doors were closed while the port was
opened. That is a regression wearing a fix's clothes.

```bash
grep -rn 'rpc.register(\|@rpc.method(' src/   # the real set, not the remembered one
```

If you cannot produce the list with a command, you do not know the surface yet.

## 2. Name what is PERMITTED

Write the allow-list. Never enumerate the bad cases.

This was got wrong four times here and the failure is always the same: the list of
forbidden things is complete only until someone thinks of another one.

- D11 named `NOT` and `CASE`; `IIF(learner_id = ?, 1, 1)`, `max(learner_id = ?, 1)`,
  `(learner_id = ?) = 0` and `learner_id = ? = 0` all walked through.
- D19 rejected `://`, `/`, `?`, `#` — and admitted `\n`, which was the entire defect.
- D20 neutered the two named methods; eight writers stayed reachable.

Each was fixed by inverting: *a conjunct must BE `alias.column = ?`*; *a host must BE a DNS
name or an IP literal*; *a method must BE named in the exposed set*. Inverting closes the
family, including the members nobody has thought of.

**Make it fail closed.** A new method, column or field added later must be refused until
someone deliberately permits it. `_NetworkRestrictedRpcServer` overrides `register()` — the
one place every registration path passes through — so exposure requires an edit to the
allow-list, not merely the absence of one.

## 3. Check the guard means what its consumer means

A guard narrower than the code it protects is not a guard.

D19's `.env` guard tested `"\n" in value or "\r" in value`. The reader used
`str.splitlines()`, which also splits on `\x0b \x0c \x1c \x1d \x1e \x85    `. A
value carrying one of those passed the guard, was written inside one line, and the *next*
read split it into a real `.env` entry. The fix was to test what the reader tests:
`value.splitlines() != [value]`.

Find the consumer. Read what it actually does. Match it, rather than assuming.

## 3b. A guard and the things that reuse it must be equally strict

If a test, an AST check or a caller reuses this rule, they must not diverge from it.

D12: the AST guard stripped SQL comments before consulting `_learner_scoped`, so the test was
strictly stricter than the rule — and the rule is the one that actually stops the module
importing. D6: every statement in the store was a named module constant except one written
inline, and the structural guard only sees named constants, so that one was invisible to it.
D4: the AST guard could not see non-literal queries, which is precisely where interpolation
risk lives — "weakest exactly where the risk is".

Ask: what else consults this rule, and does it see the same inputs I do?

## 4. Build the differential harness BEFORE changing code

Generate a corpus, record the current verdicts, then change the rule, then diff.

```python
old = {s: rule(s) for s in corpus}      # baseline, before any edit
# ... change the rule ...
moved = [s for s in corpus if old[s] is not None and rule(s) is None]   # newly accepted
```

Every statement that moves refuse→accept is one you are now permitting. Look at each.
D17's narrowing moved seven, and each had to be justified individually.

## 5. Execute what you accept against the real thing

Not the rule's opinion — the actual effect.

Run accepted statements against a real two-learner database and check whether a row changed
owner. Open a real socket. Parse the real URL back. Every bypass in D11, D17, D19 and D20
was found this way, and none by reading. When D11 finally swept 3,534 executed statements
and D19's reviewer executed ~500 transitions, both came back clean — and stayed clean.

## 6. Revert-proof, and check WHICH assertion fired

Undo the fix. The tests must fail, and fail for the reason you claim.

Weak assertions hide live bugs: D17's round-trip test asserted only `ok is True` and was
green over a URL that no longer parsed; D20 used a bare `pytest.raises(Exception)`. Assert
the specific reason code, the close code, the actual rows.

See also the memory note `verification-must-fail-for-the-claimed-reason` for the four ways
a revert-proof passes while proving nothing.

## 7. Write down only what you measured

Comments and docs around guards go stale into lies. D17 asserted "36 of 48" without
measuring it; D20 said "three methods" of a 21-method surface and claimed `.local` "cannot
be rebound" when mDNS is unauthenticated. If a number or a claim is not from this session's
run, do not write it — or name it as somebody else's measurement.
