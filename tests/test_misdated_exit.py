"""Write the tests for the misdated-exit day rule, before wiring it into pairing."""
from datetime import date, datetime, timedelta, timezone

import pytest

from attendance_sync.misdated import MAX_SHIFT, resolve_misdated_exit


def entry(day, clock):
    return {'date': day, 'time': clock}


def exit_event(day, clock):
    return {'date': day, 'time': clock}


def test_a_note_written_later_still_counts_on_the_entry_day():
    """Thursday's exit recorded on Saturday is still Thursday's work."""
    day, reason = resolve_misdated_exit(entry('2026-09-10', '08:30'),
                                        exit_event('2026-09-12', '17:10'), '2026-09-12')
    assert day == '2026-09-10'
    assert reason == 'late_note_same_day_work'


def test_a_note_written_after_a_holiday_still_counts_on_the_entry_day():
    day, reason = resolve_misdated_exit(entry('2026-09-14', '08:30'),
                                        exit_event('2026-09-18', '17:10'), '2026-09-18')
    assert day == '2026-09-14'
    assert reason == 'late_note_same_day_work'


def test_a_note_written_a_week_later_still_counts_on_the_entry_day():
    day, reason = resolve_misdated_exit(entry('2026-09-14', '08:30'),
                                        exit_event('2026-09-21', '17:10'), '2026-09-21')
    assert day == '2026-09-14'
    assert reason == 'late_note_same_day_work'


def test_an_after_midnight_shift_stays_on_the_named_next_day():
    """A 19:00 to 04:00 note is a genuine night shift, so the exit's day takes it."""
    day, reason = resolve_misdated_exit(entry('2026-09-12', '19:00'),
                                        exit_event('2026-09-13', '04:00'), '2026-09-13')
    assert day == '2026-09-13'
    assert reason == 'exit_named_day_recent'


def test_a_shift_longer_than_the_cap_is_never_stretched_to_fit():
    """Both readings must be plausible; a note dated two days on with a later clock is neither."""
    day, reason = resolve_misdated_exit(entry('2026-09-12', '08:00'),
                                        exit_event('2026-09-14', '09:00'), '2026-09-14')
    assert reason == 'late_note_same_day_work'
    assert day == '2026-09-12', 'the clocks only fit the entry day'

    day, reason = resolve_misdated_exit(entry('2026-09-12', '08:00'),
                                        exit_event('2026-09-14', '05:00'), '2026-09-14')
    assert (day, reason) == (None, 'unresolved_exit_day'), 'a 45-hour stretch is not a shift'


def test_a_missing_named_day_is_unresolved_rather_than_guessed():
    assert resolve_misdated_exit(entry('2026-09-12', '08:30'), exit_event('2026-09-13', '17:10'), None) == \
        (None, 'unresolved_exit_day')


def test_the_same_day_is_returned_unchanged():
    day, reason = resolve_misdated_exit(entry('2026-09-12', '08:30'),
                                        exit_event('2026-09-12', '17:10'), '2026-09-12')
    assert day == '2026-09-12' and reason is None


def test_the_cap_is_a_single_working_shift():
    assert MAX_SHIFT == timedelta(hours=18)
