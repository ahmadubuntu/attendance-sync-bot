import json
import httpx
import pytest
from test_parser import post

ENV = {'GOFT_URL':'https://chat.example','GOFT_TOKEN':'secret','GOFT_CHANNEL_ID':'a'*26}


def test_preview_get_only_and_context(tmp_path):
    from attendance_sync.cli import main
    seen=[]
    rows = [post('شنبه خروج 1700','2026-09-05T14:00:00+00:00','e'),post('شنبه ورود 0800','2026-09-05T05:00:00+00:00','i')]
    def handler(request):
        seen.append(request)
        assert request.method == 'GET' and request.url.host == 'chat.example'
        if request.url.path.endswith('/users/me'):
            return httpx.Response(200,json={'id':'self'})
        if request.url.path.endswith('/posts'):
            data = {'order':[],'posts':{}} if request.url.params.get('before') else {'order':[r['id'] for r in rows],'posts':{r['id']:r for r in rows}}
            return httpx.Response(200,json=data)
        return httpx.Response(200,json={'id':'a'*26})
    args=['preview','--from','2026-09-05T10:00:00+00:00','--to','2026-09-06T00:00:00+00:00','--output',str(tmp_path/'out'/'review')]
    assert main(args,env=ENV,transport=httpx.MockTransport(handler)) == 0
    report=json.loads((tmp_path/'out'/'review.json').read_text())
    assert report['coverage_complete'] and report['candidate_count']==1
    assert report['events'][0]['paired_entry_id']=='i:0'
    assert len(seen)>=4


@pytest.mark.parametrize('args', [['--days','0'],['--from','2026-09-01','--to','2026-09-02'],['--from','2026-09-03T00:00:00Z','--to','2026-09-02T00:00:00Z'],['--days','14','--from','2026-09-01T00:00:00Z']])
def test_invalid_window(args,capsys):
    from attendance_sync.cli import main
    assert main(['preview']+args,env=ENV)==2


def test_safe_debug_failure(capsys):
    from attendance_sync.cli import main
    def handler(request):
        raise httpx.ConnectError('secret password body', request=request)
    assert main(['preview','--debug'],env=ENV,transport=httpx.MockTransport(handler))==2
    text=capsys.readouterr().err
    assert 'secret' not in text and 'password' not in text


WINDOW = ['--from','2026-09-13T00:00:00+03:30','--to','2026-09-20T00:00:00+03:30']


def preview(tmp_path, rows, extra=(), env=ENV):
    from attendance_sync.cli import main
    def handler(request):
        assert request.method == 'GET' and request.url.host == 'chat.example'
        if request.url.path.endswith('/users/me'):
            return httpx.Response(200,json={'id':'self'})
        if request.url.path.endswith('/posts'):
            listed = sorted(rows, key=lambda r: r['create_at'], reverse=True)
            data = {'order':[],'posts':{}} if request.url.params.get('before') else {'order':[r['id'] for r in listed],'posts':{r['id']:r for r in listed}}
            return httpx.Response(200,json=data)
        return httpx.Response(200,json={'id':'a'*26})
    code = main(['preview']+WINDOW+['--output',str(tmp_path/'out'/'review')]+list(extra),env=env,transport=httpx.MockTransport(handler))
    path = tmp_path/'out'/'review.json'
    return code, (json.loads(path.read_text()) if code == 0 else None)


def test_preview_applies_confirmed_corrections_file(tmp_path):
    from attendance_sync.corrections import source_fingerprint
    rows = [post('دوشنبه 14050622\nورود 0810','2026-09-14T08:10:00+03:30','in'),
            post('خروج 1710','2026-09-14T17:10:00+03:30','out')]
    config = tmp_path/'corrections.json'
    config.write_text(json.dumps({'schema_version':1,'date_corrections':[dict(
        post_id='in', source_fingerprint=source_fingerprint(rows[0]), raw_date='14050622',
        target_date='2026-09-14', confirmed_by='user')]}))
    code, report = preview(tmp_path, rows, ['--corrections',str(config),'--as-of','2026-09-20T00:00:00+03:30'])
    assert code == 0
    assert {e['date'] for e in report['events']} == {'2026-09-14'}
    assert report['events'][0]['correction']['confirmed_by'] == 'user'
    assert report['kasra_check'] == 'not_performed'


def test_preview_missing_corrections_file_is_safe(tmp_path):
    rows = [post('شنبه 14050621\nورود 0810 خروج 1710','2026-09-12T17:10:00+03:30','both')]
    code, report = preview(tmp_path, rows, ['--corrections',str(tmp_path/'absent.json'),'--as-of','2026-09-20T00:00:00+03:30'])
    assert code == 0 and report['corrections_applied'] == 0


def test_preview_defers_current_day_without_registration(tmp_path):
    rows = [post('شنبه 14050621\nورود 0810 خروج 1710','2026-09-12T17:10:00+03:30','both')]
    code, report = preview(tmp_path, rows, ['--as-of','2026-09-12T18:00:00+03:30'])
    assert code == 0
    assert report['current_day'] == '2026-09-12'
    assert report['segments'] == [] and report['withheld_spans'] == []
    assert all(s['submission_eligible'] is False for s in report['deferred_spans'])


def test_preview_rejects_invalid_as_of(tmp_path, capsys):
    code, report = preview(tmp_path, [], ['--as-of','2026-09-12'])
    assert code == 2 and report is None
