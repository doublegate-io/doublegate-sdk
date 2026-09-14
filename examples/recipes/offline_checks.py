"""Recipes for the offline half of the SDK: apply a manifest, read the evidence.

Everything in this module runs against files on this machine. Nothing opens a
connection, and no result here is an admission decision — a ``clean`` outcome
means the manifest's literal required strings were present, and the manifest's
``human_review`` requirement is still outstanding. That value is carried into
every return so a caller cannot lose it by accident.

The SDK entry point is
:func:`doublegate_sdk.authoring.evaluate_file`, which returns a frozen
:class:`doublegate_sdk.authoring.FileEvaluation` with these fields:

``outcome`` (``'clean'`` | ``'flagged'``), ``findings`` (a tuple of
:class:`doublegate_sdk.checks.Finding`), ``gate_name``, ``gate_version``,
``artifact_type``, ``human_review`` and ``manifest_digest``. ``.flagged`` is a
convenience property and ``.to_payload()`` is the JSON-ready dict.

Failures arrive as :class:`doublegate_sdk.errors.DoublegateError` subclasses
carrying a fixed ``kind`` — ``PackageError`` for the manifest or the file
(``unsafe_path``, ``not_regular_file``, ``file_too_large``, ``unreadable_file``,
``invalid_json``, ``duplicate_json_key``) and ``EvaluationError`` for the
content (``invalid_utf8``, ``input_too_large``, ``unsupported_artifact_type``,
``invalid_input_type``). No message text from the SDK is parsed here; only
``kind`` is read, because that is the part the SDK keeps stable.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from doublegate_sdk.authoring import FileEvaluation, evaluate_file
from doublegate_sdk.errors import DoublegateError

__all__ = [
    "CheckedFile",
    "check_one_file",
    "check_many_files",
    "summarize_checks",
    "blocking_findings",
]


@dataclass(frozen=True, slots=True)
class CheckedFile:
    """One file's result, including the case where it could not be evaluated.

    ``status`` is the evaluation ``outcome`` (``'clean'`` or ``'flagged'``) or
    the literal ``'unevaluated'``. ``error_kind`` is set only in that last case,
    and it is the SDK's fixed diagnostic ``kind`` — never a formatted message.
    """

    path: str
    status: str
    evaluation: FileEvaluation | None = None
    error_kind: str | None = None

    @property
    def clean(self) -> bool:
        return self.status == "clean"

    @property
    def human_review(self) -> str | None:
        """The manifest's review requirement, or ``None`` if nothing evaluated.

        A clean outcome does not discharge this. It is surfaced on the result
        so a caller reporting "clean" still has the obligation in hand.
        """
        return None if self.evaluation is None else self.evaluation.human_review

    def to_payload(self) -> dict[str, Any]:
        """JSON-ready, and honest about the unevaluated case."""
        if self.evaluation is None:
            return {"path": self.path, "status": self.status,
                    "error_kind": self.error_kind}
        return {"path": self.path, "status": self.status,
                **self.evaluation.to_payload()}


def check_one_file(manifest: str | Path, content: str | Path, *,
                   artifact_type: str) -> FileEvaluation:
    """Evaluate one file and return the SDK's own :class:`FileEvaluation`.

    This is a one-line wrapper and it stays one line on purpose: the SDK's
    return value is already the right shape, so wrapping it in something new
    would only hide fields. Errors propagate as-is — a caller that wants them
    turned into data should use :func:`check_many_files`.

    >>> evaluation = check_one_file("gates/release.gate.json",
    ...                             "docs/release/2026.09.md",
    ...                             artifact_type="memory")   # doctest: +SKIP
    >>> evaluation.outcome, evaluation.human_review           # doctest: +SKIP
    ('clean', 'required')
    """
    return evaluate_file(manifest, content, artifact_type=artifact_type)


def check_many_files(manifest: str | Path, contents: Iterable[str | Path], *,
                     artifact_type: str) -> list[CheckedFile]:
    """Evaluate every path against one manifest; never stop at the first bad one.

    A file the SDK refuses becomes a ``CheckedFile`` with
    ``status='unevaluated'`` and the SDK's fixed ``kind``. A run that aborts on
    the first unreadable file tells you less than one that reports all of them,
    which is the whole reason this returns a list rather than raising.

    The manifest itself is loaded once per file by ``evaluate_file``; to apply
    one already-loaded manifest to many bodies, use
    ``doublegate_sdk.package.load_package`` plus
    ``doublegate_sdk.runtime.evaluate`` directly.
    """
    results: list[CheckedFile] = []
    for content in contents:
        try:
            evaluation = evaluate_file(manifest, content, artifact_type=artifact_type)
        except DoublegateError as error:
            # ``kind`` is the stable part. The message is not parsed, and the
            # file's contents are never echoed into the result.
            results.append(CheckedFile(str(content), "unevaluated",
                                       error_kind=getattr(error, "kind", "sdk_error")))
            continue
        results.append(CheckedFile(str(content), evaluation.outcome, evaluation))
    return results


def summarize_checks(results: Iterable[CheckedFile]) -> dict[str, Any]:
    """Count outcomes and restate what a check run does and does not settle.

    The two boolean members are constants. They are emitted anyway so a
    consumer of this dict never has to infer admission from an outcome string.
    """
    materialised = list(results)
    statuses = [result.status for result in materialised]
    reviews = {result.human_review for result in materialised
               if result.human_review is not None}
    return {
        "counts": {name: statuses.count(name)
                   for name in ("clean", "flagged", "unevaluated")},
        "total": len(materialised),
        "human_review": sorted(reviews),
        "is_admission_decision": False,
        "human_review_satisfied": False,
    }


def blocking_findings(evaluation: FileEvaluation,
                      *, severities: frozenset[str] = frozenset({"error"})
                      ) -> tuple[dict[str, Any], ...]:
    """Findings at the severities you treat as blocking, as plain dicts.

    ``severities`` is yours to choose: the bundled release-note manifest emits
    ``warning``, and whether a warning blocks your pipeline is a policy decision
    the SDK does not make for you. Nothing is filtered by default beyond the
    severity set given.

    Findings from the bundled evaluator carry fixed diagnostics and an empty
    ``excerpt``, so the returned dicts are safe to log.
    """
    return tuple(finding.to_dict() for finding in evaluation.findings
                 if finding.severity in severities)
