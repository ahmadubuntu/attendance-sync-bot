"""The parser must not drop a real attendance note.

These cases come from the actual channel: `سه شنبه 14050707 / ورود ساعت 0900` was written on
29 Sep 2026 and silently produced zero events, because the clock matcher anchors to the start
of the text and `ساعت` sits in front of the digits. A note that is dropped here is a day the
user worked that the app never even considers registering.

Every test states one shape the parser must handle, and asserts that the event exists with
the right clock and day.
"""

import pytest

from attendance_sync.parser import parse_post

POST_AT = 1789000000000  # 2026-09-09T13:06:40+03:30


def post(message, post_id='p1', create_at=POST_AT):
    return {'id': post_id, 'message': message, 'create_at': create_at, 'user_id': 'me'}


def only_event(message):
    events, _ = parse_post(post(message))
    assert len(events) == 1, f'expected one event from {message!r}, got {len(events)}'
    return events[0]


# ---------------------------------------------------------------------------
# filler words between the marker and the clock
# ---------------------------------------------------------------------------

def test_clock_after_the_word_saat_is_read():
    """`ورود ساعت 0900` is how the user actually writes it. It must yield 09:00."""
    assert only_event('سه شنبه 14050707 \nورود ساعت 0900')['time'] == '09:00'


def test_clock_after_several_filler_words_is_read():
    assert only_event('دوشنبه 14050706\nورود ساعت 08:30')['time'] == '08:30'


def test_exit_after_the_word_saat_is_read():
    assert only_event('شنبه 14050704\nخروج ساعت 1830')['time'] == '18:30'


def test_filler_before_the_clock_does_not_change_the_day():
    """The day comes from the date in the first line, whatever the second line says."""
    event = only_event('سه شنبه 14050707 \nورود ساعت 0900')
    assert event['date'] == '2026-09-29'


@pytest.mark.parametrize('filler', ['ساعت', 'ساعت حدود', 'حدود', 'حدوداکنون', ''])
def test_any_filler_prefix_before_the_clock_is_tolerated(filler):
    message = f'دوشنبه 14050706\nورود {filler} 0700'.rstrip()
    assert only_event(message)['time'] == '07:00'


# ---------------------------------------------------------------------------
# the explicit date still wins over the filler
# ---------------------------------------------------------------------------

def test_explicit_date_with_slashes_is_read():
    event = only_event('دوشنبه 1405/06/30\nورود 0710')
    assert (event['date'], event['time']) == ('2026-09-21', '07:10')


def test_compact_date_is_read():
    event = only_event('چهارشنبه 14050625\nورود 0800')
    assert (event['date'], event['time']) == ('2026-09-16', '08:00')


# ---------------------------------------------------------------------------
# a note with no clock is still a fact worth keeping
# ---------------------------------------------------------------------------

def test_entry_without_a_clock_is_kept_as_review():
    """A note with a day but no time is not attendance; it must be flagged, not dropped."""
    events, _ = parse_post(post('دوشنبه 14050706\nورود'))
    assert len(events) == 1
    assert events[0]['time'] is None
    assert 'missing_time' in events[0]['reasons']


def test_prose_with_the_word_entry_is_not_attendance():
    """`از ناهار برگشتم` has no marker, so it is not an event."""
    events, _ = parse_post(post('از ناهار برگشتم'))
    assert events == []


def test_a_sentence_mentioning_entry_is_still_attendance():
    """`فعلا خروج میزنم` states an exit without a clock: kept, flagged, never invented."""
    events, _ = parse_post(post('لپتاپ با بی برقی خاموش شد، فعلا خروج میزنم'))
    assert len(events) == 1
    assert events[0]['kind'] == 'out'
    assert events[0]['time'] is None