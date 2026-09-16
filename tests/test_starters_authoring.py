"""The three starter apps this branch owns: checks, reviewer, correction.

These tests are safety tests as much as behaviour tests. Two of the three
starters exist because the SDK does *not* publish the stage the user asked for,
and the value of those starters is entirely in what they refuse to do. So the
assertions below check refusals with the same weight as results:

* no starter opens a socket (every test runs with the transport poisoned);
* the reviewer starter never claims a reviewer stage exists, never invokes a
  model and never admits anything;
* the correction starter never calls a write-annotated tool, and in particular
  never calls ``doublegate.hide`` and reports it as a withdrawal.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STARTERS = ROOT / "examples/starters"
DATA = STARTERS / "data"

CHECKS = STARTERS / "checks.py"
REVIEWER = STARTERS / "reviewer.py"
CORRECTION = STARTERS / "correction.py"

ALL_STARTERS = (CHECKS, REVIEWER, CORRECTION)


def run(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(script), *args],
                          capture_output=True, text=True, cwd=ROOT)


# --------------------------------------------------------------------------
# every starter is an executable program
# --------------------------------------------------------------------------

@pytest.mark.parametrize("script", ALL_STARTERS, ids=lambda p: p.stem)
def test_help_is_executable(script):
    result = run(script, "--help")
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout


@pytest.mark.parametrize("script", ALL_STARTERS, ids=lambda p: p.stem)
def test_starter_takes_no_secret_argument(script):
    """A credential on a command line lands in shell history and process lists."""
    text = script.read_text(encoding="utf-8")
    result = run(script, "--help")
    for forbidden in ("--token", "--password", "--secret", "--api-key"):
        assert forbidden not in result.stdout
    assert "add_argument('--token'" not in text and 'add_argument("--token"' not in text


@pytest.mark.parametrize("script", ALL_STARTERS, ids=lambda p: p.stem)
def test_no_socket_paths_or_fake_servers(script):
    text = script.read_text(encoding="utf-8")
    for forbidden in ("AF_UNIX", "socketserver", "http.server", "HTTPServer",
                      "localhost:", "unix://", "/tmp/"):
        assert forbidden not in text, f"{script.name} mentions {forbidden}"


# --------------------------------------------------------------------------
# checks.py — the one that fully works
# --------------------------------------------------------------------------

def test_checks_default_run_reports_one_clean_and_one_flagged():
    result = run(CHECKS)
    assert result.returncode == 1, result.stderr        # flagged => non-zero
    payload = json.loads(result.stdout)
    assert payload["counts"] == {"clean": 1, "flagged": 1, "unevaluated": 0}
    assert payload["is_admission_decision"] is False
    assert payload["human_review_satisfied"] is False


def test_checks_clean_file_alone_exits_zero():
    result = run(CHECKS, str(DATA / "release-note.clean.md"))
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["counts"]["clean"] == 1
    assert payload["files"][0]["findings"] == []


def test_checks_carries_human_review_into_every_result():
    """A clean check is evidence; it does not discharge the manifest's review."""
    payload = json.loads(run(CHECKS).stdout)
    for entry in payload["files"]:
        assert entry["human_review"] == "required"


def test_checks_findings_never_quote_content():
    payload = json.loads(run(CHECKS).stdout)
    flagged = next(e for e in payload["files"] if e["status"] == "flagged")
    assert flagged["findings"]
    for finding in flagged["findings"]:
        assert finding["excerpt"] == ""


def test_checks_unreadable_file_is_reported_not_fatal(tmp_path):
    """One bad file must not hide the verdict on the rest."""
    result = run(CHECKS, str(DATA / "release-note.clean.md"), str(tmp_path / "absent.md"))
    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["counts"] == {"clean": 1, "flagged": 0, "unevaluated": 1}
    bad = next(e for e in payload["files"] if e["status"] == "unevaluated")
    assert bad["kind"] and "absent.md" not in bad["kind"]   # fixed diagnostic, no content


def test_checks_text_format_is_human_readable():
    result = run(CHECKS, "--format", "text")
    assert result.returncode == 1
    assert "clean=1 flagged=1" in result.stdout
    assert "no outcome here is an admission decision" in result.stdout


