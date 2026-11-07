#!/usr/bin/env python3
"""
timezone.py — Working-hours overlap between you and a job's location.

The question this answers: if this team works 9-6 where they are, and you are
available 9 AM - 8 PM IST, how many hours a day can you actually talk to them?

Uses zoneinfo (stdlib, py3.9+) so DST is handled correctly rather than assumed.

    >>> assess("Berlin, Germany", cfg)
    {'tz': 'Europe/Berlin', 'region': 'emea', 'overlap_hours': 6.5, 'verdict': 'strong'}
"""
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

# ── Location → IANA timezone ─────────────────────────────────────────────────
# Ordered longest-key-first at match time so "new zealand" beats "zealand".

LOCATION_TZ = {
    # ---- India ----
    "india": ("Asia/Kolkata", "india"),
    "bengaluru": ("Asia/Kolkata", "india"), "bangalore": ("Asia/Kolkata", "india"),
    "mumbai": ("Asia/Kolkata", "india"),    "bombay": ("Asia/Kolkata", "india"),
    "delhi": ("Asia/Kolkata", "india"),     "new delhi": ("Asia/Kolkata", "india"),
    "hyderabad": ("Asia/Kolkata", "india"), "pune": ("Asia/Kolkata", "india"),
    "chennai": ("Asia/Kolkata", "india"),   "gurgaon": ("Asia/Kolkata", "india"),
    "gurugram": ("Asia/Kolkata", "india"),  "noida": ("Asia/Kolkata", "india"),
    "kolkata": ("Asia/Kolkata", "india"),   "ahmedabad": ("Asia/Kolkata", "india"),
    "kanpur": ("Asia/Kolkata", "india"),    "jaipur": ("Asia/Kolkata", "india"),
    "indore": ("Asia/Kolkata", "india"),    "kochi": ("Asia/Kolkata", "india"),

    # ---- EMEA: UK & Ireland ----
    "united kingdom": ("Europe/London", "emea"), "uk": ("Europe/London", "emea"),
    "england": ("Europe/London", "emea"),   "london": ("Europe/London", "emea"),
    "scotland": ("Europe/London", "emea"),  "manchester": ("Europe/London", "emea"),
    "ireland": ("Europe/Dublin", "emea"),   "dublin": ("Europe/Dublin", "emea"),

    # ---- EMEA: Western Europe ----
    "germany": ("Europe/Berlin", "emea"),   "berlin": ("Europe/Berlin", "emea"),
    "munich": ("Europe/Berlin", "emea"),    "hamburg": ("Europe/Berlin", "emea"),
    "netherlands": ("Europe/Amsterdam", "emea"), "amsterdam": ("Europe/Amsterdam", "emea"),
    "rotterdam": ("Europe/Amsterdam", "emea"),
    "france": ("Europe/Paris", "emea"),     "paris": ("Europe/Paris", "emea"),
    "belgium": ("Europe/Brussels", "emea"), "brussels": ("Europe/Brussels", "emea"),
    "spain": ("Europe/Madrid", "emea"),     "madrid": ("Europe/Madrid", "emea"),
    "barcelona": ("Europe/Madrid", "emea"),
    "portugal": ("Europe/Lisbon", "emea"),  "lisbon": ("Europe/Lisbon", "emea"),
    "porto": ("Europe/Lisbon", "emea"),
    "italy": ("Europe/Rome", "emea"),       "rome": ("Europe/Rome", "emea"),
    "milan": ("Europe/Rome", "emea"),
    "switzerland": ("Europe/Zurich", "emea"), "zurich": ("Europe/Zurich", "emea"),
    "austria": ("Europe/Vienna", "emea"),   "vienna": ("Europe/Vienna", "emea"),

    # ---- EMEA: Nordics ----
    "sweden": ("Europe/Stockholm", "emea"), "stockholm": ("Europe/Stockholm", "emea"),
    "finland": ("Europe/Helsinki", "emea"), "helsinki": ("Europe/Helsinki", "emea"),
    "denmark": ("Europe/Copenhagen", "emea"), "copenhagen": ("Europe/Copenhagen", "emea"),
    "norway": ("Europe/Oslo", "emea"),      "oslo": ("Europe/Oslo", "emea"),
    "iceland": ("Atlantic/Reykjavik", "emea"),

    # ---- EMEA: Central/Eastern Europe ----
    "poland": ("Europe/Warsaw", "emea"),    "warsaw": ("Europe/Warsaw", "emea"),
    "krakow": ("Europe/Warsaw", "emea"),
    "czech": ("Europe/Prague", "emea"),     "prague": ("Europe/Prague", "emea"),
    "romania": ("Europe/Bucharest", "emea"), "bucharest": ("Europe/Bucharest", "emea"),
    "hungary": ("Europe/Budapest", "emea"), "budapest": ("Europe/Budapest", "emea"),
    "greece": ("Europe/Athens", "emea"),    "athens": ("Europe/Athens", "emea"),
    "estonia": ("Europe/Tallinn", "emea"),  "tallinn": ("Europe/Tallinn", "emea"),
    "lithuania": ("Europe/Vilnius", "emea"), "latvia": ("Europe/Riga", "emea"),
    "bulgaria": ("Europe/Sofia", "emea"),   "serbia": ("Europe/Belgrade", "emea"),
    "ukraine": ("Europe/Kyiv", "emea"),

    # ---- EMEA: Middle East & Africa ----
    "uae": ("Asia/Dubai", "emea"),          "dubai": ("Asia/Dubai", "emea"),
    "abu dhabi": ("Asia/Dubai", "emea"),
    "united arab emirates": ("Asia/Dubai", "emea"),
    "israel": ("Asia/Jerusalem", "emea"),   "tel aviv": ("Asia/Jerusalem", "emea"),
    "turkey": ("Europe/Istanbul", "emea"),  "istanbul": ("Europe/Istanbul", "emea"),
    "south africa": ("Africa/Johannesburg", "emea"),
    "cape town": ("Africa/Johannesburg", "emea"),
    "johannesburg": ("Africa/Johannesburg", "emea"),
    "nigeria": ("Africa/Lagos", "emea"),    "lagos": ("Africa/Lagos", "emea"),
    "kenya": ("Africa/Nairobi", "emea"),    "nairobi": ("Africa/Nairobi", "emea"),
    "egypt": ("Africa/Cairo", "emea"),      "saudi": ("Asia/Riyadh", "emea"),

    # ---- APAC ----
    "singapore": ("Asia/Singapore", "apac"),
    "australia": ("Australia/Sydney", "apac"), "sydney": ("Australia/Sydney", "apac"),
    "melbourne": ("Australia/Melbourne", "apac"),
    "brisbane": ("Australia/Brisbane", "apac"), "perth": ("Australia/Perth", "apac"),
    "new zealand": ("Pacific/Auckland", "apac"), "auckland": ("Pacific/Auckland", "apac"),
    "japan": ("Asia/Tokyo", "apac"),        "tokyo": ("Asia/Tokyo", "apac"),
    "hong kong": ("Asia/Hong_Kong", "apac"),
    "china": ("Asia/Shanghai", "apac"),     "shanghai": ("Asia/Shanghai", "apac"),
    "shenzhen": ("Asia/Shanghai", "apac"),  "beijing": ("Asia/Shanghai", "apac"),
    "indonesia": ("Asia/Jakarta", "apac"),  "jakarta": ("Asia/Jakarta", "apac"),
    "philippines": ("Asia/Manila", "apac"), "manila": ("Asia/Manila", "apac"),
    "vietnam": ("Asia/Ho_Chi_Minh", "apac"), "hanoi": ("Asia/Ho_Chi_Minh", "apac"),
    "thailand": ("Asia/Bangkok", "apac"),   "bangkok": ("Asia/Bangkok", "apac"),
    "malaysia": ("Asia/Kuala_Lumpur", "apac"),
    "kuala lumpur": ("Asia/Kuala_Lumpur", "apac"),
    "south korea": ("Asia/Seoul", "apac"),  "seoul": ("Asia/Seoul", "apac"),
    "taiwan": ("Asia/Taipei", "apac"),      "sri lanka": ("Asia/Colombo", "apac"),

    # ---- Americas ----
    "united states": ("America/New_York", "americas"),
    "usa": ("America/New_York", "americas"), "u.s.": ("America/New_York", "americas"),
    "new york": ("America/New_York", "americas"),
    "boston": ("America/New_York", "americas"),
    "atlanta": ("America/New_York", "americas"),
    "miami": ("America/New_York", "americas"),
    "chicago": ("America/Chicago", "americas"),
    "austin": ("America/Chicago", "americas"), "texas": ("America/Chicago", "americas"),
    "denver": ("America/Denver", "americas"),
    "california": ("America/Los_Angeles", "americas"),
    "san francisco": ("America/Los_Angeles", "americas"),
    "los angeles": ("America/Los_Angeles", "americas"),
    "seattle": ("America/Los_Angeles", "americas"),
    "canada": ("America/Toronto", "americas"), "toronto": ("America/Toronto", "americas"),
    "vancouver": ("America/Vancouver", "americas"),
    "brazil": ("America/Sao_Paulo", "americas"),
    "sao paulo": ("America/Sao_Paulo", "americas"),
    "mexico": ("America/Mexico_City", "americas"),
    "argentina": ("America/Argentina/Buenos_Aires", "americas"),
    "colombia": ("America/Bogota", "americas"), "chile": ("America/Santiago", "americas"),
}

