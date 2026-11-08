#!/usr/bin/env python3
"""
backup_private.py - back up the career material that must never reach the public repo.

This workspace pushes to github.com/Mukul1312/ai-job-desk, which is PUBLIC. So
facts/, prep/, docs/, applications.json and config/skill_demand.json are
gitignored here. That protects them from publication and backs up nothing: on
2026-11-08 the fact base was 83 verified facts, built over a full session, on
one disk.

This mirrors them into a SEPARATE PRIVATE repo, the same way run.sh already
handles data/ (see README, "Data backup").

    python tools/backup_private.py            # mirror, commit, push
    python tools/backup_private.py --dry-run  # show what would change
    python tools/backup_private.py --no-push  # commit locally only

One-way, always. The workspace is the source of truth and nothing is ever
copied back, because a backup that can overwrite the thing it backs up is a
way to lose work rather than keep it.

Three refusals, in order, because the whole point is not publishing this:

  1. the destination remote must be a repo GitHub reports as private
  2. the destination remote must not be this workspace's own origin
  3. a sync that would delete more than DELETE_LIMIT files stops and asks

Deletions are mirrored so a removed fact really disappears - but the third
refusal exists because "the source directory was empty" and "the user deleted
everything on purpose" look identical to a copy loop, and only one of them
should be allowed to erase the backup.
"""

import argparse
import filecmp
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

WORKSPACE = Path(__file__).parent.parent
BACKUP = WORKSPACE.parent / "ai-job-desk-career"
REMOTE = "https://github.com/Mukul1312/ai-job-desk-career.git"
REPO_SLUG = "Mukul1312/ai-job-desk-career"

# What goes in. Directories are mirrored whole; files are copied in place.
PATHS = [
    "facts",
    "prep",
    "docs",
    "applications",
    "applications.json",
    "config/skill_demand.json",
]

# Never copy these, even from inside an included directory.
EXCLUDE_NAMES = {"__pycache__", ".git", ".DS_Store", "node_modules"}
EXCLUDE_SUFFIX = {".pyc", ".pyo"}

DELETE_LIMIT = 20


def run(cmd, cwd=None, check=True, capture=True):
    r = subprocess.run(cmd, cwd=cwd, check=False, text=True,
                       capture_output=capture)
    if check and r.returncode != 0:
        sys.exit(f"  !! {' '.join(cmd)}\n{(r.stderr or r.stdout or '').strip()}")
    return r


def wanted(p: Path) -> bool:
    if p.name in EXCLUDE_NAMES or p.suffix in EXCLUDE_SUFFIX:
        return False
    return not any(part in EXCLUDE_NAMES for part in p.parts)


def collect():
    """Every file to back up, as (absolute source, path relative to the repo)."""
    out = []
    for rel in PATHS:
        src = WORKSPACE / rel
        if not src.exists():
            print(f"  -- {rel} does not exist, skipping")
            continue
        if src.is_file():
            out.append((src, Path(rel)))
            continue
        for f in sorted(src.rglob("*")):
            if f.is_file() and wanted(f):
                out.append((f, f.relative_to(WORKSPACE)))
    return out


def assert_private():
    """Refuse to push anywhere GitHub does not call private."""
    r = run(["gh", "repo", "view", REPO_SLUG, "--json", "isPrivate,visibility"],
            check=False)
    if r.returncode != 0:
        sys.exit(f"  !! cannot read {REPO_SLUG} - is gh authenticated?\n"
                 f"     {(r.stderr or '').strip()}")
    info = json.loads(r.stdout)
    if not info.get("isPrivate"):
        sys.exit(f"  !! {REPO_SLUG} is {info.get('visibility')}, not private. "
                 f"Refusing - this material is not for publication.")
    print(f"  destination {REPO_SLUG} is PRIVATE")


def assert_not_public_origin():
    """Refuse if the backup remote is this workspace's own (public) origin."""
    here = run(["git", "remote", "get-url", "origin"], cwd=WORKSPACE,
               check=False).stdout.strip()
    if here and here.rstrip("/").removesuffix(".git") == REMOTE.rstrip("/").removesuffix(".git"):
        sys.exit("  !! the backup remote is this workspace's own origin. Refusing.")


