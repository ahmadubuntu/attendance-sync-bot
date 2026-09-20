"""The day an exit belongs to when the note was written later than the work.

The exit note's own date is not always the day the work happened: a Thursday exit may be typed
on Saturday, and a day off or a holiday can sit in between. Only the clocks can tell a late note
apart from a genuine shift that runs past midnight, so they decide:

* the pair forms one plausible stretch of work and the named day is the day the clocks fall on
  -> that day is used (an evening entry with an after-midnight exit);
* the named day would date the same clocks much later -> the note was written late, so the work
  belongs to the entry's day and the late date is evidence only.

A shift longer than :data:`MAX_SHIFT` is never accepted as a single stretch: it stays unresolved
rather than being stretched to fit. Nothing here invents a day.
"""
from datetime import date, datetime, timedelta, timezone

# The longest shift that still reads as one continuous stretch of work.
MAX_SHIFT = timedelta(hours=18)


def _stamp(day, clock):
    hour, minute = (int(part) for part in clock.split(':'))
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc)


def _shift(entry, exit_event, day):
    """Duration of the interval formed when the pair is dated on ``day``, or ``None``."""
    if not entry.get('time') or not exit_event.get('time'):
        return None
    start = _stamp(date.fromisoformat(entry['date']), entry['time'])
    end = _stamp(date.fromisoformat(day), exit_event['time'])
    if end <= start:
        end += timedelta(days=1)
    return end - start


def resolve_misdated_exit(entry, exit_event, named_day):
    """Return ``(day, reason)``, or ``(None, 'unresolved_exit_day')`` when nothing fits."""
    if not named_day or not entry.get('date'):
        return None, 'unresolved_exit_day'
    entry_day = date.fromisoformat(entry['date'])
    named = date.fromisoformat(named_day)
    if named == entry_day:
        return named_day, None
    on_named = _shift(entry, exit_event, named_day)
    if on_named is not None and on_named <= MAX_SHIFT:
        return named_day, 'exit_named_day_recent'
    on_entry = _shift(entry, exit_event, entry['date'])
    if on_entry is not None and on_entry <= MAX_SHIFT:
        return entry['date'], 'late_note_same_day_work'
    return None, 'unresolved_exit_day'
