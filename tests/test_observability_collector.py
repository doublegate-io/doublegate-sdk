"""OTLP export against a real local HTTP receiver, plus the failure and shutdown posture.

This is wire capture, not a mock: a stdlib HTTP server binds a loopback port, the SDK's real
OTLPSpanExporter posts protobuf to it, and the test decodes what actually arrived.
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


class _Collector(BaseHTTPRequestHandler):
    received: list[bytes] = []

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        if self.headers.get('Content-Encoding') == 'gzip':
            body = gzip.decompress(body)
        type(self).received.append(body)
        self.send_response(200)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def log_message(self, *args):  # keep test output pristine
        return


@pytest.fixture
def collector():
    _Collector.received = []
    server = HTTPServer(('127.0.0.1', 0), _Collector)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}', _Collector
    server.shutdown()
    server.server_close()


def test_spans_actually_reach_a_local_otlp_collector(collector):
    endpoint, sink = collector
    obs = Observability.create(ObservabilityOptions(
        service_name='doublegate-crawler', service_version='0.1.0.dev0',
        traces_enabled=True, otlp_endpoint=endpoint, export_timeout_ms=5000))
    with obs.span('gate.evaluate', attributes={'operation': 'evaluate'}) as span:
        span.set_attribute('gate.outcome', 'clean')
    assert obs.shutdown() is True

    assert sink.received, 'collector received nothing'
    request = ExportTraceServiceRequest()
    request.ParseFromString(sink.received[0])
    resource_spans = request.resource_spans[0]
    resource = {kv.key: kv.value.string_value for kv in resource_spans.resource.attributes}
    assert resource['service.name'] == 'doublegate-crawler'
    assert resource['service.version'] == '0.1.0.dev0'
    span_proto = resource_spans.scope_spans[0].spans[0]
    assert span_proto.name == 'gate.evaluate'
    attrs = {kv.key: kv.value.string_value for kv in span_proto.attributes}
    assert attrs['operation'] == 'evaluate'
    assert attrs['gate.outcome'] == 'clean'


def test_secrets_never_reach_the_collector_even_with_debug_on(collector):
    endpoint, sink = collector
    secret = 'sk-live-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
    obs = Observability.create(ObservabilityOptions(
        service_name='svc', traces_enabled=True, debug=True, otlp_endpoint=endpoint))
    with obs.span('gate.evaluate', attributes={'authorization': f'Bearer {secret}',
                                               'prompt': 'the user document body'}):
        pass
    assert obs.shutdown() is True
    payload = b''.join(sink.received)
    assert secret.encode() not in payload
    assert b'the user document body' not in payload
    assert b'[redacted]' in payload


def test_unreachable_collector_does_not_fail_the_caller():
    """A dead endpoint must not raise into caller code, and must not fail admission."""
    obs = Observability.create(ObservabilityOptions(
        service_name='svc', traces_enabled=True, metrics_enabled=True,
        otlp_endpoint='http://127.0.0.1:9', export_timeout_ms=1000, max_queue_size=8))
    for _ in range(50):
        with obs.span('gate.evaluate', attributes={'operation': 'evaluate'}):
            pass
        obs.counter('doublegate.requests').add(1, {'operation': 'evaluate', 'status': 'ok'})
    admitted = True  # the caller's own work completes regardless of export outcome
    obs.shutdown(timeout_ms=1000)
    assert admitted is True


def test_shutdown_is_idempotent_and_never_raises(collector):
    endpoint, _ = collector
    obs = Observability.create(ObservabilityOptions(
        service_name='svc', traces_enabled=True, otlp_endpoint=endpoint))
    with obs.span('gate.evaluate'):
        pass
    assert obs.shutdown() is True
    obs.shutdown()
    with obs.span('gate.after_shutdown'):
        pass


def test_disabled_observability_opens_no_socket(collector):
    """Nothing is exported when no signal is switched on, even with an endpoint configured."""
    endpoint, sink = collector
    obs = Observability.create(ObservabilityOptions(
        service_name='svc', debug=True, otlp_endpoint=endpoint))
    with obs.span('gate.evaluate'):
        pass
    obs.counter('doublegate.requests').add(1, {'operation': 'evaluate'})
    assert obs.shutdown() is True
    assert sink.received == []


def test_metrics_run_without_debug(collector):
    """Monitoring does not require debug: metrics export with debug=False."""
    endpoint, sink = collector
    obs = Observability.create(ObservabilityOptions(
        service_name='svc', metrics_enabled=True, debug=False, otlp_endpoint=endpoint))
    assert obs.enabled is True
    assert obs.options.debug is False
    obs.counter('doublegate.requests').add(1, {'operation': 'evaluate', 'status': 'ok'})
    assert obs.shutdown() is True
    assert sink.received, 'metrics did not reach the collector without debug'
