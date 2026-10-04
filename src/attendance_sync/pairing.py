"""Pairing, with the confirmed rule: an exit belongs to the latest earlier entry.

The user's rule is exact: *after each entry and before the next entry, any exit belongs to
the first (earlier) entry*. Four consequences are implemented here:

* an exit always closes the latest earlier entry that is still open, even when that entry
  has no clock of its own or cannot be resolved to a date;
* a new entry never closes the previous one, so a second entry is not silently swallowed;
* a pairing is only ``ready`` when clock and order alone determine it. An entry with a
  missing or invalid clock, or one whose date conflicts with its own weekday, stays
  ``review`` and the exit paired to it inherits that state, so no interval can be built
  from a half-known pair;
* a day that holds exactly one clock is not silently discarded: it becomes one unresolved
  remainder carrying its own source ids, so a report can explain the gap.

The attended day is decided before pairing starts, by weekday_resolve: only words the
author wrote can place work on a calendar. The posting instant orders the notes and is never
allowed to name the day.
"""
from copy import deepcopy
from datetime import date, datetime, time, timedelta
from .misdated import MAX_SHIFT, resolve_misdated_exit
from .intervals import split_midnights
from .parser import WEEKDAYS
from .normalize import normalize
from .weekday_resolve import resolve_weekdays, reconcile_with_neighbours, TEXTUAL_BASES

# How long after the entry a late note may be written. Two days covers the ordinary cases --
# typing out at the end of the day, or the next morning, or the morning after that -- and stops
# short of the week-later note that would otherwise swallow any shift with matching clocks.
LATE_NOTE_WINDOW = timedelta(days=2)

STATES = ('not_applicable', 'paired', 'incomplete_day', 'review')


def flag(item, reason):
    if reason not in item['reasons']:
        item['reasons'].append(reason)
    item['status'] = 'review'


def _posting_order(events):
    """The posting instants whose order is genuinely undetermined.

    Two notes sent in the same instant, from different messages, cannot be ordered by the
    machine: `create_at` has second resolution and both carry the same one. One of them was
    written first, and nothing in the data says which.

    That matters only when the order could change a day. Two notes that each state their own
    day do not need an order: `خروج یکشنبه 1705` and `ورود دوشنبه 0900` sent in the same second
    are two different days, and the text already says which is which. So a shared instant is
    undetermined only when at least one of the notes has no day of its own -- and then it goes
    to review, rather than the bot guessing and filing a day under the wrong name.

    This is a statement about instants, not about calendar days: no timezone is read here, and
    a change of the machine clock cannot change the answer.
    """
    grouped = {}
    for event in events:
        grouped.setdefault(event['posted_at'], []).append(event)
    ambiguous = set()
    for instant, group in grouped.items():
        if len(group) < 2 or len({event['post_id'] for event in group}) == 1:
            continue
        if all(event.get('date') and event['date_basis'] in TEXTUAL_BASES for event in group):
            # Each note names its own day. The order between two of them cannot change
            # anything only when the days differ -- then neither can be the other's entry,
            # whichever arrived first. Two entries on the *same* day are different: which one
            # a later exit closes decides the shift's start, and the text does not say. So
            # the exemption is for distinct days only, and it stays a day-level check on
            # evidence rather than a reading of the clock.
            if len({event['date'] for event in group}) > 1:
                continue
        ambiguous.add(instant)
    return ambiguous


def _sequence_by_written_day(events):
    """Give every event a reading position derived only from what the text says.

    The feed is walked in posting order -- that is the order the author wrote the notes, and
    it is the only ordering the messages themselves provide. Each event is stamped with the
    last day the text stated outright (`دوشنبه 14050706`) at or before it, which is the day
    the author was working on when writing it. An event that states no day inherits that day
    for ordering purposes only: `_sequence_key` reads this field, and pairing then overwrites
    the real day from the entry the event closes.

    A machine-clock change cannot move this: no field here is read from `create_at` in a
    timezone.

    Only an outright date advances the day. A weekday-only note does not, even once the
    resolver has turned it into a day: `خروج یکشنبه 1800` after `یکشنبه 14050622 / ورود
    0900` is an inference about a day the author did not write out, and treating it as a new
    day would drag every following note back onto that day. It inherits the day the author
    was last working on, which is what a reader of the channel would assume too.
    """
    last_written = None
    for event in sorted(events, key=lambda e: (e['posted_at'], e['event_id'])):
        if event.get('date') and event.get('date_basis') in TEXTUAL_BASES:
            last_written = event['date']
        event['_written_day'] = last_written
    return events


