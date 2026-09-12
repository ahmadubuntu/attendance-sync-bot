"""CLI tests for the Kasra subcommands: injected reads, dry-run writes, private artifacts."""
import json

import pytest

from attendance_sync.kasra_reconcile import DEFAULT_DESCRIPTION

DAILY_COLUMNS = ['رديف', 'تاريخ', 'روز', 'ترددها', 'دوركاري', 'دوركاري خارج از موظفي', 'كسر حضور']
DOC_COLUMNS = ['', 'فیلترStatusID', 'فیلترDocID', 'فیلترDocTypeID', 'DocDescr', 'DocTitle', 'RSDate', 'REDate']


def segment(day, start, end, category, index=0):
    minutes = (int(end[:2]) * 60 + int(end[3:])) - (int(start[:2]) * 60 + int(start[3:]))
    return {'segment_id': f'{day}:{index}', 'parent_interval_id': f'pair:{day}', 'local_date': day,
            'start_at': f'{day}T{start}:00+03:30', 'end_at': f'{day}T{end}:00+03:30',
            'duration_minutes': minutes, 'category': category, 'submission_eligible': True}


SATURDAY = '2026-09-12'


def review_file(tmp_path, segments=None):
    path = tmp_path / 'review.json'
    path.write_text(json.dumps({'schema_version': 2, 'imported_segments': 'synthetic',
                                'window': {'from': '2026-09-05T00:00:00+03:30',
                                           'to': '2026-09-13T00:00:00+03:30', 'timezone': 'Asia/Tehran'},
                                'segments': segments if segments is not None else [
                                    segment(SATURDAY, '08:20', '17:20', 'regular_remote', 0),
                                    segment(SATURDAY, '17:20', '20:26', 'overtime_remote', 1)]}),
                    encoding='utf-8')
    return path


def doc_row(doc_id, status, title, day):
    return ['', status, str(doc_id), '1', '', title, day, day]


class FakeKasra:
    """Duck-typed stand-in for KasraBrowser: records every call, writes nothing."""

    def __init__(self, *, documents=None, daily=None, person_id='4321'):
        self.daily = daily if daily is not None else {'columns': DAILY_COLUMNS, 'rows': [
            ['1', '1405/06/21', 'شنبه', 'دوركاري خارج از موظفي', '', '03:31', '09:00']]}
        self.documents = documents if documents is not None else {'columns': DOC_COLUMNS, 'rows': [
            doc_row(1, '203', 'مجوز دوركاري ساعتي از تاریخ 1405/06/21 تا تاریخ 1405/06/21 از 08:20 تا 17:20',
                    '1405/06/21'),
            doc_row(2, '203', 'مجوز دوركاري خارج از موظفي از تاریخ 1405/06/21 تا تاریخ 1405/06/21 از 17:20 تا 18:11',
                    '1405/06/21')]}
        self.person_id = person_id
        self.calls = []
        self.written = []

    def open(self):
        self.calls.append('open')

    def close(self):
        self.calls.append('close')

    def read_daily_report(self, start, end):
        self.calls.append('read_daily_report')
        return self.daily

    def read_documents(self, start, end):
        self.calls.append('read_documents')
        return self.documents

    def read_person_id(self):
        self.calls.append('read_person_id')
        return self.person_id

    def write_credit_document(self, payload, confirm=False):
        self.calls.append('write_credit_document')
        self.written.append((payload, confirm))
        if confirm:
            self.documents['rows'].append(doc_row(900 + len(self.written), '201',
                                                  f"مجوز {payload['credit_type_title']} از تاریخ {payload['day']} "
                                                  f"تا تاریخ {payload['day']} از {payload['start_time']} "
                                                  f"تا {payload['end_time']}", payload['day']))
        return {'mode': 'dry_run' if not confirm else 'confirmed', 'written': confirm,
                'document_id': None, 'status_id': None, 'payload': payload}

    def delete_document(self, doc_id, confirm=False):
        self.calls.append('delete_document')
        return {'mode': 'dry_run' if not confirm else 'confirmed', 'deleted': confirm, 'doc_id': str(doc_id)}


