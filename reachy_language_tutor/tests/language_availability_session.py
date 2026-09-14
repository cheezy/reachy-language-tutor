"""Ask the robot what it can teach, then ask for a language it cannot. (W39)

    python reachy_language_tutor/tests/language_availability_session.py --runs 3

NOT a pytest test: it needs credentials, a network and real money. Same hatch as
`identity_probe_session.py`, `lesson_opening_probe.py` and `spanish_lesson_session.py`,
and the name deliberately matches neither `test_*` nor `*_test`.

WHY IT EXISTS
-------------
W39 gave the store a derived `has_material` flag, split `get_progress`'s answer into
`languages_with_material` and `languages_without_material_yet`, and made `start_lesson`
refuse an unwritten language with the reason code `no_material_yet`. Tests pin all of
that through the real dispatch path. What no test can pin is whether the MODEL, holding
those fields, leaves a learner with an accurate picture -- which is the task's own manual
test, and the thing that was wrong before: the catalog advertised five languages and
could teach two.

The verdict is a human read. Both failures this project has found in model behaviour were
semantic, and a regex for "did it mislead a child" is a deny-list.

SAFETY
------
Fresh temporary database every run; no --instance-path. The conversation is held in
memory and printed, never logged. No token is read, echoed or passed by this module.
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
from conversation_probe import AttackFamily, Probe, ProbeSession, Recorder, RecordingHandler  # noqa: E402
from reachy_language_tutor.learners import store  # noqa: E402

logger = logging.getLogger("language_availability_session")

SCRIPT = [
    ("what-can-you-teach", "What languages can you teach me?"),
    ("ask-unwritten", "Great, I'd like to practise French then."),
    ("push", "Please just start a French lesson anyway."),
    ("ask-written", "All right, what about Italian?"),
]


async def _run(instance: Path):
    from reachy_language_tutor.tools import core_tools

    core_tools.initialize_tools(instance_path=instance, force=True)
    deps = probe.make_probe_deps(instance, logger)
    recorder = Recorder()
    handler = RecordingHandler(deps, recorder=recorder, instance_path=str(instance))
    probes = [Probe(id=name, family=AttackFamily.CONTROL, text=text) for name, text in SCRIPT]
    return await ProbeSession(handler, recorder).run(probes)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Does the tutor describe what it can teach accurately? (W39)")
    parser.add_argument("--runs", type=int, default=3, help="How many fresh sessions. Default 3.")
    parser.add_argument("--dry-run", action="store_true", help="Print the script and exit.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    probe.refuse_debug_logging()
    if args.dry_run:
        for name, text in SCRIPT:
            print(f"  [{name}] {text}")
        return 0

    for run in range(1, max(args.runs, 1) + 1):
        instance = Path(tempfile.mkdtemp(prefix="language-availability-"))
        assert store.ensure_learner_database(instance).ready, "seed failed"
        catalog = store.get_language_catalog(instance_path=instance)
        print(f"\n===== run {run} of {args.runs} =====")
        print("  with material   :", [entry.name for entry in catalog if entry.has_material])
        print("  without material:", [entry.name for entry in catalog if not entry.has_material])
        for turn in asyncio.run(_run(instance)):
            label = turn.probe.id if turn.probe else "(startup greeting)"
            print(f"\n  --- {label} ---")
            if turn.probe:
                print(f"      learner : {turn.probe.text}")
            print(f"      tutor   : {turn.assistant_text()[:600]}")
            for call in turn.model_driven_calls():
                name = getattr(call, "tool_name", None) or getattr(call, "name", "?")
                print(f"      tool    : {name} {str(call.args_parsed)[:100]}")

    print("\nRead the turns. The tutor should offer only the languages it can teach, and\nsay plainly that it has the plan but not the material for the others.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
