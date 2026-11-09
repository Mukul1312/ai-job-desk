#!/usr/bin/env python3
"""
rank.py — Turn a pile of scored jobs into a shortlist worth your evening.

scorer.py answers "do my skills match this JD". That is necessary and not
sufficient: a perfect skills match in San Francisco at 0.5h overlap is not a
job you can take. This applies the constraints that actually decide it.

Hard filters (job is dropped):
    timezone overlap below profile.timezone.min_overlap_hours
    salary below profile.salary.min_lpa, when a salary is stated
    excluded title keywords

Ranking (survivors are reordered):
    final = fit  x  region_weight  x  india_boost  x  overlap_bonus

Every drop is counted and reported, so a filter that is quietly eating
everything is visible rather than mysterious.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import timezone as tzmod
import salary as salmod

# Resume variant -> signals that should pick it. Checked in order, so the
# more specialised variants get first refusal on a job.
RESUME_RULES = [
    ("ml", ["machine learning engineer", "ml engineer", "mlops", "deep learning",
            "pytorch", "tensorflow", "model training", "fine-tun", "research engineer",
            "applied scientist", "speech", "asr", "tts", "computer vision", "nlp"]),
    ("ai", ["ai engineer", "ai agent", "agentic", "llm", "genai", "generative ai",
            "rag", "prompt", "openai", "anthropic", "langchain", "ai product",
            "applied ai", "conversational ai", "evaluation"]),
    ("fde", ["forward deployed", "forward-deployed", "solutions engineer",
             "solutions architect", "customer engineer", "implementation",
             "deployment engineer", "professional services"]),
    ("frontend", ["frontend", "front-end", "react developer", "next.js",
                  "ui engineer", "design system", "remix"]),
    ("swe", ["software engineer", "backend", "full stack", "full-stack",
             "platform engineer", "node.js", "typescript", "api", "distributed"]),
]


def role_tier(job, cfg):
    """
    Which kind of role this is, and what that is worth.

    The search deliberately admits software, full-stack and frontend roles
    alongside AI ones — running a second pass for them would spend the
    RemoteRocketship daily quota twice. Tiering keeps one run while still
    putting the roles you actually want at the top.

    Matched on the TITLE only. A description will mention React or an LLM for
    all sorts of reasons; the title is what the job is.
    """
    tiers = (cfg or {}).get("tiers") or []
    title = (job.get("title") or "").lower()
    for t in tiers:
        for pat in t.get("match", []):
            if _pat_hit(title, pat):
                return t.get("name", "?"), float(t.get("weight", 1.0))
    return "other", float((cfg or {}).get("default_weight", 1.0))


def _pat_hit(title, pat):
    """
    Substring for phrases; whole-word for bare tokens.

    A plain substring test made "AI Platform Engineer" fall past the ai tier
    (it contains neither "ai engineer" nor " ai ") and land on swe via
    "platform engineer" — down-weighting the exact roles being targeted.
    A bare token like "ai" or "ml" therefore matches on word boundaries, so
    "AI Platform Engineer" hits while "Maintenance" does not.
    """
    pat = pat.strip().lower()
    if not pat:
        return False
    if " " in pat or len(pat) > 4:
        return pat in title
    return re.search(r"\b" + re.escape(pat) + r"\b", title) is not None


# Which resumes a tier may send, best first. The tier is decided from the title
# by role_tier(); this says what that verdict permits.
#
# An `ai` role may legitimately want the `ml` resume - "Applied ML Engineer" is
# tiered ai and ml is the right send. It may never want `swe`: that is the
# failure this table exists to stop.
TIER_RESUMES = {
    "ai":       ["ai", "ml"],
    "ml":       ["ml", "ai"],
    "fde":      ["fde", "ai", "swe"],
    "frontend": ["frontend", "swe"],
    "swe":      ["swe", "ai", "frontend"],
}


def recommend_resume(job, variants, tier=None):
    """Which of Mukul's resume variants to send.

    The tier comes first. `role_tier()` already decides what kind of role this
    is, from the title alone and with word-boundary care, and the resume choice
    used to ignore it completely - scoring title, tags and 1,200 characters of
    description with equal weight.

    Description volume then decided everything. The `swe` signals - "software
    engineer", "backend", "api", "typescript", "distributed" - occur in every
    engineering description written, including AI ones, so swe won on count.
    Measured on the 2026-11-09 run: **190 of 348 ai-tier roles were sent a
    non-ai resume, 113 of them `swe`** - among them "Senior AI Software
    Engineer", "Software Engineer - Applied AI", and eventfirst, whose own
    write-up had already concluded by hand that `ai` was the right send.

    So: the tier picks the family, and only the TITLE chooses within it. A
    description may no longer overrule what the role is called.
    """
    title = (job.get("title") or "").lower()
    allowed = TIER_RESUMES.get(tier or "")
    if allowed:
        pool = [v for v in allowed if v in variants]
        if pool:
            best, best_hits = pool[0], 0      # the tier's own resume by default
            for variant in pool:
                signals = dict(RESUME_RULES).get(variant, [])
                hits = sum(1 for s in signals if s in title)
                if hits > best_hits:
                    best, best_hits = variant, hits
            return best

    # No tier, or a tier with no mapping ("other"): nothing has established what
    # the role is, so the description is the only evidence there is.
    blob = (f"{job.get('title','')} {job.get('tags','')} "
            f"{job.get('description','')[:1200]}").lower()
    best, best_hits = None, 0
    for variant, signals in RESUME_RULES:
        if variant not in variants:
            continue
        hits = sum(1 for s in signals if s in blob)
        hits += sum(1 for s in signals if s in title)   # the title counts double
        if hits > best_hits:
            best, best_hits = variant, hits
    return best or ("ai" if "ai" in variants else next(iter(variants), None))


def location_is_real(job):
    """False when `location` is something we synthesised rather than observed.

    RemoteRocketship supplies no geography, so src/search.py labels each result
    with the region it was queried under. That label looks exactly like a real
    location and used to be scored as one: a US-only role returned under the
    UAE filter got a 9-hour Dubai overlap and ranked first, while its true
    location fails the hard 4-hour filter outright. 68% of a typical run comes
    from that source.

    Where the location is inferred, ranking must not act on it at all - not to
    drop, not to weight, not to bonus. A guess is not evidence, and a guess
    that can silently delete a job is worse than no guess.
    """
    # An explicit flag always wins. search.py sets it False once it has
    # recovered a real location from the slug, and the fallback below must not
    # then veto that - which it did on first attempt, silently keeping 650 rows
    # at region=unknown after their locations had been correctly recovered.
    if "location_inferred" in job:
        return not job["location_inferred"]
    # No flag at all: snapshots in data/raw/ taken before it existed, replayed
    # by --offline. Treat this source as unlocated, because it was.
    return not str(job.get("source", "")).lower().startswith("remoterocketship")


def rank_jobs(jobs, profile_config):
    """
    Returns (ranked_jobs, stats, unscored_relevant).

    Each surviving job gains: tz, salary_lpa, final_score, resume, and the
    reasons behind its ranking, so the report can explain itself.
    """
    tz_cfg = profile_config.get("timezone", {})
    sal_cfg = profile_config.get("salary", {})
    weights = profile_config.get("region_weights", {})
    variants = profile_config.get("resume_variants", {})
    tier_cfg = profile_config.get("role_tiers", {})
    pref_h = float(tz_cfg.get("preferred_overlap_hours", 6.0))

    kept, unscored = [], []
    stats = {"total": len(jobs), "dropped_timezone": 0,
             "dropped_salary": 0, "dropped_unscored": 0,
             "by_region": {}, "by_india": {}}

    for job in jobs:
        fit = job.get("match_score")
        # No usable JD text. The title already passed the domain gate, so the
        # role is probably relevant — we simply cannot grade the fit. Boards
        # that hide descriptions behind auth (gethired) land here, and they
        # carry most of the Indian supply, so dropping them silently would
        # quietly delete the thing we added them for.
        if fit is None:
            stats["dropped_unscored"] += 1
            # An inferred location is assessed as empty, which routes to the
            # unrestricted path rather than to a fabricated country.
            tz_u = tzmod.assess(
                job.get("location", "") if location_is_real(job) else "", tz_cfg)
            if tz_u["passes"]:
                job["tz"] = tz_u
                job["role_tier"] = role_tier(job, tier_cfg)[0]
                job["resume"] = recommend_resume(job, variants,
                                                 job["role_tier"])
                unscored.append(job)
            continue

        real_loc = location_is_real(job)
        tz = tzmod.assess(job.get("location", "") if real_loc else "", tz_cfg)
        job["tz"] = tz
        # Only a location we actually observed may drop a job. Otherwise the
        # filter is deciding on our own placeholder.
        if not tz["passes"] and real_loc:
            stats["dropped_timezone"] += 1
            continue

        # Prefer explicit numeric min/max when the board gave them.
        if job.get("salary_min") or job.get("salary_max"):
            parsed = salmod.from_minmax(job.get("salary_min"), job.get("salary_max"),
                                        job.get("salary_currency") or "INR",
                                        sal_cfg.get("fx_to_inr"))
            ok = (parsed["lpa"] is None and sal_cfg.get("accept_unlisted", True)) \
                or (parsed["high_lpa"] or 0) >= float(sal_cfg.get("min_lpa", 0))
        else:
            ok, parsed = salmod.meets_floor(job.get("salary", ""), sal_cfg)
        job["salary_lpa"] = parsed
        if not ok:
            stats["dropped_salary"] += 1
            continue

        region = tz["region"]
        if real_loc:
            w = float(weights.get(region, weights.get("unknown", 0.8)))
        else:
            # Neutral, deliberately not the 0.8 "unknown" weight. That weight
            # is for a job whose location is genuinely unclear; this is a job
            # whose location WE failed to capture, and penalising it 20% for
            # our own gap would quietly bury two-thirds of the pipeline.
            w = 1.0
            stats["location_inferred"] = stats.get("location_inferred", 0) + 1
        india = job.get("india") or {"boost": 1.0, "confidence": "unknown",
                                     "evidence": []}
        boost = float(india.get("boost", 1.0))
        # The India-capability boost exists to surface FOREIGN employers who
        # can legally hire you. For a role already located in India it says
        # nothing — applying it there is a blanket 25% bonus for Indian jobs,
        # which silently overrides the equal region weighting.
        if region == "india":
            boost = 1.0

        # Reward overlap beyond the preferred threshold, mildly and with a cap,
        # so timezone never swamps an otherwise better-matched job.
        oh = tz.get("overlap_hours")
        overlap_bonus = 1.0 if oh is None else min(1.15, 0.9 + (oh / pref_h) * 0.2)

        tier, tw = role_tier(job, tier_cfg)
        job["role_tier"] = tier
        job["final_score"] = round(fit * w * boost * overlap_bonus * tw, 1)
        job["rank_reasons"] = [
            f"fit {fit}",
            f"{tier} x{tw}",
            f"{region} x{w}" + ("" if real_loc else " (location not supplied by source)"),
            f"india:{india.get('confidence')} x{boost}",
            f"overlap {'n/a' if oh is None else str(oh)+'h'} x{round(overlap_bonus,2)}",
        ]
        job["resume"] = recommend_resume(job, variants, tier)
        stats.setdefault("by_tier", {})
        stats["by_tier"][tier] = stats["by_tier"].get(tier, 0) + 1

        stats["by_region"][region] = stats["by_region"].get(region, 0) + 1
        c = india.get("confidence", "unknown")
        stats["by_india"][c] = stats["by_india"].get(c, 0) + 1
        kept.append(job)

    kept.sort(key=lambda j: j["final_score"], reverse=True)
    # Newest first — with no score to rank on, recency is the best proxy.
    unscored.sort(key=lambda j: j.get("posted", ""), reverse=True)
    stats["kept"] = len(kept)
    stats["unscored_relevant"] = len(unscored)
    return kept, stats, unscored


def format_stats(stats):
    lines = [
        f"  Ranked {stats['kept']} of {stats['total']} jobs",
        f"    dropped: {stats['dropped_unscored']} unscorable JD "
        f"({stats.get('unscored_relevant', 0)} kept as 'worth a look'), "
        f"{stats['dropped_timezone']} timezone, {stats['dropped_salary']} salary",
    ]
    if stats["by_region"]:
        reg = ", ".join(f"{k} {v}" for k, v in
                        sorted(stats["by_region"].items(), key=lambda x: -x[1]))
        lines.append(f"    regions: {reg}")
    if stats.get("by_tier"):
        tiers = ", ".join(f"{k} {v}" for k, v in
                          sorted(stats["by_tier"].items(), key=lambda x: -x[1]))
        lines.append(f"    roles: {tiers}")
    if stats["by_india"]:
        ind = ", ".join(f"{k} {v}" for k, v in
                        sorted(stats["by_india"].items(), key=lambda x: -x[1]))
        lines.append(f"    India-capable: {ind}")
    return "\n".join(lines)


def rescore(ranked_jobs, profile_config):
    """
    Recompute final scores after a late enrichment pass changed the India
    boosts, then re-sort. Cheaper and safer than re-running rank_jobs, which
    would re-apply the hard filters to jobs that already passed them.
    """
    weights = profile_config.get("region_weights", {})
    tz_cfg = profile_config.get("timezone", {})
    tier_cfg = profile_config.get("role_tiers", {})
    pref_h = float(tz_cfg.get("preferred_overlap_hours", 6.0))

    for job in ranked_jobs:
        fit = job.get("match_score")
        if fit is None:
            continue
        tz = job.get("tz") or {}
        region = tz.get("region", "unknown")
        w = float(weights.get(region, weights.get("unknown", 0.8)))
        india = job.get("india") or {}
        boost = 1.0 if region == "india" else float(india.get("boost", 1.0))
        oh = tz.get("overlap_hours")
        overlap_bonus = 1.0 if oh is None else min(1.15, 0.9 + (oh / pref_h) * 0.2)
        tier, tw = role_tier(job, tier_cfg)
        job["role_tier"] = tier
        job["final_score"] = round(fit * w * boost * overlap_bonus * tw, 1)
        job["rank_reasons"] = [
            f"fit {fit}",
            f"{tier} x{tw}",
            f"{region} x{w}",
            f"india:{india.get('confidence','unknown')} x{boost}",
            f"overlap {'n/a' if oh is None else str(oh)+'h'} x{round(overlap_bonus,2)}",
        ]

    ranked_jobs.sort(key=lambda j: j.get("final_score", 0), reverse=True)
    return ranked_jobs
