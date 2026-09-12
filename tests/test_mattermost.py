import httpx
import pytest


def test_only_get_no_redirect_and_safe_failure():
    from attendance_sync.mattermost import Mattermost, APIError
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(302, headers={'location':'https://evil.example'}, text='secret')
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        api = Mattermost(client, 'https://chat.example', 'secret', sleep=lambda _:None)
        with pytest.raises(APIError) as error:
            api.get('/users/me')
    assert 'secret' not in str(error.value)
    assert len(seen) == 1 and seen[0].method == 'GET'


@pytest.mark.parametrize('status,count', [(401,1),(403,1),(429,3),(500,3)])
def test_bounded_retry(status,count):
    from attendance_sync.mattermost import Mattermost, APIError
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(status, headers={'Retry-After':'9999'}, text='secret')
    waits = []
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(APIError):
            Mattermost(client,'https://chat.example','secret', sleep=waits.append).get('/users/me')
    assert len(seen) == count
    assert all(w <= 5 for w in waits)


def test_cursor_pagination_includes_context_and_deduplicates():
    from attendance_sync.mattermost import collect
    pages = [ {'order':['b','a'], 'posts':{'b':{'id':'b','create_at':120},'a':{'id':'a','create_at':110}}},
              {'order':['a','old'], 'posts':{'a':{'id':'a','create_at':110},'old':{'id':'old','create_at':90}}} ]
    cursors = []
    def fetch(cursor):
        cursors.append(cursor)
        return pages[len(cursors)-1]
    rows = collect(fetch,100,130)
    assert [r['id'] for r in rows] == ['a','b']
    assert cursors == [None,'a']


@pytest.mark.parametrize('mode', ['repeat','limit','schema','order'])
def test_incomplete_coverage_fails(mode):
    from attendance_sync.mattermost import collect, APIError
    page = {'order':['a'], 'posts':{'a':{'id':'a','create_at':110}}}
    if mode == 'schema':
        page = {'order':['a'],'posts':{}}
    if mode == 'order':
        page = {'order':['a','b'],'posts':{'a':{'id':'a','create_at':90},'b':{'id':'b','create_at':110}}}
    with pytest.raises(APIError):
        collect(lambda _:page,100,130,max_pages=1 if mode=='limit' else 3)
