# Versions and compatibility

## Three distinct version domains

| Domain | Current value | Meaning |
| --- | --- | --- |
| Python distribution | `0.1.0.dev5` | Unreleased development snapshot of `doublegate-sdk`. |
| Manifest `schema_version` | `"0.1"` | Exact JSON shape accepted by the validator. |
| Manifest `sdk_version` | `"0.1"` | Exact declarative capability contract; not a PyPI version constraint. |
| Gate author's `version` | e.g. `"1.0.0"` | User-owned gate metadata, not an SDK release. |

The validator accepts only the two exact contract strings. There is no range
resolver, automatic schema upgrade or dependency installer.

## `0.1.0.dev5`

Two development lines merged. From `main`: the HTTP/MCP client (`doublegate_sdk.client`,
`connect`), `evaluate_file` authoring composition, the starter apps and recipes,
optional observability, reason and skipped vocabularies, response schemas and the
release workflow. From the ADR-0074 line: `KnowledgeClient` and `CurationClient` over
`UnixSocketTransport` and `HttpTransport` (`POST /rpc`), the `dg.*` operation table,
the organization gate's REST helpers (`org_rest`) and the signed submission event. Reconciled:
one `DoublegateError` base with one `GateError` for every door; `describe-client`
prints both descriptions; the `/mcp` reference page moved to `api/mcp-client.md`.

## Compatibility boundary

The extracted schema, evaluation algorithms and serialized payloads retain the
source SDK behavior. Imports move from `doublegate.sdk` to `doublegate_sdk`.
Existing client-gate modules are not modified or automatically redirected.
`Finding` and `ScanResult` are different Python class identities across the two
packages: bridge through `to_payload()` rather than `isinstance` across packages.
The service scanner is deliberately not part of this distribution.

Python 3.11+ is the declared floor. Tests here were executed on Linux/WSL;
package loading requires descriptor-relative POSIX APIs including `O_NOFOLLOW`.
Native Windows file loading is unsupported. In-memory validation and checks use
stdlib Python, but no cross-platform verification is claimed for this snapshot.

## Release policy

Before 1.0, breaking Python API changes require a minor version increment and
migration notes; compatible fixes increment the patch version. Development
snapshots use PEP 440 `.devN` versions and carry no stable-support guarantee.
Schema/capability changes require an explicit contract-version decision and tests;
never silently reinterpret an existing manifest. The package version and contract
version evolve independently. A stable support matrix must name tested Python,
OS and client-gate versions; none is asserted yet.

## Documentation versions

Only the `dev` documentation channel is configured. There is no `stable` or `latest` alias today. The
version selector reads a mike-compatible `versions.json`; the local preview
creates exactly one entry. An actual tagged release will get an immutable
full-version path and, only after verification, a `stable` alias. Keep old version
outputs rather than rebuilding them with new code or tools.

See [maintainer workflow](maintaining.md) for automated dev deployment and release
snapshot commands. Deployment status is visible in GitHub Actions.
