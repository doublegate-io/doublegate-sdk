"""SDK client contracts; test doubles here are transport fixtures."""
import json
import socket
import threading

import pytest

from doublegate_sdk.client import GateClient, GateError, InventoryPage, UnixSocketTransport


def page(offset=0, more=False, complete=True):
    return {'page': [{'artifact_id': 'record-a', 'state': 'FUTURE_STATE'}],
            'total': 2 if more else 1, 'limit': 1, 'offset': offset,
            'has_more': more, 'next_offset': offset + 1 if more else None,
            'coverage': {'complete': complete, 'errors': [] if complete else ['active unavailable']},
            'scope': {'gate_local': True}, 'counts': {}}


class Transport:
    def __init__(self, result): self.result, self.calls = result, []
    def call(self, method, params):
        self.calls.append((method, params))
        return self.result


def test_client_is_lazy_and_status_preserves_response():
    transport = Transport({'state': 'L1B_FLAGGED'})
    client = GateClient(transport)
    assert not transport.calls
    assert client.status('abc')['state'] == 'L1B_FLAGGED'
    assert transport.calls == [('dg.status', {'artifact_id': 'abc'})]


def test_inventory_keeps_unknown_states_and_partial_coverage():
    transport = Transport(page(complete=False))
    result = GateClient(transport).inventory(limit=1, state=['FUTURE_STATE'])
    assert isinstance(result, InventoryPage)
    assert result.records[0]['state'] == 'FUTURE_STATE'
    assert result.complete is False
    assert result.coverage_errors == ('active unavailable',)
    assert transport.calls[0] == ('dg.inventory', {'limit': 1, 'offset': 0, 'state': ['FUTURE_STATE']})


@pytest.mark.parametrize('limit,offset', [(0, 0), (True, 0), (1, -1), (1, False)])
def test_invalid_pagination_never_calls_transport(limit, offset):
    transport = Transport(page())
    with pytest.raises(ValueError): GateClient(transport).inventory(limit=limit, offset=offset)
    assert not transport.calls


def test_no_unrecognized_inventory_parameter_forwarding():
    transport = Transport(page())
    with pytest.raises(ValueError): GateClient(transport).inventory(principal='admin')
    assert not transport.calls


def test_non_progressing_page_fails_not_loops():
    transport = Transport(page(more=True))
    pages = GateClient(transport).inventory_pages(limit=1, max_pages=3)
    next(pages)
    with pytest.raises(GateError, match='invalid_response'): next(pages)


def test_page_budget_exhaustion_is_explicit():
    pages = GateClient(Transport(page(more=True))).inventory_pages(limit=1, max_pages=1)
    next(pages)
    with pytest.raises(GateError, match='page_limit'): next(pages)


def exchange(tmp_path, responder, max_bytes=65536, operation=None, allow_proposals=False):
    path = tmp_path / 'rpc.sock'
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path)); server.listen(1)
    errors = []
    def serve():
        try:
            connection, _ = server.accept()
            with connection:
                request = json.loads(connection.makefile('rb').readline())
                connection.sendall(responder(request))
        except Exception as exc: errors.append(exc)
        finally: server.close()
    thread = threading.Thread(target=serve, daemon=True); thread.start()
    try:
        options = {'allow_proposals': True} if allow_proposals else {}
        client = GateClient(UnixSocketTransport(path, timeout=2, max_response_bytes=max_bytes, **options))
        return operation(client) if operation else client.status()
    finally:
        thread.join(timeout=3)
        assert not thread.is_alive()
        assert not errors


def test_actual_socket_response(tmp_path):
    result = exchange(tmp_path, lambda r: json.dumps({'jsonrpc': '2.0', 'id': r['id'], 'result': {'role': 'client'}}).encode()+b'\n')
    assert result == {'role': 'client'}


