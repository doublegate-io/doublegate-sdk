"""Explicit gate client. No connections on import or construction.

UnixSocketTransport implements the existing local JSON-RPC surface. It is not
an HTTP client or an authorization substitute. Offline SDK modules do not import it.
"""
from __future__ import annotations

from dataclasses import dataclass
import base64
import inspect
import json
import math
import os
import socket
import time
from typing import Any, Iterator, Protocol


_OPERATIONS = {
    'status': {'rpc_method': 'dg.status', 'mutates': False},
    'inventory': {'rpc_method': 'dg.inventory', 'mutates': False},
    'inventory_pages': {'rpc_method': None, 'mutates': False},
    'recall': {'rpc_method': 'dg.recall', 'mutates': False},
    'propose': {'rpc_method': 'dg.ingest', 'mutates': True},
}


class GateError(RuntimeError):
    """A fixed diagnostic with optional server code; remote error text is withheld."""
    def __init__(self, kind: str, code: int | None = None, *, outcome_unknown: bool = False):
        self.kind, self.code = kind, code
        self.outcome_unknown = outcome_unknown
        super().__init__(kind)


class GateTransport(Protocol):
    """Explicit operation transport; the receiver remains authoritative."""
    def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]: ...


def _integer(value: Any, minimum: int) -> bool:
    return type(value) is int and value >= minimum


def _require(condition: bool) -> None:
    if not condition:
        raise GateError('invalid_response')


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError
    return remaining


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


class GateClient:
    """Propose observations and read gate results through an explicit transport."""
    _FILTERS = frozenset({'state', 'space', 'content_type', 'trust_class',
                         'retrievability', 'source', 'q', 'sort', 'descending'})

    def __init__(self, transport: GateTransport):
        self._transport = transport

    def status(self, artifact_id: str | None = None) -> dict[str, Any]:
        if artifact_id is not None and (not isinstance(artifact_id, str) or not artifact_id):
            raise ValueError('artifact_id must be a nonempty string')
        params = {} if artifact_id is None else {'artifact_id': artifact_id}
        result = self._transport.call(_OPERATIONS['status']['rpc_method'], params)
        if not isinstance(result, dict):
            raise GateError('invalid_response')
        return result

    def propose(self, content: str | bytes, *, content_type: str,
                trust_class: str, source_uri: str, space: str = 'main') -> dict[str, Any]:
        """Submit for processing, not approval. Never retries or supplies writer identity."""
        if not isinstance(content, (str, bytes)):
            raise ValueError('content must be text or bytes')
        if any(not isinstance(v, str) or not v for v in (content_type, trust_class, source_uri, space)):
            raise ValueError('content_type, trust_class, source_uri and space are required strings')
        try:
            raw = content.encode('utf-8') if isinstance(content, str) else content
        except UnicodeEncodeError:
            raise ValueError('invalid_utf8') from None
        result = self._transport.call(_OPERATIONS['propose']['rpc_method'], {
            'content': base64.b64encode(raw).decode('ascii'), 'encoding': 'base64',
            'content_type': content_type, 'trust_class': trust_class,
            'source_uri': source_uri, 'space': space})
        if (not isinstance(result, dict) or not isinstance(result.get('artifact_id'), str)
                or not result['artifact_id'] or not isinstance(result.get('state'), str)
                or not result['state']):
            raise GateError('invalid_response', outcome_unknown=True)
        return result

    def recall(self, query: str, *, limit: int = 10, spaces: list[str] | None = None,
               include_provisional: bool = False) -> dict[str, Any]:
        """Read served knowledge; exclude own unreviewed echoes and provisional rows by default."""
        if not isinstance(query, str) or not query.strip() or not _integer(limit, 1):
            raise ValueError('nonempty query and positive integer limit required')
        if type(include_provisional) is not bool:
            raise ValueError('include_provisional must be boolean')
        if spaces is not None and (not isinstance(spaces, list) or not spaces
                                   or any(not isinstance(s, str) or not s for s in spaces)):
            raise ValueError('spaces must be a nonempty list of strings')
        params = {'query': query, 'k': limit, 'include_own_pending': False,
                  'include_provisional': include_provisional}
        if spaces is not None:
            params['spaces'] = spaces
        result = self._transport.call(_OPERATIONS['recall']['rpc_method'], params)
        if (not isinstance(result, dict) or not isinstance(result.get('hits'), list)
                or not all(isinstance(hit, dict) for hit in result['hits'])
                or len(result['hits']) > limit):
            raise GateError('invalid_response')
        return result

    def inventory(self, *, limit: int = 100, offset: int = 0, **filters: Any) -> InventoryPage:
        if not _integer(limit, 1) or not _integer(offset, 0):
            raise ValueError('limit must be positive and offset nonnegative integers')
        if set(filters) - self._FILTERS:
            raise ValueError('unsupported inventory parameter')
        result = self._transport.call(_OPERATIONS['inventory']['rpc_method'], {'limit': limit, 'offset': offset, **filters})
        if not isinstance(result, dict):
            raise GateError('invalid_response')
        return InventoryPage.from_response(result, requested_offset=offset)

    def inventory_pages(self, *, limit: int = 100, max_pages: int = 10,
                        **filters: Any) -> Iterator[InventoryPage]:
        if not _integer(max_pages, 1):
            raise ValueError('max_pages must be a positive integer')
        offset = 0
        for _ in range(max_pages):
            page = self.inventory(limit=limit, offset=offset, **filters)
            yield page
            if not page.has_more:
                return
            if page.next_offset is None:
                raise GateError('invalid_response')
            offset = page.next_offset
        raise GateError('page_limit')


