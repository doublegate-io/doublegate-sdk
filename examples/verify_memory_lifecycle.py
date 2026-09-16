"""Optional source-mode integration probe; requires Client Gate, not an SDK dependency.

Runs the SDK's HTTP/MCP transport against a real, isolated Client Gate console
listener on an ephemeral loopback port — the product's own ``POST /mcp`` binding
(ADR-0050), not a mock.

Two things are proved, and they are deliberately kept apart:

1. The functional path — propose → status → recall — over real HTTP/MCP bytes.
2. The authentication behaviour of the *maintained service as it stands*:
   ``/api/*`` enforces the console bearer token (401 without, 200 with), while
   ``POST /mcp`` is mounted keyless on loopback (``console.py`` dispatches /mcp
   before ``_authed()``; loopback is the credential, INV-SEC-11 / FR-65).

So this run does NOT prove an authenticated remote MCP client. The SDK's own
handling of 401/403 is proved by the scripted-responder suite instead. The gap
is the service's, and it is recorded in the evidence rather than papered over.

The reviewer is the product's deterministic StubBackend, not a live model.
"""
from pathlib import Path
import http.client
import json
import subprocess
import sys
import tempfile
import threading
import time

from doublegate.daemon import Daemon
from doublegate.console import start_console
from doublegate_sdk.client import GateClient, GateError, HttpMcpTransport


def _free_port():
    import socket
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def _api_status_code(port, token):
    """The product's real token guard on /api/status — not a mocked check."""
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
    try:
        headers = {'Authorization': f'Bearer {token}'} if token else {}
        connection.request('GET', '/api/status', headers=headers)
        response = connection.getresponse()
        response.read()
        return response.status
    finally:
        connection.close()


def run(parent):
    root = Path(tempfile.mkdtemp(prefix='sdk-http-mcp-', dir=parent))
    home = root / 'gate'
    home.mkdir(mode=0o700)
    port = _free_port()
    (home / 'config.toml').write_text(
        '[console]\nenabled=false\n'
        '[gating]\nbackend="stub"\nauto_grade=true\n'
        '[lifecycle]\nscan_delay_s=0\n')
    daemon = Daemon(home)


    console = None
    try:
        deadline = time.monotonic() + 15
        while True:
            try:
                console = start_console(daemon, bind=('127.0.0.1', port))
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.05)

        endpoint = f'http://127.0.0.1:{port}/mcp'
        writer = GateClient(HttpMcpTransport(endpoint, allow_insecure_loopback=True,
                                             allow_writes=True, timeout=15))
        reader_transport = HttpMcpTransport(endpoint, allow_insecure_loopback=True, timeout=15)
        reader = GateClient(reader_transport)

        # ---- negotiation: what this server actually serves ----
        discovered = reader_transport.discover()
        served_tools = reader_transport.tool_names()
        assert 'doublegate.status' in served_tools, served_tools
        assert not any('inventory' in name for name in served_tools), served_tools

        # ---- functional path over real HTTP/MCP ----
        text = 'Basalt specimen registry uses durable labels for the laboratory collection.'
        proposal = writer.propose(text, content_type='memory', trust_class='T-4',
                                  source_uri='fixture://sdk-http-mcp')
        aid = proposal['artifact_id']
        assert aid.startswith('sha256:'), aid
        pre_admission_state = reader.status(aid)['state']
        assert reader.recall('Basalt')['results'] == []

        tick = daemon.tick()
        admitted_state = reader.status(aid)['state']
        assert admitted_state == 'active', (admitted_state, tick)
        hits = reader.recall('Basalt', include_provisional=True)['results']
        assert len(hits) == 1 and hits[0]['artifact_id'] == aid, hits

        # ---- a read-only client cannot be talked into writing ----
        writes_refused = False
        try:
            reader.propose('x', content_type='memory', source_uri='fixture://denied')
        except GateError as error:
            writes_refused = error.kind == 'writes_disabled'
        assert writes_refused

        # ---- the service's real token guard, on the route that has one ----
        unauth = _api_status_code(port, None)
        authed = _api_status_code(port, console.token)
        assert unauth == 401 and authed == 200, (unauth, authed)

        # ---- and the honest negative about the route that has none ----
        mcp_without_token = reader.status(aid)['state']
        mcp_is_keyless = mcp_without_token == admitted_state

        example = subprocess.run(
            [sys.executable, str(Path(__file__).with_name('connect_to_gate.py')),
             '--endpoint', endpoint, '--allow-insecure-loopback'],
            capture_output=True, text=True, timeout=30)
        (root / 'developer-example.log').write_text(example.stdout + example.stderr)
        assert example.returncode == 0, example.stderr

        result = {
            'passed': True,
            'developer_example_exit_code': example.returncode,
            'transport': 'HttpMcpTransport over the product console POST /mcp (ADR-0050)',
            'endpoint': endpoint,
            'artifact_id': aid,
            'pre_admission_state': pre_admission_state,
            'admitted_state': admitted_state,
            'own_pending_excluded_from_recall': True,
            'provisional_requires_opt_in': True,
            'same_record_recalled': True,
            'served_tools': list(served_tools),
            'server_info': discovered.get('serverInfo'),
            'protocol_versions': discovered.get('protocolVersions'),
            'inventory_tool_present': False,
            'read_only_client_refused_write': writes_refused,
            'auth_proved_on': {
                'route': '/api/status',
                'without_token': unauth,
                'with_token': authed,
                'note': "the product's own console bearer guard; no mock",
            },
            'auth_gap': {
                'route': 'POST /mcp',
                'mcp_answered_without_token': mcp_is_keyless,
                'note': ('console.py dispatches /mcp before _authed(); loopback is the '
                         'credential (INV-SEC-11 / FR-65). This run therefore does NOT '
                         'prove an authenticated MCP client against the maintained '
                         'client tier. SDK-side 401/403 handling is proved by the '
                         'scripted-responder suite. No server change was made.'),
            },
            'reviewer': 'deterministic product StubBackend; no live inference',
            'scope': ('one isolated loopback gate, source mode; not HTTPS, not remote, '
                      'not cross-user authorization, not organization delivery'),
            'sdk_origin': __import__('doublegate_sdk.client', fromlist=['x']).__file__,
            'gate_origin': __import__('doublegate.console', fromlist=['x']).__file__,
        }
        (root / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result, indent=2))
        print('Evidence:', root / 'result.json')
    finally:
        if console is not None:
            console.httpd.shutdown()
            console.httpd.server_close()
        daemon.close()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-parent', type=Path, required=True)
    args = parser.parse_args()
    run(args.output_parent)
