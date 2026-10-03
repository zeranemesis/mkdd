#!/usr/bin/env python3
"""Ranked work queue for the Release_eu decompilation.

Every retail DOL region that has no split yet shows up in the build as one
anonymous auto_<section>_<addr> object, so before anything can be decompiled we
have to know which source file owns which address range. The disc ships
sys/files/debugInfoS.MAP, a Metrowerks link map of the JP build: its addresses do
not describe the retail DOL, but it lists, per input object and in link order,
that object's .text functions with their exact sizes. For the code both builds
share, that size sequence is a signature.

tools/find_range.py already answers "where does <stem> live?" for one stem.
This script answers it for every stem at once and turns the answers into the work
queue: it joins the derived ranges with

  * config/Release_eu/dol_slices.yml   ranges already recorded by hand
  * config/Release_eu/splits.txt       units already split out of the DOL
  * config/Release_eu/matching.txt     units verified byte-exact in a full build
  * the source tree                    to turn a map stem into a real path

and reports, per unit, how confident the range is and what still blocks it.

    python tools/backlog.py                      # ranked queue
    python tools/backlog.py --unattributed       # no range known at all
    python tools/backlog.py --unit Course        # detail for one stem
    python tools/backlog.py --emit-splits Course # splits.txt block
    python tools/backlog.py --csv                # machine readable

A derived range is only a proposal: coverage is the fraction of the map object's
functions that lined up consecutively. coverage == 1.0 means the whole object
matched and the range is as reliable as the sizes are; lower values mean JP and
EU disagree on some functions, so the range still needs a human to trim it to
the functions that are actually identical. Never feed a low-coverage range into
splits.txt as-is: a split that does not match what the compiler emits breaks the
link, and a split that leaves a gap inflates .bss and breaks the DOL checksum.
"""
import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAP_REL = Path("orig/Release_eu/sys/files/debugInfoS.MAP")
SYMS_REL = Path("config/Release_eu/symbols.txt")
SLICES_REL = Path("config/Release_eu/dol_slices.yml")
SPLITS_REL = Path("config/Release_eu/splits.txt")
MATCHING_REL = Path("config/Release_eu/matching.txt")

ROW = re.compile(r"^\s+([0-9a-f]{4,8})\s+([0-9a-f]{4,8})\s+([0-9a-f]{6,8})\s+(\d+)\s+(\S+)(?:\s+(.*?))?\s*$")
SEC = re.compile(r"^(\.[\w.]+) section layout\s*$")
SYM = re.compile(r"^(\S+) = (\.[\w.]+):0x([0-9A-Fa-f]+); // type:(\w+) size:0x([0-9A-Fa-f]+)")
EXTS = (".o", ".obj", ".cpp", ".cp", ".c", ".C", ".s")
SRC_EXT = {".c", ".cpp", ".cp", ".C", ".s"}

# Source directories that are not linked into the retail DOL: the debug-only
# libraries. Keeping them out of the queue saves a third of the objects for
# nothing. Verified against config/Release_eu/dol_slices.yml, which contains no
# entry for any of them.
DEAD_PREFIXES = (
    "libs/dolphin/gx/",
    "libs/dolphin/ip/",
    "libs/dolphin/upnp/",
    "libs/dolphin/gd/",
    "libs/dolphin/lg/",
    "libs/dolphin/hio/",
    "libs/dolphin/OdemuExi2/",
    "libs/dolphin/odenotstub/",
    "libs/JSystem/Logitech/",
    "libs/runtime_libs/",
)


# --------------------------------------------------------------------------- #
# inputs
# --------------------------------------------------------------------------- #
def map_objects(map_path):
    """{stem: [(name, size)]} for .text functions, in link order."""
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
            for ext in EXTS:
                if name.endswith(ext):
                    name = name[: -len(ext)]
                    break
            out[name].append((m.group(5), size))
    return out


def retail_functions(sym_path):
    """[(addr, size, name)] for every .text/.init function, address-sorted."""
    out = []
    for line in sym_path.read_text(encoding="utf-8").splitlines():
        m = SYM.match(line.strip())
        if m and m.group(2) in (".text", ".init") and m.group(4) == "function":
            out.append((int(m.group(3), 16), int(m.group(5), 16), m.group(1)))
    out.sort()
    return out


