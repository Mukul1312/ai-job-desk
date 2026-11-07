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


def recommend_resume(job, variants):
    """Which of Mukul's resume variants to send. Falls back to 'ai'."""
    blob = (f"{job.get('title','')} {job.get('tags','')} "
            f"{job.get('description','')[:1200]}").lower()
    best, best_hits = None, 0
    for variant, signals in RESUME_RULES:
        if variant not in variants:
            continue
        hits = sum(1 for s in signals if s in blob)
        # Title matches count double — the title is what the role actually is.
        title = job.get("title", "").lower()
        hits += sum(1 for s in signals if s in title)
        if hits > best_hits:
            best, best_hits = variant, hits
    return best or ("ai" if "ai" in variants else next(iter(variants), None))


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
            tz_u = tzmod.assess(job.get("location", ""), tz_cfg)
            if tz_u["passes"]:
                job["tz"] = tz_u
                job["resume"] = recommend_resume(job, variants)
                unscored.append(job)
            continue

        tz = tzmod.assess(job.get("location", ""), tz_cfg)
        job["tz"] = tz
        if not tz["passes"]:
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
        w = float(weights.get(region, weights.get("unknown", 0.8)))
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

        job["final_score"] = round(fit * w * boost * overlap_bonus, 1)
        job["rank_reasons"] = [
            f"fit {fit}",
            f"{region} x{w}",
            f"india:{india.get('confidence')} x{boost}",
            f"overlap {'n/a' if oh is None else str(oh)+'h'} x{round(overlap_bonus,2)}",
        ]
        job["resume"] = recommend_resume(job, variants)

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
        job["final_score"] = round(fit * w * boost * overlap_bonus, 1)
        job["rank_reasons"] = [
            f"fit {fit}",
            f"{region} x{w}",
            f"india:{india.get('confidence','unknown')} x{boost}",
            f"overlap {'n/a' if oh is None else str(oh)+'h'} x{round(overlap_bonus,2)}",
        ]

    ranked_jobs.sort(key=lambda j: j.get("final_score", 0), reverse=True)
    return ranked_jobs
