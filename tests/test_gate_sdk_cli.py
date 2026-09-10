# Modified for standalone doublegate_sdk namespace; see NOTICE.
import json
import subprocess
import sys
from pathlib import Path

from doublegate_sdk.manifest import manifest_schema

ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    return subprocess.run([sys.executable, '-m', 'doublegate_sdk', *map(str, args)],
                          capture_output=True, text=True, timeout=10)


def test_schema_export_and_authoring_example():
    result = run('schema')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == manifest_schema()
    example = ROOT / 'examples/gates/runbook/gate.json'
    result = run('validate', example)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['name'] == 'runbook'
    result = run('evaluate', example, ROOT / 'examples/gates/runbook/pass.txt',
                 '--artifact-type', 'memory')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['outcome'] == 'clean'
    assert json.loads(result.stdout)['human_review'] == 'required'


def test_cli_flagged_and_error_codes_do_not_echo_content(tmp_path):
    example = ROOT / 'examples/gates/runbook/gate.json'
    body = tmp_path / 'body.txt'
    body.write_text('SECRET-CANARY')
    result = run('evaluate', example, body, '--artifact-type', 'memory')
    assert result.returncode == 1
    assert json.loads(result.stdout)['outcome'] == 'flagged'
    assert 'SECRET-CANARY' not in result.stdout + result.stderr
    body.write_bytes(b'\xff')
    result = run('evaluate', example, body, '--artifact-type', 'memory')
    assert result.returncode == 2
    assert json.loads(result.stderr)['error'] == 'invalid_utf8'
    result = run('validate', tmp_path / 'SECRET-CANARY')
    assert result.returncode == 2
    assert 'SECRET-CANARY' not in result.stdout + result.stderr
