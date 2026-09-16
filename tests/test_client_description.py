"""The offline description follows the executable clients, not a copied API.

Three doors, one command: ``describe-client`` prints the ``/rpc`` and socket clients'
description (``doublegate_sdk.describe``) with the ``/mcp`` client's own description
under ``mcp_client`` (``doublegate_sdk.client``).
"""
import inspect
import json
import subprocess
import sys

from doublegate_sdk.client import GateClient
from doublegate_sdk.client import describe_client as describe_mcp_client
from doublegate_sdk.curation import CurationClient
from doublegate_sdk.describe import describe_client
from doublegate_sdk.knowledge import KnowledgeClient
from doublegate_sdk.operations import OPERATIONS


def test_every_public_method_of_both_clients_is_described_from_its_signature():
    description = describe_client()
    assert description['scope'] == 'sdk-contract-not-server-capabilities'
    for name, cls in (('knowledge', KnowledgeClient), ('curation', CurationClient)):
        methods = {n: v for n, v in inspect.getmembers(cls, inspect.isfunction) if not n.startswith('_')}
        assert set(description['clients'][name]) == set(methods)
        for method_name, method in methods.items():
            expected = [p for p in inspect.signature(method).parameters if p != 'self']
            assert [p['name'] for p in description['clients'][name][method_name]['parameters']] == expected
    assert set(description['operations']) == set(OPERATIONS)
    assert description['operations']['dg.ingest'] == {'mutates': True, 'proof': 'none', 'role': 'both', 'scope': 'knowledge'}
    assert 'dg.sign' not in description['scopes']['knowledge'] and 'dg.sign' in description['scopes']['curation']


def test_the_mcp_description_matches_the_public_mcp_client_signatures():
    description = describe_mcp_client()
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


def test_every_described_mcp_tool_exists_in_the_declared_catalog():
    description = describe_mcp_client()
    catalog = set(description['mcp']['tools'])
    for operation in description['operations'].values():
        assert operation['tool'] in catalog


def test_the_mcp_description_names_the_http_mcp_transport():
    description = describe_mcp_client()
    assert description['transports'] == ['http_mcp', 'caller_supplied']
    assert description['mcp']['endpoint'] == 'POST /mcp'
    assert description['follows_redirects'] is False
    assert description['automatic_retries'] is False


def test_unsupported_mcp_operations_are_named_rather_than_implied():
    unsupported = describe_mcp_client()['unsupported_operations']
    assert 'inventory' in unsupported
    assert 'whole_gate_status' in unsupported


def test_the_cli_description_is_offline_and_names_all_three_doors(monkeypatch, capsys):
    import socket

    from doublegate_sdk.__main__ import main

    def forbidden(*args, **kwargs):
        raise AssertionError('description connected to a socket')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    assert main(['describe-client']) == 0
    data = json.loads(capsys.readouterr().out)
    assert data['transports'] == ['unix_socket', 'http', 'caller_supplied']
    assert data['proposals_are_admission'] is False
    assert data['mcp_client']['transports'] == ['http_mcp', 'caller_supplied']
    assert data['mcp_client']['proposals_are_admission'] is False


def test_the_cli_works_as_a_module():
    result = subprocess.run([sys.executable, '-m', 'doublegate_sdk', 'describe-client'],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data['clients']['knowledge']['recall']['parameters']
    assert data['mcp_client']['operations']['recall']['parameters']
