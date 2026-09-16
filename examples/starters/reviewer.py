"""Starter: prepare for a reviewer adapter the SDK does not publish.

**The honest headline.** This SDK has no reviewer stage. There is no model
adapter type, no reviewer protocol, no stage schema, and no tool on the client
tier that submits a reviewer verdict. ``--probe`` proves that against the
installed package rather than asserting it in prose, and the starter refuses to
pretend otherwise: it never calls a model, never contacts a gate, and never
turns a verdict into an admission.

**What it does instead**, which is the useful part while the contract is
missing: it exercises the input/output contract *your application* owns, offline,
against the fixtures in ``data/``, using only machinery the SDK actually
publishes —

* ``doublegate_sdk.schema.check_response`` validates both documents against the
  bounded Draft 2020-12 subset the SDK ships;
* ``doublegate_sdk.reasons.normalise_category``,
  ``doublegate_sdk.vocabulary.normalise_blockers`` and
  ``doublegate_sdk.skipped.normalise_skipped_reason`` re-check the verdict's
  enumerated values against the SDK's closed vocabularies, so a document that
  passes here also passes the SDK's own normalisers;
* ``doublegate_sdk.authoring.evaluate_file`` supplies the deterministic evidence
  the reviewer input carries, so the input is real, not hand-written.

The two schemas under ``data/`` are **caller-owned fixtures, not a product
contract**. They are labelled as such in their own ``description`` fields. When
the product publishes a reviewer stage, throw them away and use it.

Usage::

    python examples/starters/reviewer.py --help
    python examples/starters/reviewer.py --probe
    python examples/starters/reviewer.py                  # validate the bundled exchange
    python examples/starters/reviewer.py --exchange my-exchange.json

Exit status: ``0`` the exchange satisfies the local contract, ``1`` it does not,
``3`` the reviewer stage is unavailable and the caller asked for it (``--require-stage``).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from doublegate_sdk.authoring import evaluate_file
from doublegate_sdk.errors import DoublegateError
from doublegate_sdk.reasons import normalise_category
from doublegate_sdk.schema import check_response
from doublegate_sdk.skipped import normalise_skipped_reason
from doublegate_sdk.submission import content_digest_from_content_hash, envelope_content_hash
from doublegate_sdk.vocabulary import normalise_blockers

DATA = Path(__file__).resolve().parent / "data"
INPUT_SCHEMA = DATA / "reviewer-input.schema.json"
OUTPUT_SCHEMA = DATA / "reviewer-output.schema.json"
EXCHANGE = DATA / "reviewer-exchange.json"
MANIFEST = DATA / "release-note.gate.json"
SAMPLE_BODY = DATA / "release-note.flagged.md"

OK, CONTRACT_VIOLATION, STAGE_UNAVAILABLE = 0, 1, 3

#: Names a reviewer stage would plausibly be published under. Probed, not
#: assumed: the point is to detect the day one of them appears.
_CANDIDATE_ATTRIBUTES = (
    ("doublegate_sdk", "reviewer"),
    ("doublegate_sdk", "review"),
    ("doublegate_sdk", "adapter"),
    ("doublegate_sdk", "model"),
    ("doublegate_sdk.client", "GateClient.review"),
    ("doublegate_sdk.client", "GateClient.judge"),
    ("doublegate_sdk.client", "GateClient.verdict"),
)

#: Client-tier tools that would have to exist for a verdict to reach a gate.
_CANDIDATE_TOOLS = ("doublegate.review", "doublegate.judge", "doublegate.verdict",
                    "doublegate.admit")


def probe_reviewer_stage() -> dict[str, Any]:
    """Ask the installed SDK whether a reviewer stage exists. No network.

    Candidate names are diagnostic hints, not an executable adapter contract.
    This starter has no verified reviewer binding, even if a future symbol appears.
    """
    import importlib

    found: list[str] = []
    for module_name, attribute in _CANDIDATE_ATTRIBUTES:
        try:
            module = importlib.import_module(module_name)
        except ImportError:                      # pragma: no cover - defensive
            continue
        target: Any = module
        for part in attribute.split("."):
            target = getattr(target, part, None)
            if target is None:
                break
        if target is not None:
            found.append(f"{module_name}.{attribute}")

    from doublegate_sdk.client import describe_client

    description = describe_client()
    served = set(description["mcp"]["tools"])
    tools = sorted(served & set(_CANDIDATE_TOOLS))

    return {
        "supported": False,
        "candidate_detected": bool(found or tools),
        "python_symbols": found,
        "client_tier_tools": tools,
        "probed_symbols": [f"{m}.{a}" for m, a in _CANDIDATE_ATTRIBUTES],
        "probed_tools": list(_CANDIDATE_TOOLS),
        "available_client_tools": description["mcp"]["tools"],
        "diagnostic": (
            "reviewer_stage_unavailable: this starter has no verified reviewer adapter "
            "binding. Candidate names alone do not establish compatibility. "
            "The contract exercised below is this application's own; it "
            "is not a product schema and nothing validates it remotely."
        ),
    }


def build_reviewer_input(manifest: Path, body: Path, *, artifact_type: str) -> dict[str, Any]:
    """Derive a reviewer input from real deterministic evidence.

    ``content_digest`` is derived through ``doublegate_sdk.submission``: hash the
    bytes once into the envelope spelling, then prefix. The SDK forbids hashing
    content twice to fill that member, so this starter does not.
    """
    evaluation = evaluate_file(manifest, body, artifact_type=artifact_type)
    content_hash = envelope_content_hash(body.read_bytes())
    return {
        "artifact_type": artifact_type,
        "content_digest": content_digest_from_content_hash(content_hash),
        "deterministic_outcome": evaluation.outcome,
        "human_review": evaluation.human_review,
        # The body never travels: findings carry fixed diagnostics and empty
        # excerpts, which is why the policy is a constant rather than a choice.
        "excerpt_policy": "withheld",
        "findings": [finding.to_dict() for finding in evaluation.findings],
    }


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_verdict_vocabularies(output: dict[str, Any]) -> tuple[str, ...]:
    """Re-check the verdict's enumerated values against the SDK's closed lists."""
    problems: list[str] = []
    for field, normalise in (("category", normalise_category),
                             ("blockers", normalise_blockers),
                             ("skipped_reason", normalise_skipped_reason)):
        if field not in output:
            continue
        value = output[field]
        if field == "skipped_reason" and value is None:
            continue               # absent skip, not a silent one; nothing was skipped
        try:
            normalise(value)
        except (TypeError, ValueError) as error:
            problems.append(f"{field}: {error.__class__.__name__}: {error}")
    return tuple(problems)


def exercise_contract(exchange: dict[str, Any], *, manifest: Path, body: Path,
                      artifact_type: str) -> dict[str, Any]:
    """Validate one offline reviewer exchange. Nothing is sent, nothing admitted."""
    input_schema, output_schema = _load(INPUT_SCHEMA), _load(OUTPUT_SCHEMA)
    given_input = exchange.get("input")
    given_output = exchange.get("output")

    derived = build_reviewer_input(manifest, body, artifact_type=artifact_type)

    report: dict[str, Any] = {
        "reviewer_stage": "unavailable",
        "contract_owner": "caller",
        "live_model_invoked": False,
        "gate_contacted": False,
        "admission_effect": "none",
        "derived_input": derived,
        "derived_input_violations": list(check_response(input_schema, derived)),
        "fixture_input_violations": list(check_response(input_schema, given_input)),
        "fixture_output_violations": list(check_response(output_schema, given_output)),
    }
    report["vocabulary_violations"] = list(
        validate_verdict_vocabularies(given_output) if isinstance(given_output, dict)
        else ("output: not an object",))
    report["valid"] = not any(report[key] for key in (
        "derived_input_violations", "fixture_input_violations",
        "fixture_output_violations", "vocabulary_violations"))
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reviewer.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--probe", action="store_true",
                        help="report whether the installed SDK publishes a reviewer stage, then exit")
    parser.add_argument("--require-stage", action="store_true",
                        help="fail with status 3 unless a reviewer stage is actually published")
    parser.add_argument("--exchange", type=Path, default=EXCHANGE,
                        help="offline reviewer exchange JSON (default: bundled fixture)")
    parser.add_argument("--manifest", type=Path, default=MANIFEST,
                        help="check manifest used to derive the reviewer input")
    parser.add_argument("--body", type=Path, default=SAMPLE_BODY,
                        help="content file the derived reviewer input describes")
    parser.add_argument("--artifact-type", default="memory",
                        choices=["memory", "skill", "script", "tool"])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    probe = probe_reviewer_stage()

    if args.probe:
        print(json.dumps(probe, sort_keys=True, indent=2))
        return OK if probe["supported"] else (STAGE_UNAVAILABLE if args.require_stage else OK)

    if args.require_stage and not probe["supported"]:
        print(json.dumps({"error": probe["diagnostic"], "probe": probe},
                         sort_keys=True, indent=2), file=sys.stderr)
        return STAGE_UNAVAILABLE

    try:
        report = exercise_contract(_load(args.exchange), manifest=args.manifest,
                                   body=args.body, artifact_type=args.artifact_type)
    except OSError as error:
        # A missing or unreadable fixture is a usage failure with a message, not
        # a stack trace. The filename is named; its contents are not read out.
        raise SystemExit(
            f"fixture not readable: {Path(error.filename or '?').name}; pass "
            "--exchange, --manifest and --body explicitly") from None
    except DoublegateError as error:
        raise SystemExit(
            f"fixture rejected by the SDK: {type(error).__name__}: {error}; pass "
            "--exchange, --manifest and --body explicitly") from None
    except ValueError as error:
        raise SystemExit(f"fixture is not valid JSON or not usable: {error}") from None
    report["probe"] = probe
    print(json.dumps(report, sort_keys=True, indent=2))
    return OK if report["valid"] else CONTRACT_VIOLATION


if __name__ == "__main__":
    sys.exit(main())
