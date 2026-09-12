from test_parser import post
from attendance_sync.parser import parse_post


def events(*texts):
    result = []
    for i, text in enumerate(texts):
        result.extend(parse_post(post(text, f'2026-09-08T{8+i:02d}:00:00+00:00', str(i)))[0])
    return result


def test_latest_unmatched_and_wrong_exit_weekday():
    from attendance_sync.pairing import pair
    result = pair(events('شنبه ورود 0800', 'شنبه خروج 1700', 'یکشنبه ورود 0900', 'خروج شنبه 1800'))
    assert result[-1]['paired_entry_id'] == result[-2]['event_id']
    assert result[-1]['date'] == result[-2]['date']
    assert result[-1]['raw_weekday'] == 'شنبه'
    assert result[-1]['date_basis'] == 'paired_entry'
    assert result[-1]['status'] == 'ready'
    assert 'exit_weekday_overridden_by_pairing' in result[-1]['reasons']


def test_conflicting_entry_propagates():
    from attendance_sync.pairing import pair
    result = pair(events('سه شنبه 14050616\nورود 0720', 'خروج 1900'))
    assert result[-1]['status'] == 'review'
    assert 'paired_entry_review' in result[-1]['reasons']


def test_explicit_exit_date_not_overwritten():
    from attendance_sync.pairing import pair
    result = pair(events('14050617 ورود 0800', '14050616 خروج 1700'))
    assert result[-1]['date'] == '2026-09-07'
    assert 'explicit_exit_date_conflict' in result[-1]['reasons']


def test_same_timestamp_cross_post_review():
    from attendance_sync.pairing import pair
    data = events('شنبه ورود 0800', 'شنبه خروج 1700')
    data[1]['posted_at'] = data[0]['posted_at']
    assert all(e['status'] == 'review' for e in pair(data))


def test_duplicate_fetch_and_latest_open():
    from attendance_sync.pairing import pair
    data = events('شنبه ورود 0800', 'یکشنبه ورود 0900', 'خروج 1700')
    result = pair(data + [data[-1]])
    assert len(result) == 3
    assert result[-1]['paired_entry_id'] == result[-2]['event_id']
    assert 'unmatched_entry' in result[0]['reasons']


def test_same_message_and_unpaired():
    from attendance_sync.pairing import pair
    result = pair(events('پنجشنبه ورود 1100 خروج 1240'))
    assert result[-1]['paired_entry_id'] == result[0]['event_id']
    assert pair(events('خروج شنبه 1700'))[0]['date'] is None
