"""Pairing must be decided by the date written in the text, never by the posting clock.

These tests are written against tests/fixtures_real_posts.py, which is copied verbatim from
the authenticated Mattermost channel. Each test states one rule from the user's brief:

* the attended day comes from the text (an explicit Jalali date, else the weekday it names);
* the day the note was sent is evidence of order only, never of which day was worked;
* an exit that names no day belongs to the latest earlier entry;
* when two entries for one day cannot be told apart, the day is left for review rather than
  guessed.
"""

import datetime

import pytest

from fixtures_real_posts import TEHRAN, real_posts

from attendance_sync.pairing import pair
from attendance_sync.parser import parse_post


def parsed_events():
    events = []
    for post in real_posts():
        for group in parse_post(post):
            events.extend(group)
    return events


def paired_events():
    """Every paired event, in result order. Several notes share a clock (two 09:00 entries),
    so callers identify an event by post_id, never by clock alone."""
    return pair(parsed_events())


def event_for(post_id):
    """The single event that came from ``post_id``."""
    matches = [e for e in paired_events() if e['post_id'] == post_id]
    assert len(matches) == 1, f'expected one event from {post_id}, got {len(matches)}'
    return matches[0]


def entry_for(events, clock, day):
    """Find the paired event whose entry clock and day match."""
    for event in events:
        if event['kind'] == 'out' and event['time'] == clock and event.get('date') == day:
            return event
    return None


# ---------------------------------------------------------------------------
# Rule 1: the attended day is the day named in the text
# ---------------------------------------------------------------------------

def test_entry_day_comes_from_the_explicit_date_in_the_text():
    """`دوشنبه 14050706 / ورود 0900` was sent on 28 Sep and means 6 Shahrivar, i.e. 28 Sep."""
    monday = event_for('8ywpwbp3r7')
    assert monday['date'] == '2026-09-28'
    assert monday['date_basis'] == 'explicit_jalali'


def test_weekday_only_entry_lands_on_the_day_it_names():
    """`خروج دوشنبه 2200` names the weekday; that day, not the posting day, is the exit day."""
    assert event_for('ngd6g9c7f3')['date'] == '2026-09-28'


def test_late_named_day_is_not_pulled_back_to_the_posting_day():
    """`خروج چهارشنبه 1930` was sent 1 Oct. The named Wednesday is 30 Sep; it must stay 30 Sep."""
    late = event_for('h19mx9epzj')
    assert late['date'] == '2026-09-30', 'posting day leaked into the attended day'


# ---------------------------------------------------------------------------
# Rule 2: the posting clock orders notes, it does not place work
# ---------------------------------------------------------------------------

def test_entry_sent_on_a_later_day_keeps_its_own_named_day():
    """`سه شنبه 14050707 / ورود ساعت 0900` was sent 29 Sep, on the named day itself."""
    tuesday = event_for('awe6uu8ynb')
    assert tuesday['date'] == '2026-09-29'


def test_the_order_posts_are_handed_over_does_not_change_any_pairing():
    """Handing the posts over in a different order must not change an attended day.

    The order the author *wrote* the notes in is evidence -- it is what "the latest entry
    before it" is read against -- and that order lives in `posted_at`, which `pair()` restores.
    The order the caller happened to pass the list in is an accident of paging and API
    ordering, and must change nothing.

    This is the direct test of the user's rule: no day comes from the process date, and none
    from the sequence in which the API happened to return the messages.
    """
    baseline = sorted((e['post_id'], e['kind'], e['date']) for e in paired_events())

    shuffled = list(reversed(parsed_events()))
    after = sorted((e['post_id'], e['kind'], e['date']) for e in pair(shuffled))

    assert after == baseline, 'the order posts are handed over changed an attended day'


def test_pairing_survives_a_one_day_shift_in_every_posting_clock():
    """Move every posting instant one day later: the attended days must not move with it."""
    original = parsed_events()
    baseline = sorted((e['post_id'], e['kind'], e['date']) for e in pair(original))

    shifted = []
    for event in original:
        moved = dict(event)
        moved['posted_at'] = datetime.datetime.fromisoformat(
            event['posted_at']).timestamp() + 24 * 3600
        moved['posted_at'] = datetime.datetime.fromtimestamp(
            moved['posted_at'], datetime.timezone.utc).isoformat()
        shifted.append(moved)

    after = sorted((e['post_id'], e['kind'], e['date']) for e in pair(shifted))
    assert after == baseline


# ---------------------------------------------------------------------------
# Rule 3: an unnamed exit belongs to the latest earlier entry
# ---------------------------------------------------------------------------

