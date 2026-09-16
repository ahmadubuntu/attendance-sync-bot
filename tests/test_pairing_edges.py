"""Edge cases of the confirmed pairing rule: review inheritance and single-clock days.

The user's rule is "after each entry and before the next entry, any exit belongs to the
first (earlier) entry". A pairing that is not determined by clock and order (a missing or
invalid clock, a date conflict) must never become ready, and a day that holds exactly one
clock must still be visible as an unresolved remainder instead of disappearing.
"""
from test_parser import post
from test_pairing import events


def test_entry_without_a_clock_keeps_its_pair_in_review():
    from attendance_sync.pairing import pair
    result = pair(events('شنبه ورود', 'خروج 1700'))
    entry, exit_event = result[0], result[-1]
    assert exit_event['paired_entry_id'] == entry['event_id']
    assert entry['status'] == 'review' and exit_event['status'] == 'review'
    assert 'missing_time' in entry['reasons']
    assert 'paired_entry_review' in exit_event['reasons']


def test_invalid_clock_pair_stays_review_on_both_sides():
    from attendance_sync.pairing import pair
    result = pair(events('شنبه ورود 2460', 'خروج 1700'))
    assert [event['status'] for event in result] == ['review', 'review']
    assert 'invalid_time' in result[0]['reasons']
    assert 'paired_entry_review' in result[-1]['reasons']


def test_date_conflict_pair_stays_review_on_both_sides():
    from attendance_sync.pairing import pair
    result = pair(events('سه شنبه 14050616\nورود 0720', 'خروج 1900'))
    assert [event['status'] for event in result] == ['review', 'review']
    assert 'paired_entry_review' in result[-1]['reasons']
    assert 'paired_exit_review' in result[0]['reasons']


def test_review_pair_never_becomes_a_ready_interval_with_a_missing_start():
    from attendance_sync.intervals import build_intervals
    from attendance_sync.pairing import pair
    paired = pair(events('شنبه ورود', 'خروج 1700'))
    items = build_intervals(paired, [])
    exact = [item for item in items if item['origin'] == 'paired_events']
    assert exact[0]['status'] == 'review'
    assert all(item['start_at'] or item['status'] == 'review' for item in exact)


def test_single_exit_day_produces_a_visible_unresolved_remainder():
    from attendance_sync.pairing import pair
    result = pair(events('خروج دوشنبه 1700'))
    event = result[0]
    assert event['date'] is None and event['source_date'] == '2026-09-07'
    assert event['status'] == 'review'
    assert set(event['reasons']) >= {'unpaired_exit', 'incomplete_day'}
    assert event['missing_counterpart_id'] is None


def test_single_entry_day_produces_a_visible_unresolved_remainder():
    from attendance_sync.pairing import pair
    result = pair(events('شنبه ورود 0800'))
    entry = result[0]
    assert entry['pairing_state'] == 'incomplete_day'
    assert set(entry['reasons']) >= {'unmatched_entry', 'unmatched_event'}
    assert 'incomplete_day' not in entry['reasons']


def test_incomplete_day_remainder_keeps_its_source_ids_and_allocation_blocked():
    from attendance_sync.intervals import build_intervals
    from attendance_sync.pairing import pair
    paired = pair(events('خروج دوشنبه 1700'))
    items = build_intervals(paired, [])
    remainder = items[0]
    assert remainder['status'] == 'review'
    assert remainder['start_at'] is None and remainder['end_at'] is None
    assert remainder['source_ids'] == [paired[0]['event_id']]
    assert remainder['accounting_days'] == ['2026-09-07']
    assert set(remainder['reasons']) >= {'unpaired_exit', 'incomplete_day'}


