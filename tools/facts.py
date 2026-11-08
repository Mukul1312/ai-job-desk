#!/usr/bin/env python3
"""
facts.py — pick resume material out of the fact base.

A resume holds roughly eighteen bullets. The work does not fit, and which
eighteen belong changes with the posting. So the full record lives in facts/
and this picks from it, which makes tailoring a selection problem instead of a
remembering problem.

    python tools/facts.py --list
    python tools/facts.py --tier ai --strength high
    python tools/facts.py --match "agentic llm evaluation production"
    python tools/facts.py --match-job <url-or-file>     # match against a real JD
    python tools/facts.py --check
    python tools/facts.py --id ttl-anticheat --format detail

Matching scores a fact against the words in a job description, counting its
keywords and the text of the bullet itself. Keywords exist because postings
rarely use your vocabulary: Cloudflare Workers is "edge", a bug-fixer state
machine is "agentic". Recording both is what lets a fact be found.

--check validates every file and reports what is missing. It fails on a fact
with no evidence, because an unsupported bullet is the thing that collapses
under questioning.
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path

WORKSPACE = Path(__file__).parent.parent
FACTS_DIR = WORKSPACE / "facts"

VALID_ATTRIBUTION = {"mine", "shared", "not-mine"}
VALID_STRENGTH = {"high", "medium", "low"}
REQUIRED = ["id", "headline", "bullet", "attribution", "tiers", "keywords",
            "strength"]

STOP = set("""a an the and or of for to in on at by with from as is are was were
be been being this that these those it its if then than so but not no we our
you your they their he she his her i me my us them who whom which what when
where how why all any both each few more most other some such only own same
too very can will just should now about into over under again further once
here there up down out off above below between through during before after""".split())


def _is_team(proj):
    """True when more than one person worked on the project.

    `team.size` is written by hand and is not always a number - dualite-alpha
    records "~8 regulars" because the exact count genuinely is not known. Treat
    any non-numeric value as "more than one", which is what a hedged string
    always means here.
    """
    team = proj.get("team") or {}
    if not team:
        return False
    size = team.get("size", 1)
    if isinstance(size, str):
        digits = "".join(c for c in size if c.isdigit())
        return int(digits) > 1 if digits else True
    return size > 1


def load_all():
    """Every project file, newest project first."""
    if not FACTS_DIR.exists():
        return []
    out = []
    for path in sorted(FACTS_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"  {path.name}: invalid JSON - {e}", file=sys.stderr)
            continue
        data["_path"] = path
        out.append(data)
    out.sort(key=lambda d: d.get("start", ""), reverse=True)
    return out


def iter_facts(projects, tier=None, strength=None, attribution="mine"):
    for proj in projects:
        for fact in proj.get("facts", []):
            if attribution and fact.get("attribution") != attribution:
                continue
            if tier and tier not in fact.get("tiers", []):
                continue
            if strength and fact.get("strength") != strength:
                continue
            yield proj, fact


def tokens(text):
    return {w for w in re.findall(r"[a-z0-9.+#-]{2,}", (text or "").lower())
            if w not in STOP}


def score_fact(fact, want):
    """
    How well a fact matches a job description.

    Keywords count double: they are the deliberate mapping from what you did to
    what postings call it, so a keyword hit is a stronger signal than the same
    word happening to appear in prose.
    """
    score = 0
    hits = []
    for kw in fact.get("keywords", []):
        kw_low = kw.lower()
        if kw_low in want or (" " in kw_low and kw_low in " ".join(want)):
            score += 2
            hits.append(kw)
    body = tokens(f"{fact.get('bullet','')} {fact.get('headline','')}")
    overlap = body & want
    score += len(overlap)
    # A measured number is the rarest thing on a resume; prefer it on a tie.
    if fact.get("strength") == "high":
        score += 1
    if fact.get("metrics"):
        score += 1
    return score, hits


def cmd_check(projects):
    problems = 0
    seen_ids = Counter()
    for proj in projects:
        name = proj["_path"].name
        for key in ("project", "title", "facts"):
            if key not in proj:
                print(f"  {name}: missing top-level {key!r}")
                problems += 1
        kbc = proj.get("know_before_claiming") or {}
        if not kbc.get("items") and _is_team(proj):
            print(f"  {name}: team project with no 'know_before_claiming' list - "
                  f"record which parts a colleague wrote, so the interview "
                  f"follow-up on them is prepared rather than a surprise")
            problems += 1
        for fact in proj.get("facts", []):
            fid = fact.get("id", "<no id>")
            seen_ids[fid] += 1
            for key in REQUIRED:
                if key not in fact:
                    print(f"  {name}:{fid}: missing {key!r}")
                    problems += 1
            if fact.get("attribution") not in VALID_ATTRIBUTION:
                print(f"  {name}:{fid}: attribution must be one of "
                      f"{sorted(VALID_ATTRIBUTION)}")
                problems += 1
            if fact.get("strength") not in VALID_STRENGTH:
                print(f"  {name}:{fid}: strength must be one of "
                      f"{sorted(VALID_STRENGTH)}")
                problems += 1
            if not fact.get("evidence"):
                print(f"  {name}:{fid}: no evidence - an unsupported bullet is "
                      f"what collapses under questioning")
                problems += 1
            if fact.get("strength") == "high" and not fact.get("metrics"):
                print(f"  {name}:{fid}: marked high strength but carries no "
                      f"metric; is that right?")
    for fid, n in seen_ids.items():
        if n > 1:
            print(f"  duplicate fact id {fid!r} ({n} times)")
            problems += 1

    total = sum(len(p.get("facts", [])) for p in projects)
    print(f"\n{len(projects)} project(s), {total} fact(s), "
          f"{problems} problem(s)")
    return 1 if problems else 0


def render(proj, fact, fmt):
    if fmt == "detail":
        out = [f"## {fact['headline']}  [{fact['id']}]",
               f"_{proj['title']}_ · {proj.get('start','')} to "
               f"{proj.get('end','')} · {fact['attribution']}", ""]
        out.append(fact.get("detail") or fact["bullet"])
        if fact.get("metrics"):
            out += ["", "Metrics: " + "; ".join(fact["metrics"])]
        if fact.get("evidence"):
            out += ["", "Evidence:"] + [f"  - {e}" for e in fact["evidence"]]
        return "\n".join(out)
    if fmt == "short":
        return f"- {fact.get('short') or fact['bullet']}"
    return f"- {fact['bullet']}"


def main(argv):
    projects = load_all()
    if not projects:
        print(f"No fact files in {FACTS_DIR}. See facts/README.md.")
        return 1

    if "--check" in argv:
        return cmd_check(projects)

    fmt = argv[argv.index("--format") + 1] if "--format" in argv else "bullet"
    tier = argv[argv.index("--tier") + 1] if "--tier" in argv else None
    strength = (argv[argv.index("--strength") + 1]
                if "--strength" in argv else None)
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else 0

    if "--id" in argv:
        want_id = argv[argv.index("--id") + 1]
        for proj, fact in iter_facts(projects, attribution=None):
            if fact["id"] == want_id:
                print(render(proj, fact, fmt if fmt != "bullet" else "detail"))
                return 0
        print(f"No fact with id {want_id!r}")
        return 1

    if "--match" in argv or "--match-job" in argv:
        if "--match-job" in argv:
            src = Path(argv[argv.index("--match-job") + 1])
            text = src.read_text(encoding="utf-8", errors="replace")
        else:
            text = argv[argv.index("--match") + 1]
        want = tokens(text)

        scored = []
        for proj, fact in iter_facts(projects, tier=tier):
            s, hits = score_fact(fact, want)
            if s > 0:
                scored.append((s, hits, proj, fact))
        scored.sort(key=lambda t: -t[0])
        if not scored:
            print("Nothing matched. Try --list to see what is recorded.")
            return 0

        print(f"Best material for this posting"
              f"{f' (tier: {tier})' if tier else ''}:\n")
        for s, hits, proj, fact in scored[:limit or 8]:
            print(f"[{s:>2}] {proj['title']} · {fact['id']}"
                  f"{'  <- ' + ', '.join(hits[:5]) if hits else ''}")
            print(render(proj, fact, fmt))
            print()
        return 0

    # Default: list everything, grouped by project.
    for proj in projects:
        facts = [f for _, f in iter_facts([proj], tier=tier, strength=strength)]
        if not facts:
            continue
        print(f"\n{proj['title']} ({proj.get('start','')} to "
              f"{proj.get('end','')})")
        team = proj.get("team")
        if _is_team(proj):
            print(f"  team of {team['size']}, your share "
                  f"{team.get('share_pct','?')}% "
                  f"({team.get('your_commits','?')}/"
                  f"{team.get('total_commits','?')} commits)")
        print("-" * 64)
        for fact in facts:
            if "--format" in argv:
                # An explicit format means "give me the text", not the index.
                print(render(proj, fact, fmt))
                print()
            else:
                print(f"  [{fact['strength'][:4]:<4}] {fact['id']:<18} "
                      f"{fact['headline']}")
        kbc = (proj.get("know_before_claiming") or {}).get("items") or []
        if kbc:
            print(f"  read up before claiming ({len(kbc)}): "
                  + "; ".join(n["what"][:52] for n in kbc[:3]))
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
