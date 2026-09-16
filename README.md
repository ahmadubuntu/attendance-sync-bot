# AttendanceSync

Turn the daily attendance notes you already post in your company chat (Mattermost) into
the remote-work and overtime requests your company attendance system (Kasra) expects —
with a human approving every single write.

What it does:

1. **Extract** entry/exit, work ranges and overtime from your personal Mattermost channel,
   keeping the original message text as evidence.
2. **Compute** regular vs. out-of-obligation minutes per day, splitting overnight work at
   midnight and applying the daily quota rules (Saturday–Tuesday 9h, Wednesday 8h,
   Thursday/Friday all overtime), exactly to the minute and never rounded.
3. **Reconcile** the result against the live Kasra documents so you can see what is already
   requested, what is still waiting for an approver, and what was never registered.
4. **Register** the missing items as Kasra credit requests — only after you read the exact
   payloads and approve them with a content-bound code, and every created document carries a
   note saying a bot created it and that it may need correction.
5. **Report** any period you ask for as a colour-coded calendar plus a written summary, so
   you can see at a glance what you worked, what was registered, and what is still missing.

Nothing is ever written without that per-submission approval: there is no auto-submit mode.
Reports, corrections, plans, created-document records and the browser session all live in
private, git-ignored paths with owner-only permissions, and no secret is ever printed.

## Start here if you are not a developer

