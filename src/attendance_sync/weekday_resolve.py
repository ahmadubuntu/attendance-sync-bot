"""Resolve weekday-only dates against the dates the channel itself states.

The user's rule: only the date written in the text matters, never the day the note was sent.
A note that says `خروج دوشنبه 2200` names a weekday, which on its own is not a date -- there
are seven Mondays. The posting clock used to be used to pick one, which silently invents an
attendance day out of a process date.

This module closes that gap without inventing anything. The channel is full of notes that
state an explicit Jalali date (`دوشنبه 14050706`). Those days are ground truth: they are
days the author committed to in writing. A weekday-only note is resolved to the nearest such
stated day that actually falls on that weekday, and only when that is unambiguous.

Three outcomes, never more:

* ``explicit_jalali``      -- the text stated a full date. Unchanged, authoritative.
* ``weekday_relative``     -- resolved to the single unambiguous stated day for that weekday.
* ``weekday_undetermined`` -- the weekday matches more than one stated day, or none. The day
  stays unset and the event is flagged for the user instead of being guessed.

The posting clock never enters this decision.
"""

from datetime import date as _date, timedelta

from .parser import WEEKDAYS
from .normalize import normalize

# Bases that state a day in the author's own words.
TEXTUAL_BASES = ('explicit_jalali', 'weekday_relative')

# Bases the parser assigns when the text names no day at all. Never used to resolve a day:
# they all came out of the posting instant, which reads differently on every machine clock.
UNUSABLE_BASES = ('post_date_assumed', 'posted_clock_inferred',
                  'posted_clock_overnight_inferred', 'undated')


def weekday_of(event):
    """The weekday number the note names, or None when it names none."""
    token = normalize(event.get('raw_weekday') or '').replace(' ', '')
    return WEEKDAYS.get(token)


def stated_days(events):
    """Every day the channel commits to in writing, as a set of ISO dates.

    Only explicit Jalali dates count. A weekday word is not a day: it is resolved later,
    against this set, never fed back into it.
    """
    return {event['date'] for event in events
            if event.get('date') and event.get('date_basis') == 'explicit_jalali'}


