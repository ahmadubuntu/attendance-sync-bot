"""Adapter contract tests. No browser is launched and no network is touched here."""
import pytest

from attendance_sync.kasra_browser import KasraBrowser, KasraError, work_period_option

PAYLOAD = {'credit_type': 60054, 'credit_type_title': 'دوركاري خارج از موظفي', 'day': '1405/06/21',
           'start_date': '۱۴۰۵/۰۶/۲۱', 'start_time': '18:11', 'end_date': '۱۴۰۵/۰۶/۲۱', 'end_time': '20:26',
           'day_count': '1', 'minutes': 135, 'person_id': '1219', 'description': 'bot description',
           'requires_review': False}


def client(**overrides):
    options = dict(state_path='var/kasra-recon/session-state.json', username='u', password='p')
    options.update(overrides)
    return KasraBrowser('https://kasra.example', **options)


def test_base_url_must_be_plain_https():
    for bad in ('http://kasra.example', 'kasra.example', 'https://u:p@kasra.example', 'https://kasra.example/?x=1'):
        with pytest.raises(ValueError):
            KasraBrowser(bad, state_path='state.json')


def test_write_is_dry_run_by_default_and_touches_no_page():
    browser = client()
    result = browser.write_credit_document(PAYLOAD)
    assert result == {'mode': 'dry_run', 'written': False, 'document_id': None, 'status_id': None,
                      'payload': PAYLOAD}
    assert browser.page is None


def test_confirmed_write_refuses_without_an_open_page():
    browser = client()
    with pytest.raises(KasraError):
        browser.write_credit_document(PAYLOAD, confirm=True)


def test_delete_is_dry_run_by_default_and_targets_one_document_id():
    browser = client()
    result = browser.delete_document('900002')
    assert result == {'mode': 'dry_run', 'deleted': False, 'doc_id': '900002'}
    assert browser.page is None


def test_confirmed_delete_refuses_without_an_open_page():
    browser = client()
    with pytest.raises(KasraError):
        browser.delete_document('900002', confirm=True)


def test_reads_never_open_a_browser_without_a_session_call():
    browser = client()
    with pytest.raises(KasraError):
        browser.read_daily_report('1405/06/01', '1405/06/21')


def test_credentials_are_optional_for_read_only_use():
    browser = client(username=None, password=None)
    assert browser.username is None and 'p' != getattr(browser, 'password', None)


def test_state_file_is_private_and_gitignored():
    from pathlib import Path
    assert 'var/' in Path(client().state_path).as_posix()
    assert 'var/' in Path('.gitignore').read_text(encoding='utf-8')


def test_work_period_option_matches_the_jalali_month_label():
    options = [['56', 'مهر 1405'], ['55', 'شهريور 1405'], ['54', 'مرداد 1405']]
    assert work_period_option(options, 'شهریور 1405') == '55'
    assert work_period_option(options, 'مهر 1405') == '56'
    assert work_period_option(options, 'دي 1404') is None
