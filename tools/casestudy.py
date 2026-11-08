#!/usr/bin/env python3
"""
casestudy.py — render a project's facts as an interview narrative.

A case study is not new information. It is the fact base read aloud: the `detail`
fields are already the walkthrough, the `metrics` are the numbers, and `evidence`
is what makes them defensible. Writing a separate case-study document means
maintaining two copies of the same claims, and the copies drift - which is exactly
how a resume ends up naming a framework the project never used.

So this generates, and nothing is stored.

    python tools/casestudy.py --project ticket-to-lord
    python tools/casestudy.py --project ticket-to-lord --brief
    python tools/casestudy.py --project rghs-dictation --tier fde
    python tools/casestudy.py --list

`know_before_claiming` prints too, as a revision list rather than a disclaimer.
Team work is claimed at scope; these are the parts a colleague wrote, so the
follow-up question lands on something you have read rather than something you
have not.
"""

import json
import sys
import textwrap
from pathlib import Path

WORKSPACE = Path(__file__).parent.parent
FACTS = WORKSPACE / "facts"

WRAP = 86


def wrap(text, indent=""):
    return "\n".join(textwrap.fill(p, WRAP, initial_indent=indent,
                                   subsequent_indent=indent)
                     for p in text.split("\n") if p.strip())


def render(d, brief=False, tier=None):
    out = []
    add = out.append

    title = d.get("title", "?")
    add(f"# {title}")
    if d.get("subtitle"):
        add("")
        add(wrap(d["subtitle"]))
    add("")

    bits = []
    if d.get("role"):
        bits.append(d["role"])
    if d.get("employer"):
        bits.append(d["employer"])
    if d.get("start"):
        bits.append(f"{d['start']} to {d.get('end', 'present')}")
    if bits:
        add(" · ".join(bits))

    # Where you sat on the team is the first thing an interviewer calibrates on.
    team = d.get("team") or d.get("solo")
    if team:
        if team.get("share_pct") is not None:
            add(f"{team.get('your_commits','?')} of {team.get('total_commits','?')} commits "
                f"({team['share_pct']}%)" +
                (f", team of {team['size']}" if team.get("size") else ", sole author"))
        if team.get("note"):
            add("")
            add(wrap(team["note"]))

    for key, label in (("scale_measured", "Measured scale"), ("corpus", "Corpus"),
                       ("deployment", "Deployment"), ("funnel", "Funnel")):
        block = d.get(key)
        if not block:
            continue
        nums = {k: v for k, v in block.items()
                if not k.startswith("_") and isinstance(v, (int, float, str)) and v != ""}
        if nums:
            add("")
            add(f"**{label}.** " + wrap("; ".join(
                f"{k.replace('_', ' ')}: {v:,}" if isinstance(v, int) else f"{k.replace('_',' ')}: {v}"
                for k, v in list(nums.items())[:8])))

    facts = [f for f in d.get("facts", []) if f.get("attribution") != "not-mine"]
    if tier:
        facts = [f for f in facts if tier in f.get("tiers", [])]
    facts.sort(key=lambda f: (f.get("strength") != "high", f.get("id", "")))

    add("")
    add(f"## What I did ({len(facts)} pieces" + (f", tier: {tier}" if tier else "") + ")")

    for f in facts:
        add("")
        add(f"### {f['headline']}")
        if f.get("attribution") == "shared":
            add("*(shared work — see boundaries below)*")
        add("")
        add(wrap(f["short"] if brief else (f.get("detail") or f["bullet"])))
        if f.get("metrics"):
            add("")
            add(wrap("Numbers: " + "; ".join(f["metrics"])))
        if not brief and f.get("evidence"):
            add("")
            for e in f["evidence"][:4]:
                add(wrap(f"- {e}"))

    kbc = (d.get("know_before_claiming") or {}).get("items") or []
    if kbc:
        add("")
        add("## Read these before the interview")
        add("")
        add(wrap("Parts of this project a colleague wrote. The claim stands at scope - "
                 "this is what to have read, so a follow-up lands somewhere you have been."))
        for n in kbc:
            add("")
            add(wrap(f"**{n.get('who_wrote_it', '?')}** — {n['what']}"))
            if n.get("how_to_handle") and not brief:
                add(wrap(n["how_to_handle"], indent="  "))

    for key, label in (("do_not_use", "Do not claim"), ("method_notes", "Care with the numbers")):
        block = d.get(key) or []
        if not block:
            continue
        add("")
        add(f"## {label}")
        for item in block:
            add("")
            head = item.get("claim") or item.get("caution") or ""
            add(wrap(f"- {head}"))
            why = item.get("why") or item.get("consequence") or ""
            if why and not brief:
                add(wrap(why, indent="  "))

    return "\n".join(out)


def main(argv):
    a = argv[1:]
    files = sorted(FACTS.glob("*.json"))

    if "--list" in a or not a:
        print("\nProjects you can render:\n")
        for p in files:
            d = json.loads(p.read_text(encoding="utf-8"))
            n = len(d.get("facts", []))
            print(f"  {p.stem:<28} {n:>3} facts   {d.get('title','')[:44]}")
        print("\n  python tools/casestudy.py --project <name> [--brief] [--tier ai]")
        return 0

    if "--project" not in a:
        print("  --project is required (or --list)")
        return 2
    want = a[a.index("--project") + 1]
    tier = a[a.index("--tier") + 1] if "--tier" in a else None

    hit = [p for p in files if want.lower() in p.stem.lower()]
    if len(hit) != 1:
        print(f"  {len(hit)} projects match {want!r}: {', '.join(p.stem for p in hit)}")
        return 1

    d = json.loads(hit[0].read_text(encoding="utf-8"))
    print(render(d, brief="--brief" in a, tier=tier))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