def ensure_clone():
    if (BACKUP / ".git").exists():
        return
    print(f"  cloning {REPO_SLUG} -> {BACKUP}")
    BACKUP.parent.mkdir(parents=True, exist_ok=True)
    r = run(["git", "clone", REMOTE, str(BACKUP)], check=False)
    if r.returncode != 0:  # empty repo: clone warns but may still fail on some gits
        BACKUP.mkdir(parents=True, exist_ok=True)
        run(["git", "init", "-b", "main"], cwd=BACKUP)
        run(["git", "remote", "add", "origin", REMOTE], cwd=BACKUP)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="show changes, write nothing")
    ap.add_argument("--no-push", action="store_true", help="commit locally, do not push")
    ap.add_argument("--force-delete", action="store_true",
                    help="allow more than %d deletions" % DELETE_LIMIT)
    a = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    print(f"\n  source      {WORKSPACE}")
    print(f"  destination {BACKUP}")
    assert_not_public_origin()
    assert_private()

    files = collect()
    if not files:
        sys.exit("  !! nothing to back up - refusing to mirror an empty set")

    if not a.dry_run:
        ensure_clone()

    # The backup's own README is generated here, not mirrored from the source,
    # so it must be exempt from the deletion pass or every run deletes and
    # rewrites it and the history fills with churn.
    OWN = {Path("README.md")}

    live = {rel for _, rel in files} | OWN
    existing = set()
    if BACKUP.exists():
        for f in BACKUP.rglob("*"):
            if f.is_file() and ".git" not in f.parts:
                existing.add(f.relative_to(BACKUP))

    added = [r for _, r in files if r not in existing]
    gone = sorted(existing - live)
    changed = []
    for src, rel in files:
        dst = BACKUP / rel
        if dst.exists() and not filecmp.cmp(src, dst, shallow=False):
            changed.append(rel)

    print(f"\n  {len(files)} file(s) to back up")
    print(f"    new      {len(added)}")
    print(f"    changed  {len(changed)}")
    print(f"    removed  {len(gone)}")
    for r in added[:6]:
        print(f"      + {r}")
    for r in changed[:6]:
        print(f"      ~ {r}")
    for r in gone[:6]:
        print(f"      - {r}")

    if len(gone) > DELETE_LIMIT and not a.force_delete:
        sys.exit(f"\n  !! {len(gone)} deletions exceeds the limit of {DELETE_LIMIT}.\n"
                 f"     If the source really shrank that much, re-run with --force-delete.\n"
                 f"     Otherwise something is wrong and the backup just saved you.")

    if a.dry_run:
        print("\n  --dry-run: nothing written\n")
        return 0

    for src, rel in files:
        dst = BACKUP / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    for rel in gone:
        (BACKUP / rel).unlink(missing_ok=True)

    readme = BACKUP / "README.md"
    if not readme.exists():
        readme.write_text(
            "# ai-job-desk-career (private)\n\n"
            "Backup of the career material that is gitignored in the public\n"
            "`ai-job-desk` workspace. **Do not make this repo public.**\n\n"
            "| Path | What |\n|---|---|\n"
            "| `facts/` | verified work history; names colleagues and records whose work is whose |\n"
            "| `prep/` | interview concept cards |\n"
            "| `docs/` | session notes, decisions, integration plan |\n"
            "| `applications.json` | the application tracker |\n"
            "| `config/skill_demand.json` | self-rated skill levels |\n\n"
            "Written one-way by `tools/backup_private.py` in the workspace.\n"
            "Never edit here - the workspace is the source of truth and the next\n"
            "sync overwrites anything changed in this repo.\n",
            encoding="utf-8")

    run(["git", "add", "-A"], cwd=BACKUP)
    st = run(["git", "status", "--porcelain"], cwd=BACKUP).stdout.strip()
    if not st:
        print("\n  already up to date\n")
        return 0

    msg = f"backup: {len(added)} new, {len(changed)} changed, {len(gone)} removed"
    run(["git", "commit", "-q", "-m", msg], cwd=BACKUP)
    print(f"\n  committed: {msg}")

    if a.no_push:
        print("  --no-push: not pushed\n")
        return 0

    branch = run(["git", "branch", "--show-current"], cwd=BACKUP).stdout.strip() or "main"
    r = run(["git", "push", "-u", "origin", branch], cwd=BACKUP, check=False)
    if r.returncode != 0:
        print(f"  !! push failed:\n{(r.stderr or '').strip()}")
        print("     The commit is safe locally; push by hand when you can.\n")
        return 1
    print(f"  pushed to {REPO_SLUG} ({branch})\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
