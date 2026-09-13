"""The stdlib logging bridge: caller logs become OTel log records, without recursing."""
from __future__ import annotations

import logging

import pytest

pytest.importorskip('opentelemetry.sdk._logs')

from opentelemetry.sdk._logs import LoggerProvider  # noqa: E402
from opentelemetry.sdk._logs.export import (  # noqa: E402
    InMemoryLogRecordExporter,
    SimpleLogRecordProcessor,
)

from doublegate_sdk.observability import Observability, ObservabilityOptions  # noqa: E402


@pytest.fixture
def bridged():
    exporter = InMemoryLogRecordExporter()
    provider = LoggerProvider()
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    obs = Observability.create(ObservabilityOptions(
        service_name='svc', logs_bridge_enabled=True, logger_provider=provider))
    yield obs, exporter
    obs.shutdown()
    provider.shutdown()


def test_caller_logger_records_are_bridged(bridged):
    obs, exporter = bridged
    obs.attach_logging_bridge(logging.getLogger('doublegate.gate'))
    logging.getLogger('doublegate.gate').warning('gate_refused')
    bodies = [r.log_record.body for r in exporter.get_finished_logs()]
    assert 'gate_refused' in bodies
    obs.detach_logging_bridge()


def test_sdk_own_logger_is_never_bridged(bridged):
    """Exporting the SDK's own log lines would recurse through the exporter's logging."""
    obs, exporter = bridged
    obs.attach_logging_bridge(logging.getLogger())
    logging.getLogger('doublegate_sdk.observability').error('exporter_failed')
    bodies = [r.log_record.body for r in exporter.get_finished_logs()]
    assert 'exporter_failed' not in bodies
    obs.detach_logging_bridge()


def test_detach_removes_the_handler(bridged):
    obs, exporter = bridged
    logger = logging.getLogger('doublegate.gate.detach')
    obs.attach_logging_bridge(logger)
    obs.detach_logging_bridge()
    logger.warning('after_detach')
    bodies = [r.log_record.body for r in exporter.get_finished_logs()]
    assert 'after_detach' not in bodies


def test_bridge_is_inert_without_the_extra_or_when_disabled():
    obs = Observability.create(ObservabilityOptions(service_name='svc'))
    logger = logging.getLogger('doublegate.gate.disabled')
    before = list(logger.handlers)
    obs.attach_logging_bridge(logger)
    assert logger.handlers == before
    obs.detach_logging_bridge()