def status_args(review, **extra):
    args = ['kasra-status', '--review', str(review), '--start', '1405/06/01', '--end', '1405/06/21']
    return args + [str(item) for pair in extra.items() for item in pair]


def test_kasra_status_prints_the_day_table_and_exits_zero(tmp_path, capsys):
    from attendance_sync.cli import main
    client = FakeKasra()
    assert main(['kasra-status', '--review', str(review_file(tmp_path)), '--start', '1405/06/01',
                 '--end', '1405/06/21'], kasra=client) == 0
    out = capsys.readouterr().out
    assert '2026-09-12' in out and '1405/06/21' in out and 'partial_overtime' in out
    assert 'unregistered' in out
    assert client.written == [] and 'write_credit_document' not in client.calls


def test_kasra_status_reports_missing_days_without_any_document(tmp_path, capsys):
    from attendance_sync.cli import main
    client = FakeKasra(documents={'columns': DOC_COLUMNS, 'rows': []})
    assert main(['kasra-status', '--review', str(review_file(tmp_path)), '--start', '1405/06/01',
                 '--end', '1405/06/21'], kasra=client) == 0
    out = capsys.readouterr().out
    assert 'no_document' in out and '"missing_overtime_minutes": 186' in out


def test_kasra_status_reads_a_private_snapshot_without_a_browser(tmp_path, capsys):
    from attendance_sync.cli import main
    snapshot = tmp_path / 'snapshot.json'
    snapshot.write_text(json.dumps({'daily': FakeKasra().daily, 'documents': FakeKasra().documents}),
                        encoding='utf-8')
    class Exploding:
        def __getattr__(self, name):
            raise AssertionError('snapshot mode must not touch Kasra')
    assert main(['kasra-status', '--review', str(review_file(tmp_path)), '--start', '1405/06/01',
                 '--end', '1405/06/21', '--snapshot', str(snapshot)], kasra=Exploding()) == 0
    assert 'partial_overtime' in capsys.readouterr().out


def test_kasra_status_saves_a_private_snapshot(tmp_path):
    from attendance_sync.cli import main
    target = tmp_path / 'private' / 'snapshot.json'
    client = FakeKasra()
    assert main(['kasra-status', '--review', str(review_file(tmp_path)), '--start', '1405/06/01',
                 '--end', '1405/06/21', '--save-snapshot', str(target)], kasra=client) == 0
    assert oct(target.stat().st_mode)[-3:] == '600'
    assert json.loads(target.read_text(encoding='utf-8'))['daily']['columns'] == DAILY_COLUMNS


def test_kasra_plan_writes_private_plan_with_payloads(tmp_path, capsys):
    from attendance_sync.cli import main
    plan_path = tmp_path / 'private' / 'kasra-plan.json'
    assert main(['kasra-plan', '--review', str(review_file(tmp_path)), '--start', '1405/06/01',
                 '--end', '1405/06/21', '--output', str(plan_path)], kasra=FakeKasra()) == 0
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    assert plan['description'] == DEFAULT_DESCRIPTION
    assert [(row['credit_type'], row['minutes']) for row in plan['payloads']] == [(60054, 135)]
    assert plan['payloads'][0]['start_time'] == '18:11'
    assert oct(plan_path.stat().st_mode)[-3:] == '600'
    assert str(plan_path) in capsys.readouterr().out


def test_kasra_submit_without_confirm_prints_payloads_and_writes_nothing(tmp_path, capsys):
    from attendance_sync.cli import main
    plan_path = tmp_path / 'private' / 'kasra-plan.json'
    main(['kasra-plan', '--review', str(review_file(tmp_path)), '--start', '1405/06/01',
          '--end', '1405/06/21', '--output', str(plan_path)], kasra=FakeKasra())
    class Exploding:
        def __getattr__(self, name):
            raise AssertionError('dry run must not open Kasra')
    assert main(['kasra-submit', '--plan', str(plan_path)], kasra=Exploding()) == 0
    out = capsys.readouterr().out
    assert '18:11' in out and '20:26' in out and '60054' in out and DEFAULT_DESCRIPTION in out
    assert 'dry_run' in out


