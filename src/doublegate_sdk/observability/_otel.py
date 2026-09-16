# Modified for standalone doublegate_sdk namespace; see NOTICE.
"""OpenTelemetry wiring. Imported lazily, only when a signal is switched on.

Never touches the global provider registry: the SDK uses providers the caller owns, or builds a
private one and shuts that one down. An embedding host keeps its own telemetry stack intact.
"""
from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from typing import Any, Iterator, Mapping

from opentelemetry import context as otel_context
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from doublegate_sdk.observability.options import ObservabilityOptions
from doublegate_sdk.observability.redaction import (
    GENERIC_ERROR_CODE,
    safe_error_code,
    safe_type_name,
)

_INSTRUMENTATION_NAME = 'doublegate-sdk'
_LOGGER = logging.getLogger('doublegate_sdk.observability')


def safe_exception_type(exc: BaseException | type | None) -> str:
    """Bounded, identifier-shaped exception type code. Never caller-composed text.

    Shape policy, not provenance: a class name that is an identifier within the bound is
    emitted verbatim, and a caller can author its own exception classes, so this field may
    carry up to 64 caller-chosen identifier characters. Exception class names MUST NOT
    encode secrets. Anything unshaped, over-long or credential-shaped collapses to the
    generic code — `str(exc)` and the traceback are never read on this path.
    """
    try:
        cls = exc if isinstance(exc, type) else type(exc)
        return safe_type_name(getattr(cls, '__name__', None))
    except Exception:
        return GENERIC_ERROR_CODE


class _Span:
    """Bounds and redacts anything a caller sets after the span opens."""

    def __init__(self, span: Any) -> None:
        self._span = span

    def set_attribute(self, key: str, value: Any) -> None:
        from doublegate_sdk.observability.redaction import redact_attributes

        try:
            for safe_key, safe_value in redact_attributes({key: value}).items():
                self._span.set_attribute(safe_key, safe_value)
        except Exception:  # recording an attribute must not break the caller's operation
            pass

    def set_attributes(self, attributes: Mapping[str, Any]) -> None:
        from doublegate_sdk.observability.redaction import redact_attributes

        try:
            for safe_key, safe_value in redact_attributes(attributes).items():
                self._span.set_attribute(safe_key, safe_value)
        except Exception:
            pass

    def record_error(self, code: str) -> None:
        """Close a span with a bounded reason code — never a message, body or locals.

        `code` is a caller-supplied *identifier*, held to the same gate as an exception
        type name: identifier-shaped, at most 64 characters, and not credential-shaped.
        Anything else — free text, a body, a bearer/`sk-` token — is not scrubbed and
        emitted in part; it collapses whole to the generic `error` code, because a
        partially scrubbed arbitrary string cannot be shown to be secret-free.
        """
        try:
            self._span.set_attribute('error.type', safe_error_code(code))
            self._span.set_status(otel_trace.Status(otel_trace.StatusCode.ERROR))
        except Exception:
            pass

    def record_exception_type(self, exc: BaseException) -> None:
        """Close an errored span with the type only.

        OTel's own exception recording is switched off at span creation, so this is the
        single controlled path. It emits a bounded, identifier-shaped type code and an
        ERROR status with no description: `str(exc)` and the traceback are never read.
        """
        try:
            code = safe_exception_type(exc)
            self._span.set_attribute('error.type', code)
            # Semantic-convention event carrying only the bounded type. `message`,
            # `stacktrace` and `escaped` are deliberately absent.
            self._span.add_event('exception', {'exception.type': code})
            self._span.set_status(otel_trace.Status(otel_trace.StatusCode.ERROR))
        except Exception:
            pass

    @property
    def trace_id(self) -> str | None:
        try:
            ctx = self._span.get_span_context()
        except Exception:
            return None
        return format(ctx.trace_id, '032x') if ctx and ctx.trace_id else None

    @property
    def span_id(self) -> str | None:
        try:
            ctx = self._span.get_span_context()
        except Exception:
            return None
        return format(ctx.span_id, '016x') if ctx and ctx.span_id else None