This section takes you from zero to a working period report without reading any code.
If you only want the full command reference, skip to
[Install and verify](#install-and-verify).

### 1. The three things this program needs

| What | Where it comes from | Example value |
| --- | --- | --- |
| Your chat server address | Your company's Mattermost web address | `https://goft.example.ir` |
| Your personal access token | Mattermost: your avatar → **Profile** → **Security** → **Personal access tokens** → **Create token** | a long string of letters and numbers |
| The ID of the channel where you post attendance notes | Open the channel; the last part of its web address after `/channels/` | `7y3f5gr5pjrdim3nfwpx141cwy` |

You also need your Kasra login (address, username, password) before the program can
compare with the attendance system. Reports work without it.

### 2. Install it once

Open a terminal in the folder where you downloaded this project and paste these lines
one after another. Each one should finish without a red error message.

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest -q
```

Expected last line: `235 passed`. If you see that, the program is installed correctly.

### 3. Give it your details, once per terminal session

```sh
export GOFT_URL='https://goft.example.ir'
export GOFT_TOKEN='paste-your-token-here'
export GOFT_CHANNEL_ID='7y3f5gr5pjrdim3nfwpx141cwy'
export KASRA_URL='https://kasra.example.ir'
export KASRA_USERNAME='your-username'
export KASRA_PASSWORD='your-password'
```

Write your real values in place of the examples, inside the single quotes. Never send
these lines to anyone and never commit them to Git. Closing the terminal forgets them.

### 4. Part one — see what the program understood

This never touches Kasra. It only reads your chat and shows what it found.

```sh
.venv/bin/python -m attendance_sync preview --days 14 --output artifacts/review
```

Then open `artifacts/review.html` in your browser. For every day you see the entry and
exit times the program read from your messages, how many hours count as normal work and
how many as overtime, and the original message next to it so you can check it yourself.

### 5. Part two — see what is still missing in Kasra

```sh
.venv/bin/python -m attendance_sync kasra-status --review artifacts/review.json --start 1405/06/01 --end 1405/06/30
```

Dates here are Jalali in `YYYY/MM/DD` form. Each day gets one line: what the program
expects, what Kasra already holds, and what is missing. Nothing is written.

### 6. Part three — the period report you asked for

```sh
.venv/bin/python -m attendance_sync period-report --start 1405/05/21 --end 1405/06/20 --output artifacts/period-report
```

You get two files:

- `artifacts/period-report.html` — a Saturday-to-Friday calendar, one coloured square per
  day: **green** normal work, **orange** overtime, **dark orange** both, **blue** waiting
  for an approver, **red** recorded absence, **grey** rest day or holiday. Hover any day
  to see its exact times.
- `artifacts/period-report.md` — the written summary: totals for the whole period and one
  table row per day.

Add `--no-kasra` when you want the same report without contacting the attendance system.

### 7. Part four — register what is missing

Nothing is sent until you approve the exact content. This is deliberate.

```sh
# 1. Build the plan: what would be sent, for which day, at which times
.venv/bin/python -m attendance_sync kasra-plan --review artifacts/review.json --start 1405/06/22 --end 1405/06/22

# 2. Read the payloads and get a code. Nothing is sent yet.
.venv/bin/python -m attendance_sync kasra-submit --plan artifacts/kasra-plan.json

# 3. Send them for real, using the code printed by step 2
.venv/bin/python -m attendance_sync kasra-submit --plan artifacts/kasra-plan.json --confirm --approve <the-code>
```

If you change the plan after step 2, the code stops working and step 3 refuses to send —
that is how you know the approval always matches what is really sent. Every document the
program creates is read back from Kasra before it reports success, and the record lands in
`artifacts/kasra-created.json`.

### 8. The everyday routine

1. Post your notes in the channel as you always do: `ورود 0815`, later `خروج 1710`.
2. When you have a moment, run `preview` and glance at the HTML to confirm it was read
   correctly.
3. At the end of the period, run `period-report` and keep the two files.
4. Run `kasra-plan` then `kasra-submit` for the days the report shows as missing, and
   approve them.

### 9. When something looks wrong

| Symptom | What it means | What to do |
| --- | --- | --- |
| A day appears as *incomplete data* | The program found only one clock that day, for example an entry with no exit yet | Post the missing note in the channel and run `preview` again |
| A day appears as *current day, still open* | Today is not finished, so it is deliberately left out of the totals | Nothing; it is handled tomorrow |
| `stage=write; error_type=TimeoutError` | Kasra did not answer the save form in time; no document was created | Read the document list again later before retrying, so you never create a duplicate |
| The chat read fails with an authentication error | `GOFT_TOKEN` is missing, expired, or wrong | Create a new token in Mattermost and export it again |
| Days are attributed to the wrong day | You wrote an exit on a later day than the work | This is supported; check the exit is written after the entry it belongs to |

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

## Stage 3 — period report

`period-report` answers one question: "what did I work, what is registered, and what is
still missing" for a range you choose.

```sh
.venv/bin/python -m attendance_sync period-report --start 1405/05/21 --end 1405/06/20 --output artifacts/period-report
.venv/bin/python -m attendance_sync period-report --start 1405/05/21 --end 1405/06/20 --no-kasra --output artifacts/period-report
```

- `--start` and `--end` are Jalali `YYYY/MM/DD` and are validated exactly like
  `kasra-status`; a reversed range is refused with exit 2 and nothing written.
- `--review <file>` reuses a preview whose window matches the range exactly instead of
  fetching again. `--snapshot <file>` reads Kasra from a recorded file and opens no browser.
- `--no-kasra` produces the same report with the registered columns blank, so it works
  without any Kasra access at all.
- `--as-of` overrides the instant used for the current day; it defaults to the run time and
  a naive timestamp is refused. The current day is always deferred, never counted.

Two files are written, both private (0600 inside a 0700 directory):

- `<output>.html`: a Saturday-to-Friday calendar for exactly the requested range — not the
  wider context window — one cell per day, colour-coded by status: green regular, orange
  overtime, dark orange both, blue pending approval, red absence, grey rest or holiday,
  outlined incomplete, and the current day marked open. Every value is escaped, the document
  carries `default-src 'none'` and there are no external assets or scripts.
- `<output>.md`: period totals and one table row per day with entry, exit, computed regular
  and overtime, registered regular and overtime, and the status. Incomplete days are named
  with their source ids so a blank cell is explained rather than silent.

Status precedence is deliberate: work that can be evidenced from the chat outranks a Kasra
shortfall marker, because a day the system flags while the chat shows a full working day is
a mismatch to look at, not a day off. Suspended or absent days are only reported as such
when no work was read for them. Pending approval outranks the work labels so a day awaiting
an approver is never mistaken for a settled one.

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
  **Every write requires an explicit per-submission approval:** the dry run prints an
  `approval_code` derived from the exact payloads it would send, and the write only happens
  with `--confirm --approve <code>`. A missing, wrong, or stale code (the plan changed after
  the code was issued) is refused with exit 2 and nothing is sent. In an interactive terminal
  the code is prompted for instead of passed on the command line. `--timeout-ms` controls the
  browser timeout and defaults to 120000; the Kasra save form routinely needs more than a
  minute, and a smaller requested value is raised to that floor rather than honoured.
  Payloads flagged
  `requires_review` (an interval ending exactly at midnight) are skipped unless
  `--include-review` is added, and the created document id is read back from the document
  list into `artifacts/kasra-created.json`.
  `kasra-submit --delete-doc-id <id>` prints the code bound to that id, and
  `--confirm --approve <code>` deletes exactly that one document.
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

The write path was exercised once against the live system and verified by reading the
result back: two credit requests for one day (type 14085 for the obligation and type 60054
for the excess) were created with the required description and reappeared in the document
list with status 201 (waiting for an approver). The writer reads every created document back
before it reports success, and it never reports a write it cannot find again.

## Rules and limitations

- **Registration windows differ by credit type.** Kasra accepts a normal remote-work request
  (`دورکاری ساعتی`, type 14085) only for the most recent working day or two, so ordinary days
  must be registered promptly. Out-of-obligation remote work (`دورکاری خارج از موظفی`, type
  60054) can be registered for any day at any distance from today and is the correct type for
  evening, weekend and holiday work. A rejected write is never retried blindly: read the
  document list first so no duplicate is created.
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

The regression suite passes 235 tests: the stage-one parser and provenance tests, the
stage-two tests covering Kasra reconciliation, the dry-run-by-default writer and the
approval gate (missing, wrong and stale approval codes are all refused), plus this cycle's
pairing edge cases and the period report. Headline behaviours now pinned by tests:

- an exit closes the latest earlier unmatched entry, and a new entry never closes the
  previous one;
- a pair decided by anything other than clock order stays in review and drags its
  counterpart with it, so no interval is ever allocated with a missing side;
- a day holding a single clock becomes an unresolved remainder that blocks its own day and
  names its source ids instead of quietly disappearing;
- the requested range decides the report's cells, not the 14-day context the pipeline
  fetched for boundary pairing;
- the period report can never treat a Kasra shortfall marker as stronger evidence than work
  read from the chat, and the current day is always deferred rather than counted.

Every correction in this cycle started from an observed failure, including an exit posted
on a later day than the work it belongs to, and a status label that contradicted the
minutes printed next to it. Earlier regressions still cover multi-marker technical prose,
unresolved-day quota blocking, partial overnight withholding, and immutable source
evidence. These are observed snapshots, not hardcoded expectations.

Remaining work: reconciliation and submission are run on demand; there is no scheduler or
service in this repository, and the approval code must be read and typed for every write.
An exit written on a later working day is not always attributed to the day it belongs to,
which is the first thing to fix before any automatic submission. Two review suggestions are
still open: surfacing non-blocking interval warnings (`claim_ahead_of_post`) in the daily
summary, and flagging a day whose missing minutes cannot be explained by the readable
document coverage. The unverified boundary cases are listed in
`docs/kasra-contract.md` (§9), including an interval that ends exactly at midnight.

## License

MIT — see [LICENSE](LICENSE).
