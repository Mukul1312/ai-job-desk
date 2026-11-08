#!/usr/bin/env python3
"""
jdextract.py - read what a posting actually says about who it will hire.

Layer 1 only so far: regex, free, no API, runs on text already on disk.
See docs/LLM-EXTRACTION-DESIGN.md for the full design and why layer 2 exists.

The problem this exists for: scorer.py counts keyword overlap and has no
representation for *where*, *how senior*, or *what kind of contract*. Those
three ruled out thirteen of the nineteen roles examined on 2026-11-08/09. A role
wanting 8-10 years scored fit 89; a 3-6 month internship scored 83.

What layer 1 can do, measured on 827 RemoteRocketship descriptions:
  67% contain an explicit years figure
  59% contain a seniority word

What layer 1 cannot do, and why layer 2 is not optional:

    MarcoPolo Learning asks for "5-8 years of full-stack software engineering"
    and then says "What you've built matters more to us than years on a resume."

    A regex reads the floor as 5 and ranks it down. That sentence is what made
    it the strongest target of nineteen. The number is cheap; the qualifier is
    where the value is.

So layer 1 is a candidate generator, not an answer. It finds the figures, flags
the sentences a model should look at, and fails closed everywhere else.

    python src/jdextract.py --eval     # score against tests/jd_extraction/
    python src/jdextract.py --scan     # distribution over today's snapshot
"""

import json
import re
import sys
from pathlib import Path

WORKSPACE = Path(__file__).parent.parent

# "5+ years", "3-5 years", "8-10+ years", "5 to 8 years", "minimum 2 years"
YEARS = re.compile(
    r"(?:(?P<min>\d{1,2})\s*(?:[-–—]|\s+to\s+)\s*(?P<max>\d{1,2})\+?"
    r"|(?P<only>\d{1,2})\+?)\s*(?:\+\s*)?years?\b",
    re.I)

# Words that mean the number is a requirement rather than trivia - the company's
# age, the product's history, "a decade of global employment expertise".
REQUIRE_CUE = re.compile(
    r"\b(experience|experienced|background|working|worked|building|built|"
    r"developing|engineering|minimum|at least|require[ds]?|looking for|"
    r"you have|you bring|track record|hands-on|in production)\b", re.I)

# The qualifier that reverses the floor. MarcoPolo and eventfirst both carry one,
# and both are reachable targets BECAUSE of it.
NEGOTIABLE = re.compile(
    r"(matters? more (?:to us )?than years"
    r"|more than years on a r\w*sum"
    r"|demonstrated skill over a fixed number of years"
    r"|years (?:of experience )?(?:are|is) less important"
    r"|we (?:do not|don't) count years)", re.I)

SENIORITY = [
    ("intern", r"\b(internship|intern program|accelerator program"
               r"|current(?:ly)? (?:university )?student|recent graduate)\b"),
    ("manager", r"\b(engineering manager|people manager|head of|director of)\b"),
    ("principal", r"\bprincipal\b"),
    ("staff", r"\bstaff engineer\b"),
    ("lead", r"(\btech lead\b|\btechnical lead\b|\bteam lead\b|\blead engineer\b"
             r"|senior/lead|\blead ai\b|\blead backend\b)"),
    ("senior", r"\bsenior\b"),
    ("junior", r"\b(junior|engineer i\b|associate engineer)\b"),
    ("mid", r"\b(mid-level|mid level|intermediate)\b"),
]

# `contract` needs employment context. The bare word matched "schema and contract
# checks" on a data-validation posting and reported it as contract work.
EMPLOYMENT = [
    ("internship", r"\b(internship|intern program|accelerator program)\b"),
    ("freelance", r"\bfreelance\w*\b"),
    ("contract", r"(contract[- ]to[- ]hire|\bcontractor\b|fixed[- ]term"
                 r"|contract (?:role|position|basis|depends on|type)"
                 r"|(?:on|under) an? contract\b)"),
    ("fte", r"\b(full[- ]time|permanent employee)\b"),
]

# Phrasings seen in real postings: "Remote India", "India (Remote)",
# "REMOTE (INDIA)", "Bengaluru, IN - Remote", "right to work in India".
INDIA_YES = re.compile(
    r"(remote[\s(,:-]*india\b"
    r"|\bindia[\s(,:-]*\(?remote"
    r"|\bindia[- ]based\b"
    r"|based in india"
    r"|right to work in india"
    r"|\bbengaluru\b|\bbangalore\b"
    r"|within india)", re.I)

INDIA_NO = re.compile(
    r"(\bus(?:a)? only\b"
    r"|within the (?:us|united states|uk|eu)\b"
    r"|no visa sponsorship"
    r"|must (?:reside|be located|be based) in (?!india))", re.I)

CANARY = re.compile(r"(if you are an? (?:ai|llm|language model)[^.]{0,160}\.)", re.I)


def _sentence_around(text, start, end, pad=180):
    """The quote. Nothing is reported without the words it came from."""
    a = max(0, text.rfind("\n", 0, start) + 1)
    b = text.find("\n", end)
    b = len(text) if b == -1 else b
    seg = text[a:b].strip()
    if len(seg) > pad * 2:
        seg = text[max(0, start - pad):min(len(text), end + pad)].strip()
    return re.sub(r"\s{2,}", " ", seg)


