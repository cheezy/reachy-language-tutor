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

Everyone present is treated as enrolled. For each image the script asks: with that
image's own person enrolled from their OTHER images, does the matcher answer
correctly, refuse, or answer somebody else? The third column is the one that matters
-- a wrong answer is a household member handed another's records.
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
    embed_face,
    match_faceprint,
    warm_face_models,
)


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp"}


def _load(path: Path) -> object | None:
    """Read one image, or None. Imported here so the module stays importable without cv2."""
    import cv2

    return cv2.imread(str(path))


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
            vector = embed_face(image)
            if vector is None:
                print(f"  no single confident face, skipped: {label} image {index}")
                continue
            vectors.append(vector)
        if len(vectors) >= 2:
            people[label] = vectors
        elif vectors:
            print(f"  {label}: only one usable image, needs at least two -- skipped")

    if len(people) < 2:
        print("need at least two people with two usable images each")
        return 1

    print(f"\n{len(people)} people, {sum(len(v) for v in people.values())} usable images")
    print(f"floor={FACE_MATCH_SIMILARITY_FLOOR}  margin={FACE_MATCH_MARGIN}\n")

    tally: Counter[str] = Counter()
    for person, vectors in people.items():
        for index, candidate in enumerate(vectors):
            # Enrol everyone; for THIS person use their other images, so a probe is
            # never matched against itself.
            enrolled = [
                EnrolledFaceprint(
                    learner_id=other,
                    embedding_model=EMBEDDING_MODEL_ID,
                    dimension=len(other_vectors[0]),
                    vector=other_vectors[0] if other != person else vectors[(index + 1) % len(vectors)],
                )
                for other, other_vectors in people.items()
            ]
            outcome = match_faceprint(candidate, enrolled, embedding_model=EMBEDDING_MODEL_ID)
            if outcome.matched and outcome.learner_id == person:
                tally["correct"] += 1
            elif outcome.matched:
                tally["WRONG PERSON"] += 1
            else:
                tally[f"refused ({outcome.reason})"] += 1

    total = sum(tally.values())
    print(f"{'outcome':34} {'count':>6} {'share':>8}")
    for label, count in tally.most_common():
        print(f"{label:34} {count:>6} {count / total:>7.1%}")
    print()
    if tally["WRONG PERSON"]:
        print("A WRONG PERSON result is a false accept: one household member answered as another.")
        print("Raise FACE_MATCH_SIMILARITY_FLOOR or FACE_MATCH_MARGIN until this column is zero,")
        print("then record this table beside the constant. A refusal is the acceptable error.")
    else:
        print("No false accepts over this set. Record the table beside the constant, with the")
        print("set's size -- 'zero false accepts over N images of M people' is the claim, not")
        print("'the threshold is correct'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
