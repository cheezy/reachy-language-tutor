"""Measure the face-matching threshold against real faces, and print what it costs.

WHY THIS EXISTS AS A SCRIPT RATHER THAN A TEST. The threshold in
`faces/matching.py` is an authorization control, and the honest way to choose one is
to measure both error directions on real faces. There are no real faces in this
repository and there must not be: docs/learner-database.md promises no image of
anybody is stored, and a committed test photograph would break that on the first
clone. So the data stays on the operator's machine, outside the repo, and this script
reads it there.

IT WRITES NOTHING. No frame, no crop, no embedding, no report file. It prints a table
to the terminal and exits. Copy the numbers into the comment beside
FACE_MATCH_SIMILARITY_FLOOR yourself -- a script that edited that constant would be a
script that tuned an authorization control until the output looked nice.

IT ALSO PRINTS NO NAMES. An operator runs this with `| tee` more often than not, so
stdout becomes a log, and a directory named after a child is personal data like any
other. People are numbered in the output; map them back from your own listing.

USAGE

    python scripts/calibrate_faceprints.py ~/faces

where ~/faces holds one directory per person, each containing several images of that
person:

    ~/faces/ana/{1.jpg,2.jpg,3.jpg}
    ~/faces/ben/{1.jpg,2.jpg}

Every image is asked about in FOUR households, because the matcher's two controls
protect against different people and a table that only measures one of them says
nothing about the other:

  1. MEMBER, everyone enrolled. The image's own person is enrolled from another of
     their images. Correct, refused, or WRONG PERSON -- a household member handed
     another's records. This is what the floor and the margin protect together.
  2. STRANGER, everyone else enrolled. The image's own person is left out, so the only
     right answer is nobody. Any match is a false accept of a visitor.
  3. MEMBER, alone. Only the image's own person is enrolled -- the state of every robot
     between the first enrolment and the second. The margin has nothing to compare
     here, so this is the floor on its own.
  4. STRANGER, alone. Only one OTHER person is enrolled, once for each of them. Any
     match is a visitor answered as the one household member, on the floor alone.

The first version measured only the first, with everyone enrolled: it never asked
about a household of one or about anybody who was not enrolled, and those are the two
cases the floor alone decides. Record all four tables beside the constants.
"""

from __future__ import annotations
import sys
import logging
from pathlib import Path
from collections import Counter

# Through the package boundary, which faces/__init__.py declares is the only supported
# import path -- every symbol needed is in its __all__. The first version of this
# script reached past it into .embedding and .matching, which is exactly the rule the
# package docstring states and the sort of thing that makes a boundary decorative.
from reachy_language_tutor.faces import (
    FACE_MATCH_MARGIN,
    EMBEDDING_MODEL_ID,
    FACE_EMBEDDING_AVAILABLE,
    FACE_MATCH_SIMILARITY_FLOOR,
    EnrolledFaceprint,
    describe_face,
    match_faceprint,
    warm_face_models,
)


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp"}


def _load(path: Path) -> object | None:
    """Read one image, or None. Imported here so the module stays importable without cv2."""
    import cv2

    return cv2.imread(str(path))


# The outcomes that are a person handed records that are not theirs. Named, so the
# verdict below is taken from these rows and not from whichever happened to be printed.
_FALSE_ACCEPTS = ("WRONG PERSON", "VISITOR MATCHED")


def _enrolled(learner: str, vector: tuple[float, ...]) -> EnrolledFaceprint:
    return EnrolledFaceprint(
        learner_id=learner,
        embedding_model=EMBEDDING_MODEL_ID,
        dimension=len(vector),
        vector=vector,
    )


def _ask(
    tally: Counter[str],
    probe: tuple[float, ...],
    household: list[EnrolledFaceprint],
    owner: str | None,
) -> None:
    """Record one answer. `owner` is who the probe is, or None when they are not enrolled."""
    outcome = match_faceprint(probe, household, embedding_model=EMBEDDING_MODEL_ID)
    if not outcome.matched:
        tally[f"refused ({outcome.reason})"] += 1
    elif owner is None:
        tally["VISITOR MATCHED"] += 1
    elif outcome.learner_id == owner:
        tally["correct"] += 1
    else:
        tally["WRONG PERSON"] += 1


