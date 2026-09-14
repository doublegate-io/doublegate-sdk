# Modified for standalone doublegate_sdk namespace; see NOTICE.
"""Standalone experimental gate contracts (manifest/SDK contract 0.1).

Checks supply evidence, never publication authority. No executable plugin loader.

``doublegate_sdk.evaluate_file(manifest, content, artifact_type=...)`` is the
offline entry point: it applies a declarative check manifest to one file and
returns evidence. It opens nothing.

``doublegate_sdk.connect(endpoint, token=...)`` is the entry point for talking to
a running gate. It is resolved lazily, so importing this package for the offline
authoring half costs nothing and still opens no connection.
"""
from typing import TYPE_CHECKING

if TYPE_CHECKING:                      # pragma: no cover - typing only
    from doublegate_sdk.authoring import evaluate_file
    from doublegate_sdk.client import connect

__all__ = ["connect", "evaluate_file"]


def __getattr__(name: str):
    """Resolve the entry points on first use; nothing connects here either."""
    if name == "connect":
        from doublegate_sdk.client import connect as _connect
        return _connect
    if name == "evaluate_file":
        from doublegate_sdk.authoring import evaluate_file as _evaluate_file
        return _evaluate_file
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
