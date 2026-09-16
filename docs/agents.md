# Use DoubleGate from your agent

If your agent host supports MCP, connect it to the gate directly. You do not need
the Python SDK or a package manifest to make the gate's tools available.

## Configure the connection

In the host's MCP server settings, provide:

- The full HTTP MCP endpoint supplied by the gate operator.
- The credential required by that deployment, using the host's secret facility.
- HTTP request/response support compatible with the gate's advertised MCP version.

Host configuration formats differ; use the host's documented HTTP MCP setup rather
than copying a universal-looking JSON block. This SDK supports the gate's stateless
JSON response subset; SSE and sessionful MCP are not claimed by the Python client.

## Verify the tools before using them

Use the host's tool discovery and confirm the server's actual catalog. The inspected
Client Gate exposes these memory tasks:

| Task | Tool |
|---|---|
| Submit an observation | `doublegate.remember` |
| Retrieve knowledge | `doublegate.recall` |
| Check one artifact's state | `doublegate.status` |
| Inspect its recorded history | `doublegate.why` |
| Inspect pending metadata | `doublegate.pending` |

Availability and authorization are the answering gate's responsibility. Inventory
is not the same as pending metadata; the inspected MCP catalog has no inventory tool.

## Follow one observation through review

1. Submit content with its source URI using `doublegate.remember`.
2. Preserve the returned artifact id and inspect it with `doublegate.status`.
3. Retrieve knowledge using `doublegate.recall` after the gate's review lifecycle.
4. Inspect provenance and use `doublegate.why` when the decision needs explanation.

For authoritative recall, explicitly set `include_own_pending=false`. The inspected
MCP tool defaults differ from the Python SDK: raw MCP may append your own unreviewed
claims. Set `include_provisional=false` unless your application accepts provisional
knowledge. Tool transport success is not an admission verdict.

## Local versus remote access

The inspected Client Gate mounts MCP keylessly on loopback. Its console bearer token
is checked on `/api/*`, not `/mcp`. For remote use, the operator must provide an
endpoint with actual server-side authentication and authorization. Never publish the
keyless local listener to the network merely because your host accepts a URL.

For application-owned memory calls instead of host tools, follow the
[Python quickstart](quickstart.md). Local check manifests belong to
[custom check authoring](manual.md), not agent connection setup.
