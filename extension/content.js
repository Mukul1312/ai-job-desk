/**
 * content.js — watch an application page and report what it is.
 *
 * This script observes. It does not fill a field, click a button, or submit a
 * form, and it never sends anything off the machine. Everything it notices goes
 * into chrome.storage.local, where the popup can export it as JSON.
 *
 * WHAT IT DELIBERATELY DOES NOT RECORD
 * Field *values* are never read. We store a field's name, id, label and type so
 * we know what an Ashby form asks for versus a Greenhouse one. What you typed
 * into it is not our business and is not needed to answer the question.
 *
 * THE QUESTION
 * resolve.py predicts where each application goes, from the listing alone.
 * This records where applications really went. src/compare_destinations.py puts
 * the two side by side. Until that comparison exists, the pipeline's picture of
 * which systems matter is a guess, however well sourced.
 */

(() => {
  "use strict";

  // Reject obviously irrelevant pages before doing any real work. The content
  // script runs everywhere, so this has to stay cheap.
  const SKIP_PROTOCOLS = ["chrome:", "chrome-extension:", "about:", "devtools:"];
  if (SKIP_PROTOCOLS.some((p) => location.protocol.startsWith(p))) return;

  const FIELD_SELECTOR = "input, select, textarea";
  const IGNORED_INPUT_TYPES = new Set([
    "hidden", "submit", "button", "image", "reset", "search",
  ]);

  // Words that mark a form as a job application rather than a newsletter box.
  const APPLICATION_HINTS = [
    "resume", "cv", "cover letter", "coverletter", "first name", "last name",
    "linkedin", "portfolio", "work authorization", "work authorisation",
    "visa", "sponsorship", "notice period", "current ctc", "expected ctc",
    "salary expectation", "years of experience", "github", "why do you",
    "apply", "application",
  ];

  const SUBMIT_TEXT = /\b(submit|apply|send application|send|finish)\b/i;

  let recorded = null;
  let lastUrl = location.href;

  // ─── helpers ──────────────────────────────────────────────────────────────

  const text = (s) => (s || "").replace(/\s+/g, " ").trim().slice(0, 120);

  /** The visible label for a field, without ever touching its value. */
  function labelFor(el) {
    if (el.id) {
      const byFor = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (byFor) return text(byFor.textContent);
    }
    const wrapping = el.closest("label");
    if (wrapping) return text(wrapping.textContent);
    const aria = el.getAttribute("aria-label");
    if (aria) return text(aria);
    const labelledBy = el.getAttribute("aria-labelledby");
    if (labelledBy) {
      const node = document.getElementById(labelledBy);
      if (node) return text(node.textContent);
    }
    if (el.placeholder) return text(el.placeholder);
    return "";
  }

  /** Field metadata only. Never el.value. */
  function describeField(el) {
    const type = (el.type || el.tagName).toLowerCase();
    return {
      tag: el.tagName.toLowerCase(),
      type,
      name: text(el.name),
      id: text(el.id),
      label: labelFor(el),
      required: !!(el.required || el.getAttribute("aria-required") === "true"),
    };
  }

  function collectFields() {
    const out = [];
    for (const el of document.querySelectorAll(FIELD_SELECTOR)) {
      const type = (el.type || "").toLowerCase();
      if (IGNORED_INPUT_TYPES.has(type)) continue;
      if (el.offsetParent === null && type !== "file") continue; // not visible
      out.push(describeField(el));
      if (out.length >= 80) break; // a form this long is already unusual
    }
    return out;
  }

  /**
   * Is this an application page?
   *
   * Two independent ways to qualify: the host is a known ATS, or the page has
   * the shape of an application form (a file upload plus contact fields, or
   * enough application-specific wording). The second is what catches a company
   * running its own form on its own domain.
   */
  function shouldRecord(fields) {
    const ats = classifyUrl(location.href);

    if (ats !== "unknown" && ats !== "direct" && ats !== "email") {
      // A known ATS host, but its careers *index* is not an application.
      if (fields.length >= 2) return { record: true, ats, why: "ats-host" };
      return { record: false, ats, why: "ats-host-no-form" };
    }

    if (isAggregator(location.href)) {
      return { record: false, ats, why: "aggregator-listing" };
    }

    const hasFile = fields.some((f) => f.type === "file");
    const haystack = fields
      .map((f) => `${f.name} ${f.id} ${f.label}`.toLowerCase())
      .join(" ");
    const hints = APPLICATION_HINTS.filter((h) => haystack.includes(h)).length;

    if (hasFile && hints >= 2) {
      return { record: true, ats: ats === "unknown" ? "direct" : ats,
               why: "form-shape" };
    }
    if (hints >= 4 && fields.length >= 4) {
      return { record: true, ats: ats === "unknown" ? "direct" : ats,
               why: "form-wording" };
    }
    return { record: false, ats, why: "no-application-form" };
  }

  /** Guess the employer, for human-readable records only. */
  function guessCompany() {
    const og = document.querySelector('meta[property="og:site_name"]');
    if (og && og.content) return text(og.content);
    const title = text(document.title);
    const parts = title.split(/[|–—\-·]/).map((p) => p.trim());
    if (parts.length > 1) return text(parts[parts.length - 1]);
    try {
      return new URL(location.href).hostname.replace(/^www\./, "");
    } catch (e) {
      return "";
    }
  }

  /**
   * Where did we come from? If the referrer is a job board, that is the listing
   * resolve.py was predicting from, which is what makes the two comparable.
   */
  function listingUrl() {
    const ref = document.referrer || "";
    if (!ref) return "";
    return isAggregator(ref) || classifyUrl(ref) === "direct" ? ref : "";
  }

  function send(message) {
    try {
      chrome.runtime.sendMessage(message, () => void chrome.runtime.lastError);
    } catch (e) {
      /* extension reloaded mid-page; nothing useful to do */
    }
  }

  // ─── recording ────────────────────────────────────────────────────────────

  function evaluate() {
    const fields = collectFields();
    const verdict = shouldRecord(fields);
    if (!verdict.record) return;

    const record = {
      applyUrl: location.href.split("#")[0],
      host: location.hostname,
      ats: verdict.ats,
      detectedBy: verdict.why,
      listingUrl: listingUrl(),
      pageTitle: text(document.title),
      company: guessCompany(),
      fieldCount: fields.length,
      hasFileUpload: fields.some((f) => f.type === "file"),
      fields,
      submitted: false,
      seenAt: new Date().toISOString(),
    };

    recorded = record;
    send({ kind: "seen", record });
    attachSubmitWatchers();
  }

  let watchersAttached = false;

  function attachSubmitWatchers() {
    if (watchersAttached) return;
    watchersAttached = true;

    // Capture phase: we want to know a submit happened even if the page's own
    // handler stops propagation afterwards.
    document.addEventListener(
      "submit",
      () => markSubmitted("form-submit"),
      true
    );

    document.addEventListener(
      "click",
      (ev) => {
        const el = ev.target && ev.target.closest
          ? ev.target.closest('button, input[type="submit"], [role="button"]')
          : null;
        if (!el) return;

        // A submit *button* carries its caption in .value, which is why that
        // one case is read. It is a button label, never something typed:
        // guarded on type so the "no field values" promise stays checkable.
        const buttonCaption =
          el.tagName === "INPUT" && (el.type || "").toLowerCase() === "submit"
            ? el.value
            : "";

        const label = text(
          el.innerText || buttonCaption || el.getAttribute("aria-label") || ""
        );
        if (SUBMIT_TEXT.test(label)) markSubmitted(`click:${label.slice(0, 40)}`);
      },
      true
    );
  }

  function markSubmitted(how) {
    if (!recorded || recorded.submitted) return;
    recorded.submitted = true;
    recorded.submittedAt = new Date().toISOString();
    recorded.submittedVia = how;
    send({ kind: "submitted", record: recorded });
  }

  // ─── lifecycle ────────────────────────────────────────────────────────────

  // Most ATS pages are single-page apps: the form arrives after first paint and
  // the URL changes without a reload. Re-evaluate on both.
  const observer = new MutationObserver(() => {
    if (location.href !== lastUrl) {
      lastUrl = location.href;
      recorded = null;
      watchersAttached = false;
    }
    if (!recorded) debounceEvaluate();
  });

  let timer = null;
  function debounceEvaluate() {
    clearTimeout(timer);
    timer = setTimeout(evaluate, 600);
  }

  debounceEvaluate();
  observer.observe(document.documentElement, { childList: true, subtree: true });

  // Stop watching a page that has gone quiet; an application form that has not
  // appeared within a minute is not going to.
  setTimeout(() => observer.disconnect(), 60000);
})();
