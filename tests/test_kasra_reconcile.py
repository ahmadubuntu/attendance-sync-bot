"""Reconciliation between extracted work intervals and Kasra documents.

Synthetic data only: no browser, no network, no private records.
"""
import json
import stat

import pytest

from attendance_sync.kasra_reconcile import (
    CREDIT_TYPE_OVERTIME, CREDIT_TYPE_REGULAR, DEFAULT_DESCRIPTION, build_payloads,
    daily_report_days, documents_from_table, jalali_month_label, jalali_text, minutes_from_label,
    our_days, reconcile, subtract_intervals, write_plan,
    STATUS_APPROVED, STATUS_PENDING, label_from_minutes, persian_digits, gregorian_from_jalali,
)

SATURDAY = '2026-09-12'      # 1405/06/21
WEDNESDAY = '2026-09-16'     # 1405/06/25
THURSDAY = '2026-09-17'      # 1405/06/26
FRIDAY = '2026-09-18'        # 1405/06/27


def segment(day, start, end, category, eligible=True, index=0):
    minutes = (int(end[:2]) * 60 + int(end[3:])) - (int(start[:2]) * 60 + int(start[3:]))
    return {'segment_id': f'{day}:segment:{index}', 'parent_interval_id': f'pair:{day}',
            'local_date': day, 'start_at': f'{day}T{start}:00+03:30', 'end_at': f'{day}T{end}:00+03:30',
            'duration_minutes': minutes, 'category': category, 'classification_basis': 'daily_quota',
            'policy_version': 'test', 'submission_eligible': eligible}


def work_day(day, regular=None, overtime=None):
    rows = []
    if regular:
        rows.append(segment(day, regular[0], regular[1], 'regular_remote', index=len(rows)))
    if overtime:
        rows.append(segment(day, overtime[0], overtime[1], 'overtime_remote', index=len(rows)))
    return rows


def document(doc_id, day, credit_type, start, end, status_id=STATUS_APPROVED):
    minutes = (int(end[:2]) * 60 + int(end[3:])) - (int(start[:2]) * 60 + int(start[3:]))
    return {'doc_id': doc_id, 'credit_type': credit_type, 'status_id': status_id,
            'status_title': 'تایید شده' if status_id == STATUS_APPROVED else 'در روند',
            'day': day, 'start_time': start, 'end_time': end, 'minutes': minutes,
            'active': status_id in (STATUS_PENDING, STATUS_APPROVED), 'doc_type_id': 1}


def day_of(result, gregorian):
    return next(row for row in result['days'] if row['local_date'] == gregorian)


def test_minutes_are_exact_and_never_rounded():
    assert minutes_from_label('03:31') == 211
    assert minutes_from_label('00:00') == 0
    assert minutes_from_label('') == 0
    assert minutes_from_label('19:00') == 1140
    assert label_from_minutes(211) == '03:31'
    assert label_from_minutes(60) == '01:00'
    with pytest.raises(ValueError):
        minutes_from_label('3:1')


def test_jalali_helpers_are_reversible_and_fa_digit_aware():
    assert jalali_text(SATURDAY) == '1405/06/21'
    assert gregorian_from_jalali('1405/06/21') == SATURDAY
    assert persian_digits('1405/06/21') == '۱۴۰۵/۰۶/۲۱'
    assert jalali_month_label(SATURDAY) == 'شهریور 1405'


def test_our_days_merges_contiguous_segments_per_category():
    rows = work_day(SATURDAY, ('08:20', '17:20'), ('17:20', '19:40'))
    days = our_days(rows)
    assert len(days) == 1
    day = days[0]
    assert day['local_date'] == SATURDAY and day['jalali_date'] == '1405/06/21'
    assert day['regular_minutes'] == 540 and day['overtime_minutes'] == 140
    assert [run['minutes'] for run in day['runs']['regular_remote']] == [540]
    assert [run['minutes'] for run in day['runs']['overtime_remote']] == [140]


