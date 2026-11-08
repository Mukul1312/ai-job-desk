#!/usr/bin/env python3
"""
queue.py - turn the tracker's open roles into a ranked apply queue.

track.py answers "what needs doing". This answers the question that comes
immediately after it: which one first, and with which resume.

That gap is where the last attempt died. 65 roles were researched and ranked by
hand in Notion, and nothing was sent, because "apply to 54 things" is not a task
anyone starts on a Tuesday evening. A queue with the top role named, its resume
variant named, and the three facts to lead with is a task you can start.

Scoring is deliberately simple and auditable:

  * every fact in facts/ contributes its keywords to the tiers it belongs to
  * a role's title and notes are matched against those keywords
  * role-shape words (forward deployed, speech, frontend, ...) add weight, because
    a title says more about which resume to send than any single keyword does
  * ties break toward the tier with more high-strength facts behind it

The output is grouped by resume variant on purpose. Sending five FDE applications
in one sitting is one context switch; five applications across five variants is five.

    python tools/queue.py                 # the ranked queue
    python tools/queue.py --top 10        # just the next ten
    python tools/queue.py --tier fde      # one variant's roles
    python tools/queue.py --write         # also write docs/generated/apply-queue.md
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

WS = Path(__file__).parent.parent
STORE = WS / "applications.json"
FACTS = WS / "facts"
RESUMES = Path("C:/Users/Mukul/Desktop/job-upgrade")

VARIANT_FILE = {
    "ai": "Mukul_Dubey_Resume_AI",
    "ml": "Mukul_Dubey_Resume_ML",
    "swe": "Mukul_Dubey_Resume_SWE",
    "fde": "Mukul_Dubey_Resume_FDE",
    "frontend": "Mukul_Dubey_Resume_Frontend",
}

# Title shapes carry more signal about which resume to send than any keyword does.
SHAPE = (
    ("forward deployed", "fde", 7), ("solutions engineer", "fde", 7),
    ("solution engineer", "fde", 7), ("solutions architect", "fde", 6),
    ("deployment strategist", "fde", 6), ("pre-sales", "fde", 6),
    ("customer engineer", "fde", 5), ("partner solution", "fde", 5),
    ("machine learning engineer", "ml", 7), ("ml engineer", "ml", 7),
    ("research engineer", "ml", 6), ("speech", "ml", 4), ("audio", "ml", 4),
    ("data engineer", "ml", 4), ("mlops", "ml", 5), ("ml infra", "ml", 5),
    ("ml ops", "ml", 5), ("data intelligence", "ml", 4),
    ("applied ai", "ai", 7), ("ai engineer", "ai", 7), ("ai product", "ai", 6),
    ("agentic", "ai", 6), ("genai", "ai", 5), ("llm", "ai", 5),
    ("frontend", "frontend", 8), ("front-end", "frontend", 8), ("front end", "frontend", 8),
    ("full stack", "swe", 5), ("fullstack", "swe", 5), ("back-end", "swe", 6),
    ("backend", "swe", 6), ("software engineer", "swe", 4), ("platform", "swe", 3),
    ("infrastructure", "swe", 3), ("embedded", "swe", 3),
)

DEAD_ROLE_HINTS = ("no current", "no openings", "duplicate")

# Location gating. Added 2026-11-08 after the first version ranked a Singapore role
# first: it scored keywords only, and a perfect skills match you cannot legally take
# is worse than no match, because it costs an evening to discover.
#
# You are in Kanpur, India. A role qualifies only if it is open worldwide, open to
# India, or physically in India. Anything scoped to another country's residents is
# out regardless of how well the skills line up.
BLOCKED_LOCATION = (
    "us or europe only", "remote within the us", "usa — remote", "usa - remote",
    "us citizen", "visa", "in-office", "in-person", "nyc or sf", "required)",
    "remote (uk)", "remote (eea)", "au/nz/sg", "regionally b",
)
BLOCKED_PLACE = (
    "san francisco", "sf headquarters", "seattle", "mountain view", "nicasio",
    "cambridge/london", "ireland", "lehi", "singapore", "kuala lumpur only",
)
ALLOWED_SIGNAL = (
    "worldwide", "remote (india)", "india — remote", "india - remote",
    "executable globally", "sea remote", "globally",
)
INDIA_PLACE = (
    "india", "bengaluru", "bangalore", "chennai", "delhi", "mumbai", "pune",
    "hyderabad", "noida", "gurgaon", "kanpur",
)


def eligibility(role, notes):
    """ELIGIBLE / CHECK / BLOCKED for someone based in India."""
    t = ("%s %s" % (role, notes)).lower()
    # The first segment of `notes` is the location as recorded. A bare country name
    # there ("USA", "Ireland") means that country only, even with no other wording.
    head = (notes or "").split("·")[0].strip().lower()
    for country in ("usa", "united states", "us", "uk", "united kingdom", "ireland",
                    "singapore", "australia", "canada", "germany", "france"):
        if head == country or head.startswith(country + " ") or head.startswith(country + ","):
            if not any(a in t for a in ALLOWED_SIGNAL):
                return "BLOCKED", head[:28]
    for a in ALLOWED_SIGNAL:
        if a in t:
            return "ELIGIBLE", ""
    for b in BLOCKED_LOCATION:
        if b in t:
            return "BLOCKED", b
    # India-based roles are reachable, though some are onsite in another city.
    if any(p in t for p in INDIA_PLACE):
        onsite = "hybrid" in t or ("bengaluru" in t and "remote" not in t)
        return "ELIGIBLE", "india, onsite/hybrid" if onsite else ""
    for b in BLOCKED_PLACE:
        if b in t:
            return "BLOCKED", b
    return "CHECK", "location not stated"


def load_tiers():
    kw = defaultdict(set)
    strong = defaultdict(int)
    facts_by_tier = defaultdict(list)
    for f in sorted(FACTS.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        for fact in d["facts"]:
            for t in fact.get("tiers", []):
                facts_by_tier[t].append(fact)
                if fact.get("strength") == "high":
                    strong[t] += 1
                for k in fact.get("keywords", []):
                    kw[t].add(k.lower())
    return kw, strong, facts_by_tier


def score_role(text, kw, strong):
    t = text.lower()
    scores = defaultdict(int)
    hits = defaultdict(list)
    for tier, kws in kw.items():
        for k in kws:
            if k in t:
                scores[tier] += 1
                hits[tier].append(k)
    for word, tier, w in SHAPE:
        if word in t:
            scores[tier] += w
            hits[tier].append(word)
    if not scores:
        return "ai", 0, []
    best = max(scores.items(), key=lambda kv: (kv[1], strong.get(kv[0], 0)))
    return best[0], best[1], sorted(set(hits[best[0]]), key=len, reverse=True)[:6]


def lead_facts(tier, text, facts_by_tier, n=3):
    t = text.lower()
    ranked = []
    for f in facts_by_tier.get(tier, []):
        s = sum(2 for k in f.get("keywords", []) if k.lower() in t)
        s += 2 if f.get("strength") == "high" else 0
        s += 1 if f.get("metrics") else 0
        ranked.append((s, f))
    ranked.sort(key=lambda x: -x[0])
    return [f for _, f in ranked[:n]]


def main(argv):
    top = int(argv[argv.index("--top") + 1]) if "--top" in argv else None
    only = argv[argv.index("--tier") + 1] if "--tier" in argv else None

    kw, strong, facts_by_tier = load_tiers()
    db = json.loads(STORE.read_text(encoding="utf-8"))
    rows = [r for r in db["applications"] if r.get("status") == "interested"]

    scored, skipped = [], []
    seen = set()
    for r in rows:
        title = r.get("role") or r.get("title") or ""
        text = "%s %s %s" % (title, r.get("company", ""), r.get("notes", ""))
        if any(h in title.lower() for h in DEAD_ROLE_HINTS):
            skipped.append((r, "not a real posting"))
            continue
        key = (r.get("company", "").lower().strip(), title.lower().strip()[:40])
        if key in seen:
            skipped.append((r, "duplicate of an earlier row"))
            continue
        seen.add(key)
        tier, sc, hits = score_role(text, kw, strong)
        blocked = "[sweep" in (r.get("notes") or "")
        elig, why = eligibility(title, r.get("notes", ""))
        if elig == "BLOCKED" and "--all" not in argv:
            skipped.append((r, "location: %s" % why))
            continue
        scored.append({"r": r, "tier": tier, "score": sc, "hits": hits,
                       "blocked": blocked, "elig": elig, "elig_why": why})

    # Eligible first, then score. A role you can take beats a better keyword match
    # you cannot.
    scored.sort(key=lambda x: (x["elig"] != "ELIGIBLE", -x["score"]))
    if only:
        scored = [s for s in scored if s["tier"] == only]
    if top:
        scored = scored[:top]

    out = []
    w = out.append
    by_tier = defaultdict(list)
    for s in scored:
        by_tier[s["tier"]].append(s)

    w("%d open role(s), grouped by the resume to send" % len(scored))
    order = sorted(by_tier, key=lambda t: -len(by_tier[t]))
    for tier in order:
        group = by_tier[tier]
        name = VARIANT_FILE.get(tier, "?")
        pdf = RESUMES / (name + ".pdf")
        mark = "" if pdf.exists() else "   [PDF MISSING]"
        w("")
        w("=" * 74)
        w("%s  (%d role(s))   send: %s.pdf%s" % (tier.upper(), len(group), name, mark))
        w("=" * 74)
        for i, s in enumerate(group, 1):
            r = s["r"]
            flag = "  [site blocked the check - verify by hand]" if s["blocked"] else ""
            if s["elig"] == "CHECK":
                flag += "  [CHECK LOCATION]"
            elif s["elig_why"]:
                flag += "  [%s]" % s["elig_why"]
            w("")
            w("%2d. %s - %s%s" % (i, r.get("company", "?"), r.get("role", "?"), flag))
            w("    %s" % r.get("url", ""))
            loc = (r.get("notes") or "").split("·")[0].strip()
            if loc:
                w("    where: %s" % loc[:66])
            if s["hits"]:
                w("    matched: %s" % ", ".join(s["hits"]))
            ctx = "%s %s" % (r.get("role", ""), r.get("notes", ""))
            for f in lead_facts(tier, ctx, facts_by_tier):
                w("    lead with: %s  (%s)" % (f["headline"], f["id"]))
    if skipped:
        w("")
        w("=" * 74)
        w("SKIPPED (%d)" % len(skipped))
        for r, why in skipped:
            w("  %s - %s  :: %s" % (r.get("company", "?"), (r.get("role", "?") or "")[:46], why))

    text = "\n".join(out)
    print(text)

    if "--write" in argv:
        p = WS / "docs" / "generated" / "apply-queue.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        header = "# Apply queue\n\nGenerated by `tools/queue.py`. Regenerate after any sweep.\n\n```\n"
        p.write_text(header + text + "\n```\n", encoding="utf-8")
        print("\nwritten to %s" % p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
