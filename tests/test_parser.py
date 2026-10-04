from datetime import datetime
import pytest

# The days the test fixtures are written against. A bare weekday does not name a day any more
# -- only a date written in the message does, so a test that wants a day has to say which one.
# `dated` puts that date beside the weekday for the tests that are about something else, and
# leaves alone the tests that are specifically about a note with no date in it.
WEEKDAY_DATES = {
    'شنبه': '14050621',        # 12 September 2026, a Saturday
    'یکشنبه': '14050622',
    'دوشنبه': '14050623',
    'سه شنبه': '14050624',
    'چهارشنبه': '14050625',
    'پنجشنبه': '14050626',
    'جمعه': '14050627',
}


def dated(text):
    """Write the date a fixture's weekday names beside the weekday, once per message.

    `شنبه ورود 0800` becomes `شنبه 14050621 ورود 0800`. A message that already states a date is
    left exactly as written.

    A message that names only a weekday and no clock direction is also left alone: `خروج شنبه
    1800` is deliberately undated, because the rule under test is that an exit the text gave no
    day for takes the day of the entry it closes. Writing a date in would erase the case.
    """
    from attendance_sync.parser import DATE
    head = text.splitlines()[0] if text.strip() else ''
    if DATE.search(head):
        return text
    if 'خروج' in head and 'ورود' not in head:
        return text
    # Longest name first. `شنبه` is a substring of `یکشنبه`, so matching in declaration order
    # would find the Saturday inside the Sunday and write Saturday's date into a Sunday note --
    # a fixture that quietly asserts the wrong day, and tests that pass for the wrong reason.
    for word in sorted(WEEKDAY_DATES, key=len, reverse=True):
        if word in head:
            day = WEEKDAY_DATES[word]
            return '\n'.join([head.replace(word, f'{word} {day}', 1), *text.splitlines()[1:]])
    return text


def post(text, stamp='2026-09-08T08:00:00+00:00', ident='p'):
    return dict(id=ident, user_id='self', message=text, create_at=int(datetime.fromisoformat(stamp).timestamp()*1000), edit_at=0, delete_at=0, type='')


def test_explicit_date_conflict_preserves_evidence():
    from attendance_sync.parser import parse_post
    raw = 'سه شنبه 14050616\nورود ۰۷۲۰'
    events, ranges = parse_post(post(raw))
    event = events[0]
    assert event['raw_text'] == 'ورود ۰۷۲۰'
    assert event['raw_date'] == '14050616'
    assert event['date'] == '2026-09-07'
    # The date written in the note is the only date the parser may offer. `suggested_date` used
    # to carry the day the note happened to be posted, which meant the same message named a
    # different attendance day depending on the clock of the machine reading the channel -- the
    # Sunday worked in Iran came out as Saturday for a bot running on an American clock. There
    # is nothing to suggest from text that names no second day.
    assert event.get('suggested_date') is None
    assert event['time'] == '07:20'
    assert event['status'] == 'review'
    assert 'weekday_date_conflict' in event['reasons']
    assert ranges == []


@pytest.mark.parametrize('text,expected', [
    ('خروج دوشنبه 2026', [('out', '20:26')]),
    ('پنجشنبه ورود 1100 خروج 1240', [('in', '11:00'), ('out', '12:40')]),
    ('خروج سه شنبه 1900 با قطع برق', [('out', '19:00')]),
    ('خروج دوشنبه 1300 بدون ناهار', [('out', '13:00')]),
    ('ناهار 1330-1349', []),
    ('برگشت از ناهار', []),
])
def test_event_clocks(text, expected):
    from attendance_sync.parser import parse_post
    events, _ = parse_post(post(text))
    assert [(e['kind'], e['time']) for e in events] == expected


@pytest.mark.parametrize('text,reason', [
    ('> خروج 1700', 'quoted_attendance'),
    ('خروج 1700 نبود', 'negation_or_correction'),
    ('خروج برای ناهار 1300', 'break_not_attendance'),
    ('ورود 2460', 'invalid_time'),
    ('ورود', 'missing_time'),
])
def test_unsafe_events_review(text, reason):
    from attendance_sync.parser import parse_post
    events, _ = parse_post(post(text))
    assert events[0]['status'] == 'review'
    assert reason in events[0]['reasons']


@pytest.mark.parametrize('text,expected', [
    ('شنبه 14050621\nایجاد کالکشن 0000-0101', 1),
    ('شنبه 14050621\nکار 22:40–23:50', 1),
    ('شنبه 14050621\nاضافه کاری از 2300 تا 0400 صبح روز بعد', 1),
    ('ناهار 1330-1349', 0),
    ('قطعی برق 1330-1349', 0),
    ('> کار 1330-1349', 0),
    ('نسخه 1330-1349', 0),
    ('```\nکار 1330-1349\n```', 0),
])
def test_activity_ranges(text, expected):
    from attendance_sync.parser import parse_post
    _, ranges = parse_post(post(text))
    assert len(ranges) == expected
    if ranges:
        assert ranges[0]['rule'] == 'activity_range'
        assert ranges[0]['source_span']['line_index'] == 1
        assert ranges[0]['date'] == '2026-09-12'


def test_raw_date_preserves_original_digits():
    from attendance_sync.parser import parse_post
    event = parse_post(post('سه شنبه ۱۴۰۵۰۶۱۷\nورود ۰۷۲۰'))[0][0]
    assert event['raw_date'] == '۱۴۰۵۰۶۱۷'


def test_explicit_overtime_event_retained():
    from attendance_sync.parser import parse_post
    event = parse_post(post('شنبه ورود 2300 اضافه کاری'))[0][0]
    assert event['explicit_overtime']


def test_activity_negation_review():
    from attendance_sync.parser import parse_post
    item = parse_post(post('شنبه کار 1800-1900 نبود'))[1][0]
    assert item['status'] == 'review'
    assert 'negation_or_correction' in item['reasons']


def test_code_fence_excluded():
    from attendance_sync.parser import parse_post
    assert parse_post(post('```\nخروج 1700\n```')) == ([], [])
