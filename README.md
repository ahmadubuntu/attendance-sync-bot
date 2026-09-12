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

`--corrections` defaults to `var/corrections.json`. A missing file means no
corrections; a malformed file fails the run. Only date corrections approved by
the user are accepted, and each entry is bound to the source post ID, a SHA-256
fingerprint of the source post, its raw date token, and the confirmed target
date. The stored raw date and weekday remain unchanged; the confirmed date is
recorded under `correction` and `original_date`. If the source message or
timestamp later changes, the fingerprint no longer matches and the event returns
to review with `stale_date_correction` instead of being silently corrected.

`--as-of` is the explicit as-of instant for the current day and defaults to the
run time. Naive timestamps are rejected. The current day is deferred: it is
reported as `open_day`/`deferred_spans` with `submission_eligible: false` and
never proposed for registration, because the day is still in progress.

## Output

- `<output>.json`: schema v2 evidence, independent counts, intervals, and segments.
  Sources retain `candidate_dates`; intervals retain `accounting_days`, including
  known dates when clocks are unresolved. Unmatched eligible events have review
  intervals without invented clocks. `allocation_blockers` identifies each blocked
  day, reasons, interval and source IDs (including separately exported context).
  Each interval exposes `expected_minutes`, `allocated_minutes`, and
  `withheld_minutes`; unresolved durations are null, not zero estimates.
  `withheld_spans` records selected per-day withheld boundaries, minutes, reasons,
  and blocker source IDs; `context_withheld_spans` is separate. `deferred_spans`
  and `deferred_minutes` cover current-day work that must not be registered yet.
  `date_discrepancies` records disputed dates with the previous/next entry dates,
  the posting date, the inferred suggestion, neighbor support, and
  `kasra_check: not_performed` / `comparison_status: pending_kasra_comparison`
  until a real Kasra comparison exists. For every positive
  resolved interval, expected minutes equal allocated plus explicitly withheld
  plus deferred.
- `<output>.html`: compact daily Gregorian/Jalali summary with separate work
  spans, regular/overtime, withheld and deferred minute totals and day-specific
  review reasons; expandable escaped evidence and metadata. Self-contained;
  open it locally.
- `<output>-labels.json`: every in-window own post's ID/version and proposed
  classification. `confirmed_label` stays null until human review.

Reports are private: directory mode 0700, atomic file replacement with mode 0600.
Use a dedicated output directory. Default `artifacts/` is ignored by `.gitignore`.
Attendance/activity source lines are retained verbatim; unrelated posts and other
people's message bodies are omitted. No channel content is sent to an LLM.

## Stage 2 — Kasra reconciliation (read-only by default)

The Kasra integration is documented in `docs/kasra-contract.md`. Authentication and every
request run inside a real Chrome session (Playwright, `executable_path` `/usr/bin/google-chrome`,
headless) because the gateway rejects plain HTTP clients. The saved session lives in
`var/kasra-recon/session-state.json` (private, ignored); when it expires, the adapter logs in
again from `KASRA_URL`, `KASRA_USERNAME` and `KASRA_PASSWORD`. Credentials, cookies and
session values are never printed, logged, stored in the repository or returned by the API.

```sh
.venv/bin/python -m attendance_sync kasra-status --start 1405/06/01 --end 1405/06/21
.venv/bin/python -m attendance_sync kasra-plan --start 1405/06/01 --end 1405/06/21
.venv/bin/python -m attendance_sync kasra-submit --plan artifacts/kasra-plan.json
```

- `kasra-status` reads the daily work report and the document inquiry, then prints one line
  per day (Gregorian and Jalali date, classification, expected/requested/missing/pending
  minutes per category) plus a JSON summary with the unregistered Jalali days. It only reads.
- `kasra-plan` writes the same reconciliation to a private plan file
  (default `artifacts/kasra-plan.json`, mode 0600 in a 0700 directory) together with the
  exact payloads that would be created. `--output` changes the path.
- `kasra-submit --plan <file>` prints those payloads and exits 0 without touching Kasra.
  Only `--confirm` performs the write; payloads flagged `requires_review` (an interval
  ending exactly at midnight) are skipped unless `--include-review` is added, and the
  created document id is read back from the document list into `artifacts/kasra-created.json`.
  `kasra-submit --delete-doc-id <id> --confirm` deletes exactly one document by id.
- `--snapshot <file>` reuses a private recorded read (written by `--save-snapshot`) so the
  reconciliation and the dry run work without opening a browser.

Per-day classification: `ok`, `pending_approval` (a document of the right type exists and
waits for an approver), `no_document`, `missing_regular`, `missing_overtime`,
`partial_regular`, `partial_overtime`, and `no_work` for a day with no extracted work.
Every document the writer creates carries the configurable description in
`kasra_reconcile.DEFAULT_DESCRIPTION`; the default is overridable with `--description`.
Minutes are exact integers and are never rounded. Kasra is a request system: a saved
document is not finalized attendance, and a day covered by an active document is never
reported as missing.

The confirmed write path is not verified against the live system: the credit-request save
call (`EnterCreditNameSpace.onClickBtnSave`) has never been exercised, so no endpoint or
payload is assumed and no document has been created, edited or deleted by this project.

## Rules and limitations

- Times display in Asia/Tehran; dates include Gregorian and Jalali calendars.
- Explicit source dates remain intact. Weekday/date conflicts remain review,
  with a suggestion rather than an automatic correction. A user-confirmed
  correction entry may resolve a conflict, and disputed dates are reported under
  `date_discrepancies` with neighboring entry dates, the posting date and clock,
  and a pending Kasra comparison. Date mistakes are never inferred from a single
  message without supporting neighbors or a confirmed correction.
- The current day is never proposed for registration. It is reported separately
  as still open, and any closed interval on the current day is deferred rather
  than withheld, so an unfinished day is not mistaken for a missing record.
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
- Recorded work ranges are taken at the clock values written in the message.
  When a range has no explicit date and its recorded end time is not later than
  the posting time, the local posting date anchors the range
  (`posted_clock_inferred`); a recorded overnight range that already ended when a
  morning message was posted anchors to the previous day
  (`posted_clock_overnight_inferred`). Both keep a warning and their evidence, and
  unknown or future durations still require review.
- Lunch, power-loss, quoted and fenced-code ranges are excluded.
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
19 attendance messages, 20 event markers, 2 recorded activity ranges, 13
intervals, and 19 allocated segments. The rolling 14-day read produced 97 posts
(93 own, 4 other) at execution time. Both previews retained one context event,
one confirmed date correction, one reported date discrepancy with a pending
Kasra comparison, and one open current-day record: an entry with no closing clock
is reported as an `open_day` event rather than a registration candidate, and a
closed interval on the current day would be reported as a deferred span. Both
passed the artifact verifier, including source provenance
and complete allocated/withheld/deferred duration coverage.

The regression suite passes 176 tests: the 126 stage-one tests plus 50 stage-two tests
covering Kasra reconciliation, the dry-run-by-default writer, the payload/description
rules and the new CLI subcommands. Every correction in this cycle started
from an observed failure: the date-context annotation, the corrections file and
current-day handling in the CLI, and the daily-summary display of deferred work.
Earlier regressions still cover multi-marker technical prose, unresolved-day
quota blocking, partial overnight withholding, and immutable source evidence.
These are observed snapshots, not hardcoded expectations.

Remaining work: the disputed Tuesday date is now confirmed by the user and bound
to the source fingerprint, but no Kasra record has been read or compared, so
`kasra_check` stays `not_performed`. Kasra discovery, discrepancy comparison
against the real system, and every subsequent write still require separate
authorization.
