"""Pin the writer's browser timeout: the Kasra save form is slower than the old default."""
import argparse

from attendance_sync import cli
from attendance_sync.kasra_browser import KasraBrowser


def test_submit_defaults_to_a_timeout_that_outlasts_the_kasra_form():
    parser = cli.build_parser()
    args = parser.parse_args(['kasra-submit', '--plan', 'artifacts/kasra-plan.json'])
    assert args.timeout_ms >= 120000, 'the save form needs more than the old one-minute default'


def test_a_smaller_requested_timeout_is_raised_to_the_safe_floor():
    args = argparse.Namespace(state='var/kasra-recon/session-state.json', timeout_ms=5000)
    env = {'KASRA_URL': 'https://kasra.example.ir'}
    client = cli.build_kasra_client(args, env)
    assert client.timeout_ms >= 120000, 'a smaller request must not reintroduce the save timeout'
    assert isinstance(client, KasraBrowser)


def test_a_larger_requested_timeout_is_kept():
    args = argparse.Namespace(state='var/kasra-recon/session-state.json', timeout_ms=300000)
    client = cli.build_kasra_client(args, {'KASRA_URL': 'https://kasra.example.ir'})
    assert client.timeout_ms == 300000
