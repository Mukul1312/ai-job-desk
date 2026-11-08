#!/usr/bin/env python3
"""
rr_probe.py — map RemoteRocketship's filter space at one job per question.

Billing is per job RETURNED (measured 2026-11-09), and `totalCount` does not
depend on how many you take. So `itemsPerPage: 1` buys the size of any filter's
result set for a single job. 50 probes cost 50 jobs out of 3,000 — 1.7% of a
day — and replace guessing about which regions are worth buying.

It also catches the failure that cost us a region: an unrecognised
`locationFilters` value is DROPPED by the API, and the query then returns the
unfiltered feed at full price, looking exactly like a successful search. Any
probe whose totalCount equals the unfiltered baseline is flagged IGNORED.

**Every row this buys is archived.** A probe is billed for the job it returns
whether or not we keep it, so discarding it is pure loss — the mistake this
tool exists to stop repeating. Rows merge into the day's raw archive beside a
normal fetch.

    python tools/rr_probe.py --preset configured      # just our region list
    python tools/rr_probe.py --preset emea,apac       # the wider map
    python tools/rr_probe.py --countries "Poland,Spain" --raw
    python tools/rr_probe.py --dry-run                # price it, buy nothing

Writes data/rr_probe_map.json (one entry per run date, history kept).
"""
import argparse, json, os, sys, time, urllib.request, urllib.error
from datetime import datetime, timezone
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

import search   # noqa: E402
import store    # noqa: E402

MAP_PATH = ROOT / "data" / "rr_probe_map.json"

PRESETS = {
    "configured": None,          # filled from the profile at runtime
    "cut": ["Netherlands", "Ireland", "Spain", "Portugal", "Poland", "France", "Sweden"],
    "emea": ["Czech", "Romania", "Hungary", "Bulgaria", "Greece", "Italy", "Belgium",
             "Switzerland", "Austria", "Denmark", "Norway", "Finland", "Estonia",
             "Ukraine", "Turkey", "Israel", "South Africa", "Egypt", "Kenya",
             "Nigeria", "Saudi Arabia", "Qatar"],
    "apac": ["Australia", "New Zealand", "Japan", "South Korea", "Taiwan", "Hong Kong",
             "Malaysia", "Philippines", "Viet Nam", "Indonesia", "Thailand",
             "Sri Lanka", "Pakistan"],
    "contrast": ["United States", "Canada"],
}


