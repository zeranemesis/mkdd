#!/usr/bin/env python3
"""Find the cheapest claims left: units already cut out of the DOL, closest to done.

The work queue in tools/backlog.py answers "which unit should I attack next".
It cannot answer "which units are one step from being claimed", because the unit
has to be cut out of the DOL and rebuilt before anything can be measured about it.

That second question is the cheapest progress available, and the answer lives in
build/Release_eu/report.json: objdiff records, per unit, how many bytes and how
many functions already match, and whether the unit is fully matched. A unit can
be fully matched and still not be in config/Release_eu/matching.txt, because its
data sections were never claimed -- matching.txt gates on the whole unit, not on
just .text. Those units are pure paperwork plus whatever is missing in .data.

    python tools/claims.py                  # ranked, cheapest first
    python tools/claims.py --free           # only what is already 100%
    python tools/claims.py --unit SomeStem  # everything known about one unit
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT = Path("build/Release_eu/report.json")
MATCHING = Path("config/Release_eu/matching.txt")
SLICES = Path("config/Release_eu/dol_slices.yml")


def unit_name(stem):
    """Turn a map stem into the matching.txt / splits.txt spelling, if unique."""
    return stem


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--free", action="store_true", help="only units already fully matched")
    ap.add_argument("--unit", help="show one unit")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--root", type=Path, default=ROOT)
    a = ap.parse_args()

    report_path = a.root / REPORT
    if not report_path.is_file():
        print("no report at %s: run ninja first" % report_path, file=sys.stderr)
        return 1
    data = json.loads(report_path.read_text(encoding="utf-8"))

    claimed = set()
    mp = a.root / MATCHING
    if mp.is_file():
        for line in mp.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                claimed.add(line)

    rows = []
    for u in data["units"]:
        name = u["name"]
        if "/" not in name:
            continue
        m = u.get("measures", {})
        meta = u.get("metadata", {})
        if meta.get("auto_generated"):
            continue
        # matching.txt spells units without the module prefix and with the repo
        # path stripped of src/ or libs/
        for spelling in spellings(name):
            if spelling in claimed:
                break
        else:
            rows.append({
                "name": name,
                # complete_* is the honest gate: objdiff only reports a section as
                # complete when every byte of it matches, while matched_* is a
                # fuzzy score that also credits near-misses. A unit may only go
                # into matching.txt on complete_code == complete_data == 100.
                "code": float(m.get("complete_code_percent", 0.0)),
                "data": float(m.get("complete_data_percent", 0.0)),
                "fuzzy": float(m.get("matched_code_percent", 0.0)),
                "fns": float(m.get("matched_functions_percent", 0.0)),
                "source": meta.get("source_path", ""),
                "size": sum(int(s.get("size", 0)) for s in u.get("sections", [])),
            })
            continue
        continue

    rows.sort(key=lambda r: (-r["code"], -r["data"], r["name"]))

    if a.unit:
        found = False
        for u in data["units"]:
            if a.unit.lower() not in u["name"].lower():
                continue
            found = True
            print("unit %s" % u["name"])
            m = u.get("measures", {})
            for k in sorted(m):
                print("  %-28s %s" % (k, m[k]))
            for s in u.get("sections", []):
                print("  section %-12s size %-8s %s" % (s["name"], s.get("size"), s.get("metadata", {})))
            print("")
        if not found and a.root:
            for r in rows:
                if a.unit.lower() in r["name"].lower():
                    print("%-56s code %6.2f%%  data %6.2f%%  fns %6.2f%%  %s"
                          % (r["name"], r["code"], r["data"], r["fns"], r["source"]))
        return 0

    sel = [r for r in rows if r["code"] >= 99.99 and r["data"] >= 99.99] if a.free else rows
    free = sum(1 for r in rows if r["code"] >= 99.99 and r["data"] >= 99.99)
    code_only = sum(1 for r in rows if r["code"] >= 99.99 and r["data"] < 99.99)
    print("%d configured units are cut out of the DOL but not in matching.txt" % len(rows))
    print("  %d have every section complete: free claims, add them to matching.txt" % free)
    print("  %d more are code-complete and only their data sections are missing" % code_only)
    print("  gate is complete_code_percent == complete_data_percent == 100, not the fuzzy score\n")
    print("%-54s %7s %7s %7s  %s" % ("unit", "code%", "data%", "fns%", "source"))
    for r in sel[:a.limit]:
        print("%-54s %7.2f %7.2f %7.2f  %s"
              % (r["name"][:54], r["code"], r["data"], r["fns"], r["source"]))
    return 0


def spellings(name):
    """All the ways a unit name could appear in matching.txt.

    matching.txt spells a unit by its path inside the library, without the
    module prefix and without the repository root: report.json says
    "main/JSystem/JKernel/JKRHeap" and matching.txt says
    "JSystem/JKernel/JKRHeap.cpp". Extensions vary per language, so try them all
    rather than guess.
    """
    parts = name.split("/")
    stems = [name, "/".join(parts[1:])]
    if len(parts) > 2:
        stems.append("/".join(parts[2:]))
        stems.append(parts[-1])
    out = []
    for s in stems:
        out.append(s)
        for ext in (".cpp", ".c", ".C", ".cp"):
            out.append(s + ext)
    return out


if __name__ == "__main__":
    sys.exit(main() or 0)
