#!/usr/bin/env python3
"""
Prepare an isolated agent worktree for Mario Kart Double Dash!! (Release_eu).

Each agent gets its own git worktree so that concurrent ninja builds and
configure.py runs never collide. Toolchain downloads (compilers, binutils,
dtk, objdiff) are ~190 MB, so instead of letting every agent re-download them
we copy the already-provisioned ones from the main tree, plus the original DOL.

Usage:
    python tools/prepare_agent_worktree.py <worktree-path> [--source <main-tree>]
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def log(msg):
    print("[prepare] " + msg, flush=True)


def copy_tree(src: Path, dst: Path, sizes: dict):
    if not src.is_dir():
        log("MISSING source dir, skipping: %s" % src)
        return
    if dst.exists():
        shutil.rmtree(dst)
    log("copying %s -> %s" % (src, dst))
    shutil.copytree(src, dst, copy_function=shutil.copy2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("worktree", type=Path)
    ap.add_argument("--source", type=Path, default=Path("."))
    args = ap.parse_args()

    wt = args.worktree.resolve()
    src = args.source.resolve()
    if wt == src:
        log("worktree and source are the same; nothing to do")
        return 0
    if not (wt / "configure.py").is_file():
        log("error: %s is not a prepared worktree (no configure.py)" % wt)
        return 1

    wt.mkdir(parents=True, exist_ok=True)
    sizes = {}

    # Toolchain + build outputs that are expensive to re-download.
    for rel in ("build/compilers", "build/binutils", "build/tools"):
        copy_tree(src / rel, wt / rel, sizes)

    # The original retail DOL. orig/* is gitignored, so worktrees start without it.
    dol = wt / "orig" / "Release_eu" / "sys" / "main.dol"
    srcdol = src / "orig" / "Release_eu" / "sys" / "main.dol"
    if not dol.is_file():
        if srcdol.is_file():
            dol.parent.mkdir(parents=True, exist_ok=True)
            log("copying original DOL (%.1f MB)" % (srcdol.stat().st_size / 1e6))
            shutil.copy2(srcdol, dol)
        else:
            log("error: original DOL not found at %s" % srcdol)
            return 1

    log("done. Now run, from %s:" % wt)
    log("  python configure.py --version Release_eu")
    log("  ninja")
    return 0


if __name__ == "__main__":
    sys.exit(main())