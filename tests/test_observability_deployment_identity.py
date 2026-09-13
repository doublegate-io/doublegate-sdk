"""Deployment identity: environment is a class, not an identifier.

`deployment_environment` is a four-value enum (`local`/`development`/`staging`/`production`),
so it cannot name *which* deployment a series came from. `deployment_name` carries that,
as an OTel resource attribute — never as a metric label.
"""
from __future__ import annotations

import pytest

from doublegate_sdk.observability import (
    ObservabilityConfigError,
    ObservabilityOptions,
    bound_metric_labels,
)


def test_deployment_name_is_a_separate_resource_attribute():
    opts = ObservabilityOptions(service_name='doublegate-org', deployment_environment='production',
                                deployment_name='eu-west-1a')
    attrs = opts.resource_attributes()
    assert attrs['deployment.environment.name'] == 'production'
    assert attrs['deployment.name'] == 'eu-west-1a'


def test_deployment_name_is_absent_when_not_set():
    attrs = ObservabilityOptions(service_name='svc').resource_attributes()
    assert 'deployment.name' not in attrs


def test_deployment_name_is_charset_validated():
    with pytest.raises(ObservabilityConfigError) as exc:
        ObservabilityOptions(service_name='svc', deployment_name='eu west 1a')
    assert 'deployment_name' in str(exc.value)


def test_service_instance_id_stays_a_distinct_per_process_field():
    """Instance id identifies a process; deployment name identifies where it runs."""
    opts = ObservabilityOptions(service_name='svc', deployment_name='eu-west-1a',
                                service_instance_id='org-7')
    attrs = opts.resource_attributes()
    assert attrs['service.instance.id'] == 'org-7'
    assert attrs['deployment.name'] == 'eu-west-1a'


def test_environment_enum_still_refuses_a_deployment_identifier():
    with pytest.raises(ObservabilityConfigError):
        ObservabilityOptions(service_name='svc', deployment_environment='eu-west-1a')


def test_deployment_and_tenant_are_not_metric_labels():
    """Per the gate handoff: deployment/tenant/source must not create unbounded series."""
    kept, dropped = bound_metric_labels({'operation': 'evaluate', 'deployment': 'eu-west-1a',
                                         'scope': 'tenant-42', 'source': 'sedar'})
    assert kept == {'operation': 'evaluate'}
    assert dropped == 3


def test_remote_endpoint_is_reachable_with_an_explicit_opt_in():
    """A central collector is supported — it just cannot happen by accident."""
    opts = ObservabilityOptions(service_name='svc', otlp_endpoint='https://collector.internal:4318',
                                allow_remote_endpoint=True)
    assert opts.otlp_endpoint == 'https://collector.internal:4318'
