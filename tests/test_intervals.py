from datetime import datetime
from attendance_sync.parser import parse_post
from attendance_sync.pairing import pair
from test_pairing import events
from test_parser import post


def test_overnight_interval_and_midnight_split():
    from attendance_sync.intervals import build_intervals, split_midnights
    _, ranges = parse_post(post('شنبه 14050621\nاضافه کاری 2300-0400'))
    interval = build_intervals([], ranges)[0]
    assert interval['status'] == 'ready'
    assert interval['end_day_offset'] == 1
    assert interval['end_at'] == '2026-09-13T04:00:00+03:30'
    parts = split_midnights(datetime.fromisoformat(interval['start_at']), datetime.fromisoformat(interval['end_at']))
    assert [int((b-a).total_seconds())//60 for a,b in parts] == [60,240]
    assert interval['explicit_overtime']


def test_event_overtime_survives_midnight_and_next_day_entry_regular():
    from attendance_sync.intervals import build_intervals
    data = pair(events('شنبه ورود 2300 اضافه کاری', 'خروج 0400', 'یکشنبه ورود 0800 خروج 1600'))
    items = build_intervals(data, [])
    assert items[0]['explicit_overtime']
    assert not items[1]['explicit_overtime']


def test_unsafe_exit_blocks_entry_as_well():
    data = pair(events('شنبه ورود 0800', 'خروج 1700 نبود'))
    assert all(e['status']=='review' for e in data)


def test_return_gaps_and_pairs_retained():
    from attendance_sync.intervals import build_intervals
    data = pair(events('شنبه ورود 0800 خروج 1200', 'شنبه ورود 1300 خروج 1900'))
    items = build_intervals(data, [])
    assert len(items) == 2
    assert [int((datetime.fromisoformat(i['end_at'])-datetime.fromisoformat(i['start_at'])).total_seconds())//60 for i in items] == [240,360]
    assert items[1]['explicit_overtime']


def test_equal_clocks_review():
    from attendance_sync.intervals import build_intervals
    _, ranges = parse_post(post('شنبه کار 0400-0400'))
    item = build_intervals([], ranges)[0]
    assert item['status'] == 'review'
    assert 'ambiguous_duration' in item['reasons']


def test_exact_duplicate_merges_and_overlap_blocks():
    from attendance_sync.intervals import build_intervals
    data = pair(events('شنبه ورود 0800 خروج 1200'))
    _, ranges = parse_post(post('شنبه کار 0800-1200\nکار 1100-1300'))
    items = build_intervals(data, ranges)
    assert len(items) == 2
    assert len(items[0]['source_ids']) == 3
    assert all(i['status'] == 'review' and 'overlapping_intervals' in i['reasons'] for i in items)
