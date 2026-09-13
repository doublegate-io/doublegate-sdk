"""Bounded stdlib schema engine; deliberately not a full JSON Schema dialect."""
from __future__ import annotations

import math
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

DIALECT = 'https://json-schema.org/draft/2020-12/schema'
_TYPES = {'object': dict, 'array': list, 'string': str, 'integer': int,
          'boolean': bool, 'null': type(None)}
_ANNOTATIONS = {'$schema', '$id', '$comment', 'title', 'description', 'default',
                'examples', 'deprecated', 'readOnly', 'writeOnly'}
_ASSERTIONS = {'type', 'const', 'enum', 'properties', 'required', 'additionalProperties',
               'items', 'minItems', 'maxItems', 'uniqueItems', 'minLength', 'maxLength',
               'pattern', 'minimum', 'maximum'}


def _equal(left: Any, right: Any) -> bool:
    """JSON equality: booleans are not numbers, including inside containers."""
    if type(left) in (int, float) and type(right) in (int, float):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, list):
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_equal(left[k], right[k]) for k in left)
    return left == right


def _json(value: Any) -> bool:
    if type(value) in (str, int, bool, type(None)):
        return True
    if type(value) is float:
        return math.isfinite(value)
    if type(value) is list:
        return all(_json(v) for v in value)
    if type(value) is dict:
        return all(type(k) is str and _json(v) for k, v in value.items())
    return False


def validate_schema(schema: Any, *, root: bool = True) -> None:
    """Reject malformed/unsupported schemas before looking at any payload."""
    if type(schema) is not dict or (root and schema.get('$schema') != DIALECT):
        raise ValueError('schema must be an object declaring the supported dialect URI')
    if schema.keys() - _ANNOTATIONS - _ASSERTIONS:
        raise ValueError('unsupported schema keyword')
    if '$schema' in schema and schema['$schema'] != DIALECT:
        raise ValueError('unsupported schema dialect')
    for key in ('$id', '$comment', 'title', 'description'):
        if key in schema and type(schema[key]) is not str:
            raise ValueError('invalid schema annotation')
    for key in ('deprecated', 'readOnly', 'writeOnly'):
        if key in schema and type(schema[key]) is not bool:
            raise ValueError('invalid schema annotation')
    if 'examples' in schema and type(schema['examples']) is not list:
        raise ValueError('invalid schema examples')
    if not _json(schema):
        raise ValueError('schema must contain finite JSON values')
    if 'type' in schema:
        types = schema['type'] if type(schema['type']) is list else [schema['type']]
        if (not types or any(type(t) is not str or t not in _TYPES for t in types)
                or len(set(types)) != len(types)):
            raise ValueError('invalid or unsupported schema type')
    if 'enum' in schema:
        values = schema['enum']
        if (type(values) is not list or not values
                or any(any(_equal(v, previous) for previous in values[:i])
                       for i, v in enumerate(values))):
            raise ValueError('invalid schema enum')
    if 'required' in schema:
        names = schema['required']
        if (type(names) is not list or any(type(k) is not str for k in names)
                or len(set(names)) != len(names)):
            raise ValueError('invalid schema required')
    for key in ('additionalProperties', 'uniqueItems'):
        if key in schema and type(schema[key]) is not bool:
            raise ValueError('only boolean ' + key + ' is supported')
    for key in ('minItems', 'maxItems', 'minLength', 'maxLength'):
        if key in schema and (type(schema[key]) is not int or schema[key] < 0):
            raise ValueError('invalid schema size bound')
    for key in ('minimum', 'maximum'):
        if key in schema and type(schema[key]) not in (int, float):
            raise ValueError('invalid schema numeric bound')
    if 'pattern' in schema:
        if type(schema['pattern']) is not str:
            raise ValueError('invalid schema pattern')
        try:
            re.compile(schema['pattern'])
        except re.error:
            raise ValueError('invalid schema pattern') from None
    if 'properties' in schema:
        if type(schema['properties']) is not dict:
            raise ValueError('invalid schema properties')
        for child in schema['properties'].values():
            validate_schema(child, root=False)
    if 'items' in schema:
        validate_schema(schema['items'], root=False)


def _pointer(path: str, key: str | int) -> str:
    return path + '/' + str(key).replace('~', '~0').replace('/', '~1')


@dataclass(frozen=True)
class Violation:
    path: str
    keyword: str
    missing: str | None = None


def iter_errors(schema: dict[str, Any], value: Any, *, strict_integer: bool = False,
                string_limit: int | None = None, python_unique: bool = False,
                path: str = '') -> Iterator[Violation]:
    """Traverse once; callers select policy and format the same violations."""
    types = schema.get('type', [])
    types = [types] if isinstance(types, str) else types
    if types and not any(type(value) is _TYPES[t] or (
            t == 'integer' and not strict_integer and type(value) is float
            and math.isfinite(value) and value.is_integer()) for t in types):
        yield Violation(path, 'type')
        return
    for key in ('const', 'enum'):
        if key in schema:
            values = [schema[key]] if key == 'const' else schema[key]
            if not any(_equal(value, v) for v in values):
                yield Violation(path, key)
    if type(value) is dict:
        props = schema.get('properties', {})
        if schema.get('additionalProperties') is False and value.keys() - props.keys():
            yield Violation(path, 'additionalProperties')
        for key in schema.get('required', []):
            if key not in value:
                yield Violation(path, 'required', key)
        for key, item in value.items():
            if key in props:
                yield from iter_errors(props[key], item, strict_integer=strict_integer,
                                       string_limit=string_limit, python_unique=python_unique,
                                       path=_pointer(path, key))
    elif type(value) is list:
        for key, violated in (('minItems', len(value) < schema.get('minItems', 0)),
                              ('maxItems', len(value) > schema.get('maxItems', math.inf))):
            if violated:
                yield Violation(path, key)
        if schema.get('uniqueItems') and any(
                any((v == previous if python_unique else _equal(v, previous))
                    for previous in value[:i]) for i, v in enumerate(value)):
            yield Violation(path, 'uniqueItems')
        if 'items' in schema:
            for i, item in enumerate(value):
                yield from iter_errors(schema['items'], item, strict_integer=strict_integer,
                                       string_limit=string_limit, python_unique=python_unique,
                                       path=_pointer(path, i))
    elif type(value) is str:
        maximum = schema.get('maxLength', string_limit if string_limit is not None else math.inf)
        for key, violated in (('minLength', len(value) < schema.get('minLength', 0)),
                              ('maxLength', len(value) > maximum)):
            if violated:
                yield Violation(path, key)
        if 'pattern' in schema and not re.search(schema['pattern'], value):
            yield Violation(path, 'pattern')
    elif type(value) in (int, float):
        for key, violated in (('minimum', value < schema.get('minimum', -math.inf)),
                              ('maximum', value > schema.get('maximum', math.inf))):
            if violated:
                yield Violation(path, key)
