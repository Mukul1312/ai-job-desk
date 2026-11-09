#!/usr/bin/env python3
"""
jdfacts.py — one fact about a posting, from the best source that has it.

This is the design decision, in code, so it stops living only in prose.

The question was never "regex or LLM". It is **where does the fact live**, and
four different places answer it. Each row below was measured, not argued:

    fact              lives in                 tool        measured
    ----------------- ------------------------ ----------- -----------------
    employment_type   a structured feed field   read it     feed 89%, regex 56%
    experience_floor  a number beside a cue     regex       97%, 20/20 held-out
    negotiable        a claim, phrased freely   model       13 found, regex 0
    india_status      a location statement      model       regex missed 148
    no-India employer not in the posting at all board       Sword Health, 0/72

Three things follow from that table.

**Never pay a model for a field the feed already states.** `employment_type`
comes back on 100% of RemoteRocketship rows and is right 89% of the time;
regexing it out of prose scored 56%, and asking a model got the same answer the
regex did, because the fact is in ATS metadata and not in the body at all.

**Never write a regex for a claim that can be phrased freely.** The thirteen
postings saying years are negotiable share almost no tokens — "we do not
require any formal experience", "we care about what you've built", "not years
served", "(Adjustable; …)". `NEGOTIABLE` matches the two it was copied from.
Regexes are cheap to run and expensive to maintain; a model is the reverse, and
maintenance cost scales with phrasing diversity, which here is unbounded.

**Keep running both anyway.** Disagreement between them is the only error
detector in the system. It is how the regex was caught reading "Payward has
spent the last 15 years building…" as a 15-year requirement, and how 148 India
exclusions called `unstated` were found. One tool's output has nothing to be
wrong against. Nine of fourteen floor disagreements were floors too HIGH, which
silently deletes reachable roles — the expensive direction, and invisible
without a second opinion.

### Verification, and why a drop needs it

A posting read in full can be acted on. A RemoteRocketship bullet summary
cannot: it is a rewrite that deleted the sentences which decide reachability,
so the absence of "based in the UK" in a summary is not evidence of anything.

`verification()` says which of those we have, and `decide()` refuses to drop
anything below `posting`. 160 of 1,124 jobs have no fetchable page, and they
must be marked unverified rather than quietly treated as equal.

The model extracts; this module decides. Nothing here returns a score.
"""

import re

# Which sources may answer each field, best first, and why. Changing this table
# is changing the design, so the measurement that justifies each choice is
# beside it.
FIELD_POLICY = {
    "employment_type":        ("feed", "feed 89% vs regex 56%; ATS metadata, not body text"),
    "experience_floor_years": ("regex", "97% agreement after NOT_A_REQUIREMENT; 20/20 held-out"),
    "experience_negotiable":  ("model", "13 real positives, 13 phrasings, regex found 0"),
    "india_status":           ("model", "regex called 148 location-excluded postings 'unstated'"),
    "seniority":              ("regex", "77-84%; reported, never a drop input"),
}

# Trust levels, weakest first. A drop needs at least "posting".
VERIFICATION = ["none", "summary", "posting", "board"]


def verification(job, jd=None, board=None):
    """How much of this posting we actually read.

    board    the employer's whole board was checked - company-level, and the
             only source that can speak to a fact the posting omits
    posting  the employer's own page was fetched and read in full
    summary  only a rewritten feed description. RemoteRocketship rewrites
             100% of its 827 rows into bullets averaging 1,987 characters;
             the sentence that makes eventfirst reachable is not in it
    none     no usable text at all
    """
    if board:
        return "board"
    if jd:
        return "posting"
    if (job.get("description") or "").strip():
        return "summary"
    return "none"


