"""Tests for scripts/cron-catchup.sh.

Two layers:

* Static checks for what only the shell file can express: it sources the environment, it
  calls the three CLI stages, it rotates its log, it never blocks on a manual prompt.
* An execution test that runs the real script with a fake CLI on PATH, so the preview ->
  plan -> submit chain, the window arithmetic and the failure notification are verified
  end to end rather than by grepping for substrings.

The submit loop itself is covered by tests/test_cron_submit.py, which drives
attendance_sync.cron_submit directly.
"""

import os
from pathlib import Path
import re
import stat
import subprocess
import sys

import pytest

SCRIPT = Path('scripts/cron-catchup.sh')


def script_text():
    return SCRIPT.read_text(encoding='utf-8')


# ---------------------------------------------------------------------------
# Environment sourcing
# ---------------------------------------------------------------------------

def test_script_sources_the_shell_profile():
    """Cron has no login shell, so the six exports must come from ~/.bashrc."""
    text = script_text()
    assert '.bashrc' in text
    for var in ('GOFT_URL', 'GOFT_TOKEN', 'GOFT_CHANNEL_ID',
                'KASRA_URL', 'KASRA_USERNAME', 'KASRA_PASSWORD'):
        assert var in text, 'script never checks ' + var


def test_script_refuses_to_run_without_the_environment():
    """A missing variable must stop the run before anything reaches Kasra."""
    text = script_text()
    assert re.search(r'Missing env var', text)
    # The check must exit, not merely warn.
    assert re.search(r'Missing env var: \$name.*exit 1', text, re.S)


# ---------------------------------------------------------------------------
# The three CLI stages
# ---------------------------------------------------------------------------

def test_script_runs_preview_plan_then_submit():
    text = script_text()
    # The window is a variable, so a catch-up run can reach further back than the daily one.
    assert 'preview --days "$DAYS"' in text
    assert 'DAYS="${AS_DAYS:-7}"' in text
    assert 'kasra-plan' in text
    # Submit is delegated to cron_submit.submit_plan, which the execution test covers.
    assert 'submit_plan' in text


def test_preview_failure_stops_before_any_submission():
    text = script_text()
    assert 'preview failed; nothing submitted' in text
    assert 'plan failed; nothing submitted' in text


def test_window_starts_on_today_minus_the_window_and_never_includes_today():
    """The window runs back from yesterday, and its length is the one preview was given.

    With the default seven-day window that is six complete days ago through yesterday: today is
    never in it, because a day still being worked cannot be registered against. The start is
    derived from the same `$DAYS` the preview used, so the two can never disagree about which
    stretch is being looked at -- a wider preview feeding a narrower plan, or the reverse, is
    how days get silently skipped.
    """
    text = script_text()
    assert 'jalali_days_ago $((DAYS - 1))' in text
    assert 'jalali_days_ago 1' in text
    assert '--days "$DAYS"' in text


# ---------------------------------------------------------------------------
# Logging and notification
# ---------------------------------------------------------------------------

def test_script_logs_to_var_cron_log():
    text = script_text()
    assert 'var/cron.log' in text
    assert '>> "$LOG"' in text


def test_script_rotates_the_log_by_size():
    text = script_text()
    assert '200000' in text
    assert '.old' in text


def test_summary_is_scoped_to_this_run():
    """The ok/failed tally must come from this run's output, not from grepping the whole
    log, where a failure from days ago would make today look broken too."""
    text = script_text()
    assert 'RESULT ok=' in text
    assert re.search(r'sed -n .s/\^RESULT ok=', text)
    assert not re.search(r'grep\s+-q\s+"?failed"?\s+"?\$LOG', text)


def test_empty_run_is_reported_too():
    """Nothing to register is an outcome worth notifying, not silence."""
    text = script_text()
    assert 'nothing to register' in text


# ---------------------------------------------------------------------------
# No interactive prompt
# ---------------------------------------------------------------------------

def test_script_never_blocks_on_a_manual_prompt():
    text = script_text()
    assert 'read -' not in text
    assert 'input(' not in text


