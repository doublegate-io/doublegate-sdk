"""The offline description must follow executable SDK methods, not a copied API."""
import inspect
import json
import subprocess
import sys

from doublegate_sdk.client import GateClient


def test_description_matches_public_client_signatures():
    from doublegate_sdk.client import describe_client
    description = describe_client()
    assert description['scope'] == 'sdk-contract-not-server-capabilities'
    methods = {name: value for name, value in inspect.getmembers(GateClient, inspect.isfunction)
               if not name.startswith('_')}
    assert set(description['operations']) == set(methods)
    for name, method in methods.items():
        expected = [p for p in inspect.signature(method).parameters if p != 'self']
        assert [p['name'] for p in description['operations'][name]['parameters']] == expected
    assert description['operations']['propose']['tool'] == 'doublegate.remember'
    assert description['operations']['propose']['mutates'] is True
    assert description['operations']['status']['tool'] == 'doublegate.status'


def test_every_described_tool_exists_in_the_declared_catalog():
    from doublegate_sdk.client import describe_client
    description = describe_client()
    catalog = set(description['mcp']['tools'])
    for operation in description['operations'].values():
        assert operation['tool'] in catalog


def test_the_description_names_the_http_mcp_transport():
    from doublegate_sdk.client import describe_client
    description = describe_client()
    assert description['transports'] == ['http_mcp', 'caller_supplied']
    assert description['mcp']['endpoint'] == 'POST /mcp'
    assert description['follows_redirects'] is False
    assert description['automatic_retries'] is False


def test_unsupported_operations_are_named_rather_than_implied():
    from doublegate_sdk.client import describe_client
    unsupported = describe_client()['unsupported_operations']
    assert 'inventory' in unsupported
    assert 'whole_gate_status' in unsupported


def test_cli_description_is_offline(monkeypatch, capsys):
    import socket
    from doublegate_sdk.__main__ import main

    def forbidden(*args, **kwargs):
        raise AssertionError('description connected to a socket')

    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    assert main(['describe-client']) == 0
    data = json.loads(capsys.readouterr().out)
    assert data['transports'] == ['http_mcp', 'caller_supplied']
    assert data['proposals_are_admission'] is False


def test_cli_works_as_a_module():
    result = subprocess.run([sys.executable, '-m', 'doublegate_sdk', 'describe-client'],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['operations']['recall']['parameters']
