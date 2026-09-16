"""Tests for the importable recipes in ``examples/recipes``.

Two kinds of test live here and the difference matters:

* **Offline, real data.** Everything covering ``recipes.offline_checks`` runs
  the actual SDK against the manifest and fixtures shipped in
  ``examples/starters/data``. No doubles, no monkeypatching — the outcomes,
  findings and manifest digest asserted below are what the installed SDK
  produces.

* **Unit-only, injected clients.** Everything covering
  ``recipes.gate_operations`` builds a real
  :class:`doublegate_sdk.client.GateClient` over a scripted in-process
  transport. That exercises the SDK's own response validation on real dicts,
  but it opens **no socket** and proves nothing about any deployment: not
  authorization, not admission, not a service's field set. Real-service
  acceptance is recorded separately in ``docs/api/client.md``.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from doublegate_sdk.client import GateClient, GateError

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "examples" / "starters" / "data"
MANIFEST = DATA / "release-note.gate.json"
CLEAN = DATA / "release-note.clean.md"
FLAGGED = DATA / "release-note.flagged.md"


def _load(name: str):
    """Import a recipe module by path; ``examples`` is not an installed package."""
    spec = importlib.util.spec_from_file_location(
        f"_recipes_{name}", ROOT / "examples" / "recipes" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


offline_checks = _load("offline_checks")
gate_operations = _load("gate_operations")


# --------------------------------------------------------------------------
# Offline recipes against real SDK data. No doubles anywhere in this section.
# --------------------------------------------------------------------------

def test_check_one_file_returns_real_sdk_evaluation():
    evaluation = offline_checks.check_one_file(MANIFEST, CLEAN, artifact_type="memory")
    assert evaluation.outcome == "clean"
    assert evaluation.flagged is False
    assert evaluation.findings == ()
    assert evaluation.gate_name == "release-note"
    assert evaluation.gate_version == "1.0.0"
    assert evaluation.artifact_type == "memory"
    # A clean outcome does not discharge review; the requirement rides along.
    assert evaluation.human_review == "required"
    assert len(evaluation.manifest_digest) == 64


def test_flagged_file_carries_findings_with_no_content_echoed():
    evaluation = offline_checks.check_one_file(MANIFEST, FLAGGED, artifact_type="memory")
    assert evaluation.outcome == "flagged"
    assert evaluation.flagged is True
    assert evaluation.findings, "the flagged fixture must produce at least one finding"
    for finding in evaluation.findings:
        assert finding.kind == "required-text"
        # Fixed diagnostics only: the evaluator never quotes the body back.
        assert finding.excerpt == ""


def test_check_many_files_reports_every_file_including_unevaluable(tmp_path):
    missing = tmp_path / "does-not-exist.md"
    results = offline_checks.check_many_files(
        MANIFEST, [CLEAN, FLAGGED, missing], artifact_type="memory")

    assert [result.status for result in results] == ["clean", "flagged", "unevaluated"]
    # One bad path does not abort the run or hide the verdict on the others.
    assert results[0].clean is True
    assert results[2].evaluation is None
    assert results[2].error_kind == "unreadable_file"
    assert results[2].human_review is None
    assert results[2].to_payload() == {
        "path": str(missing), "status": "unevaluated", "error_kind": "unreadable_file"}


def test_oversized_content_reports_input_too_large_not_file_too_large(tmp_path):
    # The manifest bounds content at 4096 bytes; the package's own 65,536-byte
    # bound is a different limit with a different kind.
    oversized = tmp_path / "long.md"
    oversized.write_text("Owner: x\nRollback: y\n" + "a" * 5000, encoding="utf-8")
    results = offline_checks.check_many_files(MANIFEST, [oversized], artifact_type="memory")
    assert results[0].error_kind == "input_too_large"


def test_non_utf8_content_reports_invalid_utf8(tmp_path):
    binary = tmp_path / "blob.dat"
    binary.write_bytes(b"Owner: \xff\xfe Rollback:")
    results = offline_checks.check_many_files(MANIFEST, [binary], artifact_type="memory")
    assert results[0].error_kind == "invalid_utf8"


def test_unsupported_artifact_type_is_reported_not_raised():
    results = offline_checks.check_many_files(MANIFEST, [CLEAN], artifact_type="skill")
    assert results[0].status == "unevaluated"
    assert results[0].error_kind == "unsupported_artifact_type"


def test_check_one_file_raises_rather_than_swallowing():
    from doublegate_sdk.package import PackageError

    with pytest.raises(PackageError) as caught:
        offline_checks.check_one_file(MANIFEST, ROOT / "nope.md", artifact_type="memory")
    assert caught.value.kind == "unreadable_file"


def test_summarize_checks_counts_and_restates_the_boundary():
    results = offline_checks.check_many_files(
        MANIFEST, [CLEAN, FLAGGED], artifact_type="memory")
    summary = offline_checks.summarize_checks(results)
    assert summary["counts"] == {"clean": 1, "flagged": 1, "unevaluated": 0}
    assert summary["total"] == 2
    assert summary["human_review"] == ["required"]
    assert summary["is_admission_decision"] is False
    assert summary["human_review_satisfied"] is False


def test_blocking_findings_filters_by_caller_chosen_severity():
    evaluation = offline_checks.check_one_file(MANIFEST, FLAGGED, artifact_type="memory")
    severities = {finding.severity for finding in evaluation.findings}
    # The bundled manifest emits warnings, so the default 'error' set is empty.
    assert severities == {"warning"}
    assert offline_checks.blocking_findings(evaluation) == ()
    warned = offline_checks.blocking_findings(evaluation,
                                              severities=frozenset({"warning"}))
    assert len(warned) == len(evaluation.findings)
    assert all(entry["excerpt"] == "" for entry in warned)


def test_recipe_modules_create_no_client_of_their_own():
    """A recipe takes a client; it never builds one, so it cannot connect.

    Checked against the module namespaces rather than the text: neither module
    imports ``connect``, so there is no name in scope either one could call.
    """
    for module in (offline_checks, gate_operations):
        assert not hasattr(module, "connect"), module.__name__
        assert not hasattr(module, "HttpMcpTransport"), module.__name__


# --------------------------------------------------------------------------
# Unit-only: real GateClient over a scripted in-process transport. No socket.
# --------------------------------------------------------------------------

class ScriptedTransport:
    """An ``McpTransport`` that answers from a script and records every call.

    Unit-only. It satisfies the protocol (``call(tool, arguments) -> dict``) so
    the real ``GateClient`` validation runs against these dicts, but there is no
    wire here and no gate.
    """

    def __init__(self, answers: dict[str, Any], *, allow_writes: bool = True):
        self.answers = answers
        self.allow_writes = allow_writes
        self.calls: list[tuple[str, dict[str, Any]]] = []

    #: Mirrors the write annotations in ``doublegate_sdk.client._TOOLS``.
    WRITE_TOOLS = frozenset({"doublegate.remember", "doublegate.rank",
                             "doublegate.hide", "doublegate.ban"})

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool, arguments))
        if tool in self.WRITE_TOOLS and not self.allow_writes:
            raise GateError("writes_disabled")
        answer = self.answers[tool]
        if isinstance(answer, GateError):
            raise answer
        return answer


def _client(answers: dict[str, Any], **kwargs: Any) -> tuple[GateClient, ScriptedTransport]:
    transport = ScriptedTransport(answers, **kwargs)
    return GateClient(transport), transport


def test_observe_returns_the_handle_and_sends_no_writer_identity():
    client, transport = _client({
        "doublegate.remember": {"artifact_id": "sha256:" + "a" * 64, "state": "pending"}})
    observed = gate_operations.observe(
        client, "Basalt specimens are labelled by collection date.",
        source_uri="application://lab/observation/1", trust_class="T-4")

    assert observed.artifact_id == "sha256:" + "a" * 64
    assert observed.state == "pending"
    assert observed.response["state"] == "pending"

    tool, arguments = transport.calls[0]
    assert tool == "doublegate.remember"
    assert arguments["source_uri"] == "application://lab/observation/1"
    assert arguments["trust_class"] == "T-4"
    # Writer identity is derived from the connection; sending it is refused.
    assert "identity" not in arguments and "writer" not in arguments


def test_observe_never_retries_a_failed_write():
    client, transport = _client({
        "doublegate.remember": GateError("timeout", outcome_unknown=True)})
    with pytest.raises(GateError) as caught:
        gate_operations.observe(client, "text", source_uri="application://x")
    assert caught.value.outcome_unknown is True
    # One attempt, and only one. A resend is the caller's decision, not ours.
    assert len(transport.calls) == 1


def test_observe_file_refuses_unusable_documents_before_any_call(tmp_path):
    client, transport = _client({"doublegate.remember": {}})

    binary = tmp_path / "blob.dat"
    binary.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(ValueError, match="UTF-8"):
        gate_operations.observe_file(client, binary)

    blank = tmp_path / "blank.md"
    blank.write_text("   \n\t\n", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        gate_operations.observe_file(client, blank)

    big = tmp_path / "big.md"
    big.write_text("x" * 100, encoding="utf-8")
    with pytest.raises(ValueError, match="local bound"):
        gate_operations.observe_file(client, big, max_bytes=10)

    assert transport.calls == [], "no request may be built for a refused document"


def test_observe_file_derives_a_file_uri_from_the_real_path(tmp_path):
    client, transport = _client({
        "doublegate.remember": {"artifact_id": "sha256:" + "b" * 64, "state": "pending"}})
    note = tmp_path / "note.md"
    note.write_text("Owner: ops\n", encoding="utf-8")

    observed = gate_operations.observe_file(client, note)
    assert observed.state == "pending"
    _, arguments = transport.calls[0]
    assert arguments["source_uri"] == note.resolve().as_uri()
    assert arguments["content"] == "Owner: ops\n"


def test_read_only_client_refuses_the_write_at_the_transport():
    client, transport = _client({"doublegate.remember": {}}, allow_writes=False)
    with pytest.raises(GateError) as caught:
        gate_operations.observe(client, "text", source_uri="application://x")
    assert caught.value.kind == "writes_disabled"


def test_artifact_position_reports_which_optional_fields_were_sent():
    client, _ = _client({
        "doublegate.status": {"state": "pending", "sub_level": 2}})
    position = gate_operations.artifact_position(client, "sha256:" + "c" * 64)
    assert position["state"] == "pending"
    # 'quorum' and 'blocking' were not sent; they are absent, not defaulted.
    assert position["present_optional_fields"] == ["sub_level"]
    assert "quorum" not in position["response"]


def test_artifact_history_and_field_coverage_report_absent_keys():
    events = [{"event_id": "e1", "type": "proposed", "ts": "2026-09-12T09:15:00Z"},
              {"event_id": "e2", "type": "scanned", "ts": "2026-09-12T09:15:01Z"}]
    client, _ = _client({"doublegate.why": {"artifact_id": "sha256:x", "events": events}})

    history = gate_operations.artifact_history(client, "sha256:" + "d" * 64)
    assert history == events

    coverage = gate_operations.event_field_coverage(history)
    assert coverage["event_count"] == 2
    assert coverage["event_types"] == ["proposed", "scanned"]
    assert coverage["present"] == ["event_id", "ts", "type"]
    # Not sent by this build; reported as absent rather than filled in.
    assert coverage["absent"] == ["identity", "payload", "sig"]


def test_event_field_coverage_on_an_empty_history_invents_nothing():
    client, _ = _client({"doublegate.why": {"artifact_id": "sha256:x", "events": []}})
    coverage = gate_operations.event_field_coverage(
        gate_operations.artifact_history(client, "sha256:" + "e" * 64))
    assert coverage["event_count"] == 0
    assert coverage["event_types"] == []
    assert coverage["present"] == []
    assert coverage["absent"] == list(gate_operations.WHY_EVENT_KEYS)


def test_why_without_an_events_list_is_refused_by_the_sdk():
    client, _ = _client({"doublegate.why": {"artifact_id": "sha256:x"}})
    with pytest.raises(GateError) as caught:
        gate_operations.artifact_history(client, "sha256:" + "f" * 64)
    assert caught.value.kind == "invalid_response"


def test_recall_excludes_own_pending_and_an_empty_answer_is_not_a_failure():
    client, transport = _client({"doublegate.recall": {"results": []}})
    assert gate_operations.recall_answers(client, "how are specimens labelled?") == []
    _, arguments = transport.calls[0]
    assert arguments["include_own_pending"] is False
    assert arguments["k"] == 5


def test_waiting_for_review_returns_metadata_only():
    client, transport = _client({
        "doublegate.pending": {"pending": [{"artifact_id": "sha256:x", "state": "pending"}]}})
    waiting = gate_operations.waiting_for_review(client, limit=20)
    assert waiting == [{"artifact_id": "sha256:x", "state": "pending"}]
    assert transport.calls[0][1] == {"limit": 20}
    assert all("content" not in item for item in waiting)


def test_read_only_snapshot_attempts_both_reads_and_keeps_failures_as_data():
    client, transport = _client({
        "doublegate.status": {"state": "held"},
        "doublegate.why": GateError("unavailable")})
    snapshot = gate_operations.read_only_snapshot(client, "sha256:" + "1" * 64)

    assert snapshot["mutated"] is False
    assert snapshot["status"]["state"] == "held"
    # The status answer is not hidden by the history failure.
    assert snapshot["history"]["failed"]["kind"] == "unavailable"
    assert snapshot["history"]["failed"]["endpoint_reached"] is False
    assert snapshot["complete"] is False
    assert [tool for tool, _ in transport.calls] == ["doublegate.status", "doublegate.why"]


def test_a_refusal_still_counts_as_the_endpoint_answering():
    client, _ = _client({"doublegate.status": GateError("unauthorized", 401),
                         "doublegate.why": GateError("unauthorized", 401)})
    snapshot = gate_operations.read_only_snapshot(client, "sha256:" + "2" * 64)
    assert snapshot["complete"] is False
    assert snapshot["endpoint_reached"] is True


def test_call_or_failure_turns_a_gate_error_into_a_value():
    client, _ = _client({"doublegate.recall": {"results": [{"artifact_id": "sha256:x"}]}})
    answers, failure = gate_operations.call_or_failure(
        gate_operations.recall_answers, client, "labelling")
    assert failure is None and answers == [{"artifact_id": "sha256:x"}]

    client, _ = _client({"doublegate.recall": GateError("remote_error", 500)})
    answers, failure = gate_operations.call_or_failure(
        gate_operations.recall_answers, client, "labelling")
    assert answers is None
    assert failure.kind == "remote_error" and failure.code == 500
    assert failure.outcome_unknown is False


def test_call_or_failure_does_not_swallow_a_caller_bug():
    client, _ = _client({"doublegate.recall": {"results": []}})
    with pytest.raises(ValueError):
        gate_operations.call_or_failure(gate_operations.recall_answers, client, "")


def test_server_catalog_refuses_a_transport_without_negotiation():
    client, _ = _client({})
    with pytest.raises(TypeError, match="negotiation"):
        gate_operations.server_catalog(client)


def test_server_catalog_reads_the_servers_own_catalog():
    class Negotiating(ScriptedTransport):
        def discover(self):
            return {"serverInfo": {"name": "doublegate"}}

        def tool_names(self):
            return ("doublegate.recall", "doublegate.status")

    client = GateClient(Negotiating({}))
    catalog = gate_operations.server_catalog(client)
    assert catalog["discover"] == {"serverInfo": {"name": "doublegate"}}
    assert catalog["tools"] == ["doublegate.recall", "doublegate.status"]


# --------------------------------------------------------------------------
# The documented snippets must actually run.
# --------------------------------------------------------------------------

def test_documented_python_snippets_execute(tmp_path):
    """Run the shape the use-case docs show, end to end, offline.

    This is the check that keeps the docs from drifting: the offline half runs
    the real SDK, and the gate half runs the real ``GateClient`` over the
    scripted transport above.
    """
    from doublegate_sdk import evaluate_file

    evaluation = evaluate_file(MANIFEST, FLAGGED, artifact_type="memory")
    assert evaluation.flagged
    assert [finding.kind for finding in evaluation.findings] == ["required-text"]
    assert evaluation.human_review == "required"

    client, _ = _client({
        "doublegate.remember": {"artifact_id": "sha256:" + "9" * 64, "state": "pending"},
        "doublegate.status": {"state": "pending", "quorum": {"have": 0, "need": 2}},
        "doublegate.recall": {"results": []},
    })
    observed = gate_operations.observe(client, "one observation",
                                       source_uri="application://docs/example")
    position = gate_operations.artifact_position(client, observed.artifact_id)
    assert position["state"] == "pending"
    assert position["present_optional_fields"] == ["quorum"]
    assert gate_operations.recall_answers(client, "one observation") == []
