(() => {
  "use strict";
  const terminal = new Set(["submitted", "rejected", "interview", "withdrawn", "skipped", "saved", "dry_run_complete", "acknowledged", "assessment", "offer"]);
  const parse = (v) => { const n = Date.parse(v || ""); return Number.isFinite(n) ? n : null; };
  const latestSubmission = (app) => (app.submissions || []).map((s) => ({ ...s, at: s.at || s.submitted_at })).filter((s) => parse(s.at) !== null).sort((a,b) => parse(b.at)-parse(a.at))[0] || null;
  function channel(app) { if (app.channel) return String(app.channel); try { return new URL(app.application_url || app.job_url).hostname; } catch { return "Unknown"; } }
  function stats(apps, now, timezone) {
    const counts = { Applied: 0, "Awaiting answer": 0, Preparing: 0, Failed: 0, Skipped: 0, Today: 0, "7 days": 0 };
    const current = now instanceof Date ? now : new Date(now), parts = new Intl.DateTimeFormat("en-CA", { timeZone: timezone || "UTC", year: "numeric", month: "2-digit", day: "2-digit" }).format(current);
    const weekStart = current.getTime() - 7 * 86400000;
    for (const app of apps) {
      const status = app.status;
      if ((app.submissions || []).length || status === "submitted") counts.Applied++;
      if (status === "awaiting_answer") counts["Awaiting answer"]++;
      if (["preparing", "ready"].includes(status)) counts.Preparing++;
      if (status === "failed") counts.Failed++;
      if (status === "skipped") counts.Skipped++;
      const sub = latestSubmission(app), at = parse(sub?.at);
      if (at !== null) { const local = new Intl.DateTimeFormat("en-CA", { timeZone: timezone || "UTC", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(at)); if (local === parts) counts.Today++; if (at >= weekStart && at <= current.getTime()) counts["7 days"]++; }
    }
    return counts;
  }
  function funnel(apps, outcomes) {
    const outcomeIds = new Set((outcomes || []).filter((o) => o.outcome !== "withdrawn").map((o) => o.application_id));
    return { Captured: apps.length, Ready: apps.filter((a) => a.status === "ready" || a.status === "submitted" || (a.submissions || []).length).length, Submitted: apps.filter((a) => a.status === "submitted" || (a.submissions || []).length).length, Responded: apps.filter((a) => (a.responded === true || a.responded === undefined && (outcomeIds.has(a.id) || a.response && a.response !== "withdrawn" || a.outcome && a.outcome !== "withdrawn" || ["interview", "rejected"].includes(a.status)))).length };
  }
  function attention(apps, timezone, now = new Date()) {
    const instant = now instanceof Date ? now.getTime() : Date.parse(now), staleMs = 24 * 60 * 60 * 1000;
    return apps.flatMap((app) => {
      const status = app.status, updated = parse(app.updated_at || app.prepared?.at), stale = ["preparing", "ready"].includes(status) && updated !== null && instant - updated >= staleMs;
      const relevant = !terminal.has(status) || (app.blocked_on_answer_ids || []).length > 0;
      if (!relevant) return [];
      const blockedCount = (app.blocked_on_answer_ids || []).length;
      const reason = app.stop_reason || app.reason || (status === "awaiting_answer" || status === "blocked" || blockedCount ? (blockedCount ? `Waiting for ${blockedCount} answer${blockedCount === 1 ? "" : "s"}.` : "Waiting for an answer.") : status === "submission_uncertain" ? "Submission needs reconciliation before retry." : status === "failed" ? "Application attempt failed." : stale ? `No progress recorded for at least 24 hours in ${status}.` : status === "discovered" ? "Queued for preparation." : status === "ready" ? "Ready; queued for submission checks." : "Queued; preparation in progress.");
      return [{ application_id: app.id, id: app.id, title: app.title, company: app.company, status, reason, stale, stop_reason: app.stop_reason || null, listing_url: app.job_url || app.listing_url, record_url: app.record_url || null }];
    });
  }
  function filterApplications(apps, filters, timezone) {
    const lower = (x) => String(x || "").toLocaleLowerCase();
    return apps.filter((app) => {
      if (filters.status && app.status !== filters.status) return false;
      if (filters.company && !lower(app.company).includes(lower(filters.company))) return false;
      if (filters.channel) {
        const hosts = [app.application_url, app.job_url].map((value) => { try { return new URL(value).hostname; } catch { return ""; } });
        if (![channel(app), ...hosts].some((value) => lower(value).includes(lower(filters.channel)))) return false;
      }
      if (filters.applied_by && !lower(app.applied_by).includes(lower(filters.applied_by)) && !(app.submissions || []).some((s) => lower(s.applied_by).includes(lower(filters.applied_by)))) return false;
      if (filters.stop_reason && !lower(app.stop_reason || app.reason).includes(lower(filters.stop_reason))) return false;
      const sub = latestSubmission(app), date = sub?.at ? new Intl.DateTimeFormat("en-CA", { timeZone: timezone || "UTC", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(sub.at)) : "";
      if (filters.from && (!date || date < filters.from)) return false;
      if (filters.to && (!date || date > filters.to)) return false;
      return true;
    });
  }
  function sortApplications(apps, key, direction) {
    const get = (a) => key === "submitted_at" ? latestSubmission(a)?.at || "" : key === "channel" ? channel(a) : key === "response" ? a.response || a.outcome || "" : a[key] || "";
    const sign = direction === "desc" ? -1 : 1;
    return [...apps].sort((a,b) => {
      const av = get(a), bv = get(b);
      if (key === "submitted_at") return sign * ((parse(av) ?? -Infinity) - (parse(bv) ?? -Infinity));
      return sign * String(av).localeCompare(String(bv), undefined, { numeric: true, sensitivity: "base" });
    });
  }
  function hash(value) { let h = 2166136261; for (const c of String(value)) { h ^= c.charCodeAt(0); h = Math.imul(h, 16777619); } return (h >>> 0).toString(16); }
  window.DashboardControl = { stats, funnel, attention, filterApplications, sortApplications, latestSubmission, channel, hash };
})();
