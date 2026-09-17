"""Shared test doubles: a recording transport, an AF_UNIX one-shot server and a
one-shot HTTP server. Doubles here are transport fixtures, never gate authority."""
from __future__ import annotations

import http.server
import json
import socket
import threading

import pytest


class Recorder:
    """A caller-supplied transport that answers one canned result and records every call."""
    scope = 'knowledge'

    def __init__(self, result=None, *, results=None, scope='knowledge'):
        self.result, self.results, self.calls, self.scope = result, list(results or []), [], scope

    def call(self, method, params):
        self.calls.append((method, params))
        if self.results:
            nxt = self.results.pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            return nxt
        if isinstance(self.result, Exception):
            raise self.result
        return self.result if self.result is not None else {}


@pytest.fixture
def unix_server(tmp_path):
    """Start an AF_UNIX listener answering one request with ``responder(request) -> bytes``."""
    threads, errors, servers = [], [], []

    def start(responder):
        path = tmp_path / f'rpc-{len(servers)}.sock'
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(path)); server.listen(1); server.settimeout(5)
        servers.append(server)

        def serve():
            try:
                connection, _ = server.accept()
                with connection:
                    request = json.loads(connection.makefile('rb').readline())
                    connection.sendall(responder(request))
            except Exception as exc:  # noqa: BLE001 — reported by the fixture
                errors.append(exc)
            finally:
                server.close()
        thread = threading.Thread(target=serve, daemon=True); thread.start(); threads.append(thread)
        return path
    yield start
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()
    assert not errors


@pytest.fixture
def http_server():
    """A loopback HTTP server: ``start(handler(method, path, headers, body) -> (status, headers, body))``."""
    servers, threads = [], []

    def start(handler):
        class Handler(http.server.BaseHTTPRequestHandler):
            def _serve(self):
                n = int(self.headers.get('Content-Length') or 0)
                body = self.rfile.read(n) if n else b''
                status, headers, out = handler(self.command, self.path, dict(self.headers), body)
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header('Content-Length', str(len(out)))
                self.end_headers()
                self.wfile.write(out)
            do_POST = do_GET = do_PUT = _serve
            def log_message(self, *args):  # noqa: A002
                pass
        server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
        servers.append(server)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True); thread.start(); threads.append(thread)
        return f'http://127.0.0.1:{server.server_port}'
    yield start
    for server in servers:
        server.shutdown(); server.server_close()
    for thread in threads:
        thread.join(timeout=5)


def rpc_ok(result):
    return lambda r: json.dumps({'jsonrpc': '2.0', 'id': r['id'], 'result': result}).encode() + b'\n'


def rpc_error(code, message='refused', data=None):
    def respond(r):
        err = {'code': code, 'message': message}
        if data is not None:
            err['data'] = data
        return json.dumps({'jsonrpc': '2.0', 'id': r['id'], 'error': err}).encode() + b'\n'
    return respond