def test_our_days_keeps_real_gaps_as_separate_runs():
    rows = (work_day(SATURDAY, ('08:00', '12:00'))
            + [segment(SATURDAY, '13:00', '17:00', 'regular_remote', index=1),
               segment(SATURDAY, '17:00', '19:00', 'overtime_remote', index=2)])
    days = our_days(rows)
    assert [run['minutes'] for run in days[0]['runs']['regular_remote']] == [240, 240]
    assert days[0]['regular_minutes'] == 480


def test_our_days_ignores_non_eligible_segments():
    rows = work_day(SATURDAY, ('08:20', '17:20'), ('17:20', '19:40'))
    rows[0]['submission_eligible'] = False
    days = our_days(rows)
    assert days[0]['regular_minutes'] == 0 and days[0]['overtime_minutes'] == 140


def test_day_with_both_documents_is_ok():
    ours = our_days(work_day(SATURDAY, ('08:20', '17:20'), ('17:20', '19:40')))
    kasra = {'1405/06/21': {'jalali_date': '1405/06/21', 'documents': [
        document('1', '1405/06/21', CREDIT_TYPE_REGULAR, '08:20', '17:20'),
        document('2', '1405/06/21', CREDIT_TYPE_OVERTIME, '17:20', '19:40')]}}
    result = reconcile(ours, kasra)
    day = day_of(result, SATURDAY)
    assert day['status'] == 'ok' and day['actionable'] is False
    assert day['missing']['regular_minutes'] == 0 and day['missing']['overtime_minutes'] == 0
    assert result['unregistered'] == [] and result['counts']['missing_total_minutes'] == 0


def test_day_without_documents_reports_both_missing_exactly():
    ours = our_days(work_day(SATURDAY, ('08:20', '17:20'), ('17:20', '19:40')))
    result = reconcile(ours, {})
    day = day_of(result, SATURDAY)
    assert day['status'] == 'no_document' and day['actionable'] is True
    assert day['missing']['regular_minutes'] == 540 and day['missing']['overtime_minutes'] == 140
    assert result['unregistered'] == ['1405/06/21']
    assert result['counts']['missing_total_minutes'] == 680


def test_partially_registered_overtime_reports_exact_missing_minutes():
    ours = our_days(work_day(SATURDAY, ('08:20', '17:20'), ('17:20', '20:26')))
    kasra = {'1405/06/21': {'jalali_date': '1405/06/21', 'documents': [
        document('1', '1405/06/21', CREDIT_TYPE_REGULAR, '08:20', '17:20'),
        document('2', '1405/06/21', CREDIT_TYPE_OVERTIME, '17:20', '18:11')]}}
    result = reconcile(ours, kasra)
    day = day_of(result, SATURDAY)
    assert day['status'] == 'partial_overtime' and day['actionable'] is True
    assert day['missing']['overtime_minutes'] == 135
    assert day['requested']['overtime_minutes'] == 51
    assert day['flags'] == ['partial_overtime']


def test_pending_document_counts_as_requested_but_stays_visible():
    ours = our_days(work_day(SATURDAY, ('08:20', '17:20'), ('17:20', '19:40')))
    kasra = {'1405/06/21': {'jalali_date': '1405/06/21', 'documents': [
        document('1', '1405/06/21', CREDIT_TYPE_REGULAR, '08:20', '17:20', status_id=STATUS_PENDING),
        document('2', '1405/06/21', CREDIT_TYPE_OVERTIME, '17:20', '19:40', status_id=STATUS_PENDING)]}}
    result = reconcile(ours, kasra)
    day = day_of(result, SATURDAY)
    assert day['status'] == 'pending_approval'
    assert day['actionable'] is False and day['missing_total_minutes'] == 0
    assert day['pending']['regular_minutes'] == 540 and day['pending']['overtime_minutes'] == 140
    assert day['registered']['regular_minutes'] == 0
    assert result['counts']['pending_days'] == 1


def test_missing_regular_with_pending_overtime_keeps_both_visible():
    ours = our_days(work_day(SATURDAY, ('08:20', '17:20'), ('17:20', '19:40')))
    kasra = {'1405/06/21': {'jalali_date': '1405/06/21', 'documents': [
        document('2', '1405/06/21', CREDIT_TYPE_OVERTIME, '17:20', '19:40', status_id=STATUS_PENDING)]}}
    result = reconcile(ours, kasra)
    day = day_of(result, SATURDAY)
    assert day['status'] == 'missing_regular'
    assert day['flags'] == ['missing_regular', 'pending_approval']
    assert day['missing']['regular_minutes'] == 540 and day['missing']['overtime_minutes'] == 0