# Phrases meaning "no timezone constraint" — treated as fully compatible.
WFA_PHRASES = [
    "worldwide", "anywhere", "work from anywhere", "wfa", "global",
    "no location restriction", "remote worldwide", "fully remote",
    "100% remote", "any timezone", "async",
]

# "Remote (Europe)" style hints that name a region but no city.
REGION_FALLBACK = {
    # EEA/EU are hiring blocs, not timezones; anchor them to CET, which is
    # where most of their population and working hours sit.
    "eea": ("Europe/Berlin", "emea"),
    "emea": ("Europe/Berlin", "emea"),
    "europe": ("Europe/Berlin", "emea"),
    "european": ("Europe/Berlin", "emea"),
    "eu": ("Europe/Berlin", "emea"),
    "apac": ("Asia/Singapore", "apac"),
    "asia": ("Asia/Singapore", "apac"),
    "latam": ("America/Sao_Paulo", "americas"),
    "americas": ("America/New_York", "americas"),
    "north america": ("America/New_York", "americas"),
}


def resolve_timezone(location):
    """
    location str -> (iana_tz | None, region_tag).

    Returns (None, 'wfa') for worldwide/anywhere listings, which have no
    constraint at all and should not be penalised.
    """
    if not location:
        return None, "unknown"
    loc = str(location).lower().strip()

    if any(p in loc for p in WFA_PHRASES):
        return None, "wfa"

    # Longest key first: "new zealand" must win over "zealand",
    # "united kingdom" over "uk".
    for key in sorted(LOCATION_TZ, key=len, reverse=True):
        if key in loc:
            return LOCATION_TZ[key]

    for key in sorted(REGION_FALLBACK, key=len, reverse=True):
        if key in loc:
            return REGION_FALLBACK[key]

    return None, "unknown"


