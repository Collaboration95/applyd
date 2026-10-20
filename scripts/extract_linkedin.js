/* Paste this entire expression into the current LinkedIn job page's evaluator.
 * The browser result is a JSON string. CommonJS exports the same pure reader.
 */
(function () {
  "use strict";

  const SELECTORS = Object.freeze({
    root: [".jobs-search__job-details--container", ".jobs-details__main-content", ".job-view-layout", "main"],
    title: [".job-details-jobs-unified-top-card__job-title h1", ".jobs-unified-top-card__job-title", ".top-card-layout__title"],
    titleLink: [".job-details-jobs-unified-top-card__job-title a", "a.jobs-unified-top-card__job-title"],
    company: [".job-details-jobs-unified-top-card__company-name", ".jobs-unified-top-card__company-name", ".topcard__org-name-link"],
    location: [".job-details-jobs-unified-top-card__primary-description-container .tvm__text--low-emphasis", ".jobs-unified-top-card__bullet", ".topcard__flavor--bullet"],
    insights: [".job-details-jobs-unified-top-card__job-insight span", ".jobs-unified-top-card__job-insight span", ".job-details-jobs-unified-top-card__job-insight", ".jobs-unified-top-card__job-insight"],
    criteria: [".description__job-criteria-item"],
    criteriaLabel: [".description__job-criteria-subheader"],
    criteriaValue: [".description__job-criteria-text"],
    description: ["#job-details", ".jobs-description__content .jobs-box__html-content", ".show-more-less-html__markup"],
    apply: [".jobs-apply-button", ".jobs-s-apply button", ".jobs-s-apply a", "a.apply-button"],
    postedAge: [".job-details-jobs-unified-top-card__primary-description-container .tvm__text--positive", ".jobs-unified-top-card__posted-date", ".posted-time-ago__text"],
    hiringTeam: [".hirer-card", ".jobs-poster", ".message-the-recruiter", ".job-details-people-who-can-help__section .hirer-card__hirer-information"],
    posterName: [".hirer-card__hirer-information h3", ".jobs-poster__name", ".base-main-card__title"],
    posterHeadline: [".hirer-card__hirer-information .t-14", ".jobs-poster__headline", ".base-main-card__subtitle"],
    posterProfile: ["a[href*='/in/']"],
    canonical: ["link[rel='canonical']"]
  });

  class ListingError extends Error {
    constructor(field, reason = "missing required field") {
      super(`${field}: ${reason}`);
      this.name = "ListingError";
      this.field = field;
      this.exit_code = 2;
    }
  }

  function visible(node) {
    for (let current = node; current && current.nodeType === 1; current = current.parentElement) {
      if (current.hidden || current.getAttribute("aria-hidden") === "true") return false;
      const view = current.ownerDocument.defaultView;
      const style = view && view.getComputedStyle(current);
      if (style && (style.display === "none" || style.visibility === "hidden" || style.visibility === "collapse")) return false;
    }
    return !node.getClientRects || node.getClientRects().length > 0;
  }

  function all(scope, field, includeHidden = false) {
    for (const selector of SELECTORS[field]) {
      const matches = Array.from(scope.querySelectorAll(selector)).filter(node => includeHidden || visible(node));
      if (matches.length) return matches;
    }
    return [];
  }

  const text = node => node ? node.innerText.replace(/\r\n?/g, "\n").trim() : null;
  const oneLine = node => text(node)?.replace(/\s+/g, " ") || null;

  function required(value, field) {
    if (value === null || value === undefined || value === "") throw new ListingError(field);
    return value;
  }

  function httpURL(value, base, field) {
    let url;
    try { url = new URL(value, base); } catch (_) { throw new ListingError(field, "invalid URL"); }
    if (!["https:", "http:"].includes(url.protocol) || url.username || url.password) throw new ListingError(field, "invalid HTTP(S) URL");
    return url;
  }

  function linkedInID(url) {
    if (!/(^|\.)linkedin\.com$/i.test(url.hostname)) return null;
    const path = url.pathname.match(/^\/jobs\/view\/(?:[^/]*-)?(\d+)\/?$/);
    const query = url.searchParams.getAll("currentJobId");
    if (query.length > 1 || (query.length && !/^\d+$/.test(query[0]))) throw new ListingError("platform_job_id", "ambiguous or invalid URL job ID");
    if (path && query.length && path[1] !== query[0]) throw new ListingError("platform_job_id", "URL job IDs disagree");
    return path ? path[1] : (query[0] || null);
  }

  function detectAccountSafety(pageText, pageURL = "") {
    const text = String(pageText || "").replace(/\s+/g, " ").toLowerCase();
    const patterns = [
      ["captcha", /\b(?:captcha|recaptcha|hcaptcha)\b/],
      ["challenge", /verify (?:that )?you(?:'re| are) (?:a )?human|verify your identity|security verification|checking your browser/],
      ["unusual_activity", /unusual activity|suspicious activity|automated (?:activity|behavior|behaviour)/],
      ["restriction", /account (?:has been |is |was |temporarily )*(?:restricted|suspended|locked|disabled)/],
      ["rate_limit", /too many requests|rate[ -]?limit(?:ed|ing| exceeded)?|try again later/],
      ["sign_in", /^(?:sign in|log in|login)\b|(?:sign in|log in) to (?:continue|view|access)|session (?:has )?expired/]
    ];
    let path = "";
    try { path = new URL(pageURL).pathname; } catch {}
    if (/\/(?:checkpoint|challenge|security-verification)(?:\/|$)/i.test(path)) return "challenge";
    if (/\/(?:login|signin|authwall)(?:\/|$)/i.test(path)) return "sign_in";
    return patterns.find(([, pattern]) => pattern.test(text))?.[0] || null;
  }

  function extractListing(doc) {
    const detection = detectAccountSafety(doc.body?.innerText || "", doc.location?.href || doc.URL || "");
    if (detection) {
      const error = new ListingError("account_safety", detection);
      error.stop_reason = "account_safety";
      error.kind = detection;
      throw error;
    }
    const root = all(doc, "root")[0];
    if (!root) throw new ListingError("title", "job details panel not found");
    const sourceURL = httpURL(required(doc.location?.href, "source_url"), undefined, "source_url");
    if (!/(^|\.)linkedin\.com$/i.test(sourceURL.hostname)) throw new ListingError("source_url", "expected a LinkedIn page");
    const canonicalNode = all(doc, "canonical", true)[0];
    const titleLink = all(root, "titleLink")[0];
    const ids = [linkedInID(sourceURL)];
    // Only the selected details panel is consulted, never search-result cards.
    const panelID = root.getAttribute("data-job-id");
    if (panelID && !/^\d+$/.test(panelID)) throw new ListingError("platform_job_id", "invalid panel ID");
    ids.push(panelID);
    for (const [node, field] of [[titleLink, "title"], [canonicalNode, "canonical_url"]]) {
      if (node?.getAttribute("href")) ids.push(linkedInID(httpURL(node.getAttribute("href"), sourceURL.href, field)));
    }
    const observed = ids.filter(Boolean);
    const id = required(observed[0], "platform_job_id");
    if (observed.some(value => value !== id)) throw new ListingError("platform_job_id", "page and details panel disagree");
    const canonicalURL = `https://www.linkedin.com/jobs/view/${id}`;
    const title = required(oneLine(all(root, "title")[0]), "title");
    const company = required(oneLine(all(root, "company")[0]), "company");
    const location = required(oneLine(all(root, "location")[0]), "location");
    const description = required(text(all(root, "description")[0]), "job_description");

    const insights = all(root, "insights").map(oneLine).filter(Boolean).flatMap(value => value.split(/\s*[·•]\s*|\n/));
    const workplace = insights.find(value => /^(On-site|Hybrid|Remote)$/i.test(value)) || null;
    let employment = insights.find(value => /^(Full-time|Part-time|Contract|Temporary|Volunteer|Internship|Other)$/i.test(value)) || null;
    for (const criterion of all(root, "criteria")) {
      if (/^Employment type$/i.test(oneLine(all(criterion, "criteriaLabel")[0]) || "")) {
        employment = oneLine(all(criterion, "criteriaValue")[0]);
      }
    }
    const buttons = all(root, "apply");
    const apply = buttons.find(node => /^(Easy Apply|Apply)(?:\b|$)/i.test(oneLine(node) || node.getAttribute("aria-label") || ""));
    if (!apply) throw new ListingError("easy_apply", "recognized apply control not found");
    const easyApply = /^Easy Apply\b/i.test(oneLine(apply) || apply.getAttribute("aria-label") || "");
    const href = apply.getAttribute("href");
    const applyURL = !easyApply && href ? httpURL(href, sourceURL.href, "apply_url").href : null;
    const poster = all(root, "hiringTeam")[0];
    const profile = poster && all(poster, "posterProfile")[0];
    return {
      platform: "linkedin", source: "linkedin_job_page", platform_job_id: id,
      title, company, location, workplace_type: workplace, employment_type: employment,
      job_description: description, easy_apply: easyApply, apply_url: applyURL,
      posted_age: oneLine(all(root, "postedAge")[0]),
      hiring_team: poster ? {
        name: oneLine(all(poster, "posterName")[0]),
        headline: oneLine(all(poster, "posterHeadline")[0]),
        profile_url: profile ? httpURL(profile.getAttribute("href"), sourceURL.href, "hiring_team.profile_url").href : null
      } : null,
      canonical_url: canonicalURL, source_url: sourceURL.href,
      destination_url: easyApply ? canonicalURL : applyURL
    };
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { extractListing, ListingError, SELECTORS, detectAccountSafety };
    return;
  }
  return JSON.stringify(extractListing(document));
})();
