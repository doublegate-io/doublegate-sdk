# Modified for standalone doublegate_sdk namespace; see NOTICE.
"""Shared observability primitive: logs, metrics and traces under one caller-owned handle.

Base install has no dependencies and every call here is inert. With the `observability` extra
installed *and* a signal switched on, calls are wired to OpenTelemetry (ADR-0062). The SDK never
installs global OTel providers: it uses the caller's, or a private one it owns and shuts down.
"""
from __future__ import annotations

import logging
import sys
import time
from contextlib import contextmanager
from typing import Any, Iterator, Mapping

from doublegate_sdk.observability.options import ObservabilityConfigError, ObservabilityOptions
from doublegate_sdk.observability.redaction import (
    METRIC_LABEL_ALLOWLIST,
    REDACTED,
    bound_metric_labels,
    redact_attributes,
    redact_value,
    safe_error_code,
    safe_type_name,
)

__all__ = [
    'METRIC_LABEL_ALLOWLIST',
    'REDACTED',
    'Observability',
    'ObservabilityConfigError',
    'ObservabilityOptions',
    'bound_metric_labels',
    'redact_attributes',
    'redact_value',
    'safe_error_code',
]

_LOGGER_NAME = 'doublegate_sdk.observability'
_LEVELS = {'DEBUG': logging.DEBUG, 'INFO': logging.INFO, 'WARNING': logging.WARNING,
           'ERROR': logging.ERROR, 'CRITICAL': logging.CRITICAL}


class _NullSpan:
    """Accepts the same calls as a real span and records nothing."""

    def set_attribute(self, key: str, value: Any) -> None:
        return None

    def set_attributes(self, attributes: Mapping[str, Any]) -> None:
        return None

    def record_error(self, code: str) -> None:
        """Mirrors the real span's bounded reason-code path; records nothing."""
        return None

    @property
    def trace_id(self) -> str | None:
        return None

    @property
    def span_id(self) -> str | None:
        return None


class _NullInstrument:
    def add(self, amount: float, labels: Mapping[str, Any] | None = None) -> None:
        return None

    def record(self, amount: float, labels: Mapping[str, Any] | None = None) -> None:
        return None


