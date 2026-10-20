"""Connect the durable runner to existing workspace gates; no browser driver."""
from pathlib import Path
from runner import Runner, Hooks, Queue, Kind, Verification
import batches
import settings
import retry_policy
import budgets
import metrics


def build_runner(root, run_id, brief_file, adapter=None, *, monotonic=None):
    import workspace as w
    root = Path(root).resolve()
    brief_file = Path(brief_file)

    def private_file(reference):
        path = (root / reference).resolve()
        if not path.is_relative_to(root) or not path.is_file() or not path.stat().st_size:
            raise ValueError("runner_evidence_missing")
        return path

    def queue(identity, now):
        try:
            brief = w.read(brief_file)
        except (OSError, ValueError, TypeError):
            raise ValueError("runner_batch_brief_invalid") from None
        if not isinstance(brief, dict) or set(brief) - {"schema_version", "batch_id", "target", "category_targets"}:
            raise ValueError("runner_batch_brief_invalid")
        changed = w.requeue_answers(root, now)
        if changed["updates"]:
            w.finish_mutation(root, {"requeued": len(changed["updates"])}, deferred=True)
        return Queue(w.records(root), brief["batch_id"], brief["target"], brief.get("category_targets"))

    def recover(identity, now):
        result = w.recover_submit(root, now)
        if result["recovered"]:
            w.finish_mutation(root, result, deferred=True)

    def record(action):
        return w.read(root / action.record_ref)

    def current_resume(saved, now):
        scope = (saved.get("prepared") or {}).get("scope") or settings.load_settings(root)["resume_scope"]
        if not scope:
            raise ValueError("runner_resume_scope_required")
        return w.resolve(w.read(root / "resumes/registry.json"), scope, "application", now, root)

    def guard(action, now):
        w.work_guard(root, run_id, now, "runner")
        if (root / "applications/computer-use.lock").exists():
            raise ValueError("runner_browser_claimed")
        if action.application_id:
            saved = record(action)
            decision = retry_policy.check_retry(saved, now, budgets.retry_limit(root, saved))
            if decision.decision != retry_policy.Decision.ELIGIBLE:
                raise ValueError("retry_" + decision.decision.value)
            w.require_eligible(root, saved, now)
            resume = current_resume(saved, now)
            if action.kind in {Kind.REVIEW, Kind.SUBMIT}:
                prepared = saved.get("prepared") or {}
                if prepared.get("id") != resume["id"] or w.digest(private_file(prepared["exact_resume_copy"])) != prepared["sha256"]:
                    raise ValueError("runner_preparation_stale")
                answer_file = private_file(prepared["answers_path"])
                if w.digest(answer_file) != prepared["answers_sha256"] or not w.answer_core.validate_snapshot(root, saved, w.read(answer_file), now)["valid"]:
                    raise ValueError("answer_validation")
            if action.kind == Kind.SUBMIT:
                w.preflight(root, saved, now)

    def begin(action, now):
        observe = getattr(adapter, "observe_url", None)
        if not callable(observe):
            raise ValueError("runner_observed_identity_required")
        observed = observe(action)
        return w.begin_submit(root, action.application_id, observed, run_id, now, deferred=True).get("click_authorized") is True

    def verified_file(path, expected):
        return w.digest(private_file(path)) == expected

    def verify(action, observation, now):
        # Durable records and original artifacts establish completion. An adapter
        # string or a nonempty arbitrary file cannot manufacture a checkpoint.
        state = w.read(root / f"applications/runs/{run_id}/state.json")
        pending = state["pending"]
        if pending["action"]["sequence"] != action.sequence:
            raise ValueError("runner_pending_identity_mismatch")
        started = batches.validate_event_time(pending["started_at"])
        if action.kind == Kind.DISCOVER:
            matches = [r for r in w.records(root) if r.get("run_id") == run_id and
                       r.get("source") == "linkedin_job_page" and r.get("captured_at") and
                       batches.validate_event_time(r["captured_at"]) >= started]
            if matches:
                return Verification("verified", f"applications/records/{matches[0]['application_id']}/record.json")
        else:
            saved = record(action)
            if action.kind == Kind.PREPARE:
                if (saved.get("status") == "awaiting_answer" and saved.get("blocked_on_answer_ids") and saved.get("answer_preparation")
                        and any(e.get("reason") == "unknown_answer" and batches.validate_event_time(e["at"]) >= started for e in saved.get("events", []))):
                    return Verification("verified", action.record_ref)
                prepared = saved.get("prepared") or {}
                if saved.get("status") == "ready" and prepared.get("attempt_id") and batches.validate_event_time(prepared["prepared_at"]) >= started and verified_file(prepared["answers_path"], prepared["answers_sha256"]) and verified_file(prepared["exact_resume_copy"], prepared["sha256"]):
                    if w.answer_core.validate_snapshot(root, saved, w.read(root / prepared["answers_path"]), now)["valid"]:
                        return Verification("verified", action.record_ref)
            elif action.kind == Kind.REVIEW:
                prepared = saved.get("prepared") or {}
                if (prepared.get("review_snapshot") and any(e.get("reason") == "review_passed" and batches.validate_event_time(e["at"]) >= started for e in saved.get("events", []))) and verified_file(prepared["review_snapshot"], prepared["review_sha256"]):
                    review = w.read(root / prepared["review_snapshot"])
                    verdict = w.reviewer.validate_verdict(review["verdict"], prepared, w.read(root / prepared["answers_path"]), review["visible"])
                    if verdict["valid"] and verdict["passed"]:
                        return Verification("verified", prepared["review_snapshot"])
            elif action.kind == Kind.SUBMIT:
                for submission in saved.get("submissions", []):
                    if submission.get("confirmation_path") and submission.get("confirmation_sha256") and verified_file(submission["confirmation_path"], submission["confirmation_sha256"]):
                        return Verification("verified", submission["confirmation_path"])
            # Explicit workspace reconciliation proves non-execution; uncertainty
            # or a status edit without evidence never releases runner pending.
            for event in reversed(saved.get("events", [])):
                if not saved.get("submission_intent") and event.get("at") and batches.validate_event_time(event["at"]) >= started and event.get("reconciliation_path") and event.get("note") and verified_file(event["reconciliation_path"], event["reconciliation_sha256"]):
                    return Verification("not_executed", event["reconciliation_path"])
        return Verification("unresolved")

    def failure(action, reason, now):
        if action.application_id:
            w.retry_failure(root, action.application_id, reason, now, deferred=True)
        else:
            w.atomic_json(root / f"applications/runs/{run_id}/failure-{action.sequence}.json", {"reason": reason.value, "at": now.isoformat()})
            if reason in {retry_policy.Reason.LOGIN, retry_policy.Reason.PAGE_CHANGED, retry_policy.Reason.ACCOUNT_SAFETY}:
                w.safety.stop_run(root, run_id, "manual_stop", now)

    def answer_reuse(action):
        saved = record(action)
        snapshot = saved.get("answer_preparation") or saved.get("prepared") or {}
        if not snapshot.get("answers_path"):
            return None
        return metrics.known_answer_reuse(w.read(private_file(snapshot["answers_path"])))

    hooks = Hooks(lambda: w.writer(root), recover, queue, guard, begin, verify, failure, answer_reuse)
    return Runner(root, run_id, hooks=hooks, adapter=adapter, monotonic=monotonic)
