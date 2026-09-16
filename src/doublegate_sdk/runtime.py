# Modified for standalone doublegate_sdk namespace; see NOTICE.
"""Bounded literal checks. Results are evidence, not an admission decision."""
from __future__ import annotations

from doublegate_sdk.checks import Finding, ScanResult
from doublegate_sdk.manifest import GatePackage


from doublegate_sdk.errors import DoublegateError


class EvaluationError(DoublegateError, ValueError):
    """A fixed diagnostic, never containing input text."""
    @property
    def kind(self):
        return str(self.args[0]) if self.args else 'evaluation_error'


def evaluate(package: GatePackage, content: str, artifact_type: str) -> ScanResult:
    """Match all required strings, case-sensitively, without regex or execution.

    Callers supply a validated package. Human-review requirements remain metadata;
    a clean check does not satisfy them or authorize publication. No body is returned.
    """
    if artifact_type not in package.artifact_types:
        raise EvaluationError('unsupported_artifact_type')
    if type(content) is not str:
        raise EvaluationError('invalid_input_type')
    if len(content) > package.max_input_bytes:
        raise EvaluationError('input_too_large')
    try:
        size = len(content.encode('utf-8'))
    except UnicodeEncodeError:
        raise EvaluationError('invalid_utf8') from None
    if size > package.max_input_bytes:
        raise EvaluationError('input_too_large')
    findings = tuple(Finding('required-text', 'warning', f'required_text_missing: check {i}')
                     for i, text in enumerate(package.required_text) if text not in content)
    return ScanResult('flagged' if findings else 'clean', findings)
