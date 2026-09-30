#!/usr/bin/env python3
"""
Extract per-source-file section ranges from a Metrowerks link map.

The retail DOL ships its own link map (sys/files/debugInfoS.MAP). That map
records, for every input section, the virtual address range it occupies and the
object/source file it came from. That is exactly the information splits.txt
needs, so this script turns the map into ready-to-paste splits.txt entries.

It only emits ranges for source files the project does not already cover, and
it never splits a range in the middle of a gap, which keeps dtk from emitting
nonzero pad_ commons (see README, "Retail notes").

Usage:
    python tools/map_to_splits.py                       # list candidates
    python tools/map_to_splits.py --source JSystem/JUtility
    python tools/map_to_splits.py --all > splits_additions.txt
    python tools/map_to_splits.py --source X --emit     # splits.txt format
"""

import argparse
import re
import sys
from pathlib import Path

ROW = re.compile(
    r"^\s+([0-9a-f]{4,8})\s+([0-9a-f]{4,8})\s+([0-9a-f]{6,8})\s+(\d+)\s+(\S+)(?:\s+(.*?))?\s*$"
)
SECTION_LAYOUT = re.compile(r"^(\.[\w.]+) section layout\s*$")

# Sections whose ranges describe actual file contents in the DOL.
CONTENT_SECTIONS = (
    ".init", "extab", "extabindex", ".text", ".ctors", ".dtors",
    ".BINARY", ".rodata", ".data", ".bss", ".sdata", ".sbss",
    ".sdata2", ".sbss2",
)


def parse_map(path: Path):
    """Yield (section, addr, size, kind, symbol, object) for every map row."""
    section = None
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            m = SECTION_LAYOUT.match(line)
            if m:
                section = m.group(1)
                continue
            if section is None:
                continue
            m = ROW.match(line)
            if not m:
                continue
            addr = int(m.group(3), 16)
            size = int(m.group(2), 16)
            kind = m.group(4)
            symbol = m.group(5)
            obj = (m.group(6) or "").strip()
            if size == 0:
                continue
            yield section, addr, size, kind, symbol, obj


def source_of(obj: str):
    """
    Map an object field to a source path.

    Map rows name objects either as "file.o" or as "archive.a file.cpp".
    Only rows that name a section (kind 1) carry the owning object, so this is
    called with those.
    """
    if not obj:
        return None
    parts = obj.split()
    name = parts[-1]
    if name.startswith("auto_") or name.endswith(".a"):
        return None
    if name.endswith(".o"):
        name = name[:-2]
    for ext in (".cpp", ".cp", ".c", ".C", ".s"):
        if name.endswith(ext):
            return name[: -len(ext)]
    return None


def build_ranges(map_path: Path, splits_path: Path, symbols_path: Path):
    """Return {source: {section: (start, end)}} using only real content rows."""
    # Section rows (kind 1) give the owning source file for each address range.
    ranges = {}
    for section, addr, size, kind, symbol, obj in parse_map(map_path):
        if section not in CONTENT_SECTIONS or kind != "1":
            continue
        src = source_of(obj)
        if not src:
            continue
        per = ranges.setdefault(src, {})
        start, end = addr, addr + size
        if section in per:
            lo, hi = per[section]
            per[section] = (min(lo, start), max(hi, end))
        else:
            per[section] = (start, end)

    # Symbols that are file content but whose object is unnamed still need a
    # home; attach any symbol that falls inside an existing range is pointless,
    # so we only use rows above.
    return ranges


def existing_sources(splits_path: Path):
    if not splits_path.is_file():
        return set()
    text = splits_path.read_text(encoding="utf-8", errors="replace")
    return {m.replace("/", "\\") for m in re.findall(r"^(\S+):\s*$", text, re.M)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", type=Path,
                    default=Path("orig/Release_eu/sys/files/debugInfoS.MAP"))
    ap.add_argument("--splits", type=Path, default=Path("config/Release_eu/splits.txt"))
    ap.add_argument("--symbols", type=Path, default=Path("config/Release_eu/symbols.txt"))
    ap.add_argument("--source", action="append", default=[],
                    help="only report this source path (repeatable)")
    ap.add_argument("--all", action="store_true", help="include already-split sources")
    ap.add_argument("--emit", action="store_true", help="print splits.txt entries")
    ap.add_argument("--min-size", type=lambda v: int(v, 0), default=0x40,
                    help="skip sections smaller than this (default 0x40)")
    args = ap.parse_args()

    ranges = build_ranges(args.map, args.splits, args.symbols)
    have = existing_sources(args.splits)

    wanted = set(args.source)
    out = []
    for src in sorted(ranges):
        if src in have and not args.all and not wanted:
            continue
        if wanted and src not in wanted and src.replace("\\", "/") not in wanted:
            continue
        secs = []
        for sec in CONTENT_SECTIONS:
            if sec not in ranges[src]:
                continue
            start, end = ranges[src][sec]
            if end - start < args.min_size:
                continue
            secs.append((sec, start, end))
        if not secs:
            continue
        out.append((src, secs))

    if args.emit:
        for src, secs in out:
            print("%s:" % src)
            for sec, start, end in secs:
                print("\t%s start:0x%08X end:0x%08X" % (sec, start, end))
            print()
    else:
        print("%-56s %-52s %s" % ("source", "sections", "total bytes"))
        for src, secs in out:
            total = sum(e - s for _, s, e in secs)
            desc = " ".join("%s:0x%X-0x%X" % (sec, s, e) for sec, s, e in secs)
            print("%-56s %-52s %d" % (src, desc[:52], total))
        print("\n%d candidate sources" % len(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())