def source_index(root):
    """{stem: [repo-relative paths]} for every C/C++/asm source in the tree."""
    idx = defaultdict(list)
    for top in ("src", "libs"):
        base = root / top
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.suffix in SRC_EXT and path.is_file():
                rel = path.relative_to(root).as_posix()
                idx[path.stem].append(rel)
    return idx


OBJ = re.compile(r'Object\(\s*[A-Za-z_]\w*\s*,\s*"([^"]+)"')


def configured_stems(root):
    """Stems of every object configure.py actually compiles.

    The source tree is not the queue: configure.py decides what gets compiled and
    linked, so a .cpp that exists but is not declared there can never be matched.
    Units like Yamamoto/kartvec.cpp live in the DOL only as a dtk sinit stub.
    """
    out = set()
    text = (root / "configure.py").read_text(encoding="utf-8", errors="replace")
    for m in OBJ.finditer(text):
        out.add(Path(m.group(1)).stem)
    return out


def slices_ranges(path):
    """{'src/X.cpp': {'.text': (start, end), ...}} from dol_slices.yml."""
    out = {}
    cur = None
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith((" ", "\t")) and line.rstrip().endswith(":"):
            cur = line.strip()[:-1].lstrip("#").strip()
            out.setdefault(cur, {})
            continue
        if cur is None:
            continue
        m = re.match(r"\s*(\.[\w.]+):\s*\[\s*0x([0-9a-fA-F]+)\s*,\s*0x([0-9a-fA-F]+)\s*\]", line)
        if m:
            out[cur][m.group(1)] = (int(m.group(2), 16), int(m.group(3), 16))
    return out


def splits_units(path):
    """{unit name: {section: (start, end)}} from splits.txt."""
    out = {}
    cur = None
    if not path.is_file():
        return out
    in_sections = True
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        if not line.startswith((" ", "\t")):
            in_sections = line.strip() == "Sections:"
            if not in_sections:
                cur = line.strip()
                out.setdefault(cur, {})
            continue
        if in_sections or cur is None:
            continue
        m = re.match(r"\s*(\.[\w.]+)\s+start:0x([0-9a-fA-F]+)\s+end:0x([0-9a-fA-F]+)", line)
        if m:
            out[cur][m.group(1)] = (int(m.group(2), 16), int(m.group(3), 16))
    return out


def matching_units(path):
    if not path.is_file():
        return set()
    out = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.add(line)
    return out


# --------------------------------------------------------------------------- #
# attribution
# --------------------------------------------------------------------------- #
class RetailIndex:
    """Anchor index over retail function sizes.

    A whole-file difflib pass costs O(retail x object) per object, which is far
    too slow over a few hundred objects. Instead we index every consecutive
    triple of retail function sizes and let each map object look up its own
    triples to get candidate anchors, then extend. Same answer, linear time.
    """

    def __init__(self, retail):
        self.retail = retail
        self.tri = defaultdict(list)
        self.pair = defaultdict(list)
        self.single = defaultdict(list)
        n = len(retail)
        for i in range(n):
            s = retail[i][1]
            self.single[s].append(i)
            if i + 1 < n:
                self.pair[(s, retail[i + 1][1])].append(i)
            if i + 2 < n:
                self.tri[(s, retail[i + 1][1], retail[i + 2][1])].append(i)

    def _extend(self, sizes, ai, bi):
        """Length of the run where map sizes[bi:] == retail sizes[ai:]."""
        n = len(self.retail)
        run = 0
        while bi + run < len(sizes) and ai + run < n and sizes[bi + run] == self.retail[ai + run][1]:
            run += 1
        return run

    def _extend_left(self, sizes, ai, bi):
        run = 0
        while bi - run - 1 >= 0 and ai - run - 1 >= 0 and sizes[bi - run - 1] == self.retail[ai - run - 1][1]:
            run += 1
        return run

    def best_run(self, sizes):
        """(run_length, retail_start_index) or (0, None)."""
        if not sizes:
            return 0, None
        anchors = {}
        for width in (3, 2, 1):
            for j in range(len(sizes) - width + 1):
                key = tuple(sizes[j:j + width])
                table = {3: self.tri, 2: self.pair, 1: self.single}[width]
                for i in table.get(key, ()):  # centre of the anchor
                    anchors.setdefault((i - j, j), 0)  # (retail_start, map_start)
            if anchors:
                break
        best = (0, None)
        for (ai, bi) in anchors:
            run = self._extend(sizes, ai, bi)
            if run > best[0]:
                best = (run, ai)
        if best[0] == 0:
            return 0, None
        ai = best[1]
        left = self._extend_left(sizes, ai, 0)
        return best[0] + left, ai - left


