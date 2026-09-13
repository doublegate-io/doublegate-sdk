"""BLOCKER reproducer: caller exception text escapes via the OTel exception event.

Contract §5 says text the SDK did not compose never reaches an exporter. The
logging bridge enforces this (`_RedactBridgedRecord`). The TRACE path does not:
`Observability.span` hands the caller's exception to the OTel span's `__exit__`,
which calls `Span.record_exception(...)` and `set_status(..., description=str(exc))`.
`record_exception` builds, verbatim and unredacted:

    exception.type        type name                (bounded - fine)
    exception.message     str(exception)           <-- caller text
    exception.stacktrace  full formatted traceback <-- caller text + frame context
    exception.escaped

Nothing in the SDK filters these, so a credential or body that appears in an
exception message is exported. `_Span.record_error()` is the bounded path the
SDK offers, but it is not what the automatic span-close uses.

RED against the current tree. Run:
    .venv/bin/python -m pytest tests/test_observability_exception_leak.py -q
"""
from __future__ import annotations

import gzip
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

pytest.importorskip('opentelemetry.sdk.trace')
pytest.importorskip('opentelemetry.exporter.otlp.proto.http.trace_exporter')

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (  # noqa: E402
    ExportTraceServiceRequest,
)

from doublegate_sdk.observability import Observability, ObservabilityOptions  # noqa: E402

TOKEN = 'sk-live-' + 'A1b2C3d4' * 6
BODY = 'the artifact document body that must never be exported'
SHORT_SECRET = 'hunter2pw'  # no secret-ish shape: regex scrubbing cannot save this


class _Collector(BaseHTTPRequestHandler):
    received: dict[str, list[bytes]] = {}

    def do_POST(self):  # noqa: N802
        raw = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        if self.headers.get('Content-Encoding') == 'gzip':
            raw = gzip.decompress(raw)
        type(self).received.setdefault(self.path, []).append(raw)
        self.send_response(200)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def log_message(self, *args):
        return


@pytest.fixture
def collector():
    _Collector.received = {}
    server = HTTPServer(('127.0.0.1', 0), _Collector)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{server.server_port}', _Collector
    server.shutdown()
    server.server_close()


def _raise_through_span(endpoint: str, message: str) -> None:
    obs = Observability.create(ObservabilityOptions(
        service_name='svc', traces_enabled=True,
        otlp_endpoint=endpoint, export_timeout_ms=5000))
    with pytest.raises(RuntimeError):
        with obs.span('gate.evaluate', attributes={'operation': 'evaluate'}):
            raise RuntimeError(message)
    assert obs.shutdown() is True


def _exported(sink) -> bytes:
    return b''.join(b for parts in sink.received.values() for b in parts)


def test_a_credential_in_an_exception_message_never_reaches_the_wire(collector):
    endpoint, sink = collector
    _raise_through_span(endpoint, f'upstream auth failed token={TOKEN}')
    payload = _exported(sink)
    assert payload, 'nothing exported, the assertion would be vacuous'
    assert TOKEN.encode() not in payload, 'credential leaked in the exception event'


def test_a_body_in_an_exception_message_never_reaches_the_wire(collector):
    endpoint, sink = collector
    _raise_through_span(endpoint, f'rejected: {BODY}')
    payload = _exported(sink)
    assert payload
    assert BODY.encode() not in payload, 'caller body leaked in the exception event'


def test_an_unshaped_secret_in_an_exception_message_never_reaches_the_wire(collector):
    """Regex scrubbing cannot recognise this; only dropping the field can."""
    endpoint, sink = collector
    _raise_through_span(endpoint, f'login failed pw={SHORT_SECRET}')
    payload = _exported(sink)
    assert payload
    assert SHORT_SECRET.encode() not in payload


def test_no_traceback_is_exported_on_a_span(collector):
    """A formatted traceback carries frame context; §5 forbids emitting it."""
    endpoint, sink = collector
    _raise_through_span(endpoint, 'plain failure')
    payload = _exported(sink)
    assert payload
    assert b'Traceback' not in payload
    assert b'exception.stacktrace' not in payload


