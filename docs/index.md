# Checks produce evidence.
# People retain authority.

Doublegate SDK is a small, offline Python toolkit for declarative gate authors.
Validate one JSON manifest, check required text, and carry structured findings
into your own trusted review workflow.

<div class="contract">manifest → bounded check → evidence<br>Human review and publication remain outside this SDK.</div>

!!! warning "Development snapshot · 0.1.0.dev0"
    This is a development snapshot, not a stable release. The manifest and
    SDK contract are both `0.1`. The package is not published to PyPI.

[Run the Python quickstart](quickstart.md){ .md-button .md-button--primary }
[Browse the API](api/index.md){ .md-button }

## Small by design

- **No runtime dependencies.** Python standard library only.
- **No executable plugins.** Required strings are literal and case-sensitive.
- **Bounded local files.** POSIX-only loader rejects symlinks and special files.
- **No authority handles.** A clean result does not authorize publication.

Use the [offline manual](manual.md) for CLI examples and the complete manifest
contract, or [compatibility policy](versions.md) before integrating with client-gate.

Apache-2.0. Copyright 2026 Eugene Korniichuk and the doublegate contributors.
