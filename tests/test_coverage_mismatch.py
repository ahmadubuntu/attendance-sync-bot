"""The user confirmed: register every uncovered overtime run, even when a manual document
overlaps the day differently, and flag the day in the report as not fully settled.

Wednesday 1405/06/25 is the real shape: our overtime runs are 16:00-18:45 and 19:30-22:00,
a manually created document covers 12:00-17:00, and the minute-level subtraction says 15
minutes are missing while the actually uncovered spans total 255. The guard that skipped
such a day silently must instead register the uncovered spans and mark the day so the
period report can say it was never fully settled.
"""
from datetime import datetime

from attendance_sync.kasra_reconcile import build_payloads

TEHRAN = '+03:30'


def run(start, end, minutes):
    return {'start_at': f'2026-09-16T{start}:00{TEHRAN}', 'end_at': f'2026-09-16T{end}:00{TEHRAN}',
            'minutes': minutes}


def doc(doc_id, start, end, minutes):
    return {'doc_id': doc_id, 'credit_type': 60054, 'start_time': start, 'end_time': end,
            'minutes': minutes, 'active': True, 'status_id': 201, 'day': '1405/06/25'}


def day(overtime_runs, documents, missing_overtime):
    return {'local_date': '2026-09-16', 'jalali_date': '1405/06/25',
            'runs': {'regular_remote': [run('08:00', '16:00', 480)],
                     'overtime_remote': overtime_runs},
            'documents': documents,
            'expected': {'regular_minutes': 480, 'overtime_minutes': sum(r['minutes'] for r in overtime_runs)},
            'requested': {'regular_minutes': 0, 'overtime_minutes': sum(d['minutes'] for d in documents)},
            'registered': {'regular_minutes': 0, 'overtime_minutes': 0},
            'pending': {'regular_minutes': 0, 'overtime_minutes': sum(d['minutes'] for d in documents)},
            'missing': {'regular_minutes': 0, 'overtime_minutes': missing_overtime},
            'missing_total_minutes': missing_overtime,
            'status': 'partial_overtime', 'flags': ['partial_overtime'], 'manual_review': False,
            'actionable': True}


def test_uncovered_overtime_runs_are_registered_even_when_a_manual_document_overlaps():
    result = {'days': [day([run('16:00', '18:45', 165), run('19:30', '22:00', 150)],
                           [doc('63009', '12:00', '17:00', 300)], 15)]}
    payloads = build_payloads(result, person_id='1219')
    spans = [(p['start_time'], p['end_time'], p['minutes']) for p in payloads
             if p['category'] == 'overtime_remote']
    assert ('17:00', '18:45', 105) in spans, spans
    assert ('19:30', '22:00', 150) in spans, spans


def test_the_day_is_flagged_as_not_fully_settled():
    result = {'days': [day([run('16:00', '18:45', 165), run('19:30', '22:00', 150)],
                           [doc('63009', '12:00', '17:00', 300)], 15)]}
    payloads = build_payloads(result, person_id='1219')
    overtime = [p for p in payloads if p['category'] == 'overtime_remote']
    assert overtime and all('coverage_mismatch' in p['warnings'] for p in overtime)