def test_span_status_description_carries_no_caller_text(collector):
    endpoint, sink = collector
    _raise_through_span(endpoint, f'upstream auth failed token={TOKEN}')
    req = ExportTraceServiceRequest()
    req.ParseFromString(sink.received['/v1/traces'][0])
    for rs in req.resource_spans:
        for ss in rs.scope_spans:
            for sp in ss.spans:
                assert TOKEN not in (sp.status.message or ''), \
                    'credential leaked in the span status description'


def test_the_exception_type_is_still_recorded(collector):
    """The fix must keep the bounded signal, not blind the span entirely."""
    endpoint, sink = collector
    _raise_through_span(endpoint, f'upstream auth failed token={TOKEN}')
    req = ExportTraceServiceRequest()
    req.ParseFromString(sink.received['/v1/traces'][0])
    spans = [sp for rs in req.resource_spans for ss in rs.scope_spans for sp in ss.spans]
    assert spans, 'the span itself must still be exported'
    sp = spans[0]
    types = [kv.value.string_value
             for ev in sp.events for kv in ev.attributes if kv.key == 'exception.type']
    assert types == ['RuntimeError'] or sp.attributes, \
        'the error type must survive as a bounded code'


def test_a_caller_owned_tracer_provider_leaks_nothing_either(collector):
    """The same seam must hold when the provider is the caller's, not the SDK's."""
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    endpoint, sink = collector
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(
        OTLPSpanExporter(endpoint=f'{endpoint}/v1/traces', timeout=5)))
    obs = Observability.create(ObservabilityOptions(
        service_name='svc', traces_enabled=True, tracer_provider=provider))
    with pytest.raises(RuntimeError):
        with obs.span('gate.evaluate'):
            raise RuntimeError(f'upstream auth failed token={TOKEN} pw={SHORT_SECRET}')
    obs.shutdown()
    provider.force_flush(5000)
    payload = _exported(sink)
    assert payload, 'nothing exported, the assertion would be vacuous'
    assert TOKEN.encode() not in payload
    assert SHORT_SECRET.encode() not in payload
    assert b'exception.stacktrace' not in payload


def test_a_cancellation_baseexception_is_reraised_and_leaks_no_text(collector):
    """BaseException (cancellation/Ctrl-C) must escape unchanged and carry no text."""
    endpoint, sink = collector
    obs = Observability.create(ObservabilityOptions(
        service_name='svc', traces_enabled=True,
        otlp_endpoint=endpoint, export_timeout_ms=5000))
    with pytest.raises(KeyboardInterrupt):
        with obs.span('gate.evaluate'):
            raise KeyboardInterrupt(f'cancelled while holding {SHORT_SECRET}')
    assert obs.shutdown() is True
    payload = _exported(sink)
    assert payload
    assert SHORT_SECRET.encode() not in payload
    assert b'Traceback' not in payload


# -- BLOCKER-2: `record_error(code)` is a bounded identifier, not a text field ---------

def _record_error_through_span(endpoint: str, code) -> None:
    obs = Observability.create(ObservabilityOptions(
        service_name='svc', traces_enabled=True,
        otlp_endpoint=endpoint, export_timeout_ms=5000))
    with obs.span('gate.evaluate', attributes={'operation': 'evaluate'}) as span:
        span.record_error(code)
    assert obs.shutdown() is True


def _error_types(sink) -> list[str]:
    req = ExportTraceServiceRequest()
    req.ParseFromString(sink.received['/v1/traces'][0])
    return [kv.value.string_value
            for rs in req.resource_spans for ss in rs.scope_spans for sp in ss.spans
            for kv in sp.attributes if kv.key == 'error.type']


def test_record_error_never_puts_a_credential_on_the_wire(collector):
    """The reported escape: a bearer/`sk-` token passed as a reason code."""
    endpoint, sink = collector
    _record_error_through_span(endpoint, f'pw={SHORT_SECRET} tok={TOKEN}')
    payload = _exported(sink)
    assert payload, 'nothing exported, the assertion would be vacuous'
    assert TOKEN.encode() not in payload
    assert b'sk-live-' not in payload, 'a token fragment survived the 64-char cut'
    assert SHORT_SECRET.encode() not in payload
    assert _error_types(sink) == ['error'], 'malformed code must collapse, not be scrubbed'


