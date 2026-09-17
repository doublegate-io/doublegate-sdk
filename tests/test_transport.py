"""The two transports: one framing, one allowlist, one deadline."""
import json
import socket
import threading

import pytest
from conftest import Recorder, rpc_error, rpc_ok  # noqa: F401 — fixtures/helpers

from doublegate_sdk.errors import GateError
from doublegate_sdk.operations import CURATION, KNOWLEDGE, READ, allows, in_scope
from doublegate_sdk.transport import HttpTransport, UnixSocketTransport, decode_response


def test_the_scopes_nest_and_an_agent_scope_holds_no_deciding_verb():
    assert in_scope(READ) < in_scope(KNOWLEDGE) < in_scope(CURATION)
    for verb in ('dg.sign', 'dg.promote', 'dg.demote', 'dg.reject', 'dg.relate', 'dg.ban', 'dg.override_tip',
                 'dg.people', 'dg.keys'):
        assert not allows(KNOWLEDGE, verb), verb
    for verb in ('dg.ingest', 'dg.ingest_bundle', 'dg.comment', 'dg.recall', 'dg.rank', 'dg.hide', 'dg.crawler_policy'):
        assert allows(KNOWLEDGE, verb), verb
    assert not allows(READ, 'dg.ingest') and not allows(CURATION, 'dg.made_up')


def test_the_socket_transport_refuses_out_of_scope_verbs_before_connecting(tmp_path):
    absent = tmp_path / 'absent.sock'
    with pytest.raises(GateError, match='writes_disabled'):
        UnixSocketTransport(absent).call('dg.ingest', {})
    with pytest.raises(GateError, match='forbidden_operation'):
        UnixSocketTransport(absent, scope=KNOWLEDGE).call('dg.sign', {})
    with pytest.raises(ValueError, match='unsupported'):
        UnixSocketTransport(absent, scope=CURATION).call('dg.nonexistent', {})
    with pytest.raises(GateError, match='unavailable'):
        UnixSocketTransport(absent).call('dg.status', {})


def test_the_socket_transport_answers_and_keeps_the_servers_refusal_code(unix_server):
    path = unix_server(rpc_ok({'role': 'client'}))
    assert UnixSocketTransport(path, timeout=2).call('dg.status', {}) == {'role': 'client'}
    path = unix_server(rpc_error(-32006, 'full', {'retry_after_ms': 250}))
    with pytest.raises(GateError) as err:
        UnixSocketTransport(path, timeout=2, scope=KNOWLEDGE).call('dg.ingest', {'content': 'x'})
    assert (err.value.kind, err.value.code, err.value.retry_after_ms, err.value.detail) == ('busy', -32006, 250, 'full')
    assert 'full' not in str(err.value)


@pytest.mark.parametrize('raw', [b'garbage\n', b'{"jsonrpc":"2.0","id":999,"result":{}}\n',
                                 b'{"jsonrpc":"2.0","id":1,"result":[],"error":{}}\n',
                                 b'{"jsonrpc":"2.0","id":999,"id":1,"result":{}}\n'])
def test_a_malformed_or_duplicate_key_reply_is_refused_not_normalised(raw):
    with pytest.raises(GateError, match='invalid_response'):
        decode_response(raw, writing=False)


def test_an_oversized_reply_is_refused(unix_server):
    path = unix_server(lambda _: b'x' * 100 + b'\n')
    with pytest.raises(GateError, match='response_too_large'):
        UnixSocketTransport(path, timeout=2, max_response_bytes=32).call('dg.status', {})


def test_a_lost_reply_after_a_write_was_sent_marks_the_outcome_unknown(unix_server):
    path = unix_server(lambda _: b'')
    with pytest.raises(GateError) as err:
        UnixSocketTransport(path, timeout=2, scope=KNOWLEDGE).call('dg.ingest', {'content': 'x'})
    assert err.value.outcome_unknown is True


def test_a_relative_socket_path_is_pinned_at_construction(tmp_path, monkeypatch, unix_server):
    (tmp_path / 'here').mkdir(); (tmp_path / 'there').mkdir()
    monkeypatch.chdir(tmp_path / 'here')
    transport = UnixSocketTransport('rpc.sock')
    monkeypatch.chdir(tmp_path / 'there')
    assert transport.path == str(tmp_path / 'here' / 'rpc.sock')


@pytest.mark.parametrize('writing', [False, True])
def test_a_slow_peer_cannot_extend_the_one_deadline(tmp_path, writing):
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
                    if stop.wait(.01):
                        return
                    connection.sendall(b' ')
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            server.close()
    thread = threading.Thread(target=slow_peer, daemon=True); thread.start()
    transport = UnixSocketTransport(path, timeout=.15, scope=KNOWLEDGE)
    try:
        with pytest.raises(GateError, match='timeout') as err:
            transport.call('dg.ingest' if writing else 'dg.status', {'content': 'x'} if writing else {})
        assert err.value.outcome_unknown is writing
    finally:
        stop.set(); thread.join(4)
        assert not thread.is_alive()


