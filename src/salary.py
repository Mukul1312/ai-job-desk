#!/usr/bin/env python3
"""
salary.py — Normalise any board's salary string to Indian LPA.

Every board quotes differently: "$120k-$150k USD", "15 LPA", "₹18,00,000",
"90000.0-120000.0", "€80k". This turns all of them into one comparable
number — lakhs per annum, INR — so a 15 LPA floor actually means something.

    >>> to_lpa("$120k-$150k USD", fx)["lpa"]      # midpoint
    120.15
    >>> to_lpa("18 LPA", fx)["lpa"]
    18.0

Unparseable or absent salary returns lpa=None. That is "unknown", not zero —
callers decide whether unknown passes (profile: salary.accept_unlisted).
"""
import re

DEFAULT_FX_TO_INR = {
    "USD": 89.0, "EUR": 97.0, "GBP": 113.0, "SGD": 66.0,
    "AUD": 58.0, "AED": 24.0, "CAD": 64.0, "INR": 1.0,
}

CURRENCY_SIGNS = [
    ("₹", "INR"), ("rs.", "INR"), ("rs ", "INR"), ("inr", "INR"),
    ("$", "USD"), ("usd", "USD"), ("us$", "USD"),
    ("€", "EUR"), ("eur", "EUR"),
    ("£", "GBP"), ("gbp", "GBP"),
    ("sgd", "SGD"), ("s$", "SGD"),
    ("aud", "AUD"), ("a$", "AUD"),
    ("aed", "AED"), ("dhs", "AED"), ("dirham", "AED"),
    ("cad", "CAD"), ("c$", "CAD"),
]

NOT_LISTED = {"", "not listed", "none", "n/a", "na", "-", "–",
              "competitive", "doe", "negotiable", "unspecified"}

# Indian notations. "15 LPA" / "15 lakh" / "₹15L" all mean 1,500,000 INR.
_LPA_RE   = re.compile(r'(\d+(?:\.\d+)?)\s*(?:lpa|lac|lakh|lakhs|l\b)', re.I)
_CRORE_RE = re.compile(r'(\d+(?:\.\d+)?)\s*(?:cr|crore|crores)', re.I)


def _detect_currency(text):
    t = text.lower()
    # Longest sign first so "us$" wins over "$", "s$" over "$".
    for sign, code in sorted(CURRENCY_SIGNS, key=lambda x: -len(x[0])):
        if sign in t:
            return code
    return None


def _bare_numbers(text):
    """
    Plain numbers for lakh/crore-denominated text, where the figures are small
    ("12-20 LPA") and must not be k/m-expanded. Strips the unit words first so
    the 'l' in 'lakh' is not mistaken for a magnitude suffix.
    """
    cleaned = re.sub(r'(?:lpa|lakhs?|lacs?|crores?|cr\b|l\b)', ' ', text, flags=re.I)
    out = []
    for m in re.finditer(r'(\d+(?:\.\d+)?)', cleaned.replace(",", "")):
        try:
            v = float(m.group(1))
        except ValueError:
            continue
        if 0 < v < 10_000:          # a plausible lakh/crore figure
            out.append(v)
    return out


def _numbers(text):
    """
    Pull salary-ish numbers out, handling 1,200,000 / 12,00,000 / 120k / 120.5.
    Returns floats with k/m suffixes already expanded.
    """
    out = []
    # Number, optional grouping (western or Indian), optional k/m suffix.
    for m in re.finditer(r'(\d[\d,]*(?:\.\d+)?)\s*([kKmM])?', text):
        raw, suf = m.group(1), m.group(2)
        raw = raw.rstrip(",")
        if not raw or not raw[0].isdigit():
            continue
        try:
            val = float(raw.replace(",", ""))
        except ValueError:
            continue
        if suf and suf.lower() == "k":
            val *= 1_000
        elif suf and suf.lower() == "m":
            val *= 1_000_000
        if val > 0:
            out.append(val)
    return out


