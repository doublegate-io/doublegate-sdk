"""The offline description follows the executable clients, not a copied API."""
import inspect
import json
import subprocess
import sys

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


def test_the_cli_description_is_offline(monkeypatch, capsys):
    import socket
    from doublegate_sdk.__main__ import main

    def forbidden(*args, **kwargs):
        raise AssertionError('description connected to a socket')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    assert main(['describe-client']) == 0
    data = json.loads(capsys.readouterr().out)
    assert data['transports'] == ['unix_socket', 'http', 'caller_supplied']
    assert data['proposals_are_admission'] is False


def test_the_cli_works_as_a_module():
    result = subprocess.run([sys.executable, '-m', 'doublegate_sdk', 'describe-client'],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['clients']['knowledge']['recall']['parameters']
