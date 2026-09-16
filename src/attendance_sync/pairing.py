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
"""
from copy import deepcopy
from datetime import date
from .parser import WEEKDAYS
from .normalize import normalize

STATES = ('not_applicable', 'paired', 'incomplete_day', 'review')


def flag(item, reason):
    if reason not in item['reasons']:
        item['reasons'].append(reason)
    item['status'] = 'review'


def _posting_order(events):
    """The smallest set of posting instants whose order is genuinely undetermined.

    Two events share an instant only when they were posted at the same moment, come from
    *different* posts, and state no clock that separates them. Clocks are compared inside one
    event kind, so the confirmed "same day, exit before the next entry" shape stays readable while
    two entries of one day posted together still go to review.
    """
    # A posting instant is undetermined when events from two different messages share it: the
    # clocks state when the work happened, not the order in which the notes were written. Several
    # events in one message are readable, because that message states its own line order.
    grouped = {}
    for event in events:
        grouped.setdefault(event['posted_at'], []).append(event)
    ambiguous = set()
    for instant, group in grouped.items():
        if len(group) < 2 or len({event['post_id'] for event in group}) == 1:
            continue
        ambiguous.add(instant)
    return ambiguous


def pair(events):
    unique = {}
    for event in events:
        previous = unique.get(event['event_id'])
        if previous is None or event['source_version'] >= previous['source_version']:
            unique[event['event_id']] = event
    result = deepcopy(list(unique.values()))
    eligible = [e for e in result if e.get('pairing_eligible', True)]
    ambiguous = _posting_order(eligible)
    result.sort(key=lambda e: (e['posted_at'], e['event_index'], e['kind'], e['time'] or '99:99'))
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
            if not opened or opened[-1] is not None:
                opened.append(None)
            continue
        if event['kind'] == 'in':
            opened.append(event)
            continue
        if not opened:
            event['date'] = None
            flag(event, 'unpaired_exit')
            event['pairing_state'] = 'incomplete_day'
            continue
        if opened[-1] is None:
            event['date'] = None
            flag(event, 'ambiguous_prior_attendance')
            event['pairing_state'] = 'review'
            continue
        entry = opened.pop()
        event['paired_entry_id'] = entry['event_id']
        entry['paired_exit_id'] = event['event_id']
        entry['pairing_state'] = event['pairing_state'] = 'paired'
        event['date_basis'] = 'paired_entry'
        entry_basis, event_basis = entry['date_basis'], event['source_basis']
        # Which of the two days is the attended day? A weekday or explicit date on the entry wins.
        # An entry whose day came only from the posting clock loses to a later exit, otherwise an
        # evening entry and its after-midnight exit could never form one overnight interval.
        if entry['date'] and entry['raw_weekday'] is None and entry_basis in ('post_date_assumed', 'weekday_inferred') \
                and event['date'] and event['date'] > entry['date'] and entry['time'] and event['time'] \
                and (entry['time'] > event['time'] or event['explicit_overtime']):
            entry['date'] = event['date']
            entry['date_basis'] = 'paired_exit'
        if entry['date']:
            event['date'] = entry['date']
        elif event['date'] and event['raw_weekday'] is None and entry['raw_weekday'] is None:
            # Neither side states a weekday, so the exit's posting day is a real signal.
            entry['date'] = event['date']
            entry['date_basis'] = 'paired_exit'
        else:
            flag(event, 'paired_entry_unresolved')
        event['reasons'] = [r for r in event['reasons'] if r not in ('post_date_assumed', 'unknown_weekday')]
        event['status'] = 'review' if event['reasons'] else 'ready'
        if event['raw_date'] and entry['date'] and event['source_date'] != entry['date']:
            event['date'] = event['source_date']
            flag(event, 'explicit_exit_date_conflict')
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
        if event['raw_weekday'] and (weekday is None or not entry['date'] or weekday != date.fromisoformat(entry['date']).weekday()):
            event['reasons'].append('exit_weekday_overridden_by_pairing')
    for entry in opened:
        if entry is not None:
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
