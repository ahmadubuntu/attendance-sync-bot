# Tasks: Pairing with Text Dates Before Confirmation

**Feature**: `specs/001-text-date-pairing-confirmation`
**Created**: 2026-09-30

## Phase 0: Research (already done)

- [X] Root cause identified: explicit date from entry should win over exit's posting date.
- [X] Ambiguity detection needed when multiple entries compete for one exit.
- [X] MAX_SHIFT too high (18h) allowing false 24h+ stretches.

## Phase 1: Data Model & Contracts

- [X] Document Event and Interval entities in pairing.py docstring (update comment).
- [ ] Create contracts/pairing.py with function signatures (optional, can skip for now).

## Phase 2: Core Implementation

### T1: Fix tie-breaking: explicit_jalali entry date wins
- [ ] Edit src/attendance_sync/pairing.py lines 96-104 (the date decision block).
  - When entry['date_basis'] == 'explicit_jalali', use entry['date'] as the attended day regardless of exit's date.
  - Also, if exit has explicit_jalali, use exit's date (already somewhat handled).
  - Implement a helper function `determine_attended_day(entry, exit)` that returns (date, basis, reason).

### T2: Detect ambiguity: >1 eligible entry → review
- [ ] Add function `_detect_ambiguous_exit(entry_list, exit_event)` in pairing.py.
  - If exit has no explicit date (date_basis not explicit_jalali) and len(entry_list) >= 2 within a plausible time window (e.g., same Jalali day or adjacent), return True.
  - In the main loop, when processing an exit, if ambiguous, set status='review', pairing_state='review', and do not pop an entry (or pop but flag both as review).
  - Actually, we should still pop the latest entry but mark both entry and exit as review? The spec says: never submit ambiguous pairs. So we can mark them as review and not produce an interval.

### T3: Lower MAX_SHIFT to 14h
- [ ] Edit src/attendance_sync/misdated.py line 18: change MAX_SHIFT = timedelta(hours=18) to timedelta(hours=14).

### T4: Add `--interactive` flag
- [ ] Edit src/attendance_sync/cli.py:
  - Add `--interactive` option to preview, kasra-plan, kasra-submit (default: auto-detect if sys.stdout.isatty()).
  - When interactive and ambiguity detected (from T2), print a clear question: 
        "Ambiguous exit: [exit raw_text] could belong to entry A ([date A]) or entry B ([date B]). Which day? (A/B/override): "
    Wait for input, then set the exit's date accordingly and mark status based on answer.
  - When non-interactive (cron), skip ambiguous pairs: set status='review', pairing_state='review', and do not produce an interval (or produce but with review status so kasra-plan skips it).

### T5: kasra-plan skips review/incomplete
- [ ] Edit src/attendance_sync/period_report.py or cli.py kasra-plan subcommand:
  - When building payloads, only include events where status == 'ready' (or pairing_state == 'paired' and status == 'ready').
  - Ensure that events with status='review' or pairing_state in ('incomplete_day', 'review') are excluded.

### T6: Tests for T1-T5
- [ ] Edit tests/test_pairing_edges.py (or create new test file) to add:
    - Test explicit_jalali wins: entry with explicit date, exit with no date -> exit gets entry's date.
    - Test ambiguity detection: two entries, one exit with no date -> both marked review, no interval.
    - Test MAX_SHIFT cap: shift >14h should be unresolved.
    - Test interactive flag: when set, prompts for input (we can mock stdin).
- [ ] Ensure all existing 270 tests still pass.

### T7: Integration: live preview + cron dry-run
- [ ] Run preview --days 8 and verify the interval for 1405/07/06 is 07:00-17:05 (Monday).
- [ ] Run kasra-plan --review var/cron-review.json and verify no payloads for ambiguous days.
- [ ] Run cron-catchup.sh in dry-run mode (maybe by adding a flag) and verify zero submissions for review days.

### T8: README + release notes
- [ ] Update README.md to document the new --interactive flag and the pairing behavior.
- [ ] Create RELEASE_NOTES.md for v0.5.0 (or update existing) with the fix.

## Dependencies
- T2 depends on T1 (we need the date basis logic to detect ambiguity).
- T4 depends on T2 (interactive uses ambiguity detection).
- T5 depends on T1 and T2 (skipping review needs correct status).
- T6 depends on T1-T5.
- T7 depends on T6.
- T8 depends on T7.