class Observability:
    """Caller-controlled debug logs, metrics and traces. Never raises from telemetry."""

    def __init__(self, options: ObservabilityOptions, backend: Any = None) -> None:
        self._options = options
        self._backend = backend
        self._logger = logging.getLogger(_LOGGER_NAME)
        self._instruments: dict[tuple[str, str], Any] = {}
        self._dropped_labels = 0
        self._bridge: list[tuple[logging.Logger, logging.Handler]] = []

    @classmethod
    def create(cls, options: ObservabilityOptions) -> 'Observability':
        """Build a handle. Telemetry wiring failure degrades to inert, it never raises."""
        if not isinstance(options, ObservabilityOptions):
            raise ObservabilityConfigError('options: must be an ObservabilityOptions')
        backend = None
        if options.telemetry_requested:
            try:
                from doublegate_sdk.observability import _otel

                backend = _otel.OtelBackend(options)
            except ImportError:
                logging.getLogger(_LOGGER_NAME).warning(
                    'observability_backend_unavailable: install doublegate-sdk[observability]')
            except Exception:  # a broken collector config must not break the caller
                logging.getLogger(_LOGGER_NAME).warning('observability_backend_failed')
        return cls(options, backend)

    @property
    def options(self) -> ObservabilityOptions:
        return self._options

    @property
    def enabled(self) -> bool:
        """True only when a real backend is wired. Debug alone does not make this True."""
        return self._backend is not None

    @property
    def dropped_label_count(self) -> int:
        return self._dropped_labels

    # -- traces ---------------------------------------------------------------
    @contextmanager
    def span(self, name: str, attributes: Mapping[str, Any] | None = None,
             context: Any = None) -> Iterator[Any]:
        """Open a span around caller code.

        A caller's exception is handed to the span and then re-raised unchanged: this
        context manager can never swallow it. Telemetry failures degrade to a null span.

        What an errored span emits, and what it does not:

        * Emitted: an ERROR status **with no description**, and a bounded `error.type`
          code — either the exception class `__name__` or the code passed to
          `span.record_error(code)`.
        * Never emitted: `str(exc)`, the traceback (`exception.stacktrace`),
          `exception.message`, `exception.escaped`, frame locals, or a status description.

        `error.type` is gated on **shape, not provenance**. A value that is
        identifier-shaped, at most 64 characters and not credential-shaped is emitted
        verbatim; anything else collapses whole to the generic code `error`. Both
        exception class names and reason codes are chosen by the caller — including
        dynamically created types — so a caller controls up to 64 identifier characters
        of this field. **Caller-defined exception class names and reason codes MUST NOT
        contain secrets.** The SDK bounds the shape and length of that identifier; it
        cannot know where the identifier came from.
        """
        safe = redact_attributes(attributes)
        started = time.monotonic()
        ctx: Any = None
        if self._backend is not None:
            try:
                ctx = self._backend.span(name, safe, context)
            except Exception:
                ctx = None
        span: Any = _NullSpan()
        if ctx is not None:
            try:
                span = ctx.__enter__()
            except Exception:
                ctx, span = None, _NullSpan()
        if ctx is None:
            try:
                yield span
            finally:
                self._debug_log_span(name, safe, started, None)
            return
        try:
            yield span
        except BaseException:
            # Close the span with the caller's exception, then re-raise it untouched.
            # Re-raising unconditionally means a suppressing __exit__ cannot swallow it.
            self._close_span(ctx, sys.exc_info())
            self._debug_log_span(name, safe, started, span)
            raise
        self._close_span(ctx, (None, None, None))
        self._debug_log_span(name, safe, started, span)

    @staticmethod
    def _close_span(ctx: Any, exc_info: tuple[Any, Any, Any]) -> None:
        try:
            ctx.__exit__(*exc_info)
        except Exception:  # a failing exporter must not become the caller's error
            pass

    def _debug_log_span(self, name: str, attributes: Mapping[str, Any],
                        started: float, span: Any) -> None:
        if not self._options.debug:
            return
        try:
            fields = dict(attributes)
            fields['duration_ms'] = round((time.monotonic() - started) * 1000, 3)
            trace_id = getattr(span, 'trace_id', None) if span is not None else None
            if trace_id:
                fields['trace_id'] = trace_id
                fields['span_id'] = getattr(span, 'span_id', None)
            self.log(name, level='DEBUG', code='span_completed', **fields)
        except Exception:  # a debug line is never worth failing a caller operation
            pass

    # -- logs -----------------------------------------------------------------
    def log(self, event: str, level: str = 'INFO', **fields: Any) -> None:
        """Structured stdlib logging. Fields are redacted; bodies are dropped, never emitted."""
        numeric = _LEVELS.get(str(level).upper(), logging.INFO)
        if numeric == logging.DEBUG and not self._options.debug:
            return
        safe = redact_attributes(fields)
        safe['event'] = event
        safe['service.name'] = self._options.service_name
        safe['deployment.environment.name'] = self._options.deployment_environment
        rendered = ' '.join(f'{k}={v}' for k, v in sorted(safe.items()))
        self._logger.log(numeric, rendered)

    # -- metrics --------------------------------------------------------------
    def counter(self, name: str) -> Any:
        return self._instrument('counter', name)

    def up_down_counter(self, name: str) -> Any:
        return self._instrument('up_down_counter', name)

    def histogram(self, name: str) -> Any:
        return self._instrument('histogram', name)

    def _instrument(self, kind: str, name: str) -> Any:
        key = (kind, name)
        if key in self._instruments:
            return self._instruments[key]
        instrument: Any = _NullInstrument()
        if self._backend is not None:
            try:
                instrument = _BoundInstrument(self, self._backend.instrument(kind, name))
            except Exception:
                instrument = _NullInstrument()
        self._instruments[key] = instrument
        return instrument

    def _note_dropped(self, count: int) -> None:
        self._dropped_labels += count

    # -- context propagation --------------------------------------------------
    def attach_logging_bridge(self, logger: logging.Logger) -> bool:
        """Route a caller's stdlib logger into OTel logs. Inert unless the bridge is wired."""
        if self._backend is None:
            return False
        try:
            handler = self._backend.logging_handler()
        except Exception:
            return False
        if handler is None:
            return False
        handler.addFilter(_ExcludeSdkLogger())
        handler.addFilter(_RedactBridgedRecord())
        logger.addHandler(handler)
        self._bridge.append((logger, handler))
        return True

    def detach_logging_bridge(self) -> None:
        """Remove every handler this instance added. Safe to call more than once."""
        while self._bridge:
            logger, handler = self._bridge.pop()
            try:
                logger.removeHandler(handler)
            except Exception:
                pass

    def inject(self, carrier: dict[str, str] | None = None) -> dict[str, str]:
        """Write W3C traceparent/tracestate into a carrier. Inert when disabled."""
        carrier = {} if carrier is None else carrier
        if self._backend is None:
            return carrier
        try:
            self._backend.inject(carrier)
        except Exception:
            pass
        return carrier

    def extract(self, carrier: Mapping[str, str] | None) -> Any:
        """Read inbound trace context. It is an untrusted correlation hint, never an identity."""
        if self._backend is None or not carrier:
            return None
        try:
            return self._backend.extract(dict(carrier))
        except Exception:
            return None

    # -- lifecycle ------------------------------------------------------------
    def shutdown(self, timeout_ms: int | None = None) -> bool:
        """Best-effort bounded flush. A False return must never fail a caller operation."""
        self.detach_logging_bridge()
        if self._backend is None:
            return True
        try:
            return bool(self._backend.shutdown(timeout_ms or self._options.export_timeout_ms))
        except Exception:
            return False


