#!/usr/bin/env python3
"""Locate a source file's retail address range from the disc's link map.

The retail EU DOL is stripped, so a unit that has no split yet appears in the
build as one anonymous auto_<section>_<addr> object. This script finds it.

The disc ships sys/files/debugInfoS.MAP, a Metrowerks link map of the JP build.
Its addresses do not describe the retail DOL, but it lists, per input object and
in link order, that object's functions with their exact sizes. For code the two
builds share, that size sequence is a signature: scanning the retail DOL's own
function list (from config/Release_eu/symbols.txt) for the same sequence finds
the retail address, which is what splits.txt needs.

    python tools/find_range.py GeographyObj
    python tools/find_range.py GeographyObj --emit

A JP/EU code difference shows up as a shorter match; --min-len reports how many
consecutive functions agreed, so a weak hit is visible instead of silent.
"""
import argparse
import difflib
import re
from collections import defaultdict
from pathlib import Path

ROW = re.compile(r"^\s+([0-9a-f]{4,8})\s+([0-9a-f]{4,8})\s+([0-9a-f]{6,8})\s+(\d+)\s+(\S+)(?:\s+(.*?))?\s*$")
SEC = re.compile(r"^(\.[\w.]+) section layout\s*$")
SYM = re.compile(r"^(\S+) = (\.[\w.]+):0x([0-9A-Fa-f]+); // type:(\w+) size:0x([0-9A-Fa-f]+)")


def map_objects(map_path):
    """{object stem: [(name, size)]} for .text functions, in link order."""
    out = defaultdict(list)
    section = None
    with map_path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            m = SEC.match(line)
            if m:
                section = m.group(1)
                continue
            if section != ".text":
                continue
            m = ROW.match(line)
            if not m or int(m.group(4)) != 4:
                continue
            size = int(m.group(2), 16)
            if size == 0:
                continue
            obj = (m.group(6) or "").strip()
            if not obj:
                continue
            name = obj.split()[-1]
            for ext in (".o", ".obj", ".cpp", ".cp", ".c", ".C", ".s"):
                if name.endswith(ext):
                    name = name[: -len(ext)]
                    break
            out[name].append((m.group(5), size))
    return out


def retail_functions(sym_path):
    out = []
    for line in sym_path.read_text(encoding="utf-8").splitlines():
        m = SYM.match(line.strip())
        if m and m.group(2) in (".text", ".init") and m.group(4) == "function":
            out.append((int(m.group(3), 16), int(m.group(5), 16), m.group(1)))
    out.sort()
    return out


def best_run(hay, needle):
    """Longest run of consecutive needle sizes found in hay (sizes only)."""
    sm = difflib.SequenceMatcher(a=[s for _, s, _ in hay],
                                 b=[s for _, s in needle], autojunk=False)
    blocks = [b for b in sm.get_matching_blocks() if b.size]
    if not blocks:
        return 0, None
    b = max(blocks, key=lambda x: x.size)
    return b.size, b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name", help="source stem as it appears in the map, e.g. GeographyObj")
    ap.add_argument("--root", type=Path, default=Path("."))
    ap.add_argument("--min-len", type=int, default=4,
                    help="minimum consecutive functions that must match (default 4)")
    ap.add_argument("--emit", action="store_true")
    args = ap.parse_args()

    root = args.root
    objs = map_objects(root / "orig/Release_eu/sys/files/debugInfoS.MAP")
    retail = retail_functions(root / "config/Release_eu/symbols.txt")

    keys = [args.name, args.name + ".cpp"]
    stem = args.name
    cand = objs.get(stem)
    if cand is None:
        hits = [k for k in objs if k.lower() == args.name.lower()]
        if not hits:
            print("no map object named %s" % args.name)
            return 1
        stem = hits[0]
        cand = objs[stem]

    print("map object %s: %d functions, %d bytes of .text" % (stem, len(cand), sum(s for _, s in cand)))
    run, block = best_run(retail, cand)
    if not block or run < args.min_len:
        print("no run of >=%d consecutive functions found (longest was %d)"
              % (args.min_len, run))
        return 1

    start_addr = retail[block.a][0]
    end_addr = retail[block.a + block.size - 1][0] + retail[block.a + block.size - 1][1]
    print("longest matching run: %d of %d functions" % (run, len(cand)))
    print("retail .text 0x%08X - 0x%08X  (%d bytes)" % (start_addr, end_addr, end_addr - start_addr))
    print("\n%-12s %-7s %-7s %s" % ("map name", "map sz", "retail", "retail name"))
    for k in range(-1, block.size + 1):
        i = block.a + k
        j = block.b + k
        if 0 <= i < len(retail) and 0 <= j < len(cand):
            print("0x%08X   %6d   %6d  %-42s %s"
                  % (retail[i][0], cand[j][1], retail[i][1], cand[j][0], retail[i][2]))
        elif 0 <= j < len(cand):
            print("%-30s %6d   %-6s  (not in the matched run)" % (cand[j][0], cand[j][1], "-"))
        elif 0 <= i < len(retail):
            print("%-30s %-6s   %6d  %s" % ("(no map symbol)", "-", retail[i][1], retail[i][2]))

    if args.emit:
        print("\n%s.cpp:\n\t.text start:0x%08X end:0x%08X" % (stem, start_addr, end_addr))
    return 0


main()
