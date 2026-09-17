"""Scripted-responder tests for the HTTP/MCP gate transport.

Every test runs against a real loopback HTTP server speaking real bytes, not a
fake transport object. The responder is scripted so the parsing, the bounds and
the error mapping are exercised on the wire.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from doublegate_sdk.client import GateClient, GateError, HttpMcpTransport


def _mcp_ok(payload, request_id=1):
    return {'jsonrpc': '2.0', 'id': request_id,
            'result': {'content': [{'type': 'text', 'text': json.dumps(payload)}],
                       'structuredContent': payload, 'isError': False,
                       'resultType': 'complete'}}


class _Script:
    """One scripted HTTP/MCP responder; records what the SDK actually sent."""

    def __init__(self, reply, *, status=200, content_type='application/json'):
        self.reply, self.status, self.content_type = reply, status, content_type
        self.requests = []
        self.headers = []
        self.paths = []


def _serve(script):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(405)
            self.send_header('Content-Length', '0')
            self.end_headers()

        def do_POST(self):
            length = int(self.headers.get('Content-Length') or 0)
            raw = self.rfile.read(length)
            script.paths.append(self.path)
            script.headers.append(dict(self.headers))
            try:
                script.requests.append(json.loads(raw))
            except ValueError:
                script.requests.append(raw)
            reply = script.reply
            if callable(reply):
                reply = reply(script.requests[-1])
            body = reply if isinstance(reply, bytes) else json.dumps(reply).encode()
            self.send_response(script.status)
            self.send_header('Content-Type', script.content_type)
            self.send_header('Content-Length', str(len(body)))
            if script.status in (301, 302, 307, 308):
                self.send_header('Location', 'https://elsewhere.example/mcp')
            self.end_headers()
            self.wfile.write(body)

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
    thread.start()
    return httpd, thread


@pytest.fixture
def responder():
    started = []

    def start(reply, *, status=200, content_type='application/json'):
        script = _Script(reply, status=status, content_type=content_type)
        httpd, thread = _serve(script)
        started.append((httpd, thread))
        script.endpoint = f'http://127.0.0.1:{httpd.server_address[1]}/mcp'
        return script

    yield start
    for httpd, thread in started:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=10)
        assert not thread.is_alive()


def _transport(script, **kwargs):
    kwargs.setdefault('allow_insecure_loopback', True)
    return HttpMcpTransport(script.endpoint, **kwargs)


# ---- endpoint policy -------------------------------------------------------

def test_https_is_the_default_and_plain_http_is_refused():
    with pytest.raises(ValueError):
        HttpMcpTransport('http://gate.example/mcp')


def test_plain_http_is_refused_even_on_loopback_without_the_opt_in():
    with pytest.raises(ValueError):
        HttpMcpTransport('http://127.0.0.1:8480/mcp')


def test_the_loopback_opt_in_does_not_open_remote_plain_http():
    with pytest.raises(ValueError):
        HttpMcpTransport('http://gate.example/mcp', allow_insecure_loopback=True)


def test_https_remote_endpoint_is_accepted_without_connecting():
    transport = HttpMcpTransport('https://gate.example/mcp', token='t')
    assert transport.endpoint == 'https://gate.example/mcp'


def test_a_non_http_scheme_is_refused():
    for url in ('unix:///run/gate.sock', 'ftp://gate.example/mcp', '/mcp', ''):
        with pytest.raises(ValueError):
            HttpMcpTransport(url, allow_insecure_loopback=True)


def test_construction_makes_no_connection(responder):
    script = responder(_mcp_ok({}))
    _transport(script)
    assert script.requests == []


# ---- the wire ---------------------------------------------------------------

def test_status_sends_a_tools_call_for_the_real_tool(responder):
    script = responder(_mcp_ok({'artifact_id': 'sha256:' + 'a' * 64, 'state': 'ACTIVE'}))
    client = GateClient(_transport(script))
    result = client.status('sha256:' + 'a' * 64)
    assert result['state'] == 'ACTIVE'
    sent = script.requests[0]
    assert sent['jsonrpc'] == '2.0' and sent['method'] == 'tools/call'
    assert sent['params']['name'] == 'doublegate.status'
    assert sent['params']['arguments'] == {'artifact_id': 'sha256:' + 'a' * 64}
    assert script.paths == ['/mcp']


def test_the_token_is_sent_as_a_bearer_header(responder):
    script = responder(_mcp_ok({'artifact_id': 'sha256:' + 'b' * 64, 'state': 'ACTIVE'}))
    client = GateClient(_transport(script, token='shh'))
    client.status('sha256:' + 'b' * 64)
    assert script.headers[0]['Authorization'] == 'Bearer shh'


def test_no_authorization_header_without_a_token(responder):
    script = responder(_mcp_ok({'artifact_id': 'sha256:' + 'c' * 64, 'state': 'ACTIVE'}))
    GateClient(_transport(script)).status('sha256:' + 'c' * 64)
    assert 'Authorization' not in script.headers[0]


def test_the_token_never_appears_in_an_error(responder):
    script = responder({'jsonrpc': '2.0', 'id': 1,
                        'error': {'code': -32603, 'message': 'internal'}})
    client = GateClient(_transport(script, token='super-secret-value'))
    with pytest.raises(GateError) as caught:
        client.status('sha256:' + 'd' * 64)
    assert 'super-secret-value' not in repr(caught.value)
    assert 'internal' not in repr(caught.value)


def test_structured_content_is_required(responder):
    script = responder({'jsonrpc': '2.0', 'id': 1,
                        'result': {'content': [{'type': 'text', 'text': '{}'}]}})
    with pytest.raises(GateError) as caught:
        GateClient(_transport(script)).status('sha256:' + 'e' * 64)
    assert caught.value.kind == 'invalid_response'


def test_a_tool_level_error_flag_is_not_read_as_success(responder):
    script = responder({'jsonrpc': '2.0', 'id': 1,
                        'result': {'structuredContent': {'state': 'ACTIVE'}, 'isError': True}})
    with pytest.raises(GateError) as caught:
        GateClient(_transport(script)).status('sha256:' + 'f' * 64)
    assert caught.value.kind == 'tool_error'


def test_a_mismatched_response_id_is_refused(responder):
    script = responder(_mcp_ok({'state': 'ACTIVE'}, request_id=99))
    with pytest.raises(GateError) as caught:
        GateClient(_transport(script)).status('sha256:' + '1' * 64)
    assert caught.value.kind == 'invalid_response'


# ---- error mapping ----------------------------------------------------------

@pytest.mark.parametrize('code,kind', [
    (-32601, 'unsupported_operation'),
    (-32602, 'unsupported_operation'),
    (-32000, 'remote_error'),
    (-32002, 'remote_error'),
])
def test_jsonrpc_error_codes_map_to_stable_kinds(responder, code, kind):
    script = responder({'jsonrpc': '2.0', 'id': 1,
                        'error': {'code': code, 'message': 'leaky internal detail'}})
    with pytest.raises(GateError) as caught:
        GateClient(_transport(script)).status('sha256:' + '2' * 64)
    assert caught.value.kind == kind and caught.value.code == code


def test_unauthorized_http_status_is_its_own_kind(responder):
    script = responder({'error': 'console token required'}, status=401)
    with pytest.raises(GateError) as caught:
        GateClient(_transport(script)).status('sha256:' + '3' * 64)
    assert caught.value.kind == 'unauthorized' and caught.value.code == 401


def test_forbidden_http_status_is_its_own_kind(responder):
    script = responder({'error': 'origin refused'}, status=403)
    with pytest.raises(GateError) as caught:
        GateClient(_transport(script)).status('sha256:' + '4' * 64)
    assert caught.value.kind == 'forbidden' and caught.value.code == 403


def test_a_redirect_is_refused_and_not_followed(responder):
    script = responder({'moved': True}, status=307)
    with pytest.raises(GateError) as caught:
        GateClient(_transport(script)).status('sha256:' + '5' * 64)
    assert caught.value.kind == 'redirect_refused'
    assert len(script.requests) == 1


def test_a_non_json_body_is_an_invalid_response(responder):
    script = responder(b'<html>not json</html>', content_type='text/html')
    with pytest.raises(GateError) as caught:
        GateClient(_transport(script)).status('sha256:' + '6' * 64)
    assert caught.value.kind == 'invalid_response'


def _closed_port():
    """An ephemeral port that was bound and released: connect() is refused
    promptly, where a reserved low port can simply hang."""
    import socket as _socket
    with _socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def test_an_unreachable_endpoint_is_unavailable():
    transport = HttpMcpTransport(f'http://127.0.0.1:{_closed_port()}/mcp',
                                 allow_insecure_loopback=True, timeout=5.0)
    with pytest.raises(GateError) as caught:
        GateClient(transport).status('sha256:' + '7' * 64)
    assert caught.value.kind == 'unavailable'


def test_reads_never_report_an_unknown_outcome():
    transport = HttpMcpTransport(f'http://127.0.0.1:{_closed_port()}/mcp',
                                 allow_insecure_loopback=True, timeout=5.0)
    with pytest.raises(GateError) as caught:
        GateClient(transport).status('sha256:' + '8' * 64)
    assert caught.value.outcome_unknown is False


# ---- bounds -----------------------------------------------------------------

def test_an_oversized_response_is_refused(responder):
    script = responder(_mcp_ok({'artifact_id': 'x', 'state': 'A' * 5000}))
    with pytest.raises(GateError) as caught:
        GateClient(_transport(script, max_response_bytes=256)).status('sha256:' + '9' * 64)
    assert caught.value.kind == 'response_too_large'


def test_an_oversized_request_is_refused_before_sending(responder):
    script = responder(_mcp_ok({'results': [], 'paths': []}))
    client = GateClient(_transport(script, max_request_bytes=64, allow_writes=True))
    with pytest.raises(GateError) as caught:
        client.recall('q' * 500)
    assert caught.value.kind == 'request_too_large'
    assert script.requests == []


def test_the_request_and_response_budgets_are_separate(responder):
    """A small response cap must not trip the request guard first."""
    script = responder(_mcp_ok({'artifact_id': 'x', 'state': 'A' * 5000}))
    transport = _transport(script, max_response_bytes=256)
    assert transport.max_request_bytes != 256
    with pytest.raises(GateError) as caught:
        GateClient(transport).status('sha256:' + 'a' * 64)
    assert caught.value.kind == 'response_too_large'
    assert len(script.requests) == 1


@pytest.mark.parametrize('bad', [0, -1, float('inf'), float('nan'), 'ten', None])
def test_invalid_timeouts_are_refused(bad):
    with pytest.raises(ValueError):
        HttpMcpTransport('https://gate.example/mcp', timeout=bad)


@pytest.mark.parametrize('bad', [0, -1, 1.5, 'big', None])
def test_invalid_byte_caps_are_refused(bad):
    with pytest.raises(ValueError):
        HttpMcpTransport('https://gate.example/mcp', max_response_bytes=bad)
    with pytest.raises(ValueError):
        HttpMcpTransport('https://gate.example/mcp', max_request_bytes=bad)


# ---- writes are opt-in ------------------------------------------------------

def test_proposals_are_refused_without_the_opt_in(responder):
    script = responder(_mcp_ok({'artifact_id': 'sha256:' + 'a' * 64, 'state': 'L1_SCANNED'}))
    client = GateClient(_transport(script))
    with pytest.raises(GateError) as caught:
        client.propose('text', content_type='memory', source_uri='fixture://x')
    assert caught.value.kind == 'writes_disabled'
    assert script.requests == []


def test_a_proposal_sends_text_not_base64(responder):
    script = responder(_mcp_ok({'artifact_id': 'sha256:' + 'a' * 64, 'state': 'L1_SCANNED'}))
    client = GateClient(_transport(script, allow_writes=True))
    client.propose('hello gate', content_type='memory', source_uri='fixture://x')
    arguments = script.requests[0]['params']['arguments']
    assert arguments['content'] == 'hello gate'
    assert 'encoding' not in arguments
    assert script.requests[0]['params']['name'] == 'doublegate.remember'


def test_a_proposal_never_sends_writer_identity(responder):
    script = responder(_mcp_ok({'artifact_id': 'sha256:' + 'a' * 64, 'state': 'L1_SCANNED'}))
    client = GateClient(_transport(script, allow_writes=True))
    client.propose('hello', content_type='memory', source_uri='fixture://x')
    arguments = script.requests[0]['params']['arguments']
    assert 'writer_identity' not in arguments and 'deployment_id' not in arguments


def test_non_utf8_bytes_are_refused_rather_than_re_encoded(responder):
    script = responder(_mcp_ok({'artifact_id': 'sha256:' + 'a' * 64, 'state': 'L1_SCANNED'}))
    client = GateClient(_transport(script, allow_writes=True))
    with pytest.raises(ValueError):
        client.propose(b'\xff\xfe binary', content_type='memory', source_uri='fixture://x')
    assert script.requests == []


def test_utf8_bytes_are_accepted_and_decoded(responder):
    script = responder(_mcp_ok({'artifact_id': 'sha256:' + 'a' * 64, 'state': 'L1_SCANNED'}))
    client = GateClient(_transport(script, allow_writes=True))
    client.propose('café'.encode(), content_type='memory', source_uri='fixture://x')
    assert script.requests[0]['params']['arguments']['content'] == 'café'


def test_a_write_whose_outcome_is_unknown_says_so(responder):
    script = responder(b'truncated', content_type='application/json')
    client = GateClient(_transport(script, allow_writes=True))
    with pytest.raises(GateError) as caught:
        client.propose('hello', content_type='memory', source_uri='fixture://x')
    assert caught.value.outcome_unknown is True


def test_a_proposal_without_an_artifact_id_is_an_unknown_outcome(responder):
    script = responder(_mcp_ok({'state': 'L1_SCANNED'}))
    client = GateClient(_transport(script, allow_writes=True))
    with pytest.raises(GateError) as caught:
        client.propose('hello', content_type='memory', source_uri='fixture://x')
    assert caught.value.kind == 'invalid_response' and caught.value.outcome_unknown is True


def test_the_transport_itself_refuses_a_write_tool_when_reads_only(responder):
    """Defence in depth: not only the client method, the transport too."""
    script = responder(_mcp_ok({}))
    transport = _transport(script)
    with pytest.raises(GateError) as caught:
        transport.call('doublegate.remember', {'content': 'x', 'content_type': 'm',
                                               'source_uri': 'fixture://x'})
    assert caught.value.kind == 'writes_disabled'


def test_the_transport_refuses_a_tool_outside_the_known_catalog(responder):
    script = responder(_mcp_ok({}))
    with pytest.raises(ValueError):
        _transport(script).call('doublegate.delete', {})


# ---- argument validation ----------------------------------------------------

def test_status_requires_an_artifact_id():
    transport = HttpMcpTransport('https://gate.example/mcp')
    for bad in (None, '', 5):
        with pytest.raises((ValueError, TypeError)):
            GateClient(transport).status(bad)


def test_recall_validates_its_arguments():
    client = GateClient(HttpMcpTransport('https://gate.example/mcp'))
    for bad in ('', '   ', 5, None):
        with pytest.raises(ValueError):
            client.recall(bad)
    with pytest.raises(ValueError):
        client.recall('q', limit=0)
    with pytest.raises(ValueError):
        client.recall('q', spaces=[])
    with pytest.raises(ValueError):
        client.recall('q', include_provisional='yes')


def test_recall_maps_onto_the_real_tool_and_bounds_the_hits(responder):
    script = responder(_mcp_ok({'results': [{'artifact_id': 'sha256:' + 'a' * 64}],
                                'paths': []}))
    page = GateClient(_transport(script)).recall('basalt', limit=5)
    assert len(page['results']) == 1
    arguments = script.requests[0]['params']['arguments']
    assert script.requests[0]['params']['name'] == 'doublegate.recall'
    assert arguments['k'] == 5 and arguments['include_own_pending'] is False


def test_recall_refuses_more_hits_than_it_asked_for(responder):
    script = responder(_mcp_ok({'results': [{'artifact_id': 'a'}, {'artifact_id': 'b'}],
                                'paths': []}))
    with pytest.raises(GateError) as caught:
        GateClient(_transport(script)).recall('basalt', limit=1)
    assert caught.value.kind == 'invalid_response'


def test_pending_is_metadata_only_and_bounded(responder):
    script = responder(_mcp_ok({'pending': [{'artifact_id': 'sha256:' + 'a' * 64,
                                             'state': 'L1_SCANNED'}]}))
    rows = GateClient(_transport(script)).pending(limit=10)
    assert rows['pending'][0]['state'] == 'L1_SCANNED'
    assert script.requests[0]['params']['name'] == 'doublegate.pending'


# ---- the removed surface ----------------------------------------------------

def test_there_is_no_inventory_on_the_public_client():
    """The maintained client tier serves no inventory tool; the SDK must not
    invent one, nor keep a method that could only ever fail."""
    assert not hasattr(GateClient, 'inventory')
    assert not hasattr(GateClient, 'inventory_pages')


def test_there_is_no_unix_socket_transport():
    import doublegate_sdk.client as module
    assert not hasattr(module, 'UnixSocketTransport')


def test_the_client_module_does_not_import_socket_support():
    import doublegate_sdk.client as module
    source = open(module.__file__, encoding='utf-8').read()
    assert 'AF_UNIX' not in source


# ---- protocol negotiation ---------------------------------------------------

def test_discover_reports_the_servers_protocol_versions(responder):
    script = responder({'jsonrpc': '2.0', 'id': 1,
                        'result': {'protocolVersions': ['2026-07-28'],
                                   'serverInfo': {'name': 'doublegate', 'version': '0.1.0'},
                                   'capabilities': {'tools': {}}}})
    info = _transport(script).discover()
    assert info['protocolVersions'] == ['2026-07-28']
    assert script.requests[0]['method'] == 'server/discover'


def test_tool_names_come_from_the_server_not_from_a_guess(responder):
    script = responder({'jsonrpc': '2.0', 'id': 1,
                        'result': {'tools': [{'name': 'doublegate.status'},
                                             {'name': 'doublegate.recall'}]}})
    names = _transport(script).tool_names()
    assert names == ('doublegate.status', 'doublegate.recall')
    assert script.requests[0]['method'] == 'tools/list'


def test_the_sdk_never_claims_session_or_streaming_support():
    from doublegate_sdk.client import describe_client
    description = describe_client()
    assert description['mcp']['streaming'] is False
    assert description['mcp']['sessions'] is False
    assert description['mcp']['subset'] == 'stateless-json-request-response'


def test_no_session_header_is_sent(responder):
    script = responder(_mcp_ok({'artifact_id': 'x', 'state': 'ACTIVE'}))
    GateClient(_transport(script)).status('sha256:' + 'a' * 64)
    assert 'Mcp-Session-Id' not in script.headers[0]


def test_the_accept_header_asks_for_json_only(responder):
    script = responder(_mcp_ok({'artifact_id': 'x', 'state': 'ACTIVE'}))
    GateClient(_transport(script)).status('sha256:' + 'a' * 64)
    assert script.headers[0]['Accept'] == 'application/json'


# ---- the one-call entry point ----------------------------------------------

def test_connect_returns_a_usable_client_without_assembling_a_transport(responder):
    from doublegate_sdk.client import connect
    script = responder(_mcp_ok({'artifact_id': 'sha256:' + 'a' * 64, 'state': 'ACTIVE'}))
    client = connect(script.endpoint, allow_insecure_loopback=True)
    assert isinstance(client, GateClient)
    assert client.status('sha256:' + 'a' * 64)['state'] == 'ACTIVE'


def test_connect_makes_no_connection_of_its_own(responder):
    from doublegate_sdk.client import connect
    script = responder(_mcp_ok({}))
    connect(script.endpoint, allow_insecure_loopback=True)
    assert script.requests == []


def test_connect_passes_the_token_through(responder):
    from doublegate_sdk.client import connect
    script = responder(_mcp_ok({'artifact_id': 'x', 'state': 'ACTIVE'}))
    connect(script.endpoint, token='shh', allow_insecure_loopback=True).status('sha256:' + 'a' * 64)
    assert script.headers[0]['Authorization'] == 'Bearer shh'


def test_connect_keeps_writes_off_by_default(responder):
    from doublegate_sdk.client import connect
    script = responder(_mcp_ok({'artifact_id': 'x', 'state': 'L1_SCANNED'}))
    client = connect(script.endpoint, allow_insecure_loopback=True)
    with pytest.raises(GateError) as caught:
        client.propose('x', content_type='memory', source_uri='fixture://x')
    assert caught.value.kind == 'writes_disabled'


def test_connect_honours_the_write_opt_in(responder):
    from doublegate_sdk.client import connect
    script = responder(_mcp_ok({'artifact_id': 'sha256:' + 'a' * 64, 'state': 'L1_SCANNED'}))
    client = connect(script.endpoint, allow_insecure_loopback=True, allow_writes=True)
    assert client.propose('x', content_type='memory', source_uri='fixture://x')['state']


def test_connect_refuses_remote_plain_http():
    from doublegate_sdk.client import connect
    with pytest.raises(ValueError):
        connect('http://gate.example/mcp')


def test_connect_exposes_the_transport_for_negotiation(responder):
    from doublegate_sdk.client import connect
    script = responder({'jsonrpc': '2.0', 'id': 1,
                        'result': {'tools': [{'name': 'doublegate.status'}]}})
    client = connect(script.endpoint, allow_insecure_loopback=True)
    assert client.transport.tool_names() == ('doublegate.status',)


def test_the_package_exports_the_entry_point():
    import doublegate_sdk
    assert doublegate_sdk.connect is not None