def test_a_full_pair_is_still_exactly_one_ready_interval():
    from attendance_sync.intervals import build_intervals
    from attendance_sync.pairing import pair
    paired = pair(events('شنبه ورود 0800 خروج 1700'))
    items = build_intervals(paired, [])
    assert len(items) == 1
    assert items[0]['status'] == 'ready'
    assert items[0]['start_at'] == '2026-09-05T08:00:00+03:30'
    assert items[0]['end_at'] == '2026-09-05T17:00:00+03:30'
    assert items[0]['accounting_days'] == ['2026-09-05']
    assert 'incomplete_day' not in items[0]['reasons']
    assert paired[0]['missing_counterpart_id'] is None


def test_a_pair_in_review_is_excluded_from_allocation():
    from attendance_sync.allocation import allocate
    from attendance_sync.intervals import build_intervals
    from attendance_sync.pairing import pair
    items = build_intervals(pair(events('شنبه ورود', 'خروج 1700')), [])
    allocate(items)
    assert all(item['allocated_minutes'] == 0 for item in items)
    assert all(item['submission_eligible'] is False for item in items)


def test_a_missing_clock_pair_is_never_a_ready_interval_with_a_missing_start():
    from attendance_sync.intervals import build_intervals
    from attendance_sync.pairing import pair
    items = build_intervals(pair(events('شنبه ورود', 'خروج 1700')), [])
    exact = [item for item in items if item['origin'] == 'paired_events']
    assert exact[0]['status'] == 'review'
    assert exact[0]['start_at'] is None
    assert 'paired_entry_review' in exact[0]['reasons']


def test_a_pair_spanning_midnight_still_splits_and_stays_overnight():
    from attendance_sync.intervals import build_intervals
    from attendance_sync.pairing import pair
    paired = pair(events('شنبه ورود 2300', 'خروج 0400'))
    items = build_intervals(paired, [])[0]
    assert items['status'] == 'ready' and items['end_day_offset'] == 1
    assert items['accounting_days'] == ['2026-09-05', '2026-09-06']
    assert items['start_at'] == '2026-09-05T23:00:00+03:30'
    assert items['end_at'] == '2026-09-06T04:00:00+03:30'


def test_an_entry_after_an_exit_becomes_overtime_and_stays_ready():
    """The confirmed 'return after exit is overtime' rule must not regress to review."""
    from attendance_sync.intervals import build_intervals
    from attendance_sync.pairing import pair
    paired = pair(events('شنبه ورود 0800 خروج 1200', 'شنبه ورود 1300 خروج 1900'))
    items = build_intervals(paired, [])
    assert [item['status'] for item in items] == ['ready', 'ready']
    assert [item['explicit_overtime'] for item in items] == [False, True]
    assert 'incomplete_day' not in items[1]['reasons']


def test_an_entry_no_later_clock_is_not_a_single_clock_day():
    """Two entries with a missing start clock and no exit are not an 'incomplete day' note."""
    from attendance_sync.pairing import pair
    result = pair(events('شنبه ورود', 'خروج 1200'))
    assert result[-1]['paired_entry_id'] == result[0]['event_id']
    assert result[0]['pairing_state'] == 'review'
    assert 'incomplete_day' not in result[0]['reasons']


def test_a_return_after_an_exit_does_not_close_the_first_pair():
    """Two entries and one exit: the exit closes the later entry, the earlier one is a remainder."""
    from attendance_sync.pairing import pair
    result = pair(events('شنبه ورود 0800', 'شنبه ورود 1300', 'خروج 1900'))
    assert result[-1]['paired_entry_id'] == result[1]['event_id']
    assert result[0]['pairing_state'] == 'incomplete_day'
    assert 'unmatched_entry' in result[0]['reasons']
    assert 'missing_counterpart_id' in result[0]


def test_a_lone_review_event_is_visible_without_the_unmatched_flag():
    from attendance_sync.pairing import pair
    result = pair(events('> خروج 1700'))
    assert result[0]['pairing_eligible'] is False
    assert 'unmatched_event' not in result[0]['reasons']
    assert result[0]['status'] == 'review'
