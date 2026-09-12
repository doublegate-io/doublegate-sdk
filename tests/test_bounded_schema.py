"""Boundaries of the supported subset, not a full-dialect conformance suite."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from doublegate_sdk.schema import DIALECT, check_response


def schema(**keywords):
    return {'$schema': DIALECT, **keywords}


@pytest.mark.parametrize('keyword,value', [
    ('$ref', '#'), ('$defs', {}), ('allOf', []), ('anyOf', []), ('oneOf', []),
    ('not', {}), ('if', {}), ('then', {}), ('else', {}), ('format', 'email'),
    ('patternProperties', {}), ('unevaluatedProperties', False),
    ('dependentRequired', {}), ('prefixItems', []), ('contains', {}),
    ('multipleOf', 2), ('exclusiveMinimum', 1), ('typo', True),
])
def test_unsupported_keywords_fail_even_in_absent_properties(keyword, value):
    with pytest.raises(ValueError):
        check_response(schema(properties={'unused': {keyword: value}}), {})


@pytest.mark.parametrize('keywords', [
    {'type': []}, {'type': ['integer', 'integer']}, {'type': [None]},
    {'type': 'number'}, {'type': {}}, {'properties': []}, {'properties': {'x': True}},
    {'items': []}, {'additionalProperties': {}}, {'uniqueItems': 1},
    {'required': ['x', 'x']}, {'required': [1]}, {'enum': []}, {'enum': [1, 1.0]},
    {'minLength': True}, {'maxLength': -1}, {'minItems': 0.0}, {'maxItems': None},
    {'minimum': True}, {'maximum': float('inf')}, {'pattern': '['}, {'pattern': 1},
    {'title': 1}, {'examples': {}}, {'readOnly': 'yes'}, {'default': object()},
])
def test_malformed_schema_is_rejected(keywords):
    with pytest.raises(ValueError):
        check_response(schema(**keywords), {})


@pytest.mark.parametrize('keywords,value,expected', [
    ({'type': 'string'}, 'x' * 1000, ()),
    ({'type': 'integer'}, 2.0, ()),
    ({'type': 'integer'}, True, ('/: type',)),
    ({'type': 'integer'}, 2.1, ('/: type',)),
    ({'type': ['integer', 'null']}, None, ()),
    ({'type': 'boolean'}, False, ()),
    ({'type': 'null'}, False, ('/: type',)),
    ({'minimum': 2}, 1, ('/: minimum',)),
    ({'maximum': 2}, 3, ('/: maximum',)),
    ({'type': 'array', 'minItems': 1}, [], ('/: minItems',)),
    ({'type': 'array', 'maxItems': 1}, [1, 2], ('/: maxItems',)),
    ({'type': 'string', 'minLength': 1}, '', ('/: minLength',)),
    ({'type': 'string', 'maxLength': 1}, 'ab', ('/: maxLength',)),
    ({'pattern': '^a$'}, 'b', ('/: pattern',)),
    ({'enum': [False]}, 0, ('/: enum',)),
    ({'const': {'a': [False]}}, {'a': [0]}, ('/: const',)),
    ({'enum': [0]}, 0.0, ()),
    ({'uniqueItems': True}, [False, 0], ()),
    ({'uniqueItems': True}, [{'n': 1}, {'n': 1.0}], ('/: uniqueItems',)),
    ({'properties': {'x': {'enum': ['ok']}}}, {}, ()),
    ({'properties': {'x': {'enum': ['ok']}}}, {'x': 'bad'}, ('/x: enum',)),
    ({'type': 'string', 'enum': ['ok']}, 0, ('/: type',)),
])
def test_supported_assertions(keywords, value, expected):
    assert check_response(schema(**keywords), value) == expected


def test_errors_are_sorted_escaped_and_do_not_echo_values():
    contract = schema(type='object', additionalProperties=False, required=['missing'],
                      properties={'z': {'enum': ['ok']}, 'a~/b': {'type': 'integer'}})
    payload = {'SECRET-CANARY': 1, 'z': 'SECRET-CANARY', 'a~/b': False}
    before = copy.deepcopy((contract, payload))
    assert check_response(contract, payload) == (
        '/: additionalProperties', '/: required', '/a~0~1b: type', '/z: enum')
    assert (contract, payload) == before


def test_known_annotations_are_allowed_without_asserting_defaults():
    assert check_response(schema(title='Title', description='Description', default=1,
                                 examples=[1], deprecated=True, readOnly=True, writeOnly=False,
                                 **{'$id': 'https://example.invalid/schema', '$comment': 'note'}), None) == ()


def test_real_fixture_provenance_hash_and_events():
    fixture = Path(__file__).parent / 'fixtures/published-note'
    notes = (fixture / 'notes.jsonl').read_bytes()
    provenance = json.loads((fixture / 'provenance.json').read_text())
    assert hashlib.sha256(notes).hexdigest() == provenance['notes_sha256']
    rows = [json.loads(line) for line in notes.splitlines()]
    assert len(rows) == provenance['notes_count'] == 3
    assert {row['data']['event'] for row in rows} == {'promote', 'relation', 'scan'}
