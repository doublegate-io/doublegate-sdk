# Modified for standalone doublegate_sdk namespace; see NOTICE.
"""SDK 0.1 declarative manifest. JSON is the supported YAML subset.

No imports, entrypoints, installation, grants or deployment occur during validation.
The small validator below handles only the keywords emitted by manifest_schema();
it is not a general JSON Schema implementation. Core remains stdlib-only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


def manifest_schema() -> dict[str, Any]:
    """Return a fresh, externally usable Draft 2020-12 schema."""
    properties = {
        'schema_version': {'type': 'string', 'const': '0.1'},
        'sdk_version': {'type': 'string', 'const': '0.1'},
        'name': {'type': 'string', 'maxLength': 64, 'pattern': '^[a-z][a-z0-9-]*$'},
        'version': {'type': 'string', 'maxLength': 32, 'pattern': '^[0-9]+\\.[0-9]+\\.[0-9]+$'},
        'artifact_types': {'type': 'array', 'minItems': 1, 'maxItems': 4, 'uniqueItems': True,
                           'items': {'type': 'string', 'enum': ['memory', 'skill', 'script', 'tool']}},
        'checks': {'type': 'array', 'minItems': 1, 'maxItems': 32, 'items': {
            'type': 'object', 'additionalProperties': False, 'required': ['capability', 'text'],
            'properties': {'capability': {'type': 'string', 'const': 'required-text'},
                           'text': {'type': 'string', 'minLength': 1, 'maxLength': 256}}}},
        'max_input_bytes': {'type': 'integer', 'minimum': 1, 'maximum': 1048576},
        'human_review': {'type': 'string', 'enum': ['inherit', 'required']},
        'egress': {'type': 'array', 'maxItems': 0},
        'secret_refs': {'type': 'array', 'maxItems': 0},
    }
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema',
            'title': 'Doublegate declarative gate manifest 0.1', 'type': 'object',
            'additionalProperties': False, 'required': list(properties), 'properties': properties}


class ManifestError(ValueError):
    """Bounded diagnostic: never echo manifest values or unknown property names."""
    code = 'invalid_manifest'
    retryable = False

    def __init__(self, path: str, reason: str):
        self.path = path
        self.reason = reason
        super().__init__(f'{self.code} at {path or "/"}: {reason}')


def _validate(value: Any, schema: dict[str, Any], path: str = '') -> None:
    types = {'object': dict, 'array': list, 'string': str, 'integer': int}
    if type(value) is not types[schema['type']]:
        raise ManifestError(path, 'wrong_type')
    if ('const' in schema and value != schema['const']) or ('enum' in schema and value not in schema['enum']):
        raise ManifestError(path, 'unsupported_value')
    if isinstance(value, dict):
        props = schema['properties']
        if value.keys() - props.keys():
            raise ManifestError(path, 'unknown_field')
        for key in schema['required']:
            if key not in value:
                raise ManifestError(path + '/' + key, 'required_field')
        for key, item in value.items():
            _validate(item, props[key], path + '/' + key)
    elif isinstance(value, list):
        if not schema.get('minItems', 0) <= len(value) <= schema['maxItems']:
            raise ManifestError(path, 'array_bounds')
        if schema.get('uniqueItems') and any(v in value[:i] for i, v in enumerate(value)):
            raise ManifestError(path, 'duplicate_item')
        for i, item in enumerate(value):
            _validate(item, schema['items'], path + '/' + str(i))
    elif isinstance(value, str):
        if not schema.get('minLength', 0) <= len(value) <= schema.get('maxLength', 256):
            raise ManifestError(path, 'string_bounds')
        if 'pattern' in schema and not re.search(schema['pattern'], value):
            raise ManifestError(path, 'invalid_format')
    elif not schema['minimum'] <= value <= schema['maximum']:
        raise ManifestError(path, 'integer_bounds')


@dataclass(frozen=True, slots=True)
class GatePackage:
    name: str
    version: str
    artifact_types: tuple[str, ...]
    required_text: tuple[str, ...]
    max_input_bytes: int
    human_review: str


def validate_manifest(value: Any) -> GatePackage:
    """Validate data before constructing an immutable declarative package."""
    _validate(value, manifest_schema())
    return GatePackage(value['name'], value['version'], tuple(value['artifact_types']),
                       tuple(check['text'] for check in value['checks']),
                       value['max_input_bytes'], value['human_review'])
