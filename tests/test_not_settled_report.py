"""Pin the report side of the not-settled rule the user asked for.

A day whose Kasra coverage does not match the evidenced work is named in the period report,
so the user can take it to HR with the monthly report instead of discovering it later.
"""
from datetime import datetime

from attendance_sync.kasra_reconcile import CREDIT_TYPE_OVERTIME, STATUS_PENDING
from attendance_sync.period_report import build_period, render_markdown


def post(message, created, post_id):
    stamp = datetime.fromisoformat(created)
    return {'id': post_id, 'user_id': 'self', 'create_at': int(stamp.timestamp() * 1000),
            'edit_at': 0, 'message': message}


def base_report():
    from attendance_sync.report import build_report
    rows = [post('چهارشنبه 14050625\nورود 0800', '2026-09-16T07:59:00+03:30', 'a'),
            post('خروج چهار شنبه 1845', '2026-09-16T18:50:00+03:30', 'b'),
            post('رفع مشکل سرویس\n1930-2200', '2026-09-16T22:05:00+03:30', 'c')]
    return build_report(rows, 'self', datetime.fromisoformat('2026-09-10T00:00:00+03:30'),
                        datetime.fromisoformat('2026-09-20T23:59:00+03:30'))


def kasra_with_manual_document():
    """A manual 12:00-17:00 overtime document that overlaps the day differently."""
    return {'1405/06/25': {'jalali_date': '1405/06/25', 'day_type': None,
                           'report_regular_minutes': 0, 'report_overtime_minutes': 0,
                           'pending_markers': [],
                           'documents': [{'doc_id': '63009', 'credit_type': CREDIT_TYPE_OVERTIME,
                                          'start_time': '12:00', 'end_time': '17:00', 'minutes': 300,
                                          'active': True, 'status_id': STATUS_PENDING,
                                          'day': '1405/06/25'}]}}


def test_a_day_kasra_covers_differently_is_named_in_the_report():
    period = build_period(base_report(), start='1405/06/25', end='1405/06/25',
                          kasra=kasra_with_manual_document())
    day = period['days'][0]
    computed = day['regular_minutes'] + day['overtime_minutes']
    held = (day['registered_regular_minutes'] + day['registered_overtime_minutes']
            + day['pending_regular_minutes'] + day['pending_overtime_minutes'])
    assert held < computed, (held, computed)
    assert day['not_settled'] is True
    assert period['not_settled_days'] == ['1405/06/25']


def test_the_markdown_names_the_day_for_hr():
    period = build_period(base_report(), start='1405/06/25', end='1405/06/25',
                          kasra=kasra_with_manual_document())
    text = render_markdown(period)
    assert '## Days not fully settled with Kasra' in text
    assert '1405/06/25' in text
    assert 'Tell HR' in text


def test_a_fully_covered_day_is_not_flagged():
    period = build_period(base_report(), start='1405/06/25', end='1405/06/25', kasra=None)
    assert period['not_settled_days'] == []
    text = render_markdown(period)
    assert 'None: for every day of the range' in text
