"""Propose an observation to a real gate and inspect its state.

Set DOUBLEGATE_TOKEN through your application's credential mechanism if required.
No fallback server is started. The command writes one observation when executed.
"""
from __future__ import annotations

import argparse
import os

from doublegate_sdk import connect


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint', required=True, help='Full MCP URL, e.g. https://gate.example.org/mcp')
    parser.add_argument('--allow-insecure-loopback', action='store_true', help='Explicitly permit local HTTP development endpoints')
    args = parser.parse_args()
    client = connect(args.endpoint, token=os.environ.get('DOUBLEGATE_TOKEN'),
                     allow_writes=True, allow_insecure_loopback=args.allow_insecure_loopback,
                     timeout=15)
    print('tools:', ', '.join(client.transport.tool_names()))
    proposal = client.propose(
        'The laboratory labels basalt specimens by collection date.',
        content_type='memory', trust_class='T-4',
        source_uri='application://examples/connect_to_gate')
    print('artifact:', proposal['artifact_id'])
    print('state:', client.status(proposal['artifact_id'])['state'])
    answers = client.recall('basalt specimens', limit=5)
    print('results:', answers['results'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
