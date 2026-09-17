"""Regression tests for the hardened HTTP/MCP transport.

Each test below pins one finding from the blind core review
(`.tmp/blind-review/core-review.md`). Like `test_memory_client.py`, everything
that touches the wire runs against a real loopback HTTP server speaking real
bytes — a slow peer has to actually be slow for the deadline test to mean
anything.
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from doublegate_sdk.client import GateClient, GateError, HttpMcpTransport
from doublegate_sdk.errors import DoublegateError


def _envelope(payload, *, request_id=1):
    return json.dumps({'jsonrpc': '2.0', 'id': request_id,
                       'result': {'content': [{'type': 'text', 'text': json.dumps(payload)}],
                                  'structuredContent': payload, 'isError': False}}).encode()


@pytest.fixture
def serve():
    """Start a loopback server from a handler class; return its endpoint."""
    started = []

    def start(handler):
        class Quiet(ThreadingHTTPServer):
            # The deadline tests hang up mid-response on purpose; socketserver
            # would otherwise print the resulting broken pipe as a traceback.
            def handle_error(self, request, client_address):
                pass

        httpd = Quiet(('127.0.0.1', 0), handler)
        httpd.daemon_threads = True
        thread = threading.Thread(target=httpd.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
        thread.start()
        started.append((httpd, thread))
        return f'http://127.0.0.1:{httpd.server_address[1]}/mcp'

    yield start
    for httpd, thread in started:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=10)


def _drip_handler(*, body, chunk=64, delay=0.25, headers_first=True,
                  header_delay=0.0, content_type='application/json'):
    """A peer that answers correctly but slowly, one chunk per ``delay``."""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length') or 0))
            try:
                if header_delay:
                    # Drip the status line and headers themselves.
                    self.wfile.write(b'HTTP/1.1 200 OK\r\n')
                    self.wfile.flush()
                    for header in (f'Content-Type: {content_type}',
                                   f'Content-Length: {len(body)}'):
                        time.sleep(header_delay)
                        self.wfile.write(header.encode() + b'\r\n')
                        self.wfile.flush()
                    time.sleep(header_delay)
                    self.wfile.write(b'\r\n')
                    self.wfile.flush()
                elif headers_first:
                    self.send_response(200)
                    self.send_header('Content-Type', content_type)
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                for index in range(0, len(body), chunk):
                    self.wfile.write(body[index:index + chunk])
                    self.wfile.flush()
                    time.sleep(delay)
            except (BrokenPipeError, ConnectionResetError):
                pass        # the SDK hung up on its deadline; that is the point

    return Handler


# ---- F1: the deadline is total, and enforced without a worker thread --------

def test_a_dripping_body_is_cut_off_at_the_total_deadline(serve):
    """A peer answering inside every socket interval still hits the deadline."""
    endpoint = serve(_drip_handler(body=_envelope({'x': 'y' * 4000}), delay=0.25))
    transport = HttpMcpTransport(endpoint, allow_insecure_loopback=True, timeout=1.0)
    start = time.monotonic()
    with pytest.raises(GateError) as raised:
        transport.call('doublegate.recall', {'query': 'x'})
    elapsed = time.monotonic() - start
    assert raised.value.kind == 'timeout'
    assert elapsed < 3.0, f'deadline was 1.0s, call took {elapsed:.2f}s'


def test_a_dripping_body_on_a_write_reports_the_outcome_as_unknown(serve):
    endpoint = serve(_drip_handler(body=_envelope({'x': 'y' * 4000}), delay=0.25))
    transport = HttpMcpTransport(endpoint, allow_insecure_loopback=True,
                                 timeout=1.0, allow_writes=True)
    with pytest.raises(GateError) as raised:
        transport.call('doublegate.remember', {'content': 'x'})
    assert raised.value.kind == 'timeout'
    assert raised.value.outcome_unknown is True


def test_slow_dripped_headers_are_bounded_by_the_same_deadline(serve):
    """The header phase counts against the budget, not just the body."""
    endpoint = serve(_drip_handler(body=_envelope({'ok': True}), header_delay=0.6,
                                   delay=0.0))
    transport = HttpMcpTransport(endpoint, allow_insecure_loopback=True, timeout=1.0)
    start = time.monotonic()
    with pytest.raises(GateError) as raised:
        transport.call('doublegate.recall', {'query': 'x'})
    elapsed = time.monotonic() - start
    assert raised.value.kind == 'timeout'
    assert elapsed < 3.0, f'header drip ran {elapsed:.2f}s past a 1.0s deadline'


def test_a_chunked_but_prompt_body_is_reassembled_whole(serve):
    """Enforcing the deadline must not truncate a legitimate multi-recv body."""
    payload = {'results': [{'artifact_id': f'sha256:{index:064d}'} for index in range(200)]}
    endpoint = serve(_drip_handler(body=_envelope(payload), chunk=97, delay=0.0))
    client = GateClient(HttpMcpTransport(endpoint, allow_insecure_loopback=True,
                                         timeout=10.0))
    answers = client.recall('x', limit=200)
    assert answers == payload
    assert len(answers['results']) == 200


def test_the_deadline_is_enforced_without_a_background_thread(serve):
    """No worker is spawned to cancel a slow read: no orphan can outlive the call."""
    endpoint = serve(_drip_handler(body=_envelope({'x': 'y' * 4000}), delay=0.25))
    transport = HttpMcpTransport(endpoint, allow_insecure_loopback=True, timeout=0.5)
    before = {thread.ident for thread in threading.enumerate()}
    with pytest.raises(GateError):
        transport.call('doublegate.recall', {'query': 'x'})
    leaked = {thread.ident for thread in threading.enumerate()} - before
    # Any new thread here belongs to the test server accepting the connection,
    # never to the SDK: the transport module imports no threading primitive.
    import doublegate_sdk.client as client_module
    assert not hasattr(client_module, 'threading')
    assert all(thread.daemon for thread in threading.enumerate()
               if thread.ident in leaked)


# ---- F2: a deeply nested response stays inside the taxonomy ----------------

def _static_handler(body, *, status=200, content_type='application/json'):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length') or 0))
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def test_a_deeply_nested_response_is_an_invalid_response_not_a_recursionerror(serve):
    depth = 200_000                       # ~400 KB: well inside the 1 MiB bound
    body = (b'{"jsonrpc":"2.0","id":1,"result":{"structuredContent":'
            + b'[' * depth + b']' * depth + b'}}')
    assert len(body) < 1_048_576
    endpoint = serve(_static_handler(body))
    transport = HttpMcpTransport(endpoint, allow_insecure_loopback=True)
    with pytest.raises(DoublegateError) as raised:
        transport.call('doublegate.status', {'artifact_id': 'a'})
    assert isinstance(raised.value, GateError)
    assert raised.value.kind == 'invalid_response'


def test_a_deeply_nested_response_to_a_write_reports_an_unknown_outcome(serve):
    depth = 200_000
    body = (b'{"jsonrpc":"2.0","id":1,"result":{"structuredContent":'
            + b'[' * depth + b']' * depth + b'}}')
    endpoint = serve(_static_handler(body))
    transport = HttpMcpTransport(endpoint, allow_insecure_loopback=True,
                                 allow_writes=True)
    with pytest.raises(GateError) as raised:
        transport.call('doublegate.remember', {'content': 'x'})
    assert raised.value.kind == 'invalid_response'
    assert raised.value.outcome_unknown is True


# ---- F3: credentials in the endpoint URL are refused ----------------------

@pytest.mark.parametrize('url', [
    'https://alice:s3cret@gate.example/mcp',
    'https://alice@gate.example/mcp',
    'https://:s3cret@gate.example/mcp',
    'https://@gate.example/mcp',            # empty username is still userinfo
])
def test_userinfo_in_the_endpoint_is_refused(url):
    with pytest.raises(ValueError) as raised:
        HttpMcpTransport(url)
    assert 'credentials' in str(raised.value)


def test_the_refusal_names_the_supported_way_to_authenticate():
    with pytest.raises(ValueError) as raised:
        HttpMcpTransport('https://alice:s3cret@gate.example/mcp')
    assert 'token=' in str(raised.value)


def test_an_ordinary_endpoint_is_still_accepted():
    transport = HttpMcpTransport('https://gate.example/mcp', token='t')
    assert transport.endpoint == 'https://gate.example/mcp'


# ---- F4: pending() bounds its own list, like recall() ---------------------

class _FixedTransport:
    """A caller-supplied transport returning one scripted payload."""

    def __init__(self, payload):
        self.payload = payload
        self.sent = []

    def call(self, tool, arguments):
        self.sent.append((tool, arguments))
        return self.payload


def test_pending_refuses_more_rows_than_it_asked_for():
    client = GateClient(_FixedTransport({'pending': [{'id': str(i)} for i in range(5000)]}))
    with pytest.raises(GateError) as raised:
        client.pending(limit=1)
    assert raised.value.kind == 'invalid_response'


def test_pending_refuses_rows_that_are_not_objects():
    client = GateClient(_FixedTransport({'pending': ['not-an-object']}))
    with pytest.raises(GateError):
        client.pending(limit=10)


def test_pending_accepts_exactly_its_limit():
    rows = [{'artifact_id': str(index)} for index in range(3)]
    client = GateClient(_FixedTransport({'pending': rows}))
    assert client.pending(limit=3)['pending'] == rows


# ---- F5: a non-Latin-1 token is refused at construction ------------------

@pytest.mark.parametrize('token', ['token-\u4e2d\u6587', '\U0001f600', 'токен'])
def test_a_token_http_cannot_encode_is_refused_at_construction(token):
    with pytest.raises(ValueError) as raised:
        HttpMcpTransport('https://gate.example/mcp', token=token)
    assert 'token' in str(raised.value)


def test_a_latin1_token_is_still_accepted():
    # 'ø' and 'é' are inside Latin-1, so http.client can send them: they stay
    # legal. Only what the header encoding genuinely cannot carry is refused.
    transport = HttpMcpTransport('https://gate.example/mcp', token='sécret-tøken')
    assert transport._token == 'sécret-tøken'


def test_a_bad_token_never_reaches_the_wire(serve):
    """The refusal is a configuration error, not a transport error mid-call."""
    endpoint = serve(_static_handler(_envelope({'state': 'ACTIVE'})))
    with pytest.raises(ValueError):
        HttpMcpTransport(endpoint, allow_insecure_loopback=True, token='token-\u4e2d')


# ---- F6: the response media type must be JSON ---------------------------

def test_an_html_labelled_reply_is_refused_even_when_it_parses(serve):
    endpoint = serve(_static_handler(_envelope({'state': 'ACTIVE'}),
                                     content_type='text/html; charset=utf-8'))
    transport = HttpMcpTransport(endpoint, allow_insecure_loopback=True)
    with pytest.raises(GateError) as raised:
        transport.call('doublegate.status', {'artifact_id': 'a'})
    assert raised.value.kind == 'invalid_response'


def test_an_html_labelled_reply_to_a_write_reports_an_unknown_outcome(serve):
    endpoint = serve(_static_handler(_envelope({'artifact_id': 'a', 'state': 'PENDING'}),
                                     content_type='text/html'))
    transport = HttpMcpTransport(endpoint, allow_insecure_loopback=True,
                                 allow_writes=True)
    with pytest.raises(GateError) as raised:
        transport.call('doublegate.remember', {'content': 'x'})
    assert raised.value.outcome_unknown is True


@pytest.mark.parametrize('content_type', [
    'application/json',
    'application/json; charset=utf-8',
    'APPLICATION/JSON',
    '  application/json  ',
    'application/vnd.doublegate+json',
])
def test_json_media_types_are_accepted(serve, content_type):
    endpoint = serve(_static_handler(_envelope({'state': 'ACTIVE'}),
                                     content_type=content_type))
    transport = HttpMcpTransport(endpoint, allow_insecure_loopback=True)
    assert transport.call('doublegate.status', {'artifact_id': 'a'})['state'] == 'ACTIVE'


# ---- F8: a malformed path is a configuration error, not an outage --------

@pytest.mark.parametrize('path', ['/m\x01cp', '/mcp with space', '/mcp\x7f'])
def test_a_path_with_control_characters_or_whitespace_is_refused(path):
    with pytest.raises(ValueError) as raised:
        HttpMcpTransport(f'https://gate.example{path}')
    assert 'path' in str(raised.value)


@pytest.mark.parametrize('url', [
    'https://gate.example/mcp\tx',      # urlsplit would silently yield /mcpx
    'https://gate.example/m\ncp',
    'https://gate.example/m\rcp',
])
def test_an_endpoint_urlsplit_would_silently_rewrite_is_refused(url):
    """Tab/CR/LF are deleted by ``urlsplit``; refuse rather than quietly mutate."""
    with pytest.raises(ValueError) as raised:
        HttpMcpTransport(url)
    assert 'control characters' in str(raised.value)


def test_an_ordinary_path_is_untouched():
    transport = HttpMcpTransport('https://gate.example/v1/mcp')
    assert transport._target.path == '/v1/mcp'


def test_an_absent_path_still_defaults_to_mcp():
    assert HttpMcpTransport('https://gate.example')._target.path == '/mcp'


# ---- F9 / preserved behaviour -------------------------------------------

def test_an_oversize_response_to_a_write_still_reports_an_unknown_outcome(serve):
    body = _envelope({'padding': 'x' * 4096})
    endpoint = serve(_static_handler(body))
    transport = HttpMcpTransport(endpoint, allow_insecure_loopback=True,
                                 allow_writes=True, max_response_bytes=16)
    with pytest.raises(GateError) as raised:
        transport.call('doublegate.remember', {'content': 'x'})
    assert raised.value.kind == 'response_too_large'
    assert raised.value.outcome_unknown is True


def test_an_oversize_response_to_a_read_is_a_known_failure(serve):
    endpoint = serve(_static_handler(_envelope({'padding': 'x' * 4096})))
    transport = HttpMcpTransport(endpoint, allow_insecure_loopback=True,
                                 max_response_bytes=16)
    with pytest.raises(GateError) as raised:
        transport.call('doublegate.status', {'artifact_id': 'a'})
    assert raised.value.kind == 'response_too_large'
    assert raised.value.outcome_unknown is False


def test_nothing_is_retried_after_the_hardening(serve):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length') or 0))
            seen.append(self.path)
            body = b'{"nope":true}'
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    endpoint = serve(Handler)
    transport = HttpMcpTransport(endpoint, allow_insecure_loopback=True,
                                 allow_writes=True)
    with pytest.raises(GateError):
        transport.call('doublegate.remember', {'content': 'x'})
    assert len(seen) == 1, 'the transport must send exactly one request per call'


def test_a_redirect_is_still_refused_before_the_body_is_read(serve):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length') or 0))
            self.send_response(307)
            self.send_header('Location', 'https://elsewhere.example/mcp')
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', '0')
            self.end_headers()

    endpoint = serve(Handler)
    transport = HttpMcpTransport(endpoint, allow_insecure_loopback=True,
                                 allow_writes=True, token='t')
    with pytest.raises(GateError) as raised:
        transport.call('doublegate.remember', {'content': 'x'})
    assert raised.value.kind == 'redirect_refused'
    assert raised.value.outcome_unknown is False


def test_writes_are_still_refused_locally_without_the_opt_in():
    transport = HttpMcpTransport('https://gate.example/mcp')
    with pytest.raises(GateError) as raised:
        transport.call('doublegate.remember', {'content': 'x'})
    assert raised.value.kind == 'writes_disabled'


def test_the_transport_still_makes_no_connection_on_construction(serve):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def do_POST(self):
            seen.append(self.path)

    endpoint = serve(Handler)
    HttpMcpTransport(endpoint, allow_insecure_loopback=True)
    assert seen == []
