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
    """A same-message exit does not overwrite the entry's day just because it was posted later.

    The bagheri message posts the exit and today's entry together; the pair's clocks are what
    matter, so the exit is dated by the interval and the note's extra date stays as evidence.
    """
    events = (post('m0', 'یکشنبه 14050622\nورود 0840', '2026-09-13T08:41:00+03:30')
              + post('m1', 'خروج 1715\nسه شنبه 1405/06/24\nورود 0815', '2026-09-15T08:31:00+03:30'))
    result = paired(events)
    assert result['in:08:40']['date'] == '2026-09-13'
    assert result['out:17:15']['date'] == '2026-09-13'
    assert result['out:17:15']['source_date'] == '2026-09-15'


def test_an_exit_dated_the_next_day_with_an_earlier_clock_is_left_unresolved():
    """Twenty hours between the clocks: no single day holds this shift, so no day is chosen.

    `دوشنبه 14050623 ورود 0830` is 14 September at 08:30 and `سه شنبه 14050624 خروج 0500` is
    the 15th at 05:00. On the day named that is twenty and a half hours; on the entry's day it
    is twenty-nine. Neither is a shift, so neither day is picked and both notes go to review.
    The exit is not re-dated to the entry's day, because doing so would invent a day worked
    that nobody wrote.
    """
    events = (post('m0', 'دوشنبه 14050623\nورود 0830', '2026-09-14T09:00:00+03:30')
              + post('m1', 'سه شنبه 14050624 خروج 0500', '2026-09-15T05:00:00+03:30'))
    result = paired(events)
    assert result['out:05:00']['date'] is None
    assert result['out:05:00']['status'] == 'review'
    assert result['out:05:00']['source_date'] == '2026-09-15', 'the day written stays as evidence'
    assert 'unmatched_entry' in result['in:08:30']['reasons']


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


def test_a_weekday_naming_another_day_loses_to_the_open_entry():
    """A weekday on the exit is kept as evidence, and the mismatch is reported, not hidden.

    `خروج سه شنبه 1710` says Tuesday and the open entry is Monday the 14th. The confirmed
    rule is that an undated exit closes the last entry before it, so Monday's day stands and
    Tuesday is recorded as what the note said. Both flags are kept so the disagreement is
    visible in the report rather than silently overwritten -- the exit still goes to review.
    """
    events = (post('m0', 'دوشنبه 14050623\nورود 0830', '2026-09-14T09:00:00+03:30')
              + post('m1', 'خروج سه شنبه 1710', '2026-09-15T07:59:00+03:30'))
    result = paired(events)
    assert result['out:17:10']['date'] == '2026-09-14', 'the open entry took it'
    assert result['out:17:10']['status'] == 'review', 'the disagreement is reported'
    assert 'exit_weekday_conflicts_with_entry' in result['out:17:10']['reasons']
    assert 'exit_weekday_overridden_by_pairing' in result['out:17:10']['reasons']
    assert result['in:08:30']['paired_exit_id'] == result['out:17:10']['event_id']


def test_an_exit_without_any_entry_is_reported_instead_of_dated():
    events = post('m1', 'خروج 1710', '2026-09-15T08:31:00+03:30')
    result = paired(events)
    assert result['out:17:10']['date'] is None
    assert 'unpaired_exit' in result['out:17:10']['reasons']
    assert result['out:17:10']['pairing_state'] == 'incomplete_day'


def test_an_exit_whose_clocks_sit_before_its_entry_is_not_a_late_note():
    """A note that names a day but clocks before the entry cannot be a note written late.

    `دوشنبه 14050623 / ورود 0830` is the 14th at 08:30, and `چهارشنبه 14050626 / خروج 0500`
    names the 18th at five in the morning. On the day the exit named that reads as four days and
    twenty hours; on the entry's own day it would wrap to read as twenty and a half, which is
    also impossible. Neither reading is one shift, so the exit is not that entry's exit -- the
    note claims a day the channel cannot back, and both the claim and the refusal are kept.
    """
    events = (post('m1', 'دوشنبه 14050623\nورود 0830', '2026-09-14T09:00:00+03:30')
              + post('m2', 'پنجشنبه 14050627 خروج 0500', '2026-09-18T05:00:00+03:30'))
    result = paired(events)
    exit_event = result['out:05:00']
    assert exit_event['date'] is None, 'an impossible pair was filed on a day anyway'
    assert 'no_entry_on_the_named_day' in exit_event['reasons']
    assert exit_event['status'] == 'review'
    assert 'unmatched_entry' in result['in:08:30']['reasons']


def test_a_weekday_named_on_the_exit_that_matches_the_entry_is_clean():
    events = (post('m1', 'دوشنبه 14050623\nورود 0830', '2026-09-14T09:00:00+03:30')
              + post('m2', 'خروج دوشنبه 1710', '2026-09-14T17:15:00+03:30'))
    result = paired(events)
    assert result['out:17:10']['date'] == '2026-09-14'
    assert result['out:17:10']['status'] == 'ready'
