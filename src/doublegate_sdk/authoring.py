# Modified for standalone doublegate_sdk namespace; see NOTICE.
"""One offline call: apply a check manifest to a file and get evidence back.

This is the authoring half of the SDK, and it stays offline. Nothing here opens a
connection, and no result produced here is an admission decision: a ``clean``
outcome means the manifest's literal checks matched, nothing more. Human review
stays outstanding exactly as the manifest declares it.

``evaluate_file`` composes the three low-level steps callers otherwise wire up by
hand -- :func:`doublegate_sdk.package.load_package`,
:func:`doublegate_sdk.package.read_bounded` and
:func:`doublegate_sdk.runtime.evaluate` -- and keeps the manifest identity that
each step separately knows about attached to the result. Those functions remain
public and unchanged; use them directly when you need to evaluate many bodies
against one already-loaded manifest, or content that never came from a file.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from doublegate_sdk.checks import Finding
from doublegate_sdk.package import PackageError, load_package, read_bounded
from doublegate_sdk.runtime import EvaluationError, evaluate

__all__ = ['FileEvaluation', 'evaluate_file']


@dataclass(frozen=True, slots=True)
class FileEvaluation:
    """Evidence from one manifest applied to one file, with the gate identified."""

    outcome: str
    findings: tuple[Finding, ...]
    gate_name: str
    gate_version: str
    artifact_type: str
    human_review: str
    manifest_digest: str

    @property
    def flagged(self) -> bool:
        return self.outcome == 'flagged'

    def to_payload(self) -> dict[str, Any]:
        return {'name': self.gate_name, 'version': self.gate_version,
                'digest': self.manifest_digest, 'human_review': self.human_review,
                'artifact_type': self.artifact_type, 'outcome': self.outcome,
                'findings': [finding.to_dict() for finding in self.findings]}


def evaluate_file(manifest_path: str | Path, content_path: str | Path, *,
                  artifact_type: str) -> FileEvaluation:
    """Load a manifest, read a bounded file and return evidence about it."""
    loaded = load_package(manifest_path)
    package = loaded.package
    try:
        content = read_bounded(content_path, package.max_input_bytes).decode('utf-8')
    except UnicodeDecodeError:
        raise EvaluationError('invalid_utf8') from None
    except PackageError as error:
        if error.kind == 'file_too_large':
            raise EvaluationError('input_too_large') from None
        raise
    result = evaluate(package, content, artifact_type)
    return FileEvaluation(result.outcome, result.findings, package.name, package.version,
                          artifact_type, package.human_review, loaded.digest)
