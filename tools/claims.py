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
SPLITS = Path("config/Release_eu/splits.txt")


def load_splits(path):
    """Map a unit's matching.txt spelling to the sections splits.txt gives it.

    splits.txt is what decides whether a unit still has an anonymous auto_
    object behind it: any retail address range left out of splits.txt stays an
    auto_*.o, and claiming the unit then collides with that object at link time.
    So a unit is only claimable once every one of its sections has a range here.
    """
    out = {}
    key = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        if not line[0].isspace():
            key = line.strip().rstrip(":")
            out.setdefault(key, set())
        elif key:
            sect = line.strip().split()[0]
            out[key].add(sect)
    return out


def unit_name(stem):
    """Turn a map stem into the matching.txt / splits.txt spelling, if unique."""
    return stem


def resolve(m, kind):
    """Percentage of a unit's code or data bytes that are complete, 0..100.

    objdiff only writes complete_<kind>_percent for a section that still has
    unmatched bytes, so the key disappearing means complete rather than empty.
    matched_<kind> is a fuzzy score, but when it equals total_<kind> every byte
    of the section landed inside a matched range, which is the last resort.
    """
    total = int(m.get("total_" + kind, 0) or 0)
    if "complete_" + kind in m and total:
        return 100.0 * int(m["complete_" + kind] or 0) / total
    pct = m.get("complete_%s_percent" % kind)
    if pct is not None:
        return float(pct)
    if total and int(m.get("matched_" + kind, 0) or 0) == total:
        return 100.0
    return 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--free", action="store_true", help="only units already fully matched")
    ap.add_argument("--unit", help="show one unit")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--emit", action="store_true",
                    help="print the matching.txt spelling instead of the table")
    ap.add_argument("--ready", action="store_true",
                    help="with --emit, only units splits.txt fully covers")
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
    sp = a.root / SPLITS
    splits = load_splits(sp) if sp.is_file() else {}

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
            # objdiff's complete_* keys are optional: it drops them entirely for a
            # unit whose section is fully matched, and it never emits total_code
            # for an object that contributes no code at all. Reading a missing
            # percentage as 0 misreports both ends of the queue -- a data-only
            # object looks like 100% code over zero bytes, and a fully matched
            # object looks like 0%. Resolve each side in the same order:
            # the complete_ byte count over the total, objdiff's own percentage,
            # then matched_* equal to total_* as evidence the whole section hit.
            code = resolve(m, "code")
            data = resolve(m, "data")
            code_bytes = int(m.get("total_code", 0) or 0)
            data_bytes = int(m.get("total_data", 0) or 0)
            if data and not code:
                kind = "data-only object, needs code first"
            elif code and not data:
                kind = "code-complete, data sections short"
            elif code and data:
                kind = "every section complete"
            else:
                kind = "no code and no data bytes matched"
            rows.append({
                "name": name,
                # complete_* is the honest gate: objdiff only reports a section as
                # complete when every byte of it matches, while matched_* is a
                # fuzzy score that also credits near-misses. A unit may only go
                # into matching.txt on complete_code == complete_data == 100.
                "code": code,
                "data": data,
                "fuzzy": float(m.get("matched_code_percent", 0.0)),
                "fns": float(m.get("matched_functions_percent", 0.0)),
                "code_bytes": code_bytes,
                "data_bytes": data_bytes,
                "kind": kind,
                "missing": missing_sections(u, splits.get(path({
                    "source": meta.get("source_path", ""),
                }))),
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
    dataclass = sum(1 for r in rows if r["code_bytes"] == 0 and r["data"] >= 99.99)
    print("%d configured units are cut out of the DOL but not in matching.txt" % len(rows))
    # Being complete is not the whole gate: the unit must also have no auto_
    # object left behind, or matching.txt collides with it at link time.
    ready = [r for r in rows if r["code"] >= 99.99 and r["data"] >= 99.99
             and not r["missing"]]
    print("  %d complete every section AND no auto_ range left: ready to claim"
          % len(ready))
    print("  %d more are complete but splits.txt leaves an auto_ range behind"
          % (free - len(ready)))
    print("  %d contribute no code at all: data-only objects, they need code work first" % dataclass)
    print("  gate is code and data both complete AND every section in splits.txt")
    print()
    if a.emit:
        # matching.txt spells a unit by its path inside the library, so drop the
        # repository root that objdiff's source_path still carries.
        for r in sel:
            if a.ready and r["missing"]:
                continue
            print(path(r))
        return 0
    print("%-46s %7s %7s  %-18s %s" % ("unit", "code%", "data%", "no range in splits.txt", "source"))
    for r in sel[:a.limit]:
        print("%-46s %7.2f %7.2f  %-18s %s"
              % (r["name"][:46], r["code"], r["data"],
                 ",".join(r["missing"]) or "-", r["source"]))
    return 0


def path(row):
    """The matching.txt spelling of a unit, taken from its real source path."""
    src = row["source"].replace("\\", "/")
    for root in ("src/", "libs/"):
        if src.startswith(root):
            src = src[len(root):]
            break
    return src


def missing_sections(unit, given):
    """Sections of a unit that splits.txt does not yet give an address range.

    Non-empty means the unit still has an anonymous auto_ object covering those
    ranges, so adding it to matching.txt collides with that object at link time.
    """
    have = given or set()
    return sorted(s["name"] for s in unit.get("sections", [])
                  if s["name"] not in have)


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