def resolve_weekdays(events):
    """Give every weekday-only event a day, or an explicit reason why it has none.

    A weekday name is checked against the days the channel states in writing. A weekday that
    matches exactly one stated day is resolved to it. A weekday that matches several is settled
    by *where the note sits in the feed*: it names the latest of those days the channel had
    already written out when the note was sent. That is not a guess -- the messages before it
    name real days, and the author was working on the last of them when writing this one. It
    is not the posting clock either: that would make the answer depend on the machine's
    timezone, which is the bug this module exists to prevent. So the basis is
    `weekday_relative`, and when the ordering gives no answer the event is left unset for
    review rather than guessed.

    Mutates and returns ``events``. Events that already state an explicit date are never
    touched.
    """
    days = stated_days(events)
    # The written days in the order the channel produced them. A weekday-only note is placed
    # by the last one before it, which makes "the Monday I mean" the Monday the author had
    # just written about -- not the nearest Monday to any clock.
    written_before = {}
    latest = None
    for event in sorted(events, key=lambda e: (e['posted_at'], e['event_id'])):
        written_before[event['event_id']] = latest
        if event.get('date') and event.get('date_basis') == 'explicit_jalali':
            latest = event['date']

    for event in events:
        if event.get('date_basis') == 'explicit_jalali' and event.get('date'):
            # Already a stated day. A weekday that disagrees with it is the author's own
            # contradiction, which the pairing layer reports; do not "fix" it here.
            continue

        weekday = weekday_of(event)
        if weekday is None:
            # The text names no day. Whatever the parser assumed from the posting clock is
            # not evidence, so drop it and let pairing decide from the entry it closes.
            if event.get('date_basis') in UNUSABLE_BASES:
                event['date'] = None
                event['date_basis'] = 'weekday_undetermined'
                if 'unknown_weekday' not in event['reasons']:
                    event['reasons'].append('unknown_weekday')
                event['status'] = 'review'
            continue

        if event.get('date_basis') != 'weekday_only':
            # Some other basis (an explicit date, a paired day). Leave it to the pairing layer.
            continue

        matches = _stated_days_on(weekday, days)
        # The days this weekday could mean, for a note that also states a date it disagrees
        # with. A date written in the text is a candidate on its own: the author may have
        # mistyped the weekday rather than the date, and which of the two slipped is settled
        # later by the days either side. Both readings stay open until then.
        candidates = sorted(set(matches) | set(event.get('candidate_dates', ())))
        if len(matches) == 1:
            event['date'] = matches[0]
            event['date_basis'] = 'weekday_relative'
            event['status'] = 'review' if event['reasons'] else 'ready'
        elif not matches:
            event['date'] = None
            event['date_basis'] = 'weekday_undetermined'
            reason = 'weekday_without_stated_day'
            if reason not in event['reasons']:
                event['reasons'].append(reason)
            event['status'] = 'review'
        else:
            # A weekday-only note names the most recent day of that weekday up to the last day the
            # author had written out by then. "خروج سه شنبه 2359", sent after the Wednesday
            # entry, means the Tuesday just gone. A Tuesday after that day was never announced,
            # so it cannot be meant. This is read off the notes themselves -- no clock, no
            # timezone -- and where the evidence does not name exactly one, no day is chosen.
            anchor = written_before.get(event['event_id'])
            closed = [day for day in matches if not anchor or day <= anchor]
            if closed:
                # The latest of them is the one the author meant. Older ones are days already
                # closed by a note of their own, which is what makes "latest" the rule.
                event['date'] = closed[-1]
                event['date_basis'] = 'weekday_relative'
                event['status'] = 'ready'
                event['candidate_dates'] = closed
            else:
                # Every matching day lies after the last written one, so none can be meant.
                event['date'] = None
                event['date_basis'] = 'weekday_only'
                event['candidate_dates'] = matches
                event['status'] = 'review'
    return events


def _stated_days_on(weekday, days):
    """Every stated day that falls on ``weekday``."""
    return sorted(day for day in days if _date.fromisoformat(day).weekday() == weekday)


def _fits_between(day, previous, following):
    """True when ``day`` does not contradict the days either side of it.

    A channel of attendance notes runs forwards: each day is worked after the one before it. A
    day that lands before the note before it, or after the note after it, contradicts the only
    evidence there is. Equality is allowed -- two entries on one day are ordinary, and a day
    repeated in a correction is not a chronology error.
    """
    if previous is not None and day < previous['date']:
        return False
    if following is not None and day > following['date']:
        return False
    return True


def _fits_the_channel(day, previous, following, stated):
    """True when ``day`` has a place in the whole channel, not merely between two neighbours.

    ``_fits_between`` only rules a day out relative to the notes touching this one. A reading
    can pass that and still contradict the rest of the channel: `یکشنبه 14050621` with
    `دوشنبه 14050623` after it offers Sunday the 6th and Sunday the 13th, and neither is
    contradicted by that Monday -- but the channel states no day at all before the 12th, so
    the 6th is a day nobody here worked. Judging a reading against the whole channel, not just
    its doorstep, is what keeps a disagreement from being resolved into a day that invents
    work nobody wrote down.
    """
    if not _fits_between(day, previous, following):
        return False
    known = sorted(stated)
    if not known:
        return True
    earliest, latest = known[0], known[-1]
    if previous is None and day < earliest:
        # Nothing was written before this note, so it cannot be the first day of the week
        # either side of the date -- that week went by unrecorded, and this note did not
        # record it.
        return False
    if following is None and day > latest:
        # The same at the far end: a day after every day the channel mentions is a day the
        # channel never went on to.
        return False
    return True


