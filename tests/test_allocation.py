from datetime import date
import pytest
from attendance_sync.intervals import resolve


@pytest.mark.parametrize('day,quota', [(12,540),(13,540),(14,540),(15,540),(16,480),(17,0),(18,0)])
def test_daily_quota(day, quota):
    from attendance_sync.allocation import quota_minutes
    assert quota_minutes(date(2026,9,day)) == quota


def test_regular_intervals_share_daily_quota_without_gap():
    from attendance_sync.allocation import allocate
    intervals = [resolve('a',['a'],'2026-09-12','08:00','12:00','activity_range',False,[],'ready'),resolve('b',['b'],'2026-09-12','13:00','19:00','activity_range',False,[],'ready')]
    segments = allocate(intervals)
    assert [s['duration_minutes'] for s in segments]==[240,300,60]


def test_review_interval_blocks_quota_for_affected_day():
    from attendance_sync.allocation import allocate
    intervals = [resolve('a',['a'],'2026-09-12','08:00','12:00','activity_range',False,[],'review'),resolve('b',['b'],'2026-09-12','13:00','19:00','activity_range',False,[],'ready')]
    assert allocate(intervals)==[]


def test_allocation_preserves_overtime_and_daily_quota():
    from attendance_sync.allocation import allocate
    intervals = [resolve('a', ['a'], '2026-09-12', '23:00', '04:00', 'activity_range', True, [], 'ready'),
                 resolve('b', ['b'], '2026-09-13', '08:20', '19:40', 'paired_events', False, [], 'ready')]
    segments = allocate(intervals)
    assert [(s['category'],s['duration_minutes']) for s in segments] == [('overtime_remote',60),('overtime_remote',240),('regular_remote',540),('overtime_remote',140)]
    assert segments[2]['end_at'] == '2026-09-13T17:20:00+03:30'
