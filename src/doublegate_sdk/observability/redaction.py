# Modified for standalone doublegate_sdk namespace; see NOTICE.
"""Redaction applied to every attribute, at every verbosity. Debug does not weaken it."""
from __future__ import annotations

import re
from typing import Any, Mapping

REDACTED = '[redacted]'
MAX_VALUE_LEN = 256

# Keys whose value is never emitted, matched case-insensitively as substrings.
_SECRET_KEY_MARKERS = (
    'authorization', 'proxy-authorization', 'cookie', 'set-cookie', 'api-key', 'apikey',
    'x-api-key', 'token', 'secret', 'password', 'passwd', 'credential', 'private_key',
    'privatekey', 'session', 'signature', 'dsn', 'connection_string', 'auth',
)
# Keys carrying caller content: dropped entirely, not redacted, so no length leaks.
_BODY_KEY_MARKERS = (
    'prompt', 'completion', 'body', 'payload', 'content', 'document', 'artifact', 'text',
    'message', 'response', 'stdout', 'stderr', 'traceback', 'locals', 'query',
)

_BEARER = re.compile(r'\b[Bb]earer\s+[A-Za-z0-9\-._~+/]+=*')
_JWT = re.compile(r'\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]*')
_URL_WITH_USERINFO = re.compile(r'\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s/@]+:[^\s/@]+@\S+')
_URL_WITH_QUERY = re.compile(r'\b([a-zA-Z][a-zA-Z0-9+.-]*://[^\s?#]+)\?\S*')
_LONG_SECRETISH = re.compile(r'\b[A-Za-z0-9_\-]{40,}\b')


def redact_value(value: Any) -> Any:
    """Scrub secret-shaped text out of a scalar and bound its length."""
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return value
    if not isinstance(value, str):
        value = str(value)
    scrubbed = _URL_WITH_USERINFO.sub(REDACTED, value)
    scrubbed = _URL_WITH_QUERY.sub(r'\1?' + REDACTED, scrubbed)
    scrubbed = _BEARER.sub(REDACTED, scrubbed)
    scrubbed = _JWT.sub(REDACTED, scrubbed)
    scrubbed = _LONG_SECRETISH.sub(REDACTED, scrubbed)
    if len(scrubbed) > MAX_VALUE_LEN:
        scrubbed = scrubbed[:MAX_VALUE_LEN] + '...[truncated]'
    return scrubbed


def redact_attributes(attributes: Mapping[str, Any] | None) -> dict[str, Any]:
    """Redact secret-keyed values, drop body-keyed values, scrub and bound the rest."""
    if not attributes:
        return {}
    out: dict[str, Any] = {}
    for key, value in attributes.items():
        if not isinstance(key, str) or key == '':
            continue
        lowered = key.lower()
        if any(marker in lowered for marker in _BODY_KEY_MARKERS):
            continue
        if any(marker in lowered for marker in _SECRET_KEY_MARKERS):
            out[key] = REDACTED
            continue
        if not isinstance(value, (str, int, float, bool)):
            continue
        out[key] = redact_value(value)
    return out


# -- bounded code policy ------------------------------------------------------
# `error.type` is a *code* field, not a text field: it carries a short identifier a
# dashboard can group by. Two callers reach it — a reason code passed to
# `record_error(code)` and an exception class `__name__` — and both are caller-defined
# strings. Neither is scrubbed into shape: a value that is not already identifier-shaped
# and within the bound is not partially redacted and emitted, it is replaced wholesale by
# the generic code. Scrubbing arbitrary text and shipping the remainder cannot be shown
# safe (an unshaped secret like `hunter2pw` survives every regex); refusing anything that
# is not a bounded identifier can.
#
# This gates SHAPE, not provenance: an identifier-shaped value within the bound is
# emitted verbatim, so a caller who chooses its own codes or class names controls up to
# MAX_CODE_LEN identifier characters of this field. Those identifiers MUST NOT carry
# secrets — see the `error.type` note in `Observability.span`.
GENERIC_ERROR_CODE = 'error'
MAX_CODE_LEN = 64
# Reason codes are snake_case, sometimes dotted or hyphenated (`gate.retry_exhausted`).
_CODE_SHAPE = re.compile(r'\A[A-Za-z_][A-Za-z0-9_.-]*\Z')
# A class name is a plain identifier; a dynamic type can still put anything in `__name__`.
_TYPE_NAME_SHAPE = re.compile(r'\A[A-Za-z_][A-Za-z0-9_.]*\Z')


def _gate_code(value: Any, shape: re.Pattern[str]) -> str:
    """Emit `value` only if it is an identifier of the given shape, bounded, and not
    credential-shaped under the same policy every attribute is held to."""
    try:
        if not isinstance(value, str):
            return GENERIC_ERROR_CODE
        if not value or len(value) > MAX_CODE_LEN:
            return GENERIC_ERROR_CODE
        if not shape.match(value):
            return GENERIC_ERROR_CODE
        # Identifier shape admits hyphenated credential text (`sk-live-…`). Anything the
        # existing scrubber would touch is not emitted in redacted form — it is refused,
        # so no fragment of a token reaches the wire.
        if redact_value(value) != value:
            return GENERIC_ERROR_CODE
        return value
    except Exception:
        return GENERIC_ERROR_CODE


def safe_error_code(code: Any) -> str:
    """A caller-supplied reason code, or the generic code if it is not a bounded identifier."""
    return _gate_code(code, _CODE_SHAPE)


def safe_type_name(name: Any) -> str:
    """An exception class `__name__`, or the generic code if it is not a bounded identifier."""
    return _gate_code(name, _TYPE_NAME_SHAPE)


# Only these labels may reach a metric; everything else would create unbounded series.
METRIC_LABEL_ALLOWLIST = frozenset({
    'operation', 'status', 'outcome', 'reason_code', 'artifact_type', 'tier', 'queue',
})
_MAX_LABEL_LEN = 64


def bound_metric_labels(labels: Mapping[str, Any] | None) -> tuple[dict[str, str], int]:
    """Keep allowlisted labels only; return the kept labels and the number dropped."""
    if not labels:
        return {}, 0
    kept: dict[str, str] = {}
    dropped = 0
    for key, value in labels.items():
        if key not in METRIC_LABEL_ALLOWLIST or not isinstance(value, (str, int, float, bool)):
            dropped += 1
            continue
        text = str(value)
        if len(text) > _MAX_LABEL_LEN:
            dropped += 1
            continue
        kept[key] = text
    return kept, dropped
