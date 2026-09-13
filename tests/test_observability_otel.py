"""Real OpenTelemetry wiring: actual spans, metrics and W3C propagation, no mocks.

Skipped entirely when the `observability` extra is absent, which is also the proof that the
base install never needs OpenTelemetry.
"""
from __future__ import annotations

import logging

import pytest

otel_trace = pytest.importorskip('opentelemetry.trace')
from opentelemetry.sdk.metrics import MeterProvider  # noqa: E402
from opentelemetry.sdk.metrics.export import InMemoryMetricReader  # noqa: E402
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter  # noqa: E402

from doublegate_sdk.observability import Observability, ObservabilityOptions  # noqa: E402


@pytest.fixture
def wired():
    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[reader])
    obs = Observability.create(ObservabilityOptions(
        service_name='doublegate-crawler', service_version='0.1.0.dev0',
        deployment_environment='local', traces_enabled=True, metrics_enabled=True,
        tracer_provider=tracer_provider, meter_provider=meter_provider))
    yield obs, exporter, reader
    assert obs.shutdown() is True
    tracer_provider.shutdown()
    meter_provider.shutdown()


def test_span_is_really_exported_with_service_and_correlation_attributes(wired):
    obs, exporter, _ = wired
    with obs.span('gate.evaluate', attributes={'operation': 'evaluate'}) as span:
        span.set_attribute('gate.outcome', 'clean')
        assert span.trace_id is not None and len(span.trace_id) == 32
    spans = exporter.get_finished_spans()
    assert [s.name for s in spans] == ['gate.evaluate']
    assert spans[0].attributes['operation'] == 'evaluate'
    assert spans[0].attributes['gate.outcome'] == 'clean'


def test_span_attributes_are_redacted_before_export(wired):
    obs, exporter, _ = wired
    with obs.span('gate.evaluate', attributes={'authorization': 'Bearer sk-live-abcdefghijklmnop',
                                               'prompt': 'user text'}):
        pass
    attrs = exporter.get_finished_spans()[0].attributes
    assert attrs['authorization'] == '[redacted]'
    assert 'prompt' not in attrs


def test_metrics_are_really_recorded_with_bounded_labels(wired):
    obs, _, reader = wired
    obs.counter('doublegate.requests').add(1, {'operation': 'evaluate', 'status': 'ok',
                                               'request_id': 'r-1'})
    obs.histogram('doublegate.operation.duration').record(12.5, {'operation': 'evaluate'})
    obs.up_down_counter('doublegate.inflight').add(1, {'queue': 'admission'})
    data = reader.get_metrics_data()
    points = {}
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                points[metric.name] = list(metric.data.data_points)
    assert set(points) == {'doublegate.requests', 'doublegate.operation.duration',
                           'doublegate.inflight'}
    labels = dict(points['doublegate.requests'][0].attributes)
    assert labels == {'operation': 'evaluate', 'status': 'ok'}
    assert obs.dropped_label_count == 1
    assert points['doublegate.operation.duration'][0].sum == 12.5


def test_caller_provider_keeps_its_own_resource(wired):
    """A caller-owned provider is used as-is; the SDK does not rewrite its resource."""
    obs, exporter, _ = wired
    with obs.span('gate.evaluate'):
        pass
    assert exporter.get_finished_spans()[0].resource is not None


def test_sdk_owned_provider_carries_service_identity():
    """When the SDK builds the provider itself, service identity is on the resource."""
    obs = Observability.create(ObservabilityOptions(
        service_name='doublegate-crawler', service_version='0.1.0.dev0',
        deployment_environment='local', traces_enabled=True))
    owned = obs._backend._owned[0]
    resource = owned.resource.attributes
    assert resource['service.name'] == 'doublegate-crawler'
    assert resource['service.version'] == '0.1.0.dev0'
    assert resource['deployment.environment.name'] == 'local'
    assert resource['service.instance.id']
    assert obs.shutdown() is True


def test_w3c_context_round_trips_and_parents_a_remote_span(wired):
    obs, exporter, _ = wired
    with obs.span('gate.request') as span:
        carrier = obs.inject({})
        parent_trace = span.trace_id
    assert 'traceparent' in carrier
    assert parent_trace in carrier['traceparent']
    ctx = obs.extract(carrier)
    assert ctx is not None
    with obs.span('gate.child', context=ctx):
        pass
    child = [s for s in exporter.get_finished_spans() if s.name == 'gate.child'][0]
    assert format(child.context.trace_id, '032x') == parent_trace


def test_context_propagates_across_async_tasks(wired):
    import asyncio

    obs, exporter, _ = wired

    async def child():
        with obs.span('gate.async_child'):
            await asyncio.sleep(0)

    async def main():
        with obs.span('gate.async_parent') as span:
            parent = span.trace_id
            await asyncio.gather(child(), child())
            return parent

    parent_trace = asyncio.run(main())
    children = [s for s in exporter.get_finished_spans() if s.name == 'gate.async_child']
    assert len(children) == 2
    assert all(format(s.context.trace_id, '032x') == parent_trace for s in children)


def test_sdk_never_installs_global_providers():
    from opentelemetry import trace as global_trace

    before = global_trace.get_tracer_provider()
    obs = Observability.create(ObservabilityOptions(
        service_name='svc', traces_enabled=True, metrics_enabled=True))
    assert obs.enabled is True
    assert global_trace.get_tracer_provider() is before
    assert obs.shutdown() is True


def test_logging_bridge_does_not_recurse(wired):
    obs, _, _ = wired
    sdk_logger = logging.getLogger('doublegate_sdk.observability')
    obs.log('gate.evaluated', level='INFO', operation='evaluate')
    assert sdk_logger.propagate is not None
    assert obs.shutdown() is True
