"""A span never swallows, replaces or re-types a caller's exception.

Telemetry is an observer. If instrumenting an operation could change which exception a
caller sees, the instrumentation would be able to break the operation it is watching.
"""
from __future__ import annotations

import logging

import pytest

from doublegate_sdk.observability import Observability, ObservabilityOptions


class _CallerError(RuntimeError):
    """Something only the caller's code raises."""


def _disabled() -> Observability:
    return Observability.create(ObservabilityOptions(service_name='svc'))


def test_caller_exception_escapes_a_disabled_span():
    obs = _disabled()
    with pytest.raises(_CallerError, match='caller_failed'):
        with obs.span('gate.evaluate'):
            raise _CallerError('caller_failed')


def test_caller_exception_escapes_a_disabled_span_with_debug_on():
    obs = Observability.create(ObservabilityOptions(service_name='svc', debug=True))
    with pytest.raises(_CallerError):
        with obs.span('gate.evaluate'):
            raise _CallerError('caller_failed')


def test_keyboardinterrupt_is_not_swallowed():
    """BaseException, not just Exception: a Ctrl-C must still stop the process."""
    obs = _disabled()
    with pytest.raises(KeyboardInterrupt):
        with obs.span('gate.evaluate'):
            raise KeyboardInterrupt


def test_generatorexit_style_close_does_not_hide_the_cause():
    obs = _disabled()
    with pytest.raises(SystemExit):
        with obs.span('gate.evaluate'):
            raise SystemExit(2)


class _SwallowingCtx:
    """A pathological span context manager whose __exit__ suppresses everything."""

    def __enter__(self):
        return object()

    def __exit__(self, *exc_info):
        return True  # a real OTel span never does this; the SDK must not trust that


class _SwallowingBackend:
    def span(self, name, attributes, context=None):
        return _SwallowingCtx()


def test_a_suppressing_span_context_cannot_swallow_the_caller_exception():
    obs = Observability(ObservabilityOptions(service_name='svc'), _SwallowingBackend())
    with pytest.raises(_CallerError):
        with obs.span('gate.evaluate'):
            raise _CallerError('caller_failed')


class _ExplodingCtx:
    def __enter__(self):
        raise RuntimeError('exporter_broken_on_enter')

    def __exit__(self, *exc_info):
        return False


class _ExplodingExitCtx:
    def __enter__(self):
        return object()

    def __exit__(self, *exc_info):
        raise RuntimeError('exporter_broken_on_exit')


class _ExplodingBackend:
    def __init__(self, ctx_cls):
        self._ctx_cls = ctx_cls

    def span(self, name, attributes, context=None):
        return self._ctx_cls()


def test_backend_failure_on_enter_degrades_to_a_null_span():
    obs = Observability(ObservabilityOptions(service_name='svc'),
                        _ExplodingBackend(_ExplodingCtx))
    ran = False
    with obs.span('gate.evaluate') as span:
        span.set_attribute('gate.outcome', 'clean')
        ran = True
    assert ran is True


def test_backend_failure_on_exit_does_not_reach_the_caller():
    obs = Observability(ObservabilityOptions(service_name='svc'),
                        _ExplodingBackend(_ExplodingExitCtx))
    with obs.span('gate.evaluate'):
        pass


def test_backend_failure_on_exit_does_not_replace_the_caller_exception():
    obs = Observability(ObservabilityOptions(service_name='svc'),
                        _ExplodingBackend(_ExplodingExitCtx))
    with pytest.raises(_CallerError):
        with obs.span('gate.evaluate'):
            raise _CallerError('caller_failed')


def test_exception_message_is_not_logged_by_the_sdk_debug_line(caplog):
    """Debug adds structure, never the caller's exception text."""
    obs = Observability.create(ObservabilityOptions(service_name='svc', debug=True))
    with caplog.at_level(logging.DEBUG, logger='doublegate_sdk.observability'):
        with pytest.raises(_CallerError):
            with obs.span('gate.evaluate'):
                raise _CallerError('secret_failure_detail')
    assert 'secret_failure_detail' not in caplog.text
