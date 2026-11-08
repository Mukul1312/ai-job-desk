// GENERATED FILE - do not edit by hand.
// Source: src/resolve.py (ATS_PATTERNS, AGGREGATOR_HOSTS, NOISE_HOSTS)
// Regenerate: python src/gen_extension_patterns.py
//
// Shared so the recorder and the resolver classify a destination the same way.
// If they drift, the comparison in src/compare_destinations.py measures the
// drift rather than the predictions.

const ATS_PATTERNS = [
  [
    "greenhouse",
    [
      "greenhouse.io",
      "boards.greenhouse.io",
      "job-boards.greenhouse.io"
    ]
  ],
  [
    "lever",
    [
      "lever.co",
      "jobs.lever.co",
      "hire.lever.co"
    ]
  ],
  [
    "ashby",
    [
      "ashbyhq.com",
      "jobs.ashbyhq.com"
    ]
  ],
  [
    "workday",
    [
      "myworkdayjobs.com",
      "workday.com",
      "wd1.",
      "wd3.",
      "wd5."
    ]
  ],
  [
    "smartrecruiters",
    [
      "smartrecruiters.com"
    ]
  ],
  [
    "workable",
    [
      "workable.com",
      "apply.workable.com"
    ]
  ],
  [
    "taleo",
    [
      "taleo.net",
      "taleo.com"
    ]
  ],
  [
    "bamboohr",
    [
      "bamboohr.com",
      "bamboohr.co.uk"
    ]
  ],
  [
    "jazzhr",
    [
      "applytojob.com",
      "jazzhr.com"
    ]
  ],
  [
    "breezy",
    [
      "breezy.hr"
    ]
  ],
  [
    "recruitee",
    [
      "recruitee.com"
    ]
  ],
  [
    "teamtailor",
    [
      "teamtailor.com"
    ]
  ],
  [
    "rippling",
    [
      "rippling.com",
      "ats.rippling.com"
    ]
  ],
  [
    "personio",
    [
      "personio.de",
      "jobs.personio.com"
    ]
  ],
  [
    "jobvite",
    [
      "jobvite.com"
    ]
  ],
  [
    "icims",
    [
      "icims.com"
    ]
  ],
  [
    "pinpoint",
    [
      "pinpointhq.com"
    ]
  ],
  [
    "comeet",
    [
      "comeet.com",
      "comeet.co"
    ]
  ],
  [
    "dayforce",
    [
      "dayforcehcm.com"
    ]
  ],
  [
    "manatal",
    [
      "manatal.com"
    ]
  ],
  [
    "yc_waas",
    [
      "ycombinator.com/companies",
      "workatastartup.com"
    ]
  ],
  [
    "wellfound",
    [
      "wellfound.com",
      "angel.co"
    ]
  ],
  [
    "linkedin",
    [
      "linkedin.com/jobs",
      "linkedin.com/easy-apply"
    ]
  ],
  [
    "notion_form",
    [
      "notion.site",
      "notion.so"
    ]
  ],
  [
    "tally",
    [
      "tally.so"
    ]
  ],
  [
    "typeform",
    [
      "typeform.com"
    ]
  ],
  [
    "airtable",
    [
      "airtable.com"
    ]
  ],
  [
    "fillout",
    [
      "fillout.com"
    ]
  ],
  [
    "google_form",
    [
      "docs.google.com/forms",
      "forms.gle"
    ]
  ]
];

const AGGREGATOR_HOSTS = [
  "news.ycombinator.com",
  "gethired.devhub.co.in",
  "remoteok.com",
  "remoteok.io",
  "remotive.com",
  "workingnomads.com",
  "himalayas.app",
  "weworkremotely.com",
  "remoterocketship.com"
];

const NOISE_HOSTS = [
  "twitter.com",
  "x.com",
  "github.com",
  "youtube.com",
  "youtu.be",
  "medium.com",
  "substack.com",
  "crunchbase.com",
  "glassdoor.com",
  "facebook.com",
  "instagram.com",
  "t.me",
  "discord.gg",
  "discord.com",
  "calendly.com",
  "news.ycombinator.com",
  "en.wikipedia.org",
  "google.com/maps",
  "goo.gl/maps",
  "apple.com",
  "play.google.com"
];

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
