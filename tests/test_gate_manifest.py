# Modified for standalone doublegate_sdk namespace; see NOTICE.
import copy
import importlib.util

import pytest


def manifest():
    return {
        'schema_version': '0.1', 'sdk_version': '0.1', 'name': 'runbook',
        'version': '1.0.0', 'artifact_types': ['memory'],
        'checks': [{'capability': 'required-text', 'text': 'Owner:'}],
        'max_input_bytes': 4096, 'human_review': 'inherit',
        'egress': [], 'secret_refs': [],
    }


def test_declarative_package_is_validated_and_immutable():
    assert importlib.util.find_spec('doublegate_sdk.manifest') is not None
    from doublegate_sdk.manifest import validate_manifest
    raw = manifest()
    package = validate_manifest(raw)
    raw['checks'][0]['text'] = 'mutated'
    assert package.required_text == ('Owner:',)
    assert package.human_review == 'inherit'


@pytest.mark.parametrize(('field', 'value'), [
    ('sdk_version', '99'), ('schema_version', True), ('max_input_bytes', True),
    ('max_input_bytes', 0), ('max_input_bytes', 1048577),
    ('checks', []), ('checks', [{'capability': 'python', 'text': 'os.system'}]),
    ('checks', [{'capability': 'required-text', 'text': '', 'severity': 'info'}]),
    ('human_review', 'disabled'), ('egress', ['https://example.com']),
    ('secret_refs', ['authority-signing-key']), ('artifact_types', ['unknown']),
    ('name', 'invalid name'), ('version', 'latest'),
])
def test_invalid_or_privileged_manifest_is_rejected(field, value):
    from doublegate_sdk.manifest import ManifestError, validate_manifest
    raw = copy.deepcopy(manifest())
    raw[field] = value
    with pytest.raises(ManifestError) as error:
        validate_manifest(raw)
    assert error.value.code == 'invalid_manifest'
    assert error.value.path.startswith('/' + field)
    assert not error.value.retryable


def test_manifest_rejects_integral_float_without_coercion():
    from doublegate_sdk.manifest import ManifestError, validate_manifest
    with pytest.raises(ManifestError) as error:
        validate_manifest(manifest() | {'max_input_bytes': 4096.0})
    assert (error.value.path, error.value.reason) == ('/max_input_bytes', 'wrong_type')


@pytest.mark.parametrize('items', [[False, 0], [True, 1], [1, 1.0]])
def test_manifest_duplicate_diagnostic_keeps_baseline_precedence(items):
    from doublegate_sdk.manifest import ManifestError, validate_manifest
    with pytest.raises(ManifestError) as error:
        validate_manifest(manifest() | {'artifact_types': items})
    assert (error.value.path, error.value.reason) == ('/artifact_types', 'duplicate_item')


def test_unknown_authority_fields_fail_without_echoing_values():
    from doublegate_sdk.manifest import ManifestError, validate_manifest
    raw = manifest() | {'override_human_pin': 'SECRET-CANARY'}
    with pytest.raises(ManifestError) as error:
        validate_manifest(raw)
    assert 'SECRET-CANARY' not in str(error.value)