def _parse_hhmm(s):
    h, m = str(s).split(":")
    return time(int(h), int(m))


def _to_utc_minutes(t, tz, ref):
    """Local wall-clock time in tz -> minutes since UTC midnight on ref date."""
    dt = datetime.combine(ref, t, tzinfo=ZoneInfo(tz))
    u = dt.astimezone(ZoneInfo("UTC"))
    return u.hour * 60 + u.minute, u.date()


def _interval_utc(start, end, tz, ref):
    """
    Local [start,end) in tz -> absolute minute interval on a UTC timeline,
    anchored so the start lands on day 0. Handles windows that cross midnight.
    """
    s_min, s_date = _to_utc_minutes(start, tz, ref)
    e_min, e_date = _to_utc_minutes(end, tz, ref)
    s_abs = s_min + (s_date - ref).days * 1440
    e_abs = e_min + (e_date - ref).days * 1440
    if e_abs <= s_abs:          # crossed midnight
        e_abs += 1440
    return s_abs, e_abs


def overlap_hours(job_tz, tz_cfg, ref=None):
    """
    Hours per day you and a team in job_tz are both working.

    Checks day offsets -1/0/+1 so a Sydney morning still matches an
    Indian evening rather than falling off the end of the timeline.
    """
    ref = ref or datetime.now(ZoneInfo("UTC")).date()
    home_tz = tz_cfg.get("home_tz", "Asia/Kolkata")

    u_s, u_e = _interval_utc(
        _parse_hhmm(tz_cfg.get("available_from", "09:00")),
        _parse_hhmm(tz_cfg.get("available_to", "20:00")),
        home_tz, ref,
    )
    j_from, j_to = tz_cfg.get("assume_job_hours", ["09:00", "18:00"])
    j_s, j_e = _interval_utc(_parse_hhmm(j_from), _parse_hhmm(j_to), job_tz, ref)

    best = 0.0
    for shift in (-1440, 0, 1440):
        lo = max(u_s, j_s + shift)
        hi = min(u_e, j_e + shift)
        best = max(best, (hi - lo) / 60.0)
    return round(best, 2)