def measure(people: dict[str, list[tuple[float, ...]]]) -> dict[str, Counter[str]]:
    """Ask the matcher about every image in the four households the module docstring names.

    Pure: vectors in, tallies out, nothing read or written -- which is also what lets it
    be tested with synthetic vectors and no photographs.
    """
    tables: dict[str, Counter[str]] = {
        "1. member, everyone enrolled": Counter(),
        "2. stranger, everyone else enrolled": Counter(),
        "3. member, alone in the household": Counter(),
        "4. stranger, one other person enrolled": Counter(),
    }
    first, second, third, fourth = tables.values()
    for person, vectors in people.items():
        for index, probe in enumerate(vectors):
            # Their own enrolment is always another of their images, never the probe.
            own = _enrolled(person, vectors[(index + 1) % len(vectors)])
            others = [_enrolled(other, other_vectors[0]) for other, other_vectors in people.items() if other != person]
            _ask(first, probe, [own, *others], person)
            _ask(second, probe, others, None)
            _ask(third, probe, [own], person)
            for somebody_else in others:
                _ask(fourth, probe, [somebody_else], None)
    return tables


def main(argv: list[str]) -> int:
    """Run the calibration over a directory of people and print the result."""
    logging.basicConfig(level=logging.WARNING)
    if len(argv) != 2:
        print(__doc__)
        return 2
    root = Path(argv[1]).expanduser()
    if not root.is_dir():
        print(f"not a directory: {root}")
        return 2
    repo_root = Path(__file__).resolve().parent.parent
    # is_relative_to, not equality: scripts/ and the package directory are inside the
    # repository too, and the message already promised to refuse them.
    if root.resolve().is_relative_to(repo_root):
        print("refusing to read anything inside the repository; point this at a directory of faces outside it")
        return 2
    if not FACE_EMBEDDING_AVAILABLE or not warm_face_models():
        print("face models unavailable (no library, or no network to fetch the pinned models)")
        return 1

    people: dict[str, list[tuple[float, ...]]] = {}
    # Numbered, never named: person_01, person_02. The operator maps them back from
    # their own directory listing, and the table means exactly the same either way.
    for number, person_dir in enumerate(sorted(p for p in root.iterdir() if p.is_dir()), start=1):
        label = f"person_{number:02d}"
        vectors: list[tuple[float, ...]] = []
        for index, image_path in enumerate(sorted(person_dir.iterdir()), start=1):
            if image_path.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            image = _load(image_path)
            if image is None:
                print(f"  unreadable, skipped: {label} image {index}")
                continue
            # describe_face rather than embed_face, so the operator is told WHICH
            # problem their photograph has. Both return the same faceprint; only this
            # one says why there isn't one. An operator curating a folder can act on
            # "two faces in shot" and cannot act on "no single confident face" -- and
            # that collapsed message was hiding five different causes, which is the
            # same defect enrolment was written to stop showing a household.
            described = describe_face(image)
            if not described.usable:
                print(f"  skipped ({described.reason}): {label} image {index}")
                continue
            vectors.append(described.vector)
        if len(vectors) >= 2:
            people[label] = vectors
        elif vectors:
            print(f"  {label}: only one usable image, needs at least two -- skipped")

    if len(people) < 2:
        print("need at least two people with two usable images each")
        return 1

    print(f"\n{len(people)} people, {sum(len(v) for v in people.values())} usable images")
    print(
        f"floor={FACE_MATCH_SIMILARITY_FLOOR}  margin={FACE_MATCH_MARGIN}  (one floor, whatever the household size)\n"
    )

    tables = measure(people)
    for title, tally in tables.items():
        total = sum(tally.values())
        print(title)
        print(f"  {'outcome':34} {'count':>6} {'share':>8}")
        for label, count in tally.most_common():
            print(f"  {label:34} {count:>6} {count / total:>7.1%}")
        print()

    false_accepts = sum(tally[label] for tally in tables.values() for label in _FALSE_ACCEPTS)
    if false_accepts:
        print(f"{false_accepts} FALSE ACCEPT(S): a household member answered as another, or a visitor answered")
        print("as a member. Raise FACE_MATCH_SIMILARITY_FLOOR or FACE_MATCH_MARGIN until every such row")
        print("is zero, then record these tables beside the constants. A refusal is the acceptable error.")
    else:
        print("No false accepts over this set, in any of the four households. Record the tables beside")
        print("the constants, with the set's size -- 'zero false accepts over N images of M people' is")
        print("the claim, not 'the threshold is correct'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
