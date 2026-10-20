# Outcome tracking helper

`scripts/outcomes.py` provides pure helpers for recording employer responses. It
does not write application records or integrate with the event CLI or exporter.

```python
from outcomes import apply_outcome, overdue

updated = apply_outcome(
    record,
    "interview",
    occurred_at="2026-10-20T09:00:00+08:00",
    recorded_at="2026-10-20T11:30:00+08:00",
    evidence="applications/records/example/interview-email.txt",
    note="Recruiter invited the applicant to interview.",
    follow_up_at="2026-10-27T09:00:00+08:00",
)
```

Supported event statuses are `acknowledged`, `assessment`, `interview`,
`rejected`, `offer`, and `withdrawn`. Both event timestamps and optional
follow-up timestamps must be ISO-8601 strings with an explicit UTC offset.
`occurred_at` records when the event happened; `recorded_at` records when it
was entered. Events are appended to `outcome_events`; the helper derives
`outcome` and `latest_outcome_at` from the event with the latest occurrence
time. A repeated identical event is ignored.

The input must contain a confirmed submission timestamp, either in
`submissions[].submitted_at` or the top-level `submitted_at`. The helper
returns a deep copy and leaves all submission data and original events intact,
so callers can continue grouping by the original submission time. A supplied
`follow_up_at` is stored on the event and as the record's current follow-up
date. If omitted, `follow_up_days` schedules it from `occurred_at` (14 days by
default). The latest outcome event controls the current follow-up date.

`overdue(record, now, days=14)` returns true at or after the follow-up date
when one is set. With no outcome or follow-up date, it returns true at or
after the original submission time plus `days`. Once an outcome exists and
there is no explicit follow-up, it returns false. Both `now` and saved dates
must include UTC offsets.

Use `workspace.py event --id <app> --status interview --at <offset-time> --follow-up-at <offset-time>`; evidence and notes are optional. Outcome fields pass through CSV/dashboard; overdue follow-ups use `follow_up_days` (default 14). Rejected/withdrawn records have no automatic follow-up. Withdrawals alone are not counted as employer responses. Exact repeated events keep their original recorded timestamp.