def assess(location, tz_cfg):
    """
    Full timezone verdict for one job.

    overlap_hours is None for WFA/unknown — absence of evidence, not a zero.
    Callers must not treat None as a failure.
    """
    tz, region = resolve_timezone(location)
    min_h = float(tz_cfg.get("min_overlap_hours", 4.0))
    pref_h = float(tz_cfg.get("preferred_overlap_hours", 6.0))

    if region == "wfa":
        return {"tz": None, "region": "wfa", "overlap_hours": None,
                "verdict": "wfa", "passes": True,
                "note": "No location restriction"}

    if tz is None:
        return {"tz": None, "region": "unknown", "overlap_hours": None,
                "verdict": "unknown", "passes": True,
                "note": f"Could not resolve timezone from {location!r}"}

    h = overlap_hours(tz, tz_cfg)
    if h >= pref_h:
        verdict = "strong"
    elif h >= min_h:
        verdict = "workable"
    else:
        verdict = "poor"

    return {"tz": tz, "region": region, "overlap_hours": h,
            "verdict": verdict, "passes": h >= min_h,
            "note": f"{h}h overlap with {tz.split('/')[-1].replace('_',' ')}"}


if __name__ == "__main__":
    cfg = {"home_tz": "Asia/Kolkata", "available_from": "09:00",
           "available_to": "20:00", "min_overlap_hours": 4.0,
           "preferred_overlap_hours": 6.0, "assume_job_hours": ["09:00", "18:00"]}
    samples = [
        "Berlin, Germany", "London, United Kingdom", "Amsterdam, Netherlands",
        "Dublin, Ireland", "Singapore", "Dubai, UAE", "Sydney, Australia",
        "Tokyo, Japan", "Bengaluru, India", "New York, United States",
        "San Francisco, CA", "Remote (Europe)", "Worldwide", "Mars Colony",
    ]
    print(f"Your window: {cfg['available_from']}-{cfg['available_to']} IST\n")
    print(f"{'Location':<32}{'Region':<10}{'Overlap':<10}Verdict")
    print("-" * 66)
    for s in samples:
        a = assess(s, cfg)
        oh = "n/a" if a["overlap_hours"] is None else f"{a['overlap_hours']}h"
        print(f"{s:<32}{a['region']:<10}{oh:<10}{a['verdict']}")
