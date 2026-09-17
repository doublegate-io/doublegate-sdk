"""One error base, one gate vocabulary (ADR-0074 d5).

``DoublegateError`` is the base every SDK failure shares, so a caller can catch
the SDK without swallowing unrelated application exceptions. ``GateError`` is
the one raised on a wire: ``kind`` is what a caller acts on; ``code`` is the
server's JSON-RPC number or HTTP status when there was one; ``detail`` carries
the server's message, capped, and never enters ``str(exc)`` so a log line
cannot leak what a gate said about a held row. The two tables here are the
same ones the constellation app's ``rpc.ts`` uses; change both or neither.
"""
from __future__ import annotations

DETAIL_MAX = 512


class DoublegateError(Exception):
    """Catch SDK failures without swallowing unrelated application exceptions.

    Concrete exceptions retain their own safe diagnostic, protocol code and
    established ValueError/RuntimeError compatibility. ``kind`` names the category;
    specialized fields provide details without parsing human-readable text.
    """

    kind = 'sdk_error'


#: JSON-RPC error code -> kind. A code not listed is ``remote_error``.
CODE_KINDS: dict[int, str] = {
    -32000: 'identity',
    -32001: 'request_too_large', -32002: 'request_too_large',
    -32600: 'invalid_params', -32602: 'invalid_params',
    -32601: 'unsupported_operation',
    -32006: 'busy',
    -32009: 'banned',
    -32010: 'refused', -32012: 'refused', -32013: 'refused', -32014: 'refused',
}

#: HTTP status -> kind, for the ``POST /rpc`` transport. Anything else is ``remote_error``.
HTTP_KINDS: dict[int, str] = {401: 'auth', 403: 'scope', 413: 'request_too_large', 429: 'busy', 503: 'busy'}

#: Every kind this SDK raises, so a caller can enumerate what to handle.
KINDS: frozenset[str] = frozenset({
    # decided on this side, before or around I/O
    'unavailable', 'timeout', 'unsupported_transport', 'invalid_response', 'request_too_large',
    'response_too_large', 'writes_disabled', 'forbidden_operation', 'page_limit',
    'redirect_refused',
    # the server's answer
    'identity', 'invalid_params', 'unsupported_operation', 'busy', 'banned', 'refused', 'remote_error',
    # the HTTP ``/rpc`` door's answer
    'auth', 'scope',
    # the HTTP ``/mcp`` door's answer (``doublegate_sdk.client``)
    'unauthorized', 'forbidden', 'tool_error',
})


class GateError(DoublegateError, RuntimeError):
    """A fixed diagnostic. ``str(exc)`` is the kind (and code); the server's text is ``detail``."""

    def __init__(self, kind: str, code: int | None = None, *, retry_after_ms: int | None = None,
                 outcome_unknown: bool = False, detail: str | None = None):
        if kind not in KINDS:
            raise ValueError(f'unknown error kind {kind!r}')
        self.kind, self.code = kind, code
        self.retry_after_ms = retry_after_ms
        self.outcome_unknown = outcome_unknown
        self.detail = None if detail is None else str(detail)[:DETAIL_MAX]
        super().__init__(kind if code is None else f'{kind} ({code})')

    def __repr__(self) -> str:
        return f'GateError(kind={self.kind!r}, code={self.code!r}, outcome_unknown={self.outcome_unknown!r})'


def kind_of_code(code: int) -> str:
    return CODE_KINDS.get(code, 'remote_error')


def kind_of_status(status: int) -> str:
    return HTTP_KINDS.get(status, 'remote_error')