def test_inactive_document_does_not_count_as_requested():
    ours = our_days(work_day(SATURDAY, ('08:20', '17:20'), None))
    kasra = {'1405/06/21': {'jalali_date': '1405/06/21', 'documents': [
        {**document('1', '1405/06/21', CREDIT_TYPE_REGULAR, '08:20', '17:20', status_id=205),
         'status_title': 'حذف شده'}]}}
    result = reconcile(ours, kasra)
    day = day_of(result, SATURDAY)
    assert day['status'] == 'missing_regular' and day['missing']['regular_minutes'] == 540


def test_document_of_another_day_does_not_cover():
    ours = our_days(work_day(SATURDAY, None, ('17:20', '19:40')))
    kasra = {'1405/06/20': {'jalali_date': '1405/06/20', 'documents': [
        document('1', '1405/06/20', CREDIT_TYPE_OVERTIME, '17:20', '19:40')]}}
    result = reconcile(ours, kasra)
    assert day_of(result, SATURDAY)['status'] == 'missing_overtime'


def test_thursday_overtime_only_day_never_reports_missing_regular():
    ours = our_days(work_day(THURSDAY, None, ('16:00', '20:26')))
    result = reconcile(ours, {})
    day = day_of(result, THURSDAY)
    assert day['status'] == 'missing_overtime'
    assert day['expected']['regular_minutes'] == 0
    assert day['missing']['overtime_minutes'] == 266
    assert 'missing_regular' not in day['flags']


def test_thursday_and_friday_fully_registered_are_ok():
    rows = work_day(THURSDAY, None, ('16:00', '20:26')) + work_day(FRIDAY, None, ('09:00', '10:31'))
    ours = our_days(rows)
    kasra = {
        '1405/06/26': {'jalali_date': '1405/06/26', 'documents': [
            document('1', '1405/06/26', CREDIT_TYPE_OVERTIME, '16:00', '20:26')]},
        '1405/06/27': {'jalali_date': '1405/06/27', 'documents': [
            document('2', '1405/06/27', CREDIT_TYPE_OVERTIME, '09:00', '10:31')]}}
    result = reconcile(ours, kasra)
    assert [row['status'] for row in result['days']] == ['ok', 'ok']
    assert result['counts']['ok_days'] == 2


def test_days_without_work_are_never_reported_as_missing():
    rows = work_day(WEDNESDAY, ('08:00', '16:00'), None)
    ours = our_days(rows)
    # Kasra knows about the quiet Thursday and a holiday, with documents for another person-day.
    kasra = {
        '1405/06/26': {'jalali_date': '1405/06/26', 'day_type': 'استراحت', 'documents': []},
        '1405/06/23': {'jalali_date': '1405/06/23', 'day_type': 'ولادت رسول اکرم (ص)', 'documents': [
            document('9', '1405/06/23', CREDIT_TYPE_REGULAR, '08:00', '16:00')]}}
    result = reconcile(ours, kasra)
    for row in result['days']:
        if row['expected']['regular_minutes'] + row['expected']['overtime_minutes'] == 0:
            assert row['status'] != 'no_document'
            assert row['missing_total_minutes'] == 0
            assert row['actionable'] is False
    assert result['unregistered'] == [] or result['unregistered'] == ['1405/06/25']


def test_reconcile_never_invents_days_absent_from_our_data():
    ours = our_days(work_day(SATURDAY, ('08:20', '17:20'), ('17:20', '19:40')))
    result = reconcile(ours, {})
    assert [row['local_date'] for row in result['days']] == [SATURDAY]


