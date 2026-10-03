import struct

d = open(r"orig/Release_eu/sys/main.dol", "rb").read()
off = struct.unpack(">I", d[0x4C:0x50])[0]
shoff = struct.unpack(">I", d[0x48:0x4C])[0]
print("shoff(file)=0x%X shoff(off)=0x%X" % (shoff, off))
n = struct.unpack(">H", d[shoff + 0x3C:shoff + 0x3E])[0]
print("shnum", n)
strtab = shoff + struct.unpack(">I", d[shoff + 0x20:shoff + 0x24])[0]
for i in range(n):
    base = shoff + i * 0x28
    nameoff = struct.unpack(">I", d[base:base + 4])[0]
    nm = d[strtab + nameoff:d.index(b"\0", strtab + nameoff)].decode()
    typ = struct.unpack(">I", d[base + 4:base + 8])[0]
    addr = struct.unpack(">I", d[base + 0xC:base + 0x10])[0]
    foff = struct.unpack(">I", d[base + 0x18:base + 0x1C])[0]
    size = struct.unpack(">I", d[base + 0x14:base + 0x18])[0]
    if typ == 8:
        continue
    print("%-10s addr=0x%08X foff=0x%08X size=0x%08X delta=0x%08X" % (nm, addr, foff, size, addr - foff if foff else 0))