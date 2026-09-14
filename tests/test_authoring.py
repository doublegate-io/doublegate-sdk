"""Behaviour of the one-call offline operation: manifest + file -> evidence."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / 'examples/gates/runbook/gate.json'
PASSING = ROOT / 'examples/gates/runbook/pass.txt'


def test_evaluating_a_passing_file_reports_the_gate_it_applied():
    from doublegate_sdk.authoring import evaluate_file

    evaluation = evaluate_file(GATE, PASSING, artifact_type='memory')

    assert evaluation.outcome == 'clean'
    assert evaluation.flagged is False
    assert evaluation.findings == ()
    assert evaluation.gate_name == 'runbook'
    assert evaluation.gate_version == '1.0.0'
    assert evaluation.artifact_type == 'memory'
    assert evaluation.human_review == 'required'
    assert len(evaluation.manifest_digest) == 64


def test_a_missing_required_string_is_flagged_without_quoting_the_body(tmp_path):
    from doublegate_sdk.authoring import evaluate_file

    body = tmp_path / 'body.txt'
    body.write_text('SECRET-CANARY has no owner line\n')

    evaluation = evaluate_file(GATE, body, artifact_type='memory')

    assert evaluation.flagged is True
    assert evaluation.outcome == 'flagged'
    assert len(evaluation.findings) == 1
    assert evaluation.findings[0].kind == 'required-text'
    assert 'SECRET-CANARY' not in json.dumps(evaluation.to_payload())


def test_the_payload_carries_the_gate_identity_the_cli_prints(tmp_path):
    from doublegate_sdk.authoring import evaluate_file

    payload = evaluate_file(GATE, PASSING, artifact_type='memory').to_payload()

    assert payload == {'name': 'runbook', 'version': '1.0.0',
                       'digest': payload['digest'], 'human_review': 'required',
                       'artifact_type': 'memory', 'outcome': 'clean', 'findings': []}
    assert json.loads(json.dumps(payload)) == payload


def test_an_unreadable_body_fails_as_an_sdk_error_naming_no_path(tmp_path):
    from doublegate_sdk.authoring import evaluate_file
    from doublegate_sdk.errors import DoublegateError

    with pytest.raises(DoublegateError) as caught:
        evaluate_file(GATE, tmp_path / 'SECRET-CANARY.txt', artifact_type='memory')

    assert 'SECRET-CANARY' not in str(caught.value)


def test_a_non_utf8_body_is_refused_before_any_check_runs(tmp_path):
    from doublegate_sdk.authoring import evaluate_file
    from doublegate_sdk.runtime import EvaluationError

    body = tmp_path / 'body.bin'
    body.write_bytes(b'\xff')

    with pytest.raises(EvaluationError) as caught:
        evaluate_file(GATE, body, artifact_type='memory')

    assert str(caught.value) == 'invalid_utf8'


def test_an_artifact_type_the_gate_does_not_cover_is_refused(tmp_path):
    from doublegate_sdk.authoring import evaluate_file
    from doublegate_sdk.runtime import EvaluationError

    with pytest.raises(EvaluationError) as caught:
        evaluate_file(GATE, PASSING, artifact_type='script')

    assert str(caught.value) == 'unsupported_artifact_type'


def test_a_body_over_the_manifest_limit_is_refused_by_the_reader(tmp_path):
    from doublegate_sdk.authoring import evaluate_file
    from doublegate_sdk.package import load_package
    from doublegate_sdk.runtime import EvaluationError, evaluate

    body = tmp_path / 'big.txt'
    body.write_text('Owner: Operations\n' + 'x' * 5000)

    with pytest.raises(EvaluationError) as caught:
        evaluate_file(GATE, body, artifact_type='memory')

    with pytest.raises(EvaluationError) as in_memory:
        evaluate(load_package(GATE).package, body.read_text(), 'memory')
    assert caught.value.kind == in_memory.value.kind == 'input_too_large'


def test_an_invalid_manifest_is_refused_before_the_body_is_read(tmp_path):
    from doublegate_sdk.authoring import evaluate_file
    from doublegate_sdk.manifest import ManifestError

    manifest = tmp_path / 'gate.json'
    manifest.write_text(json.dumps({'schema_version': '0.1'}))

    with pytest.raises(ManifestError) as caught:
        evaluate_file(manifest, tmp_path / 'absent.txt', artifact_type='memory')

    assert caught.value.code == 'invalid_manifest'


def run_cli(*args):
    return subprocess.run(
        [sys.executable, '-m', 'doublegate_sdk', 'evaluate', *map(str, args)],
        capture_output=True, text=True, timeout=30)


def test_the_cli_prints_exactly_the_operation_payload_on_a_clean_run():
    from doublegate_sdk.authoring import evaluate_file

    result = run_cli(GATE, PASSING, '--artifact-type', 'memory')

    assert result.returncode == 0, result.stderr
    assert result.stderr == ''
    expected = evaluate_file(GATE, PASSING, artifact_type='memory').to_payload()
    assert json.loads(result.stdout) == expected


def test_the_cli_still_exits_one_on_flagged_and_two_on_a_handled_error(tmp_path):
    body = tmp_path / 'body.txt'
    body.write_text('SECRET-CANARY')

    flagged = run_cli(GATE, body, '--artifact-type', 'memory')
    assert flagged.returncode == 1
    assert json.loads(flagged.stdout)['outcome'] == 'flagged'
    assert 'SECRET-CANARY' not in flagged.stdout + flagged.stderr

    body.write_bytes(b'\xff')
    failed = run_cli(GATE, body, '--artifact-type', 'memory')
    assert failed.returncode == 2
    assert failed.stdout == ''
    assert json.loads(failed.stderr) == {'error': 'invalid_utf8'}


def test_the_operation_is_reachable_from_the_package_root():
    import doublegate_sdk
    from doublegate_sdk.authoring import evaluate_file

    assert doublegate_sdk.evaluate_file is evaluate_file
    assert 'evaluate_file' in doublegate_sdk.__all__


def test_importing_the_authoring_half_opens_no_socket():
    program = (
        'import socket, sys\n'
        'def refuse(*a, **k):\n'
        '    raise AssertionError("the offline SDK opened a socket")\n'
        'socket.socket.connect = refuse\n'
        'socket.create_connection = refuse\n'
        'import doublegate_sdk\n'
        'from doublegate_sdk.authoring import evaluate_file\n'
        'print(evaluate_file(sys.argv[1], sys.argv[2], artifact_type="memory").outcome)\n'
    )
    result = subprocess.run([sys.executable, '-c', program, str(GATE), str(PASSING)],
                            capture_output=True, text=True, timeout=30)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'clean'
