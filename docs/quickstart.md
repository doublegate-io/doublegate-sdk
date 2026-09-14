# Connect your Python application

Use the SDK when your application needs to submit observations, check their review
state and retrieve knowledge. You need a running gate's MCP endpoint and whatever
credential that deployment requires. Ask its operator for the endpoint and access;
a URL or space name alone does not grant permission.

## 1. Install the development SDK

From a checkout of the SDK revision you intend to use:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install .
```

Development distributions are published through GitHub, not PyPI. These commands
install the selected source checkout; they do not start a gate or provision access.

## 2. Connect and submit an observation

Set `DOUBLEGATE_ENDPOINT` to the full MCP URL. Supply `DOUBLEGATE_TOKEN` through your
application's secret mechanism when the deployment requires a bearer credential.
The HTTPS address below must be your actual deployment, not an example host.

```python
import os
from doublegate_sdk import connect

client = connect(
    os.environ['DOUBLEGATE_ENDPOINT'],
    token=os.environ.get('DOUBLEGATE_TOKEN'),
    allow_writes=True,
)
proposal = client.propose(
    'The laboratory labels basalt specimens by collection date.',
    content_type='memory',
    trust_class='T-4',
    source_uri='application://laboratory/observation/123',
)
artifact_id = proposal['artifact_id']
print(client.status(artifact_id)['state'])
```

Keep `artifact_id`: it is the handle for checking this proposal. The returned state
is the gate's answer. Submission success does not mean review is finished.

## 3. Retrieve knowledge

```python
answers = client.recall('How are basalt specimens labelled?', limit=5)
for result in answers['results']:
    print(result)
```

An empty result immediately after submission is expected while the observation is
awaiting review. Recall excludes your unreviewed submissions. Provisional knowledge
is excluded by default; use `include_provisional=True` only when your application
accepts it. Keep the returned provenance and flags with the content you use.

## Run the complete example

The repository includes the same workflow as a runnable program:

```sh
python examples/connect_to_gate.py --endpoint "$DOUBLEGATE_ENDPOINT"
```

This writes one observation to the selected gate. It never launches a substitute
server. For a local development endpoint, explicitly add
`--allow-insecure-loopback`; remote connections use HTTPS.

## Handle failures

Catch `GateError` from `doublegate_sdk.client` and branch on `kind`, not its text.
If `outcome_unknown` is true after a write, reconcile with the gate before retrying:
the server may already have accepted the request. The SDK does not retry writes.
See [client reference](api/client.md) for the exact errors and bounds.

## Deployment boundary

Use the MCP URL, not the daemon administration `/rpc` URL. Both use HTTP but
have different operations. See [HTTP interfaces and migration](http-interfaces.md).

The inspected Client Gate's local `/mcp` endpoint is keyless on loopback. Its
console token protects `/api/*`, not that MCP route. An SDK bearer header cannot
supply missing server enforcement. Authenticated remote MCP requires an endpoint
that actually verifies the credential. See [roadmap](roadmap.md) for that acceptance
work; do not expose the keyless local listener as a remote service.

Next: [connect an agent host](agents.md), or [write a deterministic check](manual.md)
if you are extending validation rather than consuming memory.
