#!/usr/bin/env python3
"""Bootstrap and tear down an isolated agent worktree.

An agent decompiling one unit needs its own checkout: configure.py writes
build.ninja and objdiff.json at the repository root, and every agent re-runs it
with its own --build-dir, so two agents sharing a tree corrupt each other's
split and each other's diff data.

What an agent must NOT do is copy the toolchain. build/compilers,
build/binutils and build/tools are ~190 MB; tools/prepare_agent_worktree.py used
to copy them into every worktree, and with a hundred worktrees that is both slow
and pointless. configure.py takes the tool locations as flags, so the toolchain
stays in the main tree and only the source checkout is duplicated. The original
DOL is shared through a directory junction rather than a second copy.

    python tools/worktree.py create w1          # from G:\\decomp\\Mariokartdd
    python tools/worktree.py commands w1        # what the agent runs
    python tools/worktree.py remove w1          # after its commit is picked

The created worktree is on branch w1 cut from `port`, with everything an agent
needs already wired up. Give the agent the output of `commands w1` verbatim.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMP = Path(os.environ.get("TEMP", "C:/Users/valen/AppData/Local/Temp")) / "kilo"
VERSION = "Release_eu"
# Shared, read-only toolchain: never copied, only pointed at.
TOOLCHAIN = ROOT / "build"
DOL_SRC = ROOT / "orig" / VERSION / "sys"


def sh(*args, **kw):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kw)


def path_for(name):
    return TEMP / name


def sys_link(wt):
    return wt / "orig" / VERSION / "sys"


def unlink_sys(wt):
    """Delete the orig junction before the worktree goes.

    `git worktree remove --force` deletes the directory recursively, and on
    Windows a recursive delete follows a directory junction into its target. Doing
    that here empties the shared orig/<version>/sys tree: it took out the retail
    main.dol and debugInfoS.MAP once already, which stops the whole project
    building. os.rmdir on a junction removes the link only, never the target.
    """
    link = sys_link(wt)
    try:
        if link.is_junction():
            os.rmdir(str(link))
            return True
        if link.is_symlink():
            os.unlink(str(link))
            return True
    except (OSError, AttributeError):
        pass
    return False


def create(name, branch=None):
    wt = path_for(name)
    branch = branch or name
    if wt.exists():
        print("already exists: %s (reset it with: git -C %s reset --hard port)" % (wt, wt))
        return 1
    sh("git", "worktree", "add", "-b", branch, str(wt), "port", cwd=str(ROOT))

    # Share the original DOL through a junction: one copy on disk, and the agent
    # still sees orig/<version>/sys/main.dol exactly where configure.py wants it.
    orig = wt / "orig" / VERSION
    orig.mkdir(parents=True, exist_ok=True)
    link = orig / "sys"
    if not link.exists():
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(DOL_SRC)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print("junction failed (%s); falling back to a copy" % r.stdout.strip())
            DOL_SRC.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy2(DOL_SRC / "main.dol", link / "main.dol")
            for extra in DOL_SRC.glob("files/*"):
                (link / "files").mkdir(exist_ok=True)
                shutil.copy2(extra, link / "files" / extra.name)

    print("ready: %s (branch %s)" % (wt, branch))
    commands(name)
    return 0


def commands(name):
    wt = path_for(name)
    print("\n--- agent %s, run from %s ---" % (name, wt))
    print('cd /d "%s"' % wt)
    # No --build-dir on purpose: configure.py writes the generated include paths
    # as -i build/<version>/include, hardcoded relative to the repository root, so
    # redirecting the outputs to build-<name> makes the compiler look for
    # generated headers in a directory that does not exist and every translation
    # unit warns about missing includes. The worktree is already isolated, so the
    # default build/ inside it is the right answer.
    print("python configure.py --version %s \\" % VERSION)
    print('    --compilers "%s" \\' % (TOOLCHAIN / "compilers"))
    print('    --binutils "%s" \\' % (TOOLCHAIN / "binutils"))
    print('    --dtk "%s" \\' % (TOOLCHAIN / "tools" / "dtk.exe"))
    print('    --objdiff "%s"' % (TOOLCHAIN / "tools" / "objdiff-cli.exe"))
    print("")
    print("# one unit at a time, never a full link:")
    print("ninja build/%s/src/<unit>.o" % VERSION)
    print("")
    print("# measure against the retail target:")
    print("python tools/match.py -u main/<unit>")
    print("")
    print("# where the work queue comes from:")
    print('python "%s\\tools\\backlog.py" --limit 40' % ROOT)
    print("")


def remove(name, force=False):
    wt = path_for(name)
    if not wt.exists():
        print("nothing at %s" % wt)
        return 0
    unlink_sys(wt)
    args = ["git", "worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(wt))
    r = subprocess.run(args, capture_output=True, text=True, cwd=str(ROOT))
    if r.returncode != 0:
        print("git worktree remove failed: %s" % (r.stderr or r.stdout).strip())
        return 1
    subprocess.run(["git", "worktree", "prune"], cwd=str(ROOT), capture_output=True)
    print("removed %s" % wt)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=("create", "create-many", "commands", "remove"))
    ap.add_argument("name", nargs="+")
    ap.add_argument("--branch")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if a.action == "create-many":
        # Sequential on purpose: concurrent `git worktree add` on one repository
        # races on the ref store, and a wave of ten agents needs all of them.
        rc = 0
        for n in a.name:
            rc |= create(n) or 0
        return rc
    if a.action == "create":
        return create(a.name[0], a.branch)
    if a.action == "commands":
        commands(a.name[0])
        return 0
    return remove(a.name[0], a.force)


if __name__ == "__main__":
    sys.exit(main() or 0)
