#!/usr/bin/env python3
"""Print the JP link map's contribution rows for one object, grouped by section."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEC = re.compile(r"^(\.[\w.]+) section layout\s*$")
ROW = re.compile(r"^\s+([0-9a-f]{4,8})\s+([0-9a-f]{4,8})\s+([0-9a-f]{6,8})\s+(\d+)\s+(\S+)(.*)$")

stem = sys.argv[1]
section = None
cur = {}
order = []
with (ROOT / "orig/Release_eu/sys/files/debugInfoS.MAP").open(
        encoding="utf-8", errors="replace") as f:
    for line in f:
        m = SEC.match(line)
        if m:
            section = m.group(1)
            continue
        if not section:
            continue
        m = ROW.match(line)
        if not m:
            continue
        obj = (m.group(6) or "").strip()
        if not obj:
            continue
        name = obj.split()[-1]
        for ext in (".o", ".obj", ".cpp", ".cp", ".c", ".C", ".s"):
            if name.endswith(ext):
                name = name[: -len(ext)]
                break
        if name != stem:
            continue
        key = section
        if key not in cur:
            cur[key] = [0, 0, []]
            order.append(key)
        cur[key][0] += int(m.group(2), 16)
        cur[key][1] += 1
        cur[key][2].append((int(m.group(1), 16), int(m.group(2), 16), m.group(5)))

for k in order:
    tot, cnt, rows = cur[k]
    print("=== %s : %d rows, %d bytes ===" % (k, cnt, tot))
    for addr, sz, nm in rows:
        print("   0x%08X +%d  %s" % (addr, sz, nm))