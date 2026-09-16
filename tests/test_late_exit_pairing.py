"""The asked-for rule: an exit closes the entry it follows, wherever it was written.

A late-that-day or next-morning exit carries no date of its own, so pairing has to attribute
the exit's clock to the entry's day instead of dropping it for lack of a date. The cases here
are the ones the user named, including the shape that used to fail: an exit written on the next
working day, followed by the new day's entry in another message.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from attendance_sync.pairing import pair
from attendance_sync.parser import parse_post

TEHRAN = ZoneInfo('Asia/Tehran')


def post(post_id, message, created, event_index_start=0):
    stamp = datetime.fromisoformat(created).astimezone(TEHRAN)
    events, _ = parse_post({'id': post_id, 'create_at': int(stamp.timestamp() * 1000),
                            'message': message, 'edit_at': 0})
    for offset, event in enumerate(events):
        event['event_index'] = event_index_start + offset
    return events


def paired(events):
    result = pair(events)
    for event in result:
        event.setdefault('paired_entry_id', None)
        event.setdefault('paired_exit_id', None)
    return {event['kind'] + ':' + (event['time'] or '-'): event for event in result}


def test_late_evening_exit_closes_that_days_entry():
    """An exit posted late the same night belongs to that day, not to the posting day."""
    events = (post('m1', 'دوشنبه 14050623\nورود 0830', '2026-09-14T09:00:00+03:30')
              + post('m2', 'خروج 2300', '2026-09-14T23:05:00+03:30'))
    result = paired(events)
    assert result['out:23:00']['date'] == '2026-09-14'
    assert result['out:23:00']['status'] == 'ready'
    assert result['out:23:00']['reasons'] == []


def test_an_exit_after_midnight_closes_the_entry_it_follows():
    """Evening entry, exit written just after midnight: one interval on the entry's day."""
    events = (post('m1', 'شنبه 14050621\nورود 2300', '2026-09-12T23:05:00+03:30')
              + post('m2', 'خروج 0400', '2026-09-13T04:05:00+03:30'))
    result = paired(events)
    assert result['out:04:00']['date'] == '2026-09-12'
    assert result['out:04:00']['status'] == 'ready'


def test_exit_written_inside_the_next_morning_note_is_still_todays_exit():
    """An exit posted with an entry dated today belongs to today, not to the previous entry.

    A closed exit keeps its own explicit date and is flagged for review rather than being
    re-pointed at whichever entry happens to be open.
    """
    events = (post('m0', 'یکشنبه 14050622\nورود 0840', '2026-09-13T08:41:00+03:30')
              + post('m1', 'خروج 1715\nسه شنبه 1405/06/24\nورود 0815', '2026-09-15T08:31:00+03:30'))
    result = paired(events)
    assert result['out:17:15']['date'] == result['in:08:15']['date']
    assert 'explicit_exit_date_conflict' in result['out:17:15']['reasons']
    assert result['out:17:15']['status'] == 'review'


def test_exit_without_a_named_day_on_the_next_morning_closes_yesterdays_entry():
    """A bare morning exit written before today's entry closes the entry still open.

    Nothing in this message dates the exit, so the open entry from the day before takes it, and
    today's own entry stays open for its own exit.
    """
    events = (post('m0', 'دوشنبه 14050623\nورود 0830', '2026-09-14T09:00:00+03:30')
              + post('m1', 'خروج 1710', '2026-09-15T07:59:00+03:30')
              + post('m2', 'سه شنبه 1405/06/24\nورود 0800', '2026-09-15T08:00:00+03:30'))
    result = paired(events)
    assert result['out:17:10']['date'] == '2026-09-14'
    assert result['in:08:30']['paired_exit_id'] == result['out:17:10']['event_id']
    assert result['out:17:10']['status'] == 'ready'


def test_a_named_day_on_the_exit_is_recorded_while_the_pairing_stays_on_the_open_entry():
    """A weekday on the exit is kept as evidence, and the mismatch is reported, not resolved.

    The exit still closes the open entry (that is the confirmed rule), but the day it names is
    preserved so the discrepancy is visible instead of silently overwritten.
    """
    events = (post('m0', 'دوشنبه 14050623\nورود 0830', '2026-09-14T09:00:00+03:30')
              + post('m1', 'خروج سه شنبه 1710', '2026-09-15T07:59:00+03:30'))
    result = paired(events)
    assert result['out:17:10']['source_date'] == '2026-09-15'
    assert result['out:17:10']['date'] == '2026-09-14'
    assert 'exit_weekday_overridden_by_pairing' in result['out:17:10']['reasons']
    assert result['in:08:30']['paired_exit_id'] == result['out:17:10']['event_id']


def test_an_exit_without_any_entry_is_reported_instead_of_dated():
    events = post('m1', 'خروج 1710', '2026-09-15T08:31:00+03:30')
    result = paired(events)
    assert result['out:17:10']['date'] is None
    assert 'unpaired_exit' in result['out:17:10']['reasons']
    assert result['out:17:10']['pairing_state'] == 'incomplete_day'


def test_explicit_date_that_contradicts_the_pairing_still_requires_review():
    events = (post('m1', 'دوشنبه 14050623\nورود 0830', '2026-09-14T09:00:00+03:30')
              + post('m2', 'چهارشنبه 14050625 خروج 1710', '2026-09-15T08:31:00+03:30'))
    result = paired(events)
    assert result['out:17:10']['date'] == '2026-09-16'
    assert 'explicit_exit_date_conflict' in result['out:17:10']['reasons']
    assert result['out:17:10']['status'] == 'review'


def test_a_weekday_named_on_the_exit_that_matches_the_entry_is_clean():
    events = (post('m1', 'دوشنبه 14050623\nورود 0830', '2026-09-14T09:00:00+03:30')
              + post('m2', 'خروج دوشنبه 1710', '2026-09-14T17:15:00+03:30'))
    result = paired(events)
    assert result['out:17:10']['date'] == '2026-09-14'
    assert result['out:17:10']['status'] == 'ready'
