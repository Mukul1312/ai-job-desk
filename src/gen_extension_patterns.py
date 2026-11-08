#!/usr/bin/env python3
"""
gen_extension_patterns.py — keep the recorder extension and resolve.py agreeing
on what counts as which applicant tracking system.

The whole point of the extension is to check resolve.py's predictions against
what really happens when an application is submitted. If the two sides disagree
about what "ashby" means, that comparison measures the disagreement instead of
the thing we care about. So there is one source of truth — ATS_PATTERNS in
resolve.py — and this script copies it into the extension.

Run it after changing ATS_PATTERNS:

    python src/gen_extension_patterns.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from resolve import ATS_PATTERNS, AGGREGATOR_HOSTS, NOISE_HOSTS  # noqa: E402

OUT = Path(__file__).parent.parent / "extension" / "ats-patterns.js"

TEMPLATE = """\
// GENERATED FILE - do not edit by hand.
// Source: src/resolve.py (ATS_PATTERNS, AGGREGATOR_HOSTS, NOISE_HOSTS)
// Regenerate: python src/gen_extension_patterns.py
//
// Shared so the recorder and the resolver classify a destination the same way.
// If they drift, the comparison in src/compare_destinations.py measures the
// drift rather than the predictions.

const ATS_PATTERNS = %(ats)s;

const AGGREGATOR_HOSTS = %(agg)s;

const NOISE_HOSTS = %(noise)s;

/** Name the system behind a URL, or "direct" / "unknown". */
function classifyUrl(url) {
  if (!url) return "unknown";
  const low = url.toLowerCase();
  if (low.startsWith("mailto:")) return "email";
  for (const [kind, needles] of ATS_PATTERNS) {
    if (needles.some((n) => low.includes(n))) return kind;
  }
  let host = "";
  try {
    host = new URL(low).hostname;
  } catch (e) {
    return "unknown";
  }
  if (host && !AGGREGATOR_HOSTS.some((a) => host.includes(a))) return "direct";
  return "unknown";
}

/** Is this URL a job board rather than an application destination? */
function isAggregator(url) {
  if (!url) return false;
  try {
    const host = new URL(url.toLowerCase()).hostname;
    return AGGREGATOR_HOSTS.some((a) => host.includes(a));
  } catch (e) {
    return false;
  }
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { classifyUrl, isAggregator, ATS_PATTERNS };
}
"""


def main():
    ats = json.dumps([[kind, list(needles)] for kind, needles in ATS_PATTERNS],
                     indent=2)
    agg = json.dumps(list(AGGREGATOR_HOSTS), indent=2)
    noise = json.dumps(list(NOISE_HOSTS), indent=2)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(TEMPLATE % {"ats": ats, "agg": agg, "noise": noise},
                   encoding="utf-8")
    print(f"Wrote {OUT.relative_to(Path(__file__).parent.parent)} "
          f"({len(ATS_PATTERNS)} systems, {len(AGGREGATOR_HOSTS)} aggregators)")


if __name__ == "__main__":
    main()
