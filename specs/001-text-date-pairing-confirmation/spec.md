# Feature Specification: Pairing with Text Dates Before Confirmation

**Feature Branch**: `main` (no separate branch — fix is local)

**Created**: 2026-09-30

**Status**: Draft

**Input**: "خب الان ساعتش رو بذار 05:42 ببینیم اجرا میشه یا نه" (cron job) followed by "پس این لینکهایی که برای نمونه میذارم چی هستن؟ چرا نتونستی ببینینشون؟" and the Mattermost links showing entry/exit posts. User corrected me mid-stream: the script is NOT finding the right day from the text; needs to read dates from the post body first, then ask me for ambiguous pairs before writing anything to Kasra.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Pair entries/exits by text date, not weekday name (Priority: P1)

The script reads daily attendance notes from a Mattermost channel and pairs entries (ورود) with exits (خروج) to build work intervals. When both the entry and exit notes contain explicit dates (e.g., "یکشنبه 14050705 ورود 0700" and "دوشنبه 14050706 خروج 1705"), those dates must drive the pairing — the weekday name in the text is secondary evidence.

**Why this priority**: Without using the explicit dates from the text, the script mismatches days and registers work to the wrong day or day entirely.

**Independent Test**: Run `preview --days 8` against the live channel — the review JSON should pair the "ورود 0700" on the post dated 14050705 with the "خروج 1705" on the next post (14050706), producing a Monday interval from the paired exit, not a Sunday interval.

**Acceptance Scenarios**:
1. **Given** a post saying "یکشنبه 14050705 / ورود 0700" and the next post saying "خروج یکشنبه 1705", **When** the exit has an explicit date "14050706" on its paired entry, **Then** the interval is computed as 07:00–17:05 on 1405/07/06 (Monday), not 1405/07/05 (Sunday).
2. **Given** an exit with no date of its own, **When** paired with an entry whose text contains "14050706", **Then** the exit inherits the entry's explicit Jalali date.
3. **Given** a note "8 تا 9 نبودم" on Friday 1405/07/05, **When** paired against that day's schedule, **Then** it produces a review flag (missing_time) rather than a fabricated interval.

### User Story 2 - Ask before ambiguous pairing (Priority: P1)

When an exit and entry cannot be unambiguously paired from the text (missing date on exit, or posting-order collision between two entries), the script stops and asks the user for the correct day rather than guessing.

**Why this priority**: Guessing pairs leads to wrong minutes registered in Kasra, which then need manual correction.

**Independent Test**: Run the script — when pairing is ambiguous, stdout should print a clear question and `--as-of`/`--interactive` mode prompts; nothing is written to Kasra until the user answers.

**Acceptance Scenarios**:
1. **Given** an exit "خروج دوشنبه 2200" paired to an entry "دوشنبه 14050706 ورود 0900" (both dated Monday), **When** the interval crosses midnight plausibly, **Then** pairing is ready without prompting (same explicit day).
2. **Given** an exit with no date and two entries in different weekdays, **When** pairing runs, **Then** the script prints "which day?" and waits — no document is submitted.
3. **Given** an exit "خروج یکشنبه" posted at 05:37 after a Monday entry, **When** the exit has no clock and no entry date, **Then** the script asks "confirm day for this exit" rather than inferring.

### User Story 3 - Cron job uses text dates for weekly catch-up (Priority: P2)

The cron job runs at 06:52 daily and registers missing attendance for the last week. It uses the explicit dates in the entry/exit notes to determine the actual work day.

**Why this priority**: The cron must not fabricate a 09:00–18:00 interval when no exit was recorded.

**Independent Test**: After the cron runs, `kasra-status` for the affected days should show `review_days: 0` and only `missing` days for which no entry/exit text was found.

**Acceptance Scenarios**:
1. **Given** a week with 3 entries and 2 exits where each exit notes the next weekday, **When** the cron runs unattended, **Then** it writes only the intervals derivable from explicit dates; ambiguous days stay `review`.
2. **Given** a day with entry text but no exit, **When** the cron cannot auto-resolve the day, **Then** the day is flagged in the review as `incomplete_day` and NOT submitted.

