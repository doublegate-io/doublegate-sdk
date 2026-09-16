"""Explicit gate client over the product's public HTTP/MCP endpoint.

The gate's supported network surface is ``POST /mcp`` (ADR-0050): one JSON-RPC
message in, one JSON object out. :class:`HttpMcpTransport` speaks that and only
that. It is not an authorization layer, not a general MCP framework, and not a
streaming client — the server issues no session id and offers no SSE stream, so
neither does this.

Nothing connects on import or on construction. There is no endpoint discovery,
no credential discovery, no redirect following and no automatic retry.

The tool catalog below is the maintained client tier's, read from the service's
own schema. It is deliberately narrow: there is **no inventory tool**, and
``status`` requires an artifact id. See ``docs/api/client.md``.
"""
from __future__ import annotations

from dataclasses import dataclass
import http.client
import inspect
import io
import json
import math
import ssl
import time
import urllib.parse
from typing import Any, Protocol

#: The MCP tools the maintained client tier serves, and whether each one writes.
#: Read from ``doublegate/mcp.py`` (``TIER_TOOLS["client"]``). A tool absent here
#: is refused locally rather than sent and blamed on the server.
_TOOLS: dict[str, bool] = {
    'doublegate.remember': True,
    'doublegate.recall': False,
    'doublegate.status': False,
    'doublegate.why': False,
    'doublegate.tip': False,
    'doublegate.pending': False,
    'doublegate.rank': True,
    'doublegate.hide': True,
    'doublegate.ban': True,
}

#: The SDK's own operations, and the tool each one calls. ``describe_client``
#: reports this; there is no entry without a tool behind it.
_OPERATIONS: dict[str, dict[str, Any]] = {
    'status': {'tool': 'doublegate.status', 'mutates': False},
    'recall': {'tool': 'doublegate.recall', 'mutates': False},
    'pending': {'tool': 'doublegate.pending', 'mutates': False},
    'why': {'tool': 'doublegate.why', 'mutates': False},
    'propose': {'tool': 'doublegate.remember', 'mutates': True},
}

_LOOPBACK_HOSTS = ('127.0.0.1', 'localhost', '::1', '[::1]')

#: Characters a URL path may not carry. ``http.client`` refuses these itself,
#: but only once the request is on its way out — which reads to an operator as
#: the gate being unreachable. They are refused at construction instead.
_FORBIDDEN_PATH_CHARS = frozenset(chr(c) for c in range(0x21)) | {chr(0x7f)}

#: JSON-RPC codes that mean "this build does not serve that" rather than "the
#: call failed". ``-32602`` lands here because the server answers an unknown
#: tool name with it (``mcp.py`` ``_call``), which is a catalog gap, not a bug.
_UNSUPPORTED_CODES = frozenset({-32601, -32602})


from doublegate_sdk.errors import GateError  # one class for every door (ADR-0068 d5)

__all__ = ["GateClient", "GateError", "HttpMcpTransport", "McpTransport", "connect", "describe_client"]


class McpTransport(Protocol):
    """Explicit tool transport over ``POST /mcp``; the receiving gate remains authoritative.

    Distinct from ``doublegate_sdk.transport.GateTransport``, whose ``call`` takes a
    ``dg.*`` verb for ``POST /rpc`` or the Unix socket. Same shape, different vocabulary.
    """

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


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


