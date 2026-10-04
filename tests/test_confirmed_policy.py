"""Synthetic source-bound corrections and confirmed work policy regressions."""
from datetime import datetime
import hashlib
import json
from copy import deepcopy
from test_parser import dated
from test_parser import post as _post
from attendance_sync.report import build_report


def post(text, stamp='2026-09-08T08:00:00+00:00', ident='p'):
    """A message that names a day, the way the fixtures in this file all assume.

    A bare weekday used to be enough to name the day, read off the posting clock and therefore
    dependent on the machine's timezone. It is not any more: only a date written in the message
    decides the day. `dated` writes in the date each fixture's weekday stands for, so these
    tests assert on a real day. A fixture that genuinely needs an undated note calls `_post`.
    """
    return _post(dated(text), stamp, ident)



def report(rows, **kwargs):
    return build_report(rows, 'self', datetime.fromisoformat('2026-09-01T00:00:00+03:30'),
                        datetime.fromisoformat('2026-09-20T23:59:00+03:30'), **kwargs)


def signature(row):
    fields = {k: row.get(k, 0) for k in ('id', 'create_at', 'edit_at', 'message')}
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def override(row):
    return {'schema_version': 1, 'date_corrections': [dict(post_id=row['id'], source_fingerprint=signature(row),
            raw_date='14050622', target_date='2026-09-14', confirmed_by='user')]}


def test_confirmed_correction_precedes_pairing_preserves_raw_evidence():
    rows = [post('دوشنبه 14050622\nورود 0810', '2026-09-14T08:10:00+03:30', 'synthetic-in'),
            post('خروج 1710', '2026-09-14T17:10:00+03:30', 'synthetic-out')]
    before = deepcopy(rows)
    result = report(rows, corrections=override(rows[0]))
    entry, exit_event = result['events']
    assert entry['raw_date'] == '14050622'
    assert entry['date'] == exit_event['date'] == '2026-09-14'
    assert entry['correction']['confirmed_by'] == 'user'
    assert entry['source_fingerprint'] == signature(rows[0])
    assert result['intervals'][0]['status'] == 'ready'
    assert result['intervals'][0]['accounting_days'] == ['2026-09-14']
    assert result['kasra_check'] == 'not_performed'
    assert rows == before


def test_stale_override_remains_review_even_when_date_is_no_longer_conflicting():
    row = post('دوشنبه 14050622\nورود 0810 خروج 1710', '2026-09-14T17:10:00+03:30', 'synthetic')
    config = override(row)
    row['message'] = row['message'].replace('14050622', '14050623')
    result = report([row], corrections=config)
    assert result['segments'] == []
    assert 'stale_date_correction' in result['events'][0]['reasons']


def test_invalid_or_unconfirmed_correction_is_rejected():
    import pytest
    row = post('دوشنبه 14050622\nورود 0810', '2026-09-14T08:10:00+03:30')
    config = override(row)
    config['date_corrections'][0]['confirmed_by'] = 'software'
    with pytest.raises(ValueError):
        report([row], corrections=config)


def test_whole_post_work_context_and_completed_clock_anchor_range():
    row = post('شنبه 14050621\nرفع مشکل سرویس\n22:10-23:20', '2026-09-12T23:25:00+03:30')
    result = report([row])
    activity = result['ranges'][0]
    assert activity['status'] == 'ready'
    assert activity['date_basis'] == 'explicit_jalali'
    assert activity['date'] == '2026-09-12'
    assert 'inferred_date_from_completed_range' not in activity['warnings']
    assert activity['work_context'] == ['رفع مشکل سرویس']
    assert sum(s['duration_minutes'] for s in result['segments']) == 70


def test_unanchored_overnight_completed_morning_uses_previous_start_day():
    result = report([post('شنبه 14050621\nاضافه کاری 2300-0400', '2026-09-13T04:05:00+03:30')])
    activity = result['ranges'][0]
    assert activity['date'] == '2026-09-12'
    assert activity['date_basis'] == 'explicit_jalali'
    assert activity['status'] == 'ready'
    assert [s['duration_minutes'] for s in result['segments']] == [60, 239]
    assert all(s['category'] == 'overtime_remote' for s in result['segments'])


def test_night_range_after_closed_attendance_is_overtime_despite_unused_quota():
    result = report([post('شنبه 14050621 ورود 0900 خروج 1200', '2026-09-12T12:00:00+03:30', 'day'),
                     post('شنبه 14050621\nرفع مشکل\n22:10-23:20', '2026-09-12T23:25:00+03:30', 'night')])
    night = next(i for i in result['intervals'] if i['origin'] == 'activity_range')
    assert night['explicit_overtime'] is True
    assert 'return_after_exit' in night['reasons']
    assert [(s['category'], s['duration_minutes']) for s in result['segments'] if s['parent_interval_id'] == night['interval_id']] == [('overtime_remote', 70)]


