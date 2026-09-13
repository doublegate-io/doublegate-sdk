"""All three OTLP signals on a real loopback wire, decoded from what actually arrived.

Traces, metrics and logs each get their own protobuf assertion against a stdlib HTTP server
bound to 127.0.0.1 — not a mock exporter. The leakage assertions read the raw posted bytes,
so a secret that survives any layer of the pipeline fails the test.
"""
from __future__ import annotations

import gzip
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

pytest.importorskip('opentelemetry.sdk.trace')
pytest.importorskip('opentelemetry.exporter.otlp.proto.http.trace_exporter')
pytest.importorskip('opentelemetry.instrumentation.logging.handler')

from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (  # noqa: E402
    ExportLogsServiceRequest,
)
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (  # noqa: E402
    ExportMetricsServiceRequest,
)
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (  # noqa: E402
    ExportTraceServiceRequest,
)

from doublegate_sdk.observability import Observability, ObservabilityOptions  # noqa: E402

# A token-shaped string the SDK must never let through, in each of the shapes §5 names.
TOKEN = 'sk-live-' + 'A1b2C3d4' * 6
JWT = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r'
BODY = 'the artifact document body that must never be exported'


class _Collector(BaseHTTPRequestHandler):
    """Records every OTLP POST per signal path."""

    received: dict[str, list[bytes]] = {}

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        if self.headers.get('Content-Encoding') == 'gzip':
            body = gzip.decompress(body)
        type(self).received.setdefault(self.path, []).append(body)
        self.send_response(200)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def log_message(self, *args):
        return


@pytest.fixture
def collector():
    _Collector.received = {}
    server = HTTPServer(('127.0.0.1', 0), _Collector)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}', _Collector
    server.shutdown()
    server.server_close()


def _all_bytes(sink) -> bytes:
    return b''.join(b for parts in sink.received.values() for b in parts)


def _resource_of(attrs) -> dict:
    return {kv.key: kv.value.string_value for kv in attrs}


def test_all_three_signals_reach_the_wire_in_one_run(collector):
    endpoint, sink = collector
    obs = Observability.create(ObservabilityOptions(
        service_name='doublegate-org', service_version='0.1.0.dev0',
        deployment_environment='local', deployment_name='eu-west-1a',
        traces_enabled=True, metrics_enabled=True, logs_bridge_enabled=True,
        otlp_endpoint=endpoint, export_timeout_ms=5000))
    caller_log = logging.getLogger('doublegate.gate.wire')
    caller_log.setLevel(logging.INFO)
    assert obs.attach_logging_bridge(caller_log) is True

    with obs.span('gate.evaluate', attributes={'operation': 'evaluate'}):
        obs.counter('doublegate.requests').add(1, {'operation': 'evaluate', 'status': 'ok'})
        caller_log.warning('gate_refused')
    assert obs.shutdown() is True

    assert '/v1/traces' in sink.received, 'no trace export reached the collector'
    assert '/v1/metrics' in sink.received, 'no metric export reached the collector'
    assert '/v1/logs' in sink.received, 'no log export reached the collector'

    traces = ExportTraceServiceRequest()
    traces.ParseFromString(sink.received['/v1/traces'][0])
    rs = traces.resource_spans[0]
    resource = _resource_of(rs.resource.attributes)
    assert resource['service.name'] == 'doublegate-org'
    assert resource['deployment.name'] == 'eu-west-1a'
    assert resource['deployment.environment.name'] == 'local'
    assert rs.scope_spans[0].spans[0].name == 'gate.evaluate'

    metrics = ExportMetricsServiceRequest()
    metrics.ParseFromString(sink.received['/v1/metrics'][0])
    names = {m.name for rm in metrics.resource_metrics
             for sm in rm.scope_metrics for m in sm.metrics}
    assert 'doublegate.requests' in names

    logs = ExportLogsServiceRequest()
    logs.ParseFromString(sink.received['/v1/logs'][0])
    bodies = [lr.body.string_value for rl in logs.resource_logs
              for sl in rl.scope_logs for lr in sl.log_records]
    assert 'gate_refused' in bodies


