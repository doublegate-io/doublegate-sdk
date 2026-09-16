"""Two transports, one framing, one allowlist (ADR-0074 d1, d6).

No connection on import or construction; no discovery, credentials, retries
or alternate endpoints. The transport's ``scope`` is the widest class of
verb it will emit — checked before any I/O — and a refusal here is a
courtesy: the receiving gate refuses regardless, from the identity it
derived off the connection (I2). A caller-supplied ``GateTransport`` owns
its own allowlist.

Both transports here carry ``dg.*`` verbs: ``UnixSocketTransport`` over the
daemon socket, ``HttpTransport`` over ``POST /rpc``. The third door,
``POST /mcp`` (the MCP tool catalog), is ``doublegate_sdk.client.HttpMcpTransport``
behind ``doublegate_sdk.client.McpTransport``; same shape, different vocabulary.
"""
from __future__ import annotations

import http.client
import json
import math
import os
import socket
import time
import urllib.parse
from collections.abc import Callable
from typing import Any, Protocol

from doublegate_sdk.errors import GateError, kind_of_code, kind_of_status
from doublegate_sdk.operations import CURATION, OPERATIONS, READ, allows, check_scope

MAX_REQUEST_BYTES = 1_048_576
DEFAULT_MAX_RESPONSE_BYTES = 1_048_576


class GateTransport(Protocol):
    """Explicit operation transport; the receiver remains authoritative."""
    def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]: ...


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GateError('invalid_response')
        result[key] = value
    return result


def _bearer_text(bearer: Any) -> str:
    """A bearer is text without whitespace; anything else is refused before I/O."""
    if isinstance(bearer, str) and not any(c.isspace() for c in bearer):
        return bearer
    raise ValueError('bearer must be text without whitespace')


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError
    return remaining


def encode_request(method: str, params: dict[str, Any]) -> bytes:
    """One JSON-RPC 2.0 message, id 1, bounded."""
    if not isinstance(params, dict):
        raise ValueError('params must be an object')
    request = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params},
                         allow_nan=False).encode()
    if len(request) > MAX_REQUEST_BYTES:
        raise GateError('request_too_large')
    return request


def decode_response(raw: bytes, *, writing: bool) -> dict[str, Any]:
    """The result of a JSON-RPC reply, or the refusal it carried as a ``GateError``."""
    try:
        data = json.loads(raw, object_pairs_hook=_unique_object)
        if not (isinstance(data, dict) and data.get('jsonrpc') == '2.0'):
            raise GateError('invalid_response')
        if not (type(data.get('id')) is int and data['id'] == 1):
            raise GateError('invalid_response')
        if ('result' in data) == ('error' in data):
            raise GateError('invalid_response')
        if 'error' in data:
            err = data['error']
            code = err['code']
            if type(code) is not int:
                raise GateError('invalid_response')
            extra = err.get('data') if isinstance(err.get('data'), dict) else {}
            retry = extra.get('retry_after_ms')
            raise GateError(kind_of_code(code), code,
                            retry_after_ms=retry if type(retry) is int else None,
                            outcome_unknown=writing and code == -32603,
                            detail=err.get('message') if isinstance(err.get('message'), str) else None)
        if not isinstance(data['result'], dict):
            raise GateError('invalid_response')
        return data['result']
    except (ValueError, KeyError, TypeError, AttributeError):
        raise GateError('invalid_response', outcome_unknown=writing) from None
    except GateError as exc:
        if exc.kind == 'invalid_response':
            exc.outcome_unknown = writing
        raise


class _Bounded:
    """What both transports share: scope, timeout, response cap, the pre-I/O checks."""
    _scope: str
    _timeout: float
    _max_bytes: int

    def _init_bounds(self, timeout: float, max_response_bytes: int, scope: str) -> None:
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('timeout must be finite and positive')
        if type(max_response_bytes) is not int or max_response_bytes < 1:
            raise ValueError('max_response_bytes must be a positive integer')
        self._scope = check_scope(scope)
        self._timeout, self._max_bytes = float(timeout), max_response_bytes

    @property
    def scope(self) -> str:
        return self._scope

    @property
    def timeout(self) -> float:
        return self._timeout

    def _admit(self, method: str) -> bool:
        """The verb is known and within scope; returns whether it writes."""
        if method not in OPERATIONS:
            raise ValueError('unsupported transport operation')
        op = OPERATIONS[method]
        if not allows(self._scope, method):
            raise GateError('writes_disabled' if self._scope == READ and op.mutates else 'forbidden_operation')
        return op.mutates


