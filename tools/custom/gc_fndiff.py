#!/usr/bin/env python3
"""Diff one function of our object against the retail bytes at its split address,
skipping words that carry a relocation (those legitimately differ pre-link)."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OD = r"C:\Users\valen\AppData\Local\Temp\kilo\mkdd-a1\build\binutils\powerpc-eabi-objdump.exe"
TEXTOFF = 0x27C0
TEXTVMA = 0x800056C0


def run(*a):
    return subprocess.run([OD, *a], capture_output=True, text=True).stdout


def sec_text(obj):
    out = run("-s", "-j", ".text", str(obj))
    data = bytearray()
    import re
    off_re = re.compile(r"^([0-9a-f]{4,8})$")
    grp_re = re.compile(r"^[0-9a-f]{8}$")
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 2 or not off_re.match(parts[0]):
            continue
        for g in parts[1:]:
            if not grp_re.match(g):
                break
            data += bytes.fromhex(g)
    return bytes(data)


def reloc_words(obj):
    out = run("-r", "-j", ".text", str(obj))
    s = set()
    for line in out.splitlines():
        p = line.split()
        if p and len(p[0]) == 8:
            try:
                s.add(int(p[0], 16))
            except ValueError:
                pass
    return s


def main():
    name, vma, size = sys.argv[1], int(sys.argv[2], 16), int(sys.argv[3], 0)
    sym, = [a for a in sys.argv[4:] if not a.startswith("0x")]
    obj = ROOT / "build/Release_eu/src/Kaneshige/GeoCannon.o"
    elf = (ROOT / "build/Release_eu/main.elf").read_bytes()
    retail = elf[TEXTOFF + vma - TEXTVMA: TEXTOFF + vma - TEXTVMA + size]
    text = sec_text(obj)
    rel = reloc_words(obj)
    out = run("-t", str(obj))
    start = None
    for line in out.splitlines():
        p = line.split()
        if len(p) >= 2 and p[-1] == sym and p[0].strip("l gOwW") == "":
            pass
        if len(p) >= 2 and p[-1] == sym:
            try:
                start = int(p[0], 16)
            except ValueError:
                start = None
    print("sym %s at .text+0x%s" % (sym, hex(start) if start is not None else "?"))
    ours = text[start:start + size]
    bad = []
    for i in range(size):
        if i in rel or (i & ~3) in rel:
            continue
        if ours[i] != retail[i]:
            bad.append(i)
    print("non-reloc differing bytes: %d / %d" % (len(bad), size))
    for i in bad:
        print("   +0x%03X vma 0x%08X ours %02X retail %02X"
              % (i, vma + i, ours[i], retail[i]))


main()