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
