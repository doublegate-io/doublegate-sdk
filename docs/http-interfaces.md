# Choose the right HTTP interface

## Application or agent integration

Use the gate's **MCP endpoint** with the Python SDK or your agent host. The URL
ends in `/mcp` on the inspected service. The SDK sends MCP discovery and tool calls;
it does not send the internal daemon's `dg.*` operations.

```python
import os
from doublegate_sdk import connect

client = connect(os.environ['DOUBLEGATE_ENDPOINT'],
                 token=os.environ.get('DOUBLEGATE_TOKEN'))
answers = client.recall('laboratory specimen labels', limit=5)
```

To run the repository example against a real endpoint:

```sh
python examples/connect_to_gate.py --endpoint "$DOUBLEGATE_ENDPOINT"
```

The example submits one observation. Credentials come from `DOUBLEGATE_TOKEN`,
not a command-line token argument. Nothing launches a fake server if the endpoint
is absent. For local HTTP development, explicitly add `--allow-insecure-loopback`.
Use HTTPS for remote endpoints and confirm the server actually enforces access.

## Daemon administration and service-internal callers

The Client Gate runtime migration replaces its Unix listener with local HTTP
JSON-RPC at `/rpc`. That migration publishes per-run connection information in
`rpc.json` inside the gate home. This is an internal credential file, not public
configuration: do not paste its contents into logs, docs or agent messages.

**`/rpc` is not `/mcp`.** The SDK's `connect()` expects MCP and cannot be pointed at
an internal RPC endpoint just because both use HTTP and JSON. Gate CLI and internal
service callers use their own HTTP adapter for daemon operations.

The runtime migration is being validated separately from this SDK branch. Do not
assume a deployed gate has adopted it until its exact build has been checked.
Neither a bearer token nor loopback transport replaces operation authorization.

## Moving from the earlier SDK socket example

- Remove `UnixSocketTransport` construction. It is no longer a public SDK transport.
- Obtain the supported MCP URL from the deployment operator; do not derive it from
  a former socket filename or point it at `rpc.json`'s administration URL.
- Use `connect(endpoint, token=...)` for application calls.
- Enable writes explicitly with `allow_writes=True` only when submitting content.
- Use artifact-specific `status(artifact_id)`; the MCP contract does not provide
  the earlier daemon-wide status call.
- Inventory is absent from the inspected MCP catalog. Pending metadata is a
  different operation and must not be presented as a full inventory substitute.

## Acceptance boundaries

The SDK's real-service example has exercised the inspected console's loopback MCP
binding. That binding does not check bearer credentials on `/mcp`, even though
`/api/*` console calls do. The new protected internal `/rpc` listener does not
retroactively authenticate that separate MCP endpoint. Remote MCP authentication,
server grants and cross-principal testing remain separate acceptance work.

See [Python quickstart](quickstart.md), [agent integration](agents.md), and
[client reference](api/mcp-client.md) for the supported calls and errors.