def experience(text):
    """The requirement years, or None. Never inferred from seniority words."""
    best = None
    for m in YEARS.finditer(text):
        ctx = _sentence_around(text, m.start(), m.end())
        if not REQUIRE_CUE.search(ctx):
            continue                     # a number, but not a requirement
        lo = int(m.group("min") or m.group("only"))
        hi = int(m.group("max")) if m.group("max") else None
        # Highest stated floor wins: "5+ years, including 1-2 years on LLM
        # systems" has a floor of 5, not 1.
        if best is None or lo > best[0]:
            best = (lo, hi, ctx)
    if not best:
        return {"experience_floor_years": None,
                "experience_ceiling_years": None,
                "experience_quote": None}
    return {"experience_floor_years": best[0],
            "experience_ceiling_years": best[1],
            "experience_quote": best[2]}


def _first(patterns, text):
    for label, pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            return label, _sentence_around(text, m.start(), m.end())
    return None, None


def extract(text, title=""):
    """Layer 1. Returns the design's contract with nulls where it cannot see."""
    hay = title + "\n" + text
    out = {}
    out.update(experience(hay))

    neg = NEGOTIABLE.search(hay)
    out["experience_negotiable"] = bool(neg)
    out["experience_negotiable_quote"] = (
        _sentence_around(hay, neg.start(), neg.end()) if neg else None)

    sen, sq = _first(SENIORITY, hay)
    out["seniority"] = sen
    out["seniority_quote"] = sq

    emp, eq = _first(EMPLOYMENT, hay)
    out["employment_type"] = emp or "unknown"
    out["employment_quote"] = eq

    m_yes = INDIA_YES.search(hay)
    m_no = INDIA_NO.search(hay)
    if m_yes:
        out["india_status"] = "explicit_yes"
        out["india_quote"] = _sentence_around(hay, m_yes.start(), m_yes.end())
    elif m_no:
        out["india_status"] = "explicit_no"
        out["india_quote"] = _sentence_around(hay, m_no.start(), m_no.end())
    else:
        out["india_status"] = "unstated"
        out["india_quote"] = None

    c = CANARY.search(hay)
    out["canary"] = c.group(1) if c else None

    # Layer 1 does not attempt these. Nulls, not guesses - see the design doc on
    # absence being a value rather than a default.
    out["locations"] = []
    out["required"] = []
    out["preferred"] = []
    out["_layer"] = 1
    return out


CASE_DIRS = {
    "cresta-ai-growth-engineer": "cresta-ai-growth-engineer",
    "streamhub-fullstack-automation": "streamhub-fullstack-automation-ai-eval",
    "shuru-voice-ai-engineer": "shuru-voice-ai-engineer",
    "marrina-ai-engineer-saas": "marrina-ai-engineer-saas",
    "marcopolo-ai-product-engineer": "marcopolo-ai-product-engineer",
    "eventfirst-senior-fullstack": "eventfirst-senior-fullstack-ts-ai",
    "synthio-labs-swe-ai": "synthio-labs-software-engineer-ai",
}


def _load_case_text(name):
    folder = CASE_DIRS.get(name)
    if not folder:
        return None
    p = WORKSPACE / "applications" / folder / "jd.txt"
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else None


def cmd_eval():
    exp_dir = WORKSPACE / "tests" / "jd_extraction" / "expected"
    cases = sorted(exp_dir.glob("*.json"))
    if not cases:
        print("no expected/ cases found")
        return 1

    fields = ["experience_floor_years", "seniority", "employment_type",
              "india_status", "experience_negotiable"]
    score = {f: [0, 0] for f in fields}
    missing = []

    print("layer 1 (regex) against " + str(len(cases)) + " hand-labelled cases\n")
    for c in cases:
        want = json.loads(c.read_text(encoding="utf-8"))
        text = _load_case_text(c.stem)
        if not text:
            missing.append(c.stem)
            continue
        got = extract(text)
        row = []
        for f in fields:
            w, g = want.get(f), got.get(f)
            score[f][1] += 1
            if w == g:
                score[f][0] += 1
                row.append(f.split("_")[0][:6] + "=ok")
            else:
                row.append(f.split("_")[0][:6] + "=" + repr(g) + "!=" + repr(w))
        print("  " + c.stem[:34].ljust(35) + "  ".join(row))

    print()
    for f in fields:
        ok, n = score[f]
        if n:
            print("  " + f.ljust(26) + str(ok) + "/" + str(n)
                  + "  (" + str(100 * ok // n) + "%)")
    if missing:
        print("\n  no source text for " + str(len(missing)) + " case(s)")
        print("  only roles with a saved jd.txt can be scored")
    return 0


def cmd_scan():
    import glob
    files = sorted(glob.glob(str(WORKSPACE / "data/raw/*/remoterocketship.json")),
                   reverse=True)
    if not files:
        print("no snapshot found")
        return 1
    rows = json.loads(Path(files[0]).read_text(encoding="utf-8"))
    rows = rows if isinstance(rows, list) else rows.get("jobs", [])
    from collections import Counter
    floors, sen, emp, neg = [], Counter(), Counter(), 0
    for r in rows:
        got = extract(r.get("description") or "", r.get("title") or "")
        if got["experience_floor_years"] is not None:
            floors.append(got["experience_floor_years"])
        sen[got["seniority"]] += 1
        emp[got["employment_type"]] += 1
        neg += bool(got["experience_negotiable"])
    n = len(rows)
    print(str(n) + " rows from " + Path(files[0]).parent.name + "\n")
    print("  experience floor found : " + str(len(floors))
          + "  (" + str(100 * len(floors) // n) + "%)")
    print("  distribution           : " + str(dict(sorted(Counter(floors).items()))))
    print("  floor above 5 years    : " + str(sum(1 for f in floors if f > 5)))
    print("  seniority              : " + str(dict(sen.most_common())))
    print("  employment             : " + str(dict(emp.most_common())))
    print("  negotiable qualifier   : " + str(neg))
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if "--scan" in sys.argv:
        raise SystemExit(cmd_scan())
    raise SystemExit(cmd_eval())
