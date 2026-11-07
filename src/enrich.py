#!/usr/bin/env python3
"""
enrich.py — "Can this company actually employ me in India?"

The constraint that kills most applications is never in the job description.
A role can be a perfect fit, remote, timezone-aligned — and the company still
has no way to put an Indian resident on payroll.

Three layers, cheapest first. Every job gets layers 1-2 (free); layer 3 costs
a few cents and only runs on jobs that already survived the other filters.

  1. EOR / global-hiring language in the JD            free
  2. Company seen hiring in India on any board we pull free
  3. EPFO — India's statutory provident-fund register  ~5c/company

Layer 3 is the authoritative one: EPFO registration means the company legally
employs people in India and files monthly PF returns naming a headcount.

IMPORTANT ASYMMETRY — an EPFO hit is strong evidence they CAN hire you.
An EPFO miss proves nothing: companies that hire Indians through an EOR
(Deel, Remote.com, Oyster) leave no EPFO trace of their own. Never use a miss
to disqualify a job; it only withholds a boost.

Provider is wrapped behind one function so a vendor change is a config change.
Needs MINDCASE_API_KEY; without it layers 1-2 still run.
"""
import json, os, re, time, urllib.request, urllib.error
from pathlib import Path

WORKSPACE = Path(__file__).parent.parent
CACHE_FILE = WORKSPACE / "india_hiring_cache.json"

MINDCASE_BASE = "https://api.mindcase.co/v1/data"
EPFO_SEARCH = f"{MINDCASE_BASE}/pf-info/listing/run?wait=true"

# Rows requested per company name. Billing appears to be per ROW returned,
# not per name searched, so this is the single most important cost knob in
# the project. Keep it small: the exact-name match we want is near the top,
# and the rest of the register's lookalikes are pure spend.
EPFO_MAX_ROWS = 5
EPFO_COST_PER_ROW_USD = 0.05
EPFO_DETAIL = f"{MINDCASE_BASE}/pf-info/establishment/run?wait=true"

# Employer-of-record and global-hiring language. Presence of any of these in a
# JD means they have thought about hiring outside their own jurisdiction.
EOR_SIGNALS = [
    "employer of record", "eor", "deel", "remote.com", "oyster hr", "oysterhr",
    "velocity global", "globalization partners", "g-p", "rippling",
    "multiplier", "papaya global", "safeguard global", "remofirst",
    "hire anywhere", "hire globally", "globally distributed",
    "work from anywhere", "any country", "anywhere in the world",
    "fully distributed", "contractor agreement", "independent contractor",
]

# Developer marketplaces. You contract through them rather than being
# employed by the end client, which means the "does this company have an
# Indian entity" question does not arise at all — they pay contractors
# wherever they live. For someone hiring from India that is not noise to be
# filtered out, it is the constraint being solved structurally.
MARKETPLACES = [
    "lemon.io", "lemon io", "proxify", "toptal", "turing", "turing.com",
    "andela", "x-team", "xteam", "gun.io", "arc.dev", "gigster", "hivex",
    "silver.dev", "silverdev", "crossover", "remotemore", "deel talent",
    "upstack", "scalable path", "soshace", "braintrust", "a.team",
]

# Phrasing in the posting itself that means contract rather than payroll.
CONTRACT_SIGNALS = [
    "contract role", "contract position", "freelance", "freelancer",
    "independent contractor", "b2b contract", "contract basis",
    "hourly rate", "day rate", "c2c", "corp-to-corp",
]

INDIA_TOKENS = [
    "india", "bengaluru", "bangalore", "mumbai", "delhi", "hyderabad",
    "pune", "chennai", "gurgaon", "gurugram", "noida", "kolkata",
    "ahmedabad", "jaipur", "indore", "kochi", "trivandrum", "coimbatore",
]


# ── cache ────────────────────────────────────────────────────────────────────

def _load_cache():
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_cache(cache):
    try:
        CACHE_FILE.write_text(json.dumps(cache, indent=2, ensure_ascii=False),
                              encoding="utf-8")
    except Exception as e:
        print(f"  enrich: could not write cache: {e}")


