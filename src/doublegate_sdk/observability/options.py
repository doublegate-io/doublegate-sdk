# Modified for standalone doublegate_sdk namespace; see NOTICE.
"""Validated observability options. Rejected here, before any telemetry exists."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import urlparse

_LOOPBACK_HOSTS = frozenset({'localhost', '127.0.0.1', '::1', '[::1]'})
_ENVIRONMENTS = frozenset({'local', 'development', 'staging', 'production'})
# Resource attributes describe the process, so anything per-request is a cardinality bug.
_FORBIDDEN_ATTR_MARKERS = ('request', 'trace', 'span', 'user', 'session', 'job', 'operation.id',
                           'claim', 'token', 'id.')
_MAX_EXTRA_ATTRS = 16
_MAX_ATTR_LEN = 256


class ObservabilityConfigError(ValueError):
    """A fixed diagnostic naming the offending option, never containing a secret value."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ObservabilityConfigError(message)


def _validate_name(value: str, option: str) -> str:
    _require(isinstance(value, str) and value.strip() != '', f'{option}: must be a non-empty string')
    _require(len(value) <= 128, f'{option}: exceeds 128 characters')
    _require(all(c.isalnum() or c in '-_./' for c in value), f'{option}: invalid characters')
    return value


def _validate_endpoint(endpoint: str, allow_remote: bool) -> str:
    parsed = urlparse(endpoint)
    _require(parsed.scheme in ('http', 'https'), 'otlp_endpoint: must be an http(s) URL')
    _require(bool(parsed.hostname), 'otlp_endpoint: missing host')
    _require(not parsed.username and not parsed.password, 'otlp_endpoint: credentials in URL are refused')
    _require(not parsed.query, 'otlp_endpoint: query strings are refused')
    if parsed.hostname not in _LOOPBACK_HOSTS:
        _require(allow_remote, 'otlp_endpoint: non-loopback endpoint requires allow_remote_endpoint=True')
    return endpoint


def _validate_extra_attributes(attrs: Mapping[str, Any] | None) -> dict[str, str]:
    if not attrs:
        return {}
    _require(len(attrs) <= _MAX_EXTRA_ATTRS,
             f'extra_resource_attributes: at most {_MAX_EXTRA_ATTRS} entries')
    validated: dict[str, str] = {}
    for key, value in attrs.items():
        _require(isinstance(key, str) and key != '', 'extra_resource_attributes: keys must be strings')
        lowered = key.lower()
        _require(not any(marker in lowered for marker in _FORBIDDEN_ATTR_MARKERS),
                 f'extra_resource_attributes: {key} looks per-request; resource attributes must be low cardinality')
        _require(isinstance(value, (str, int, float, bool)),
                 f'extra_resource_attributes: {key} must be a scalar')
        text = str(value)
        _require(len(text) <= _MAX_ATTR_LEN, f'extra_resource_attributes: {key} value exceeds {_MAX_ATTR_LEN} characters')
        validated[key] = text
    return validated


@dataclass(frozen=True)
class ObservabilityOptions:
    """Explicit, caller-owned configuration. Nothing is read from the environment.

    Monitoring (`metrics_enabled`, `traces_enabled`) is independent of `debug`: debug raises log
    verbosity only and never enables export or payload capture.
    """

    service_name: str
    service_version: str | None = None
    deployment_environment: str = 'local'
    deployment_name: str | None = None
    service_instance_id: str | None = None
    debug: bool = False
    traces_enabled: bool = False
    metrics_enabled: bool = False
    logs_bridge_enabled: bool = False
    otlp_endpoint: str | None = None
    allow_remote_endpoint: bool = False
    otlp_headers: Mapping[str, str] | None = None
    export_timeout_ms: int = 5000
    max_queue_size: int = 2048
    tracer_provider: Any = None
    meter_provider: Any = None
    logger_provider: Any = None
    extra_resource_attributes: Mapping[str, Any] | None = None
    _resource_attributes: dict[str, str] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        _validate_name(self.service_name, 'service_name')
        if self.service_version is not None:
            _validate_name(self.service_version, 'service_version')
        _require(self.deployment_environment in _ENVIRONMENTS,
                 f'deployment_environment: must be one of {sorted(_ENVIRONMENTS)}')
        if self.deployment_name is not None:
            _validate_name(self.deployment_name, 'deployment_name')
        if self.service_instance_id is not None:
            _validate_name(self.service_instance_id, 'service_instance_id')
        for flag in ('debug', 'traces_enabled', 'metrics_enabled', 'logs_bridge_enabled',
                     'allow_remote_endpoint'):
            _require(isinstance(getattr(self, flag), bool), f'{flag}: must be a bool')
        _require(isinstance(self.export_timeout_ms, int) and 100 <= self.export_timeout_ms <= 60000,
                 'export_timeout_ms: must be an int between 100 and 60000')
        _require(isinstance(self.max_queue_size, int) and 1 <= self.max_queue_size <= 65536,
                 'max_queue_size: must be an int between 1 and 65536')
        if self.otlp_endpoint is not None:
            _validate_endpoint(self.otlp_endpoint, self.allow_remote_endpoint)
        if self.otlp_headers is not None:
            _require(all(isinstance(k, str) and isinstance(v, str) for k, v in self.otlp_headers.items()),
                     'otlp_headers: keys and values must be strings')
        object.__setattr__(self, '_resource_attributes',
                           _validate_extra_attributes(self.extra_resource_attributes))

    @property
    def telemetry_requested(self) -> bool:
        """True when any signal is switched on. Debug alone is stdlib logging only."""
        return self.traces_enabled or self.metrics_enabled or self.logs_bridge_enabled

    def resource_attributes(self) -> dict[str, str]:
        attrs = {'service.name': self.service_name,
                 'deployment.environment.name': self.deployment_environment}
        if self.service_version:
            attrs['service.version'] = self.service_version
        if self.deployment_name:
            attrs['deployment.name'] = self.deployment_name
        if self.service_instance_id:
            attrs['service.instance.id'] = self.service_instance_id
        attrs.update(self._resource_attributes)
        return attrs

    def __repr__(self) -> str:  # headers never reach a diagnostic
        return (f'ObservabilityOptions(service_name={self.service_name!r}, '
                f'debug={self.debug}, traces_enabled={self.traces_enabled}, '
                f'metrics_enabled={self.metrics_enabled}, '
                f'otlp_endpoint={self.otlp_endpoint!r}, otlp_headers=[redacted])')
