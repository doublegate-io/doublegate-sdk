# Add governed memory to your application

Use DoubleGate to submit observations, inspect their review state and retrieve
knowledge with provenance. The SDK gives Python applications a client; agent hosts
can connect directly through MCP.

## Choose your starting point

| What you are building | Start here |
|---|---|
| A Python application using memory | [Connect, propose and recall](quickstart.md) |
| An agent using tools | [Connect your agent through MCP](agents.md) |
| A deterministic content check | [Author a check](manual.md) |
| A DoubleGate integration or extension | [API reference](api/index.md) |

## One client, a few memory operations

```python
import os
from doublegate_sdk import connect

client = connect(os.environ['DOUBLEGATE_ENDPOINT'],
                 token=os.environ.get('DOUBLEGATE_TOKEN'))
answers = client.recall('How do we label laboratory specimens?', limit=5)
```

Start with a running gate endpoint. Enable writes explicitly when your application
needs to propose observations. Preserve the artifact id to follow review, and keep
provenance with retrieved content. See the [complete quickstart](quickstart.md).

## How the workflow fits together

Submit an observation → inspect its state → retrieve it when eligible for serving.

The gate performs review and enforces permissions. Your application chooses when
to submit and how to use the returned evidence. Pending submissions do not become
knowledge merely because a request succeeded.

## Developer resources

- [Client API and error handling](api/client.md)
- [Compatibility](versions.md)
- [SDK roadmap](roadmap.md)
- [Contributing and verification](maintaining.md)

The development SDK is distributed through GitHub. Remote authenticated MCP and
release acceptance are tracked in the roadmap. The inspected local Client Gate's
MCP listener is keyless on loopback; do not expose it as an authenticated remote API.

Apache-2.0. Copyright 2026 Eugene Korniichuk and the doublegate contributors.