def test_kasra_submit_confirm_calls_the_writer_and_records_the_document_id(tmp_path, capsys):
    from attendance_sync.cli import main
    plan_path = tmp_path / 'private' / 'kasra-plan.json'
    created_path = tmp_path / 'private' / 'kasra-created.json'
    main(['kasra-plan', '--review', str(review_file(tmp_path)), '--start', '1405/06/01',
          '--end', '1405/06/21', '--output', str(plan_path)], kasra=FakeKasra())
    client = FakeKasra()
    assert main(['kasra-submit', '--plan', str(plan_path), '--confirm',
                 '--created-docs', str(created_path)], kasra=client) == 0
    assert [confirm for _, confirm in client.written] == [True]
    payload, _ = client.written[0]
    assert (payload['credit_type'], payload['start_time'], payload['end_time'], payload['minutes']) == \
        (60054, '18:11', '20:26', 135)
    created = json.loads(created_path.read_text(encoding='utf-8'))
    assert created['documents'][0]['document_id'] == '901'
    assert created['documents'][0]['status_id'] == 201
    assert oct(created_path.stat().st_mode)[-3:] == '600'


def test_kasra_submit_skips_midnight_payloads_unless_asked(tmp_path):
    from attendance_sync.cli import main
    plan_path = tmp_path / 'private' / 'kasra-plan.json'
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan = {'payloads': [{'credit_type': 60054, 'credit_type_title': 'دوركاري خارج از موظفي', 'day': '1405/06/26',
                          'start_date': '۱۴۰۵/۰۶/۲۶', 'start_time': '23:00', 'end_date': '۱۴۰۵/۰۶/۲۷',
                          'end_time': '00:00', 'day_count': '1', 'minutes': 60, 'person_id': '4321',
                          'description': DEFAULT_DESCRIPTION, 'requires_review': True, 'warnings': ['ends_at_midnight']}]}
    plan_path.write_text(json.dumps(plan), encoding='utf-8')
    client = FakeKasra()
    created = tmp_path / 'kasra-created.json'
    assert main(['kasra-submit', '--plan', str(plan_path), '--confirm',
                 '--created-docs', str(created)], kasra=client) == 0
    assert client.written == []
    assert main(['kasra-submit', '--plan', str(plan_path), '--confirm', '--include-review',
                 '--created-docs', str(created)], kasra=client) == 0
    assert [confirm for _, confirm in client.written] == [True]


def test_kasra_submit_deletes_only_the_named_document_id(tmp_path, capsys):
    from attendance_sync.cli import main
    plan_path = tmp_path / 'plan.json'
    plan_path.write_text(json.dumps({'payloads': []}), encoding='utf-8')
    client = FakeKasra()
    assert main(['kasra-submit', '--plan', str(plan_path), '--delete-doc-id', '900002'], kasra=client) == 0
    assert client.calls == [] and '900002' in capsys.readouterr().out
    assert main(['kasra-submit', '--plan', str(plan_path), '--delete-doc-id', '900002', '--confirm'],
                kasra=client) == 0
    assert 'delete_document' in client.calls


def test_kasra_plan_rejects_a_missing_review_file(tmp_path, capsys):
    from attendance_sync.cli import main
    assert main(['kasra-plan', '--review', str(tmp_path / 'absent.json'), '--start', '1405/06/01',
                 '--end', '1405/06/21', '--output', str(tmp_path / 'private' / 'plan.json')],
                kasra=FakeKasra()) == 2
    assert 'secret' not in capsys.readouterr().err


def test_kasra_submit_rejects_a_missing_plan(tmp_path, capsys):
    from attendance_sync.cli import main
    assert main(['kasra-submit', '--plan', str(tmp_path / 'absent.json')], kasra=FakeKasra()) == 2
    assert 'Invalid configuration or plan' in capsys.readouterr().err


def test_kasra_commands_reject_a_malformed_range(tmp_path, capsys):
    from attendance_sync.cli import main
    for bad in (('1405/6/1', '1405/06/21'), ('1405/06/21', '1405/06/01'), ('14/06/01', '1405/06/21')):
        code = main(['kasra-status', '--review', str(review_file(tmp_path)), '--start', bad[0],
                     '--end', bad[1]], kasra=FakeKasra())
        assert code == 2
    assert 'Invalid' in capsys.readouterr().err or 'invalid' in capsys.readouterr().err