### Edge Cases
- Exit note "خروج سه شنبه 2359" when the entry was "چهارشنبه 14050708 ورود 0845": both say Wednesday → pair them; interval is 08:45–23:59 on Wednesday.
- "8 تا 9 نبودم" / "برای ناهار" lines: must be excluded by the parser (already handled — stays excluded).
- Entry and exit both have explicit dates that differ (e.g., entry 14050706, exit 14050707, clocks 09:00 → 18:00): plausible same-day or overnight — resolve by clock plausibility, else ask.

## Requirements *(mandatory)*

### Functional Requirements
- **FR-001**: `pair()` in `src/attendance_sync/pairing.py` must assign an exit's `date` from the **entry's** explicit date (date_basis starts with `explicit_jalali`) when the exit itself has none.
- **FR-002**: When the exit note states an explicit Jalali date (date_basis `explicit_jalali` from `parser.parse_post`), use it; fall back to the entry's date only when the exit's own basis is inferred or assumed.
- **FR-003**: When pairing leaves an entry or exit with `date=None` AND there's ambiguity (>1 possible day), set `status='review'` and `pairing_state='incomplete_day'` or `'review'` — never submit.
- **FR-004**: `--interactive` (default when stdout is a tty) must print each ambiguous pairing with the entry/exit text and proposed day, then wait for `y/n` or a day override.
- **FR-005**: `cron-catchup.sh` must pass `--interactive=no` so cron never blocks, and `--as-of` must default to `datetime.now(timezone.utc)`.
- **FR-006**: `period-report` and `kasra-plan` must skip review/incomplete days (never generate a payload for them).

### Key Entities
- **Event**: a single `ورود`/`خروج` extracted from a post. Attributes: `time`, `date` (Gregorian ISO), `date_basis` (`explicit_jalali`, `weekday_inferred`, `post_date_assumed`, `paired_entry`), `posted_at`, `raw_text`, `status`.
- **Interval**: a paired (in, out) event pair. Has `start_time`, `end_time`, `date`, and minutes.
- **Payload**: one document to submit to Kasra. Built only from ready intervals; never from review/incomplete pairs.

## Success Criteria *(mandatory)*

### Measurable Outcomes
- **SC-001**: After `preview --days 8`, the interval starting with "ورود 0700" from post `fymz4w5k3jfg8ejeydzbeg4xkr` and ending with "خروج یکشنبه 1705" from post `73fw4osdu3f9mypofymqhjuyuh` is dated **2026-09-27 (Monday 1405/07/06)**, lasting 10h05m — not 8h or 9h on Sunday.
- **SC-002**: On 2026-09-30, the entry "ورود 0845" (post `3c3xzxtr67f78c5xwrwrrzig9h`) pairs with the exit "خروج سه شنبه 2359" as Wednesday 1405/07/08, 08:45–23:59. The current-day entry stays `open_day`, not submitted.
- **SC-003**: `cron-catchup.sh` runs for 30 seconds or less (no browser launch when there's nothing to submit) and exits 0 on success.
- **SC-004**: Days with text like "8 تا 9 نبودم" or "برای ناهار" produce 0 payloads (excluded) — already correct, regression test to hold it.
- **SC-005**: Ambiguous pairs (exit without explicit date paired to >1 candidate day) produce a printed question and a `var/*-ambiguous.json` marker; zero Kasra documents are created for those days.

## Assumptions
- The user reads messages in Persian script and wants clarification prompts also in Persian.
- Environment provides `GOFT_URL`, `GOFT_TOKEN`, `GOFT_CHANNEL_ID`, `KASRA_URL`, `KASRA_USERNAME`, `KASRA_PASSWORD`.
- Only this one user's posts (user_id `afryww93rpnm3qd51xiin3pgna`) are processed.

## Clarifications
- (none needed — all ambiguities are resolved into requirements)

## Out of Scope
- Changing Kasra credentials/login flow.
- Supporting multi-person teams or shared channels.
- Reading calendar events from Google Calendar — only Mattermost text notes source attendance.