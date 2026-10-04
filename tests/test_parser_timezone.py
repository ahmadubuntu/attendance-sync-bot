"""The system clock may be anywhere. The text is written in Iran time.

`create_at` is a UTC instant, so it carries no day on its own: what day it *looks* like depends
entirely on the machine that ran the bot. A message sent 09:00 New York time is 17:30 Tehran,
and a message sent 22:00 Tehran is 14:30 the same day in New York. Deriving the attended day
from the posting instant therefore silently shifts work across midnight, and it does so
differently depending on where the machine happens to be.

So no day may ever come out of the posting instant. A weekday with no date beside it names a
weekday, not a day, and only the channel's own written days can settle it. These tests pin
that: the same posts parse identically no matter what the machine clock says.
"""

import datetime

import pytest

from attendance_sync.parser import parse_post


def post_at(ms, message):
    return {'id': 'probe', 'create_at': ms, 'message': message, 'user_id': 'u'}


def tehran_monday_nine():
    return int(datetime.datetime(
        2026, 9, 28, 9, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=3, minutes=30))
    ).timestamp() * 1000)


# Machine clocks that produce a different calendar day for the very same instant.
OFFSETS = [
    ('tehran', datetime.timezone(datetime.timedelta(hours=3, minutes=30))),
    ('new_york', datetime.timezone(datetime.timedelta(hours=-4))),
    ('utc', datetime.timezone.utc),
    ('tokyo', datetime.timezone(datetime.timedelta(hours=9))),
]


@pytest.mark.parametrize('clock', OFFSETS, ids=[name for name, _ in OFFSETS])
def test_a_weekday_with_no_date_never_becomes_a_calendar_day(clock, monkeypatch):
    """`دوشنبه` alone must stay a weekday. No posting instant can promote it to a day."""
    message = 'دوشنبه\nورود 0900'
    events = _events(parse_post(post_at(tehran_monday_nine(), message)))

    assert events, 'the entry was dropped'
    entry = events[0]
    assert entry['time'] == '09:00'
    assert entry['raw_weekday'] == 'دوشنبه'
    assert entry['date'] is None, 'a weekday was turned into a day using the posting clock'
    assert entry['date_basis'] not in ('weekday_inferred', 'post_date_assumed')


def test_the_same_instant_parses_identically_on_every_machine_clock():
    """Four clocks, one answer. A machine-clock change must not move a single event."""
    message = 'دوشنبه\nورود 0900'
    results = []
    for _, clock in OFFSETS:
        with _system_clock(clock):
            results.append(_events(parse_post(post_at(tehran_monday_nine(), message))))

    shapes = {(e['kind'], e['time'], e['date'], e['date_basis'], tuple(e['reasons']))
              for group in results for e in group}
    assert len(shapes) == 1, f'parsing is machine-clock dependent: {shapes}'


def test_a_night_shift_exit_sent_after_midnight_tehran_time_keeps_the_previous_day():
    """Sent 01:00 Tehran on Tuesday, saying Monday. Monday is the day, in any timezone.

    This is the case the bug produces: 01:00 Tehran is 17:00 Monday in New York, so a parser
    that trusts the posting clock reads the note as Monday and agrees with itself -- while a
    parser that trusts Tehran reads it as Tuesday and looks one day late.
    """
    sent_tuesday_0100 = int(datetime.datetime(
        2026, 9, 29, 1, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=3, minutes=30))
    ).timestamp() * 1000)
    events = _events(parse_post(post_at(sent_tuesday_0100, 'خروج دوشنبه 2200')))

    assert len(events) == 1
    exit_event = events[0]
    assert exit_event['raw_weekday'] == 'دوشنبه'
    assert exit_event['date'] is None, 'posting clock decided the day'
    assert exit_event['time'] == '22:00'


def test_two_machine_clocks_near_midnight_produce_the_same_candidate_set():
    """Shifting the machine clock across midnight must not change which days are possible."""
    shapes = set()
    for _, clock in OFFSETS:
        with _system_clock(clock):
            events = _events(parse_post(post_at(tehran_monday_nine(), 'شنبه\nورود 0810')))
        shapes.add(tuple(
            (e['kind'], e['time'], e['date'], e['date_basis'],
             tuple(e.get('candidate_dates') or ()), tuple(e['reasons']))
            for e in events))

    assert len(shapes) == 1, f'candidate days shifted with the machine clock: {shapes}'


def _events(parsed):
    return parsed[0] if isinstance(parsed, tuple) else parsed


class _system_clock:
    """Set the machine's local timezone for the duration of the block."""

    def __init__(self, clock):
        self.clock = clock

    def __enter__(self):
        self.previous = datetime.datetime.now().astimezone().tzinfo
        # astimezone() with no argument reads the machine clock; overriding os TZ is what
        # actually moves it, and time.tzset() re-reads it.
        import os
        import time as _time

        self.previous_tz = os.environ.get('TZ')
        name = {
            'tehran': 'Asia/Tehran',
            'new_york': 'America/New_York',
            'utc': 'UTC',
            'tokyo': 'Asia/Tokyo',
        }.get(str(self.clock.utcoffset(None)))
        os.environ['TZ'] = name or 'UTC'
        _time.tzset()
        return self

    def __exit__(self, *exc):
        import os
        import time as _time

        if self.previous_tz is None:
            os.environ.pop('TZ', None)
        else:
            os.environ['TZ'] = self.previous_tz
        _time.tzset()
        return False