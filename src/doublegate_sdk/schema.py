"""Validate published response dictionaries using a bounded, stdlib-only subset.

This is not full Draft 2020-12 validation. References, composition, conditionals,
format and unknown keywords are rejected, never silently ignored. See the API
manual for supported keywords. Schemas are trusted application inputs, not a
sandbox for hostile patterns or deeply nested documents.
"""
from __future__ import annotations

from typing import Any

from doublegate_sdk._schema import DIALECT, iter_errors, validate_schema

__all__ = ['DIALECT', 'check_response']


def check_response(schema: dict[str, Any], payload: Any) -> tuple[str, ...]:
    """Return sorted instance-pointer/keyword violations, without payload values.

    Malformed or unsupported schemas raise ValueError, even in unused branches.
    Payloads must be decoded finite JSON values. Integral floats count as response
    integers; booleans do not. Objects are closed only with additionalProperties
    false. No schema or payload is modified, and no resources are fetched.
    """
    try:
        validate_schema(schema)
        return tuple(sorted(f'{error.path or "/"}: {error.keyword}'
                            for error in iter_errors(schema, payload)))
    except RecursionError:
        raise ValueError('schema or payload nesting exceeds supported depth') from None
