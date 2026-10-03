import re
import subprocess
import sys

OBJDUMP = r"build/binutils/powerpc-eabi-objdump.exe"


def sections(path):
    out = subprocess.run([OBJDUMP, "-h", path], capture_output=True, text=True).stdout
    secs = {}
    for line in out.splitlines():
        m = re.match(r"\s*\d+\s+(\S+)\s+([0-9a-f]{8})\s+([0-9a-f]{8})\s+([0-9a-f]{8})\s", line)
        if m:
            secs[m.group(1)] = (int(m.group(2), 16), int(m.group(4), 16))
    return secs


def data(path, name):
    out = subprocess.run([OBJDUMP, "-s", "-j", name, path], capture_output=True, text=True).stdout
    buf = bytearray()
    for line in out.splitlines():
        m = re.match(r"\s*([0-9a-f]+)\s((?:[0-9a-f]{8}\s){1,4})", line)
        if m:
            for w in m.group(2).split():
                buf += bytes.fromhex(w)
    return bytes(buf)


def syms(path, sec):
    out = subprocess.run([OBJDUMP, "-t", path], capture_output=True, text=True).stdout
    res = []
    for line in out.splitlines():
        p = line.split()
        if len(p) == 6 and p[2] == "F" and p[3] == sec:
            res.append((int(p[0], 16), int(p[4], 16), p[1], p[5]))
    res.sort()
    return res


def main(base, ours, sec):
    bs = sections(base)
    os_ = sections(ours)
    print("base .text size 0x%X   ours %s size 0x%X" % (bs[sec][0], sec, os_[sec][0]))
    bdata = data(base, sec)
    odata = data(ours, sec)
    bsyms = syms(base, sec)
    osyms = syms(ours, sec)
    print("=" * 100)
    print("BASE:")
    for off, size, kind, name in bsyms:
        print("  %08x %08x %-4s %s" % (off, size, "MATCH" if (off, size) in [(o, s) for o, s, k, n in osyms] else "", name))
    print("OURS:")
    for off, size, kind, name in osyms:
        print("  %08x %08x      %s" % (off, size, name))
    print("=" * 100)
    # byte compare base funcs against our funcs of the same size
    bysize = {}
    for off, size, kind, name in osyms:
        bysize.setdefault(size, []).append((off, name))
    for off, size, kind, name in bsyms:
        b = bdata[off:off + size]
        cands = bysize.get(size, [])
        hits = []
        for ooff, oname in cands:
            if odata[ooff:ooff + size] == b:
                hits.append(oname)
        status = "IDENTICAL -> " + ", ".join(hits) if hits else "NO IDENTICAL CANDIDATE"
        print("%-45s size=0x%-5x off=0x%-5x %s" % (name, size, off, status))


main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else ".text")