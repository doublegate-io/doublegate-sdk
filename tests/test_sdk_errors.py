import pytest


def test_sdk_failures_share_a_base_without_losing_specific_types():
    from doublegate_sdk.errors import DoublegateError
    from doublegate_sdk.manifest import ManifestError
    from doublegate_sdk.package import PackageError
    from doublegate_sdk.runtime import EvaluationError
    from doublegate_sdk.client import GateError
    errors = [ManifestError('/', 'invalid type'), PackageError('unsafe_path'),
              EvaluationError('invalid_utf8'), GateError('timeout', outcome_unknown=True)]
    for error in errors:
        assert isinstance(error, DoublegateError)
        assert isinstance(error.kind, str) and error.kind
    assert all(isinstance(e, ValueError) for e in errors[:3])
    assert isinstance(errors[3], RuntimeError)
    assert errors[3].outcome_unknown
    assert str(errors[1]) == 'unsafe_path'
    assert str(errors[2]) == 'invalid_utf8'
    assert errors[0].code == 'invalid_manifest'


def test_sdk_catch_does_not_swallow_application_value_errors():
    from doublegate_sdk.errors import DoublegateError
    assert not isinstance(ValueError('application bug'), DoublegateError)
