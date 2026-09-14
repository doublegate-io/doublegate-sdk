"""A response owns its stream after HTTPConnection releases a closing socket.

Two shapes matter here and they pull in opposite directions:

* A closing peer may frame the body with ``Content-Length`` *or* with nothing at
  all, ending it by shutting down the write side. The EOF-framed shape is the
  one that breaks if ``_DeadlineReader`` reads the socket directly instead of
  holding the standard library's file over it, because ``HTTPResponse`` then
  loses the descriptor before the body is drained.
* Holding that file must not cost the total deadline. A peer that dribbles an
  EOF-framed body forever is still cut off on budget, and a write cut off that
  way still reports its outcome as unknown.

The raw-socket server below exists because ``BaseHTTPRequestHandler`` cannot
emit an EOF-framed response or drip bytes on demand.
"""
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from doublegate_sdk.client import GateError, HttpMcpTransport


@pytest.mark.parametrize('version', ['HTTP/1.0', 'HTTP/1.1'])
@pytest.mark.parametrize('writing', [False, True])
def test_closing_response_preserves_body_and_write_handle(version, writing):
    payload = {'artifact_id': 'artifact-returned', 'state': 'pending', 'padding': 'x' * 200000}
    class Handler(BaseHTTPRequestHandler):
        protocol_version = version
        def log_message(self, *args): pass
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            body = json.dumps({'jsonrpc': '2.0', 'id': request['id'],
                               'result': {'structuredContent': payload}}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        transport = HttpMcpTransport(f'http://127.0.0.1:{server.server_port}/mcp',
                                    allow_insecure_loopback=True, allow_writes=writing)
        tool = 'doublegate.remember' if writing else 'doublegate.status'
        assert transport.call(tool, {'artifact_id': 'a'}) == payload
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()
        assert not thread.is_alive()


# --------------------------------------------------------------------------
# EOF-framed responses: the body ends when the peer shuts down, not at a
# declared length. Nothing above this line exercises that framing.
# --------------------------------------------------------------------------

@pytest.fixture
def raw_serve():
    """Serve one connection with a script that owns the whole response."""
    state = {}

    def start(script):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)

        def run():
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            try:
                head = b''
                while b'\r\n\r\n' not in head:
                    chunk = conn.recv(65536)
                    if not chunk:
                        return
                    head += chunk
                headers, _, rest = head.partition(b'\r\n\r\n')
                length = 0
                for line in headers.split(b'\r\n'):
                    if line.lower().startswith(b'content-length:'):
                        length = int(line.split(b':')[1])
                while len(rest) < length:
                    more = conn.recv(65536)
                    if not more:
                        break
                    rest += more
                script(conn)
            except OSError:
                pass
            finally:
                conn.close()
                listener.close()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        state['thread'] = thread
        return listener.getsockname()[1]

    yield start
    thread = state.get('thread')
    if thread is not None:
        thread.join(10)


def _reply(payload):
    return json.dumps({'jsonrpc': '2.0', 'id': 1,
                       'result': {'structuredContent': payload}}).encode()


_EOF_HEAD = (b'HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n'
             b'Connection: close\r\n\r\n')


def _call(port, *, writing=False, timeout=10.0):
    transport = HttpMcpTransport(f'http://127.0.0.1:{port}/mcp',
                                 allow_insecure_loopback=True,
                                 allow_writes=writing, timeout=timeout)
    tool = 'doublegate.remember' if writing else 'doublegate.status'
    return transport.call(tool, {'artifact_id': 'a'})


@pytest.mark.parametrize('writing', [False, True])
def test_an_eof_framed_body_arriving_late_is_still_read_whole(raw_serve, writing):
    """No Content-Length, and the body lands well after the headers."""
    payload = {'artifact_id': 'sha256:delayed', 'state': 'pending'}

    def script(conn):
        conn.sendall(_EOF_HEAD)
        time.sleep(0.5)
        conn.sendall(_reply(payload))
        conn.shutdown(socket.SHUT_WR)

    assert _call(raw_serve(script), writing=writing, timeout=10.0) == payload


def test_a_large_eof_framed_body_spanning_many_reads_is_reassembled(raw_serve):
    payload = {'artifact_id': 'sha256:large', 'state': 'pending', 'padding': 'x' * 300000}
    body = _reply(payload)

    def script(conn):
        conn.sendall(_EOF_HEAD)
        for start in range(0, len(body), 8192):
            conn.sendall(body[start:start + 8192])
        conn.shutdown(socket.SHUT_WR)

    assert _call(raw_serve(script), timeout=20.0) == payload


def test_a_write_over_an_eof_framed_reply_still_returns_its_artifact_id(raw_serve):
    """The id is the only reference the caller has; closing framing must not eat it."""
    payload = {'artifact_id': 'sha256:write-id-survives', 'state': 'pending'}

    def script(conn):
        conn.sendall(_EOF_HEAD)
        time.sleep(0.2)
        conn.sendall(_reply(payload))
        conn.shutdown(socket.SHUT_WR)

    answer = _call(raw_serve(script), writing=True, timeout=10.0)
    assert answer['artifact_id'] == 'sha256:write-id-survives'


def test_an_eof_framed_body_that_never_ends_is_cut_off_at_the_deadline(raw_serve):
    """Holding the stdlib file must not buy the peer unbounded time."""
    def script(conn):
        conn.sendall(_EOF_HEAD)
        conn.sendall(b'{"jsonrpc":"2.0","id":1,"result":{"structuredContent":{')
        for index in range(200):
            conn.sendall(b'"k%d":"v",' % index)
            time.sleep(0.3)

    start = time.monotonic()
    with pytest.raises(GateError) as raised:
        _call(raw_serve(script), writing=True, timeout=1.0)
    elapsed = time.monotonic() - start

    assert raised.value.kind == 'timeout'
    # A write cut off mid-body cannot know whether the gate recorded it.
    assert raised.value.outcome_unknown is True
    assert elapsed < 3.0, f'deadline was not total: {elapsed:.2f}s for a 1.0s budget'


def test_a_dripped_status_line_on_a_closing_reply_is_bounded(raw_serve):
    def script(conn):
        for byte in b'HTTP/1.0 200 OK\r\n':
            conn.sendall(bytes([byte]))
            time.sleep(0.3)

    start = time.monotonic()
    with pytest.raises(GateError) as raised:
        _call(raw_serve(script), timeout=1.0)

    assert raised.value.kind == 'timeout'
    assert time.monotonic() - start < 3.0