@pytest.mark.parametrize('timeout', [None, '10', [], 0, float('inf')])
def test_invalid_bounds_are_refused_at_construction(tmp_path, timeout):
    with pytest.raises(ValueError, match='timeout'):
        UnixSocketTransport(tmp_path / 'x', timeout=timeout)
    with pytest.raises(ValueError, match='scope'):
        UnixSocketTransport(tmp_path / 'x', scope='admin')


def test_with_timeout_copies_everything_but_the_deadline(tmp_path):
    t = UnixSocketTransport(tmp_path / 'x', timeout=5, scope=KNOWLEDGE)
    short = t.with_timeout(0.3)
    assert (short.path, short.scope, short.timeout) == (t.path, KNOWLEDGE, 0.3)
    h = HttpTransport('http://127.0.0.1:1', bearer='k', scope=CURATION).with_timeout(0.2)
    assert (h.base_url, h.scope, h.timeout) == ('http://127.0.0.1:1', CURATION, 0.2)


def test_the_http_transport_posts_one_message_to_rpc_with_the_bearer(http_server):
    seen = []

    def handler(method, path, headers, body):
        seen.append((method, path, headers.get('Authorization'), json.loads(body)))
        return 200, {'Content-Type': 'application/json'}, json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': {'pong': True}}).encode()
    base = http_server(handler)
    assert HttpTransport(base + '/', bearer='tok-1').call('dg.ping', {}) == {'pong': True}
    assert seen == [('POST', '/rpc', 'Bearer tok-1', {'jsonrpc': '2.0', 'id': 1, 'method': 'dg.ping', 'params': {}})]


def test_a_callable_bearer_is_asked_once_per_request_so_a_holder_can_re_mint(http_server):
    """ADR-0080 d4: an agent's assertion is short-lived; the transport asks its
    holder for the current one on every call and never caches it."""
    seen = []
    minted = iter(['first', 'second', 'third'])

    def handler(method, path, headers, body):
        seen.append(headers.get('Authorization'))
        return 200, {'Content-Type': 'application/json'}, json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': {}}).encode()
    transport = HttpTransport(http_server(handler), bearer=lambda: next(minted))
    transport.call('dg.ping', {}); transport.call('dg.ping', {})
    assert seen == ['Bearer first', 'Bearer second']
    transport.with_timeout(1).call('dg.ping', {})  # the callable travels with the copy
    assert seen[-1] == 'Bearer third'
    with pytest.raises(ValueError, match='bearer'):
        HttpTransport(http_server(handler), bearer=lambda: 'has space').call('dg.ping', {})


@pytest.mark.parametrize('status,kind,retry', [(401, 'auth', None), (403, 'scope', None), (413, 'request_too_large', None),
                                               (429, 'busy', 2000), (503, 'busy', 2000), (500, 'remote_error', None)])
def test_the_http_door_statuses_become_kinds_and_retry_after_is_kept(http_server, status, kind, retry):
    base = http_server(lambda m, p, h, b: (status, {'Retry-After': '2'} if status in (429, 503) else {}, b'{"error":"no"}'))
    with pytest.raises(GateError) as err:
        HttpTransport(base).call('dg.ping', {})
    assert (err.value.kind, err.value.retry_after_ms) == (kind, retry)
    assert 'no' not in str(err.value)


def test_a_json_rpc_refusal_rides_a_200_on_http(http_server):
    base = http_server(lambda m, p, h, b: (200, {}, json.dumps({'jsonrpc': '2.0', 'id': 1, 'error': {'code': -32601, 'message': 'x'}}).encode()))
    with pytest.raises(GateError, match='unsupported_operation'):
        HttpTransport(base).call('dg.ping', {})


def test_the_http_transport_never_opens_a_unix_socket_and_refuses_before_io(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError('AF_UNIX opened')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    with pytest.raises(GateError, match='forbidden_operation'):
        HttpTransport('http://127.0.0.1:1', scope=KNOWLEDGE).call('dg.sign', {})
    with pytest.raises(ValueError):
        HttpTransport('ftp://x')
    with pytest.raises(ValueError):
        HttpTransport('http://127.0.0.1:1', bearer='has space')


def test_an_unreachable_http_gate_is_unavailable_not_a_crash():
    probe = socket.socket(); probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]; probe.close()
    with pytest.raises(GateError, match='unavailable'):
        HttpTransport(f'http://127.0.0.1:{port}', timeout=2).call('dg.ping', {})
