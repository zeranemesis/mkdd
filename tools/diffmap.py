"""Rank the remaining byte differences in our DOL against the retail PAL DOL.

For every byte that differs between build/Release_eu/main.dol and
orig/Release_eu/sys/main.dol, find which translation unit owns that virtual
address according to config/Release_eu/dol_slices.yml, then group the
differences per unit and per section. The result is a ranked work list for
reaching a byte-exact build.
"""

import argparse
import bisect
import collections
import re

OURS = r"build/Release_eu/main.dol"
RETAIL = r"orig/Release_eu/sys/main.dol"
SLICES = r"config/Release_eu/dol_slices.yml"

# Every file-backed DOL section shares one vaddr = fileoff + DELTA, and the
# header records .init's file offset at 0x00 and its vaddr at 0x48.
HEADER_FOFF_INIT = 0x00
HEADER_VADDR_INIT = 0x48
# A DOL records one file offset and one vaddr per section, so each section has
# its own delta. .sdata and .sdata2 live in a later segment and do NOT share
# the .text delta, which is the trap that makes them look broken.
HEADER_SECTIONS = (
    # (name, foff word, vaddr word, size word, foff word is 0 for .text)
    (".text", 0x00, 0x4C, 0x94, True),
    (".ctors", 0x24, 0x6C, 0xB4, False),
    (".dtors", 0x28, 0x70, 0xB8, False),
    (".BINARY", 0x2C, 0x74, 0xBC, False),
    (".rodata", 0x30, 0x78, 0xC0, False),
    (".data", 0x34, 0x7C, 0xC4, False),
    (".sdata", 0x38, 0x80, 0xC8, False),
    (".sdata2", 0x3C, 0x84, 0xCC, False),
)

SPLIT_RE = re.compile(r"^(\S+):\s*$")
SLICE_RE = re.compile(r"^\s*(\.\w+):\s*\[\s*(0x[0-9a-fA-F]+)\s*,\s*(0x[0-9a-fA-F]+)\s*\]")


def dol_sections(path):
    """Return [(name, vaddr, size, delta)] for one DOL."""
    import struct

    d = open(path, "rb").read()
    word = lambda o: struct.unpack(">I", d[o:o + 4])[0]
    first_delta = word(0x48) - word(0x00)
    out = []
    for name, foff_word, va_word, sz_word, from_init in HEADER_SECTIONS:
        addr = word(va_word)
        delta = first_delta if from_init else addr - word(foff_word)
        foff = word(foff_word) if not from_init else addr - delta
        # the header rounds section sizes up to the 0x20 file alignment
        size = min(word(sz_word), len(d) - foff)
        out.append((name, addr, size, delta))
    out.sort(key=lambda s: s[1])
    return out


def make_foff_to_vaddr(sections):
    def conv(foff):
        for name, addr, size, delta in sections:
            if addr - delta <= foff < addr - delta + size:
                return foff + delta, name
        return None, None

    return conv


def load_slices(path):
    units = []
    name = None
    with open(path) as fh:
        for line in fh:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            m = SPLIT_RE.match(line)
            if m:
                name = m.group(1)
                continue
            m = SLICE_RE.match(line)
            if m and name:
                units.append((name, m.group(1), int(m.group(2), 16), int(m.group(3), 16)))
    return units


def diff_runs(ours, retail):
    n = min(len(ours), len(retail))
    runs = []
    i = 0
    while i < n:
        if ours[i] != retail[i]:
            j = i
            while j < n and ours[j] != retail[j]:
                j += 1
            runs.append((i, j))
            i = j
        else:
            i += 1
    if len(ours) != len(retail):
        runs.append((n, max(len(ours), len(retail))))
    return runs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-bytes", type=int, default=1)
    ap.add_argument("--section", help="only report this section, e.g. .data")
    ap.add_argument("--unowned", action="store_true", help="only differences no unit owns")
    ap.add_argument("--top", type=int, default=60)
    args = ap.parse_args()

    ours_sections = dol_sections(OURS)
    retail_sections = {n: (a, s, d) for n, a, s, d in dol_sections(RETAIL)}
    ours = open(OURS, "rb").read()
    retail = open(RETAIL, "rb").read()
    slices = sorted(load_slices(SLICES), key=lambda s: s[2])
    starts = [s[2] for s in slices]

    def owner(vaddr):
        i = bisect.bisect_right(starts, vaddr) - 1
        if i >= 0 and slices[i][2] <= vaddr < slices[i][3]:
            return slices[i]
        return None

    def next_owner(vaddr):
        i = bisect.bisect_right(starts, vaddr)
        return slices[i][2] if i < len(slices) else vaddr + 1

    per_unit = collections.defaultdict(lambda: collections.Counter())
    per_section = collections.Counter()
    unowned = collections.Counter()
    unowned_runs = []

    def walk(vaddr, size, section):
        limit = vaddr + size
        pos = vaddr
        while pos < limit:
            hit = owner(pos)
            if hit is None:
                stop = min(limit, next_owner(pos))
                unowned["<%s unclaimed>" % section] += stop - pos
                unowned_runs.append((section, pos, stop))
                pos = stop
                continue
            name, sect, lo, hi = hit
            stop = min(limit, hi)
            per_unit[name][sect] += stop - pos
            per_section[sect] += stop - pos
            pos = stop

    for name, addr, size, delta in ours_sections:
        retail_addr, retail_size, retail_delta = retail_sections[name]
        if addr != retail_addr:
            print("note: %s vaddr differs, ours 0x%08X retail 0x%08X" % (name, addr, retail_addr))
            continue
        limit = min(size, retail_size)
        pos = addr
        while pos < addr + limit:
            if ours[pos - delta] == retail[pos - retail_delta]:
                pos += 1
                continue
            end = pos
            while end < addr + limit and ours[end - delta] != retail[end - retail_delta]:
                end += 1
            walk(pos, end - pos, name)
            pos = end

    total = sum(per_section.values()) + sum(unowned.values())
    print("total differing bytes: %d" % total)
    for section, count in per_section.most_common():
        print("  %-10s %7d" % (section, count))
    for section, count in unowned.most_common():
        print("  %-10s %7d" % (section, count))

    if args.section:
        keep = {args.section}
    else:
        keep = None

    rows = []
    for name, sections in per_unit.items():
        count = sum(
            v for k, v in sections.items() if keep is None or k in keep
        )
        if count >= args.min_bytes:
            detail = " ".join(
                "%s=%d" % (k, v) for k, v in sorted(sections.items()) if keep is None or k in keep
            )
            rows.append((count, name, detail))
    rows.sort(reverse=True)

    print("\n%-9s %-52s %s" % ("bytes", "unit", "sections"))
    for count, name, detail in rows[: args.top]:
        print("%-9d %-52s %s" % (count, name, detail))

    if args.unowned:
        print("\nunowned differences: %d bytes" % sum(unowned.values()))
        merged = []
        for section, lo, hi in unowned_runs:
            if merged and merged[-1][0] == section and merged[-1][2] == lo:
                merged[-1][2] = hi
            else:
                merged.append([section, lo, hi])
        merged.sort(key=lambda r: r[2] - r[1], reverse=True)
        print("%-9s %-11s %-11s %s" % ("bytes", "start", "end", "section"))
        for section, lo, hi in merged[: args.top]:
            print("%-9d 0x%08X 0x%08X %s" % (hi - lo, lo, hi, section))


if __name__ == "__main__":
    main()