def _sequence_key(event):
    """Sort key: the day the text was on when this note was written, then the note itself.

    A note that states no day of its own is ordered by the last day the text did state. That
    keeps `خروج چهارشنبه 1930`, written after `چهارشنبه 14050708 / ورود 0845`, on that
    Wednesday instead of being pushed to the end of the feed and attached to a later day.
    The day itself is still decided by pairing; this only fixes the reading order.

    The second field is the line the event sits on inside its own message, *not* whether it is
    an exit. Grouping every entry ahead of every exit would read `شنبه 14050621 / ورود 0800 /
    خروج 1200` and `شنبه 14050621 / ورود 1300 / خروج 1900` as 08:00, 13:00, 12:00, 19:00 --
    two entries, then two exits -- and the second entry would swallow the first shift's exit.
    Reading line by line gives 08:00, 12:00, 13:00, 19:00, which is what the author wrote.
    """
    return (event.get('_written_day') or '', event['posted_at'], event['event_index'],
            event['kind'] != 'in', event['time'] or '99:99')


def event_stated(event):
    """True when the message itself names the day this event belongs to."""
    return bool(event.get('date')) and event.get('date_basis') in TEXTUAL_BASES


def _late_note_window(entry, event):
    """True when the exit names close enough after the entry to read as a late note.

    A late note is somebody typing out at the end of a day, or the next morning, or the morning
    after that. It is never typed about a shift six days behind them, so the two days have to
    sit within two days of each other. Without this a plausible-looking clock pair is enough:
    `08:25` in on the 23rd and `17:10` out on the 29th are two clocks eight hours apart, and
    measuring them on the entry's day makes a six-day-old Tuesday look like one shift. The
    clocks are a necessary check, not a sufficient one.
    """
    if not (entry.get('date') and event.get('date')):
        return False
    entry_day = date.fromisoformat(entry['date'])
    exit_day = date.fromisoformat(event['date'])
    return timedelta(0) <= exit_day - entry_day <= LATE_NOTE_WINDOW


def _fits_one_shift(entry, event):
    """True when the two clocks describe one shift of a believable length.

    Used to accept a late note: an entry at 08:30 and an exit at 17:10, named days apart, is
    one working day described across two notes. Two clocks far more than one shift apart are
    not one shift, so the late-note reading is refused and the pair goes to review instead.

    The length is measured on the day the note named, and on the entry's day if that one is out
    of reach. Measuring only the named day would refuse every late note, because the whole
    point of one is that it was written after the day it describes.

    Wrapping to the next day is allowed only as far as a night shift reaches: from the entry's
    own day, or the day after it. Without that limit the measurement wraps a *week* and turns
    an `08:25` entry on the 23rd plus a `17:10` exit on the 29th into a three-hour shift,
    which is how a Tuesday six days adrift would get filed against a shift it never touched.
    """
    if not (entry.get('date') and event.get('date')):
        return False
    if not (entry.get('time') and event.get('time')):
        return False
    entry_day = date.fromisoformat(entry['date'])
    start = datetime.combine(entry_day, time.fromisoformat(entry['time']))
    for day in (event['date'], entry['date']):
        named = date.fromisoformat(day)
        # Beyond the day after the entry's, this is not a shift that ran late.
        if not (entry_day <= named <= entry_day + timedelta(days=1)):
            continue
        end = datetime.combine(named, time.fromisoformat(event['time']))
        if end <= start:
            end += timedelta(days=1)
        if timedelta(0) < end - start <= MAX_SHIFT:
            return True
    return False


