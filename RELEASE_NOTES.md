# v0.3.0 — period report, confirmed pairing, and a README anyone can follow

## Highlights

- **The period report is now a permanent command.** Give it a range and it writes a
  colour-coded calendar plus a written summary, instead of you running an ad-hoc script.
- **The pairing rule you confirmed is implemented.** An exit closes the latest earlier
  unmatched entry, and a new entry never closes the previous one.
- **The Kasra save timeout is fixed.** Registration no longer fails with `TimeoutError`
  because the browser gave up at 60 seconds while the form was still saving.
- **The README now starts from zero.** A non-technical reader can install the tool, run the
  four steps and read the report without touching code.

## Period report

```sh
python -m attendance_sync period-report --start 1405/05/21 --end 1405/06/20 --output artifacts/period-report
python -m attendance_sync period-report --start 1405/05/21 --end 1405/06/20 --no-kasra --output artifacts/period-report
```

Writes two private files (0600 inside a 0700 directory):

- `.html` — a Saturday-to-Friday calendar for exactly the requested range: green regular,
  orange overtime, dark orange both, blue awaiting an approver, red recorded absence, grey
  rest or holiday, outlined incomplete, current day marked open. Escaped throughout, no
  external assets, `default-src 'none'`.
- `.md` — period totals and one row per day with entry, exit, computed regular and overtime,
  registered regular and overtime, and the status. Unresolved days are named with their
  source ids so a blank cell is explained instead of silent.

Behaviour worth knowing:

- A Kasra shortfall marker never outranks work read from the chat. A day the system flags
  while the channel shows a full working day is reported as work plus a mismatch to look at,
  not as a day off.
- Pending approval outranks the work labels, so a day awaiting an approver is never mistaken
  for a settled one.
- The current day is deferred by default, exactly as `preview` does, and is never counted.
- `--review`, `--snapshot`, `--no-kasra` and `--as-of` all work: you can build the report
  without any Kasra access at all.

## Pairing correctness

- An exit always closes the latest earlier unmatched entry.
- A pair whose determination is not clock order (missing clock, invalid clock, conflicting
  explicit date) stays in review and drags its counterpart with it, so no interval is ever
  allocated with a missing side.
- A day holding a single clock becomes an unresolved remainder that blocks its own day,
  keeps its source ids and its accounting day, and is reported instead of disappearing.
- An entry whose day came only from its posting clock loses to a later exit, so an evening
  entry with an after-midnight exit still forms one overnight interval across two days.

## Kasra writes

- `kasra-submit` accepts `--timeout-ms` and defaults to 120000. The previous 60-second
  browser timeout was shorter than the save form needs, which produced `TimeoutError` on
  writes that the server may have accepted anyway. A smaller requested value is raised to
  the floor rather than honoured.
- Corrected a registration that used `00:00` as an end clock; the last minute of the day is
  the value the form accepts.
- Every write still requires the content-bound approval code. A changed plan invalidates the
  code, every created document is read back from Kasra before success is reported, and the
  record is written after each payload so a later failure cannot lose an earlier document.

## Notes on the two-day registration limit

Kasra accepts a normal remote-work request (`دورکاری ساعتی`, type 14085) only for the most
recent working day or two. Out-of-obligation remote work (`دورکاری خارج از موظفی`, type
60054) has no such limit: it can be registered for any day, at any distance from today, and
is the correct type for evening, weekend and holiday work. The everyday consequence is that
ordinary days should be registered promptly, while overtime can be batched whenever it is
convenient.

## README

Rewritten so the first screen is an ordinary-language guide: what the program needs and
where each value comes from, a one-time install, the environment variables, the four steps
in order (inspect, compare, report, register), the everyday routine, and a table of what to
do when a day looks wrong. The technical reference is unchanged and now sits below it.

## Tests

235 passing. New coverage pins the period report's status precedence, the deferred current
day, the requested-range grid, the pairing edge cases, and the writer's timeout floor.

## Known limitations

- An exit written on a later working day is not always attributed to the day it belongs to.
  This is the first thing to fix before any automatic submission without approval.
- No scheduler or service: every command is run on demand and every write needs its code.
- Official holiday exceptions are not defined; only the weekly quota rules are.
- An interval ending exactly at midnight remains unverified against the live system.