class _ExcludeSdkLogger(logging.Filter):
    """The SDK's own diagnostics must never be exported, or an export failure logs itself."""

    _EXCLUDED = ('doublegate_sdk.observability', 'opentelemetry')

    def filter(self, record: logging.LogRecord) -> bool:
        return not any(record.name.startswith(prefix) for prefix in self._EXCLUDED)


class _RedactBridgedRecord(logging.Filter):
    """Scrub a caller's record before it becomes an exported OTel log.

    Contract §5 holds at every verbosity, and the bridge is the one path where text the SDK
    never composed reaches an exporter. The rendered message is scrubbed, `extra` fields go
    through the same attribute redaction as spans, and `exc_info` is reduced to the exception
    type: a formatted traceback carries frame locals, which are never emitted.

    The `error.type` it sets goes through the same bounded-identifier gate as the trace
    path: shape and length, not provenance. A caller-defined exception class name that is
    identifier-shaped and ≤64 characters is emitted verbatim, so such names MUST NOT
    contain secrets; anything else collapses to the generic code `error`.
    """

    _STANDARD = frozenset(vars(logging.LogRecord('', 0, '', 0, '', None, None)))

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact_value(record.getMessage())
            record.args = None
            if record.exc_info:
                exc_type = record.exc_info[0]
                record.exc_info = None
                record.exc_text = None
                record.__dict__['error.type'] = safe_type_name(
                    getattr(exc_type, '__name__', None))
            extras = {k: v for k, v in vars(record).items()
                      if k not in self._STANDARD and k != 'error.type'}
            if extras:
                safe = redact_attributes(extras)
                for key in extras:
                    record.__dict__.pop(key, None)
                record.__dict__.update(safe)
        except Exception:
            # If scrubbing cannot be proven to have happened, do not export the record.
            return False
        return True


class _BoundInstrument:
    """Applies the metric label allowlist before anything reaches the backend."""

    def __init__(self, owner: Observability, inner: Any) -> None:
        self._owner = owner
        self._inner = inner

    def add(self, amount: float, labels: Mapping[str, Any] | None = None) -> None:
        self._emit('add', amount, labels)

    def record(self, amount: float, labels: Mapping[str, Any] | None = None) -> None:
        self._emit('record', amount, labels)

    def _emit(self, method: str, amount: float, labels: Mapping[str, Any] | None) -> None:
        kept, dropped = bound_metric_labels(labels)
        if dropped:
            self._owner._note_dropped(dropped)
        try:
            getattr(self._inner, method)(amount, kept)
        except Exception:
            pass


def NullObservability(service_name: str = 'doublegate-sdk') -> Observability:
    """A handle that records nothing — the default for callers that want no telemetry."""
    return Observability(ObservabilityOptions(service_name=service_name), None)
