#!/usr/bin/env python3
"""Match one unit against its retail target, and sweep compiler flags for it.

The decompilation loop is always the same three questions and the repo answers
none of them directly:

  1. which of this unit's functions still differ, and where exactly;
  2. what do the bytes look like on each side;
  3. is the remaining difference a code problem or a compiler-flag problem.

build/checkunit.py answers none of these once a unit has more than a handful of
functions, and the objdiff GUI is not usable over ten agents. So this reads the
two objects itself: objdump for section contents, nm for symbol extents, then
compares symbol by symbol. Target objects produced by dtk give every function its
own section, so a function's bytes are its section; the compiled side keeps one
.text and each function is sliced out of it by address and size.

    python tools/match.py -u main/Osako/main              # per-function verdict
    python tools/match.py -u main/Osako/main --missing    # what is not written yet
    python tools/match.py -u main/Osako/main --sweep      # try flag variants
    python tools/match.py -u main/Osako/main --fn Init__10KartPerCamFi
    python tools/match.py --list                          # units not yet complete

--sweep takes the unit's real compile command out of build.ninja, rewrites the
one optimisation flag that is being questioned, recompiles to a scratch
directory, and re-measures. Almost every "this unit is 99.8%" in this project is
a flag difference (-O4,s vs -O4,p, -inline off, -use_lmw_stmw off, -str pool), so
sweeping before rewriting C++ is the cheapest move available.

Nothing here writes outside build/, so ten agents can run it at once.
"""
import argparse
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OBJDIFF = "objdiff.json"
BINUTILS = ROOT / "build" / "binutils"
OBJDUMP = BINUTILS / "powerpc-eabi-objdump.exe"
NM = BINUTILS / "powerpc-eabi-nm.exe"
SCRATCH = "build/match"

SEC_RE = re.compile(r"^Contents of section (\S+):")
HEX_RE = re.compile(r"^\s*([0-9a-fA-F]+)\s((?:[0-9a-fA-F]{2,8}\s){1,4})")
NM_RE = re.compile(r"^([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+(\S)\s+(\S+)$")

CODE_TYPES = {"T", "t", "W", "w"}
DATA_TYPES = {"D", "d", "B", "b", "R", "r", "S", "s", "V", "v", "N", "n"}


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def section_bytes(obj):
    """{section name: raw bytes} for one ELF object."""
    r = run([str(OBJDUMP), "-s", str(obj)])
    if r.returncode != 0:
        raise RuntimeError("objdump failed on %s: %s" % (obj, r.stderr.strip()))
    out = {}
    name = None
    for line in r.stdout.splitlines():
        m = SEC_RE.match(line)
        if m:
            name = m.group(1)
            out.setdefault(name, bytearray())
            continue
        if name is None:
            continue
        m = HEX_RE.match(line)
        if m:
            # objdump groups bytes into words, so a token is up to 8 hex digits
            for word in m.group(2).split():
                for k in range(0, len(word), 2):
                    out[name].append(int(word[k:k + 2], 16))
    return {k: bytes(v) for k, v in out.items()}


def symbols(obj):
    """{name: (addr, size, type)} from nm -S."""
    r = run([str(NM), "-S", str(obj)])
    out = {}
    for line in r.stdout.splitlines():
        m = NM_RE.match(line)
        if m:
            out[m.group(4)] = (int(m.group(1), 16), int(m.group(2), 16), m.group(3))
    return out


def extract(obj):
    """{symbol: (bytes, kind)} for every defined symbol, code and data.

    On the dtk target side each function is its own section, so the section name
    is the symbol name. On the compiled side everything shares .text, so code is
    sliced by nm's address and size. Data symbols are taken from whichever
    section nm places them in.
    """
    secs = section_bytes(obj)
    syms = symbols(obj)
    out = {}
    for name, (addr, size, kind) in syms.items():
        if not size:
            continue
        if kind in CODE_TYPES:
            # dtk gives every target function its own section; a compiled object
            # keeps them all in .text, so try both layouts.
            if name in secs and len(secs[name]) >= size:
                out[name] = (secs[name][:size], "code")
                continue
            text = secs.get(".text")
            if text is not None and addr + size <= len(text):
                out[name] = (text[addr:addr + size], "code")
        elif kind in DATA_TYPES:
            for secname, blob in secs.items():
                if secname == name or secname.startswith(name + "."):
                    if len(blob) >= size:
                        out[name] = (blob[:size], "data")
                        break
            else:
                if name in secs:
                    out[name] = (secs[name][:size], "data")
    return out


