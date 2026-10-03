#!/usr/bin/env python3
"""Dump the JP link map's function list for one object, and the retail
function list over an address window, side by side. Diagnostic only."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ROW = re.compile(r"^\s+([0-9a-f]{4,8})\s+([0-9a-f]{4,8})\s+([0-9a-f]{6,8})\s+(\d+)\s+(\S+)(?:\s+(.*?))?\s*$")
SEC = re.compile(r"^(\.[\w.]+) section layout\s*$")
SYM = re.compile(r"^(\S+) = (\.[\w.]+):0x([0-9A-Fa-f]+); // type:(\w+) size:0x([0-9A-Fa-f]+)")


def map_objects(map_path):
    out = {}
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
            name = (m.group(6) or "").strip().split()[-1]
            for ext in (".o", ".obj", ".cpp", ".cp", ".c", ".C", ".s"):
                if name.endswith(ext):
                    name = name[: -len(ext)]
                    break
            out.setdefault(name, []).append((name and m.group(5), size, int(m.group(1), 16)))
    return out


def retail(sym_path):
    out = []
    for line in sym_path.read_text(encoding="utf-8").splitlines():
        m = SYM.match(line.strip())
        if m:
            out.append((m.group(1), m.group(2), int(m.group(3), 16),
                        m.group(4), int(m.group(5), 16)))
    out.sort(key=lambda r: r[2])
    return out


def main():
    stem = sys.argv[1]
    lo = int(sys.argv[2], 16) if len(sys.argv) > 2 else 0
    hi = int(sys.argv[3], 16) if len(sys.argv) > 3 else 0xFFFFFFFF

    objs = map_objects(ROOT / "orig/Release_eu/sys/files/debugInfoS.MAP")
    print("=== JP map object %s ===" % stem)
    cand = objs.get(stem, [])
    tot = 0
    for n, s, _a in cand:
        tot += s
        print("  map sz=%6d  %s" % (s, n))
    print("  --- %d functions, %d bytes" % (len(cand), tot))

    print("\n=== retail functions 0x%08X..0x%08X ===" % (lo, hi))
    for n, sec, a, t, s in retail(ROOT / "config/Release_eu/symbols.txt"):
        if lo <= a < hi:
            print("  0x%08X sz=%6d %-10s %s %s" % (a, s, sec, t, n))


main()