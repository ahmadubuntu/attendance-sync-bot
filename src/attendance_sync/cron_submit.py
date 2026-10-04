"""Submit planned Kasra payloads, one browser session per payload.

The shell wrapper (scripts/cron-catchup.sh) drives this module instead of embedding the
loop in a heredoc, so the approval-code handling and the per-run failure accounting can be
tested without a browser or a network.

Rules that live here:

* Every payload is planned and approved inside one run. The approval code is content-bound
  to the payloads this run produced, so a code from an older run can never authorise a newer
  plan.
* One browser session per payload. A single session that saves several documents in a row
  drags a modal overlay across saves and the read-back of the later documents fails.
* A payload that fails is recorded and the loop continues. The run reports how many
  succeeded and how many failed; it never claims a partial run was a full one.
"""

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Optional

# Kasra saves take about a minute per document; the CLI floor is lower.
SUBMIT_TIMEOUT_MS = 300000

# The CLI lives in ./src and is not installed into the venv, so every child interpreter is
# given the path explicitly. AS_SRC is set by the caller (the shell wrapper); without it the
# package is located from this file instead.
#
# The parent directory, not this one. This file is src/attendance_sync/cron_submit.py, so
# its own directory is the package itself -- putting that on sys.path lets `import
# attendance_sync` find nothing, because the package is one level up. The cron job ran with
# AS_SRC unset and every payload failed with ModuleNotFoundError, which is how a whole month
# of attendance went unregistered without a single visible error.
DEFAULT_SRC = str(Path(__file__).resolve().parent.parent)


def _child_command():
    """Return a bootstrap snippet that puts src on sys.path and calls the CLI."""
    src = os.environ.get('AS_SRC') or DEFAULT_SRC
    return (
        'import sys, os; '
        f'sys.path.insert(0, {src!r}); '
        'sys.argv = ["attendance_sync"] + sys.argv[1:]; '
        'from attendance_sync.cli import main; main()'
    )


def _assert_src_exists():
    """Fail loudly when the path the children are given does not hold the package.

    A wrong src directory is a silent failure: the child exits non-zero with
    ModuleNotFoundError and nothing else, and a caller reading that as a browser or network
    problem retries forever without ever seeing the cause. Checked once per run, where the
    error can be named, instead of inside every child.
    """
    src = os.environ.get('AS_SRC') or DEFAULT_SRC
    if not (Path(src) / 'attendance_sync' / 'cli.py').is_file():
        raise RuntimeError(
            f'src directory for the child interpreter is wrong: no attendance_sync/cli.py '
            f'under {src!r}'
        )
    return src


def _run(args, timeout=None):
    """Run the CLI in a fresh interpreter so each payload gets its own browser session."""
    return subprocess.run(
        [sys.executable, '-c', _child_command()] + list(args),
        capture_output=True, text=True, timeout=timeout,
    )


def extract_approval_code(stdout):
    """Pull the approval code out of a dry-run summary.

    stdout carries the payload echo, the summary and a hint line; only the summary is a JSON
    object carrying an approval_code key. A hint line or an empty stream yields None.
    """
    for line in (stdout or '').splitlines():
        line = line.strip()
        if not line.startswith('{') or 'approval_code' not in line:
            continue
        try:
            return json.loads(line).get('approval_code')
        except json.JSONDecodeError:
            continue
    return None


@dataclass
class SubmitOutcome:
    """What happened to one payload."""
    label: str
    code: Optional[str] = None
    detail: str = ''

    @property
    def ok(self):
        return bool(self.code)


@dataclass
class RunReport:
    """The per-run tally the shell wrapper turns into a notification."""
    ok: list = field(default_factory=list)
    failed: list = field(default_factory=list)

    @property
    def total(self):
        return len(self.ok) + len(self.failed)

    def summary(self):
        return 'RESULT ok=%d failed=%d' % (len(self.ok), len(self.failed))


def label_for(payload):
    """Human-readable day/time label used in the log and the notification."""
    return '%s %s-%s' % (payload['day'], payload['start_time'], payload['end_time'])


def submit_plan(plan, single_path, log=print):
    """Submit every payload in ``plan['payloads']``.

    Each payload is written alone to ``single_path`` so the CLI submits exactly one document
    per run. Returns a RunReport; failures are collected, never raised, so one bad payload
    does not abandon the rest.
    """
    # Checked before the first payload, not per payload: a wrong src path would fail every
    # one of them the same way, and one clear message is worth more than a list of identical
    # tracebacks.
    _assert_src_exists()
    report = RunReport()
    for payload in plan.get('payloads') or []:
        label = label_for(payload)
        with open(single_path, 'w', encoding='utf-8') as handle:
            json.dump({'payloads': [payload]}, handle, ensure_ascii=False)

        dry = _run(['kasra-submit', '--plan', single_path])
        code = extract_approval_code(dry.stdout)
        if not code:
            detail = (dry.stdout or dry.stderr).strip()
            log('DRY RUN FAILED %s %s' % (label, detail))
            report.failed.append(SubmitOutcome(label, None, detail))
            continue

        done = _run(['kasra-submit', '--plan', single_path, '--confirm', '--approve', code,
                     '--timeout-ms', str(SUBMIT_TIMEOUT_MS)])
        if done.returncode != 0:
            detail = (done.stdout or done.stderr).strip()
            log('SUBMIT FAILED %s %s' % (label, detail))
            report.failed.append(SubmitOutcome(label, code, detail))
            continue

        report.ok.append(SubmitOutcome(label, code))
        log('SUBMITTED %s code %s' % (label, code))

    log(report.summary())
    return report