def _norm(name):
    """Normalise a company name for cache keys and register lookups."""
    n = (name or "").lower().strip()
    n = re.sub(r'\b(inc|llc|ltd|limited|pvt|private|corp|corporation|gmbh|'
               r'bv|ab|oy|sas|plc|co|company|technologies|technology|labs|'
               r'software|solutions)\b', ' ', n)
    n = re.sub(r'[^a-z0-9 ]', ' ', n)
    return re.sub(r'\s+', ' ', n).strip()


# ── layer 1: JD language ─────────────────────────────────────────────────────

def engagement(job):
    """
    How you would be engaged: marketplace, contract, or employment.

    Returns (kind, why) where kind is "marketplace", "contract" or "".
    Marketplace is decided on the company name; contract on the posting text.
    """
    company = (job.get("company") or "").lower()
    for m in MARKETPLACES:
        if m in company:
            return "marketplace", f"{job.get('company')} contracts you out — no local entity needed"
    blob = " ".join(str(job.get(k, "")) for k in
                    ("description", "tags", "title")).lower()
    for s in CONTRACT_SIGNALS:
        if s in blob:
            return "contract", f"posting says {s}"
    return "", ""


def eor_signal(job):
    """Does the posting itself say they hire across borders?"""
    blob = " ".join(str(job.get(k, "")) for k in
                    ("description", "location", "tags", "title")).lower()
    hits = [s for s in EOR_SIGNALS if s in blob]
    return hits


# ── layer 2: company seen hiring in India in this run ────────────────────────

def build_india_board_index(all_jobs):
    """
    Companies with an India-located posting anywhere in today's pull.

    Free, and surprisingly strong: a Bengaluru listing means they already have
    a way to pay someone in India.
    """
    index = {}
    for j in all_jobs:
        loc = str(j.get("location", "")).lower()
        if any(tok in loc for tok in INDIA_TOKENS):
            key = _norm(j.get("company", ""))
            if key:
                index.setdefault(key, []).append(j.get("location", ""))
    return index


# ── layer 3: EPFO via Mindcase ───────────────────────────────────────────────

def _mindcase_post(url, payload, key, timeout=90):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json",
                 "User-Agent": "remote-job-hunter"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


# Normalised company names this short or this generic cannot identify a firm
# in a register of millions. Matching on them produces confident nonsense.
_TOO_GENERIC = {
    "e source", "source", "one", "air", "ai", "data", "cloud", "tech",
    "global", "digital", "systems", "group", "partners", "ventures",
    "g p", "io", "labs", "hr", "apps", "core", "next", "first", "prime",
}


def _name_matches(target, est_norm):
    """
    Does this register row really belong to the company we asked about?

    Substring matching is not good enough here and the failure mode is
    expensive: "e source" occurs inside "on(e source) concierge services",
    and "g p" inside "ginnin(g p)alvarising mill". Both produced confident
    false positives that pushed the wrong jobs to the top of the shortlist.

    Rules:
      * the company name must appear as whole TOKENS, not as characters
      * those tokens must start the establishment name — Indian subsidiaries
        are named "<Parent> India Private Limited", not "<Something> <Parent>"
      * names too short or too generic to identify anyone are refused outright
    """
    if not target or not est_norm:
        return False
    if target in _TOO_GENERIC or len(target.replace(" ", "")) < 4:
        return False

    t_tokens = target.split()
    e_tokens = est_norm.split()
    if not t_tokens or len(e_tokens) < len(t_tokens):
        return False

    # Indian filings often prefix "m/s" (messrs); ignore it when anchoring.
    if e_tokens and e_tokens[0] in ("m", "ms", "ms."):
        e_tokens = e_tokens[1:]

    return e_tokens[:len(t_tokens)] == t_tokens