def test_report_minutes_are_parsed_and_only_informational():
    ours = our_days(work_day(SATURDAY, ('08:20', '17:20'), ('17:20', '19:40')))
    kasra = {'1405/06/21': {'jalali_date': '1405/06/21', 'day_type': 'دوركاري ساعتي (منتظر تایید)',
                            'report_regular_minutes': 0, 'report_overtime_minutes': 140, 'documents': []}}
    result = reconcile(ours, kasra)
    day = day_of(result, SATURDAY)
    assert day['report']['overtime_minutes'] == 140
    assert day['status'] == 'no_document'          # the report column is not a document
    assert day['missing']['overtime_minutes'] == 140


def test_daily_report_rows_map_to_registered_minutes():
    columns = ['رديف', 'تاريخ', 'روز', 'ترددها', 'دوركاري', 'دوركاري خارج از موظفي', 'كسر حضور']
    rows = [
        ['1', '1405/06/21', 'شنبه', 'دوركاري خارج از موظفي', '', '03:31', '09:00'],
        ['2', '1405/06/17', 'سه شنبه', 'دوركاري ساعتي (منتظر تایید)', '04:00', '', '09:00'],
        ['', 'جمع', '', '', '', '03:31', '123:00'],
    ]
    days = daily_report_days(columns, rows)
    assert sorted(days) == ['1405/06/17', '1405/06/21']
    assert days['1405/06/21']['report_overtime_minutes'] == 211
    assert days['1405/06/21']['report_regular_minutes'] == 0
    assert days['1405/06/17']['report_regular_minutes'] == 240
    assert days['1405/06/17']['pending_markers'] == ['regular_remote']


def test_document_table_rows_map_to_credit_types_and_ranges():
    columns = ['', 'فیلترStatusID', 'فیلترDocID', 'فیلترDocTypeID', 'فیلتر تاریخ درخواست',
               'DocDescr', 'DocTitle', 'RSDate', 'REDate']
    rows = [
        ['', '203', '900001', '1', '1405/06/11', '',
         'مجوز دوركاري ساعتي از تاریخ 1405/06/01 تا تاریخ 1405/06/01 از 08:20 تا 17:20',
         '1405/06/01', '1405/06/01'],
        ['', '201', '900002', '1', '1405/06/21', '',
         'مجوز دوركاري خارج از موظفي از تاریخ 1405/06/17 تا تاریخ 1405/06/17 از 16:20 تا 19:00',
         '1405/06/17', '1405/06/17'],
        ['', '203', '9', '1', '1405/06/11', 'مجوز ماموريت ساعتي', '', '1405/06/09', '1405/06/09'],
    ]
    documents = documents_from_table(columns, rows)
    assert [row['credit_type'] for row in documents] == [CREDIT_TYPE_REGULAR, CREDIT_TYPE_OVERTIME, None]
    assert documents[0]['day'] == '1405/06/01' and documents[0]['minutes'] == 540
    assert documents[1]['minutes'] == 160 and documents[1]['status_id'] == STATUS_PENDING
    assert documents[2]['minutes'] is None


def test_documents_from_table_ignores_rows_without_document_id():
    columns = ['', 'فیلترDocID', 'DocTitle', 'RSDate', 'REDate', 'فیلترStatusID']
    rows = [['', '', '', '', '', ''], ['', 'سرصفحه', '', '', '', '']]
    assert documents_from_table(columns, rows) == []


def test_subtract_intervals_returns_exact_remaining_runs():
    runs = [{'start_at': f'{SATURDAY}T17:20:00+03:30', 'end_at': f'{SATURDAY}T19:40:00+03:30', 'minutes': 140}]
    covered = [(f'{SATURDAY}T17:20:00+03:30', f'{SATURDAY}T18:40:00+03:30')]
    missing = subtract_intervals(runs, covered)
    assert [(row['start_at'], row['end_at'], row['minutes']) for row in missing] == [
        (f'{SATURDAY}T18:40:00+03:30', f'{SATURDAY}T19:40:00+03:30', 60)]


def test_subtract_intervals_keeps_a_middle_gap():
    runs = [{'start_at': f'{SATURDAY}T08:00:00+03:30', 'end_at': f'{SATURDAY}T16:00:00+03:30', 'minutes': 480}]
    covered = [(f'{SATURDAY}T09:00:00+03:30', f'{SATURDAY}T10:00:00+03:30'),
               (f'{SATURDAY}T14:00:00+03:30', f'{SATURDAY}T14:30:00+03:30')]
    missing = subtract_intervals(runs, covered)
    assert [(row['start_at'][11:16], row['minutes']) for row in missing] == [
        ('08:00', 60), ('10:00', 240), ('14:30', 90)]


