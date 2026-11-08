#!/usr/bin/env python3
"""
rr_topup.py — spend leftover RemoteRocketship quota before it expires.

The daily cap (3000 jobs / 500 requests) is a cap, not a balance. Whatever
is unspent at 00:00 UTC — 05:30 IST — is gone. A normal run uses a fraction
of it, so the remainder is free evidence if it is spent on something the
regular run cannot tell us:

  * regions not in profiles/*.remoterocketship.locationFilters, to measure
    what we gave up when the list was cut
  * deeper pages of a region that returned no duplicates, which means its
    listings were not exhausted at the page limit

Results merge into today's snapshot, so the next ./run.sh ranks and
publishes them without re-buying anything.

    # what is left, without spending it
    python tools/rr_topup.py --status

    # measure three regions we cut
    python tools/rr_topup.py --regions "Netherlands,Ireland,Poland"

    # is there a page 3 behind India's zero-duplicate first two pages?
    python tools/rr_topup.py --regions India --start-page 3 --pages 2

    # see the cost first
    python tools/rr_topup.py --regions Spain --dry-run
"""
import argparse, json, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    envf = ROOT / ".env"
    if envf.exists():
        for line in envf.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

import search      # noqa: E402
import store       # noqa: E402


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(
        description="Spend leftover RemoteRocketship quota on extra regions or pages",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    ap.add_argument("--profile-config", default=str(ROOT / "mukul-profile.json"))
    ap.add_argument("--profile-meta", default=str(ROOT / "profiles/ai-agent-ml.json"))
    ap.add_argument("--regions", help="Comma-separated, e.g. 'Netherlands,Ireland'")
    ap.add_argument("--pages", type=int, default=2, help="Pages per region (50 jobs each)")
    ap.add_argument("--start-page", type=int, default=1,
                    help="First page to buy; use >1 to skip pages already on disk")
    ap.add_argument("--dry-run", action="store_true", help="Price it, fetch nothing")
    ap.add_argument("--status", action="store_true",
                    help="Show quota and today's per-region spend, then exit")
    args = ap.parse_args()

    if args.status:
        return status()

    if not args.regions:
        ap.error("--regions is required (or use --status)")

    regions = [r.strip() for r in args.regions.split(",") if r.strip()]
    if not regions:
        ap.error("--regions parsed to nothing")

    profile_config = json.loads(Path(args.profile_config).read_text(encoding="utf-8"))
    profile_meta = json.loads(Path(args.profile_meta).read_text(encoding="utf-8"))

    configured = profile_meta.get("remoterocketship", {}).get("locationFilters") or []
    overlap = [r for r in regions if r in configured]
    if overlap and args.start_page == 1:
        print(f"  note: {', '.join(overlap)} already in the daily region list.")
        print("        Pages 1+ were fetched this morning, so this re-buys them.")
        print("        Use --start-page to go deeper instead.\n")

    print("\n  RemoteRocketship top-up")
    billed = search.topup_remoterocketship(
        profile_config, profile_meta, regions,
        max_pages=args.pages, start_page=args.start_page,
        dry_run=args.dry_run)
    print()
    return 0 if (billed or args.dry_run) else 1


def status():
    spent_jobs, spent_reqs = search._rr_spent_today()
    h, m, nxt = search._utc_reset_in()
    print(f"\n  RemoteRocketship quota — UTC day {search.datetime_utc_date()}")
    print(f"    cap       : {search.RR_DAILY_JOBS:,} jobs / "
          f"{search.RR_DAILY_REQUESTS} requests")
    print(f"    spent     : {spent_jobs:,} jobs / {spent_reqs} requests")
    print(f"    REMAINING : {search.RR_DAILY_JOBS - spent_jobs:,} jobs / "
          f"{search.RR_DAILY_REQUESTS - spent_reqs} requests")
    print(f"    expires in: {h}h {m}m  ({nxt:%H:%M} UTC / 05:30 IST) — unused is lost")

    today = search.datetime_utc_date()
    rows = [r for r in store.read_quota_log() if r.get("date") == today]
    if rows:
        print(f"\n  Bought today:")
        print(f"    {'region':<26}{'pages':>7}{'billed':>8}{'unique':>8}{'ranked':>8}")
        for r in sorted(rows, key=lambda x: -int(x.get("billed") or 0)):
            print(f"    {str(r.get('region'))[:25]:<26}"
                  f"{str(r.get('pages', '?')):>7}"
                  f"{int(r.get('billed') or 0):>8}"
                  f"{int(r.get('unique') or 0):>8}"
                  f"{int(r.get('ranked') or 0):>8}")

    print("\n  Cumulative:")
    print(store.quota_report())
    return 0


if __name__ == "__main__":
    sys.exit(main())