def _is_same_day_barrier(barrier, event, entry):
    """True when an unreadable-order barrier competes with this exit for the same day.

    A barrier exists because two notes share a posting instant, so the feed cannot say which
    came first. On another day it is not in play for this exit and can be stepped past. On the
    exit's own day the two are candidates for the same shift and the text does not say which
    one the exit closes -- so pairing either is a guess about a day.

    The days compared are the *entries*, not the notes themselves. An exit written without a
    day (`خروج 1700`) is dated by pairing -- it takes the last entry before it -- so the exit
    has no day of its own to compare, and comparing its empty value with the barrier's day
    would say "different" every time and let the barrier be ignored exactly when it matters.
    """
    if 'ambiguous_post_order' not in barrier.get('reasons', ()):
        return False
    exit_day = event.get('date') or (entry.get('date') if entry else None)
    return (barrier.get('date') is not None
            and exit_day is not None
            and barrier['date'] == exit_day)


def _same_weekday_reading(event, entry):
    """True when an entry's day could be the day a weekday-only exit was talking about.

    A bare weekday is a claim, not a day: `خروج شنبه 1800` sent after `یکشنبه 14050622 /
    ورود 0900` is filed on the open entry's day, because the open entry is the only real
    evidence of the day and the resolver's weekday reading was just a guess at which Saturday
    was meant. That is only reasonable while the guess is *close*. `خروج سه شنبه 1710`
    resolving to the 29th, meeting an entry from the 23rd, is six days away and not a guess
    that pairing is entitled to overrule -- a Tuesday exit has no business closing a shift
    from the Tuesday a fortnight before. So the two days must fall in the same week: the
    exit's reading is only overruled by an entry it could plausibly have meant.
    """
    if not entry.get('date') or not event.get('date'):
        return False
    entry_day = date.fromisoformat(entry['date'])
    exit_day = date.fromisoformat(event['date'])
    # Monday of each week. Same week means the exit's reading is one the open entry could
    # plausibly have been; a week or more apart and pairing is inventing a link.
    return (entry_day - timedelta(days=entry_day.weekday())) == (
        exit_day - timedelta(days=exit_day.weekday()))


def _candidate_entry(opened, event):
    """The open entry this exit would close if nothing were held back, or ``None``.

    An exit that names its own day reaches past any open entry on another day to the newest
    one on the day it wrote; an exit that names no day takes the newest open entry, full
    stop. This returns whichever of the two applies, without removing anything -- deciding
    whether the pairing happens at all is a separate question, asked after the barriers.
    """
    if event.get('date') and opened[-1]['kind'] != 'in':
        behind = [c for c in reversed(opened) if c['kind'] == 'in']
        if behind and behind[0].get('date') == event['date']:
            return behind[0]
    return opened[-1] if opened[-1]['kind'] == 'in' else None