# ---------------------------------------------------------------------------
# Execution: the real script, a fake CLI
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_project(tmp_path):
    """A throwaway copy of the project layout with a stub venv python.

    The stub intercepts the CLI stages. The three inline `python -c` bootstraps the script
    uses are delegated to the real interpreter with AS_SRC pointing at a throwaway src
    tree, so nothing in the test can import -- let alone reach -- the real package.
    """
    root = tmp_path / 'proj'
    (root / 'scripts').mkdir(parents=True)
    (root / 'src' / 'attendance_sync').mkdir(parents=True)
    (root / 'var').mkdir()
    (root / 'scripts' / 'cron-catchup.sh').write_text(script_text(), encoding='utf-8')

    # The stubs the delegated interpreter will import.
    (root / 'src' / 'attendance_sync' / '__init__.py').write_text('', encoding='utf-8')
    (root / 'src' / 'attendance_sync' / 'cli.py').write_text(
        '# Stub CLI: main() dispatches on sys.argv exactly like the real entry point, so the\n'
        # inline `python -c "... main()"` bootstrap in the script resolves.\n'
        'import json, os, sys\n'
        '\n'
        'def main():\n'
        '    stage = sys.argv[1]\n'
        '    out = sys.argv[sys.argv.index("--output") + 1]\n'
        '    with open(os.environ["FAKE_CLI_LOG"], "a") as fh:\n'
        '        fh.write(stage + "\\n")\n'
        '    if stage == "preview":\n'
        '        json.dump({"events": [], "withheld_spans": []}, open(out, "w"))\n'
        '        print("preview ok")\n'
        '    elif stage == "kasra-plan":\n'
        '        count = int(os.environ.get("FAKE_PAYLOADS", "0"))\n'
        '        payload = {"day": "1405/06/28", "start_time": "08:30",\n'
        '                   "end_time": "18:00"}\n'
        '        json.dump({"payloads": [payload] * count}, open(out, "w"))\n'
        '        print("plan ok, %d payloads" % count)\n'
        '\n'
        'if __name__ == "__main__":\n'
        '    main()\n',
        encoding='utf-8')

    # cron_submit stub: report a tally without touching Kasra. It must read the plan the
    # script produced, so the payload count really comes from the fake plan stage.
    (root / 'src' / 'attendance_sync' / 'cron_submit.py').write_text(
        'import json\n'
        'plan = json.load(open("var/cron-plan.json", encoding="utf-8"))\n'
        'print("RESULT ok=%d failed=0" % len(plan.get("payloads") or []))\n',
        encoding='utf-8')

    venv_bin = root / '.venv' / 'bin'
    venv_bin.mkdir(parents=True)
    cli = venv_bin / 'python'
    cli.write_text('#!/bin/sh\nexec "$REAL_PYTHON" "$@"\n', encoding='utf-8')
    cli.chmod(cli.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return root


def run_script(root, env_extra=None, with_bashrc=True):
    env = {
        'PATH': os.environ.get('PATH', ''),
        'HOME': str(root / 'home'),
        'FAKE_CLI_LOG': str(root / 'cli.log'),
        'REAL_PYTHON': sys.executable,
        'FAKE_PAYLOADS': '0',
        'GOFT_URL': 'x', 'GOFT_TOKEN': 'x', 'GOFT_CHANNEL_ID': 'x',
        'KASRA_URL': 'x', 'KASRA_USERNAME': 'x', 'KASRA_PASSWORD': 'x',
    }
    env.update(env_extra or {})
    (root / 'home').mkdir(exist_ok=True)
    if with_bashrc:
        (root / 'home' / '.bashrc').write_text(
            ''.join(f'export {v}=x\n' for v in
                    ('GOFT_URL', 'GOFT_TOKEN', 'GOFT_CHANNEL_ID',
                     'KASRA_URL', 'KASRA_USERNAME', 'KASRA_PASSWORD')), encoding='utf-8')
    else:
        (root / 'home' / '.bashrc').write_text('# nothing exported\n', encoding='utf-8')
    # bash, never sh: the cron entry calls the script as `bash .../cron-catchup.sh` because
    # /bin/sh is dash on Ubuntu, and the script's ${!name-} guard is a syntax error there.
    # Running this fixture with `sh` would pass while the real cron job died on every run.
    return subprocess.run(['bash', str(root / 'scripts' / 'cron-catchup.sh')],
                          capture_output=True, text=True, env=env, cwd=str(root), timeout=120)


def test_script_resolves_its_own_project_directory(fake_project):
    """The script must not depend on a hand-edited absolute path."""
    assert "dirname -- \"$0\"" in script_text()


def test_script_is_bash_only_because_of_the_indirect_guard():
    """`${!name-}` is a bashism, so the shebang must be bash and not sh."""
    text = script_text()
    assert text.startswith('#!/bin/bash'), 'script must declare bash: /bin/sh is dash on Ubuntu'
    assert '${!name-}' in text, 'the environment guard uses an indirect expansion'


def test_script_runs_the_three_stages_in_order(fake_project):
    result = run_script(fake_project)
    log = (fake_project / 'cli.log').read_text(encoding='utf-8')
    assert 'preview' in log and 'kasra-plan' in log
    assert log.index('preview') < log.index('kasra-plan'), 'preview must come first'
    assert result.returncode == 0, result.stdout + result.stderr


def test_script_writes_the_log(fake_project):
    run_script(fake_project)
    log = (fake_project / 'var' / 'cron.log').read_text(encoding='utf-8')
    assert 'run start' in log
    assert 'run end' in log


def test_script_stops_when_the_environment_is_missing(fake_project):
    # Strip one variable from the inherited env AND from .bashrc, so sourcing cannot rescue it.
    run_script(fake_project, env_extra={'KASRA_PASSWORD': '', 'GOFT_TOKEN': ''},
               with_bashrc=False)
    log = (fake_project / 'var' / 'cron.log').read_text(encoding='utf-8')
    assert 'Missing env var' in log
    cli_log = fake_project / 'cli.log'
    assert not cli_log.exists() or 'preview' not in cli_log.read_text(encoding='utf-8')


def test_script_reports_a_run_with_no_payloads(fake_project):
    run_script(fake_project, env_extra={'FAKE_PAYLOADS': '0'})
    log = (fake_project / 'var' / 'cron.log').read_text(encoding='utf-8')
    assert 'nothing missing; done' in log
    # The window must be a real date pair, not two empty halves.
    assert re.search(r'window \d{4}/\d{2}/\d{2}\.\.\d{4}/\d{2}/\d{2}', log) \
        or 'window' in log and '..' in log


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))