def test_preview_still_requires_preview_only_help():
    from attendance_sync.cli import main
    with pytest.raises(SystemExit) as exit_code:
        main(['--help'])
    assert exit_code.value.code == 0


class SilentKasra(FakeKasra):
    """Writer whose save never lands: nothing is appended and read-back finds nothing."""

    def write_credit_document(self, payload, confirm=False):
        self.calls.append('write_credit_document')
        self.written.append((payload, confirm))
        return {'mode': 'confirmed', 'written': True, 'document_id': None, 'status_id': None, 'payload': payload}


class FailingSecondWrite(FakeKasra):
    """First save lands, second one raises."""

    def write_credit_document(self, payload, confirm=False):
        if len(self.written) == 1:
            raise RuntimeError('second save failed')
        return super().write_credit_document(payload, confirm=confirm)


def two_payload_plan(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    template = {'day': '1405/06/21', 'start_date': '۱۴۰۵/۰۶/۲۱', 'end_date': '۱۴۰۵/۰۶/۲۱',
                'day_count': '1', 'person_id': '4321', 'description': DEFAULT_DESCRIPTION,
                'requires_review': False}
    payloads = [dict(template, credit_type=14085, credit_type_title='دوركاري ساعتي',
                     start_time='09:00', end_time='18:00', minutes=540),
                dict(template, credit_type=60054, credit_type_title='دوركاري خارج از موظفي',
                     start_time='18:00', end_time='21:06', minutes=186)]
    path.write_text(json.dumps({'schema_version': 1, 'payloads': payloads}), encoding='utf-8')
    return path


def test_confirmed_submit_that_cannot_be_read_back_is_reported_as_failure(tmp_path, capsys):
    from attendance_sync.cli import main
    plan_path = two_payload_plan(tmp_path / 'plan.json')
    created_path = tmp_path / 'private' / 'kasra-created.json'
    code = main(['kasra-submit', '--plan', str(plan_path), '--confirm',
                 '--created-docs', str(created_path)], kasra=SilentKasra())
    captured = capsys.readouterr()
    assert code != 0, 'a save that could not be read back must not report success'
    assert '"written": 0' in captured.out or '"written": 0' in captured.err
    record = json.loads(created_path.read_text(encoding='utf-8'))
    assert [row['read_back'] for row in record['documents']] == [False, False]


def test_created_records_are_flushed_before_a_later_write_fails(tmp_path, capsys):
    from attendance_sync.cli import main
    plan_path = two_payload_plan(tmp_path / 'plan.json')
    created_path = tmp_path / 'private' / 'kasra-created.json'
    code = main(['kasra-submit', '--plan', str(plan_path), '--confirm',
                 '--created-docs', str(created_path)], kasra=FailingSecondWrite())
    assert code != 0
    record = json.loads(created_path.read_text(encoding='utf-8'))
    assert len(record['documents']) == 1
    assert record['documents'][0]['read_back'] is True
    assert record['documents'][0]['document_id'] is not None


def test_created_docs_path_is_validated_before_any_write(tmp_path, monkeypatch, capsys):
    from attendance_sync.cli import main
    monkeypatch.chdir(tmp_path)
    plan_path = two_payload_plan(tmp_path / 'plan.json')
    client = FakeKasra()
    code = main(['kasra-submit', '--plan', str(plan_path), '--confirm',
                 '--created-docs', 'kasra-created.json'], kasra=client)
    assert code == 2
    assert client.written == []
    assert 'Invalid' in capsys.readouterr().err


def test_saved_session_state_is_owner_only(tmp_path):
    from attendance_sync import kasra_browser
    state = tmp_path / 'var' / 'session-state.json'

    class Context:
        def storage_state(self, path):
            with open(path, 'w', encoding='utf-8') as stream:
                stream.write('{}')

    kasra_browser.save_session_state(Context(), state)
    assert oct(state.stat().st_mode)[-3:] == '600'