def test_current_open_entry_is_information_not_review_or_quota_blocker():
    result = report([post('شنبه ورود 0810', '2026-09-12T08:10:00+03:30')],
                    as_of=datetime.fromisoformat('2026-09-12T10:00:00+03:30'))
    event = result['events'][0]
    assert event['status'] == 'open_day'
    assert event['submission_eligible'] is False
    assert event['reasons'] == []
    assert result['intervals'][0]['status'] == 'open_day'
    assert result['allocation_blockers'] == []
    assert result['review_count'] == 0 and result['open_day_count'] == 1
    assert result['segments'] == []


def test_current_closed_work_is_deferred_and_overnight_previous_day_stays_allocated():
    result = report([post('جمعه 14050620 اضافه کاری 2300-0400', '2026-09-12T04:00:00+03:30', 'night'),
                     post('شنبه 14050621 کار 0800-0900', '2026-09-12T09:00:00+03:30', 'day')],
                    as_of=datetime.fromisoformat('2026-09-12T10:00:00+03:30'))
    assert [(s['local_date'], s['duration_minutes']) for s in result['segments']] == [('2026-09-11', 60)]
    assert [s['duration_minutes'] for s in result['deferred_spans']] == [239, 60]
    assert all(s['submission_eligible'] is False for s in result['deferred_spans'])
    assert result['withheld_spans'] == [] and result['review_count'] == 0
    for item in result['intervals']:
        assert item['expected_minutes'] == item['allocated_minutes'] + item['withheld_minutes'] + item['deferred_minutes']
    assert result['intervals'][1]['submission_eligible'] is False


def test_a_conflict_the_neighbourhood_settles_is_filed_and_its_history_kept():
    """`دوشنبه 14050622` says Monday but writes Sunday the 13th, and Monday the 13th is not a Monday.

    The note before it also writes the 13th and the note after writes the 15th, so the day is
    settled by its neighbours rather than left in review. That is the rule: a disagreement is
    only a problem when the days around it cannot say which day is meant. What the note
    actually said stays on the event, so the disagreement is visible in the report instead of
    being silently overwritten.
    """
    result = report([
        post('یکشنبه 14050622 ورود 0800 خروج 1700', '2026-09-13T17:00:00+03:30', 'before'),
        post('دوشنبه 14050622 ورود 0800 خروج 1700', '2026-09-14T17:00:00+03:30', 'conflict'),
        post('سه شنبه 14050624 ورود 0800 خروج 1700', '2026-09-15T17:00:00+03:30', 'after')])
    conflict = next(e for e in result['events'] if e['post_id'] == 'conflict'
                    and e['kind'] == 'in')
    assert conflict['date'] == '2026-09-13', 'the neighbourhood settled the day'
    assert conflict['status'] == 'ready', 'a settled day is not blocked'
    assert conflict['raw_date'] == '14050622', 'what the note wrote is kept as evidence'
    assert conflict['date_basis'] == 'explicit_jalali'
    assert result['date_discrepancy_count'] == 0


def test_a_conflict_no_neighbour_can_settle_waits_for_the_user():
    """With nothing around it to say which day was meant, the day is not chosen.

    `چهارشنبه 14050623` names Wednesday but writes the 14th, which is a Monday. The days on
    either side are Tuesdays, and a Tuesday either side does not say whether this note belongs
    to the Monday the 14th or to the Wednesday the 16th, so nothing settles it: the event keeps
    every reading it has and goes to review.
    """
    result = report([
        post('چهارشنبه 14050623 ورود 0800 خروج 1700', '2026-09-14T17:00:00+03:30', 'conflict'),
        post('سه شنبه 14050620 کار 0800-0900', '2026-09-08T09:00:00+03:30', 'before'),
        post('سه شنبه 14050627 کار 0800-0900', '2026-09-18T09:00:00+03:30', 'after')])
    conflict = next(e for e in result['events'] if e['post_id'] == 'conflict'
                    and e['kind'] == 'in')
    assert conflict['status'] == 'review', 'an unsettled day waits for the user'
    assert 'weekday_date_conflict' in conflict['reasons']
    assert result['date_discrepancy_count'] >= 1
    assert result['date_discrepancy_count'] == len(result['date_discrepancies'])