def compare(base, target):
    """Per-symbol verdict between two extracted symbol tables."""
    rows = []
    for name in sorted(set(base) | set(target)):
        if name not in base:
            rows.append((name, "missing", 0, len(target[name][0]), target[name][1]))
        elif name not in target:
            rows.append((name, "extra", len(base[name][0]), 0, base[name][1]))
        else:
            bb, bk = base[name]
            tb, tk = target[name]
            kind = "code" if "code" in (bk, tk) else "data"
            same = bb == tb
            rows.append((name, "exact" if same else "diff",
                         len(bb) if same else common_prefix(bb, tb), len(tb), kind))
    return rows


def common_prefix(a, b):
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return i
    return n


def load_units(root, only=None):
    data = json.loads((root / OBJDIFF).read_text(encoding="utf-8"))
    units = {}
    for u in data["units"]:
        if "base_path" not in u:
            continue
        if u.get("metadata", {}).get("auto_generated"):
            continue
        units[u["name"]] = u
    if only:
        units = {k: v for k, v in units.items() if only in k}
    return units


def resolve(root, spec):
    """Accept main/Osako/main, Osako/main, Osako/main.cpp or a full unit name."""
    units = load_units(root)
    if spec in units:
        return units[spec]
    cands = [n for n in units if spec in n]
    if not cands:
        cands = [n for n in units if n.split("/")[-1].split(".")[0] == spec]
    if not cands:
        return None
    return units[sorted(cands)[0]]


def compile_command(root, base_path, out_dir):
    r = run(["ninja", "-t", "commands", base_path.replace("\\", "/")], cwd=str(root))
    if r.returncode != 0:
        raise RuntimeError("ninja -t commands failed: %s" % r.stderr.strip())
    for line in reversed(r.stdout.splitlines()):
        line = line.strip()
        if "-c " in line and "mwcceppc" in line:
            return line
    raise RuntimeError("no mwcc command for %s" % base_path)


def run_variant(cmd, drop_seqs, add_tokens, out_dir):
    """Re-run the compile with token sequences removed and tokens added.

    Removal has to work on sequences, not on single tokens: dropping the bare
    token "off" to swap -inline would also delete the "off" of
    -Cpp_exceptions off and the compiler then aborts on a dangling option.
    """
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    toks = shlex.split(cmd, posix=False)
    out = []
    i = 0
    while i < len(toks):
        if toks[i] == "-o" and i + 1 < len(toks):
            out += ["-o", str(d)]
            i += 2
            continue
        dropped = False
        for seq in drop_seqs:
            if toks[i:i + len(seq)] == list(seq):
                i += len(seq)
                dropped = True
                break
        if dropped:
            continue
        out.append(toks[i])
        i += 1
    # insert the variant flags immediately after the compiler path, which sits
    # after the sjiswrap shim: putting them any earlier makes sjiswrap try to
    # execute "-O4,p" as the compiler
    pos = 0
    for k, t in enumerate(out):
        if "mwcceppc" in t:
            pos = k + 1
            break
    if not pos:
        pos = 1
    return run(out[:pos] + list(add_tokens) + out[pos:])