def resolve(job, jd=None, board=None, regex_out=None):
    """{field: {"value", "source", "quote"}} — each fact from its best source.

    Absent sources are skipped, not guessed at. A field nobody can answer is
    absent from the result, which is not the same as being false.
    """
    facts = {}

    def put(field, value, source, quote=None):
        if value is None or value == "":
            return
        facts[field] = {"value": value, "source": source, "quote": quote}

    # Tier A — already structured. Read it; do not parse prose for it.
    put("employment_type", job.get("employment_type"), "feed")

    # Tier B — the regex, on whatever text exists.
    if regex_out:
        put("experience_floor_years", regex_out.get("experience_floor_years"),
            "regex", regex_out.get("experience_quote"))
        put("seniority", regex_out.get("seniority"), "regex",
            regex_out.get("seniority_quote"))

    # Tier C — only a model reads these, and only from a real posting.
    if jd:
        put("india_status", jd.get("india_status"), "model", jd.get("india_quote"))
        if jd.get("experience_negotiable"):
            put("experience_negotiable", True, "model",
                jd.get("experience_negotiable_quote"))
        # Seniority: the model won all four held-out disagreements, and the
        # regex's failures were all the same shape - reading a seniority word
        # about somebody else ("an internship or role where you wrote code",
        # "a growth ladder from Associate through Principal", "reporting to the
        # Head of"). So the model overrides it where both have an answer.
        if jd.get("seniority"):
            put("seniority", jd["seniority"], "model", jd.get("seniority_quote"))

        # The model also reads the floor. Prefer it where the regex is silent,
        # and record a disagreement rather than silently picking a winner.
        mf = jd.get("experience_floor_years")
        cur = facts.get("experience_floor_years")
        if mf is not None and cur is None:
            put("experience_floor_years", mf, "model", jd.get("experience_quote"))
        elif mf is not None and cur and cur["value"] != mf:
            facts["experience_floor_years"]["disagrees"] = {"model": mf}

    # Tier D — not in the posting at all.
    if board and board.get("verdict") == "no_india":
        put("employer_hires_india", False, "board",
            "%s: %d open postings on their own board, %d engineering, none "
            "India-scoped" % (board.get("company", "?"), board.get("open", 0),
                              board.get("eng", 0)))
    return facts


def decide(facts, profile_config, level="posting"):
    """Should this posting be dropped, and on whose word. (reason, quote, source).

    Every threshold is set loose on purpose. A wrongly dropped job disappears
    before anyone sees it; a wrongly kept one costs seconds of reading. So the
    years rule fires only well above the profile, and `experience_negotiable`
    overrides it outright — those are the most reachable roles in the set and a
    floor rule would delete them.

    Returns (None, None, None) when nothing disqualifies it. Silence is never
    a reason: absent fields, "unstated", and unverified postings all mean keep.
    """
    if VERIFICATION.index(level) < VERIFICATION.index("posting"):
        return None, None, None        # not read well enough to act on

    def val(f):
        return (facts.get(f) or {}).get("value")

    def quote(f):
        return (facts.get(f) or {}).get("quote")

    if val("employer_hires_india") is False:
        return ("employer has no India roles", quote("employer_hires_india"), "board")

    if val("india_status") == "explicit_no":
        return "india_excluded", quote("india_status"), "model"

    years = float(profile_config.get("years_experience") or 0)
    tol = float(profile_config.get("experience_tolerance_years", 4))
    floor = val("experience_floor_years")
    if (floor is not None and years and floor > years + tol
            and not val("experience_negotiable")):
        src = (facts["experience_floor_years"] or {}).get("source", "?")
        return f"wants {floor}+ years", quote("experience_floor_years"), src

    if val("seniority") in {"intern", "manager"}:
        # Reported from the regex, which scores 77-84% and has been caught
        # reading "an internship or role where you wrote code" as an intern
        # posting. Only acted on when a model agrees.
        if (facts.get("seniority") or {}).get("source") == "model":
            return f"role is {val('seniority')}", quote("seniority"), "model"

    return None, None, None


def company_key(name):
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())
