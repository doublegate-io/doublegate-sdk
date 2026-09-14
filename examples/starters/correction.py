"""Starter: prepare a correction, and report honestly that withdrawal is unavailable.

**The honest headline.** This SDK cannot withdraw, retract, delete or correct an
admitted artifact. The client tier serves nine tools; none of them removes an
artifact or replaces its content, and there is no delete RPC to call. Three
tools *write* — ``doublegate.rank``, ``doublegate.hide`` and ``doublegate.ban``
— and this starter will not touch them, because:

* ``hide`` changes what a reader is served. The artifact, its content and its
  ledger remain exactly where they were. Calling it "withdrawal" would tell an
  operator something was removed when nothing was.
* ``rank`` reorders. It is not a statement about correctness.
* ``ban`` is an authority action about a source, not a correction of one record.

So the starter does the two things that *are* honestly available, and blocks
loudly on the third:

1. **Prepare** a local, explicit correction request bound to the artifact id and
   to caller-supplied provenance, canonicalized with
   ``doublegate_sdk.submission.canonical_bytes`` so the request has one byte
   string and a stable local ``request_id``. This is a work item for whoever
   carries the correction by hand. It is not sent anywhere.
2. **Inspect** the artifact's current position with ``status`` and ``why``, over
   a client built with ``allow_writes=False``, so the transport itself refuses a
   write-annotated tool even if a caller reaches past this module. Inspection is
   opt-in (``--endpoint``); without it the starter is fully offline.
3. **Report** withdrawal as ``unavailable`` with a fixed diagnostic, and exit
   non-zero when the caller asked for withdrawal (``--withdraw``). Nothing
   claims an effect that did not happen.

Credentials: read from the ``DOUBLEGATE_TOKEN`` environment variable only. No
token argument exists, and no token is printed.

Usage::

    python examples/starters/correction.py --help
    python examples/starters/correction.py --artifact-id sha256:<64 hex>
    python examples/starters/correction.py --artifact-id ... --endpoint https://gate.example.org/mcp
    python examples/starters/correction.py --artifact-id ... --withdraw    # exits 3

Exit status: ``0`` the correction request was prepared, ``2`` inspection failed,
``3`` withdrawal was requested and is not supported by this SDK.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from doublegate_sdk.submission import canonical_bytes, submission_artifact_id

DATA = Path(__file__).resolve().parent / "data"
DEFAULT_PROVENANCE = DATA / "correction-provenance.json"
DEFAULT_BODY = DATA / "correction-body.md"
DEFAULT_ARTIFACT_ID = "sha256:" + "0" * 64

OK, INSPECTION_FAILED, WITHDRAWAL_UNSUPPORTED = 0, 2, 3

#: Write tools the client tier serves, and why each is *not* a withdrawal. Kept
#: as data so the refusal is inspectable rather than a comment in a code path.
WRITE_TOOLS_THAT_ARE_NOT_WITHDRAWAL = {
    "doublegate.rank": "reorders what is served; says nothing about correctness",
    "doublegate.hide": "changes visibility; the artifact, its content and its ledger remain",
    "doublegate.ban": "an authority action about a source, not a correction of one record",
}

WITHDRAWAL_DIAGNOSTIC = (
    "withdrawal_unavailable: this SDK exposes no tool that withdraws, retracts, "
    "deletes or replaces an admitted artifact. doublegate.hide is visibility, not "
    "removal, and this starter refuses to call it as if it were. Candidate names "
    "alone do not establish compatibility: a matching tool or operation name is "
    "reported for inspection under candidate_detected and does not flip this "
    "result. The prepared request below has had no remote effect."
)


def probe_withdrawal_support() -> dict[str, Any]:
    """Report whether this starter has a verified withdrawal binding. No network.

    It does not, and a name cannot change that. Candidate names are reported as
    diagnostic hints under ``candidate_detected``; ``supported`` is a constant
    ``False`` because there is no code path in this starter that withdraws
    anything, so any other value would be a claim about an effect that cannot
    happen. Mirrors ``reviewer.probe_reviewer_stage``.
    """
    from doublegate_sdk.client import describe_client

    description = describe_client()
    operations = description["operations"]
    candidates = ("withdraw", "retract", "correct", "delete", "remove", "supersede")
    found_ops = sorted(name for name in operations if name in candidates)
    found_tools = sorted(tool for tool in description["mcp"]["tools"]
                         if any(word in tool for word in candidates))
    return {
        "supported": False,
        "candidate_detected": bool(found_ops or found_tools),
        "operations": found_ops,
        "tools": found_tools,
        "available_operations": sorted(operations),
        "available_tools": description["mcp"]["tools"],
        "write_tools_that_are_not_withdrawal": WRITE_TOOLS_THAT_ARE_NOT_WITHDRAWAL,
        "diagnostic": WITHDRAWAL_DIAGNOSTIC,
    }


def prepare_correction_request(artifact_id: str, *, body: Path, provenance: Path,
                               reason_category: str | None = None) -> dict[str, Any]:
    """Build a local correction request bound to an artifact id and its provenance.

    The request is canonicalized with the SDK's submission profile, so it has one
    byte string and one derived ``request_id``. That id identifies *this local
    request document*; it is not a gate-issued id and confers nothing.
    """
    if not isinstance(artifact_id, str) or not artifact_id.strip():
        raise ValueError("artifact_id must be a nonempty string")
    if reason_category is not None:
        from doublegate_sdk.reasons import normalise_category
        normalise_category(reason_category)      # raises on anything outside the five

    document: dict[str, Any] = {
        "kind": "local-correction-request",
        "target_artifact_id": artifact_id,
        "proposed_body": body.read_text(encoding="utf-8"),
        "provenance": json.loads(provenance.read_text(encoding="utf-8")),
        "carried_by": "human",
        "remote_effect": "none",
    }
    if reason_category is not None:
        document["reason_category"] = reason_category
    return {
        "request_id": submission_artifact_id(document),
        "canonical_bytes_length": len(canonical_bytes(document)),
        "document": document,
    }


#: Error kinds that mean the endpoint was never reached. Anything else — an
#: `unauthorized` or `forbidden` reply, for instance — is the endpoint answering,
#: so reporting it as unreachable would be untrue.
_NOT_REACHED_KINDS = frozenset({"unavailable", "timeout", "redirect_refused"})


def inspect_artifact(endpoint: str, artifact_id: str, *,
                     allow_insecure_loopback: bool = False,
                     timeout: float = 15.0) -> dict[str, Any]:
    """Read the artifact's current position. Read-only by construction.

    ``allow_writes`` stays ``False``, so the transport refuses a write-annotated
    tool itself. The token comes from the environment and is never returned.

    ``inspection_succeeded`` says whether both reads returned. ``endpoint_reached``
    is a separate, narrower claim: it stays true when the gate answered at all,
    including when it answered with a refusal.
    """
    from doublegate_sdk.client import GateError, connect

    client = connect(endpoint, token=os.environ.get("DOUBLEGATE_TOKEN"),
                     allow_writes=False,
                     allow_insecure_loopback=allow_insecure_loopback, timeout=timeout)
    observed: dict[str, Any] = {"inspection_succeeded": True, "endpoint_reached": True,
                                "mutated": False}
    for name in ("status", "why"):
        try:
            observed[name] = getattr(client, name)(artifact_id)
        except GateError as error:
            observed[name] = {"error": error.kind, "code": error.code}
            observed["inspection_succeeded"] = False
            if error.kind in _NOT_REACHED_KINDS:
                observed["endpoint_reached"] = False
    return observed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="correction.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--artifact-id", default=DEFAULT_ARTIFACT_ID,
                        help="artifact the correction targets (default: a placeholder id)")
    parser.add_argument("--body", type=Path, default=DEFAULT_BODY,
                        help="proposed corrected text")
    parser.add_argument("--provenance", type=Path, default=DEFAULT_PROVENANCE,
                        help="JSON provenance recorded by the operator")
    parser.add_argument("--reason-category", default=None,
                        choices=["off-scope", "quality", "policy", "duplicate", "unreadable"],
                        help="optional reason category from the SDK's closed list")
    parser.add_argument("--endpoint", default=None,
                        help="optional read-only MCP URL for a status/why inspection; "
                             "omit to stay fully offline")
    parser.add_argument("--allow-insecure-loopback", action="store_true",
                        help="permit http:// against a loopback host when inspecting")
    parser.add_argument("--timeout", type=float, default=15.0,
                        help="total deadline per inspection request")
    parser.add_argument("--withdraw", action="store_true",
                        help="ask for withdrawal; this SDK cannot do it and the run exits 3")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    probe = probe_withdrawal_support()

    try:
        correction_request = prepare_correction_request(
            args.artifact_id, body=args.body, provenance=args.provenance,
            reason_category=args.reason_category)
    except OSError as error:
        # A missing or unreadable fixture is a usage failure with a message, not
        # a stack trace. The filename is named; its contents are not read out.
        raise SystemExit(
            f"fixture not readable: {Path(error.filename or '?').name}; pass "
            "--body and --provenance explicitly") from None
    except ValueError as error:
        raise SystemExit(f"invalid provenance or artifact id: {error}") from None

    result: dict[str, Any] = {
        "withdrawal": "supported" if probe["supported"] else "unavailable",
        "probe": probe,
        "remote_mutation_attempted": False,
        "correction_request": correction_request,
        "next_step": (
            "Carry the prepared request to whoever holds correction authority for "
            "this register. This starter has changed nothing remotely."),
    }

    status = OK
    if args.endpoint:
        result["inspection"] = inspect_artifact(
            args.endpoint, args.artifact_id,
            allow_insecure_loopback=args.allow_insecure_loopback,
            timeout=args.timeout)
        if not result["inspection"]["inspection_succeeded"]:
            status = INSPECTION_FAILED
    else:
        result["inspection"] = {"skipped": "no --endpoint given; run stayed offline"}

    if args.withdraw and not probe["supported"]:
        result["withdrawal_requested"] = True
        result["error"] = probe["diagnostic"]
        print(json.dumps(result, sort_keys=True, indent=2), file=sys.stderr)
        return WITHDRAWAL_UNSUPPORTED

    print(json.dumps(result, sort_keys=True, indent=2))
    return status


if __name__ == "__main__":
    sys.exit(main())