def _positive_finite(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value > 0


class _DeadlineReader(io.RawIOBase):
    """A raw reader that re-arms the socket timeout from a total deadline.

    ``http.client`` reads a response through a buffered file over the socket, so
    a single ``settimeout`` before ``getresponse()`` bounds each *recv*, not the
    exchange: a peer that emits one byte per interval is never late and never
    finishes. Re-arming from the remaining budget before every read makes the
    deadline total, and costs no thread — when the budget is gone ``remaining()``
    raises :class:`TimeoutError` on the calling thread.
    """

    def __init__(self, sock: Any, remaining: Any):
        self._sock, self._remaining = sock, remaining
        # Let the standard library retain the descriptor until the body closes.
        self._raw = sock.makefile('rb', buffering=0)

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        self._sock.settimeout(self._remaining())
        return self._raw.readinto(buffer)

    def close(self) -> None:
        try:
            self._raw.close()
        finally:
            super().close()


class _DeadlineSocket:
    """Hands ``http.client`` a deadline-bounded file over the real socket.

    ``HTTPResponse`` touches the socket only through ``makefile('rb')``; every
    other attribute is delegated so ``close()`` and friends behave normally.
    """

    def __init__(self, sock: Any, remaining: Any):
        self._sock, self._remaining = sock, remaining

    def makefile(self, mode: str = 'rb', *args: Any, **kwargs: Any) -> Any:
        if 'b' not in mode or ('r' not in mode and '+' not in mode):
            return self._sock.makefile(mode, *args, **kwargs)
        return io.BufferedReader(_DeadlineReader(self._sock, self._remaining))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._sock, name)


def _is_json_media_type(header: str | None) -> bool:
    """``application/json`` (or a ``+json`` structured suffix), parameters aside."""
    if not header:
        return False
    media = header.split(';', 1)[0].strip().lower()
    return media == 'application/json' or media.endswith('+json')


@dataclass(frozen=True)
class _Endpoint:
    scheme: str
    host: str
    port: int
    path: str
    loopback: bool


def _parse_endpoint(url: Any, *, allow_insecure_loopback: bool) -> _Endpoint:
    if not isinstance(url, str) or not url:
        raise ValueError('endpoint must be a nonempty https:// (or opted-in loopback http://) URL')
    if any(c in url for c in '\t\r\n'):
        # ``urlsplit`` deletes these silently, so ``https://host/mcp\tx`` would
        # quietly become ``/mcpx``. An endpoint that parses to something other
        # than what was written is refused rather than corrected.
        raise ValueError('endpoint carries control characters')
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ('https', 'http'):
        raise ValueError('endpoint scheme must be https or (opted-in) loopback http')
    if parts.username is not None or parts.password is not None:
        # ``urlsplit().hostname`` strips userinfo, so a credential written here
        # is never sent — it only lingers in ``transport.endpoint`` waiting to be
        # logged. Refuse it, as ``observability/options.py`` already does. An
        # empty username (``https://@host/mcp``) counts: it is still userinfo.
        raise ValueError('endpoint carries no credentials; pass token= instead')
    if not parts.hostname:
        raise ValueError('endpoint must name a host')
    host = parts.hostname
    loopback = host in _LOOPBACK_HOSTS
    if parts.scheme == 'http':
        if not allow_insecure_loopback:
            raise ValueError('plain http requires allow_insecure_loopback=True and a loopback host')
        if not loopback:
            raise ValueError('allow_insecure_loopback covers loopback hosts only, never a remote endpoint')
    if parts.query or parts.fragment:
        raise ValueError('endpoint carries no query or fragment')
    path = parts.path or '/mcp'
    if any(c in _FORBIDDEN_PATH_CHARS for c in path):
        raise ValueError('endpoint path carries control characters or whitespace')
    port = parts.port or (443 if parts.scheme == 'https' else 80)
    return _Endpoint(parts.scheme, host, port, path, loopback)