class OtelBackend:
    """Owns only what it created. Caller-supplied providers are used, never shut down."""

    def __init__(self, options: ObservabilityOptions) -> None:
        self._options = options
        self._owned: list[Any] = []
        self._propagator = TraceContextTextMapPropagator()
        resource = Resource.create(self._resource_attributes(options))

        self._tracer = None
        if options.traces_enabled:
            provider = options.tracer_provider
            if provider is None:
                provider = TracerProvider(resource=resource)
                self._attach_span_export(provider)
                self._owned.append(provider)
            self._tracer = provider.get_tracer(_INSTRUMENTATION_NAME)

        self._meter = None
        if options.metrics_enabled:
            provider = options.meter_provider
            if provider is None:
                readers = self._metric_readers()
                provider = MeterProvider(resource=resource, metric_readers=readers,
                                         shutdown_on_exit=False)
                self._owned.append(provider)
            self._meter = provider.get_meter(_INSTRUMENTATION_NAME)

        self._logger_provider = None
        if options.logs_bridge_enabled:
            self._logger_provider = self._build_logger_provider(options, resource)

    @staticmethod
    def _resource_attributes(options: ObservabilityOptions) -> dict[str, str]:
        attrs = options.resource_attributes()
        # An instance id keeps per-process series distinguishable without per-request cardinality.
        attrs.setdefault('service.instance.id', options.service_instance_id or str(uuid.uuid4()))
        return attrs

    def _attach_span_export(self, provider: Any) -> None:
        exporter = self._span_exporter()
        if exporter is None:
            return
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        # Bounded, non-blocking: overflow drops rather than back-pressuring the caller.
        provider.add_span_processor(BatchSpanProcessor(
            exporter, max_queue_size=self._options.max_queue_size,
            export_timeout_millis=self._options.export_timeout_ms))

    def _span_exporter(self) -> Any:
        if not self._options.otlp_endpoint:
            return None
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        return OTLPSpanExporter(endpoint=f'{self._options.otlp_endpoint}/v1/traces',
                                headers=dict(self._options.otlp_headers or {}),
                                timeout=max(1, self._options.export_timeout_ms // 1000))

    def _metric_readers(self) -> list[Any]:
        if not self._options.otlp_endpoint:
            return []
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

        exporter = OTLPMetricExporter(endpoint=f'{self._options.otlp_endpoint}/v1/metrics',
                                      headers=dict(self._options.otlp_headers or {}),
                                      timeout=max(1, self._options.export_timeout_ms // 1000))
        return [PeriodicExportingMetricReader(exporter)]

    def _build_logger_provider(self, options: ObservabilityOptions, resource: Any) -> Any:
        provider = options.logger_provider
        if provider is not None:
            return provider
        from opentelemetry.sdk._logs import LoggerProvider

        provider = LoggerProvider(resource=resource, shutdown_on_exit=False)
        if options.otlp_endpoint:
            from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
            from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

            provider.add_log_record_processor(BatchLogRecordProcessor(
                OTLPLogExporter(endpoint=f'{options.otlp_endpoint}/v1/logs',
                                headers=dict(options.otlp_headers or {}),
                                timeout=max(1, options.export_timeout_ms // 1000)),
                max_queue_size=options.max_queue_size,
                export_timeout_millis=options.export_timeout_ms))
        self._owned.append(provider)
        return provider

    # -- signals --------------------------------------------------------------
    @contextmanager
    def span(self, name: str, attributes: Mapping[str, Any],
             context: Any = None) -> Iterator[_Span]:
        if self._tracer is None:
            yield _Span(otel_trace.NonRecordingSpan(otel_trace.INVALID_SPAN_CONTEXT))
            return
        # `record_exception=False` and `set_status_on_exception=False` switch off OTel's
        # automatic close-path recording. Left on, `use_span.__exit__` would compose
        # `exception.message` (`str(exc)`), `exception.stacktrace` (a full traceback with
        # frame context) and a status description from text the SDK never composed —
        # contract §5 forbids all three, and no scrubber can recognise an unshaped secret
        # inside them. The controlled replacement below records the type and nothing else.
        # The exception is always re-raised, so a caller — including BaseException
        # cancellation — sees it unchanged.
        with self._tracer.start_as_current_span(
                name, context=context, attributes=dict(attributes),
                record_exception=False, set_status_on_exception=False) as span:
            wrapped = _Span(span)
            try:
                yield wrapped
            except BaseException as exc:
                wrapped.record_exception_type(exc)
                raise

    def instrument(self, kind: str, name: str) -> Any:
        if self._meter is None:
            raise RuntimeError('metrics_disabled')
        if kind == 'counter':
            return _CounterAdapter(self._meter.create_counter(name))
        if kind == 'up_down_counter':
            return _CounterAdapter(self._meter.create_up_down_counter(name))
        if kind == 'histogram':
            return _HistogramAdapter(self._meter.create_histogram(name))
        raise RuntimeError('unknown_instrument_kind')

    def inject(self, carrier: dict[str, str]) -> None:
        self._propagator.inject(carrier)

    def logging_handler(self) -> Any:
        """A stdlib handler feeding OTel logs, or None when the bridge is off."""
        if self._logger_provider is None:
            return None
        # `opentelemetry.sdk._logs.LoggingHandler` emits a DeprecationWarning in 1.44 and
        # names its own replacement: the handler in `opentelemetry-instrumentation-logging`,
        # which the `observability` extra installs. We use the supported one rather than
        # silencing the warning — silencing it would hide a real removal when it lands.
        try:
            from opentelemetry.instrumentation.logging.handler import LoggingHandler
        except ImportError:
            _LOGGER.warning(
                'observability_logs_bridge_unavailable: '
                'install doublegate-sdk[observability] for the supported logging handler')
            return None
        return LoggingHandler(logger_provider=self._logger_provider)

    def extract(self, carrier: Mapping[str, str]) -> Any:
        return self._propagator.extract(dict(carrier), context=otel_context.get_current())

    def shutdown(self, timeout_ms: int) -> bool:
        """Flush only what this backend owns, bounded. Caller providers are left alone."""
        ok = True
        for provider in self._owned:
            try:
                flush = getattr(provider, 'force_flush', None)
                if flush is not None:
                    ok = bool(flush(timeout_ms)) and ok
                provider.shutdown()
            except Exception:
                ok = False
        return ok


class _CounterAdapter:
    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def add(self, amount: float, labels: Mapping[str, str]) -> None:
        self._inner.add(amount, dict(labels))

    def record(self, amount: float, labels: Mapping[str, str]) -> None:
        self._inner.add(amount, dict(labels))


class _HistogramAdapter:
    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def record(self, amount: float, labels: Mapping[str, str]) -> None:
        self._inner.record(amount, dict(labels))

    def add(self, amount: float, labels: Mapping[str, str]) -> None:
        self._inner.record(amount, dict(labels))
