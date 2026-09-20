"""The user's night rule: the earlier day keeps 23:59 and the next day owns 00:01 onward.

One minute at 00:00 is deliberately unassigned, because the attendance system refuses 00:00 as
an end clock (proven against the live form: a 19:00-00:00 request was only accepted as
19:00-23:59). The total registered for a night shift is therefore one minute short of the raw
span per midnight crossed, and that is the accepted cost of a form that will actually save.
"""
from datetime import datetime, timedelta

import pytest

from attendance_sync.intervals import split_midnights

TEHRAN = '+03:30'


def parts(start_raw, end_raw):
    return split_midnights(datetime.fromisoformat(start_raw + TEHRAN),
                           datetime.fromisoformat(end_raw + TEHRAN))


def minutes(start, end):
    return int((end - start).total_seconds()) // 60


def test_evening_shift_keeps_2359_on_the_first_day():
    segments = parts('2026-09-12T19:00:00', '2026-09-13T04:00:00')
    assert [minutes(a, b) for a, b in segments] == [300, 239]
    assert segments[0][0].date().isoformat() == '2026-09-12'
    assert segments[0][1].strftime('%H:%M') == '00:00'
    assert segments[1][0].strftime('%H:%M') == '00:01'


def test_the_first_minute_of_a_night_segment_is_never_returned():
    for start, end in (('2026-09-12T19:00:00', '2026-09-13T04:00:00'),
                       ('2026-09-12T17:30:00', '2026-09-13T01:00:00'),
                       ('2026-09-12T23:59:00', '2026-09-13T00:30:00')):
        for part_start, _part_end in parts(start, end):
            if part_start.date().isoformat() != start[:10]:
                assert part_start.strftime('%H:%M') == '00:01', 'a night part must start at 00:01'


def test_an_assignable_total_is_one_minute_short_of_the_span_per_midnight():
    segments = parts('2026-09-12T19:00:00', '2026-09-13T04:00:00')
    assert sum(minutes(a, b) for a, b in segments) == 539


def test_a_shift_crossing_two_midnights_is_split_three_ways():
    segments = parts('2026-09-12T19:00:00', '2026-09-14T02:00:00')
    assert len(segments) == 3
    assert [part_start.date().isoformat() for part_start, _ in segments] == [
        '2026-09-12', '2026-09-13', '2026-09-14']
    assert segments[1][0].strftime('%H:%M') == '00:01'
    assert segments[2][0].strftime('%H:%M') == '00:01'


def test_a_daytime_interval_is_untouched():
    segments = parts('2026-09-12T08:30:00', '2026-09-12T17:10:00')
    assert len(segments) == 1
    assert minutes(*segments[0]) == 520


def test_an_invalid_interval_still_raises():
    with pytest.raises(ValueError):
        split_midnights(datetime.fromisoformat('2026-09-12T17:00:00' + TEHRAN),
                        datetime.fromisoformat('2026-09-12T08:00:00' + TEHRAN))