def attribute(objs, retail):
    """{stem: dict(start, end, run, total, coverage, funcs)}"""
    idx = RetailIndex(retail)
    out = {}
    for stem, funcs in objs.items():
        sizes = [s for _, s in funcs]
        run, ai = idx.best_run(sizes)
        total = sum(sizes)
        entry = {"run": run, "total_funcs": len(sizes), "map_bytes": total}
        if run and ai is not None:
            entry["start"] = retail[ai][0]
            last = min(ai + run - 1, len(retail) - 1)
            entry["end"] = retail[last][0] + retail[last][1]
            entry["bytes"] = entry["end"] - entry["start"]
            entry["coverage"] = run / len(sizes)
        else:
            entry.update({"start": 0, "end": 0, "bytes": 0, "coverage": 0.0})
        out[stem] = entry
    return out


def _clusters(addrs, gap=0x2000):
    """Split sorted addresses into clusters separated by more than `gap`."""
    out = []
    cur = []
    for a in sorted(addrs):
        if cur and a - cur[-1] > gap:
            out.append(cur)
            cur = []
        cur.append(a)
    if cur:
        out.append(cur)
    return out


def _clean(retail, name_to_stems, objs, rounds=4):
    """Resolve each retail function to one object, by name then by proximity.

    A weak or inline function is emitted by several objects, so a name alone is
    ambiguous; the JP and EU link orders also differ, so link order alone is not a
    reliable tie-break. What is reliable is locality: an object contributes one
    contiguous run of addresses to the DOL. So we assign by name, keep only each
    object's largest address cluster as authoritative, and hand the strays back
    to whichever other candidate object already owns code nearby.
    """
    n = len(retail)
    owners = [None] * n
    for i, (_addr, _size, name) in enumerate(retail):
        cands = name_to_stems.get(name)
        if cands:
            owners[i] = cands[0]

    for _ in range(rounds):
        owned = defaultdict(list)
        for (addr, _size, _name), stem in zip(retail, owners):
            if stem:
                owned[stem].append(addr)
        clusters = {}
        for stem, addrs in owned.items():
            cl = _clusters(addrs)
            clusters[stem] = max(cl, key=len) if cl else []

        moved = 0
        for i, (addr, _size, name) in enumerate(retail):
            cands = name_to_stems.get(name) or []
            keep = [s for s in cands if clusters.get(s) and min(
                abs(addr - a) for a in clusters[s]) < 0x400]
            if owners[i] in keep:
                continue
            if owners[i] is not None:
                moved += 1
            best, best_d = None, None
            for s in keep:
                d = min(abs(addr - a) for a in clusters[s])
                if best_d is None or d < best_d:
                    best, best_d = s, d
            owners[i] = best
        if not moved:
            break

    for i in range(n):
        if owners[i] is not None:
            continue
        prev = owners[i - 1] if i > 0 else None
        nxt = owners[i + 1] if i + 1 < n else None
        if prev is not None and prev == nxt:
            owners[i] = prev
    return owners