def test_a_token_in_a_bridged_caller_log_never_reaches_the_wire(collector):
    """The bridge carries text the SDK did not compose; §5 still applies to it."""
    endpoint, sink = collector
    obs = Observability.create(ObservabilityOptions(
        service_name='svc', logs_bridge_enabled=True, debug=True, otlp_endpoint=endpoint))
    caller_log = logging.getLogger('doublegate.gate.leak')
    caller_log.setLevel(logging.INFO)
    assert obs.attach_logging_bridge(caller_log) is True

    caller_log.warning('calling upstream with Bearer %s', TOKEN)
    caller_log.warning('token in a jwt: %s', JWT)
    caller_log.warning('auth url https://user:pw@collector.internal/v1?token=%s', TOKEN)
    assert obs.shutdown() is True

    payload = _all_bytes(sink)
    assert payload, 'nothing was exported, so the assertion below would be vacuous'
    assert TOKEN.encode() not in payload
    assert JWT.encode() not in payload
    assert b'[redacted]' in payload


def test_caller_log_extras_are_masked_on_the_wire(collector):
    endpoint, sink = collector
    obs = Observability.create(ObservabilityOptions(
        service_name='svc', logs_bridge_enabled=True, otlp_endpoint=endpoint))
    caller_log = logging.getLogger('doublegate.gate.extras')
    caller_log.setLevel(logging.INFO)
    assert obs.attach_logging_bridge(caller_log) is True

    caller_log.warning('gate_refused', extra={'authorization': f'Bearer {TOKEN}',
                                              'document': BODY,
                                              'reason_code': 'blocked'})
    assert obs.shutdown() is True

    payload = _all_bytes(sink)
    assert payload
    assert TOKEN.encode() not in payload
    assert BODY.encode() not in payload
    assert b'blocked' in payload, 'a safe attribute should survive redaction'


def test_a_bridged_exception_exports_the_type_but_not_the_traceback(collector):
    """A formatted traceback carries frame locals; §5 forbids emitting them."""
    endpoint, sink = collector
    obs = Observability.create(ObservabilityOptions(
        service_name='svc', logs_bridge_enabled=True, otlp_endpoint=endpoint))
    caller_log = logging.getLogger('doublegate.gate.exc')
    caller_log.setLevel(logging.INFO)
    assert obs.attach_logging_bridge(caller_log) is True

    try:
        raise ValueError(f'upstream rejected {TOKEN}')
    except ValueError:
        caller_log.exception('gate_failed')
    assert obs.shutdown() is True

    payload = _all_bytes(sink)
    assert payload
    assert TOKEN.encode() not in payload
    assert b'Traceback' not in payload
    assert b'ValueError' in payload, 'the exception type is the bounded code we do keep'


def test_otlp_headers_are_sent_but_never_appear_in_a_diagnostic(collector):
    endpoint, sink = collector
    opts = ObservabilityOptions(
        service_name='svc', traces_enabled=True, otlp_endpoint=endpoint,
        otlp_headers={'authorization': f'Bearer {TOKEN}'})
    assert TOKEN not in repr(opts)
    obs = Observability.create(opts)
    with obs.span('gate.evaluate'):
        pass
    assert obs.shutdown() is True
    # The header rides the HTTP request, never the exported payload.
    assert TOKEN.encode() not in _all_bytes(sink)


def test_the_sdk_own_diagnostics_never_reach_the_wire(collector):
    endpoint, sink = collector
    obs = Observability.create(ObservabilityOptions(
        service_name='svc', logs_bridge_enabled=True, otlp_endpoint=endpoint))
    assert obs.attach_logging_bridge(logging.getLogger()) is True
    logging.getLogger('doublegate_sdk.observability').error('exporter_failed_internal')
    logging.getLogger('opentelemetry.exporter').error('otel_internal_failure')
    assert obs.shutdown() is True
    payload = _all_bytes(sink)
    assert b'exporter_failed_internal' not in payload
    assert b'otel_internal_failure' not in payload


def test_the_bridge_is_removed_from_the_caller_logger_on_shutdown(collector):
    endpoint, _ = collector
    logger = logging.getLogger('doublegate.gate.cleanup')
    before = list(logger.handlers)
    obs = Observability.create(ObservabilityOptions(
        service_name='svc', logs_bridge_enabled=True, otlp_endpoint=endpoint))
    assert obs.attach_logging_bridge(logger) is True
    assert len(logger.handlers) == len(before) + 1
    obs.shutdown()
    assert logger.handlers == before