class UnixSocketTransport:
    """Bounded local JSON-RPC with proposal writes disabled by default.

    Paths are explicit. No discovery, credentials, retries or alternate endpoints.
    """
    def __init__(self, path: str | os.PathLike[str], *, timeout: float = 10.0,
                 max_response_bytes: int = 1_048_576, allow_proposals: bool = False):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('timeout must be finite and positive')
        if not _integer(max_response_bytes, 1):
            raise ValueError('max_response_bytes must be a positive integer')
        if type(allow_proposals) is not bool:
            raise ValueError('allow_proposals must be boolean')
        self._allow_proposals = allow_proposals
        raw_path = os.fspath(path)
        if not isinstance(raw_path, str) or not raw_path or '\x00' in raw_path:
            raise ValueError('path must be nonempty text without NUL characters')
        self._path = os.path.abspath(raw_path)
        self._timeout, self._max_bytes = timeout, max_response_bytes

    def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        operation = next((op for op in _OPERATIONS.values()
                          if op['rpc_method'] is not None and op['rpc_method'] == method), None)
        if operation is None:
            raise ValueError('unsupported transport operation')
        writing = bool(operation['mutates'])
        if writing and not self._allow_proposals:
            raise GateError('writes_disabled')
        if not hasattr(socket, 'AF_UNIX'):
            raise GateError('unsupported_transport')
        request = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method,
                              'params': params}, allow_nan=False).encode() + b'\n'
        if len(request) > 1_048_576:
            raise GateError('request_too_large')
        deadline = time.monotonic() + self._timeout
        sending = False
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(_remaining(deadline))
                connection.connect(self._path)
                connection.settimeout(_remaining(deadline))
                sending = True
                connection.sendall(request)
                response = bytearray()
                while b'\n' not in response:
                    connection.settimeout(_remaining(deadline))
                    chunk = connection.recv(min(65536, self._max_bytes + 1 - len(response)))
                    if not chunk:
                        raise GateError('invalid_response')
                    response.extend(chunk)
                    if len(response) > self._max_bytes:
                        raise GateError('response_too_large')
        except TimeoutError:
            raise GateError('timeout', outcome_unknown=writing and sending) from None
        except OSError:
            raise GateError('unavailable', outcome_unknown=writing and sending) from None
        except GateError as exc:
            exc.outcome_unknown = writing and sending
            raise
        try:
            data = json.loads(response, object_pairs_hook=_unique_object)
            _require(isinstance(data, dict) and data.get('jsonrpc') == '2.0')
            _require(type(data.get('id')) is int and data['id'] == 1)
            _require(('result' in data) != ('error' in data))
            if 'error' in data:
                code = data['error']['code']
                _require(type(code) is int)
                raise GateError('unsupported_operation' if code == -32601 else 'remote_error', code,
                                outcome_unknown=writing and code == -32603)
            _require(isinstance(data['result'], dict))
            return data['result']
        except (ValueError, KeyError, TypeError, AssertionError):
            raise GateError('invalid_response', outcome_unknown=writing) from None
        except GateError as exc:
            if exc.kind == 'invalid_response':
                exc.outcome_unknown = writing
            raise


def describe_client() -> dict[str, Any]:
    """Describe this SDK's Python API offline, not a remote gate's capabilities."""
    operations = {}
    for name, metadata in _OPERATIONS.items():
        signature = inspect.signature(getattr(GateClient, name))
        parameters = []
        for parameter in signature.parameters.values():
            if parameter.name == 'self':
                continue
            variadic = parameter.kind in (inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL)
            value = {'name': parameter.name, 'kind': parameter.kind.name,
                     'annotation': str(parameter.annotation),
                     'required': parameter.default is inspect.Parameter.empty and not variadic}
            if parameter.default is not inspect.Parameter.empty:
                value['default'] = parameter.default
            parameters.append(value)
        operations[name] = {**metadata, 'parameters': parameters,
                            'returns': str(signature.return_annotation)}
    return {'scope': 'sdk-contract-not-server-capabilities',
            'format': 'python-call-description-not-json-schema',
            'transports': ['unix_socket', 'caller_supplied'],
            'proposals_are_admission': False, 'automatic_retries': False,
            'inventory_filters': sorted(GateClient._FILTERS), 'operations': operations}
