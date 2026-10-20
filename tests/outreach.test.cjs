"use strict";
const assert = require("node:assert/strict");
const { test } = require("node:test");
const { harness } = require("./control-plane.test.cjs");

const event = (changes = {}) => ({
  follow_up_id: "synthetic-follow-up", channel: "linkedin", status: "note_created",
  reserved_at: "2026-10-20T10:00:00+08:00",
  text: 'Following up on Engineer. Built <img src=x onerror="alert(1)">. Thanks!',
  profile_url: "https://www.linkedin.com/in/synthetic-recruiter/?trk=saved",
  ...changes
});
function load(outreach, extra = {}) {
  return harness({ applications: [{ id: "synthetic-app", title: "Synthetic Engineer",
    company: "Synthetic Employer", status: "submitted", submissions: [{ at: "2026-10-05T10:00:00+08:00" }],
    events: [], outreach, ...extra }] }, new Map(), "#application-synthetic-app");
}

test("saved note renders literal text, copies exact text only on a click, and stays unsent", async () => {
  const note = event(), h = load([note]), section = h.get("detail-outreach");
  assert.ok(section.textContent.includes(note.text));
  assert.ok(section.textContent.includes("LinkedIn note saved · not sent"));
  assert.equal(section.querySelectorAll("img").length, 0);
  assert.deepEqual(h.copied, []);
  const link = section.querySelector("a");
  assert.equal(link.href, note.profile_url);
  assert.equal(link.target, "_blank");
  assert.equal(link.rel, "noopener noreferrer");
  assert.equal(link.listeners.click, undefined);
  const original = JSON.stringify(h.window.JobJournal.state.data.applications);
  await section.querySelector("button").click();
  assert.deepEqual(h.copied, [note.text]);
  assert.equal(section.querySelector("button").textContent, "Copied");
  assert.equal(JSON.stringify(h.window.JobJournal.state.data.applications), original);
  assert.equal(h.location.hash, "#application-synthetic-app");
});

test("note limits use UTF-16 units with exact boundary and no truncation", () => {
  for (const text of ["x".repeat(200), "🙂".repeat(100)]) {
    const h = load([event({ text })]), section = h.get("detail-outreach");
    assert.equal(section.querySelector(".outreach-note").textContent, text);
    assert.equal(section.querySelectorAll("button").length, 1);
  }
  for (const text of ["x".repeat(201), "🙂".repeat(101), "", null]) {
    const h = load([event({ text })]), section = h.get("detail-outreach");
    assert.equal(section.querySelectorAll("button").length, 0);
    assert.equal(section.querySelectorAll(".outreach-note").length, 0);
    assert.ok(section.textContent.includes("200 UTF-16 units"));
  }
});

test("imported outreach profile URLs filter unsafe schemes, hosts, credentials and nonprofiles", () => {
  for (const profile_url of ["javascript:alert(1)", "data:text/html,synthetic", "file:///synthetic/private",
    "../private", "//www.linkedin.com/in/example", "https:www.linkedin.com/in/example",
    "https:///www.linkedin.com/in/example", "https://www.linkedin.com.evil.invalid/in/example",
    "https://evil.invalid/in/example", "https://" + "user:pass@www.linkedin.com.test/in/example",
    "https://www.linkedin.com:99999/in/example", "https://www.linkedin.com/in/example\n",
    "https://www.linkedin.com\\@evil.invalid/in/example", "https://www.linkedin.com/jobs/view/123",
    "https://www.linkedin.com/in/", null]) {
    const h = load([event({ profile_url })]);
    assert.equal(h.get("detail-outreach").querySelectorAll("a").length, 0, String(profile_url));
    assert.deepEqual(h.copied, []);
  }
});

test("uncertain reservations and email drafts never expose send actions", () => {
  const h = load([event({ status: "draft_uncertain" }), event({ channel: "email", status: "draft_created",
    subject: "Synthetic email subject", text: "Synthetic email body" })]);
  const section = h.get("detail-outreach");
  assert.ok(section.textContent.includes("reconcile before retry"));
  assert.ok(section.textContent.includes("Email draft created · not sent"));
  assert.ok(section.textContent.includes("Synthetic email subject"));
  assert.equal(section.querySelectorAll("button").length, 0);
  assert.deepEqual(h.copied, []);
});

test("manual confirmation shows actual send and recording times without changing application runs", () => {
  const h = load([event({ manual_sends: [{ occurred_at: "2026-10-20T10:02:00+08:00",
    recorded_at: "2026-10-20T12:00:00+08:00" }] })]);
  assert.ok(h.get("detail-outreach").textContent.includes("Manual send confirmed:"));
  assert.ok(h.get("detail-outreach").textContent.includes("recorded"));
  const runs = h.window.JobRuns.groupRuns(h.window.JobJournal.state.data.applications);
  assert.equal(runs.length, 1);
  assert.equal(runs[0].entries[0].at, "2026-10-05T10:00:00+08:00");
});

test("legacy records, malformed entries, blockers and dialog reload clear outreach", () => {
  const h = load([event(), null, [], false], { outreach_blocker: { reason: "credentials_required" } });
  assert.ok(h.get("detail-outreach").textContent.includes("Blocked: credentials_required"));
  h.window.JobJournal.loadData({ applications: [{ id: "other", company: "Other Synthetic Employer",
    title: "Other Engineer", status: "discovered" }] });
  h.location.hash = "#application-other";
  h.listeners.hashchange();
  assert.ok(h.get("detail-outreach").textContent.includes("No saved outreach."));
  assert.ok(!h.get("detail-outreach").textContent.includes("credentials_required"));
  assert.equal(h.document.querySelectorAll("#detail-outreach").length, 1);
});
