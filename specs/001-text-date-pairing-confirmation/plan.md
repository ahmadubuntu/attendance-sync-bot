# Implementation Plan: Pairing with Text Dates Before Confirmation

**Feature**: `specs/001-text-date-pairing-confirmation`
**Created**: 2026-09-30
**Status**: Draft

## Technical Context

- **Language**: Python 3.11+ (src/ layout)
- **Test framework**: pytest, 270 tests already passing
- **Key file**: `src/attendance_sync/pairing.py` — `pair()` function (178 lines)
- **Key file**: `src/attendance_sync/parser.py` — `parse_post()` extracts events with `date_basis`
- **Key file**: `src/attendance_sync/cli.py` — `preview`, `kasra-plan`, `kasra-submit` subcommands
- **Key file**: `scripts/cron-catchup.sh` — cron wrapper
- **Browser automation**: Playwright for Kasra (write path only)

## Constitution Check

- No Persian comments in code ✓ (all English)
- Plans in `.hermes/plans/<slug>.md` ✓ (this plan is in specs/)
- Credentials redacted ✓
- README readable by non-technical person ✓

## Gates

- ERROR if any spec requirement (FR-001..FR-006) lacks a task

## Phases

### Phase 0: Research (none needed — root cause already identified)

Root cause is known from live data:
1. `pair()` uses `source_date` (posting day) to break ties, which favors the exit's posting day (Tuesday 1405/07/06) over the entry's explicit date — **reversed from user intent**.
2. `resolve_misdated_exit()` caps shifts at 18h — it accepts the false 24h+ "Monday" stretch because `on_named` exceeds MAX_SHIFT and falls through to `unresolved_exit_day`, then the caller silently uses the entry day anyway.
3. No ambiguity detection: when two entries (07:00 Sat, 09:00 Mon) compete for one exit (Mon 17:05), the LIFO rule picks the wrong one.

### Phase 1: Data Model & Contracts

**Entities** (update `src/attendance_sync/pairing.py` docstring + tests):
- `Event`: `event_id`, `kind` (in/out), `time`, `date`, `date_basis` (`explicit_jalali` | `weekday_inferred` | `post_date_assumed` | `paired_entry`), `posted_at`, `status`, `pairing_state`
- `Interval`: `entry`, `exit`, `date`, `minutes`, `basis`, `status` (ready | review | incomplete_day)

**Contract** (add to `src/attendance_sync/contracts.py` or new file):
- `pair(events, *, interactive=False) -> list[Event]`: returns events with `pairing_state` set; ambiguous pairs get `status='review'`, never `paired`.
- `resolve_pair(entry, exit) -> (date, basis, reason)`: uses explicit date first, then weekday, then posting date as last resort.

### Phase 2: Core Implementation [P]

**Task 2a — Fix tie-breaking in `pair()`** (pairing.py lines 96-104):
- When entry's basis is `explicit_jalali`, the entry's explicit date wins over the exit's posting date for the date decision.
- Add `explicit_date_wins` check: if `entry['date_basis'] == 'explicit_jalali'`, use `entry['date']` as the attended day regardless of exit date.

**Task 2b — Detect ambiguity (two entries for one exit)** (pairing.py new function `_detect_ambiguity`):
- Before popping the latest entry, check if there are multiple eligible entries within a plausible window.
- If `len(opened) >= 2` and the exit has no explicit date, set `ambiguous=True` → `status='review'`, `pairing_state='review'`.

**Task 2c — Fix `resolve_misdated_exit` cap** (misdated.py line 18):
- Lower `MAX_SHIFT` from 18h to 14h (no legitimate shift exceeds 14h for this user's schedule).
- This prevents the false 24h+ Monday stretch from being accepted.

**Task 2d — Add `--interactive` flag** (cli.py):
- New CLI option `--interactive` (default: auto-detect tty).
- When interactive and ambiguity detected: print entry/exit text + proposed day, prompt `y/n/day-override`.
- When non-interactive (cron): skip ambiguous pairs, mark `review`, never submit.

**Task 2e — `kasra-plan` skips review/incomplete** (cli.py or period_report.py):
- Filter payloads: only `status='ready'` intervals produce payloads.
- Add `review_count` and `ambiguous_count` to plan output JSON.

### Phase 3: Tests

- **TDD**: write failing tests first for each task.
- `tests/test_pairing_edges.py`: add cases for (a) text-date wins over posting date, (b) ambiguity detection, (c) MAX_SHIFT cap.
- `tests/test_misdated.py`: update MAX_SHIFT test.
- `tests/test_interactive.py`: test `--interactive` flag behavior.
- Regression: existing 270 tests must still pass.

### Phase 4: Integration

- Run `preview --days 8` against live channel → verify interval for 1405/07/06 is 07:00–17:05 (10h05m).
- Run `kasra-plan --review var/cron-review.json` → verify no payloads for ambiguous days.
- Run cron script in dry-run → verify zero submissions for review days.

### Phase 5: Polish

- Update README with new `--interactive` behavior.
- Add `RELEASE_NOTES.md` entry.
- Bump version to 0.5.0.

## Tasks Summary

| ID | Task | Depends On |
|----|------|------------|
| T1 | Fix tie-breaking: explicit_jalali entry date wins | — |
| T2 | Detect ambiguity: >1 eligible entry → review | T1 |
| T3 | Lower MAX_SHIFT to 14h | — |
| T4 | Add `--interactive` flag | T2 |
| T5 | `kasra-plan` skips review/incomplete | T1, T2 |
| T6 | Tests for T1-T5 | T1-T5 |
| T7 | Integration: live preview + cron dry-run | T6 |
| T8 | README + release notes | T7 |

## Validation

- **SC-001**: Interval for 1405/07/06 = 07:00–17:05 (10h05m), dated Monday ✓
- **SC-002**: 1405/07/08 entry stays `open_day`, not submitted ✓
- **SC-003**: Cron runs ≤30s when nothing to submit ✓
- **SC-004**: "8 تا 9 نبودم" lines produce 0 payloads ✓
- **SC-005**: Ambiguous pairs print question, zero Kasra docs ✓