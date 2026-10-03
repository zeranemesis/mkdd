#!/usr/bin/env python3
"""Report which claimed units the DOL actually links from our own source.

objdiff scores a unit against the retail object whether or not the DOL uses it,
so a 100% unit can be pure progress on paper and contribute nothing to the DOL.
The decomp toolchain keeps two object trees under build/Release_eu: src\\ holds
what the compiler made from our C++, obj\\ holds what dtk reconstructed from the
retail DOL. main.elf's inputs say which one each unit came from.

Usage:
    python tools/claimed_from_src.py          # every unit in matching.txt
    python tools/claimed_from_src.py --all    # every linked object
"""

import argparse
import re
import sys
from pathlib import Path

NINJA = Path("build.ninja")
MATCHING = Path("config/Release_eu/matching.txt")
LINK_RULE = "build build\\Release_eu\\main.elf:"
OBJECT = re.compile(r"^\s*build\\Release_eu\\(src|obj)\\(.*?\.o)\s*\$?\s*$")


def link_inputs():
    """Return (ours, theirs): object paths linked from src/ and from obj/."""
    lines = NINJA.read_text(encoding="utf-8").splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.startswith(LINK_RULE))
    except StopIteration:
        sys.exit("no %s rule in build.ninja: run configure.py first" % LINK_RULE)
    ours, theirs = [], []
    for ln in lines[start + 1:]:
        if ln.startswith("build ") and "main.elf" not in ln:
            break
        m = OBJECT.match(ln)
        if m:
            (ours if m.group(1) == "src" else theirs).append(
                m.group(2).replace("\\", "/"))
    return ours, theirs


def stem(obj):
    return obj.rsplit("/", 1)[-1][:-2]


def claimed_units():
    return [ln.strip() for ln in MATCHING.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true",
                    help="list every linked object instead of only the claims")
    a = ap.parse_args()

    ours, theirs = link_inputs()
    our_stems = {stem(o) for o in ours}
    dtk_stems = {stem(o) for o in theirs}
    print("%d objects linked from src/ (compiled from our source)" % len(our_stems))
    print("%d objects linked from obj/ (dtk's copy of the retail bytes)\n"
          % len(dtk_stems))

    if a.all:
        for o in sorted(ours):
            print("  ours  %s" % o)
        return 0

    # A unit's source stem is its file name without the extension; matching.txt
    # spells it with that extension, and libs/ units repeat the class name.
    bad = 0
    for unit in claimed_units():
        base = unit.rsplit("/", 1)[-1]
        for cand in (base, base.rsplit(".", 1)[0]):
            if cand in our_stems:
                break
        else:
            bad += 1
            print("  %-56s dtk object: %s" % (unit, base.rsplit(".", 1)[0] in dtk_stems))
    print("\n%d of %d claimed units are linked from dtk, not from our source"
          % (bad, len(claimed_units())))
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)