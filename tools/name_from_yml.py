#!/usr/bin/env python3
"""Fill the retail DOL's stripped placeholder names from config/<version>/symbols.yml.

Why: the retail PAL DOL is stripped, so config/Release_eu/symbols.txt still calls
4710 of its functions fn_<addr> and 10624 data symbols lbl_<addr>. dtk names the
target object's sections from symbols.txt, and objdiff pairs functions by symbol
name, so every unit that is already byte-correct reports a low percentage and the
link reports "undefined symbol" for the names other units call. symbols.yml is the
project's hand-maintained PAL name list; it already carries 5093 real names, and
where it and symbols.txt both name an address they agree 3358 times out of 3388
(99.11%), so it is a far better source than the JP link map.

Safety rules:
  - an address symbols.txt already names is never touched, so the hand-verified
    names and the current link cannot regress;
  - a name that already exists elsewhere in symbols.txt, or that symbols.yml
    itself lists at two addresses, is skipped and reported. Both would give the
    linker two definitions of one name: symbols.yml carries a few duplicated
    destructor entries (0x80018df0 and 0x8007a984 are both
    __dt__13J2DOrthoGraphFv) and writing either one makes the link fail with
    "multiply-defined";
  - only "  0xADDR: name" mapping lines are read, so the build log that has been
    appended to symbols.yml cannot inject a name;
  - the symbol's section, type, size, scope and comment are preserved verbatim;
    nothing is added, removed, moved or reordered, and the file keeps its order.

Usage:
  python tools/name_from_yml.py --check     # agreement report, no writes
  python tools/name_from_yml.py --stats     # how many names would land
  python tools/name_from_yml.py --write     # rewrite config/Release_eu/symbols.txt
"""
import argparse
import re
from collections import Counter
from pathlib import Path

SYM = re.compile(r"^(\S+) = (\.[\w.]+):0x([0-9A-Fa-f]+); // type:(\w+) size:0x([0-9A-Fa-f]+)(.*)$")
YML = re.compile(r"^  (0x[0-9a-f]{8}): ([^\s#]+)(?: #.*)?$")

LO, HI = 0x80000000, 0x80420000
PLACEHOLDER = ("fn_", "lbl_", "@")


def load_yml(path):
    names, junk = {}, 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("  0x") and YML.match(line) is None:
            junk += 1
            continue
        m = YML.match(line)
        if not m:
            continue
        addr = int(m.group(1), 16)
        if not (LO <= addr < HI):
            continue
        names.setdefault(addr, m.group(2))
    tally = Counter(names.values())
    dupe = {n for n, c in tally.items() if c > 1}
    return names, junk, dupe


def load_txt(path):
    out = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        m = SYM.match(line.strip())
        if m:
            out.append((int(m.group(3), 16), m.group(1)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path("."))
    ap.add_argument("--version", default="Release_eu")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    root = args.root
    sym_path = root / ("config/%s/symbols.txt" % args.version)
    yml_path = root / ("config/%s/symbols.yml" % args.version)

    yml, junk, yml_dupe = load_yml(yml_path)
    rows = load_txt(sym_path)
    at = {addr: name for addr, name in rows}
    counts = Counter(name for _, name in rows)

    agree, disagree = 0, []
    for addr, name in rows:
        if addr in yml and not name.startswith(PLACEHOLDER):
            if yml[addr] == name:
                agree += 1
            else:
                disagree.append((addr, name, yml[addr]))
    print("symbols.txt symbols: %d (%d placeholders)"
          % (len(rows), sum(1 for _, n in rows if n.startswith(PLACEHOLDER))))
    print("symbols.yml names:   %d  (%d rejected mapping-like lines)"
          % (len(yml), junk))
    print("both files name the address: agree=%d disagree=%d (%.2f%%)"
          % (agree, len(disagree), 100.0 * agree / max(1, agree + len(disagree))))

    upgrades = [(a, at[a], yml[a]) for a in yml
                if a in at and at[a].startswith(PLACEHOLDER) and not yml[a].startswith(PLACEHOLDER)]
    blocked = [u for u in upgrades if counts[u[2]] > 0 or u[2] in yml_dupe]
    free = [u for u in upgrades if counts[u[2]] == 0 and u[2] not in yml_dupe]

    print("\nnames available for a placeholder address: %d (blocked: %d)"
          % (len(upgrades), len(blocked)))
    for addr, old, new in blocked:
        why = "already used elsewhere" if counts[new] > 0 else "duplicated in symbols.yml"
        print("   0x%08X %-12s -> %s (%s)" % (addr, old, new, why))

    secs = Counter()
    for line in sym_path.read_text(encoding="utf-8").splitlines():
        m = SYM.match(line.strip())
        if m and int(m.group(3), 16) in {a for a, _, _ in free}:
            secs[m.group(2)] += 1
    print("   by section: %s" % dict(secs))
    for addr, old, new in free[:15]:
        print("   0x%08X %-12s -> %s" % (addr, old, new))
    if len(free) > 15:
        print("   ... %d more" % (len(free) - 15))

    if args.check:
        if disagree:
            print("\ndisagreements (left untouched):")
            for addr, old, new in disagree:
                print("   0x%08X txt=%-46s yml=%s" % (addr, old, new))
        return 0

    if args.stats:
        return 0

    if args.write:
        rename = {a: n for a, _, n in free}
        out, applied = [], 0
        for line in sym_path.read_text(encoding="utf-8").splitlines():
            m = SYM.match(line.strip())
            if m and int(m.group(3), 16) in rename:
                out.append(SYM.sub(lambda mm: "%s = %s:0x%08X; // type:%s size:0x%s%s"
                                   % (rename[int(mm.group(3), 16)], mm.group(2),
                                      int(mm.group(3), 16), mm.group(4), mm.group(5), mm.group(6)),
                                   line.strip()))
                applied += 1
                continue
            out.append(line.rstrip())
        sym_path.write_text("\n".join(out) + "\n", encoding="utf-8")
        print("renamed %d symbols in %s" % (applied, sym_path))
    return 0


main()