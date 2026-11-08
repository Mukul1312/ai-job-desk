#!/usr/bin/env python3
"""
prep.py — concept cards, checked against the fact base.

The Notion workspace this replaces stated the rule it ran on:

    "A concept without an example is forgettable; an example without a concept
     doesn't generalise. Before an interview: for each Concept Card you claim to
     know, make sure you can name one Case Study example that uses it."

There it was a discipline you had to remember. Here it is a validator. A card
whose `facts:` do not resolve to real ids in facts/ fails `--check`, the same way
a fact with no evidence fails `tools/facts.py --check`.

    python tools/prep.py                 # cards, with their backing
    python tools/prep.py --check         # validate; non-zero on failure
    python tools/prep.py --concept cqrs  # one card's backing facts, in full
    python tools/prep.py --gaps          # concepts named in a card's `related` with no card yet

Cards live in prep/concepts/*.md and open with YAML-ish frontmatter:

    ---
    concept: CQRS
    status: seeded | stub
    facts: [ttl-leaderboard, ttl-approxrank]
    adrs: [0001-leaderboard-internal-cqrs]
    related: [event-driven-sync, ledger-vs-mutable-state]
    ---

Only `concept` and `facts` are required. `facts` may be empty on a `stub`, which
is how you record that a concept is worth learning before you have done it.
"""

import json
import re
import sys
from pathlib import Path

WORKSPACE = Path(__file__).parent.parent
CARDS = WORKSPACE / "prep" / "concepts"
FACTS = WORKSPACE / "facts"

FM = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)


def parse_front(text):
    """Minimal frontmatter reader: scalars and [a, b] lists. No dependency."""
    m = FM.match(text)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        v = v.strip()
        if v.startswith("[") and v.endswith("]"):
            inner = v[1:-1].strip()
            meta[k.strip()] = [x.strip().strip("\"'") for x in inner.split(",") if x.strip()]
        else:
            meta[k.strip()] = v.strip("\"'")
    return meta, text[m.end():]


def load_fact_index():
    """Every fact id in the base, mapped to (project title, headline)."""
    idx = {}
    for p in sorted(FACTS.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for f in d.get("facts", []):
            idx[f["id"]] = (d.get("title", p.stem), f.get("headline", ""))
    return idx


def load_cards():
    if not CARDS.exists():
        return []
    out = []
    for p in sorted(CARDS.glob("*.md")):
        meta, body = parse_front(p.read_text(encoding="utf-8"))
        meta["_path"] = p
        meta["_slug"] = p.stem
        meta["_body"] = body
        meta["_words"] = len(body.split())
        out.append(meta)
    return out


def cmd_check(cards, idx):
    problems = 0
    for c in cards:
        name = c["_slug"]
        if not c.get("concept"):
            print(f"  {name}: missing `concept`")
            problems += 1
        status = c.get("status", "stub")
        if status not in ("seeded", "stub"):
            print(f"  {name}: status must be seeded or stub, got {status!r}")
            problems += 1
        facts = c.get("facts", [])
        missing = [f for f in facts if f not in idx]
        if missing:
            print(f"  {name}: these facts do not exist: {', '.join(missing)}")
            problems += 1
        if status == "seeded" and not facts:
            # The whole point of the rule. A card you claim to know needs an example.
            print(f"  {name}: marked seeded but names no backing fact - "
                  f"a concept without an example is forgettable")
            problems += 1
        if status == "seeded" and c["_words"] < 120:
            print(f"  {name}: marked seeded but only {c['_words']} words; is it really?")
    print(f"\n{len(cards)} card(s), {problems} problem(s)")
    return 1 if problems else 0


def cmd_list(cards, idx):
    if not cards:
        print(f"No cards in {CARDS}. See prep/README.md.")
        return 0
    seeded = [c for c in cards if c.get("status") == "seeded"]
    print(f"\n{len(cards)} card(s), {len(seeded)} seeded\n")
    for c in sorted(cards, key=lambda c: (c.get("status") != "seeded", c["_slug"])):
        mark = "*" if c.get("status") == "seeded" else " "
        print(f" {mark} {c['_slug']:<32} {c.get('concept','?')[:40]}")
        for fid in c.get("facts", []):
            if fid in idx:
                proj, head = idx[fid]
                print(f"      <- {proj[:22]:<22} {head[:48]}")
            else:
                print(f"      <- {fid}  (MISSING)")
    return 0


def cmd_concept(cards, idx, slug):
    hit = [c for c in cards if slug.lower() in c["_slug"] or slug.lower() in c.get("concept", "").lower()]
    if len(hit) != 1:
        print(f"  {len(hit)} cards match {slug!r}")
        return 1
    c = hit[0]
    print(f"\n# {c.get('concept')}  [{c.get('status','stub')}]\n")
    if not c.get("facts"):
        print("  No backing facts recorded.\n")
    for fid in c.get("facts", []):
        for p in sorted(FACTS.glob("*.json")):
            d = json.loads(p.read_text(encoding="utf-8"))
            for f in d.get("facts", []):
                if f["id"] == fid:
                    print(f"## {f['headline']}  ({d.get('title')})\n")
                    print(f"{f.get('detail') or f['bullet']}\n")
                    if f.get("metrics"):
                        print("Metrics: " + "; ".join(f["metrics"]) + "\n")
    return 0


def cmd_gaps(cards):
    have = {c["_slug"] for c in cards}
    wanted = {}
    for c in cards:
        for r in c.get("related", []):
            if r not in have:
                wanted.setdefault(r, []).append(c["_slug"])
    if not wanted:
        print("No gaps: every related concept has a card.")
        return 0
    print(f"\n{len(wanted)} concept(s) referenced but not written:\n")
    for slug, by in sorted(wanted.items(), key=lambda kv: -len(kv[1])):
        print(f"  {slug:<34} wanted by: {', '.join(by)}")
    return 0


def main(argv):
    cards = load_cards()
    idx = load_fact_index()
    a = argv[1:]
    if "--check" in a:
        return cmd_check(cards, idx)
    if "--gaps" in a:
        return cmd_gaps(cards)
    if "--concept" in a:
        i = a.index("--concept")
        if len(a) <= i + 1:
            print("  --concept needs a slug")
            return 2
        return cmd_concept(cards, idx, a[i + 1])
    return cmd_list(cards, idx)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