def test_unsupported_operation_is_not_empty_success(tmp_path):
    def respond(r):
        return json.dumps({'jsonrpc': '2.0', 'id': r['id'], 'error': {'code': -32601, 'message': 'secret-text'}}).encode()+b'\n'
    with pytest.raises(GateError) as err: exchange(tmp_path, respond)
    assert err.value.kind == 'unsupported_operation'
    assert err.value.code == -32601
    assert 'secret-text' not in str(err.value)


@pytest.mark.parametrize('response', [b'garbage\n', b'{"jsonrpc":"2.0","id":999,"result":{}}\n', b'{"jsonrpc":"2.0","id":1,"result":[],"error":{}}\n'])
def test_malformed_response_refused(tmp_path, response):
    with pytest.raises(GateError, match='invalid_response'): exchange(tmp_path, lambda _: response)


def test_oversized_response_refused(tmp_path):
    with pytest.raises(GateError, match='response_too_large'):
        exchange(tmp_path, lambda _: b'x'*100+b'\n', max_bytes=32)


def test_unavailable_does_not_return_empty(tmp_path):
    with pytest.raises(GateError, match='unavailable'):
        GateClient(UnixSocketTransport(tmp_path/'absent')).status()


def test_propose_preserves_bytes_and_never_supplies_writer_identity():
    import base64
    transport = Transport({'artifact_id': 'a', 'state': 'L1B_FLAGGED'})
    result = GateClient(transport).propose(b'raw\x00\xff', content_type='imported_document',
                                         trust_class='T-4', source_uri='fixture://bytes')
    method, params = transport.calls[0]
    assert method == 'dg.ingest'
    assert base64.b64decode(params['content']) == b'raw\x00\xff'
    assert params['encoding'] == 'base64'
    assert 'writer_identity' not in params and 'deployment_id' not in params
    assert result['state'] == 'L1B_FLAGGED'


def test_proposal_transport_requires_explicit_opt_in(tmp_path):
    client = GateClient(UnixSocketTransport(tmp_path/'absent'))
    with pytest.raises(GateError, match='writes_disabled'):
        client.propose('new fact', content_type='memory', trust_class='T-4', source_uri='fixture://x')


def test_recall_excludes_pending_and_provisional_by_default():
    transport = Transport({'hits': [], 'paths': {'keyword': True}})
    assert GateClient(transport).recall('knowledge')['hits'] == []
    assert transport.calls == [('dg.recall', {'query': 'knowledge', 'k': 10,
                                            'include_own_pending': False, 'include_provisional': False})]


def test_recall_options_are_explicit():
    transport = Transport({'hits': [{'artifact_id': 'a', 'provisional': True}], 'paths': {}})
    result = GateClient(transport).recall('knowledge', limit=2, spaces=['team'], include_provisional=True)
    assert result['hits'][0]['provisional'] is True
    assert transport.calls[0][1]['spaces'] == ['team']


def test_lost_proposal_response_has_unknown_outcome_without_retry(tmp_path):
    with pytest.raises(GateError) as error:
        exchange(tmp_path, lambda request: b'', allow_proposals=True,
                 operation=lambda client: client.propose('new fact', content_type='memory',
                                                         trust_class='T-4', source_uri='fixture://x'))
    assert error.value.outcome_unknown is True


def test_successful_proposal_uses_real_socket(tmp_path):
    result = exchange(tmp_path, lambda r: json.dumps({'jsonrpc': '2.0', 'id': r['id'],
                      'result': {'artifact_id': 'a', 'state': 'L1_SCANNED'}}).encode()+b'\n',
                      allow_proposals=True, operation=lambda c: c.propose('fact', content_type='memory',
                      trust_class='T-4', source_uri='fixture://x'))
    assert result['artifact_id'] == 'a'


@pytest.mark.parametrize('query,limit', [('', 1), ('q', True), ('q', 0)])
def test_invalid_recall_never_calls_transport(query, limit):
    transport = Transport({'hits': []})
    with pytest.raises(ValueError): GateClient(transport).recall(query, limit=limit)
    assert not transport.calls


