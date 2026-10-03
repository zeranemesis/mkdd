#!/usr/bin/env python3
"""List dtk's auto-generated (unattributed) objects with their address extents,
so gaps in a section can be located for a unit that wants to claim one."""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CFG = json.loads((ROOT / "build/Release_eu/config.json").read_text(encoding="utf-8"))

# auto object names look like auto_<sectionidx>_<addr>_<section>
pat = re.compile(r"^auto_(\d+)_([0-9A-Fa-f]+)_(.+)$")

by_sec = {}
for u in CFG["units"]:
    name = u["name"]
    m = pat.match(name)
    if not m:
        continue
    by_sec.setdefault(m.group(3), []).append((int(m.group(2), 16), u["code_size"], u["data_size"]))

want = sys.argv[1] if len(sys.argv) > 1 else None
for sec, rows in sorted(by_sec.items()):
    if want and sec != want:
        continue
    rows.sort()
    print("=== %s : %d auto objects ===" % (sec, len(rows)))
    prev_end = None
    for addr, code, data in rows:
        size = code or data
        if prev_end is not None and addr > prev_end:
            print("   HOLE 0x%08X .. 0x%08X  (%d bytes)" % (prev_end, addr, addr - prev_end))
        print("   0x%08X +%d  (%d)" % (addr, size, size))
        prev_end = addr + size