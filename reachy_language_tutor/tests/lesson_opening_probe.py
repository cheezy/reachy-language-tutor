"""Drive a lesson opening through the real model and read what it actually says.

    python reachy_language_tutor/tests/lesson_opening_probe.py --runs 4

This is NOT a pytest test, and the name is deliberate: it does not match `test_*` or
`*_test`, so `tests/test_pytest_configuration.py`'s disk-vs-collected comparison never
names it and `pytest reachy_language_tutor` never runs it. It needs credentials, a
network and real money -- the same reasoning `tests/identity_probe_session.py` records
at length, and the same hatch.

WHY THIS EXISTS
---------------
D32 fixed a defect a learner reported in their own words: "I'm glad you could speak
Italian, but I have no idea what you just said." Lessons opened straight into the target
language, at the one turn whose job is to orient a beginner.

The tests in `test_locked_profile.py` assert that particular sentences are IN the
profile. That is not the same claim as the model OBEYING them, and D32 is where the
difference stopped being theoretical: the first fix passed every one of those assertions
and still failed live, twice over. Read that history in `docs/lesson-flow.md`.

So this module answers the question the prompt-level tests cannot: given the profile as
it stands, what does the model actually say when a lesson opens?

`docs/identity-boundary-probe.md` instructs "Re-run it after any prompt or model change."
A change to the locked profile is a prompt change. Re-run this too.

WHAT IT DOES
------------
Three scripted learner turns, chosen to exercise both halves of the fix:

  * **Italian** -- has written material (11 stored dialogue turns at position 1), so the
    opening should be one English sentence naming what is about to be practised, and
    then a switch into Italian.
  * **French** -- has none (six placeholder lessons, 0 stored turns, notes and drills),
    so `start_lesson` refuses it outright with `lesson_not_written_yet` and the opening
    should stay in English: say that French cannot be taught yet, offer the languages in
    `languages_with_material`, and switch nowhere. It must not invent vocabulary, an
    example or a drill.

    It must not name the French lesson's title or objective either, and that is not a
    nicety. On THIS path the refusal carries `language`, `language_code` and
    `languages_with_material` and nothing else -- no tool returned a title -- so a tutor
    that names one has invented it. Naming the objective IS permitted on the other
    no-material path, where `get_lesson_content` returns the lesson; the two paths differ
    and this leg only exercises the first.

    This leg was Spanish until D33. W38 curated six Spanish units, which turned it into a
    second has-material leg without anything failing to say so -- see the comment on
    SCRIPT below. Check which languages are still empty before trusting this list.

THE VERDICT IS A HUMAN READ, AND THAT IS DELIBERATE
---------------------------------------------------
There is no automatic pass/fail here. Both real failures this probe found were semantic:
one delivered a correct fact in the wrong language, the other invented content that was
perfectly well-formed. A regex for either would be a deny-list, which this repository has
paid for four times (CLAUDE.md, "Name what is PERMITTED"). Read the turns.

RUN IT MORE THAN ONCE. Both failures were intermittent -- one appeared in one run of
three, the other in another -- so a single green run is not evidence. `--runs` defaults
to 4 for that reason, and each run is a fresh session.

SAFETY
------
  * The database is built fresh in a temporary directory on every run. There is no
    `--instance-path`, so this can never be pointed at a real household's data.
  * The conversation is held in memory and printed to stdout. It is never logged:
    `refuse_debug_logging()` is called before any session starts, which is the same
    guard `identity_probe_session.py` uses to keep a learner's turns out of the log.
  * The Hugging Face token is never read, echoed, stored or passed by this module. The
    shipped client resolves it from the ambient `hf auth login` credential.
"""

import argparse
import asyncio
import logging
import sys
import tempfile
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
sys.path[:0] = [str(_TESTS), str(_TESTS.parent / "src")]

import conversation_probe as probe  # noqa: E402
from conversation_probe import (  # noqa: E402
    AttackFamily,
    Probe,
    ProbeSession,
    Recorder,
    RecordingHandler,
)
from reachy_language_tutor.learners import store  # noqa: E402

logger = logging.getLogger("lesson_opening_probe")

# AttackFamily.CONTROL, not an attack family: these are ordinary learner turns. The probe
# harness was built for the adversarial identity work, and CONTROL is the value it uses
# for a turn that is not trying to break anything.
# The third turn names a language with NOTHING written in it, which is the whole point
# of this probe -- and which language that is has already changed once. D32 wrote it as
# Spanish because Spanish was then an empty plan; W38 curated six Spanish units, so from
# that commit the leg silently started exercising the has-material path instead, and this
# probe went on reporting that it had covered the no-material one. D33 moved it to French.
#
# If French is ever curated, move this again rather than deleting the turn -- and check
# the footer in main(), which tells the reader what to expect of this language by name.
# `store.get_language_catalog` is what says which languages have material today.
SCRIPT = [
    ("greet", "Hello Reachy."),
    ("italian", "I'd like to practise some Italian please."),
    ("french", "Actually, can we do French instead?"),
]


async def _run_once(instance: Path):
    """One fresh session, three scripted turns, returning the recorded turns."""
    from reachy_language_tutor.tools import core_tools

    core_tools.initialize_tools(instance_path=instance, force=True)
    deps = probe.make_probe_deps(instance, logger)
    recorder = Recorder()
    handler = RecordingHandler(deps, recorder=recorder, instance_path=str(instance))
    probes = [Probe(id=i, family=AttackFamily.CONTROL, text=t) for i, t in SCRIPT]
    return await ProbeSession(handler, recorder).run(probes)


def _report(turns) -> None:
    """Print every turn. No verdict -- see the module docstring for why."""
    for turn in turns:
        label = turn.probe.id if turn.probe else "(startup greeting)"
        print(f"\n  --- {label} ---")
        if turn.probe:
            print(f"      learner : {turn.probe.text}")
        print(f"      tutor   : {turn.assistant_text()[:600]!r}")
        for call in turn.model_driven_calls():
            name = getattr(call, "tool_name", None) or getattr(call, "name", "?")
            print(f"      tool    : {name} {str(call.args_parsed)[:90]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Does a lesson opening orient a learner who does not speak the language? (D32)"
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=4,
        help="How many fresh sessions to run. Both failures this probe found were "
        "intermittent, so one run proves nothing. Default 4.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the scripted turns and exit, without calling the model.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # Before any session exists, so a learner's turns cannot reach the log even if a
    # later line in this function raises.
    probe.refuse_debug_logging()

    if args.dry_run:
        for name, text in SCRIPT:
            print(f"  [{name}] {text}")
        return 0

    if args.runs < 1:
        parser.error("--runs must be at least 1")

    for run in range(1, args.runs + 1):
        instance = Path(tempfile.mkdtemp(prefix="lesson-opening-probe-"))
        assert store.ensure_learner_database(instance).ready, "seed failed"
        print(f"\n========== run {run} of {args.runs} ==========")
        _report(asyncio.run(_run_once(instance)))

    print(
        "\nRead the turns above. Italian should open with one English sentence and then\n"
        "switch. French should stay in English, say it cannot be taught yet, and offer a\n"
        "language that has material. Nothing else is grounded: start_lesson's refusal\n"
        "carries no title and no objective, so a named French lesson is invented, and so\n"
        "is any French word, example or drill. One French phrase offered as teaching, or\n"
        "one lesson title nothing returned, is the failure."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