@pytest.mark.parametrize('timeout', [None, '10', []])
def test_invalid_timeout_has_a_consistent_validation_error(tmp_path, timeout):
    with pytest.raises(ValueError, match='timeout'):
        UnixSocketTransport(tmp_path/'unused', timeout=timeout)


@pytest.mark.parametrize('path', ['', b'byte-path'])
def test_invalid_socket_path_is_refused_at_construction(path):
    with pytest.raises(ValueError, match='path'):
        UnixSocketTransport(path)


def test_invalid_text_does_not_reach_transport():
    transport = Transport({})
    with pytest.raises(ValueError, match='invalid_utf8'):
        GateClient(transport).propose('private-prefix\ud800', content_type='memory',
                                     trust_class='T-4', source_uri='fixture://invalid')
    assert not transport.calls


def test_relative_socket_destination_does_not_change_with_cwd(tmp_path, monkeypatch):
    stop = threading.Event()
    threads, servers = [], []
    for label in ('original', 'other'):
        folder = tmp_path/label; folder.mkdir()
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(folder/'rpc.sock')); server.listen(1); server.settimeout(.1)
        servers.append(server)
        def respond(listener=server, value=label):
            while not stop.is_set():
                try:
                    connection, _ = listener.accept()
                except TimeoutError:
                    continue
                with connection:
                    connection.makefile('rb').readline()
                    connection.sendall(json.dumps({'jsonrpc': '2.0', 'id': 1,
                                                   'result': {'destination': value}}).encode()+b'\n')
                return
        thread = threading.Thread(target=respond, daemon=True); thread.start(); threads.append(thread)
    try:
        monkeypatch.chdir(tmp_path/'original')
        client = GateClient(UnixSocketTransport('rpc.sock'))
        monkeypatch.chdir(tmp_path/'other')
        assert client.status()['destination'] == 'original'
    finally:
        stop.set()
        for thread in threads:
            thread.join(3)
            assert not thread.is_alive()
        for server in servers: server.close()


def test_duplicate_response_keys_are_not_silently_overwritten(tmp_path):
    raw = b'{"jsonrpc":"2.0","id":999,"id":1,"result":{}}\n'
    with pytest.raises(GateError, match='invalid_response'):
        exchange(tmp_path, lambda _: raw)


@pytest.mark.parametrize('changes', [
    {'total': 2},
    {'total': 1, 'has_more': True, 'next_offset': 1},
    {'coverage': {'complete': True, 'errors': ['unavailable']}},
])
def test_contradictory_inventory_coverage_is_rejected(changes):
    data = page()
    data.update(changes)
    with pytest.raises(GateError, match='invalid_response'):
        GateClient(Transport(data)).inventory(limit=1)


@pytest.mark.parametrize('writing', [False, True])
def test_slow_response_cannot_reset_request_deadline(tmp_path, writing):
    path = tmp_path / 'slow.sock'
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path)); server.listen(1); server.settimeout(3)
    stop = threading.Event()
    def slow_peer():
        try:
            connection, _ = server.accept()
            with connection:
                connection.makefile('rb').readline()
                for _ in range(60):
                    if stop.wait(.01): return
                    connection.sendall(b' ')
                connection.sendall(b'{"jsonrpc":"2.0","id":1,"result":{"artifact_id":"a","state":"L1_SCANNED"}}\n')
        except (BrokenPipeError, ConnectionResetError):
            pass  # Expected when the tested client expires its deadline.
        finally:
            server.close()
    thread = threading.Thread(target=slow_peer, daemon=True); thread.start()
    client = GateClient(UnixSocketTransport(path, timeout=.15, allow_proposals=writing))
    try:
        with pytest.raises(GateError, match='timeout') as error:
            if writing:
                client.propose('fact', content_type='memory', trust_class='T-4', source_uri='fixture://slow')
            else:
                client.status()
        assert error.value.outcome_unknown is writing
    finally:
        stop.set(); thread.join(4)
        assert not thread.is_alive()