def to_lpa(salary_text, fx=None, assume_currency=None):
    """
    Parse a salary string to LPA.

    Returns {lpa, low_lpa, high_lpa, currency, confidence, raw}.
    lpa is None when nothing usable was found.
    """
    fx = fx or DEFAULT_FX_TO_INR
    raw = str(salary_text or "").strip()
    if raw.lower() in NOT_LISTED:
        return {"lpa": None, "low_lpa": None, "high_lpa": None,
                "currency": None, "confidence": "none", "raw": raw}

    text = raw.replace("–", "-").replace("—", "-")

    # --- Indian notations first: these are unambiguous ---
    # In a range the unit is usually written once ("12-20 LPA"), so once we
    # know the text is denominated in lakh/crore, every bare number in it is
    # in that unit — not just the one the regex touched.
    cr = _CRORE_RE.findall(text)
    if cr:
        vals = sorted(float(v) * 100 for v in _bare_numbers(text))  # 1cr = 100L
        return _pack(vals or [float(c) * 100 for c in cr], "INR", "high", raw)

    if _LPA_RE.search(text):
        vals = sorted(_bare_numbers(text))
        return _pack(vals, "INR", "high", raw)

    # --- Everything else: detect currency, read numbers ---
    cur = _detect_currency(text) or assume_currency
    nums = _numbers(text)
    if not nums:
        return {"lpa": None, "low_lpa": None, "high_lpa": None,
                "currency": cur, "confidence": "none", "raw": raw}

    # Drop obvious non-salary noise (hourly rates, stray years, percentages).
    nums = [n for n in nums if n >= 1000]
    if not nums:
        return {"lpa": None, "low_lpa": None, "high_lpa": None,
                "currency": cur, "confidence": "none", "raw": raw}

    if cur is None:
        # No symbol. A bare 1,500,000 in an Indian context is INR;
        # a bare 120,000 is almost certainly not. Guess by magnitude.
        cur = "INR" if max(nums) >= 300_000 else "USD"
        conf = "low"
    else:
        conf = "high" if len(nums) >= 1 else "low"

    rate = fx.get(cur, fx.get("USD", 89.0))
    vals = sorted(n * rate / 100_000 for n in nums)    # -> lakhs
    return _pack(vals, cur, conf, raw)


def _pack(vals, currency, confidence, raw):
    lo, hi = vals[0], vals[-1]
    return {
        "lpa": round((lo + hi) / 2, 2),
        "low_lpa": round(lo, 2),
        "high_lpa": round(hi, 2),
        "currency": currency,
        "confidence": confidence,
        "raw": raw,
    }


def meets_floor(salary_text, cfg, fx=None):
    """
    Does this clear the configured floor?

    Returns (passes: bool, parsed: dict). Unlisted salary passes when
    salary.accept_unlisted is true — most good remote roles hide the number,
    and dropping them would gut the pipeline.
    """
    fx = fx or cfg.get("fx_to_inr") or DEFAULT_FX_TO_INR
    floor = float(cfg.get("min_lpa", 0))
    parsed = to_lpa(salary_text, fx)

    if parsed["lpa"] is None:
        return bool(cfg.get("accept_unlisted", True)), parsed
    # Compare against the TOP of the range: a "10-20 LPA" band is still
    # worth a conversation when your floor is 15.
    return parsed["high_lpa"] >= floor, parsed


def from_minmax(smin, smax, currency, fx=None):
    """For APIs that give numeric min/max directly (gethired, Adzuna)."""
    fx = fx or DEFAULT_FX_TO_INR
    vals = [float(v) for v in (smin, smax) if v not in (None, "", 0)]
    if not vals:
        return {"lpa": None, "low_lpa": None, "high_lpa": None,
                "currency": currency, "confidence": "none", "raw": ""}
    rate = fx.get((currency or "INR").upper(), 1.0)
    conv = sorted(v * rate / 100_000 for v in vals)
    return _pack(conv, currency, "high", f"{smin}-{smax} {currency}")


if __name__ == "__main__":
    import sys
    # Windows consoles default to cp1252 and choke on the rupee sign.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    cases = [
        "$120k-$150k USD", "$100K+ USD", "15 LPA", "₹18,00,000", "12-20 LPA",
        "€80,000 - €100,000", "£70k", "90000.0-120000.0", "1.2 crore",
        "Not listed", "SGD 120,000", "competitive", "$53,000", "8 LPA",
    ]
    cfg = {"min_lpa": 15, "accept_unlisted": True}
    print(f"{'Raw':<26}{'Cur':<6}{'LPA':<10}{'Range':<18}{'>=15 LPA?'}")
    print("-" * 74)
    for c in cases:
        ok, p = meets_floor(c, cfg)
        lpa = "—" if p["lpa"] is None else f"{p['lpa']}"
        rng = "—" if p["lpa"] is None else f"{p['low_lpa']}-{p['high_lpa']}"
        print(f"{c:<26}{str(p['currency'] or '—'):<6}{lpa:<10}{rng:<18}{'PASS' if ok else 'FAIL'}")
