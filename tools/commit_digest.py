#!/usr/bin/env python3
"""
commit_digest.py — build an evidence base for resume claims from git history.

A resume bullet is a claim about what you did. Git history is the record of what
you actually did, with dates and co-authors attached. This walks every repo under
a root, pulls out one author's commits, and writes a digest you can check a
bullet against before sending it.

    python tools/commit_digest.py ~/code --author <your-git-email-prefix>
    python tools/commit_digest.py ~/code --author <your-git-email-prefix> \\
        --out docs/generated/commit-digest.md

Why it matters beyond writing bullets: on a team project, the interesting
engineering stories belong to whoever did them. Claiming a colleague's debugging
story is the fastest way to lose an interview, because the follow-up question is
always "walk me through how you found it". The per-repo author split here shows
which parts of a shared project are yours to tell.

It reads git metadata only - no diffs, no file contents - so it is safe to run
across a workspace containing client code.
"""

import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

SEP = "\x1f"       # unit separator; commit messages never contain it
RECORD = "\x1e"    # record separator

# Commit-message vocabulary, grouped into the themes a resume cares about.
TOPICS = {
    "architecture": ["ddd", "domain", "hexagonal", "adr", "context-map",
                     "restructure", "refactor", "boundary", "layer"],
    "observability": ["observability", "sentry", "grafana", "monitor",
                      "metric", "logging", "trace", "alert", "inventory"],
    "infrastructure": ["cloudflare", "worker", "wrangler", "hyperdrive", "kv",
                       "deploy", "staging", "migrate", "account", "docker",
                       "ci", "pipeline", "terraform"],
    "database": ["postgres", "neon", "migration", "schema", "sql", "query",
                 "index", "supabase", "mongo", "redis"],
    "ai/ml": ["llm", "prompt", "agent", "openai", "claude", "model", "eval",
              "embedding", "rag", "whisper", "transcri", "speech", "tts"],
    "security/abuse": ["auth", "oauth", "jwt", "turnstile", "rate limit",
                       "waf", "exploit", "cve", "baduser", "bad user", "fraud",
                       "cheat", "abuse", "spoof", "security"],
    "frontend": ["ui", "component", "react", "css", "style", "tailwind",
                 "responsive", "layout", "animation", "figma", "design"],
    "api/backend": ["api", "endpoint", "handler", "route", "service",
                    "webhook", "queue", "worker", "backend"],
    "testing": ["test", "spec", "coverage", "e2e", "load test", "k6",
                "benchmark"],
    "docs": ["doc", "readme", "comment", "changelog"],
}

NOISE = re.compile(
    r"(?i)^(merge (branch|pull request|remote)|wip\b|fixup|squash|"
    r"index on |untracked files on |on \w+: |bump|chore\(deps\)|"
    r"initial commit|update|updated|add|added|fix|fixes|minor|test)$"
)


def run(repo, args):
    try:
        out = subprocess.run(
            ["git", "-C", str(repo)] + args,
            capture_output=True, text=True, timeout=60,
            encoding="utf-8", errors="replace",
        )
        return out.stdout if out.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return ""


# Directories never worth descending into. node_modules in particular nests
# deep enough on Windows to blow past MAX_PATH, and rglob cannot be stopped
# part way - it raises before any filter of ours gets a say. So the walk prunes
# as it goes rather than filtering afterwards.
PRUNE = {
    "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
    ".next", ".nuxt", "vendor", "target", ".cache", "Pods", ".terraform",
}


def find_repos(root, max_depth=5):
    import os

    root = Path(root)
    repos = []
    for dirpath, dirnames, _ in os.walk(root, topdown=True, onerror=lambda e: None):
        here = Path(dirpath)
        try:
            depth = len(here.relative_to(root).parts)
        except ValueError:
            dirnames[:] = []
            continue

        if ".git" in dirnames:
            repos.append(here)
            # A repo's own contents cannot hold another repo we care about,
            # and submodules are reached through the parent anyway.
            dirnames[:] = [d for d in dirnames
                           if d not in PRUNE and not d.startswith(".")]

        if depth >= max_depth:
            dirnames[:] = []
            continue

        dirnames[:] = [d for d in dirnames if d not in PRUNE]

    return sorted(set(repos))