def _probe(key, rr, min_usd, extra, keep):
    """One probe: 1 job billed. Returns (totalCount, jobs_left, openings)."""
    filters = {"page": 1, "itemsPerPage": 1, "showRemoteJobs": True,
               "showHybridJobs": False, "showOnsiteJobs": False,
               "sortBy": "DateAdded", "hideGhostJobsFilter": "true",
               "showJobsWithoutSalaryWithMinSalaryFilter": True}
    for k in ("jobTitleFilters", "techStackFilters", "seniorityFilters",
              "keywordFilters", "excludedKeywordFilters", "employmentTypeFilters"):
        if rr.get(k):
            filters[k] = rr[k]
    if min_usd:
        filters["minSalaryFilter"] = min_usd
    filters.update(extra)

    body = json.dumps({"filters": filters,
                       "includeJobDescription": True}).encode()
    req = urllib.request.Request(
        search.RR_ENDPOINT, data=body, method="POST",
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json",
                 "User-Agent": "remote-job-hunter",
                 "RR-API-Version": "1"})
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                search._rr_note_quota(r.headers)
                data = json.loads(r.read().decode())
            got = data.get("jobOpenings") or []
            keep.extend(got)          # billed for it either way — never drop it
            return data.get("totalCount"), search.RR_QUOTA.get("jobs_left"), got
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise search._RRQuotaExhausted("daily cap reached")
            if e.code in (401, 403):
                raise search._RRQuotaExhausted(f"HTTP {e.code} — key or subscription")
            return f"HTTP{e.code}", None, []
        except Exception:
            if attempt == 2:
                return "ERR", None, []
            time.sleep(1)
    return "ERR", None, []


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile-config", default=str(ROOT / "mukul-profile.json"))
    ap.add_argument("--profile-meta", default=str(ROOT / "profiles/ai-agent-ml.json"))
    ap.add_argument("--preset", default="configured",
                    help="comma-separated: " + ",".join(PRESETS) + ",all")
    ap.add_argument("--countries", help="explicit comma-separated list, overrides --preset")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    key = os.getenv("REMOTEROCKETSHIP_API_KEY", "").strip()
    if not key:
        print("  no REMOTEROCKETSHIP_API_KEY in .env")
        return 1

    profile = json.loads(Path(args.profile_config).read_text(encoding="utf-8"))
    meta = json.loads(Path(args.profile_meta).read_text(encoding="utf-8"))
    rr = search._rr_validate_filters(dict(meta.get("remoterocketship", {})))
    sal = profile.get("salary", {})
    min_lpa = float(sal.get("min_lpa", 0) or 0)
    usd = float((sal.get("fx_to_inr") or {}).get("USD", 89.0))
    min_usd = int(min_lpa * 100_000 / usd) if min_lpa else 0

    PRESETS["configured"] = list(rr.get("locationFilters") or [])
    if args.countries:
        countries = [c.strip() for c in args.countries.split(",") if c.strip()]
    else:
        names = list(PRESETS) if args.preset == "all" else \
            [p.strip() for p in args.preset.split(",") if p.strip()]
        countries, seen = [], set()
        for n in names:
            for c in PRESETS.get(n) or []:
                if c not in seen:
                    seen.add(c); countries.append(c)

    # Validate before spending: an unknown country is not a narrow search.
    allowed = set((search._rr_enums() or {}).get("locationFilters") or [])
    if allowed:
        bad = [c for c in countries if c not in allowed]
        if bad:
            print(f"  not in RR's country list, skipping: {bad}")
            countries = [c for c in countries if c in allowed]

    n = len(countries) + 1          # +1 for the no-country baseline
    print(f"\n  {n} probes = {n} jobs billed (1 each). Rows are archived.")
    if args.dry_run:
        print("  --dry-run: nothing fetched.\n")
        return 0

    bought, rows = [], []
    try:
        base, left, _ = _probe(key, rr, min_usd, {}, bought)
        print(f"  baseline, our filters, no country : {base:,}"
              if isinstance(base, int) else f"  baseline: {base}")
        for c in countries:
            tc, left, _ = _probe(key, rr, min_usd, {"locationFilters": [c]}, bought)
            ignored = isinstance(tc, int) and isinstance(base, int) and tc == base
            flag = "  <-- IGNORED, returns the unfiltered feed" if ignored else ""
            pages = (-(-tc // 50)) if isinstance(tc, int) else None
            print(f"    {c:<22}{str(tc):>8}"
                  f"{('  ' + str(pages) + 'p') if pages else '':>6}{flag}")
            rows.append({"country": c, "totalCount": tc, "ignored": ignored,
                         "full_depth_pages": pages})
    except search._RRQuotaExhausted as e:
        print(f"  stopped — {e}")

    # Archive every row we were billed for, merged beside a normal fetch.
    if bought:
        path, added = store.merge_raw("remoterocketship", bought)
        print(f"\n  archived {len(bought)} billed row(s), {added} new to "
              f"{path.name if path else '?'}")

    if rows:
        hist = {}
        if MAP_PATH.exists():
            try:
                hist = json.loads(MAP_PATH.read_text(encoding="utf-8"))
            except Exception:
                hist = {}
        if "runs" not in hist:
            hist = {"runs": []}      # older flat format is replaced, not merged
        hist["runs"].append({
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "baseline_our_filters": base,
            "jobs_left_after": search.RR_QUOTA.get("jobs_left"),
            "rows": rows,
        })
        MAP_PATH.write_text(json.dumps(hist, indent=1, ensure_ascii=False),
                            encoding="utf-8")
        print(f"  map: {MAP_PATH}  ({len(hist['runs'])} run(s) on record)")

    q = search.RR_QUOTA
    if q.get("jobs_left") is not None:
        print(f"  quota (from the API): {q['jobs_left']:,} jobs / "
              f"{q.get('requests_left')} requests left\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