class UnixSocketTransport(_Bounded):
    """Bounded local JSON-RPC over the gate's own socket, newline-framed."""

    def __init__(self, path: str | os.PathLike[str], *, timeout: float = 10.0,
                 max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES, scope: str = READ):
        self._init_bounds(timeout, max_response_bytes, scope)
        raw_path = os.fspath(path)
        if not isinstance(raw_path, str) or not raw_path or '\x00' in raw_path:
            raise ValueError('path must be nonempty text without NUL characters')
        self._path = os.path.abspath(raw_path)

    @property
    def path(self) -> str:
        return self._path

    def with_timeout(self, timeout: float) -> UnixSocketTransport:
        return UnixSocketTransport(self._path, timeout=timeout, max_response_bytes=self._max_bytes, scope=self._scope)

    def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        writing = self._admit(method)
        if not hasattr(socket, 'AF_UNIX'):
            raise GateError('unsupported_transport')
        request = encode_request(method, params) + b'\n'
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
        return decode_response(bytes(response), writing=writing)


class HttpTransport(_Bounded):
    """The same message over ``POST {base}/rpc`` with a bearer: the client
    gate's console token on its loopback listener, an organization gate's
    admin key, or an assertion an agent mints from its own key (ADR-0080 d4).
    ``bearer`` is the string itself or a zero-argument callable returning the
    current one, called once per request — the shape a short-lived assertion
    needs, re-minted by its holder before it expires. JSON-RPC refusals ride a
    200; the door's own refusals are HTTP statuses (401 auth, 403 scope, 413,
    429/503 with Retry-After)."""

    def __init__(self, base_url: str, *, bearer: str | Callable[[], str] = '', timeout: float = 10.0,
                 max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES, scope: str = READ):
        self._init_bounds(timeout, max_response_bytes, scope)
        if not isinstance(base_url, str) or not base_url:
            raise ValueError('base_url must be nonempty text')
        parts = urllib.parse.urlsplit(base_url)
        if parts.scheme not in ('http', 'https') or not parts.hostname:
            raise ValueError('base_url must be an http(s) URL with a host')
        if not callable(bearer):
            _bearer_text(bearer)
        self._parts, self._bearer = parts, bearer
        self._base_url = base_url

    @property
    def base_url(self) -> str:
        return self._base_url

    def with_timeout(self, timeout: float) -> HttpTransport:
        return HttpTransport(self._base_url, bearer=self._bearer, timeout=timeout,
                             max_response_bytes=self._max_bytes, scope=self._scope)

    def _connection(self, timeout: float) -> http.client.HTTPConnection:
        cls = http.client.HTTPSConnection if self._parts.scheme == 'https' else http.client.HTTPConnection
        port = self._parts.port or (443 if self._parts.scheme == 'https' else 80)
        return cls(self._parts.hostname or '', port, timeout=timeout)

    def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        writing = self._admit(method)
        body = encode_request(method, params)
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        bearer = _bearer_text(self._bearer()) if callable(self._bearer) else self._bearer
        if bearer:
            headers['Authorization'] = 'Bearer ' + bearer
        path = (self._parts.path.rstrip('/') or '') + '/rpc'
        sending = False
        try:
            connection = self._connection(self._timeout)
            try:
                sending = True
                connection.request('POST', path, body=body, headers=headers)
                response = connection.getresponse()
                status = response.status
                retry_after = response.getheader('Retry-After')
                raw = response.read(self._max_bytes + 1)
            finally:
                connection.close()
        except TimeoutError:
            raise GateError('timeout', outcome_unknown=writing and sending) from None
        except (OSError, http.client.HTTPException):
            raise GateError('unavailable', outcome_unknown=writing and sending) from None
        if len(raw) > self._max_bytes:
            raise GateError('response_too_large', outcome_unknown=writing)
        if status != 200:
            retry = None
            if retry_after is not None:
                try:
                    retry = int(float(retry_after) * 1000)
                except ValueError:
                    retry = None
            raise GateError(kind_of_status(status), retry_after_ms=retry,
                            outcome_unknown=writing and status >= 500,
                            detail=raw.decode('utf-8', 'replace') if raw else None)
        return decode_response(raw, writing=writing)


__all__ = ['GateTransport', 'UnixSocketTransport', 'HttpTransport', 'encode_request', 'decode_response',
           'MAX_REQUEST_BYTES', 'DEFAULT_MAX_RESPONSE_BYTES', 'CURATION']
