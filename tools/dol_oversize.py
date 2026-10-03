"""Why is our DOL bigger than the retail one?

A byte-for-byte match needs the same size first. This walks every section of
every object main.elf actually links from our source and compares its size with
the range splits.txt assigns to that unit. A positive delta is bytes we emit
that retail does not have at that address; a negative one means an auto object
is still filling in behind us.

Usage:
    python tools/dol_oversize.py            # every linked unit, worst first
    python tools/dol_oversize.py --section .text
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from claimed_from_src import link_inputs  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OBJ = ROOT / "build" / "Release_eu"
OBJDUMP = ROOT / "build" / "binutils" / "powerpc-eabi-objdump.exe"
CONFIG = ROOT / "build" / "Release_eu" / "config.json"


def split_ranges():
    """unit spelling -> {section: (start, end)} from splits.txt."""
    out, key = {}, None
    path = ROOT / "config" / "Release_eu" / "splits.txt"
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        if not line[0].isspace():
            key = line.strip().rstrip(":")
            out.setdefault(key, {})
        elif key:
            parts = line.strip().split()
            sect = parts[0]
            start = end = None
            for tok in parts[1:]:
                if tok.startswith("start:"):
                    start = int(tok.split(":")[1], 16)
                elif tok.startswith("end:"):
                    end = int(tok.split(":")[1], 16)
            if start is not None and end is not None:
                out[key][sect] = (start, end)
    return out


def section_sizes(obj):
    res = subprocess.run([str(OBJDUMP), "-h", str(obj)], stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True).stdout
    sizes = {}
    for line in res.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0].isdigit() and parts[1].startswith("."):
            sizes[parts[1]] = int(parts[2], 16)
    return sizes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--section", default=".text")
    a = ap.parse_args()

    ours, _ = link_inputs()
    ranges = split_ranges()
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    by_path = {}
    for u in cfg.get("units", []):
        obj = (u.get("object") or "").replace("\\", "/")
        if "/obj/" not in obj:
            continue
        by_path[obj.split("/obj/", 1)[1]] = u.get("name", "")

    rows = []
    for rel in ours:
        unit = by_path.get(rel)
        if unit is None:
            continue
        got = ranges.get(unit, {}).get(a.section)
        obj = OBJ / "src" / rel
        if not obj.is_file():
            obj = OBJ / rel
        if not obj.is_file():
            continue
        size = section_sizes(obj).get(a.section, 0)
        want = (got[1] - got[0]) if got else 0
        rows.append((size - want, size, want, unit, rel))

    rows.sort(reverse=True)
    over = [r for r in rows if r[0] > 0]
    under = [r for r in rows if r[0] < 0]
    print("%-52s %8s %8s %8s" % ("unit", "ours", "retail", "delta"))
    for d, size, want, unit, rel in rows:
        if d:
            print("%-52s %8d %8d %+8d" % (unit[:52], size, want, d))
    print("\n%s: %d units linked, %d over, %d under, net %+d bytes"
          % (a.section, len(rows), len(over), len(under),
             sum(r[0] for r in rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)