def _adjacent_weekday_days(day, weekday):
    """The nearest days of ``weekday`` either side of ``day``.

    This is what a weekday written beside a date can mean: the same day of the week in the
    week just gone, or the one coming. Both are worked out from the date in the text, so the
    same message yields the same two days whatever clock the machine reading the channel runs
    on -- a bot on a New York clock reading an Iranian note must not offer an American Friday
    as the Tuesday that was meant.
    """
    written = _date.fromisoformat(day)
    # The nearest such day before it, and the nearest after. Walking forward and back from the
    # written date, the first day landing on that weekday is the only candidate at each side;
    # every further one is a week older or newer than the note could plausibly have meant.
    before = max((written - timedelta(days=back)
                  for back in range(1, 8)
                  if (written - timedelta(days=back)).weekday() == weekday),
                 default=None)
    after = min((written + timedelta(days=forward)
                 for forward in range(1, 8)
                 if (written + timedelta(days=forward)).weekday() == weekday),
                default=None)
    return [day.isoformat() for day in (before, after) if day is not None]


def reconcile_with_neighbours(events, ranges=()):
    """Settle a weekday/date disagreement against the days written either side of it.

    A note carrying both a date and a weekday that do not match -- `یکشنبه 14050621 ورود
    0800`, where the date is a Saturday -- is not automatically wrong. One of the two is a
    slip, and which one is knowable from the neighbourhood rather than from the note alone. A
    channel runs forwards, so the reading that lands in its proper place between the notes
    around it is the one that holds.

    ``ranges`` matter as much as events here: a worked stretch of the day written out as
    `یکشنبه 14050622 کار 1300-2300` is just as firm a statement of a day as an entry note,
    and it is often the only neighbouring day there is. Leaving ranges out would make every
    note surrounded only by ranges look like the first note in the channel.

    Two outcomes, decided only by that comparison:

    * the note is settled -- the date written in it stands when the channel has room for it,
      or the weekday's day takes over when the date has none and only one day is left. Either
      way the note is *not* a problem. What it disagreed with is recorded on the event, so a
      reader can still see the slip, but nothing is held for review and no day is blocked.
    * the note is not settled -- both readings have a place, or none does, so the channel
      cannot say which word slipped. The conflict stands and the user is asked. This module
      never picks on a hunch.

    Either way the days the text could mean are kept on the event, so an unresolved note is
    held against every reading rather than one arbitrarily chosen.

    Only notes that state a clean day act as neighbours. A note that is itself in conflict
    cannot vouch for the one after it, and letting it would make the answer depend on which
    of two conflicted notes happened to be read first.
    """
    def _key(item):
        return item.get('event_id') or item.get('range_id') or ''
    everything = sorted(list(events) + list(ranges), key=lambda e: (e['posted_at'], _key(e)))
    at = {_key(item): index for index, item in enumerate(everything)}
    # The days the channel commits to in writing, read once and kept: reconciling a note
    # must not be able to move the ground the next note is measured against.
    stated = {item['date'] for item in everything
              if item.get('date') and item.get('date_basis') == 'explicit_jalali'}
    anchors = [item for item in everything
               if item.get('date') and item.get('date_basis') == 'explicit_jalali'
               and 'weekday_date_conflict' not in item.get('reasons', ())]

    # Every note in the channel is settled, entries and worked ranges alike: both state a day,
    # and `یکشنبه 14050621 کار 0800-0900` is as wrong as `یکشنبه 14050621 ورود 0800` when the
    # date beside it is a Saturday. Reconciling only the entries would leave the ranges able to
    # disagree with the entries they share a day with.
    for event in everything:
        if 'weekday_date_conflict' not in event.get('reasons', ()):
            continue
        weekday = weekday_of(event)
        written = event.get('date')
        if weekday is None or not written:
            continue
        key = _key(event)
        others = [a for a in anchors if _key(a) != key]
        before = [a for a in others if at[_key(a)] < at[key]]
        after = [a for a in others if at[_key(a)] > at[key]]
        previous = before[-1] if before else None
        following = after[0] if after else None
        if previous is None and following is None:
            # There is nothing to measure against. A single note, first in the channel, is not
            # a slip the neighbourhood can correct -- the neighbourhood would have to be the note
            # itself. Left for the user, unchanged, but still holding every day it could mean:
            # the date written, and that weekday in the weeks either side of it.
            event['candidate_dates'] = sorted(
                {written} | set(_adjacent_weekday_days(written, weekday)))
            continue

        # The two readings: the date as written, or the day that weekday lands on. The second is
        # worked out from the date in the text -- that weekday in the week before or after --
        # never from the clock, so a bot on a New York clock reading an Iranian note still reads
        # the same days. Both are then tested against the days the channel states.
        readings = {written}
        readings.update(_adjacent_weekday_days(written, weekday))
        # A day reached through the weekday word only counts if the channel names that day
        # somewhere. `یکشنبه 14050621` offers Sunday the 13th, but if no note in the channel
        # ever writes the 13th then nothing here says that Sunday was worked -- the weekday is
        # offering a day out of thin air, and only the date written is real evidence.
        readings = {day for day in readings if day == written or day in stated}
        fitting = [day for day in sorted(readings)
                   if _fits_the_channel(day, previous, following, stated)]
        # Every day the text could mean, recorded whether or not the note gets settled below.
        # A note nobody can settle is held against all of them, and a note that is settled
        # still carries the day it disagreed with so a reader can see what was weighed.
        event['candidate_dates'] = sorted(fitting) if fitting else sorted(readings)

        # Which reading holds, decided by the channel and by nothing else.
        #
        # The date written in the note is not automatically the answer: the note also names a
        # weekday, and one of the two is a slip. Preferring the date outright would make the
        # weekday decorative -- every disagreement would be silently closed in favour of the
        # date, which is the thing the neighbourhood exists to check. So both readings are
        # treated as equals, and the channel is asked which of them has a place:
        #
        #   * one reading fits and the other does not -- that one is the day. Not a guess: the
        #     other reading is impossible here, and one of two impossible-and-possible pairs
        #     has only one answer.
        #   * both fit, or neither does -- nothing is known, and the user is asked.
        #
        # Equality with a neighbour counts as fitting. Two notes sharing a day is ordinary, so
        # a reading that merely matches the next note is not thereby proved, and neither is one
        # contradicted by it.
        if len(fitting) == 1:
            settled = fitting[0]
        else:
            settled = None

        if settled is not None:
            if settled != written:
                event['date'] = settled
                event['date_basis'] = 'weekday_relative'
            else:
                # The date stands and the weekday word was what slipped.
                event['date_basis'] = 'explicit_jalali'
            event['reasons'] = [r for r in event['reasons'] if r != 'weekday_date_conflict']
            if event.get('status') == 'review' and not set(event['reasons']) & _REVIEW_REASONS:
                event['status'] = 'ready'
            event.setdefault('information', []).append(
                f'weekday_and_date_disagreed; the day was taken as {event["date"]} '
                f'because it is the one that fits the days written either side')
            continue

        # Nothing is known: several readings have a place in the channel and none of them is
        # singled out, or none of them has one at all. The neighbourhood cannot say which word
        # slipped, so the conflict stands and the user is asked. The candidate days set above
        # are what it is held against -- no day is guessed on a hunch.
    return events


# Reasons that hold an event for review on their own. Used only to give an event its status
# back after a disagreement was settled; anything else on the event still needs a person.
_REVIEW_REASONS = frozenset((
    'multiple_weekdays', 'multiple_dates', 'invalid_date', 'unknown_weekday',
    'no_date_in_text', 'weekday_without_stated_day', 'ambiguous_post_order',
    'unpaired_exit', 'unmatched_entry', 'unmatched_event', 'no_entry_on_the_named_day',
))