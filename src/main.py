#!/usr/bin/env python3
"""
main.py — Entry point for remote-job-hunter.

    python src/main.py --profile-config mukul-profile.json \
                       --profile-meta profiles/ai-agent-ml.json \
                       --output daily_report.md

Pipeline:
    fetch  ->  score (skills)  ->  enrich (can they hire in India?)
           ->  rank (timezone / salary / region)  ->  report

Report-only by default. The auto-apply path still exists in apply.py and
confirm.py but stays dormant unless the profile sets auto_apply: true — it is
deliberately opt-in, not opt-out.
"""
import json, argparse, sys, os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# Load .env before anything reads an API key.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    # Minimal fallback so the project works without python-dotenv installed.
    _envf = Path(__file__).parent.parent / ".env"
    if _envf.exists():
        for line in _envf.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from search import fetch_all
from scorer import score_jobs
from gaps import analyze_gaps
from report import generate_report
from enrich import enrich_free, enrich_epfo
from rank import rank_jobs, rescore, format_stats
import store
from export_web import export as export_web

VERSION = "2.0.0"


def main():
    # Emoji and the rupee sign break on a cp1252 Windows console.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="remote-job-hunter — remote job search + ranking")
    ap.add_argument("--profile-config", required=True, help="Your profile JSON")
    ap.add_argument("--profile-meta", required=True, help="Domain profile JSON")
    ap.add_argument("--output", default="daily_report.md", help="Report path")
    ap.add_argument("--limit", type=int, default=40, help="Jobs to show in the report")
    ap.add_argument("--no-enrich", action="store_true", help="Skip India-hiring enrichment")
    ap.add_argument("--no-paid-enrich", action="store_true",
                    help="Free India signals only — no billed EPFO lookups")
    ap.add_argument("--offline", action="store_true",
                    help="Replay the latest stored snapshots; makes no network calls")
    ap.add_argument("--new-only", action="store_true",
                    help="Report only jobs not seen in a previous run")
    ap.add_argument("--no-resolve", action="store_true",
                    help="Skip working out where each application actually goes")
    ap.add_argument("--resolve-budget", type=int, default=60,
                    help="Max listing pages to fetch when resolving destinations")
    ap.add_argument("--prune-days", type=int, default=30,
                    help="Delete raw snapshots older than this many days")
    ap.add_argument("--queue-applications", action="store_true",
                    help="Also write pending_applications.json for the (dormant) apply flow")
    args = ap.parse_args()

    profile_config = json.loads(Path(args.profile_config).read_text(encoding="utf-8"))
    profile_meta = json.loads(Path(args.profile_meta).read_text(encoding="utf-8"))

    domain = profile_meta["domain"]
    phrases = profile_meta["phrases"]
    resume_path = Path(profile_config["resume_path"]).expanduser()

    print(f"\n  remote-job-hunter v{VERSION}")
    print(f"  Profile: {profile_meta['label']} ({profile_config.get('name','')})")
    tz = profile_config.get("timezone", {})
    sal = profile_config.get("salary", {})
    print(f"  Window:  {tz.get('available_from','?')}-{tz.get('available_to','?')} "
          f"{tz.get('home_tz','')} | min overlap {tz.get('min_overlap_hours','?')}h")
    print(f"  Floor:   {sal.get('min_lpa','?')} LPA\n")

    if not resume_path.exists():
        print(f"  !! Resume not found: {resume_path}")
        print("     Fix resume_path in your profile — scoring needs it.\n")

    print("Fetching jobs..." if not args.offline else "Replaying snapshots (offline)...")
    jobs = fetch_all(domain, phrases, profile_config, profile_meta,
                     offline=args.offline)

    # Stamp is_new/first_seen before anything filters, so "new" means new to
    # you rather than new to the shortlist.
    new_count = store.mark_seen(jobs)
    print(f"  {new_count} never seen before "
          f"({len(jobs) - new_count} carried over from earlier runs)")
    if args.new_only:
        jobs = [j for j in jobs if j.get("is_new")]
        print(f"  --new-only: {len(jobs)} jobs")

    print("\nScoring against resume...")
    scored_jobs, known_skills = score_jobs(jobs, domain, str(resume_path),
                                           profile_config, profile_meta)

    if not args.no_enrich:
        print("\nChecking India hiring capability...")
        # Pass the full pull so the free board-index layer sees every company,
        # not only the ones that happened to survive scoring.
        enrich_free(scored_jobs, profile_config, all_jobs=jobs)

    print("\nRanking...")
    ranked, stats, unscored = rank_jobs(scored_jobs, profile_config)
    print(format_stats(stats))

    # Paid EPFO lookups run HERE, after ranking, so a small budget lands on the
    # roles actually worth applying to instead of on fetch order. The boosts it
    # applies change the scores, so the list is re-sorted afterwards.
    if not args.no_enrich and not args.no_paid_enrich:
        print("\nEPFO check on top candidates...")
        if enrich_epfo(ranked, profile_config):
            rescore(ranked, profile_config)

    # Close the loop on the paid source: attribute what actually ranked back
    # to the region that was billed for it, so the region list can be tuned
    # from evidence instead of argued about.
    try:
        import search as _search
        ranked_by_region = {}
        for j in ranked:
            if str(j.get("source", "")).startswith("RemoteRocketship"):
                r = j.get("location") or "(all)"
                ranked_by_region[r] = ranked_by_region.get(r, 0) + 1

        if _search.RR_REGION_STATS:
            for row in _search.RR_REGION_STATS:
                row["ranked"] = ranked_by_region.get(row["region"], 0)
            store.log_quota(_search.RR_REGION_STATS)
        elif ranked_by_region:
            # Served from cache, so nothing was billed and there are no new
            # rows — but regions bought by a top-up earlier today have never
            # been scored against a shortlist. Without this they stay at
            # ranked=0 and the efficiency table calls them worthless.
            store.backfill_ranked(ranked_by_region)

        if ranked_by_region:
            print("\nPaid-source efficiency (cumulative):")
            print(store.quota_report())
    except Exception as e:
        print(f"  quota accounting skipped: {e}")

    # Where does an application actually go? A listing URL is not an apply URL,
    # so without this every job looks like a generic form and the report cannot
    # say which systems are really in the pipeline. Runs after ranking so any
    # page fetches are spent on the roles worth applying to.
    if not args.no_resolve:
        print("\nResolving apply destinations...")
        try:
            from resolve import resolve_all
            resolve_all(ranked,
                        allow_fetch=not args.offline,
                        fetch_budget=args.resolve_budget)
        except Exception as e:
            print(f"  resolve skipped: {e}")

    print("\nAnalysing skill gaps...")
    gap_analysis = analyze_gaps(ranked, known_skills)
    gap_analysis["known_skills"] = known_skills

    print("\nWriting report...")
    generate_report(ranked, gap_analysis, profile_config, profile_meta,
                    args.output, stats=stats, limit=args.limit,
                    unscored=unscored)

    top = ranked[:8]
    if top:
        print(f"\n  Top {len(top)}:")
        for i, j in enumerate(top, 1):
            tzi = j.get("tz", {})
            oh = tzi.get("overlap_hours")
            lpa = (j.get("salary_lpa") or {}).get("lpa")
            print(f"   {i}. {j['title'][:42]:<44} {j['company'][:18]:<20} "
                  f"{j['final_score']:>5}  "
                  f"{'n/a' if oh is None else str(oh)+'h':>5}  "
                  f"{'—' if lpa is None else str(lpa)+' LPA':>10}  "
                  f"[{j.get('resume','?')}]")
    else:
        print("\n  No jobs survived the filters. Loosen min_overlap_hours or min_lpa.")

    try:
        web_path = export_web(ranked, unscored, stats, profile_config,
                              profile_meta, gap_analysis)
        print(f"  Web data: web/{web_path.name}")
    except Exception as e:
        print(f"  Web export failed: {e}")

    print(f"\n  Report: {args.output}")

    if args.prune_days:
        dropped = store.prune(args.prune_days)
        if dropped:
            print(f"  Pruned {dropped} snapshot day(s) older than {args.prune_days}d")
    st = store.stats()
    print(f"  Stored: {st['days']} day(s), {st['jobs']} jobs, "
          f"{st['mb']} MB in data/raw\n")

    # Dormant by design — see module docstring.
    if args.queue_applications and profile_config.get("auto_apply"):
        from apply import save_pending
        save_pending(ranked[:args.limit], profile_config.get("name", "User"))
        print("  Wrote pending_applications.json")


if __name__ == "__main__":
    main()
