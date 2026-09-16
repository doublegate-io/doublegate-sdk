"""Published producer contract and manifest compatibility regression tests."""
import copy
import json
from pathlib import Path

import pytest
from doublegate_sdk.manifest import ManifestError, validate_manifest
from doublegate_sdk.schema import DIALECT, check_response

FIXTURE = Path(__file__).parent / 'fixtures/published-note'
NOTE_SCHEMA = json.loads((FIXTURE / 'note.schema.json').read_text())
NOTES = [json.loads(line)['data'] for line in (FIXTURE / 'notes.jsonl').read_text().splitlines()]
MANIFEST = json.loads((Path(__file__).parents[1] / 'examples/gates/runbook/gate.json').read_text())

@pytest.mark.parametrize('note', NOTES)
def test_serialized_producer_note(note):
    assert check_response(NOTE_SCHEMA, note) == ()

@pytest.mark.parametrize('payload', [None, True, False, 1, 1.5, 'text', []])
def test_nonobject_payloads_fail_without_crashing(payload):
    assert check_response(NOTE_SCHEMA, payload) == ('/: type',)

@pytest.mark.parametrize(('field', 'value'), [
    ('artifact_id', None), ('event', 'invented'), ('outcome', 'invented'),
    ('provisional', 1), ('provisional', None), ('findings_count', True),
    ('findings_count', -1), ('findings_count', '1'), ('k', True), ('k', []),
    ('summary', 'x' * 121), ('relation', 'invented'),
])
def test_invalid_note_field(field, value):
    assert check_response(NOTE_SCHEMA, NOTES[0] | {field: value})

@pytest.mark.parametrize('value', [None, 0, 2, 2.0])
def test_nullable_integer_schema_accepts_json_integer(value):
    assert check_response(NOTE_SCHEMA, NOTES[0] | {'k': value}) == ()

@pytest.mark.parametrize('schema', [None, [], {}, {'$schema': DIALECT, 'type': 'bogus'}, {'$schema': DIALECT, 'required': 'x'}])
def test_malformed_schema_raises_value_error(schema):
    with pytest.raises(ValueError):
        check_response(schema, {})

def test_nested_array_and_object_errors():
    schema = {'$schema': DIALECT, 'type': 'object', 'additionalProperties': False,
              'required': ['rows'], 'properties': {'rows': {'type': 'array', 'items': {
                  'type': 'object', 'additionalProperties': False, 'required': ['n'],
                  'properties': {'n': {'type': 'integer'}}}}}}
    assert check_response(schema, {'rows': [{'n': True}, {'n': 2}, {}]}) == (
        '/rows/0/n: type', '/rows/2: required')

def test_closed_shape_and_required_fields():
    payload = copy.deepcopy(NOTES[0])
    del payload['artifact_id']
    payload['body'] = 'must not echo'
    errors = check_response(NOTE_SCHEMA, payload)
    assert errors == ('/: additionalProperties', '/: required')

def test_unavailable_reference_is_value_error():
    with pytest.raises(ValueError):
        check_response({'$schema': DIALECT, '$ref': 'https://example.invalid/schema.json'}, {})

# Existing behavior, not a desired new feature: these must remain unchanged.
def test_manifest_integral_float_remains_rejected():
    with pytest.raises(ManifestError) as error:
        validate_manifest(MANIFEST | {'max_input_bytes': 4096.0})
    assert error.value.reason == 'wrong_type'

def test_manifest_missing_field_keeps_path_and_reason():
    payload = copy.deepcopy(MANIFEST)
    del payload['name']
    with pytest.raises(ManifestError) as error:
        validate_manifest(payload)
    assert (error.value.path, error.value.reason) == ('/name', 'required_field')

def test_manifest_type_failure_keeps_reason():
    with pytest.raises(ManifestError) as error:
        validate_manifest(MANIFEST | {'max_input_bytes': True})
    assert (error.value.path, error.value.reason) == ('/max_input_bytes', 'wrong_type')
