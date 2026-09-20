"""The user asked to always be told about a weekday/date mismatch, in the report itself.

A technical flag inside the JSON is not telling them. The report now carries a named section --
in the HTML and as its own file -- that states which note disagreed, what the note said, which
date the program actually used, and whether the neighbouring notes back that reading.
"""
from datetime import datetime

from attendance_sync.report import build_report, render_date_discrepancies_markdown, render_html

MISMATCHED = ['سه شنبه 14050623\nورود 0830']
CONSISTENT = ['دوشنبه 14050623 ورود 0830']


def report(messages):
    rows = [{'id': 'a', 'user_id': 'self',
             'create_at': int(datetime.fromisoformat('2026-09-14T09:00:00+03:30').timestamp() * 1000),
             'edit_at': 0, 'message': message} for message in messages]
    return build_report(rows, 'self', datetime.fromisoformat('2026-09-01T00:00:00+03:30'),
                        datetime.fromisoformat('2026-09-20T23:59:00+03:30'))


def test_a_mismatched_note_is_named_in_its_own_section():
    result = report(MISMATCHED)
    assert result['date_discrepancy_count'] == 1
    section = '\n'.join(render_date_discrepancies_markdown(result))
    assert '## Date mismatches' in section
    assert 'Nothing was silently corrected' in section
    assert '| Posted (local) | Post | Written day | Written date | Used |' in section


def test_the_section_states_what_was_written_and_what_was_used():
    section = '\n'.join(render_date_discrepancies_markdown(report(MISMATCHED)))
    rows = [line for line in section.splitlines() if line.startswith('| 2026-')]
    assert rows, section
    assert 'سه شنبه' in rows[0] and '14050623' in rows[0]
    assert '2026-09-08' in rows[0], 'the date the program read as the day is named: ' + rows[0]


def test_the_section_says_none_when_every_note_agrees_with_itself():
    section = '\n'.join(render_date_discrepancies_markdown(report(CONSISTENT)))
    assert 'None: every note that states a day agrees' in section


def test_the_html_report_carries_the_section_and_stays_escaped():
    page = render_html(report(MISMATCHED))
    assert '<h2>Date mismatches</h2>' in page
    assert '<script' not in page
    assert "default-src &#x27;none&#x27;" in page or "default-src 'none'" in page