def test_checks_rejects_unknown_artifact_type():
    assert run(CHECKS, "--artifact-type", "nonsense").returncode == 2


# --------------------------------------------------------------------------
# reviewer.py — the honest blocked one
# --------------------------------------------------------------------------

def test_reviewer_probe_reports_the_stage_as_unavailable():
    result = run(REVIEWER, "--probe")
    assert result.returncode == 0, result.stderr
    probe = json.loads(result.stdout)
    assert probe["supported"] is False
    assert probe["python_symbols"] == []
    assert probe["client_tier_tools"] == []
    assert probe["diagnostic"].startswith("reviewer_stage_unavailable")


def test_reviewer_candidate_is_not_a_verified_integration(monkeypatch):
    """A similarly named object must never activate a nonexistent binding."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("_starter_reviewer", REVIEWER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.probe_reviewer_stage()["supported"] is False

    import doublegate_sdk
    monkeypatch.setattr(doublegate_sdk, "reviewer", object(), raising=False)
    flipped = module.probe_reviewer_stage()
    assert flipped["supported"] is False
    assert flipped["candidate_detected"] is True
    assert "doublegate_sdk.reviewer" in flipped["python_symbols"]


def test_reviewer_require_stage_fails_loudly():
    result = run(REVIEWER, "--require-stage")
    assert result.returncode == 3
    assert "reviewer_stage_unavailable" in result.stderr
    assert result.stdout == ""                                # nothing claimed on stdout


def test_reviewer_exercises_the_local_contract_and_passes():
    result = run(REVIEWER)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["valid"] is True
    assert report["derived_input_violations"] == []
    assert report["fixture_input_violations"] == []
    assert report["fixture_output_violations"] == []
    assert report["vocabulary_violations"] == []


def test_reviewer_never_claims_a_model_ran_or_a_gate_was_touched():
    report = json.loads(run(REVIEWER).stdout)
    assert report["reviewer_stage"] == "unavailable"
    assert report["contract_owner"] == "caller"
    assert report["live_model_invoked"] is False
    assert report["gate_contacted"] is False
    assert report["admission_effect"] == "none"


def test_reviewer_derived_input_carries_real_deterministic_evidence():
    report = json.loads(run(REVIEWER).stdout)
    derived = report["derived_input"]
    assert derived["deterministic_outcome"] == "flagged"
    assert derived["human_review"] == "required"
    assert derived["content_digest"].startswith("sha256:")
    assert len(derived["content_digest"]) == len("sha256:") + 64
    assert derived["excerpt_policy"] == "withheld"
    assert all(f["excerpt"] == "" for f in derived["findings"])


def test_reviewer_schemas_declare_themselves_caller_owned():
    """A fixture schema must not be mistakable for a published product contract."""
    for name in ("reviewer-input.schema.json", "reviewer-output.schema.json"):
        schema = json.loads((DATA / name).read_text(encoding="utf-8"))
        assert "caller-owned" in schema["title"]
        assert "not a product contract" in schema["title"]
        assert "no gate validates it" in schema["description"]


def test_reviewer_output_schema_reuses_the_sdk_closed_vocabularies():
    from doublegate_sdk.reasons import CATEGORIES
    from doublegate_sdk.skipped import SKIPPED_REASONS
    from doublegate_sdk.vocabulary import BLOCKERS

    schema = json.loads((DATA / "reviewer-output.schema.json").read_text(encoding="utf-8"))
    props = schema["properties"]
    assert set(props["category"]["enum"]) - {None} == set(CATEGORIES)
    assert set(props["blockers"]["items"]["enum"]) == set(BLOCKERS)
    assert set(props["skipped_reason"]["enum"]) - {None} == set(SKIPPED_REASONS)


def test_reviewer_rejects_a_verdict_outside_the_closed_vocabulary(tmp_path):
    exchange = json.loads((DATA / "reviewer-exchange.json").read_text(encoding="utf-8"))
    exchange["output"]["blockers"] = ["awaiting_signer"]       # a local mode name, not a blocker
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(exchange), encoding="utf-8")

    result = run(REVIEWER, "--exchange", str(path))
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["valid"] is False
    assert report["fixture_output_violations"] or report["vocabulary_violations"]


def test_reviewer_rejects_a_verdict_with_no_blockers(tmp_path):
    exchange = json.loads((DATA / "reviewer-exchange.json").read_text(encoding="utf-8"))
    exchange["output"]["blockers"] = []
    path = tmp_path / "empty.json"
    path.write_text(json.dumps(exchange), encoding="utf-8")

    result = run(REVIEWER, "--exchange", str(path))
    assert result.returncode == 1
    assert json.loads(result.stdout)["valid"] is False


# --------------------------------------------------------------------------
# correction.py — the other honest blocked one
# --------------------------------------------------------------------------

def test_correction_reports_withdrawal_as_unavailable():
    result = run(CORRECTION)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["withdrawal"] == "unavailable"
    assert payload["remote_mutation_attempted"] is False
    assert payload["probe"]["supported"] is False
    assert payload["probe"]["candidate_detected"] is False


def test_correction_candidate_name_is_not_a_withdrawal_binding(monkeypatch, capsys):
    """A matching tool or operation NAME must never report withdrawal as supported.

    There is no code path in this starter that withdraws anything, so any
    ``supported: True`` would be a claim about an effect that cannot happen. The
    sibling reviewer starter is held to the same rule.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("_starter_correction", CORRECTION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.probe_withdrawal_support()["supported"] is False

    import doublegate_sdk.client as client_module
    real_describe = client_module.describe_client

    def with_a_tool_name():
        described = dict(real_describe())
        described["mcp"] = dict(described["mcp"])
        described["mcp"]["tools"] = sorted([*described["mcp"]["tools"], "doublegate.supersede"])
        return described

    def with_an_operation_name():
        described = dict(real_describe())
        described["operations"] = {**described["operations"], "delete": {"writes": True}}
        return described

    for fake, field, expected in ((with_a_tool_name, "tools", "doublegate.supersede"),
                                  (with_an_operation_name, "operations", "delete")):
        monkeypatch.setattr(client_module, "describe_client", fake)
        probe = module.probe_withdrawal_support()
        assert probe["supported"] is False
        assert probe["candidate_detected"] is True
        assert expected in probe[field]           # reported as a hint, not acted on


def test_correction_withdraw_still_refuses_when_a_candidate_name_exists(monkeypatch, capsys):
    """The end-to-end refusal, not just the probe: --withdraw must still exit 3."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("_starter_correction_cli", CORRECTION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    import doublegate_sdk.client as client_module
    real_describe = client_module.describe_client

    def with_a_tool_name():
        described = dict(real_describe())
        described["mcp"] = dict(described["mcp"])
        described["mcp"]["tools"] = sorted([*described["mcp"]["tools"], "doublegate.supersede"])
        return described

    monkeypatch.setattr(client_module, "describe_client", with_a_tool_name)
    code = module.main(["--withdraw", "--artifact-id", "sha256:" + "a" * 64])
    captured = capsys.readouterr()

    assert code == 3, "an unsupported withdrawal must fail, not exit 0"
    assert captured.out == "", "nothing may be claimed on stdout for a refused withdrawal"
    payload = json.loads(captured.err)
    assert payload["withdrawal"] == "unavailable"
    assert payload["withdrawal_requested"] is True
    assert payload["remote_mutation_attempted"] is False
    assert payload["probe"]["candidate_detected"] is True


def test_correction_diagnostic_says_a_name_does_not_establish_support():
    payload = json.loads(run(CORRECTION).stdout)
    assert "Candidate names alone do not establish compatibility" in \
        payload["probe"]["diagnostic"]


def test_correction_exposes_a_timeout_like_every_other_network_starter():
    """diagnostics tells the operator to raise --timeout; it must exist here too."""
    result = run(CORRECTION, "--help")
    assert "--timeout" in result.stdout
    text = CORRECTION.read_text(encoding="utf-8")
    assert "timeout=15\n" not in text and "timeout=15," not in text


def test_correction_missing_fixture_is_a_usage_failure_not_a_traceback(tmp_path):
    result = run(CORRECTION, "--body", str(tmp_path / "absent.md"))
    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert "fixture not readable" in result.stderr
    assert "absent.md" in result.stderr


def test_correction_withdraw_exits_three_and_claims_no_effect():
    result = run(CORRECTION, "--withdraw")
    assert result.returncode == 3
    assert result.stdout == ""
    payload = json.loads(result.stderr)
    assert payload["withdrawal_requested"] is True
    assert payload["error"].startswith("withdrawal_unavailable")
    assert payload["remote_mutation_attempted"] is False
    # the blocked run still hands back the work it could do
    assert payload["correction_request"]["request_id"].startswith("sha256:")


def test_correction_never_treats_hide_or_rank_as_withdrawal():
    text = CORRECTION.read_text(encoding="utf-8")
    for tool in ("doublegate.hide", "doublegate.rank", "doublegate.ban"):
        # named only as data explaining the refusal, never called
        assert f"call('{tool}'" not in text and f'call("{tool}"' not in text
    assert "allow_writes=False" in text
    assert "allow_writes=True" not in text

    payload = json.loads(run(CORRECTION).stdout)
    explained = payload["probe"]["write_tools_that_are_not_withdrawal"]
    assert set(explained) == {"doublegate.hide", "doublegate.rank", "doublegate.ban"}


def test_correction_request_is_bound_to_artifact_id_and_provenance():
    result = run(CORRECTION, "--artifact-id", "sha256:" + "a" * 64,
                 "--reason-category", "quality")
    assert result.returncode == 0, result.stderr
    document = json.loads(result.stdout)["correction_request"]["document"]
    assert document["target_artifact_id"] == "sha256:" + "a" * 64
    assert document["reason_category"] == "quality"
    assert document["provenance"]["observed_by"]
    assert document["remote_effect"] == "none"
    assert document["carried_by"] == "human"


def test_correction_request_id_is_deterministic_and_content_bound():
    first = json.loads(run(CORRECTION, "--artifact-id", "sha256:" + "b" * 64).stdout)
    again = json.loads(run(CORRECTION, "--artifact-id", "sha256:" + "b" * 64).stdout)
    other = json.loads(run(CORRECTION, "--artifact-id", "sha256:" + "c" * 64).stdout)

    same = first["correction_request"]["request_id"]
    assert same == again["correction_request"]["request_id"]
    assert same != other["correction_request"]["request_id"]


def test_correction_rejects_a_reason_category_outside_the_five():
    assert run(CORRECTION, "--reason-category", "vibes").returncode == 2


def test_correction_stays_offline_without_an_endpoint():
    payload = json.loads(run(CORRECTION).stdout)
    assert "skipped" in payload["inspection"]
    assert "offline" in payload["inspection"]["skipped"]


def test_correction_inspection_is_read_only_by_construction():
    """The starter must build its client with writes off, not merely avoid them."""
    from doublegate_sdk.client import GateError, HttpMcpTransport

    transport = HttpMcpTransport("https://gate.invalid/mcp", allow_writes=False)
    for tool in ("doublegate.hide", "doublegate.rank", "doublegate.ban",
                 "doublegate.remember"):
        with pytest.raises(GateError) as caught:
            transport.call(tool, {})
        assert caught.value.kind == "writes_disabled"      # refused locally, never sent


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

def test_fixture_manifest_is_valid_and_declares_no_egress_or_secrets():
    from doublegate_sdk.package import load_package

    loaded = load_package(DATA / "release-note.gate.json")
    assert loaded.package.name == "release-note"
    assert loaded.package.human_review == "required"
    assert loaded.package.required_text == ("Owner:", "Rollback:")

    raw = json.loads((DATA / "release-note.gate.json").read_text(encoding="utf-8"))
    assert raw["egress"] == [] and raw["secret_refs"] == []


def test_no_fixture_carries_a_credential_or_endpoint():
    for path in sorted(DATA.iterdir()):
        text = path.read_text(encoding="utf-8").lower()
        for forbidden in ("bearer ", "authorization", "api_key", "password",
                          "https://gate.", "token"):
            assert forbidden not in text, f"{path.name} mentions {forbidden}"
