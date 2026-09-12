# AttendanceSync — Stage 1

Python 3.11+ read-only Mattermost attendance preview. No Kasra connection,
login, writes, notifications, approvals, database, or scheduling is implemented.

## Install and verify

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest -q
```

If a SOCKS proxy prevents pip bootstrapping, configure a supported package-manager
proxy or install over an authorized direct connection. The application uses direct
HTTPS (`trust_env=False`); it does not inherit proxy environment settings.

Export `GOFT_URL`, `GOFT_TOKEN`, and `GOFT_CHANNEL_ID` in your private environment.
The URL must be an HTTPS deployment base URL, without credentials, query, fragment,
or `/api/v4` suffix. Channel IDs must match Mattermost's 26-character format.
Do not put secret values in commands, source, reports, or version control.

```sh
.venv/bin/python -m attendance_sync preview --days 14 --output artifacts/review
.venv/bin/python -m attendance_sync preview --from 2026-08-29T10:15:33.002000+00:00 --to 2026-09-12T10:15:33.002172+00:00 --output artifacts/review
```

`--from` and `--to` require timezone-aware ISO timestamps. Do not combine them
with `--days`. Windows are bounded to 366 days. `--debug` prints only a stage,
exception type, and redacted diagnostics. Exit 0 means a preview was generated,
not that attendance was approved or submitted. Exit 2 means no complete new
preview; any existing reports remain from an earlier run.

## Output

- `<output>.json`: schema v2 evidence, independent counts, intervals, and segments.
  Sources retain `candidate_dates`; intervals retain `accounting_days`, including
  known dates when clocks are unresolved. Unmatched eligible events have review
  intervals without invented clocks. `allocation_blockers` identifies each blocked
  day, reasons, interval and source IDs (including separately exported context).
  Each interval exposes `expected_minutes`, `allocated_minutes`, and
  `withheld_minutes`; unresolved durations are null, not zero estimates.
  `withheld_spans` records selected per-day withheld boundaries, minutes, reasons,
  and blocker source IDs; `context_withheld_spans` is separate. For every positive
  resolved interval, expected minutes equal allocated plus explicitly withheld.
- `<output>.html`: compact daily Gregorian/Jalali summary with separate work
  spans, regular/overtime and withheld minute totals and day-specific review reasons; expandable escaped
  evidence and metadata. Self-contained; open it locally.
- `<output>-labels.json`: every in-window own post's ID/version and proposed
  classification. `confirmed_label` stays null until human review.

Reports are private: directory mode 0700, atomic file replacement with mode 0600.
Use a dedicated output directory. Default `artifacts/` is ignored by `.gitignore`.
Attendance/activity source lines are retained verbatim; unrelated posts and other
people's message bodies are omitted. No channel content is sent to an LLM.

## Rules and limitations

- Times display in Asia/Tehran; dates include Gregorian and Jalali calendars.
- Explicit source dates remain intact. Weekday/date conflicts remain review,
  with a suggestion rather than an automatic correction.
- Exits pair to the latest earlier unmatched entry in posting order. A wrong
  exit weekday is retained as a warning, not used to reassign the entry.
  Conflicting explicit exit dates and tied cross-post timestamps require review.
- A bounded 14-day lookback before the requested window supports boundary pairing.
  Context evidence is separated and never expands the selected message window.
  Context work consumes daily quota before selected intervals are allocated;
  uncertain context work blocks allocation. Referenced context events, ranges
  and quota-relevant intervals remain available as evidence.
  Intervals crossing the source window are explicitly marked.
- Multiple work intervals remain separate. Gaps are not work. A return following
  an already closed entry-date interval is overtime. Ordinary activity ranges
  otherwise share a daily quota; explicit overtime never consumes that quota.
- Valid overnight intervals roll over once and split at Tehran midnight.
  Equal clocks are ambiguous, not a guessed 24-hour shift.
- Quotas: Saturday–Tuesday 540 minutes; Wednesday 480; Thursday/Friday zero.
  Official holiday exceptions are not defined.
- Exact duplicate interval representations merge provenance. Non-identical
  overlaps require review. No totals silently double-count overlapping work.
- Activity ranges without a reliable date or confirmed work context remain
  review. Lunch, power-loss, quoted and fenced-code ranges are excluded.
  No time is subtracted merely because a message mentions lunch or electricity.
- `ready` describes parser confidence, not external-write authorization. Review
  intervals do not produce allocation segments. Known/candidate days containing
  unresolved eligible attendance also block quota, including unmatched entries
  and context evidence. Quoted/break/ineligible markers do not block days.
  Partially blocked overnight work records the withheld and allocated days
  separately. Event, range, interval, and segment counts are different quantities.
- Attendance markers require an anchored attendance phrase and adjacent clock,
  allowing intervening weekdays/dates. Technical login prose is not attendance.
  Quotes/code cannot supply date context or consume open entries; genuine
  uncertain attendance still propagates review. Tied posts block dependent pairing.
- Resolved work beyond the explicit report cutoff requires review. Claims more
  than one minute ahead of their source posting timestamp also require review;
  retrospective overnight work remains valid. No future work is allocated.
- Natural-language coverage is conservative, not exhaustive. Source text with
  multiple dates is held for review rather than guessed. Review labels are not
  ground truth and no precision/recall claim is made.

The API client issues only bounded GET requests, refuses redirects, validates
schema and descending cursor order, and fails on repeated pages or incomplete
coverage. It retries only 429/5xx (three attempts, delay capped at five seconds).
Cursor scans are not a transactional server snapshot; edits after fetching can
change later runs. Every preview recomputes pairing from the fetched versions.

## Verified correctness rerun

The fixed-window live read produced 99 posts: 95 own and 4 other-author posts;
19 attendance messages, 20 event markers, 2 activity ranges, 13 intervals, and
12 allocated segments. There were 17 ready events and 3 review events. The
rolling 14-day read produced 97 posts (93 own, 4 other) at execution time.
Both previews retained one context event and passed the artifact verifier,
including source provenance and complete allocated/withheld duration coverage.
Each exposed 6 day-blocker records, 5 withheld spans (1,807 minutes), 3,999
allocated minutes, and one unresolved interval with unknown duration.
The regression suite passes 112 tests. The final three blockers were reproduced
RED before their fixes; regression coverage includes both full/partial withholding,
candidate-day ambiguity, excluded markers, and immutable source evidence.
These are observed snapshots, not hardcoded expectations.

Remaining human review includes the conflicting Tuesday date, an unmatched
current entry, unanchored activity ranges, and interval overlaps. Kasra discovery
and every subsequent write require separate authorization.
