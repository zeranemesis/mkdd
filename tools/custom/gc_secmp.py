#!/usr/bin/env python3
"""Byte-compare one section of our compiled object against the retail ELF/DOL
bytes over a claimed split range. Diagnostic only; reports the exact offsets
that differ and whether the tail is pure zero padding."""
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OD = r"C:\Users\valen\AppData\Local\Temp\kilo\mkdd-a1\build\binutils\powerpc-eabi-objdump.exe"


def sec_bytes(obj, name):
    out = subprocess.run([OD, "-s", "-j", name, str(obj)],
                         capture_output=True, text=True).stdout
    data = bytearray()
    hexre = __import__("re").compile(r"^[0-9a-fA-F]{4,8}$")
    grpre = __import__("re").compile(r"^[0-9a-fA-F]{8}$")
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 2 or not hexre.match(parts[0]):
            continue
        for grp in parts[1:]:
            if not grpre.match(grp):
                break
            data += bytes.fromhex(grp)
    return bytes(data)


# section -> (VMA, file offset) in build/Release_eu/main.elf, from `objdump -h`
ELF_SEC = {
    ".rodata": (0x8032CBE0, 0x329CE0),
    ".data": (0x80346C20, 0x343D20),
    ".sdata": (0x803D3240, 0x36AEE0),
    ".sdata2": (0x803D63E0, 0x36C620),
}


def elf_bytes_at(name, vma, n):
    elf = (ROOT / "build/Release_eu/main.elf").read_bytes()
    base_vma, base_off = ELF_SEC[name]
    off = base_off + (vma - base_vma)
    return elf[off:off + n]


def main():
    obj = ROOT / "build/Release_eu/src/Kaneshige/GeoCannon.o"
    name, vma, size = sys.argv[1], int(sys.argv[2], 16), int(sys.argv[3], 0)
    ours = sec_bytes(obj, name)
    retail = elf_bytes_at(name, vma, size)
    print("our %-8s len=%d (0x%X)   retail claim len=%d (0x%X)"
          % (name, len(ours), len(ours), size, size))
    n = min(len(ours), len(retail))
    bad = [i for i in range(n) if ours[i] != retail[i]]
    print("differing bytes in common prefix: %d" % len(bad))
    for i in bad[:40]:
        print("   +0x%03X (vma 0x%08X): ours %02X retail %02X"
              % (i, vma + i, ours[i], retail[i]))
    extra = retail[len(ours):]
    print("retail tail beyond our section: %d bytes, all zero: %s"
          % (len(extra), set(extra) == {0} if extra else "n/a"))
    if extra:
        print("   tail bytes: " + " ".join("%02X" % b for b in extra))


main()