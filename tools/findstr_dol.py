import sys

data = open(r"orig/Release_eu/sys/main.dol", "rb").read()
pat = sys.argv[1].encode()
start = 0
while True:
    i = data.find(pat, start)
    if i < 0:
        break
    print("fileoff 0x%08X" % i)
    start = i + 1