def test_bare_exit_joins_the_latest_earlier_entry():
    """`خروج 2200` names no day. It must close the Monday 0710 entry, not an older one."""
    bare = event_for('9h5eh9ne4i')
    assert bare['pairing_state'] == 'paired'
    assert bare['paired_entry_id']
    entry = next(e for e in paired_events() if e['event_id'] == bare['paired_entry_id'])
    assert entry['time'] == '07:10'
    assert entry['date'] == '2026-09-21'


def test_bare_exit_inherits_the_entry_day():
    """The unnamed exit has no day of its own, so the pair reports the entry's day."""
    assert event_for('9h5eh9ne4i')['date'] == '2026-09-21'


# ---------------------------------------------------------------------------
# Rule 4: ambiguity is surfaced, not guessed
# ---------------------------------------------------------------------------

def test_every_named_day_is_attributed_to_some_pair_or_flagged():
    """No event may end the run with a day that was invented rather than derived.

    Anything not paired must carry a reason explaining why, so the report can ask the user
    about it instead of silently registering a wrong interval.
    """
    for event in pair(parsed_events()):
        if event['pairing_state'] == 'paired':
            continue
        assert event['reasons'], f"{event['kind']} {event['time']} left unexplained"


def test_a_day_with_two_unresolvable_entries_is_left_for_review():
    """Two entries for one day with no way to choose stay open; neither is registered."""
    events = [
        {'event_id': 'e1', 'kind': 'in', 'time': '08:00', 'date': '2026-09-27',
         'date_basis': 'explicit_jalali', 'raw_weekday': 'یکشنبه', 'raw_date': None,
         'posted_at': 1, 'event_index': 0, 'post_id': 'p1', 'reasons': [],
         'status': 'ready', 'source_version': 1, 'pairing_eligible': True,
         'explicit_overtime': False, 'raw_context': []},
        {'event_id': 'e2', 'kind': 'in', 'time': '09:30', 'date': '2026-09-27',
         'date_basis': 'explicit_jalali', 'raw_weekday': 'یکشنبه', 'raw_date': None,
         'posted_at': 2, 'event_index': 0, 'post_id': 'p2', 'reasons': [],
         'status': 'ready', 'source_version': 1, 'pairing_eligible': True,
         'explicit_overtime': False, 'raw_context': []},
    ]
    result = pair(events)
    assert all(e['pairing_state'] != 'paired' for e in result)
    assert all(e['reasons'] for e in result)


@pytest.mark.parametrize('time', ['23:59'])
def test_night_boundaries_are_preserved_verbatim(time):
    """A clock at a minute boundary must survive parsing unchanged."""
    events = parsed_events()
    assert any(e['kind'] == 'out' and e['time'] == time for e in events)


def test_both_tuesday_exits_are_refused_because_tuesdays_entry_arrived_later():
    """`خروج سه شنبه 1710` and `خروج سه شنبه 2359` were both written before Tuesday was worked.

    The real channel order is the evidence here. `چهارشنبه 14050701 / ورود 0825` and the 17:10
    exit share one posting instant, so which came first is not readable, and the 23:59 exit was
    sent Wednesday morning. Tuesday's own entry, `سه شنبه 14050707 / ورود 0900`, was written
    after both of them, so the day they name -- the 29th -- has an entry that sits later in the
    feed than the exits. An exit cannot close an entry the feed shows it preceding.

    That is the order the feed gives and it cannot be talked round: an exit written before the
    entry it would close cannot be paired to it. Both are held for review with no day, and the
    Tuesday entry is left unmatched rather than being attached to an exit that precedes it.
    Pairing these anyway is what used to file the 23:59 against the wrong Wednesday.
    """
    for post_id, clock in (('am1xto63ti', '17:10'), ('fqd59fjy7b', '23:59')):
        exit_event = event_for(post_id)
        assert exit_event['time'] == clock
        assert exit_event['date'] is None, 'an exit was filed on a day it cannot be shown for'
        assert exit_event['pairing_state'] == 'incomplete_day', \
            'a refused exit keeps no day to be reviewable about'
        assert 'no_entry_on_the_named_day' in exit_event['reasons']

    # The Tuesday entry both exits named kept its own day and closed with the exit that
    # carries no clock of its own -- `لپتاپ ... فعلا خروج میزنم` -- which is the day's real
    # exit and is flagged for review because that note states no time to measure.
    tuesday_entry = event_for('awe6uu8ynb')
    assert tuesday_entry['date'] == '2026-09-29'
    assert tuesday_entry['time'] == '09:00'
    assert tuesday_entry['paired_exit_id'] == next(
        e['event_id'] for e in paired_events() if e['post_id'] == 'cqbk88cngi')


def test_fixture_dates_line_up_with_the_named_weekdays():
    """Guard on the fixture itself: the named weekday must match the named date."""
    events = parsed_events()
    for event in events:
        if not event.get('date') or not event.get('raw_weekday'):
            continue
        named = event['raw_weekday'].replace('‌', '').replace(' ', '')
        assert named, 'fixture post lost its weekday'