def attribute_by_name(retail, objs):
    """Exact attribution of retail functions to map objects, by symbol name.

    The size-sequence pass above can only confirm a range when JP and EU emitted
    identical code, which covers a small minority of the DOL. But 8 724 of the
    13 434 retail functions already carry their real Metrowerks name, because a
    previous run transferred them off the link map. The map says which object
    each of those names was compiled into, so name -> object attributes the bulk
    of the executable exactly, with no size guessing at all.

    Returns ({stem: dict}, {addr: stem}, stats).
    """
    name_to_stems = defaultdict(list)
    for stem, funcs in objs.items():
        for name, _size in funcs:
            if stem not in name_to_stems[name]:
                name_to_stems[name].append(stem)

    owners = _clean(retail, name_to_stems, objs)

    agg = defaultdict(lambda: {"addr": [], "named": 0, "inherited": 0})
    addr_owner = {}
    assigned = 0
    for (addr, size, name), stem in zip(retail, owners):
        if stem is None:
            continue
        e = agg[stem]
        e["addr"].append((addr, size))
        addr_owner[addr] = stem
        assigned += 1
        if name in name_to_stems and stem in name_to_stems[name]:
            e["named"] += 1
        else:
            e["inherited"] += 1

    out = {}
    for stem, e in agg.items():
        addrs = sorted(e["addr"])
        start = addrs[0][0]
        end = addrs[-1][0] + addrs[-1][1]
        span = end - start
        code = sum(s for _, s in addrs)
        out[stem] = {
            "start": start,
            "end": end,
            "span": span,
            "code": code,
            "bytes": span,
            "holes": span - code,
            "funcs": len(addrs),
            "named": e["named"],
            "inherited": e["inherited"],
            "coverage": 1.0,
            "run": len(addrs),
            "total_funcs": e["named"],
        }
    stats = {
        "retail": len(retail),
        "assigned": assigned,
        "unassigned": len(retail) - assigned,
    }
    return out, addr_owner, stats



# --------------------------------------------------------------------------- #
# queue
# --------------------------------------------------------------------------- #
def build_queue(root, mode="name"):
    retail = retail_functions(root / SYMS_REL)
    objs = map_objects(root / MAP_REL)
    if mode == "name":
        attr, addr_owner, stats = attribute_by_name(retail, objs)
    else:
        attr = attribute(objs, retail)
        addr_owner, stats = {}, {}
    srcs = source_index(root)
    slices = slices_ranges(root / SLICES_REL)
    splits = splits_units(root / SPLITS_REL)
    claimed = matching_units(root / MATCHING_REL)

    split_stems = {Path(u).stem: u for u in splits}
    claimed_stems = defaultdict(list)
    for u in claimed:
        claimed_stems[Path(u).stem].append(u)
    slice_stems = defaultdict(list)
    for u in slices:
        slice_stems[Path(u).stem].append(u)

    rec_by_path = {}
    rec_by_stem = defaultdict(list)
    for unit, secs in slices.items():
        if ".text" not in secs:
            continue
        lo, hi = secs[".text"]
        # dol_slices.yml keys are repo-relative, sometimes with a ./ or ../ and
        # sometimes without the src|libs prefix; normalise before matching on the
        # path, because two different files can share a stem (Course.cpp exists
        # both as src/Kaneshige/Course/Course.cpp and as a Kaneshige/Course unit)
        # and a stem-keyed lookup silently hands an agent the other file's range.
        norm = unit.replace("\\", "/").lstrip("./")
        if not norm.startswith(("src/", "libs/")):
            norm = "src/" + norm
        rec_by_path[norm] = (lo, hi)
        rec_by_path.setdefault(Path(norm).name, (lo, hi))
        rec_by_stem[Path(norm).stem].append((lo, hi))

    # The queue is indexed by source file, not by map object: an agent is handed
    # a file to decompile. A hand-recorded range in dol_slices.yml always wins
    # over a derived one -- --verify shows the derived range agrees with the
    # recorded one on 211 units exactly and is a strict subset on 54 more, but
    # still disagrees on a handful, so the recorded value is authoritative.
    rows = []
    configured = configured_stems(root)
    for stem, paths in sorted(srcs.items()):
        rel = paths[0]
        if any(rel.startswith(p) for p in DEAD_PREFIXES):
            continue
        if stem not in configured:
            continue
        rec = rec_by_path.get(rel)
        ambiguous = len(rec_by_stem.get(stem, [])) > 1 or len(paths) > 1
        der = attr.get(stem) if mode == "name" else attr.get(stem)
        if rec:
            start, end, source = rec[0], rec[1], "recorded"
        elif der and der.get("bytes"):
            start, end, source = der["start"], der["end"], "derived"
        else:
            start, end, source = 0, 0, "none"
        in_splits = stem in split_stems
        is_claimed = stem in claimed_stems
        rows.append({
            "stem": stem,
            "path": rel,
            "bytes": end - start,
            "start": start,
            "end": end,
            "run": (der or {}).get("run", 0),
            "funcs": (der or {}).get("funcs", 0),
            "coverage": (der or {}).get("coverage", 0.0),
            "in_slices": bool(rec),
            "in_splits": in_splits,
            "claimed": is_claimed,
            "dead": False,
            "ambiguous": ambiguous,
            "source": source,
            "status": ("claimed" if is_claimed else
                       "split" if in_splits else
                       "attributed" if start else "unattributed"),
        })
    rows.sort(key=lambda r: (r["status"] == "unattributed", -r["bytes"], r["stem"]))
    return rows, retail, objs, attr, addr_owner, stats