class HttpMcpTransport:
    """The product's ``POST /mcp`` endpoint, bounded and explicit.

    HTTPS is the default and the only remote option. ``allow_insecure_loopback``
    exists for tests and for a daemon on this host; it refuses a remote host.

    Writes are off unless ``allow_writes=True`` — the transport refuses a
    write-annotated tool itself, so a read-only client cannot be talked into
    writing by a caller that reaches past :class:`GateClient`.

    ``timeout`` is a total deadline for the whole exchange, not a per-socket
    operation timeout: it covers connect, send, the status line, the headers and
    the body, and is re-checked before every read, so a peer cannot extend it by
    answering slowly. It is enforced on the calling thread with no background
    worker. One thing it does **not** cover: name resolution, which the standard
    library performs inside ``connect()`` with no cancellation hook — a DNS
    server that hangs is bounded by the resolver, not by this. Use an IP literal
    or a resolver timeout if that matters to you.

    Nothing is retried: a write repeated without an idempotency contract is
    worse than a write that failed loudly.
    """

    #: The stateless JSON request/response subset the server actually serves.
    #: No ``Mcp-Session-Id``, no SSE, no ``initialize`` handshake by default.
    PROTOCOL_SUBSET = 'stateless-json-request-response'

    def __init__(self, endpoint: str, *, token: str | None = None, timeout: float = 10.0,
                 max_response_bytes: int = 1_048_576, max_request_bytes: int = 1_048_576,
                 allow_writes: bool = False, allow_insecure_loopback: bool = False,
                 ssl_context: ssl.SSLContext | None = None):
        if type(allow_insecure_loopback) is not bool:
            raise ValueError('allow_insecure_loopback must be boolean')
        if type(allow_writes) is not bool:
            raise ValueError('allow_writes must be boolean')
        if not _positive_finite(timeout):
            raise ValueError('timeout must be finite and positive')
        if not _integer(max_response_bytes, 1):
            raise ValueError('max_response_bytes must be a positive integer')
        if not _integer(max_request_bytes, 1):
            raise ValueError('max_request_bytes must be a positive integer')
        if token is not None:
            if (not isinstance(token, str) or not token
                    or any(c in token for c in '\r\n')):
                raise ValueError('token must be nonempty single-line text')
            try:
                # ``http.client`` encodes headers as Latin-1. A token outside
                # that range would otherwise blow up inside ``request()`` as a
                # raw UnicodeEncodeError, mid-call and outside the taxonomy.
                # A credential problem belongs at construction.
                token.encode('latin-1')
            except UnicodeEncodeError:
                raise ValueError('token must be nonempty single-line text') from None
        self._target = _parse_endpoint(endpoint, allow_insecure_loopback=allow_insecure_loopback)
        self.endpoint = endpoint
        self._token = token
        self._timeout = float(timeout)
        self.max_response_bytes = max_response_bytes
        self.max_request_bytes = max_request_bytes
        self._allow_writes = allow_writes
        self._ssl_context = ssl_context

    # ---- protocol negotiation (only what the server supports) ----

    def discover(self) -> dict[str, Any]:
        """``server/discover`` — protocol versions, server info, capabilities.

        This is the negotiation method the maintained server always answers.
        ``initialize`` is refused unless the operator turned on a legacy flag,
        so the SDK does not send it.
        """
        return self._rpc('server/discover', {}, writing=False)

    def tool_names(self) -> tuple[str, ...]:
        """The tools this server actually lists, in its own order."""
        result = self._rpc('tools/list', {}, writing=False)
        tools = result.get('tools')
        _require(isinstance(tools, list))
        names = []
        for tool in tools:
            _require(isinstance(tool, dict) and isinstance(tool.get('name'), str))
            names.append(tool['name'])
        return tuple(names)

    # ---- tool calls ----

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool not in _TOOLS:
            raise ValueError(f'unknown tool for this client tier: {tool!r}')
        writing = _TOOLS[tool]
        if writing and not self._allow_writes:
            raise GateError('writes_disabled')
        result = self._rpc('tools/call', {'name': tool, 'arguments': arguments}, writing=writing)
        if result.get('isError'):
            raise GateError('tool_error', outcome_unknown=writing)
        payload = result.get('structuredContent')
        if not isinstance(payload, dict):
            # The text block carries the same payload, but a response missing
            # the structured half is not one this client will guess at.
            raise GateError('invalid_response', outcome_unknown=writing)
        return payload

    # ---- the wire ----

    def _rpc(self, method: str, params: dict[str, Any], *, writing: bool) -> dict[str, Any]:
        try:
            body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method,
                               'params': params}, allow_nan=False).encode()
        except (ValueError, TypeError):
            raise ValueError('arguments must be JSON-serializable without NaN or Infinity') from None
        if len(body) > self.max_request_bytes:
            raise GateError('request_too_large')

        headers = {'Content-Type': 'application/json', 'Accept': 'application/json',
                   'Content-Length': str(len(body))}
        if self._token is not None:
            headers['Authorization'] = f'Bearer {self._token}'

        deadline = time.monotonic() + self._timeout
        sending = False
        connection = None
        try:
            connection = self._connect(deadline)
            # ``sending`` stays conservative: it is set before the socket is
            # touched, so a failure anywhere from connect onwards reports a
            # write's outcome as unknown rather than guessing it did not land.
            sending = True
            connection.connect()          # explicit: ``sock`` must exist to arm
            connection.sock.settimeout(self._remaining(deadline))
            connection.request('POST', self._target.path, body=body, headers=headers)
            # Read the status line, the headers and the body through one
            # deadline-bounded file. A slow drip of *headers* is bounded by the
            # same budget as a slow drip of body bytes.
            connection.sock = _DeadlineSocket(connection.sock,
                                              lambda: self._remaining(deadline))
            response = connection.getresponse()
            status = response.status
            content_type = response.getheader('Content-Type')
            if status in (301, 302, 303, 307, 308):
                # Following a redirect would carry the bearer token to whatever
                # host the answer names. It is refused, not followed.
                raise GateError('redirect_refused', status)
            raw = self._read_bounded(response, deadline)
            if len(raw) > self.max_response_bytes:
                raise GateError('response_too_large', outcome_unknown=writing)
        except GateError:
            raise
        except TimeoutError:
            raise GateError('timeout', outcome_unknown=writing and sending) from None
        except (OSError, http.client.HTTPException):
            raise GateError('unavailable', outcome_unknown=writing and sending) from None
        finally:
            if connection is not None:
                connection.close()

        if status == 401:
            raise GateError('unauthorized', 401)
        if status == 403:
            raise GateError('forbidden', 403)
        if status == 413:
            raise GateError('request_too_large', 413)
        if status == 405:
            raise GateError('unsupported_operation', 405)
        if status == 202:
            # A notification was accepted. This client sends none.
            raise GateError('invalid_response', 202, outcome_unknown=writing)
        if status != 200:
            raise GateError('remote_error', status, outcome_unknown=writing)
        if not _is_json_media_type(content_type):
            # The request said ``Accept: application/json``. A 200 labelled
            # anything else is an interstitial or a proxy, not this gate's
            # answer — even when its body happens to parse.
            raise GateError('invalid_response', outcome_unknown=writing)
        return self._parse(raw, writing=writing)

    def _read_bounded(self, response: Any, deadline: float) -> bytes:
        """Read at most ``max_response_bytes + 1`` bytes, deadline enforced.

        ``read(n)`` would loop inside the buffered reader until it had all ``n``
        bytes; ``read1`` returns what one recv produced, so the budget is checked
        between chunks as well as before each recv.
        """
        limit = self.max_response_bytes + 1
        chunks: list[bytes] = []
        total = 0
        while total < limit:
            self._remaining(deadline)
            chunk = response.read1(limit - total)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        return b''.join(chunks)

    def _connect(self, deadline: float) -> Any:
        timeout = self._remaining(deadline)
        if self._target.scheme == 'https':
            context = self._ssl_context or ssl.create_default_context()
            return http.client.HTTPSConnection(self._target.host, self._target.port,
                                               timeout=timeout, context=context)
        return http.client.HTTPConnection(self._target.host, self._target.port, timeout=timeout)

    def _remaining(self, deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        return remaining

    def _parse(self, raw: bytes, *, writing: bool) -> dict[str, Any]:
        try:
            message = json.loads(raw, object_pairs_hook=_unique_object)
            _require(isinstance(message, dict) and message.get('jsonrpc') == '2.0')
            _require(type(message.get('id')) is int and message['id'] == 1)
            _require(('result' in message) != ('error' in message))
            if 'error' in message:
                code = message['error']['code']
                _require(type(code) is int)
                kind = 'unsupported_operation' if code in _UNSUPPORTED_CODES else 'remote_error'
                # -32603 is the server's own internal failure: a write may or
                # may not have landed, and saying otherwise would be a guess.
                raise GateError(kind, code, outcome_unknown=writing and code == -32603)
            _require(isinstance(message['result'], dict))
            return message['result']
        except GateError as error:
            if error.kind == 'invalid_response' and writing:
                error.outcome_unknown = True
            raise
        except (ValueError, KeyError, TypeError, RecursionError):
            # RecursionError: ``json.loads`` on a deeply nested document, well
            # inside the byte bound. ``package.py`` already treats it as bad
            # input; a caller's ``except DoublegateError`` must catch it here
            # too, and a write must still report its outcome as unknown.
            raise GateError('invalid_response', outcome_unknown=writing) from None


def connect(endpoint: str, *, token: str | None = None, allow_writes: bool = False,
            timeout: float = 10.0, allow_insecure_loopback: bool = False,
            max_response_bytes: int = 1_048_576, max_request_bytes: int = 1_048_576,
            ssl_context: ssl.SSLContext | None = None) -> 'GateClient':
    """Build a client for one gate endpoint. The one call most callers need.

    ``connect('https://gate.example.org/mcp', token=...)`` is equivalent to
    wrapping :class:`HttpMcpTransport` in a :class:`GateClient`, and exists so a
    caller does not have to assemble a transport to make an ordinary request.

    It connects to nothing: the first request happens on the first method call.
    Every bound and every credential is still explicit, and writes remain off
    unless ``allow_writes=True``.
    """
    return GateClient(HttpMcpTransport(
        endpoint, token=token, timeout=timeout, allow_writes=allow_writes,
        allow_insecure_loopback=allow_insecure_loopback,
        max_response_bytes=max_response_bytes, max_request_bytes=max_request_bytes,
        ssl_context=ssl_context))


class GateClient:
    """Propose observations and read gate answers through an explicit transport.

    Usually built by :func:`connect`. Presence in a read is not admission, and a
    proposal is not an approval. The gate decides; this object carries bytes.
    """

    def __init__(self, transport: McpTransport):
        self._transport = transport

    @property
    def transport(self) -> McpTransport:
        """The underlying transport — for ``discover()`` / ``tool_names()``."""
        return self._transport

    @staticmethod
    def _artifact_id(artifact_id: Any) -> str:
        if not isinstance(artifact_id, str) or not artifact_id.strip():
            raise ValueError('artifact_id must be a nonempty string')
        return artifact_id

    def status(self, artifact_id: str) -> dict[str, Any]:
        """One artifact's position in the register.

        The maintained tool requires an artifact id; there is no whole-gate
        status call on this surface.
        """
        result = self._transport.call('doublegate.status',
                                      {'artifact_id': self._artifact_id(artifact_id)})
        if not isinstance(result, dict) or not isinstance(result.get('state'), str):
            raise GateError('invalid_response')
        return result

    def why(self, artifact_id: str) -> dict[str, Any]:
        """Every ledger event that moved one artifact, in order."""
        result = self._transport.call('doublegate.why',
                                      {'artifact_id': self._artifact_id(artifact_id)})
        if not isinstance(result, dict) or not isinstance(result.get('events'), list):
            raise GateError('invalid_response')
        return result

    def propose(self, content: str | bytes, *, content_type: str, source_uri: str,
                trust_class: str | None = None, space: str | None = None,
                derives_from: list[str] | None = None) -> dict[str, Any]:
        """Submit content for admission. Not an approval, and never retried.

        ``content`` is text. Bytes are accepted only when they are valid UTF-8
        and are decoded; binary is refused rather than base64-smuggled into a
        text field the service would store verbatim.

        Writer identity is never sent: the service derives it from the
        connection and refuses a request that carries it.
        """
        if isinstance(content, bytes):
            try:
                content = content.decode('utf-8')
            except UnicodeDecodeError:
                raise ValueError('content must be UTF-8 text; this surface stores no binary') from None
        if not isinstance(content, str) or not content:
            raise ValueError('content must be nonempty text')
        if any(not isinstance(v, str) or not v for v in (content_type, source_uri)):
            raise ValueError('content_type and source_uri are required nonempty strings')
        arguments: dict[str, Any] = {'content': content, 'content_type': content_type,
                                     'source_uri': source_uri}
        for name, value in (('trust_class', trust_class), ('space', space)):
            if value is not None:
                if not isinstance(value, str) or not value:
                    raise ValueError(f'{name} must be nonempty text when given')
                arguments[name] = value
        if derives_from is not None:
            if (not isinstance(derives_from, list)
                    or any(not isinstance(p, str) or not p for p in derives_from)):
                raise ValueError('derives_from must be a list of nonempty artifact ids')
            arguments['derives_from'] = list(derives_from)
        result = self._transport.call('doublegate.remember', arguments)
        if (not isinstance(result, dict) or not isinstance(result.get('artifact_id'), str)
                or not result['artifact_id'] or not isinstance(result.get('state'), str)
                or not result['state']):
            raise GateError('invalid_response', outcome_unknown=True)
        return result

    def recall(self, query: str, *, limit: int = 10, spaces: list[str] | None = None,
               include_provisional: bool = False, audience: str | None = None) -> dict[str, Any]:
        """Read served knowledge.

        Own unreviewed echoes are excluded by default, so a caller does not read
        back its own un-admitted proposal and mistake it for an answer.
        """
        if not isinstance(query, str) or not query.strip():
            raise ValueError('query must be nonempty text')
        if not _integer(limit, 1):
            raise ValueError('limit must be a positive integer')
        if type(include_provisional) is not bool:
            raise ValueError('include_provisional must be boolean')
        if spaces is not None and (not isinstance(spaces, list) or not spaces
                                   or any(not isinstance(s, str) or not s for s in spaces)):
            raise ValueError('spaces must be a nonempty list of strings')
        arguments: dict[str, Any] = {'query': query, 'k': limit, 'include_own_pending': False,
                                     'include_provisional': include_provisional}
        if spaces is not None:
            arguments['spaces'] = spaces
        if audience is not None:
            if not isinstance(audience, str) or not audience:
                raise ValueError('audience must be nonempty text when given')
            arguments['audience'] = audience
        result = self._transport.call('doublegate.recall', arguments)
        if (not isinstance(result, dict) or not isinstance(result.get('results'), list)
                or not all(isinstance(hit, dict) for hit in result['results'])
                or len(result['results']) > limit):
            raise GateError('invalid_response')
        return result

    def pending(self, *, limit: int = 100) -> dict[str, Any]:
        """What is waiting for review, as metadata only — never content."""
        if not _integer(limit, 1):
            raise ValueError('limit must be a positive integer')
        result = self._transport.call('doublegate.pending', {'limit': limit})
        if (not isinstance(result, dict) or not isinstance(result.get('pending'), list)
                or not all(isinstance(item, dict) for item in result['pending'])
                or len(result['pending']) > limit):
            raise GateError('invalid_response')
        return result


def describe_client() -> dict[str, Any]:
    """Describe this SDK's Python API offline. Not a remote gate's capabilities."""
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
    return {
        'scope': 'sdk-contract-not-server-capabilities',
        'format': 'python-call-description-not-json-schema',
        'transports': ['http_mcp', 'caller_supplied'],
        'mcp': {
            'endpoint': 'POST /mcp',
            'subset': HttpMcpTransport.PROTOCOL_SUBSET,
            'streaming': False,
            'sessions': False,
            'negotiation': ['server/discover', 'tools/list'],
            'tools': sorted(_TOOLS),
        },
        'unsupported_operations': {
            'inventory': 'the maintained client tier serves no inventory tool',
            'whole_gate_status': 'doublegate.status requires an artifact id',
        },
        'proposals_are_admission': False,
        'automatic_retries': False,
        'follows_redirects': False,
        'operations': operations,
    }
