"use strict";
const assert = require("node:assert/strict");
const { test } = require("node:test");
const { harness } = require("./control-plane.test.cjs");

const contact = {
  name: '<img src=x onerror="alert(1)">', headline: "Synthetic hiring lead",
  profile_url: "https://www.linkedin.com/in/synthetic-cedar/?trk=visible",
  email: "Cedar@example.org", source: "linkedin_hiring_team",
  captured_at: "2026-10-20T12:30:00+08:00", relationship: "hiring_team",
  verified: false, contact_id: "contact-synthetic"
};
const app = (id, contacts) => ({ id, company: "Synthetic Employer", title: "Synthetic Engineer",
  status: "discovered", contacts, submissions: [], events: [] });

test("details display exact contact facts as text and a safe profile link", () => {
  const h = harness({ applications: [app("synthetic-one", [contact])] }, new Map(), "#application-synthetic-one");
  const section = h.get("detail-contacts");
  assert.ok(section);
  assert.equal(h.get("details").open, true);
  for (const [key, value] of Object.entries(contact)) {
    if (key === "verified") continue;
    assert.ok(section.textContent.includes(value), `${key} missing`);
  }
  assert.ok(section.textContent.includes("Unverified"));
  assert.equal(section.querySelectorAll("img").length, 0);
  const links = section.querySelectorAll("a");
  assert.equal(links.length, 1);
  assert.equal(links[0].href, contact.profile_url);
  assert.equal(links[0].target, "_blank");
  assert.equal(links[0].rel, "noopener noreferrer");
});

test("imported contact profile URLs cannot create unsafe or local links", () => {
  for (const profile_url of ["javascript:alert(1)", "data:text/html,synthetic", "file:///synthetic/private",
    "../synthetic", "//example.org/person", "https:example.org/person", "https:///example.org/person", "https://" + "user:pass@example.org/person",
    "https://example.org:99999/person", "https://example.org/a\nb", "https://example.org\\person"]) {
    const h = harness({ applications: [app("synthetic-one", [{ ...contact, profile_url }])] }, new Map(), "#application-synthetic-one");
    const section = h.get("detail-contacts");
    assert.equal(section.querySelectorAll("a").length, 0, profile_url);
    assert.ok(section.textContent.includes(profile_url));
  }
});

test("details handle historical records, missing facts and clear the previous contacts", () => {
  const h = harness({ applications: [app("synthetic-one", [contact]), app("synthetic-two", undefined)] }, new Map(), "#application-synthetic-one");
  h.location.hash = "#application-synthetic-two";
  h.listeners.hashchange();
  const section = h.get("detail-contacts");
  assert.ok(section.textContent.includes("No saved contacts."));
  assert.ok(!section.textContent.includes(contact.name));
  assert.equal(h.document.querySelectorAll("#detail-contacts").length, 1);
  const partial = harness({ applications: [app("synthetic-three", [null, false, [], { source: "jd_text", email: "synthetic@example.org", verified: false }])] }, new Map(), "#application-synthetic-three");
  assert.ok(partial.get("detail-contacts").textContent.includes("Not recorded"));
  assert.ok(partial.get("detail-contacts").textContent.includes("synthetic@example.org"));
});
