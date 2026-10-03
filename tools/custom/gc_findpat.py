#!/usr/bin/env python3
"""Search main.dol for a byte pattern given as hex, and report file offset plus
the VMA under both candidate per-section DOL deltas."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOL = ROOT / "orig/Release_eu/sys/main.dol"
SECTIONS = {  # name -> (dol_delta)
    ".init": 0x80003100 - 0x200,
    ".text": 0x800030C0,
    ".rodata": 0x80003000,
    ".data": 0x80003000,
    ".sdata": 0x80003000,
    ".sdata2": 0x80003000,
}


def delta_for(vma):
    for name, d in SECTIONS.items():
        if (vma - d) & 0x0FFFFFFF < 0x02000000:
            return name, d
    return "?", 0


def main():
    pat = bytes.fromhex(sys.argv[1].replace(" ", ""))
    data = DOL.read_bytes()
    start = 0
    hits = 0
    while True:
        i = data.find(pat, start)
        if i < 0:
            break
        for d in sorted(set(SECTIONS.values())):
            vma = i + d
            name = [k for k, v in SECTIONS.items() if v == d][0]
            print("fileoff 0x%08X  as %-8s VMA 0x%08X" % (i, name, vma))
        hits += 1
        start = i + 1
    print("total hits: %d" % hits)


main()