def repo_stats(repo, author):
    """Commits by author and by everyone, with no diffs read."""
    fmt = f"%H{SEP}%an{SEP}%ae{SEP}%ad{SEP}%s{RECORD}"
    raw = run(repo, ["log", "--all", f"--format={fmt}", "--date=short"])
    if not raw:
        return None

    mine, everyone = [], Counter()
    needle = author.lower()
    for rec in raw.split(RECORD):
        rec = rec.strip("\n")
        if not rec:
            continue
        parts = rec.split(SEP)
        if len(parts) < 5:
            continue
        sha, name, email, date, subject = parts[:5]
        everyone[f"{name} <{email}>"] += 1
        if needle in email.lower() or needle in name.lower():
            mine.append({"sha": sha[:8], "date": date, "subject": subject})

    if not mine:
        return None

    dates = sorted(c["date"] for c in mine)
    topics = defaultdict(list)
    for c in mine:
        low = c["subject"].lower()
        for topic, words in TOPICS.items():
            if any(w in low for w in words):
                topics[topic].append(c)

    total = sum(everyone.values())
    return {
        "repo": repo,
        "mine": mine,
        "total": total,
        "share": 100.0 * len(mine) / total if total else 0.0,
        "authors": everyone,
        "first": dates[0],
        "last": dates[-1],
        "topics": topics,
        "solo": len(everyone) == 1,
    }


def substantive(commits, limit=14):
    """Commit subjects that say something, longest first."""
    out = [c for c in commits if not NOISE.match(c["subject"].strip())
           and len(c["subject"]) > 18]
    out.sort(key=lambda c: -len(c["subject"]))
    return out[:limit]


def write_digest(results, author, out_path):
    lines = [
        f"# Commit digest - {author}",
        "",
        f"Generated {datetime.now():%Y-%m-%d} from git metadata only "
        f"(no diffs, no file contents).",
        "",
        "Use this to check a resume bullet against what the history actually "
        "shows. On shared repos, the author split says which parts of the "
        "project are yours to talk about in an interview.",
        "",
    ]

    team = [r for r in results if not r["solo"]]
    solo = [r for r in results if r["solo"]]
    total_commits = sum(len(r["mine"]) for r in results)

    lines += [
        "## Summary",
        "",
        f"- **{total_commits} commits** across **{len(results)} repositories**",
        f"- {len(team)} team repos, {len(solo)} solo repos",
        f"- Span: {min(r['first'] for r in results)} to "
        f"{max(r['last'] for r in results)}",
        "",
        "## Team repositories",
        "",
        "Where the work was shared. The share column is the strongest evidence "
        "for how much of a project is honestly yours to claim.",
        "",
        "| Repository | Your commits | Share | Period | Other contributors |",
        "|---|---:|---:|---|---|",
    ]
    for r in sorted(team, key=lambda r: -len(r["mine"])):
        others = len(r["authors"]) - 1
        lines.append(
            f"| `{r['repo'].name}` | {len(r['mine'])} / {r['total']} | "
            f"{r['share']:.0f}% | {r['first']} to {r['last']} | {others} |"
        )

    lines += ["", "## Detail", ""]
    for r in sorted(results, key=lambda r: -len(r["mine"])):
        if len(r["mine"]) < 3:
            continue
        lines += [
            f"### {r['repo'].name}",
            "",
            f"`{r['repo']}`",
            "",
            f"**{len(r['mine'])} of {r['total']} commits "
            f"({r['share']:.0f}%)**, {r['first']} to {r['last']}"
            + ("" if r["solo"] else
               f", alongside {len(r['authors']) - 1} other contributor(s)"),
            "",
        ]

        if r["topics"]:
            ranked = sorted(r["topics"].items(), key=lambda kv: -len(kv[1]))
            lines.append("Topics: " + ", ".join(
                f"{t} ({len(c)})" for t, c in ranked[:7]))
            lines.append("")

        if not r["solo"]:
            lines.append("Contributors:")
            lines.append("")
            for who, n in r["authors"].most_common(6):
                mark = "  <- you" if author.lower() in who.lower() else ""
                lines.append(f"- {who}: {n}{mark}")
            lines.append("")

        picks = substantive(r["mine"])
        if picks:
            lines.append("Representative commits:")
            lines.append("")
            for c in picks:
                lines.append(f"- `{c['date']}` {c['subject']}")
            lines.append("")

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text("\n".join(lines), encoding="utf-8")
    return total_commits


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    root = argv[1]
    author = argv[argv.index("--author") + 1] if "--author" in argv else None
    out = (argv[argv.index("--out") + 1] if "--out" in argv
           else "docs/generated/commit-digest.md")
    if not author:
        print("--author is required (an email fragment or name)")
        return 2

    repos = find_repos(root)
    print(f"Scanning {len(repos)} repositories under {root} ...")

    results = []
    for repo in repos:
        stats = repo_stats(repo, author)
        if stats:
            results.append(stats)

    if not results:
        print(f"No commits found for {author!r}.")
        return 1

    total = write_digest(results, author, out)
    team = [r for r in results if not r["solo"]]
    print(f"\n{total} commits across {len(results)} repos "
          f"({len(team)} shared with others)")
    print("\nLargest contributions:")
    for r in sorted(results, key=lambda r: -len(r["mine"]))[:12]:
        tag = "solo" if r["solo"] else f"{r['share']:.0f}% of {r['total']}"
        print(f"  {len(r['mine']):>4} commits  {r['repo'].name[:38]:<38} {tag}")
    print(f"\nWritten to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
