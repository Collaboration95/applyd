"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { spawnSync } = require("node:child_process");
const { test } = require("node:test");
const { extractListing, ListingError } = require("../scripts/extract_linkedin.js");
const snippet = fs.readFileSync(path.join(__dirname, "../scripts/extract_linkedin.js"), "utf8");

// Small HTML/DOM adapter, intentionally limited to the selectors the reader uses.
// It parses actual fixture markup; no field values or selector results are stubbed.
const decode = value => value.replace(/&(?:amp|lt|gt|quot|apos|#39|nbsp);/g, entity => ({
  "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&apos;": "'", "&#39;": "'", "&nbsp;": " "
})[entity]);
const blocks = new Set(["div", "p", "h1", "h3", "ul", "li", "section", "main", "aside"]);

class Element {
  constructor(tag, attrs, parent, doc) {
    this.tagName = tag;
    this.attrs = attrs;
    this.parentElement = parent;
    this.ownerDocument = doc;
    this.children = [];
    this.nodeType = 1;
  }
  getAttribute(key) { return this.attrs[key] ?? null; }
  get hidden() { return Object.hasOwn(this.attrs, "hidden"); }
  get innerText() {
    const render = node => {
      if (typeof node === "string") return node.replace(/\s+/g, " ");
      if (node.hidden || /display\s*:\s*none|visibility\s*:\s*hidden/.test(node.attrs.style || "")) return "";
      const content = node.children.map(render).join("");
      return node.tagName === "br" ? "\n" : blocks.has(node.tagName) ? `\n${content}\n` : content;
    };
    return render(this).replace(/ *\n */g, "\n").replace(/\n{2,}/g, "\n").trim();
  }
  getClientRects() { return [{}]; }
  querySelectorAll(selector) {
    const parts = selector.split(/\s+/);
    const matches = (node, simple) => {
      const tag = simple.match(/^[a-z][\w-]*/i)?.[0];
      if (tag && node.tagName !== tag) return false;
      for (const match of simple.matchAll(/([.#])([\w-]+)/g)) {
        if (match[1] === "#" && node.attrs.id !== match[2]) return false;
        if (match[1] === "." && !(node.attrs.class || "").split(/\s+/).includes(match[2])) return false;
      }
      for (const match of simple.matchAll(/\[([\w-]+)(\*?=)['"]([^'"]*)['"]\]/g)) {
        const value = node.getAttribute(match[1]);
        if (value === null || (match[2] === "=" ? value !== match[3] : !value.includes(match[3]))) return false;
      }
      return true;
    };
    const chain = node => {
      if (!matches(node, parts.at(-1))) return false;
      let ancestor = node.parentElement;
      for (let i = parts.length - 2; i >= 0; i--) {
        while (ancestor && !matches(ancestor, parts[i])) ancestor = ancestor.parentElement;
        if (!ancestor) return false;
        ancestor = ancestor.parentElement;
      }
      return true;
    };
    const found = [];
    const walk = node => {
      for (const child of node.children) if (typeof child !== "string") {
        if (chain(child)) found.push(child);
        walk(child);
      }
    };
    walk(this);
    return found;
  }
}

function parseHTML(html) {
  const doc = new Element("document", {}, null, null);
  doc.ownerDocument = doc;
  doc.nodeType = 9;
  doc.defaultView = { getComputedStyle(node) {
    const style = node.getAttribute("style") || "";
    return { display: /display\s*:\s*none/.test(style) ? "none" : "block",
      visibility: /visibility\s*:\s*hidden/.test(style) ? "hidden" : "visible" };
  } };
  const stack = [doc];
  for (const match of html.matchAll(/<!--[\s\S]*?-->|<![^>]*>|<\/[\w-]+\s*>|<[\w-]+\b[^>]*>|[^<]+/g)) {
    const token = match[0];
    if (token.startsWith("<!")) continue;
    if (token.startsWith("</")) { stack.pop(); continue; }
    if (!token.startsWith("<")) { stack.at(-1).children.push(decode(token)); continue; }
    const tag = token.match(/^<([\w-]+)/)[1];
    const attrs = {};
    const rest = token.slice(tag.length + 1, -1);
    for (const attr of rest.matchAll(/([\w-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g)) {
      attrs[attr[1]] = decode(attr[2] ?? attr[3] ?? attr[4] ?? "");
    }
    const node = new Element(tag, attrs, stack.at(-1), doc);
    stack.at(-1).children.push(node);
    if (tag === "html") doc.location = { href: attrs["data-page-url"] };
    if (!["link", "meta", "br", "hr", "img", "input"].includes(tag)) stack.push(node);
  }
  return doc;
}

function fixture(name, transform = value => value) {
  return parseHTML(transform(fs.readFileSync(path.join(__dirname, `fixtures/linkedin-${name}.html`), "utf8")));
}
function fails(doc, field) {
  assert.throws(() => extractListing(doc), error => error instanceof ListingError && error.field === field && error.exit_code === 2);
}

test("three real synthetic fixtures give stable JSON and the browser IIFE matches CommonJS", () => {
  for (const name of ["easy-card", "no-card", "external"]) {
    const doc = fixture(name);
    const before = JSON.stringify(doc.children, (key, value) => ["parentElement", "ownerDocument"].includes(key) ? undefined : value);
    const result = extractListing(doc);
    assert.deepEqual(extractListing(fixture(name)), result);
    const forbidden = () => { throw new Error("network or mutation attempted"); };
    const browserJSON = vm.runInNewContext(snippet, { document: doc, URL, fetch: forbidden, XMLHttpRequest: forbidden,
      location: Object.freeze({ assign: forbidden, replace: forbidden }), setTimeout: forbidden });
    assert.deepEqual(JSON.parse(browserJSON), result);
    const validated = spawnSync("python3", [path.join(__dirname, "../scripts/listing_input.py")], { input: browserJSON, encoding: "utf8" });
    assert.equal(validated.status, 0, validated.stderr);
    const normalized = JSON.parse(validated.stdout);
    assert.equal(normalized.role, result.title);
    assert.equal(normalized.application_url, result.apply_url);
    assert.equal(normalized.listing_url, result.canonical_url);
    assert.equal(normalized.source_url, result.source_url);
    assert.equal(normalized.canonical_listing_url, result.canonical_url);
    assert.equal(normalized.job_description, result.job_description);
    assert.deepEqual(normalized.hiring_team, result.hiring_team);
    assert.equal(JSON.stringify(doc.children, (key, value) => ["parentElement", "ownerDocument"].includes(key) ? undefined : value), before);
    assert.equal(result.source, "linkedin_job_page");
  }
});

test("Easy Apply includes exact current facts, multiline description and visible hiring card", () => {
  assert.deepEqual(extractListing(fixture("easy-card")), {
    platform: "linkedin", source: "linkedin_job_page", platform_job_id: "900001",
    title: "Synthetic Backend Engineer", company: "Example Cedar Labs", location: "Singapore",
    workplace_type: "Hybrid", employment_type: "Full-time",
    job_description: "Build synthetic APIs & services.\nRequirements:\nPython experience.\nMaintain clear documentation.",
    easy_apply: true, apply_url: null, posted_age: "2 days ago",
    hiring_team: { name: "Example Hiring Person", headline: "Recruiter at Example Cedar Labs", profile_url: "https://www.linkedin.com/in/synthetic-hiring-person/" },
    canonical_url: "https://www.linkedin.com/jobs/view/900001",
    source_url: "https://www.linkedin.com/jobs/view/900001?trk=synthetic",
    destination_url: "https://www.linkedin.com/jobs/view/900001"
  });
});

test("existing hiring_team shape becomes contacts at capture time without extra reads", () => {
  for (const name of ["easy-card", "no-card", "external"]) {
    const result = extractListing(fixture(name));
    const normalized = spawnSync("python3", ["-B", "-c", [
      "import json, sys",
      "sys.path.insert(0, sys.argv[1])",
      "from listing_input import validate_listing, normalize_listing_contacts",
      'print(json.dumps(normalize_listing_contacts(validate_listing(json.load(sys.stdin)), "2026-10-20T12:30:00+08:00")))'
    ].join("\n"), path.join(__dirname, "../scripts")], { input: JSON.stringify(result), encoding: "utf8" });
    assert.equal(normalized.status, 0, normalized.stderr);
    const saved = JSON.parse(normalized.stdout);
    assert.deepEqual(saved.hiring_team, result.hiring_team);
    assert.equal(saved.contacts.length, result.hiring_team ? 1 : 0);
    if (result.hiring_team) {
      const contact = saved.contacts[0];
      for (const field of ["name", "headline", "profile_url"]) assert.equal(contact[field], result.hiring_team[field]);
      assert.equal(contact.source, "linkedin_hiring_team");
      assert.equal(contact.captured_at, "2026-10-20T12:30:00+08:00");
      assert.equal(contact.relationship, "hiring_team");
      assert.equal(contact.email, null);
      assert.equal(contact.verified, false);
    }
  }
});

test("search details fallback ignores unrelated result and hidden poster/stale panel", () => {
  const result = extractListing(fixture("no-card"));
  assert.equal(result.platform_job_id, "900002");
  assert.equal(result.title, "Synthetic Data Intern");
  assert.equal(result.company, "Example Birch Analytics");
  assert.equal(result.workplace_type, "On-site");
  assert.equal(result.employment_type, "Internship");
  assert.equal(result.job_description, "Explore fictional datasets.\nWrite reproducible reports.");
  assert.equal(result.hiring_team, null);
});

test("external guest layout preserves the actual destination and unknown workplace", () => {
  const result = extractListing(fixture("external"));
  assert.equal(result.platform_job_id, "900003");
  assert.equal(result.easy_apply, false);
  assert.equal(result.apply_url, "https://careers.example.org/jobs/elm-003?source=linkedin&campaign=synthetic");
  assert.equal(result.destination_url, result.apply_url);
  assert.equal(result.workplace_type, null);
  assert.equal(result.employment_type, "Contract");
  assert.equal(result.hiring_team.headline, "Talent partner");
});

test("each missing required DOM field raises a typed field-specific error", () => {
  for (const [marker, field] of [["job-details-jobs-unified-top-card__job-title", "title"],
    ["job-details-jobs-unified-top-card__company-name", "company"], ["tvm__text--low-emphasis", "location"],
    ['id="job-details"', "job_description"], ["jobs-apply-button", "easy_apply"]]) {
    fails(fixture("easy-card", html => html.replaceAll(marker, "removed")), field);
  }
  fails(fixture("easy-card", html => html.replaceAll("900001", "not-an-id")), "platform_job_id");
});

test("mismatched current page and selected panel block stale capture", () => {
  fails(fixture("easy-card", html => html.replace('data-job-id="900001"', 'data-job-id="900099"')), "platform_job_id");
});

test("an unknown apply label is not guessed; invisible facts stay null", () => {
  fails(fixture("easy-card", html => html.replaceAll("Easy Apply", "Saved")), "easy_apply");
  const result = extractListing(fixture("easy-card", html => html.replace('class="job-details-people-who-can-help__section hirer-card"', 'class="job-details-people-who-can-help__section hirer-card" aria-hidden="true"')));
  assert.equal(result.hiring_team, null);
});

test("a button without an exposed external href keeps its destination unknown", () => {
  const result = extractListing(fixture("easy-card", html => html.replaceAll("Easy Apply", "Apply")));
  assert.equal(result.easy_apply, false);
  assert.equal(result.apply_url, null);
  assert.equal(result.destination_url, null);
});

test("unsafe apply links are typed errors without following the link", () => {
  fails(fixture("external", html => html.replace("https://careers.example.org/jobs/elm-003?source=linkedin&amp;campaign=synthetic", "javascript:alert(1)")), "apply_url");
});

test("a section of unrelated people is not a hiring-team card", () => {
  const doc = fixture("easy-card", html => html.replace('class="job-details-people-who-can-help__section hirer-card"', 'class="job-details-people-who-can-help__section"').replace("hirer-card__hirer-information", "unrelated-alumni"));
  assert.equal(extractListing(doc).hiring_team, null);
});

test("source URL without a job ID remains valid when the selected DOM supplies identity", () => {
  const doc = fixture("easy-card", html => html.replace("https://www.linkedin.com/jobs/view/900001?trk=synthetic", "https://www.linkedin.com/jobs/collections/recommended/"));
  const result = extractListing(doc);
  assert.equal(result.platform_job_id, "900001");
  const validated = spawnSync("python3", [path.join(__dirname, "../scripts/listing_input.py")], { input: JSON.stringify(result), encoding: "utf8" });
  assert.equal(validated.status, 0, validated.stderr);
});

test("conflicting or duplicate URL job IDs block capture", () => {
  for (const query of ["?currentJobId=900002", "?currentJobId=900001&currentJobId=900001", "?currentJobId=invalid"]) {
    fails(fixture("easy-card", html => html.replace("?trk=synthetic", query)), "platform_job_id");
  }
});