def _field(row, *names):
    """
    Read a field without betting on its exact spelling.

    The published samples render as tables with headers like
    "Establishment Name"; the live JSON may use establishment_name,
    establishmentName or similar. Matching on normalised keys means a
    naming difference degrades to "not found" instead of silently
    discarding every row.
    """
    if not isinstance(row, dict):
        return None
    norm = {re.sub(r'[^a-z0-9]', '', str(k).lower()): v for k, v in row.items()}
    for n in names:
        key = re.sub(r'[^a-z0-9]', '', n.lower())
        if key in norm and norm[key] not in (None, ""):
            return norm[key]
    # Fall back to any key that contains the requested token.
    for n in names:
        key = re.sub(r'[^a-z0-9]', '', n.lower())
        for k, v in norm.items():
            if key in k and v not in (None, ""):
                return v
    return None


def _rows(resp):
    """Mindcase returns rows under one of a few keys depending on the agent."""
    if isinstance(resp, list):
        return resp
    for k in ("results", "data", "rows", "items"):
        v = resp.get(k)
        if isinstance(v, list):
            return v
    return []


def epfo_lookup(company_name, key):
    """
    Look a company up in the EPFO register.

    Returns {found, establishments[], employee_count, note} — employee_count
    from the most recent PF remittance when the detail call exposes it.
    """
    out = {"found": False, "establishments": [], "employee_count": None,
           "note": ""}
    name = (company_name or "").strip()
    if not name:
        return out

    # Refuse to spend on a name that cannot identify anyone. "E Source" and
    # "G-P" match thousands of register rows by accident, so the call is pure
    # cost with a false-positive risk attached.
    target_pre = _norm(name)
    if target_pre in _TOO_GENERIC or len(target_pre.replace(" ", "")) < 4:
        out["note"] = f"name too generic to search ({name!r}) — skipped, no charge"
        out["rows_billed"] = 0
        return out

    # COST CONTROL — READ BEFORE CHANGING.
    # The listing agent is priced "5c per establishment" with "no row cap set"
    # and Max Results defaulting to ALL. A name like "Infosys" matches ~100
    # rows in the register, so an uncapped search plausibly bills 100 x 5c =
    # $5.00 for ONE company. We only ever use the rows whose name actually
    # matches, so paying for the long tail of lookalikes buys nothing.
    # Capping hard keeps a run's worst case bounded and predictable.
    try:
        resp = _mindcase_post(
            EPFO_SEARCH,
            {"params": {"names": [name], "maxResults": EPFO_MAX_ROWS}}, key)
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode()[:200]
        except Exception:
            pass
        if e.code == 402:
            out["note"] = "Mindcase balance is $0 — top up at console.mindcase.co"
            out["billing"] = True
        elif e.code in (401, 403):
            out["note"] = f"Mindcase auth failed (HTTP {e.code}) — check MINDCASE_API_KEY"
            out["billing"] = True
        else:
            out["note"] = f"EPFO search HTTP {e.code}: {body}"
        return out
    except Exception as e:
        out["note"] = f"EPFO search error: {e}"
        return out

    rows = _rows(resp)
    # Billing tracks rows returned; the API tells us how many, so record it
    # rather than assuming the cap was hit.
    out["rows_billed"] = (resp.get("returned") if isinstance(resp, dict)
                          else None) or len(rows)
    if isinstance(resp, dict) and resp.get("truncated"):
        out["truncated"] = True
    if not rows:
        out["note"] = "no EPFO establishment matched"
        return out

    target = _norm(name)
    matches = []
    for r in rows:
        est_name = str(_field(r, "establishment name", "name") or "")
        if not est_name:
            continue
        if _name_matches(target, _norm(est_name)):
            matches.append({
                "id": _field(r, "establishment id", "id"),
                "name": est_name,
                "address": _field(r, "address") or "",
            })

    if not matches:
        out["note"] = f"{len(rows)} register hits, none matched {name!r}"
        return out

    out["found"] = True
    out["establishments"] = matches[:5]
    out["note"] = f"{len(matches)} EPFO establishment(s)"
    return out


