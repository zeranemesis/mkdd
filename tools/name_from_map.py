#!/usr/bin/env python3
"""Fill the stripped retail DOL's function names from the disc's JP link map.

Why: the retail EU DOL (GM4P01) is stripped, so config/Release_eu/symbols.txt
calls all 13434 of its functions fn_<addr>. objdiff pairs functions by symbol
name, so every unit reports 0% until those placeholders are renamed, and the
link fails with "undefined symbol" as soon as a unit that references them is
compiled. The disc still ships sys/files/debugInfoS.MAP, a Metrowerks link map
of the JP build: its addresses do not match the retail DOL, but its function
*order* (link order) and *sizes* do, for the code the two builds share. Aligning
the two sequences by size therefore transfers real mangled names onto retail
addresses.

Safety rules:
  - an address that already has a real name is never touched, so the 1212
    hand-verified names and the current link cannot regress;
  - only proposals inside a matching block of at least MIN_BLOCK consecutive
    functions are accepted (isolated size coincidences are where the errors are);
  - linker/compiler placeholder names (@332@..., ...) are skipped, their
    numbering is build specific;
  - a name that would land on two retail addresses is dropped entirely;
  - everything is reported, nothing is silent.

Usage:
  python tools/name_from_map.py --check          # accuracy on known names
  python tools/name_from_map.py --stats          # coverage, no writes
  python tools/name_from_map.py --write          # rewrite config/Release_eu/symbols.txt
"""
import argparse
import difflib
import re
from collections import Counter
from pathlib import Path

ROW = re.compile(r"^\s+([0-9a-f]{4,8})\s+([0-9a-f]{4,8})\s+([0-9a-f]{6,8})\s+(\d+)\s+(\S+)(?:\s+(.*?))?\s*$")
SEC = re.compile(r"^(\.[\w.]+) section layout\s*$")
SYM = re.compile(r"^(\S+) = (\.[\w.]+):0x([0-9A-Fa-f]+); // type:function size:0x([0-9A-Fa-f]+)")

MIN_BLOCK = 2


def map_text_functions(map_path):
    seq, section = [], None
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
            if size:
                seq.append((int(m.group(3), 16), size, m.group(5)))
    seq.sort()
    return seq


def retail_functions(sym_path):
    out = []
    for line in sym_path.read_text(encoding="utf-8").splitlines():
        m = SYM.match(line.strip())
        if m:
            out.append((int(m.group(3), 16), int(m.group(4), 16), m.group(1)))
    out.sort()
    return out


def usable(name):
    if not name or name.startswith("@") or name.startswith("..."):
        return False
    if name in ("_stack_addr", "_SDA_BASE_", "_SDA2_BASE_"):
        return False
    return True


def proposals(retail, mseq, min_block=MIN_BLOCK, taken=()):
    sm = difflib.SequenceMatcher(a=[r[1] for r in retail], b=[m[1] for m in mseq], autojunk=False)
    pairs = []
    for b in sm.get_matching_blocks():
        if b.size < min_block:
            continue
        for k in range(b.size):
            pairs.append((b.a + k, b.b + k))
    # A weak symbol emitted by several translation units appears once per unit
    # in the map. The linker keeps the first one in link order and discards the
    # rest, and the map lists objects in link order, so the first occurrence is
    # the retail copy. Keep it, drop the later duplicates.
    first = {}
    for j, m in enumerate(mseq):
        first.setdefault(m[2], j)
    return [(i, j) for i, j in pairs if first[mseq[j][2]] == j and mseq[j][2] not in taken]


def map_section_rows(map_path, sections):
    """{section: [(addr, size, name)]} in layout order, data/rodata rows only."""
    out = {s: [] for s in sections}
    section = None
    with map_path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            m = SEC.match(line)
            if m:
                section = m.group(1)
                continue
            if section not in out:
                continue
            m = ROW.match(line)
            if not m:
                continue
            kind = int(m.group(4))
            if kind == 1:                      # the section row itself
                continue
            size = int(m.group(2), 16)
            if size == 0:
                continue
            name = m.group(5)
            if not usable(name):
                continue
            out[section].append((int(m.group(3), 16), size, name))
    for s in out:
        out[s].sort()
    return out


DATA_SECTIONS = (".rodata", ".data", ".sdata", ".sbss", ".sdata2", ".sbss2", ".bss")
SYM_ANY = re.compile(r"^(\S+) = (\.[\w.]+):0x([0-9A-Fa-f]+); // type:(\w+) size:0x([0-9A-Fa-f]+)(.*)$")


def retail_by_section(sym_path):
    out = {}
    for line in sym_path.read_text(encoding="utf-8").splitlines():
        m = SYM_ANY.match(line.strip())
        if not m:
            continue
        out.setdefault(m.group(2), []).append(
            (int(m.group(3), 16), int(m.group(5), 16), m.group(1), (m.group(6) or "").strip()))
    for s in out:
        out[s].sort()
    return out


