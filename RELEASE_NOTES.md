# v0.4.0 — exits pair across days

## Highlights

- **An exit now closes the entry it follows on whatever day it was written.** A late evening
  exit, an after-midnight exit and a next-morning exit all attach to the entry they follow, and
  the work is counted on the day that entry opened.
- **A misdated exit is never silently moved.** When the exit names a different weekday or date,
  that is kept as evidence and reported as a warning or a review conflict.

## Why

Notes are not always written the same day. The exit for a day's work is often posted the next
morning, sometimes in the same message as the new day's entry, and sometimes just after
midnight. Those exits used to lose their day: they could not be attributed, so the day they
belonged to looked blank while the exit sat unresolved. That is the case this release closes.

## Behaviour

- An exit written late the same evening, after midnight, or the next morning closes the latest
  earlier unmatched entry, and the interval is counted on the entry's day.
- A weekday or explicit date on the exit that disagrees with the pairing is preserved
  (`source_date`) and reported: `exit_weekday_overridden_by_pairing` as a warning, or
  `explicit_exit_date_conflict` for review. The entry is never silently reassigned.
- An exit with no entry to close is still reported with no date and no invented clock.
- Pairing stays review when clock and order alone cannot decide it, so an interval is never
  built from a half-known pair.

## Period report, from v0.3.0

```sh
python -m attendance_sync period-report --start 1405/05/21 --end 1405/06/20 --output artifacts/period-report
```

A colour-coded Saturday-to-Friday calendar plus a written summary for exactly the range you
ask for: green regular, orange overtime, dark orange both, blue awaiting an approver, red
recorded absence, grey rest or holiday. `--no-kasra` builds it without contacting the
attendance system, and the current day is always deferred rather than counted.

## Kasra writes, from v0.3.0

- `--timeout-ms` defaults to 120000. The old 60-second browser timeout was shorter than the
  save form needs, which produced `TimeoutError` on writes the server may have accepted anyway.
  A smaller requested value is raised to the floor rather than honoured.
- Registration windows differ by credit type. A normal remote-work request is accepted only for
  the most recent working day or two; out-of-obligation remote work can be registered for any
  day at any distance.
- Every write still requires the content-bound approval code, and every created document is
  read back from Kasra before success is reported.

## Tests

243 passing. New coverage pins the late-evening, after-midnight and next-morning exit shapes,
the named-day conflict, the bare exit with no counterpart, and the earlier releases' behaviours.

## Known limitations

- No scheduler or service: every command is run on demand and every write needs its code.
- Official holiday exceptions are not defined; only the weekly quota rules are.
- An exit that carries an explicit date on a later day than its entry is reported for review
  instead of being attributed back to the open entry.
- An interval ending exactly at midnight remains unverified against the live system.
