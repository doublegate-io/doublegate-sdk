"""Configuration is validated before any telemetry exists, and the base install is inert."""
from __future__ import annotations

import logging

import pytest

from doublegate_sdk.observability import (
    ObservabilityConfigError,
    ObservabilityOptions,
)


def test_service_name_is_required():
    with pytest.raises(ObservabilityConfigError) as exc:
        ObservabilityOptions(service_name='')
    assert 'service_name' in str(exc.value)


def test_remote_otlp_endpoint_is_refused_unless_explicitly_allowed():
    with pytest.raises(ObservabilityConfigError) as exc:
        ObservabilityOptions(service_name='svc', otlp_endpoint='https://collector.example.com:4318')
    assert 'allow_remote_endpoint' in str(exc.value)


def test_loopback_otlp_endpoint_is_accepted():
    opts = ObservabilityOptions(service_name='svc', otlp_endpoint='http://127.0.0.1:4318')
    assert opts.otlp_endpoint == 'http://127.0.0.1:4318'


def test_export_is_disabled_by_default():
    opts = ObservabilityOptions(service_name='svc')
    assert opts.otlp_endpoint is None
    assert opts.traces_enabled is False
    assert opts.metrics_enabled is False
    assert opts.debug is False


def test_high_cardinality_resource_attribute_is_refused():
    with pytest.raises(ObservabilityConfigError):
        ObservabilityOptions(service_name='svc', extra_resource_attributes={'request.id': 'abc'})


def test_disabled_observability_has_no_side_effects(caplog):
    from doublegate_sdk.observability import Observability

    obs = Observability.create(ObservabilityOptions(service_name='svc'))
    assert obs.enabled is False
    with caplog.at_level(logging.DEBUG):
        with obs.span('gate.evaluate') as span:
            span.set_attribute('gate.outcome', 'clean')
        obs.counter('doublegate.requests').add(1, {'operation': 'evaluate'})
        obs.histogram('doublegate.operation.duration').record(1.0, {'operation': 'evaluate'})
        obs.log('gate.evaluated', level='DEBUG', code='gate_evaluated')
    assert obs.inject({}) == {}
    assert obs.shutdown() is True