def data_proposals(root, min_block):
    mrows = map_section_rows(root / "orig/Release_eu/sys/files/debugInfoS.MAP", DATA_SECTIONS)
    rsym = retail_by_section(root / "config/Release_eu/symbols.txt")
    taken = set()
    for s, rows in rsym.items():
        for addr, size, name, _ in rows:
            if not name.startswith("lbl_"):
                taken.add(name)
    out = {}
    for s in DATA_SECTIONS:
        rows = mrows.get(s) or []
        syms = rsym.get(s) or []
        if not rows or not syms:
            continue
        sm = difflib.SequenceMatcher(a=[r[1] for r in syms], b=[r[1] for r in rows], autojunk=False)
        pairs = []
        for b in sm.get_matching_blocks():
            if b.size < min_block:
                continue
            for k in range(b.size):
                pairs.append((b.a + k, b.b + k))
        first = {}
        for j, r in enumerate(rows):
            first.setdefault(r[2], j)
        out[s] = [(syms[i], rows[j]) for i, j in pairs
                  if first[rows[j][2]] == j and rows[j][2] not in taken]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path("."))
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--data", action="store_true", help="operate on data sections")
    ap.add_argument("--min-block", type=int, default=MIN_BLOCK)
    args = ap.parse_args()

    root = args.root
    sym_path = root / "config/Release_eu/symbols.txt"
    if args.data:
        return data_main(root, sym_path, args)

    map_path = root / "orig/Release_eu/sys/files/debugInfoS.MAP"

    mseq = map_text_functions(map_path)
    retail = retail_functions(sym_path)
    # names already used at some other address: a proposal may not collide
    taken = set()
    for addr, size, name in retail:
        if not name.startswith("fn_"):
            taken.add(name)
    pairs = proposals(retail, mseq, args.min_block, taken)
    known = sum(1 for _, _, n in retail if not n.startswith("fn_"))
    print("retail functions: %d (%d already named), map functions: %d"
          % (len(retail), known, len(mseq)))
    print("proposals accepted: %d  (%.1f%% of the stripped ones)"
          % (len(pairs), 100.0 * len(pairs) / max(1, len(retail) - known)))

    if args.check:
        # measure alignment quality on the addresses the project already named;
        # the collision filter is skipped here so correct proposals are visible
        probe = proposals(retail, mseq, args.min_block)
        ok = bad = 0
        rows = []
        for i, j in probe:
            rname, mname = retail[i][2], mseq[j][2]
            if rname.startswith("fn_"):
                continue
            if rname == mname:
                ok += 1
            else:
                bad += 1
                rows.append((hex(retail[i][0]), rname, mname, hex(retail[i][1])))
        print("accuracy on already-named addresses: ok=%d bad=%d (%.2f%%)"
              % (ok, bad, 100.0 * ok / max(1, ok + bad)))
        for r in rows[:20]:
            print("   %s retail=%-44s map=%-44s size=%s" % r)
        return 0

    if args.stats:
        per_unit = Counter()
        return 0

    if args.write:
        out, applied = [], 0
        newname = {}
        for i, j in pairs:
            raddr, rsize, rname = retail[i]
            mname = mseq[j][2]
            if not rname.startswith("fn_") or not usable(mname):
                continue
            newname[raddr] = (mname, rsize)
        for line in sym_path.read_text(encoding="utf-8").splitlines():
            m = SYM.match(line.strip())
            if m:
                addr = int(m.group(3), 16)
                if addr in newname:
                    name, size = newname[addr]
                    out.append("%s = .text:0x%08X; // type:function size:0x%X" % (name, addr, size))
                    applied += 1
                    continue
            out.append(line.rstrip())
        sym_path.write_text("\n".join(out) + "\n", encoding="utf-8")
        print("renamed %d symbols in %s" % (applied, sym_path))
    return 0


def data_main(root, sym_path, args):
    prop = data_proposals(root, args.min_block)
    if args.check:
        ok = bad = total = 0
        rows = []
        for s, pairs in prop.items():
            for (raddr, rsize, rname, rtail), (maddr, msize, mname) in pairs:
                total += 1
                if rname.startswith("lbl_"):
                    continue
                if rname == mname:
                    ok += 1
                else:
                    bad += 1
                    rows.append((s, hex(raddr), rname, mname))
        print("data proposals: %d" % total)
        print("accuracy on already-named: ok=%d bad=%d (%.2f%%)"
              % (ok, bad, 100.0 * ok / max(1, ok + bad)))
        for r in rows[:20]:
            print("   %-8s %s retail=%-40s map=%s" % r)
        return 0
    if args.stats:
        for s, pairs in prop.items():
            print("%-9s %6d proposals" % (s, len(pairs)))
        return 0
    if args.write:
        newname = {}
        for s, pairs in prop.items():
            for (raddr, rsize, rname, rtail), (maddr, msize, mname) in pairs:
                if rname.startswith("lbl_"):
                    continue
                newname[(s, raddr)] = (mname, rsize, rtail)
        out, applied = [], 0
        for line in sym_path.read_text(encoding="utf-8").splitlines():
            m = SYM_ANY.match(line.strip())
            if m:
                key = (m.group(2), int(m.group(3), 16))
                if key in newname:
                    name, size, tail = newname[key]
                    out.append("%s = %s:0x%08X; // type:%s size:0x%X%s"
                               % (name, m.group(2), key[1], m.group(4), size, tail))
                    applied += 1
                    continue
            out.append(line.rstrip())
        sym_path.write_text("\n".join(out) + "\n", encoding="utf-8")
        print("renamed %d data symbols in %s" % (applied, sym_path))
    return 0


main()
