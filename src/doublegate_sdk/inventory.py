"""The inventory page as the gate answers it: metadata, scope and coverage,
never converted into a claim of admission."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from doublegate_sdk.errors import GateError


def _integer(value: Any, minimum: int) -> bool:
    return type(value) is int and value >= minimum


def _require(condition: bool) -> None:
    if not condition:
        raise GateError('invalid_response')


@dataclass(frozen=True)
class InventoryPage:
    records: tuple[dict[str, Any], ...]
    total: int
    limit: int
    offset: int
    has_more: bool
    next_offset: int | None
    complete: bool
    coverage_errors: tuple[Any, ...]
    scope: dict[str, Any]
    counts: dict[str, Any]

    @classmethod
    def from_response(cls, data: dict[str, Any], *, requested_offset: int) -> InventoryPage:
        try:
            rows, coverage = data['page'], data['coverage']
            _require(isinstance(rows, list) and all(isinstance(r, dict) for r in rows))
            _require(_integer(data['total'], 0) and _integer(data['limit'], 1))
            _require(_integer(data['offset'], 0) and data['offset'] == requested_offset)
            _require(len(rows) <= data['limit'] and len(rows) <= data['total'])
            _require(type(data['has_more']) is bool)
            _require(data['has_more'] == (data['offset'] + len(rows) < data['total']))
            _require(not rows or data['offset'] + len(rows) <= data['total'])
            following = data['next_offset']
            if data['has_more']:
                _require(bool(rows) and _integer(following, data['offset'] + 1))
                _require(following == data['offset'] + len(rows))
            else:
                _require(following is None)
            _require(type(coverage['complete']) is bool and isinstance(coverage['errors'], list))
            _require(not coverage['complete'] or not coverage['errors'])
            _require(isinstance(data['scope'], dict) and isinstance(data['counts'], dict))
            return cls(tuple(dict(r) for r in rows), data['total'], data['limit'],
                       data['offset'], data['has_more'], following, coverage['complete'],
                       tuple(coverage['errors']), dict(data['scope']), dict(data['counts']))
        except (KeyError, TypeError, AssertionError):
            raise GateError('invalid_response') from None
