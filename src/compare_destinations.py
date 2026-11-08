#!/usr/bin/env python3
"""
compare_destinations.py — check resolve.py's predictions against reality.

resolve.py decides where an application goes by reading the listing. The
recorder extension records where applications actually went. Neither is worth
much alone: the prediction is unverified, and the recording only covers jobs
already applied to. Together they say whether the pipeline's picture of which
systems matter can be trusted.

Usage:

    python src/compare_destinations.py applications-2026-11-15.json

Reads the extension's export, matches each recorded application back to a
predicted destination, and prints where the two agree.

Matching is by listing URL where the browser passed a referrer, and otherwise
by apply URL against the resolver cache. Applications it cannot match are
reported rather than dropped: an unmatched row usually means a job was found
somewhere the pipeline does not search, which is itself worth knowing.
"""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent))

from resolve import classify, load_cache  # noqa: E402

WORKSPACE = Path(__file__).parent.parent


# Params that identify the campaign that sent you, not the job.
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gh_src", "gh_jid", "ref", "referrer", "source", "src", "fbclid", "gclid",
    "lever-source", "trackingid", "trk", "origin",
}


def _norm(url):
    """
    Compare URLs without being defeated by tracking params or a trailing slash.

    The query string has to survive. A Hacker News listing is
    news.ycombinator.com/item?id=50349129 - the id is the *only* thing telling
    one job from another, so dropping the query collapses every HN listing onto
    one key and silently makes them all the same job.
    """
    if not url:
        return ""
    try:
        p = urlparse(url.lower().split("#")[0])
        kept = [
            kv for kv in p.query.split("&")
            if kv and kv.split("=")[0] not in TRACKING_PARAMS
        ]
        base = f"{p.netloc}{p.path}".rstrip("/")
        return f"{base}?{'&'.join(sorted(kept))}" if kept else base
    except Exception:  # noqa: BLE001
        return url.lower().rstrip("/")


def load_predictions():
    """
    Every destination the pipeline has predicted, keyed both ways.

    The resolver cache is keyed by listing URL. We also index by predicted
    apply URL, because a recorded application may only carry the destination
    it landed on, with no referrer to tie it back to the listing.
    """
    cache = load_cache()
    by_listing, by_apply = {}, {}
    for listing_url, pred in cache.items():
        by_listing[_norm(listing_url)] = (listing_url, pred)
        apply_url = pred.get("apply_url")
        if apply_url and not apply_url.startswith("mailto:"):
            by_apply[_norm(apply_url)] = (listing_url, pred)
    return by_listing, by_apply


def match(app, by_listing, by_apply):
    """Find the prediction for one recorded application, if there is one."""
    listing = _norm(app.get("listingUrl"))
    if listing and listing in by_listing:
        return by_listing[listing] + ("listing-url",)

    apply_norm = _norm(app.get("applyUrl"))
    if apply_norm and apply_norm in by_apply:
        return by_apply[apply_norm] + ("apply-url",)

    # An ATS application URL usually embeds the employer's board slug, so a
    # host-level match is weak but better than discarding the row.
    host = urlparse((app.get("applyUrl") or "").lower()).netloc
    if host:
        for key, (listing_url, pred) in by_apply.items():
            if key.startswith(host):
                return listing_url, pred, "host-only"
    return None, None, "unmatched"


def main(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    apps = data.get("applications", data if isinstance(data, list) else [])
    if not apps:
        print("No applications in that export.")
        return 1

    submitted = [a for a in apps if a.get("submitted")]
    by_listing, by_apply = load_predictions()

    print(f"Recorded: {len(apps)} forms seen, {len(submitted)} submitted")
    print(f"Predictions available: {len(by_listing)} listings\n")

    agree, disagree, unmatched = [], [], []
    how = Counter()

    # Only submitted applications are evidence. A form merely opened may have
    # been closed because it was the wrong job.
    population = submitted or apps
    if not submitted:
        print("No submitted applications yet - comparing forms seen instead.\n")

    for app in population:
        listing_url, pred, via = match(app, by_listing, by_apply)
        how[via] += 1
        if pred is None:
            unmatched.append(app)
            continue
        predicted = pred.get("apply_kind", "unresolved")
        actual = app.get("ats") or classify(app.get("applyUrl", ""))
        row = (app.get("company") or app.get("host"), predicted, actual, via)
        (agree if predicted == actual else disagree).append(row)

    checked = len(agree) + len(disagree)
    if checked:
        pct = 100.0 * len(agree) / checked
        print(f"Predicted correctly: {len(agree)}/{checked}  ({pct:.0f}%)\n")

    if disagree:
        print("WHERE THE PREDICTION WAS WRONG")
        for company, predicted, actual, via in disagree[:25]:
            print(f"  {str(company)[:28]:<28} predicted {predicted:<12} "
                  f"actually {actual:<12} [{via}]")
        print()

        # A repeated miss is a resolver bug; a scattered one is just noise.
        pairs = Counter((p, a) for _, p, a, _ in disagree)
        repeated = [(k, n) for k, n in pairs.most_common() if n > 1]
        if repeated:
            print("  Repeated misses (fix these in resolve.py first):")
            for (predicted, actual), n in repeated:
                print(f"    {n:>3}x  {predicted} -> really {actual}")
            print()

    if unmatched:
        print(f"UNMATCHED ({len(unmatched)}) - applied to, but not from this "
              f"pipeline's listings:")
        for app in unmatched[:15]:
            print(f"  {str(app.get('company') or app.get('host'))[:30]:<30} "
                  f"{app.get('ats','?'):<12} {str(app.get('applyUrl',''))[:46]}")
        print()

    print("WHAT YOU ACTUALLY APPLIED THROUGH")
    for kind, n in Counter(a.get("ats", "unknown") for a in population).most_common():
        print(f"  {n:>4}  {kind}")

    # The payoff: which handler is worth writing next.
    print("\nFIELDS MOST ASKED FOR (across submitted applications)")
    labels = defaultdict(int)
    for app in population:
        seen = set()
        for f in app.get("fields", []):
            key = (f.get("label") or f.get("name") or f.get("type") or "").lower()[:40]
            if key and key not in seen:
                seen.add(key)
                labels[key] += 1
    for label, n in sorted(labels.items(), key=lambda kv: -kv[1])[:18]:
        print(f"  {n:>4}  {label}")

    print(f"\nMatch methods: {dict(how)}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
