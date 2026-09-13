"""Secrets and bodies never reach a sink, at any verbosity, and labels stay bounded."""
from __future__ import annotations

import logging

from doublegate_sdk.observability import (
    REDACTED,
    Observability,
    ObservabilityOptions,
    bound_metric_labels,
    redact_attributes,
    safe_error_code,
)

SECRET = 'sk-live-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'


def test_auth_headers_cookies_and_dsns_are_redacted():
    out = redact_attributes({
        'authorization': f'Bearer {SECRET}',
        'Cookie': 'session=abc123',
        'set-cookie': 'a=b',
        'x-api-key': SECRET,
        'db.dsn': 'postgresql://user:pw@localhost:5432/db',
        'operation': 'evaluate',
    })
    assert out['authorization'] == REDACTED
    assert out['Cookie'] == REDACTED
    assert out['set-cookie'] == REDACTED
    assert out['x-api-key'] == REDACTED
    assert out['db.dsn'] == REDACTED
    assert out['operation'] == 'evaluate'
    assert SECRET not in repr(out)
    assert 'pw' not in repr(out)


def test_prompt_and_artifact_bodies_are_dropped_not_redacted():
    out = redact_attributes({'prompt': 'secret user question', 'artifact_body': 'doc',
                             'response_text': 'answer', 'gate.outcome': 'clean'})
    assert 'prompt' not in out
    assert 'artifact_body' not in out
    assert 'response_text' not in out
    assert out['gate.outcome'] == 'clean'


def test_secret_shaped_values_are_scrubbed_even_under_a_harmless_key():
    out = redact_attributes({'note': f'called with Bearer {SECRET}',
                             'jwt': 'eyJhbGciOi.eyJzdWIiOjEyMw.sig',
                             'url': 'https://api.example.com/v1/x?api_key=abc'})
    assert SECRET not in out['note']
    assert out['jwt'] == REDACTED
    assert out['url'].endswith(REDACTED)


def test_debug_does_not_weaken_redaction(caplog):
    obs = Observability.create(ObservabilityOptions(service_name='svc', debug=True))
    with caplog.at_level(logging.DEBUG, logger='doublegate_sdk.observability'):
        obs.log('gate.evaluated', level='DEBUG', authorization=f'Bearer {SECRET}',
                prompt='user text', operation='evaluate')
    text = caplog.text
    assert SECRET not in text
    assert 'user text' not in text
    assert 'operation=evaluate' in text


def test_debug_log_is_suppressed_when_debug_is_off(caplog):
    obs = Observability.create(ObservabilityOptions(service_name='svc'))
    with caplog.at_level(logging.DEBUG, logger='doublegate_sdk.observability'):
        obs.log('gate.evaluated', level='DEBUG', operation='evaluate')
        obs.log('gate.failed', level='ERROR', operation='evaluate')
    assert 'gate.evaluated' not in caplog.text
    assert 'gate.failed' in caplog.text


def test_metric_labels_outside_the_allowlist_are_dropped_and_counted():
    kept, dropped = bound_metric_labels({'operation': 'evaluate', 'status': 'ok',
                                         'request_id': 'r-1', 'trace_id': 't-1'})
    assert kept == {'operation': 'evaluate', 'status': 'ok'}
    assert dropped == 2


def test_long_label_values_are_dropped():
    kept, dropped = bound_metric_labels({'operation': 'x' * 200})
    assert kept == {}
    assert dropped == 1


# -- the bounded-code gate behind `error.type` ---------------------------------------

def test_a_valid_reason_code_passes_the_gate_unchanged():
    for code in ('retry_exhausted', 'gate.quorum', 'reviewer-unavailable', 'HumanRequired'):
        assert safe_error_code(code) == code


def test_free_text_collapses_rather_than_being_scrubbed():
    """Partial redaction of arbitrary text cannot be shown safe; refuse it instead."""
    assert safe_error_code('login failed pw=hunter2pw') == 'error'
    assert safe_error_code('the artifact body') == 'error'


def test_credential_shaped_codes_collapse_even_when_identifier_shaped():
    assert safe_error_code('sk-live-' + 'A1b2C3d4' * 6) == 'error'
    assert safe_error_code('Bearer abcdef') == 'error'


def test_over_long_empty_and_non_string_codes_collapse():
    assert safe_error_code('E' * 65) == 'error'
    assert safe_error_code('') == 'error'
    assert safe_error_code(None) == 'error'
    assert safe_error_code({'k': 'v'}) == 'error'
    assert safe_error_code('9leading_digit') == 'error'
