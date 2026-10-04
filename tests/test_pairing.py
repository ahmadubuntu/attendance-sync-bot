from test_parser import post, dated
from attendance_sync.parser import parse_post

# A weekday alone is not a day. The parser used to turn one into a date by reading the posting
# instant, which made every result depend on the machine's timezone -- on a New York clock
# `شنبه` resolved to Friday. The rule now is that only a date written in the message decides
# the day, and a bare weekday stays a weekday until pairing settles it against a day the
# channel stated. `dated` in `test_parser` writes the date a fixture's weekday names, so
# these tests assert on a real day instead of on whatever clock the machine happens to keep.


def events(*texts):
    result = []
    for i, text in enumerate(texts):
        result.extend(parse_post(post(dated(text), f'2026-09-08T{8+i:02d}:00:00+00:00', str(i)))[0])
    return result


def test_latest_open_entry_wins_over_a_bare_weekday():
    from attendance_sync.pairing import pair
    # Saturday is worked and closed in one note. Sunday's entry is then open, and a bare
    # `خروج یکشنبه 1800` arrives naming Sunday -- a weekday, not a date, with no day written
    # beside it. Two independent routes reach the same day: the weekday resolver reads
    # `یکشنبه` against the days the channel states and finds exactly one, and the user's
    # rule -- an exit with no date takes the latest entry before it -- points at the same open
    # entry. The day is the Sunday that was written out, and the record says which route
    # settled it so a reader can tell an inferred day from a stated one.
    result = {e['event_id']: e for e in pair(events(
        'شنبه 14050621 ورود 0800 خروج 1700',
        'یکشنبه 14050622 ورود 0900',
        'خروج یکشنبه 1800'))}
    exit_note = result['2:0']
    entry_note = result['1:0']
    assert exit_note['paired_entry_id'] == entry_note['event_id']
    assert exit_note['date'] == entry_note['date'] == '2026-09-13'
    assert exit_note['raw_weekday'] == 'یکشنبه'
    assert exit_note['date_basis'] == 'weekday_relative'
    assert exit_note['status'] == 'ready'
    assert 'exit_weekday_overridden_by_pairing' not in exit_note['reasons']


def test_conflicting_entry_propagates():
    from attendance_sync.pairing import pair
    result = pair(events('سه شنبه 14050616\nورود 0720', 'خروج 1900'))
    assert result[-1]['status'] == 'review'
    assert 'paired_entry_review' in result[-1]['reasons']


def test_explicit_exit_date_not_overwritten():
    """An exit cannot claim a day before the entry it closes.

    `14050617 ورود 0800` is 14 September; `14050616 خروج 1700` is 13 September. The exit's own
    date is strong evidence, but it points at the day *before* the entry, so it cannot be
    closing this entry -- there was no shift open yet. The pair is refused and the exit is
    held for review, rather than being filed under the entry's day or under its own.
    """
    from attendance_sync.pairing import pair
    # The result is ordered by the decision, not by the feed, so each event is named by the
    # post it came from rather than by its position.
    result = {e['post_id']: e for e in pair(events('14050617 ورود 0800',
                                                 '14050616 خروج 1700'))}
    assert result['1']['date'] is None, 'an exit was filed on a day before its own entry'
    assert result['1']['status'] == 'review'
    assert 'unpaired_exit' in result['1']['reasons']
    # The entry is left open, not consumed by an exit that cannot belong to it.
    assert result['0']['pairing_state'] == 'incomplete_day'
    assert 'unmatched_entry' in result['0']['reasons']


def test_a_late_note_exit_stays_on_the_entry_day():
    """An exit named for a later day still belongs to the entry's day when the clocks fit.

    `14050623 ورود 0830` is 14 September and `14050625 خروج 1710` is the 16th. The author wrote
    about the day just worked, two days later. The entry's day wins, and the disagreement is
    reported rather than buried.

    The clocks are what decide this, and they have to fit on the entry's day to do it. Named the
    very next day, 08:30 to 17:10 is thirty-two hours and forty minutes -- far longer than any
    shift, and the pair goes to review instead. Named two days on it is the same eight hours
    forty minutes, just written down late.
    """
    from attendance_sync.pairing import pair
    result = {e['post_id']: e for e in pair(events('14050623 ورود 0830',
                                                 '14050625 خروج 1710'))}
    assert result['1']['date'] == '2026-09-14'
    assert 'late_note_same_day_work' in result['1']['reasons']


def test_a_night_shift_keeps_the_day_the_exit_names():
    """23:00 in and 04:00 out is the night itself, and it is credited across two days.

    `14050623 ورود 2300` is 14 September and `14050624 خروج 0400` is the 15th. Unlike a late
    note, these clocks only make one shift when the exit is dated on the day it wrote: the
    hours up to 23:59 were worked on the 14th and the hours from 00:01 on the 15th, and that
    split is what the day totals are built from. Filing the whole night on the 14th would take
    the early hours away from the day they were worked in.
    """
    from attendance_sync.pairing import pair
    result = {e['post_id']: e for e in pair(events('14050623 ورود 2300',
                                                 '14050624 خروج 0400'))}
    assert result['1']['date'] == '2026-09-15'
    assert 'exit_named_day_recent' in result['1']['reasons']
    assert 'late_note_same_day_work' not in result['1']['reasons']


def test_an_exit_too_far_from_its_entry_is_not_a_late_note():
    """The clocks decide the late-note reading, and here they refuse it.

    `14050623 ورود 0830` on the 14th against `14050626 خروج 0500` on the 16th is
    forty-four and a half hours on the day named and twenty and a half on the entry's -- both
    past any shift. Filing that exit on the 14th would invent a day worked that nobody wrote,
    so it stays unpaired and both notes go to review.
    """
    from attendance_sync.pairing import pair
    result = {e['post_id']: e for e in pair(events('14050623 ورود 0830',
                                                 '14050626 خروج 0500'))}
    assert result['1']['date'] is None
    assert 'no_entry_on_the_named_day' in result['1']['reasons']
    assert 'unmatched_entry' in result['0']['reasons']


def test_same_timestamp_cross_post_review():
    """Two notes sent in the same instant cannot be ordered, so neither is guessed at.

    Both name the same day in writing, which is not what makes this ambiguous -- what is
    ambiguous is that the text gives no way to say which of the two arrived first, so the exit
    could be closing the entry or standing on its own. Order here comes only from the posting
    instant, and it is the same instant, so there is nothing to order by.
    """
    from attendance_sync.pairing import pair
    data = events('شنبه 14050621 ورود 0800', 'شنبه 14050621 خروج 1700')
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