def company_india_presence(company_name, job, board_index, cfg, key=None,
                           cache=None, budget=None):
    """
    Single entry point. Returns a verdict dict:

        {confidence: confirmed|likely|unknown,
         boost: float, evidence: [str], epfo: {...} | None}

    `budget` is a one-element list used as a mutable counter so the caller can
    cap paid lookups per run.
    """
    cache = cache if cache is not None else {}
    key_norm = _norm(company_name)
    evidence, epfo = [], None

    # layer 0 — engagement type. A marketplace or contract role sidesteps
    # the entity question entirely, so it needs no paid lookup at all.
    kind, why = engagement(job)
    if kind:
        evidence.append(why)

    # layer 1
    hits = eor_signal(job)
    if hits:
        evidence.append(f"JD mentions {', '.join(hits[:2])}")

    # layer 2
    if key_norm and key_norm in board_index:
        locs = board_index[key_norm][:2]
        evidence.append(f"hiring in India: {'; '.join(locs)}")

    # layer 3 (cached, budgeted, opt-in)
    if cfg.get("use_epfo") and key and key_norm:
        if key_norm in cache:
            epfo = cache[key_norm]
        elif budget is None or budget[0] > 0:
            epfo = epfo_lookup(company_name, key)
            if epfo.get("billing"):
                # Billing/auth is a run-wide condition, not a per-company one.
                # Keep it out of the cache and stop trying.
                if budget is not None:
                    budget[0] = 0
                print(f"  enrich: EPFO disabled — {epfo['note']}")
                return _verdict(None, hits, key_norm, board_index, cfg, evidence)
            cache[key_norm] = epfo
            if budget is not None:
                budget[0] -= 1
            time.sleep(0.4)          # be polite to the API
        if epfo and epfo.get("found"):
            ests = epfo.get("establishments") or []
            evidence.append(
                f"EPFO registered: {ests[0]['name'][:40]}" if ests
                else "EPFO registered")

    return _verdict(epfo, hits, key_norm, board_index, cfg, evidence, kind)


def _verdict(epfo, eor_hits, key_norm, board_index, cfg, evidence, kind=""):
    """Grade the collected evidence. Shared so the billing bail-out below
    still returns a well-formed verdict from the free layers alone."""
    # Contracting resolves the hiring question rather than leaving it open,
    # so these never sit at "unknown" waiting for an EPFO hit that will never
    # come. The boost stays NEUTRAL on purpose: being hireable is a gate, not
    # a mark of quality. Boosting it floats a marketplace's whole catalogue —
    # a PHP role outranked every direct-employer AI role when this was 1.25.
    # The label tells you the route is open; fit, overlap and salary decide
    # the order.
    if kind in ("marketplace", "contract"):
        return {"confidence": "contractor", "boost": 1.0,
                "evidence": evidence, "epfo": epfo, "engagement": kind}
    if (epfo and epfo.get("found")) or (key_norm and key_norm in board_index):
        conf, boost = "confirmed", float(cfg.get("boost_confirmed", 1.25))
    elif eor_hits:
        conf, boost = "likely", float(cfg.get("boost_eor_signal", 1.1))
    else:
        # No evidence either way. Not a penalty — most companies simply do not
        # advertise this, and an EOR arrangement leaves no public trace.
        conf, boost = "unknown", 1.0

    return {"confidence": conf, "boost": boost, "evidence": evidence,
            "epfo": epfo, "engagement": kind}


def enrich_free(jobs, profile_config, all_jobs=None):
    """
    Layers 1-2 only — costs nothing, so it runs on every job before ranking.
    Annotates each job in-place with `india`.
    """
    cfg = (profile_config or {}).get("india_hiring", {})
    if not cfg.get("enabled", True):
        for j in jobs:
            j["india"] = {"confidence": "unknown", "boost": 1.0,
                          "evidence": [], "epfo": None}
        return jobs

    board_index = build_india_board_index(all_jobs if all_jobs is not None else jobs)
    if board_index:
        print(f"  enrich: {len(board_index)} companies seen hiring in India today")

    for j in jobs:
        # key=None keeps this strictly free — no paid lookup can fire here.
        j["india"] = company_india_presence(
            j.get("company", ""), j, board_index, cfg, key=None)

    confirmed = sum(1 for j in jobs if j["india"]["confidence"] == "confirmed")
    likely = sum(1 for j in jobs if j["india"]["confidence"] == "likely")
    print(f"  enrich: {confirmed} confirmed / {likely} likely from free signals "
          f"({len(jobs)} jobs)")
    return jobs