def unowned_regions(retail, addr_owner, slices):
    """Contiguous retail runs no map object claims, largest first.

    These are where the remaining un-decompiled code actually lives: not one
    source file, but the boundary regions between files, plus anything the JP map
    simply does not describe. Reporting them as byte ranges is what makes the tail
    of the campaign attackable.
    """
    skip = set()
    for sections in slices.values():
        for sec, (lo, hi) in sections.items():
            if sec == ".text":
                skip.add((lo, hi))
    owned = [(a, a + s) for a, s, _ in retail if a in addr_owner]
    regions = []
    i = 0
    n = len(retail)
    while i < n:
        addr, size, _name = retail[i]
        if addr in addr_owner:
            i += 1
            continue
        lo, hi = addr, addr + size
        j = i + 1
        while j < n and retail[j][0] not in addr_owner and retail[j][0] == hi:
            hi = retail[j][0] + retail[j][1]
            j += 1
        regions.append((lo, hi, j - i))
        i = j
    regions.sort(key=lambda r: r[0] - r[1])
    return regions



def fmt_int(n):
    return "{:,}".format(n)


def print_row(r):
    return "%-10s %-26s %-34s %8s %5s  0x%08X-0x%08X" % (
        r["status"][:10], r["stem"][:26], r["path"][:34], fmt_int(r["bytes"]),
        r["funcs"], r["start"], r["end"])