def test_record_error_never_puts_a_caller_body_on_the_wire(collector):
    endpoint, sink = collector
    _record_error_through_span(endpoint, BODY)
    payload = _exported(sink)
    assert payload
    assert BODY.encode() not in payload
    assert BODY.split()[2].encode() not in payload, 'a truncated body prefix survived'
    assert _error_types(sink) == ['error']


def test_record_error_drops_unshaped_text_no_regex_could_catch(collector):
    """`pw=hunter2pw` has no secret shape: only refusing free text can stop it."""
    endpoint, sink = collector
    _record_error_through_span(endpoint, f'login failed pw={SHORT_SECRET}')
    payload = _exported(sink)
    assert payload
    assert SHORT_SECRET.encode() not in payload
    assert _error_types(sink) == ['error']


def test_record_error_drops_a_bare_token_shaped_identifier(collector):
    """An `sk-live-…` token is identifier-shaped under a hyphen-tolerant code rule;
    the credential policy, not the shape rule, must refuse it."""
    endpoint, sink = collector
    _record_error_through_span(endpoint, TOKEN)
    payload = _exported(sink)
    assert payload
    assert TOKEN.encode() not in payload
    assert b'sk-live-' not in payload
    assert _error_types(sink) == ['error']


def test_record_error_drops_an_over_long_code(collector):
    endpoint, sink = collector
    _record_error_through_span(endpoint, 'E' * 65)
    assert _error_types(sink) == ['error']


def test_record_error_drops_a_non_string_code(collector):
    endpoint, sink = collector
    _record_error_through_span(endpoint, {'secret': SHORT_SECRET})
    payload = _exported(sink)
    assert SHORT_SECRET.encode() not in payload
    assert _error_types(sink) == ['error']


@pytest.mark.parametrize('code', [
    'retry_exhausted', 'hard_constraint', 'gate.quorum', 'reviewer-unavailable',
    'HumanRequired', '_internal',
])
def test_a_valid_gate_code_is_preserved_verbatim(collector, code):
    """The gate's closed blocker vocabulary must survive the gate unchanged."""
    endpoint, sink = collector
    _record_error_through_span(endpoint, code)
    assert _error_types(sink) == [code]


def test_record_error_sets_the_error_status(collector):
    """Collapsing the code must not blind the span: ERROR status still closes it."""
    endpoint, sink = collector
    _record_error_through_span(endpoint, f'pw={SHORT_SECRET}')
    req = ExportTraceServiceRequest()
    req.ParseFromString(sink.received['/v1/traces'][0])
    spans = [sp for rs in req.resource_spans for ss in rs.scope_spans for sp in ss.spans]
    assert spans
    assert spans[0].status.code == 2, 'ERROR status must still be recorded'
    assert spans[0].status.message == '', 'a status description is never emitted'


def test_a_caller_exception_type_still_follows_the_same_policy(collector):
    """Caller exceptions keep the identifier they had; unshaped names still collapse."""
    endpoint, sink = collector
    obs = Observability.create(ObservabilityOptions(
        service_name='svc', traces_enabled=True,
        otlp_endpoint=endpoint, export_timeout_ms=5000))
    leaky = type(f'leaked {SHORT_SECRET} !!', (RuntimeError,), {})
    with pytest.raises(RuntimeError):
        with obs.span('gate.evaluate'):
            raise leaky('boom')
    assert obs.shutdown() is True
    payload = _exported(sink)
    assert payload
    assert SHORT_SECRET.encode() not in payload
    assert _error_types(sink) == ['error']


def test_a_legitimate_caller_exception_subclass_name_is_kept(collector):
    endpoint, sink = collector
    obs = Observability.create(ObservabilityOptions(
        service_name='svc', traces_enabled=True,
        otlp_endpoint=endpoint, export_timeout_ms=5000))

    class GateRejectedError(RuntimeError):
        pass

    with pytest.raises(GateRejectedError):
        with obs.span('gate.evaluate'):
            raise GateRejectedError(f'rejected token={TOKEN}')
    assert obs.shutdown() is True
    payload = _exported(sink)
    assert TOKEN.encode() not in payload
    assert _error_types(sink) == ['GateRejectedError'], \
        'a real subclass name is a load-bearing signal and must survive'