def _needs_epfo(job):
    """
    Is a paid lookup worth it for this job?

    Skip when we already know (free layers said confirmed), and skip jobs
    located in India — "an Indian company can employ in India" is not a
    finding. What EPFO buys is foreign employers with a quiet Indian entity.
    """
    india = job.get("india") or {}
    if india.get("confidence") in ("confirmed", "contractor"):
        return False
    if (job.get("tz") or {}).get("region") == "india":
        return False
    return bool((job.get("company") or "").strip())


def enrich_epfo(ranked_jobs, profile_config):
    """
    Layer 3 — paid, and therefore targeted.

    Runs AFTER ranking so the budget is spent on the roles you would actually
    apply to, not on whichever jobs happened to be first in the fetch order.
    One lookup per company, best-ranked job first. Mutates `india` in place;
    caller should re-score afterwards since the boost changes.
    """
    cfg = (profile_config or {}).get("india_hiring", {})
    if not (cfg.get("enabled", True) and cfg.get("use_epfo")):
        return 0

    key = os.getenv("MINDCASE_API_KEY", "").strip()
    if not key:
        print("  enrich: MINDCASE_API_KEY not set — EPFO skipped")
        return 0

    cache = _load_cache()
    budget = [int(cfg.get("epfo_max_lookups_per_run", 10))]
    before = len(cache)

    # One company, one lookup — several jobs may share an employer.
    seen_companies, targets = set(), []
    for j in ranked_jobs:
        if not _needs_epfo(j):
            continue
        cname = _norm(j.get("company", ""))
        if not cname or cname in seen_companies:
            continue
        seen_companies.add(cname)
        targets.append(j)
        if len(targets) >= budget[0]:
            break

    if not targets:
        print("  enrich: no job needs a paid EPFO lookup")
        return 0

    print(f"  enrich: EPFO on top {len(targets)} companies "
          f"(skipping already-confirmed and India-located)")

    board_index = {}
    upgraded = 0
    for j in targets:
        verdict = company_india_presence(
            j.get("company", ""), j, board_index, cfg,
            key=key, cache=cache, budget=budget)
        if verdict["confidence"] != "unknown":
            upgraded += 1
        # Carry the paid result to every job from the same employer.
        cname = _norm(j.get("company", ""))
        for other in ranked_jobs:
            if _norm(other.get("company", "")) == cname:
                prev_ev = (other.get("india") or {}).get("evidence", [])
                merged = list(dict.fromkeys(prev_ev + verdict["evidence"]))
                other["india"] = {**verdict, "evidence": merged}

    if len(cache) != before:
        _save_cache(cache)

    used = int(cfg.get("epfo_max_lookups_per_run", 10)) - budget[0]
    billed = sum((c.get("rows_billed") or 0) for c in cache.values()
                 if isinstance(c, dict))
    print(f"  enrich: {used} EPFO lookup(s), {upgraded} upgraded | "
          f"{billed} rows billed lifetime ~${billed * EPFO_COST_PER_ROW_USD:.2f} "
          f"(cached — never re-charged)")
    return upgraded


# Backwards-compatible alias for the single-pass behaviour.
def enrich_jobs(jobs, profile_config, all_jobs=None):
    return enrich_free(jobs, profile_config, all_jobs)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    k = os.getenv("MINDCASE_API_KEY", "").strip()
    if not k:
        print("Set MINDCASE_API_KEY to test EPFO lookups.")
        print("Free layers still work — testing EOR detection:\n")
        for d in ["We hire anywhere in the world via Deel.",
                  "Must be located in the United States."]:
            print(f"  {d!r}\n    -> {eor_signal({'description': d})}")
    else:
        for name in sys.argv[1:] or ["Infosys", "Zomato"]:
            r = epfo_lookup(name, k)
            print(f"{name}: found={r['found']} — {r['note']}")
            for e in r["establishments"][:3]:
                print(f"    {e['id']}  {e['name']}")
