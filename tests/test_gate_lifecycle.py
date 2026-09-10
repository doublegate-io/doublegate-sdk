# Modified for standalone doublegate_sdk namespace; see NOTICE.
"""Offline SDK lifecycle: evidence only, with no artifact text in output."""
import json
import os

import pytest

from doublegate_sdk import manifest as manifests


def raw_manifest():
    return dict(schema_version='0.1', sdk_version='0.1', name='runbook',
                version='1.0.0', artifact_types=['memory'],
                checks=[dict(capability='required-text', text='Owner:')],
                max_input_bytes=64, human_review='required', egress=[], secret_refs=[])


def test_required_text_is_literal_case_sensitive_evidence():
    from doublegate_sdk import runtime
    package = manifests.validate_manifest(raw_manifest())
    assert runtime.evaluate(package, 'Owner: Alice', 'memory').outcome == 'clean'
    result = runtime.evaluate(package, 'owner: SECRET-CANARY', 'memory')
    assert result.flagged
    assert result.findings[0].kind == 'required-text'
    assert result.findings[0].detail == 'required_text_missing: check 0'
    assert result.masked == ''
    assert 'SECRET-CANARY' not in json.dumps(result.to_payload())
    assert package.human_review == 'required'


@pytest.mark.parametrize(('body', 'kind', 'reason'), [
    ('é' * 33, 'memory', 'input_too_large'),
    ('Owner:', 'script', 'unsupported_artifact_type'),
    ('\ud800', 'memory', 'invalid_utf8'),
])
def test_evaluation_refuses_invalid_input(body, kind, reason):
    from doublegate_sdk import runtime
    with pytest.raises(runtime.EvaluationError, match=reason):
        runtime.evaluate(manifests.validate_manifest(raw_manifest()), body, kind)


def test_loading_digest_is_canonical_and_covers_policy(tmp_path):
    from doublegate_sdk.package import load_package
    path = tmp_path / 'gate.json'
    raw = raw_manifest()
    path.write_text(json.dumps(raw))
    first = load_package(path)
    path.write_text(json.dumps(raw, indent=2, sort_keys=True))
    assert load_package(path).digest == first.digest
    assert len(first.digest) == 64
    raw['human_review'] = 'inherit'
    path.write_text(json.dumps(raw))
    assert load_package(path).digest != first.digest
    assert first.package.human_review == 'required'


@pytest.mark.parametrize('case', ['symlink', 'parent_symlink', 'fifo', 'directory',
                                 'oversize', 'duplicate', 'invalid_json', 'utf8', 'deep'])
def test_loader_rejects_unsafe_files_without_echo(tmp_path, case):
    from doublegate_sdk.package import PackageError, load_package
    path = tmp_path / 'gate.json'
    if case == 'symlink':
        path.symlink_to(tmp_path / 'SECRET-CANARY')
    elif case == 'parent_symlink':
        real = tmp_path / 'real'
        real.mkdir()
        (real / 'gate.json').write_text(json.dumps(raw_manifest()))
        link = tmp_path / 'link'
        link.symlink_to(real, target_is_directory=True)
        path = link / 'gate.json'
    elif case == 'fifo':
        os.mkfifo(path)
    elif case == 'directory':
        path.mkdir()
    else:
        values = {'oversize': b'x' * 65537, 'duplicate': b'{"x":1,"x":2}',
                  'invalid_json': b'SECRET-CANARY', 'utf8': b'\xff',
                  'deep': b'[' * 2000 + b']' * 2000}
        path.write_bytes(values[case])
    with pytest.raises((PackageError, manifests.ManifestError)) as error:
        load_package(path)
    assert 'SECRET-CANARY' not in str(error.value)
