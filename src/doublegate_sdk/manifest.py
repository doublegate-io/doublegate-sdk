# Modified for standalone doublegate_sdk namespace; see NOTICE.
"""SDK 0.1 declarative manifest. JSON is the supported YAML subset.

No imports, entrypoints, installation, grants or deployment occur during validation.
Manifests and response checks share a bounded stdlib schema engine.
Manifest integer strictness and bounded diagnostics remain SDK policy.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from doublegate_sdk._schema import iter_errors, _pointer


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


from doublegate_sdk.errors import DoublegateError


class ManifestError(DoublegateError, ValueError):
    """Bounded diagnostic: never echo manifest values or unknown property names."""
    code = 'invalid_manifest'
    kind = 'invalid_manifest'
    retryable = False

    def __init__(self, path: str, reason: str):
        self.path = path
        self.reason = reason
        super().__init__(f'{self.code} at {path or "/"}: {reason}')


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
    error = next(iter_errors(manifest_schema(), value, strict_integer=True,
                             string_limit=256, python_unique=True), None)
    if error is not None:
        reasons = {'type': 'wrong_type', 'const': 'unsupported_value',
                   'enum': 'unsupported_value', 'additionalProperties': 'unknown_field',
                   'required': 'required_field', 'minItems': 'array_bounds',
                   'maxItems': 'array_bounds', 'uniqueItems': 'duplicate_item',
                   'minLength': 'string_bounds', 'maxLength': 'string_bounds',
                   'pattern': 'invalid_format', 'minimum': 'integer_bounds',
                   'maximum': 'integer_bounds'}
        path = _pointer(error.path, error.missing) if error.missing is not None else error.path
        raise ManifestError(path, reasons[error.keyword])
    return GatePackage(value['name'], value['version'], tuple(value['artifact_types']),
                       tuple(check['text'] for check in value['checks']),
                       value['max_input_bytes'], value['human_review'])