LEGEND = ("status: claimed=verified byte-exact in matching.txt  split=cut out of the DOL\n"
          "         attributed=range known, not cut yet  unattributed=no range at all\n\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--unit", help="show detail for one map stem")
    ap.add_argument("--emit-splits", metavar="STEM", help="print a splits.txt block for STEM")
    ap.add_argument("--unattributed", action="store_true", help="only units with no range at all")
    ap.add_argument("--verify", action="store_true",
                    help="check the derived ranges against the hand-recorded dol_slices.yml ones")
    ap.add_argument("--holes", action="store_true",
                    help="list contiguous retail runs no object claims, largest first")
    ap.add_argument("--mode", choices=("name", "size"), default="name",
                    help="attribution method (default: name, exact; size is the fuzzy fallback)")
    ap.add_argument("--min-bytes", type=lambda s: int(s, 0), default=0)
    ap.add_argument("--max-coverage", type=float, default=1.01,
                    help="hide ranges less aligned than this (default 1.01 = keep all)")
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--include-dead", action="store_true")
    ap.add_argument("--csv", action="store_true")
    args = ap.parse_args()

    rows, retail, objs, attr, addr_owner, stats = build_queue(args.root, args.mode)

    if args.verify:
        slices = slices_ranges(args.root / SLICES_REL)
        by_stem = defaultdict(list)
        for unit, secs in slices.items():
            if ".text" in secs:
                by_stem[Path(unit).stem].append((unit, secs[".text"]))
        exact = contain = overlap = missing = 0
        bad = []
        for stem, entries in sorted(by_stem.items()):
            e = attr.get(stem)
            if not e:
                missing += 1
                continue
            for unit, (lo, hi) in entries:
                ds, de = e["start"], e["end"]
                if ds == lo and de == hi:
                    exact += 1
                elif lo <= ds and de <= hi:
                    contain += 1
                elif ds < hi and lo < de:
                    overlap += 1
                    bad.append((stem, unit, "0x%08X-0x%08X" % (lo, hi),
                                "0x%08X-0x%08X" % (ds, de)))
                else:
                    bad.append((stem, unit, "0x%08X-0x%08X" % (lo, hi),
                                "0x%08X-0x%08X" % (ds, de)))
        total = exact + contain + len(bad)
        print("verification against %d hand-recorded .text ranges" % total)
        print("  exact            %4d" % exact)
        print("  derived inside   %4d" % contain)
        print("  disagreeing      %4d" % len(bad))
        print("  not attributable %4d" % missing)
        if bad:
            print("\n%-24s %-34s %-21s %s" % ("stem", "recorded", "derived", "verdict"))
            for stem, unit, rec, der in bad[:args.limit]:
                print("%-24s %-34s %-21s %s" % (stem[:24], unit[:34], rec, der))
        return 0

    if args.holes:
        regions = unowned_regions(retail, addr_owner, slices_ranges(args.root / SLICES_REL))
        total = sum(hi - lo for lo, hi, _ in regions)
        print("contiguous retail runs no map object claims: %d, %s bytes of .text\n"
              % (len(regions), fmt_int(total)))
        print("%-11s %-11s %8s %6s  %s" % ("start", "end", "bytes", "funcs", "first symbol"))
        for lo, hi, count in regions[:args.limit]:
            first = next(n for a, _, n in retail if a == lo)
            print("0x%08X  0x%08X  %8s %6d  %s" % (lo, hi, fmt_int(hi - lo), count, first[:44]))
        return 0

    if args.unit or args.emit_splits:
        stem = args.unit or args.emit_splits
        if stem not in attr:
            print("no map object named %s" % stem, file=sys.stderr)
            return 1
        e = attr[stem]
        funcs = objs[stem]
        print("map object %s: %d functions, %d bytes of .text" % (stem, len(funcs), e["map_bytes"]))
        if not e["run"]:
            print("no consecutive size run found: this unit is not attributable from the map")
            return 1
        print("run %d/%d functions, coverage %.0f%%" % (e["run"], len(funcs), e["coverage"] * 100))
        print("retail .text 0x%08X - 0x%08X  (%d bytes)" % (e["start"], e["end"], e["bytes"]))
        lo, hi = e["start"], e["end"]
        print("\n%-12s %-7s %-7s %-42s %s" % ("addr", "map sz", "retail", "retail name", "map name"))
        for addr, size, name in retail:
            if lo <= addr < hi:
                print("0x%08X   %6s   %6d  %-42s" % (addr, "-", size, name))
        if args.emit_splits:
            print("\n%s.cpp:" % stem)
            print("\t.text start:0x%08X end:0x%08X" % (lo, hi))
        return 0

    sel = [r for r in rows if not r["dead"] or args.include_dead]
    if args.unattributed:
        sel = [r for r in sel if r["status"] == "unattributed"]
    if args.min_bytes:
        sel = [r for r in sel if r["bytes"] >= args.min_bytes]
    if args.max_coverage <= 1.0:
        sel = [r for r in sel if r["coverage"] >= args.max_coverage]

    if args.csv:
        w = csv.writer(sys.stdout)
        w.writerow(["stem", "path", "bytes", "start", "end", "funcs",
                    "in_slices", "in_splits", "claimed", "source", "status"])
        for r in sel:
            w.writerow([r["stem"], r["path"], r["bytes"], "0x%08X" % r["start"], "0x%08X" % r["end"],
                        r["funcs"], int(r["in_slices"]), int(r["in_splits"]), int(r["claimed"]),
                        r["source"], r["status"]])
        return 0

    counts = defaultdict(int)
    code = defaultdict(int)
    for r in sel:
        counts[r["status"]] += 1
        code[r["status"]] += r["bytes"]
    amb = [r for r in sel if r.get("ambiguous") and r["status"] == "attributed"]
    print(LEGEND)
    for k in ("claimed", "split", "attributed", "unattributed"):
        if counts[k]:
            note = "" if k != "unattributed" else "  <- no range known, needs map work"
            print("  %-13s %4d units  %10s bytes of .text%s" % (k, counts[k], fmt_int(code[k]), note))
    if amb:
        print("\n  %d attributed units share a stem with another file: check them by hand" % len(amb))
    if stats:
        print("\n  attribution by name: %d/%d retail functions assigned, %d on object boundaries"
              % (stats["assigned"], stats["retail"], stats["unassigned"]))
    print("\n%d source files in the queue\n" % len(sel))
    print("%-10s %-26s %-34s %8s %5s  %s" % ("status", "stem", "path", "bytes", "funcs", "range"))
    for r in sel[:args.limit]:
        if r["bytes"] < args.min_bytes:
            continue
        print(print_row(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
