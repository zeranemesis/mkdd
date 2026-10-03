import struct
import sys

d = open(r"orig/Release_eu/sys/main.dol", "rb").read()
# segment deltas: vaddr = fileoff + DELTA
DELTAS = {0x80003000: "seg1/.text/.ctors", 0x80006000: "seg3/.rodata/.data"}
foff = int(sys.argv[1], 16)
n = int(sys.argv[2], 0)
delta = int(sys.argv[3], 16) if len(sys.argv) > 3 else 0x80006000
for i in range(n):
    off = foff + i * 4
    w = struct.unpack(">I", d[off:off + 4])[0]
    print("file 0x%08X  vaddr 0x%08X  0x%08X" % (off, off + delta, w))