def test_payloads_cover_only_unregistered_minutes_with_exact_times():
    ours = our_days(work_day(SATURDAY, ('08:20', '17:20'), ('17:20', '20:26')))
    kasra = {'1405/06/21': {'jalali_date': '1405/06/21', 'documents': [
        document('1', '1405/06/21', CREDIT_TYPE_REGULAR, '08:20', '17:20'),
        document('2', '1405/06/21', CREDIT_TYPE_OVERTIME, '17:20', '18:11')]}}
    result = reconcile(ours, kasra)
    payloads = build_payloads(result, person_id='4321')
    assert len(payloads) == 1
    payload = payloads[0]
    assert payload['credit_type'] == CREDIT_TYPE_OVERTIME
    assert payload['start_time'] == '18:11' and payload['end_time'] == '20:26'
    assert payload['minutes'] == 135
    assert payload['description'] == DEFAULT_DESCRIPTION
    assert payload['person_id'] == '4321' and payload['day_count'] == '1'
    assert payload['start_date'] == '۱۴۰۵/۰۶/۲۱' and payload['requires_review'] is False


def test_payloads_use_the_two_confirmed_credit_types_per_day():
    ours = our_days(work_day(SATURDAY, ('08:20', '17:20'), ('17:20', '19:40')))
    payloads = build_payloads(reconcile(ours, {}), person_id='4321')
    assert [(row['category'], row['credit_type'], row['minutes']) for row in payloads] == [
        ('regular_remote', CREDIT_TYPE_REGULAR, 540), ('overtime_remote', CREDIT_TYPE_OVERTIME, 140)]
    assert {row['credit_type_title'] for row in payloads} == {'دوركاري ساعتي', 'دوركاري خارج از موظفي'}


def test_payload_description_is_configurable_in_one_place():
    ours = our_days(work_day(SATURDAY, None, ('16:00', '20:26')))
    payloads = build_payloads(reconcile(ours, {}), person_id='4321', description='CUSTOM')
    assert [row['description'] for row in payloads] == ['CUSTOM']


def test_payload_ending_at_midnight_is_flagged_for_review():
    rows = [segment(SATURDAY, '22:00', '23:59', 'overtime_remote')]
    rows.append({**rows[0], 'segment_id': 'x', 'start_at': f'{THURSDAY}T23:00:00+03:30',
                 'end_at': f'{FRIDAY}T00:00:00+03:30', 'local_date': THURSDAY, 'duration_minutes': 60})
    ours = our_days(rows)
    payloads = build_payloads(reconcile(ours, {}), person_id='4321')
    midnight = next(row for row in payloads if row['day'] == '1405/06/26')
    assert midnight['end_time'] == '00:00' and midnight['end_date'] == '۱۴۰۵/۰۶/۲۷'
    assert midnight['requires_review'] is True and midnight['minutes'] == 60


def test_no_payload_when_registration_has_no_document_times():
    ours = our_days(work_day(SATURDAY, None, ('16:00', '20:26')))
    kasra = {'1405/06/21': {'jalali_date': '1405/06/21', 'report_overtime_minutes': 140,
                            'documents': [{'doc_id': '5', 'credit_type': CREDIT_TYPE_OVERTIME,
                                           'status_id': STATUS_PENDING, 'status_title': 'در روند',
                                           'day': '1405/06/21', 'start_time': None, 'end_time': None,
                                           'minutes': None, 'active': True}]}}
    result = reconcile(ours, kasra)
    assert day_of(result, SATURDAY)['manual_review'] is True
    assert build_payloads(result, person_id='4321') == []


def test_plan_writer_is_private(tmp_path):
    plan = {'days': [], 'payloads': []}
    path = write_plan(plan, tmp_path/'private'/'kasra-plan.json')
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert json.loads(path.read_text(encoding='utf-8')) == plan
