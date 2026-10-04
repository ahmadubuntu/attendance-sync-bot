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
    """Duration of the interval formed when the pair is dated on ``day``, or ``None``.

    ``None`` when ``day`` is too far from the entry's own day, so that an exit named days away
    cannot be measured as though it had been pulled back onto the entry. The comparison below
    deliberately does *not* wrap past the entry's day: an `08:25` entry on the 23rd against a
    `17:10` exit named for the 29th is a six-day gap, not a five-day-and-forty-five-hour shift
    that happens to read as three hours once it is allowed to wrap. Wrapping exists for a
    night shift, and a night shift crosses one midnight, not six.

    The reach is the same two days a late note may be written in -- see ``LATE_NOTE_WINDOW`` --
    so the two places that answer "is this the same shift?" cannot answer differently.
    """
    if not entry.get('time') or not exit_event.get('time'):
        return None
    entry_day = date.fromisoformat(entry['date'])
    named = date.fromisoformat(day)
    if not (entry_day <= named <= entry_day + timedelta(days=2)):
        return None
    start = _stamp(entry_day, entry['time'])
    end = _stamp(named, exit_event['time'])
    if end <= start:
        end += timedelta(days=1)
    return end - start


def resolve_misdated_exit(entry, exit_event, named_day):
    """Return ``(day, reason)``, or ``(None, 'unresolved_exit_day')`` when nothing fits.

    Which day an exit belongs to is decided by which one makes it the same believable-length
    shift as the entry it closes.

    A shift that runs past midnight keeps the day the note named. `ورود 1900` on the 12th and
    `خروج 0400` written for the 13th are one night of work, and the split is what the day
    totals are built from: the hours up to 23:59 belong to the day before and the hours from
    00:01 belong to the day named, so the night is credited to both days in the proportions it
    was actually worked. Filing the whole night on the entry's day would take those early
    hours away from the day they were worked in.

    A note written later about a day already worked takes the entry's day instead. The clocks
    decide which of the two applies -- 08:30 to 17:10 named two days on is a late note about
    one day, while 19:00 to 04:00 named one day on is the night itself.

    Only when neither day works is the day unresolved, and then the user is asked rather than
    one of the two chosen.
    """
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
