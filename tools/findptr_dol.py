import struct
import sys

d = open(r"orig/Release_eu/sys/main.dol", "rb").read()
# .data/.sdata vaddr -> fileoff for the file-backed DOL segments.
# Derived empirically: file offset 0x33A760 holds "/Objects/Car_Public1.bmd"
# and the retail GeoNormCar getBmdFileName relocation says 0x8033D760.
for want in [int(a, 16) for a in sys.argv[1:]]:
    pat = struct.pack(">I", want)
    i = d.find(pat)
    hits = []
    start = 0
    while True:
        i = d.find(pat, start)
        if i < 0:
            break
        hits.append(i)
        start = i + 1
    print("0x%08X -> %d occurrence(s): %s" % (want, len(hits), ", ".join("fileoff 0x%X" % h for h in hits)))