VARIANTS = [
    ("baseline", [], []),
    ("O4,p", [["-O4,s"]], ["-O4,p"]),
    ("O4,s +opt nopeephole", [], ["-opt", "nopeephole"]),
    ("O4,p +opt nopeephole", [["-O4,s"]], ["-O4,p", "-opt", "nopeephole"]),
    ("-inline off", [["-inline", "auto"]], ["-inline", "off"]),
    ("-use_lmw_stmw off", [["-use_lmw_stmw", "on"]], ["-use_lmw_stmw", "off"]),
    ("-str pool", [["-str", "pool"], ["-str", "reuse"], ["-str", "readonly"]], ["-str", "pool"]),
    ("-str reuse", [["-str", "pool"], ["-str", "readonly"]], ["-str", "reuse"]),
    ("-str readonly", [["-str", "pool"], ["-str", "reuse"]], ["-str", "readonly"]),
    ("-fp soft", [["-fp", "fmadd"], ["-fp", "hardware"]], ["-fp", "soft"]),
    ("-fp hardware", [["-fp", "fmadd"], ["-fp", "soft"]], ["-fp", "hardware"]),
    ("-char unsigned", [["-char", "signed"]], ["-char", "unsigned"]),
    ("-fp_contract off", [["-fp_contract", "on"]], ["-fp_contract", "off"]),
    ("-common off", [["-common", "on"]], ["-common", "off"]),
    ("no -rostr", [["-rostr"]], []),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-u", "--unit", help="unit name or fragment")
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--missing", action="store_true", help="only symbols the source does not define")
    ap.add_argument("--extra", action="store_true",
                    help="also list symbols the source defines that the retail unit lacks")
    ap.add_argument("--data", action="store_true", help="include data symbols in the listing")
    ap.add_argument("--fn", help="show a single symbol's first difference")
    ap.add_argument("--sweep", action="store_true", help="try flag variants and rank them")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--list", action="store_true", help="list configured units and their base paths")
    a = ap.parse_args()

    if a.list:
        for name, u in sorted(load_units(a.root).items()):
            print("%-52s %s" % (name, u["base_path"]))
        return 0

    if not a.unit:
        ap.error("-u is required")
    unit = resolve(a.root, a.unit)
    if unit is None:
        print("no configured unit matches %r" % a.unit, file=sys.stderr)
        return 1

    base_path = a.root / unit["base_path"]
    target_path = a.root / unit["target_path"]
    source = unit.get("metadata", {}).get("source_path", "?")
    print("unit    %s" % unit["name"])
    print("source  %s" % source)
    print("base    %s" % unit["base_path"])
    print("target  %s" % unit["target_path"])

    if a.sweep:
        cmd = compile_command(a.root, unit["base_path"], SCRATCH)
        flag_list = shlex.split(cmd, posix=False)
        flag_list = flag_list[1:]  # drop the sjiswrap compiler path
        results = []
        for name, drop, extra in VARIANTS:
            out_dir = a.root / SCRATCH / name.replace(" ", "_").replace(",", "").replace("+", "p")
            r = run_variant(cmd, drop, extra, out_dir)
            if r.returncode != 0:
                results.append((name, -1, 0, 0, r.stderr.strip().splitlines()[-1][:70]
                                if r.stderr.strip() else "compile failed"))
                continue
            cand = out_dir / (Path(unit["base_path"]).stem + ".o")
            if not cand.is_file():
                results.append((name, -1, 0, 0, "no object produced"))
                continue
            rows = compare(extract(cand), extract(target_path))
            code = [r for r in rows if r[4] == "code"]
            ok = sum(1 for r in code if r[1] == "exact")
            tot = sum(r[3] for r in code)
            got = sum(r[2] for r in code)
            results.append((name, got, tot, ok, ""))
        print("\n%-26s %10s %10s %6s  %s" % ("variant", "matched", "total", "fns", "note"))
        for name, got, tot, ok, note in sorted(results, key=lambda x: (-x[1], -x[3])):
            pct = "%.2f%%" % (100.0 * got / tot) if tot else "-"
            print("%-26s %10s %10s %6s  %s"
                  % (name, "%s %s" % (got, pct) if tot else "-",
                     tot or "-", ok if ok >= 0 else "-", note))
        return 0

    if not base_path.is_file():
        print("base object missing: run ninja %s first" % unit["base_path"], file=sys.stderr)
        return 1
    base = extract(base_path)
    target = extract(target_path)
    rows = compare(base, target)
    if not a.data:
        rows = [r for r in rows if r[4] == "code"]

    code = [r for r in rows if r[4] == "code" and r[3] > 0]
    extra = [r for r in rows if r[4] == "code" and r[1] == "extra"]
    got = sum(r[2] for r in code)
    tot = sum(r[3] for r in code)
    ok = sum(1 for r in code if r[1] == "exact")
    print("\ncode: %d/%d bytes exact (%.2f%%), %d/%d retail functions exact%s"
          % (got, tot, 100.0 * got / tot if tot else 0.0, ok, len(code),
             "" if not extra else ", %d extra symbols not in the retail unit" % len(extra)))

    if a.missing:
        sel = [r for r in rows if r[1] in ("missing", "diff", "extra")]
    else:
        sel = [r for r in rows if r[1] in ("missing", "diff")]
    if a.extra:
        sel += [r for r in rows if r[1] == "extra"]
    if not sel:
        print("every symbol in scope is byte-exact")
        return 0

    print("\n%-52s %-8s %7s %7s" % ("symbol", "state", "match", "size"))
    for name, state, m, t, kind in sel[:a.limit]:
        print("%-52s %-8s %7d %7d" % (name[:52], state, m, t))

    if a.fn:
        r = run([str(OBJDUMP), "-d", "--disassemble=" + a.fn, str(base_path)])
        print("\n--- compiled ---\n%s" % r.stdout[-3000:])
        r = run([str(OBJDUMP), "-d", "--disassemble=" + a.fn, str(target_path)])
        print("\n--- target ---\n%s" % r.stdout[-3000:])
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
