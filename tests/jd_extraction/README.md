# JD extraction evals

Ground truth for `src/jdextract.py`, built from nineteen postings read by hand on
2026-11-08/09 and written up in `applications/`.

```
expected/<case>.json    the fields, hand-read from the real posting
```

Run: `python src/jdextract.py --eval`

## Two honest weaknesses in this eval

**Only 6 of 18 cases have source text.** The other twelve have ground truth but
no saved posting, so they cannot be scored. Capturing a `jd.txt` for each is the
cheapest improvement available.

**The 6 that do have text are paraphrases, not the real postings.** They were
typed from the originals while writing the application folders, which means the
eval is partly testing against a summary. Streamhub's lost "Bengaluru, IN"
entirely, which is why its `india_status` passed for the wrong reason. **Replace
them with the real page text before trusting any number here above 90%.**

## A finding about ground truth itself

Three seniority labels were originally wrong - Streamhub, MarcoPolo and Synthio
were labelled `mid`/`senior` when **none of those postings contains a seniority
word at all.** The labels recorded a conclusion rather than the text, and the
extractor returning `None` was the more correct answer.

The rule, learned the hard way: **expected values must be what the posting says,
not what the reader concluded.** A field the posting leaves unstated is `null`,
and an eval that punishes `null` teaches the extractor to guess - which is the
failure this whole layer exists to prevent.

Marrina is the counter-example worth keeping: its posting states "Minimum
Experience: Mid-level", so `mid` is correct there and was restored.