def pair(events, ranges=()):
    """Pair entries with exits and settle what day each one belongs to.

    ``ranges`` are the worked stretches written as `کار 1300-2300`. They take no part in
    pairing -- an entry/exit pair is what forms a shift -- but they do state a day, and a
    note's weekday is settled against the days around it. A day written out as a range is
    as firm a statement as a note saying `ورود`, so ranges are passed in to be read, not
    paired.
    """
    unique = {}
    for event in events:
        previous = unique.get(event['event_id'])
        if previous is None or event['source_version'] >= previous['source_version']:
            unique[event['event_id']] = event
    result = deepcopy(list(unique.values()))
    # The order the caller hands the list over is an accident of paging and API ordering; the
    # order the author *wrote* the notes in is evidence. `posted_at` is restored first, then
    # `event_id` breaks any remaining tie, because `create_at` only resolves to the second and
    # two notes written in the same second would otherwise be ordered by whatever the caller
    # happened to pass first. That tie is reported as `ambiguous_post_order` below -- it is
    # genuinely undecidable, not silently resolved -- but the *result* is at least the same
    # answer every run, on any machine clock, which is what makes it reviewable.
    result.sort(key=lambda e: (e['posted_at'], e['event_id']))
    # Phase one of the user's order: extract every entry and exit, then place them on a
    # calendar. A weekday is resolved against the days the channel states in writing, never
    # against the posting clock, so an exit written the next morning keeps its own day.
    # A note whose weekday and date disagree is not a problem on its own: it is settled
    # against the days written either side of it first, and only a disagreement the
    # neighbourhood cannot resolve survives as a conflict.
    reconcile_with_neighbours(result, ranges)
    resolve_weekdays(result)
    eligible = [e for e in result if e.get('pairing_eligible', True)]
    # Whether two notes sent in the same instant can be ordered is only answerable once the
    # text has decided which days they claim, so this runs after the weekday resolution.
    ambiguous = _posting_order(eligible)
    # An event the text dated only by weekday has no day of its own. It is filed against the
    # last day the text dated outright *before* it in the feed -- the day it was written on, as
    # far as the evidence goes. Which entry it closes is then settled by pairing, which is the
    # user's rule; this sort only decides the reading order, never the day.
    _sequence_by_written_day(result)
    result.sort(key=lambda e: (_sequence_key(e), e['posted_at'], e['event_index'],
                               e['kind'], e['time'] or '99:99'))
    opened = []
    for event in result:
        event['source_date'] = event['date']
        event['source_basis'] = event['date_basis']
        event['missing_counterpart_id'] = None
        event['pairing_state'] = 'not_applicable'
        if not event.get('pairing_eligible', True):
            continue
        if event['posted_at'] in ambiguous:
            flag(event, 'ambiguous_post_order')
            event['pairing_state'] = 'review'
            # Its place in the feed is unknown, but the note still has to be held: the entry
            # it may or may not have preceded is real work, and an exit at an unknown position
            # is just as likely to have closed one as opened a day. Holding it as a barrier
            # keeps the next exit from reaching across the gap and inventing a day.
            opened.append(event)
            continue
        if event['kind'] == 'in':
            opened.append(event)
            continue
        if not opened:
            event['date'] = None
            flag(event, 'unpaired_exit')
            event['pairing_state'] = 'incomplete_day'
            continue
        # A barrier note sits on top when two messages share one posting instant. The exit in
        # front of it may be the one that should have closed the entry behind, so when the
        # exit's own day says which entry it means, the barrier is set aside -- flagged, not
        # silently dropped. Where the text does not say, the barrier holds and the exit goes
        # to review rather than reaching across the gap.
        entry = None
        # A note held because its place in the feed is unknown -- an ambiguous posting instant,
        # whatever kind it is -- competes with this exit when it sits on the exit's own day.
        # Which entry a later exit closes decides the shift's start, and an unreadable order
        # does not say. Stepping past it picks one of the candidates silently, so the exit goes
        # to review instead. A barrier on another day was never in play for this exit.
        held = [c for c in reversed(opened)
                if _is_same_day_barrier(c, event, _candidate_entry(opened, event))]
        if held:
            flag(event, 'ambiguous_prior_attendance')
            event['date'] = None
            event['pairing_state'] = 'review'
            continue
        if opened[-1]['kind'] != 'in' and event_stated(event):
            behind = [c for c in reversed(opened) if c['kind'] == 'in']
            if behind and behind[0]['date'] == event['date']:
                barrier = opened.pop()
                flag(barrier, 'barrier_stepped_over_by_a_dated_exit')
                entry = opened.pop()
        if entry is None:
            if opened[-1]['kind'] != 'in':
                event['date'] = None
                flag(event, 'ambiguous_prior_attendance')
                event['pairing_state'] = 'incomplete_day'
                continue
            entry = opened.pop()
        # Which day does this pair belong to? The text decides: a date written in the message
        # is final. An exit the text left undated takes the latest entry before it, which is
        # the rule the user confirmed -- so it has no day to conflict with.
        #
        # When the exit names a day and the newest open entry is not on that day, the exit is
        # not asking for that entry: `خروج سه شنبه 2359` written on Wednesday morning closes
        # Tuesday's shift, even though Wednesday's entry is already on the stack. So the stack
        # is searched for the latest entry on the exit's own day, and only that one.
        if event_stated(event) and event['date'] != entry['date']:
            # The exit names a day and the newest open entry is not on it. The entry it meant
            # is further down the stack: `خروج سه شنبه 2359` written on Wednesday morning
            # closes Tuesday's shift, even though Wednesday's entry is already open. Those
            # entries in between are still real work, so they go back on the stack untouched.
            behind = [c for c in reversed(opened)
                      if c['date'] and c['date'] == event['date']]
            if behind:
                entry = behind[0]
                opened.remove(entry)
            elif event['date'] > entry['date'] and _late_note_window(entry, event) \
                    and _fits_one_shift(entry, event):
                # The exit names a *later* day than the entry and the clocks are one shift's
                # worth apart, so it is about the day just worked: `ورود 0830` on 10 Sep with
                # `خروج 1710` written on the 12th. That is how people write a late note.
                #
                # Whether the pair keeps the exit's day or the entry's is not decided here. It
                # is settled further down, in `resolve_misdated_exit`, which is the single
                # place that weighs the day named against the entry's. A 19:00-to-04:00 pair
                # named one day on is the night itself and keeps the exit's day; a
                # 08:30-to-17:10 pair named days apart is a late note and takes the entry's.
                # Deciding it in both places would let the two answers disagree.
                event['date'] = entry['date']
                event['date_basis'] = 'paired_entry'
            elif entry['date'] == event['date']:
                # Same day, and the newest open entry is already gone from the stack -- a
                # second exit for a day that has one entry (`خروج سه شنبه 2359` after
                # `خروج سه شنبه 1710`). That is one entry, two exits: not a missing entry but
                # an extra one. The pair is kept so the day is not lost, and the duplicate is
                # flagged for review rather than silently filed.
                flag(event, 'extra_exit_for_the_same_day')
            else:
                # The exit named a day, the newest open entry is on a different one, and no
                # entry on the exit's own day is waiting. Normally that is a missing entry
                # and the pair is refused. It is not so when the note named no date of its
                # own -- only a weekday: `خروج شنبه 1800` sent after `یکشنبه 14050622 /
                # ورود 0900` names Saturday while the open entry is Sunday, and the weekday
                # resolver had to pick a Saturday from the days the channel states. A weekday
                # is a claim, not a day -- the day's evidence is the entry that is actually
                # open -- so the entry's day is taken and the contradiction is reported,
                # exactly as `late_note_same_day_work` reports one.
                if (event['date_basis'] != 'explicit_jalali' and entry['date']
                        and _same_weekday_reading(event, entry)):
                    # Pairing has overruled the day a bare weekday pointed at. The pair is
                    # kept and filed on the entry's day, which is the only real evidence for
                    # it; the two flags below record that the note's own claim lost, so the
                    # report shows a disagreement rather than a confident match.
                    entry = opened.pop()
                    event['date'] = entry['date']
                    event['date_basis'] = 'paired_entry'
                    flag(event, 'exit_weekday_overridden_by_pairing')
                else:
                    opened.append(entry)
                    event['date'] = None
                    flag(event, 'no_entry_on_the_named_day')
                    event['pairing_state'] = 'incomplete_day'
                    continue
        event['paired_entry_id'] = entry['event_id']
        entry['paired_exit_id'] = event['event_id']
        entry['pairing_state'] = event['pairing_state'] = 'paired'
        # The pair shares one attended day. Whose word decides it depends on which side has
        # one: a date written in the message is the strongest evidence there is, and neither
        # side may overrule it. Only when one side stated nothing does the other side's day
        # fill the gap -- which is the user's rule, "an exit with no date belongs to the latest
        # entry before it".
        # entry may have been re-picked from further down the stack, so it is re-read here.
        entry_stated = bool(entry['date']) and entry['date_basis'] in TEXTUAL_BASES
        if entry_stated and not event_stated(event):
            event['date'], event['date_basis'] = entry['date'], 'paired_entry'
        elif event_stated(event) and not entry_stated:
            entry['date'], entry['date_basis'] = event['date'], 'paired_exit'
        elif entry_stated and event_stated(event):
            # Both sides stated a day and they agree (the mismatch was rejected above), so
            # there is nothing to decide.
            pass
        elif event['date'] or entry['date']:
            event['date'] = event['date'] or entry['date']
            entry['date'] = entry['date'] or event['date']
        else:
            flag(event, 'paired_entry_unresolved')
        # A note that named only a weekday has not stated a day: `weekday_only` is a constraint,
        # not a decision. When the channel names that weekday twice, pairing may well close it
        # onto the wrong week. So its day is left for review rather than borrowed from the
        # entry -- borrowing is what makes a two-week-old Monday land on the wrong Monday.
        if event['date_basis'] == 'weekday_only' and not event_stated(event):
            flag(event, 'weekday_matches_several_stated_days')
            event['pairing_state'] = event['pairing_state'] = 'review'

        # "The text named no day" is not a defect once the pair has settled one: the note said
        # nothing about a day and the entry it closes did, and that is the whole point of
        # pairing. It stops being a reason only when a day was actually arrived at.
        if event['date']:
            event['reasons'] = [r for r in event['reasons']
                                if r not in ('post_date_assumed', 'unknown_weekday',
                                              'no_date_in_text', 'weekday_without_stated_day')]
        event['status'] = 'review' if event['reasons'] else 'ready'
        if event['raw_date'] and entry['date'] and event['source_date'] != entry['date']:
            # The note may name a later day simply because it was written later. The clocks
            # decide: a plausible single shift on the named day is kept, otherwise the work
            # belongs to the entry's day and the late date stays as evidence.
            day, reason = resolve_misdated_exit(entry, event, event['source_date'])
            if day:
                event['date'] = day
                if reason:
                    event['reasons'].append(reason)
                event['status'] = 'review' if event['reasons'] else 'ready'
            else:
                event['date'] = event['source_date']
                flag(event, 'explicit_exit_date_conflict')
        entry_basis, event_basis = entry['source_basis'], event['source_basis']
        # An entry whose clock or date is not trustworthy cannot define an interval on its own.
        # The decision is judged on the entry's own reasons before the exit flags are copied
        # back onto it, otherwise the mark would always be present and mean nothing. A weekday
        # the entry itself stated is reconciled with the exit's day by the rule above, so only a
        # date that was assumed from the clock and then contradicted is a real conflict.
        unusable = (entry_basis == 'post_date_assumed' and event_basis != 'post_date_assumed' and event['source_date'] != entry['date']) \
            or entry['time'] is None
        if entry['status'] == 'review':
            flag(event, 'paired_entry_review')
        if event['status'] == 'review':
            flag(entry, 'paired_exit_review')
        if unusable:
            flag(entry, 'unpairable_entry')
            event['pairing_state'] = entry['pairing_state'] = 'review'
            flag(event, 'paired_entry_review')
        weekday = WEEKDAYS.get(normalize(event['raw_weekday'] or '').replace(' ', ''))
        if event['raw_weekday'] and entry['date'] and weekday is not None \
                and weekday != date.fromisoformat(entry['date']).weekday():
            # The exit names a weekday that is not the entry's day. The entry's own explicit
            # date stands, but the contradiction is reported rather than silently resolved.
            flag(event, 'exit_weekday_conflicts_with_entry')
            if event['date_basis'] == 'paired_entry':
                # ...and the weekday was the *only* day the note claimed: there is no date of
                # its own left to conflict with. Pairing settled it against the newest open
                # entry, and it is the newest open entry that is the evidence. Saying so
                # keeps a wrong weekday visible in the report instead of letting it vanish
                # into a confident `ready` -- and because pairing overruled what the author
                # wrote, the pair is held for the user rather than filed on the guess. The
                # day is decided; whether it is the day that was worked is the user's call.
                flag(event, 'exit_weekday_overridden_by_pairing')
    for entry in opened:
        flag(entry, 'unmatched_entry')
        entry['pairing_state'] = 'incomplete_day'
    for event in result:
        if 'paired_entry_id' not in event and 'paired_exit_id' not in event and event.get('pairing_eligible', True):
            if event['kind'] == 'out' and event['date'] is None:
                continue
            flag(event, 'unmatched_event')
    # A day that holds exactly one clock has no counterpart at all: say so explicitly, so the
    # period report can explain the day instead of showing it as blank. The confirmed
    # current-day open entry keeps the legacy reason set, which the report reads as open_day.
    counterpart = {}
    for event in result:
        if event['pairing_state'] != 'incomplete_day':
            continue
        day = event['source_date'] or event['date']
        if day:
            counterpart.setdefault(day, []).append(event['event_id'])
    for event in result:
        if event['pairing_state'] != 'incomplete_day':
            continue
        day = event['source_date'] or event['date']
        if day and len(counterpart[day]) > 1:
            event['missing_counterpart_id'] = next(i for i in counterpart[day] if i != event['event_id'])
        if event['kind'] == 'in':
            # An outstanding entry already carries unmatched_entry (and the parent's
            # unmatched_event), which is what the day-level reading reacts to; adding
            # incomplete_day would collide with the confirmed current-day open entry, which the
            # report turns into open_day only while the reason set is exactly those flags.
            continue
        flag(event, 'incomplete_day')
    return result
