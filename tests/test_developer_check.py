"""Developer verification command failure controls, not product runtime evidence."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def checker():
    spec = importlib.util.spec_from_file_location('sdk_check', ROOT/'scripts/check.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('xml', [
    '<testsuites><testsuite tests="0"/></testsuites>',
    '<testsuites><testsuite><testcase><skipped/></testcase></testsuite></testsuites>',
    '<testsuites><testsuite><testcase><failure/></testcase></testsuite></testsuites>',
])
def test_empty_or_incomplete_suite_is_not_acceptance(tmp_path, xml):
    path = tmp_path/'report.xml'; path.write_text(xml)
    with pytest.raises(RuntimeError): checker().coverage(path)


def test_actual_case_elements_define_counts(tmp_path):
    path = tmp_path/'report.xml'
    path.write_text('<testsuites><testsuite tests="999"><testcase name="real"/></testsuite></testsuites>')
    assert checker().coverage(path) == {'collected': 1, 'passed': 1, 'failed': 0, 'errors': 0, 'skipped': 0}


def test_source_guard_rejects_import_from_other_checkout(tmp_path, monkeypatch):
    module = checker()
    monkeypatch.setitem(sys.modules, 'doublegate_sdk.fake', SimpleNamespace(__file__=str(tmp_path/'fake.py')))
    with pytest.raises(RuntimeError, match='SDK source mismatch'):
        module.check_origins()


def test_preflight_runs_from_another_directory(tmp_path):
    import subprocess, json
    result = subprocess.run([sys.executable, str(ROOT/'scripts/check.py'), '--preflight'],
                            cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['source'] == str(ROOT/'src/doublegate_sdk')


def test_snapshot_detects_changes_to_executed_examples_and_package_docs(tmp_path, monkeypatch):
    module = checker()
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, 'SOURCE', tmp_path/'src/doublegate_sdk')
    names = ['examples/starters/app.py', 'llms.txt', 'MANIFEST.in']
    for name in names:
        path = tmp_path/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('initial fixture')
    before = module.snapshot()
    assert set(names).issubset(before)
    for name in names:
        (tmp_path/name).write_text('changed fixture')
        after = module.snapshot()
        assert after[name] != before[name]


def prepare_run(tmp_path, monkeypatch):
    module = checker()
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, 'preflight', lambda: {'source': 'fixture'})
    monkeypatch.setattr(sys, 'argv', ['check.py'])
    return module


def saved_result(tmp_path):
    import json
    paths = list((tmp_path/'.tmp/verification').glob('run-*/result.json'))
    assert len(paths) == 1
    return json.loads(paths[0].read_text()), paths[0].parent


def test_snapshot_failure_is_recorded_not_left_running(tmp_path, monkeypatch):
    module = prepare_run(tmp_path, monkeypatch)
    def broken(): raise OSError('snapshot unavailable')
    monkeypatch.setattr(module, 'snapshot', broken)
    assert module.main() == 1
    result, _ = saved_result(tmp_path)
    assert result['status'] == 'failed'
    assert result['failure_phase'] == 'snapshot'
    assert result['source_unchanged'] is None


def test_failed_test_run_preserves_coverage(tmp_path, monkeypatch):
    import subprocess
    module = prepare_run(tmp_path, monkeypatch)
    monkeypatch.setattr(module, 'snapshot', lambda: {})
    calls = []
    def failed(command, **kwargs):
        calls.append(command)
        Path(command[-1]).write_text('<testsuites><testsuite><testcase><failure/></testcase></testsuite></testsuites>')
        return subprocess.CompletedProcess(command, 1, 'test failed', '')
    monkeypatch.setattr(module.subprocess, 'run', failed)
    assert module.main() == 1
    result, _ = saved_result(tmp_path)
    assert result['coverage']['failed'] == 1
    assert result['failure_phase'] == 'tests'
    assert len(calls) == 1  # Do not build docs after test failure.


def test_timeout_preserves_partial_output(tmp_path, monkeypatch):
    import subprocess
    module = prepare_run(tmp_path, monkeypatch)
    monkeypatch.setattr(module, 'snapshot', lambda: {})
    def timed_out(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 300, output=b'checkpoint-before-timeout', stderr=b'error detail')
    monkeypatch.setattr(module.subprocess, 'run', timed_out)
    assert module.main() == 1
    result, output = saved_result(tmp_path)
    assert result['failure_phase'] == 'tests'
    assert result['steps'][0]['exit_code'] == 124
    assert 'checkpoint-before-timeout' in (output/'tests.log').read_text()
    assert 'error detail' in (output/'tests.log').read_text()
