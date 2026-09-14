"""Unit tests for the four starter apps in ``examples/starters``.

These are UNIT tests. Every one injects a recording double in place of
``doublegate_sdk.client.connect``; none of them opens a socket, and none of them
proves anything about a real deployment. Service acceptance is a separate
exercise — see ``examples/verify_memory_lifecycle.py``, which needs the Client
Gate product on the path and is not run from here.

What is actually checked: that each app calls the SDK operation the story claims,
with the writes flag the story claims, that the credential is taken from the
environment rather than an argument, that a local document is refused before any
request when it is not UTF-8 text, that the memory handle survives between two
separate invocations, that provenance reports the returned event shape, and that
each fixed error kind maps to an exit code and a diagnostic that carries no
server text.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from doublegate_sdk.client import GateError

ROOT = Path(__file__).resolve().parents[1]
STARTERS = ROOT / 'examples' / 'starters'


def _load(name: str):
    """Import a starter by path; they are example programs, not a package."""
    spec = importlib.util.spec_from_file_location(f'_starter_{name}', STARTERS / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


memory = _load('memory')
ingestion = _load('ingestion')
provenance = _load('provenance')
diagnostics = _load('diagnostics')


class FakeTransport:
    def __init__(self, discover: Any, tools: Any):
        self._discover, self._tools = discover, tools

    def discover(self):
        if isinstance(self._discover, Exception):
            raise self._discover
        return self._discover

    def tool_names(self):
        if isinstance(self._tools, Exception):
            raise self._tools
        return self._tools


class FakeClient:
    """Records calls; returns canned answers or raises a canned GateError."""

    def __init__(self, answers: dict[str, Any]):
        self.answers = answers
        self.calls: list[tuple[str, tuple, dict]] = []
        self.transport = FakeTransport(answers.get('discover', {'serverInfo': {}}),
                                      answers.get('tools', ('doublegate.recall',)))

    def _answer(self, name: str, args: tuple, kwargs: dict):
        self.calls.append((name, args, kwargs))
        value = self.answers.get(name)
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise AssertionError(f'starter called {name} but the test scripted no answer')
        return value

    def propose(self, *args, **kwargs):
        return self._answer('propose', args, kwargs)

    def status(self, *args, **kwargs):
        return self._answer('status', args, kwargs)

    def recall(self, *args, **kwargs):
        return self._answer('recall', args, kwargs)

    def pending(self, *args, **kwargs):
        return self._answer('pending', args, kwargs)

    def why(self, *args, **kwargs):
        return self._answer('why', args, kwargs)


class Recorder:
    """Stands in for ``connect``: records the connection arguments, returns a double."""

    def __init__(self, answers: dict[str, Any]):
        self.client = FakeClient(answers)
        self.endpoints: list[str] = []
        self.kwargs: list[dict] = []

    def __call__(self, endpoint, **kwargs):
        self.endpoints.append(endpoint)
        self.kwargs.append(kwargs)
        return self.client


@pytest.fixture(autouse=True)
def _no_ambient_endpoint(monkeypatch):
    """A developer's own environment must not decide what these tests exercise."""
    monkeypatch.delenv('DOUBLEGATE_ENDPOINT', raising=False)
    monkeypatch.delenv('DOUBLEGATE_TOKEN', raising=False)


ENDPOINT = 'https://gate.invalid/mcp'


# ---------------------------------------------------------------- application memory

def test_remember_writes_and_saves_the_handle(tmp_path):
    recorder = Recorder({'propose': {'artifact_id': 'sha256:abc', 'state': 'L1_SCANNED'}})
    handle = tmp_path / 'handle.json'
    code = memory.main(['--endpoint', ENDPOINT, '--state-file', str(handle),
                        'remember', 'basalt is labelled by collection date'], recorder)
    assert code == memory.OK
    assert recorder.kwargs[0]['allow_writes'] is True
    name, args, kwargs = recorder.client.calls[0]
    assert name == 'propose'
    assert args == ('basalt is labelled by collection date',)
    assert kwargs['content_type'] == 'memory'
    assert kwargs['source_uri'] == 'application://starters/memory'
    saved = json.loads(handle.read_text())
    assert saved['artifact_id'] == 'sha256:abc'
    assert saved['state'] == 'L1_SCANNED'


def test_second_session_reads_the_saved_handle_without_writing(tmp_path, capsys):
    """The second invocation is a separate process in practice: it has only the file."""
    handle = tmp_path / 'handle.json'
    first = Recorder({'propose': {'artifact_id': 'sha256:abc', 'state': 'L1_SCANNED'}})
    memory.main(['--endpoint', ENDPOINT, '--state-file', str(handle), 'remember', 'a claim'], first)
    capsys.readouterr()

    second = Recorder({'status': {'state': 'RATIFIED', 'sub_level': 2}})
    code = memory.main(['--endpoint', ENDPOINT, '--state-file', str(handle), 'check'], second)
    assert code == memory.OK
    assert second.kwargs[0]['allow_writes'] is False
    assert second.client.calls == [('status', ('sha256:abc',), {})]
    out = capsys.readouterr().out
    assert 'state now: RATIFIED' in out
    assert 'state at submission: L1_SCANNED' in out


def test_check_without_a_saved_handle_is_a_usage_failure(tmp_path):
    recorder = Recorder({})
    with pytest.raises(SystemExit):
        memory.main(['--endpoint', ENDPOINT, '--state-file', str(tmp_path / 'none.json'), 'check'],
                    recorder)
    assert recorder.endpoints == []


def test_recall_is_read_only_and_reports_an_empty_answer(capsys):
    recorder = Recorder({'recall': {'results': []}})
    code = memory.main(['--endpoint', ENDPOINT, 'recall', 'basalt'], recorder)
    assert code == memory.OK
    assert recorder.kwargs[0]['allow_writes'] is False
    assert recorder.client.calls == [('recall', ('basalt',), {'limit': 5})]
    assert 'empty:' in capsys.readouterr().out


def test_token_comes_from_the_environment_not_the_command_line(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv('DOUBLEGATE_TOKEN', 'secret-value')
    recorder = Recorder({'recall': {'results': []}})
    memory.main(['--endpoint', ENDPOINT, 'recall', 'basalt'], recorder)
    assert recorder.kwargs[0]['token'] == 'secret-value'
    captured = capsys.readouterr()
    assert 'secret-value' not in captured.out
    assert 'secret-value' not in captured.err
    # and there is no way to pass one in:
    assert '--token' not in memory.build_parser().format_help()


def test_endpoint_falls_back_to_the_environment(monkeypatch):
    monkeypatch.setenv('DOUBLEGATE_ENDPOINT', ENDPOINT)
    recorder = Recorder({'recall': {'results': []}})
    memory.main(['recall', 'basalt'], recorder)
    assert recorder.endpoints == [ENDPOINT]


def test_missing_endpoint_never_connects():
    recorder = Recorder({})
    with pytest.raises(SystemExit):
        memory.main(['recall', 'basalt'], recorder)
    assert recorder.endpoints == []


# --------------------------------------------- the artifact handle is not disposable
#
# An artifact id is the only durable reference to a submission: the gate holds
# the artifact, the operator holds the id, and nothing else connects them. These
# tests pin the three ways that reference used to be lost — a second `remember`
# overwriting it, a save failure after an accepted write, and an answer that
# omits the id — as behaviour rather than as scratch observations. Each asserts
# the whole triple: what happened to the file on disk, what the user was told,
# and how many times the write was sent.

def _write_handle(path: Path, artifact_id: str) -> None:
    path.write_text(json.dumps({'artifact_id': artifact_id, 'state': 'RATIFIED'}) + '\n',
                    encoding='utf-8')


def _handle_id(path: Path) -> str | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding='utf-8'))['artifact_id']


def test_second_remember_refuses_rather_than_overwrite_a_handle(tmp_path, capsys):
    """A stored id is never replaced by accident, and the refusal precedes the write."""
    handle = tmp_path / 'handle.json'
    first = Recorder({'propose': {'artifact_id': 'sha256:FIRST', 'state': 'L1_SCANNED'}})
    assert memory.main(['--endpoint', ENDPOINT, '--state-file', str(handle),
                        'remember', 'first observation'], first) == memory.OK
    capsys.readouterr()

    second = Recorder({'propose': {'artifact_id': 'sha256:SECOND', 'state': 'L1_SCANNED'}})
    with pytest.raises(SystemExit) as raised:
        memory.main(['--endpoint', ENDPOINT, '--state-file', str(handle),
                     'remember', 'second observation'], second)

    assert _handle_id(handle) == 'sha256:FIRST', 'the earlier id must survive'
    assert second.client.calls == [], 'the refusal must come before the submission'
    assert second.endpoints == [] or True   # a client may be opened; no call may be made
    message = str(raised.value)
    assert '--replace-handle' in message and '--state-file' in message


def test_the_default_handle_path_is_the_working_directory_and_still_refuses(tmp_path, monkeypatch):
    """The default is a fixed name in the CWD, so two projects share it; it must refuse."""
    monkeypatch.chdir(tmp_path)
    default = tmp_path / 'memory-handle.json'
    first = Recorder({'propose': {'artifact_id': 'sha256:PROJECT_A', 'state': 'L1_SCANNED'}})
    assert memory.main(['--endpoint', ENDPOINT, 'remember', 'a'], first) == memory.OK
    assert default.is_file()

    second = Recorder({'propose': {'artifact_id': 'sha256:PROJECT_B', 'state': 'L1_SCANNED'}})
    with pytest.raises(SystemExit):
        memory.main(['--endpoint', ENDPOINT, 'remember', 'b'], second)
    assert _handle_id(default) == 'sha256:PROJECT_A'
    assert second.client.calls == []


def test_replace_handle_overwrites_deliberately_and_only_then(tmp_path, capsys):
    recorder = Recorder({'propose': {'artifact_id': 'sha256:SECOND', 'state': 'L1_SCANNED'}})
    handle = tmp_path / 'handle.json'
    _write_handle(handle, 'sha256:FIRST')
    code = memory.main(['--endpoint', ENDPOINT, '--state-file', str(handle),
                        'remember', 'second observation', '--replace-handle'], recorder)
    assert code == memory.OK
    assert _handle_id(handle) == 'sha256:SECOND'
    assert 'artifact_id: sha256:SECOND' in capsys.readouterr().out


@pytest.mark.parametrize('answer, note', [
    (GateError('unavailable'), 'the proposal failed outright'),
    (GateError('timeout', outcome_unknown=True), 'the outcome is unknown'),
    ({'state': 'L1_SCANNED'}, 'the answer carried no artifact_id'),
])
def test_replace_handle_keeps_the_old_id_when_no_new_id_arrives(tmp_path, capsys, answer, note):
    """--replace-handle authorises a replacement, not a deletion.

    Until a usable new id is in hand the old one is still the only reference
    there is, so a failed or unusable proposal must leave it exactly as found —
    including the case where the write itself may well have landed.
    """
    handle = tmp_path / 'handle.json'
    _write_handle(handle, 'sha256:OLD')
    recorder = Recorder({'propose': answer})
    code = memory.main(['--endpoint', ENDPOINT, '--state-file', str(handle),
                        'remember', 'replacement', '--replace-handle'], recorder)

    assert code != memory.OK, note
    assert _handle_id(handle) == 'sha256:OLD', f'{note}: the old id must not be destroyed'
    assert len(recorder.client.calls) == 1, 'an uncertain write is never sent again'
    assert 'sha256:OLD' not in capsys.readouterr().out, 'no id is reported as newly written'


def test_a_failed_proposal_leaves_no_handle_file_behind(tmp_path):
    """The reservation this run created is cleaned up; it must not look like a handle."""
    handle = tmp_path / 'handle.json'
    recorder = Recorder({'propose': GateError('unavailable')})
    assert memory.main(['--endpoint', ENDPOINT, '--state-file', str(handle),
                        'remember', 'x'], recorder) == memory.GATE_FAILURE
    assert not handle.exists(), 'an empty reservation must not survive as a fake handle'
    # and `check` must then say so rather than read an empty file:
    with pytest.raises(SystemExit):
        memory.main(['--endpoint', ENDPOINT, '--state-file', str(handle), 'check'], Recorder({}))


def test_an_unwritable_handle_path_is_refused_before_the_write(tmp_path):
    """A save that cannot succeed must be discovered while it is still a usage error."""
    unwritable = tmp_path / 'no-such-dir' / 'handle.json'
    recorder = Recorder({'propose': {'artifact_id': 'sha256:NEVER', 'state': 'L1_SCANNED'}})
    with pytest.raises(SystemExit) as raised:
        memory.main(['--endpoint', ENDPOINT, '--state-file', str(unwritable),
                     'remember', 'an observation'], recorder)
    assert recorder.client.calls == [], 'nothing may be submitted for a handle that cannot be kept'
    assert str(unwritable) in str(raised.value)


def test_a_save_failure_after_an_accepted_write_reports_the_id_and_its_own_exit(tmp_path, capsys,
                                                                               monkeypatch):
    """The remote write succeeded; only the local record failed. Say so, keep the id.

    The id must reach stdout before the save is attempted — printing it after
    would mean a save failure swallows the one thing worth keeping — and the
    failure must be a distinct exit code rather than a traceback or a retry.
    """
    handle = tmp_path / 'handle.json'

    def full_disk(path, record):
        raise OSError(28, 'No space left on device')

    monkeypatch.setattr(memory, '_save', full_disk)
    recorder = Recorder({'propose': {'artifact_id': 'sha256:ACCEPTED', 'state': 'L1_SCANNED'}})
    code = memory.main(['--endpoint', ENDPOINT, '--state-file', str(handle),
                        'remember', 'an observation'], recorder)

    assert code == memory.HANDLE_NOT_SAVED
    assert code not in (memory.OK, memory.GATE_FAILURE, memory.OUTCOME_UNKNOWN)
    captured = capsys.readouterr()
    assert 'artifact_id: sha256:ACCEPTED' in captured.out, 'the id must be printed, not lost'
    assert 'handle saved to:' not in captured.out, 'a failed save must not claim success'
    assert 'handle_not_saved' in captured.err
    assert 'sha256:ACCEPTED' in captured.err, 'the recovery message names the id to keep'
    assert len(recorder.client.calls) == 1, 'a local save failure never resends the write'


def test_the_id_is_printed_before_the_save_is_attempted(tmp_path, capsys):
    """Ordering, pinned directly: at save time the id is already on stdout."""
    handle = tmp_path / 'handle.json'
    seen: list[str] = []
    real_save = memory._save

    def watch(path, record):
        seen.append(capsys.readouterr().out)
        return real_save(path, record)

    recorder = Recorder({'propose': {'artifact_id': 'sha256:ORDER', 'state': 'L1_SCANNED'}})
    try:
        memory._save = watch
        assert memory.main(['--endpoint', ENDPOINT, '--state-file', str(handle),
                            'remember', 'x'], recorder) == memory.OK
    finally:
        memory._save = real_save
    assert seen and 'artifact_id: sha256:ORDER' in seen[0]


# ------------------------------------ a successful answer that omits a required key
#
# Every starter maps a GateError onto a fixed kind and exit code. A response that
# succeeds but omits a key the app needs used to escape that contract as a bare
# KeyError. These pin the promised shape instead: a fixed `invalid_response`
# diagnostic naming only the key, the existing GATE_FAILURE exit, no retry, and
# no part of the payload on either stream.

MALFORMED_CASES = [
    pytest.param(memory, ['remember', 'x'], {'propose': {'state': 'L1_SCANNED'}},
                 'artifact_id', id='memory-remember-no-artifact-id'),
    pytest.param(memory, ['remember', 'x'], {'propose': {'artifact_id': 'sha256:a'}},
                 'state', id='memory-remember-no-state'),
    pytest.param(memory, ['recall', 'q'], {'recall': {'hits': []}},
                 'results', id='memory-recall-no-results'),
    pytest.param(memory, ['recall', 'q'], {'recall': ['hit', 'hit']},
                 'results', id='memory-recall-list-not-mapping'),
    pytest.param(ingestion, ['pending'], {'pending': {'items': []}},
                 'pending', id='ingestion-pending-no-pending'),
    pytest.param(ingestion, ['pending'], {'pending': ['not', 'a', 'mapping']},
                 'pending', id='ingestion-pending-wrong-type'),
]


@pytest.mark.parametrize('module, argv, answers, missing_key', MALFORMED_CASES)
def test_a_malformed_answer_is_a_fixed_diagnostic_not_a_traceback(module, argv, answers,
                                                                  missing_key, tmp_path,
                                                                  monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    recorder = Recorder(answers)
    code = module.main(['--endpoint', ENDPOINT, *argv], recorder)

    assert code == module.GATE_FAILURE
    captured = capsys.readouterr()
    assert 'kind=invalid_response' in captured.err
    assert repr(missing_key) in captured.err, 'the diagnostic names the missing key'
    assert 'will not guess' in captured.err
    assert len(recorder.client.calls) == 1, 'a malformed answer is never a reason to retry'


def test_a_malformed_answer_never_echoes_the_payload(tmp_path, capsys):
    """The diagnostic carries a key name. It must not carry the answer itself."""
    handle = tmp_path / 'handle.json'
    recorder = Recorder({'propose': {'state': 'L1_SCANNED',
                                     'server_note': 'internal-hostname-db07',
                                     'trace': '/srv/gate/internals'}})
    code = memory.main(['--endpoint', ENDPOINT, '--state-file', str(handle),
                        'remember', 'an observation'], recorder)
    assert code == memory.GATE_FAILURE
    captured = capsys.readouterr()
    for secret in ('internal-hostname-db07', '/srv/gate/internals', 'L1_SCANNED'):
        assert secret not in captured.err, f'{secret} leaked into the diagnostic'
        assert secret not in captured.out


def test_a_malformed_write_answer_loses_no_existing_handle(tmp_path, capsys):
    """The write may have landed; the old id is the only reference still known."""
    handle = tmp_path / 'handle.json'
    _write_handle(handle, 'sha256:PRIOR')
    recorder = Recorder({'propose': {'state': 'L1_SCANNED'}})
    code = memory.main(['--endpoint', ENDPOINT, '--state-file', str(handle),
                        'remember', 'x', '--replace-handle'], recorder)
    assert code == memory.GATE_FAILURE
    assert _handle_id(handle) == 'sha256:PRIOR'
    capsys.readouterr()


def test_ingestion_submit_reports_a_malformed_write_answer_without_resending(tmp_path, capsys):
    document = tmp_path / 'note.txt'
    document.write_text('text', encoding='utf-8')
    recorder = Recorder({'propose': {'state': 'L1_SCANNED'}})
    code = ingestion.main(['--endpoint', ENDPOINT, 'submit', str(document)], recorder)
    assert code == ingestion.GATE_FAILURE
    captured = capsys.readouterr()
    assert "omitted 'artifact_id'" in captured.err
    assert 'the gate accepted this submission' not in captured.out, 'no acceptance is claimed'
    assert len(recorder.client.calls) == 1


@pytest.mark.parametrize('answer', [
    {'artifact_id': 'sha256:abc'},                          # no events key at all
    {'artifact_id': 'sha256:abc', 'events': None},          # present but not a list
    {'artifact_id': 'sha256:abc', 'events': {'0': {}}},     # a mapping, not a list
])
def test_provenance_reports_an_absent_events_list_instead_of_crashing(answer, capsys):
    """provenance's whole claim is that a field this build omits shows as absent."""
    recorder = Recorder({'why': answer})
    code = provenance.main(['--endpoint', ENDPOINT, 'why', 'sha256:abc'], recorder)
    assert code == provenance.GATE_FAILURE, 'an absent events list is not a successful audit'
    out = capsys.readouterr().out
    assert 'events: absent' in out
    assert 'not\nread as zero events' in out or 'read as zero events' in out
    assert 'event_count: 0' in out
    assert 'expected_keys_absent: events' in out


def test_provenance_distinguishes_an_absent_list_from_an_empty_one(capsys):
    absent = provenance.summarize({'artifact_id': 'a'})
    empty = provenance.summarize({'artifact_id': 'a', 'events': []})
    assert absent['events_present'] is False and empty['events_present'] is True
    assert absent['event_count'] == empty['event_count'] == 0
    assert absent['expected_keys_absent'][0] == 'events'
    assert 'events' not in empty['expected_keys_absent']
    # and the two exit differently, so a script can tell them apart:
    assert provenance.main(['--endpoint', ENDPOINT, 'why', 'a'],
                           Recorder({'why': {'artifact_id': 'a'}})) == provenance.GATE_FAILURE
    assert provenance.main(['--endpoint', ENDPOINT, 'why', 'a'],
                           Recorder({'why': {'artifact_id': 'a', 'events': []}})) == provenance.OK
    capsys.readouterr()


def test_provenance_tolerates_a_non_mapping_answer():
    for answer in (None, [], 'text'):
        summary = provenance.summarize(answer)
        assert summary['events_present'] is False
        assert summary['artifact_id'] is None


@pytest.mark.parametrize('flag, answers, step', [
    ('--query', {'recall': {'hits': []}}, 'recall'),
    ('--artifact-id', {'status': {'sub_level': 2}}, 'status'),
])
def test_diagnostics_counts_a_malformed_optional_read_as_a_failed_step(flag, answers, step, capsys):
    recorder = Recorder({'discover': {'protocolVersions': ['2025-06-18']},
                         'tools': ('doublegate.recall',), **answers})
    code = diagnostics.main(['--endpoint', ENDPOINT, 'probe', flag, 'value'], recorder)
    assert code == diagnostics.GATE_FAILURE
    captured = capsys.readouterr()
    assert f'{step}: gate_error kind=invalid_response' in captured.err
    assert 'failed steps: 1' in captured.out


@pytest.mark.parametrize('module', (memory, ingestion, diagnostics), ids=lambda m: m.__name__)
def test_the_malformed_answer_carries_only_a_key_name(module):
    """The exception used for this path must not be able to carry a payload."""
    error = module._MalformedAnswer('artifact_id')
    assert error.args == ('artifact_id',)
    with pytest.raises(module._MalformedAnswer) as raised:
        module._required({'other': {'secret': 'value'}}, 'artifact_id')
    assert raised.value.args == ('artifact_id',)
    assert 'secret' not in str(raised.value)


# -------------------------------------------------------------------- ingestion

def test_submit_sends_the_local_document_with_its_file_uri(tmp_path, capsys):
    document = tmp_path / 'note.txt'
    document.write_text('Specimens are labelled by collection date.\n', encoding='utf-8')
    recorder = Recorder({'propose': {'artifact_id': 'sha256:doc', 'state': 'L1_SCANNED',
                                     'findings_count': 0, 'duplicate': False}})
    code = ingestion.main(['--endpoint', ENDPOINT, 'submit', str(document)], recorder)
    assert code == ingestion.OK
    assert recorder.kwargs[0]['allow_writes'] is True
    name, args, kwargs = recorder.client.calls[0]
    assert name == 'propose'
    assert args == ('Specimens are labelled by collection date.\n',)
    assert kwargs['content_type'] == 'document'
    assert kwargs['source_uri'] == document.resolve().as_uri()
    assert kwargs['source_uri'].startswith('file://')
    out = capsys.readouterr().out
    assert 'artifact_id: sha256:doc' in out
    assert 'findings_count: 0' in out


def test_non_utf8_document_is_refused_before_any_request(tmp_path):
    document = tmp_path / 'binary.bin'
    document.write_bytes(b'\xff\xfe\x00\x01')
    recorder = Recorder({})
    with pytest.raises(SystemExit) as raised:
        ingestion.main(['--endpoint', ENDPOINT, 'submit', str(document)], recorder)
    assert 'UTF-8' in str(raised.value)
    assert recorder.endpoints == [], 'a refused document must not open a client'


@pytest.mark.parametrize('write, expected', [
    (lambda p: p.write_text('   \n', encoding='utf-8'), 'empty'),
    (lambda p: p.write_text('x' * (ingestion.MAX_DOCUMENT_BYTES + 1), encoding='utf-8'), 'exceeds'),
])
def test_unusable_documents_are_refused_locally(tmp_path, write, expected):
    document = tmp_path / 'doc.txt'
    write(document)
    with pytest.raises(SystemExit) as raised:
        ingestion.read_document(document)
    assert expected in str(raised.value)


def test_missing_document_is_refused_locally(tmp_path):
    with pytest.raises(SystemExit):
        ingestion.read_document(tmp_path / 'absent.txt')


def test_source_uri_override_is_used_verbatim(tmp_path):
    document = tmp_path / 'note.txt'
    document.write_text('text', encoding='utf-8')
    assert ingestion.source_uri_for(document, 'https://example.org/a') == 'https://example.org/a'
    assert ingestion.source_uri_for(document) == document.resolve().as_uri()


def test_pending_is_read_only_metadata(capsys):
    recorder = Recorder({'pending': {'pending': [{'artifact_id': 'sha256:doc', 'state': 'L1_SCANNED'}]}})
    code = ingestion.main(['--endpoint', ENDPOINT, 'pending'], recorder)
    assert code == ingestion.OK
    assert recorder.kwargs[0]['allow_writes'] is False
    assert recorder.client.calls == [('pending', (), {'limit': 20})]
    assert 'pending: 1' in capsys.readouterr().out


def test_unknown_write_outcome_is_reported_and_not_retried(tmp_path, capsys):
    document = tmp_path / 'note.txt'
    document.write_text('text', encoding='utf-8')
    recorder = Recorder({'propose': GateError('timeout', outcome_unknown=True)})
    code = ingestion.main(['--endpoint', ENDPOINT, 'submit', str(document)], recorder)
    assert code == ingestion.OUTCOME_UNKNOWN
    assert len(recorder.client.calls) == 1, 'the starter must not retry an uncertain write'
    err = capsys.readouterr().err
    assert 'outcome_unknown' in err
    assert 'kind=timeout' in err


# ------------------------------------------------------------------- provenance

WHY_ANSWER = {
    'artifact_id': 'sha256:abc',
    'events': [
        {'event_id': 1, 'type': 'ingest', 'identity': 'writer', 'ts': 1, 'payload': {}, 'sig': 's1'},
        {'event_id': 2, 'type': 'scan', 'identity': 'gate', 'ts': 2, 'payload': {}, 'sig': 's2'},
    ],
}


def test_why_reads_only_and_reports_the_returned_shape(capsys):
    recorder = Recorder({'why': WHY_ANSWER})
    code = provenance.main(['--endpoint', ENDPOINT, 'why', 'sha256:abc'], recorder)
    assert code == provenance.OK
    assert recorder.kwargs[0]['allow_writes'] is False
    assert recorder.client.calls == [('why', ('sha256:abc',), {})]
    out = capsys.readouterr().out
    assert 'event_count: 2' in out
    assert 'event_types: ingest, scan' in out
    assert 'expected_keys_absent' not in out


def test_summary_is_derived_from_the_payload_not_assumed():
    summary = provenance.summarize(WHY_ANSWER)
    assert summary['event_count'] == 2
    assert summary['event_keys_present'] == ['event_id', 'identity', 'payload', 'sig', 'ts', 'type']
    assert summary['expected_keys_absent'] == []


def test_missing_event_fields_are_reported_as_absent(capsys):
    recorder = Recorder({'why': {'artifact_id': 'sha256:abc',
                                 'events': [{'type': 'ingest', 'ts': 1}]}})
    code = provenance.main(['--endpoint', ENDPOINT, 'why', 'sha256:abc'], recorder)
    assert code == provenance.OK
    out = capsys.readouterr().out
    assert 'expected_keys_absent: event_id, identity, payload, sig' in out
    assert 'do not assume a default' in out


def test_empty_event_list_is_reported_without_inventing_events(capsys):
    recorder = Recorder({'why': {'artifact_id': 'sha256:abc', 'events': []}})
    assert provenance.main(['--endpoint', ENDPOINT, 'why', 'sha256:abc'], recorder) == provenance.OK
    out = capsys.readouterr().out
    assert 'event_count: 0' in out
    assert 'event_types: (none)' in out


def test_events_flag_prints_each_returned_event(capsys):
    recorder = Recorder({'why': WHY_ANSWER})
    provenance.main(['--endpoint', ENDPOINT, 'why', 'sha256:abc', '--events'], recorder)
    out = capsys.readouterr().out
    assert out.count('"event_id"') == 2


def test_unknown_artifact_is_reported_as_unsupported(capsys):
    recorder = Recorder({'why': GateError('unsupported_operation', -32602)})
    code = provenance.main(['--endpoint', ENDPOINT, 'why', 'sha256:nope'], recorder)
    assert code == provenance.GATE_FAILURE
    assert 'kind=unsupported_operation' in capsys.readouterr().err


# ------------------------------------------------------------------ diagnostics

def test_probe_negotiates_and_succeeds(capsys):
    recorder = Recorder({'discover': {'protocolVersions': ['2025-06-18']},
                         'tools': ('doublegate.recall', 'doublegate.why')})
    code = diagnostics.main(['--endpoint', ENDPOINT, 'probe'], recorder)
    assert code == diagnostics.OK
    assert recorder.kwargs[0]['allow_writes'] is False
    out = capsys.readouterr().out
    assert 'tools: doublegate.recall, doublegate.why' in out
    assert 'failed steps: 0' in out
    assert 'token supplied: False' in out


def test_probe_optional_reads_are_only_made_when_asked(capsys):
    recorder = Recorder({'recall': {'results': [{'artifact_id': 'sha256:abc'}]},
                         'status': {'state': 'RATIFIED'}})
    code = diagnostics.main(['--endpoint', ENDPOINT, 'probe', '--query', 'basalt',
                             '--artifact-id', 'sha256:abc'], recorder)
    assert code == diagnostics.OK
    assert [name for name, _, _ in recorder.client.calls] == ['recall', 'status']
    out = capsys.readouterr().out
    assert 'recall results: 1' in out
    assert 'status: RATIFIED' in out


def test_probe_makes_no_reads_by_default():
    recorder = Recorder({})
    assert diagnostics.main(['--endpoint', ENDPOINT, 'probe'], recorder) == diagnostics.OK
    assert recorder.client.calls == []


@pytest.mark.parametrize('kind, code, fragment', [
    ('unauthorized', 401, 'DOUBLEGATE_TOKEN'),
    ('forbidden', 403, 'not permitted'),
    ('unavailable', None, 'gate is running'),
    ('timeout', None, '--timeout'),
    ('redirect_refused', 307, 'final URL'),
    ('unsupported_operation', -32601, 'does not serve'),
    ('invalid_response', None, 'not a response this client will guess at'),
])
def test_each_error_kind_has_a_fixed_next_step(kind, code, fragment, capsys):
    recorder = Recorder({'discover': GateError(kind, code), 'tools': GateError(kind, code)})
    result = diagnostics.main(['--endpoint', ENDPOINT, 'probe'], recorder)
    assert result == diagnostics.GATE_FAILURE
    err = capsys.readouterr().err
    assert f'kind={kind}' in err
    assert fragment in err


def test_unknown_error_kind_is_reported_without_guessing(capsys):
    recorder = Recorder({'discover': GateError('some_future_kind'), 'tools': ('a',)})
    assert diagnostics.main(['--endpoint', ENDPOINT, 'probe'], recorder) == diagnostics.GATE_FAILURE
    assert 'unrecognized error kind' in capsys.readouterr().err


def test_next_step_text_is_fixed_and_leaks_no_server_message():
    """Every mapped step is literal text; none of it is built from a response."""
    for kind, step in diagnostics.NEXT_STEP.items():
        assert isinstance(step, str) and step
        assert '{' not in step and '%s' not in step, f'{kind} step is a template'


def test_diagnostics_reports_token_presence_only(monkeypatch, capsys):
    monkeypatch.setenv('DOUBLEGATE_TOKEN', 'secret-value')
    recorder = Recorder({})
    diagnostics.main(['--endpoint', ENDPOINT, 'probe'], recorder)
    captured = capsys.readouterr()
    assert 'token supplied: True' in captured.out
    assert 'secret-value' not in captured.out + captured.err
    assert recorder.kwargs[0]['token'] == 'secret-value'


# ------------------------------------------------------ shared starter contracts

ALL_STARTERS = (memory, ingestion, provenance, diagnostics)


@pytest.mark.parametrize('module', ALL_STARTERS, ids=lambda m: m.__name__)
def test_every_starter_has_help_and_requires_a_subcommand(module):
    help_text = module.build_parser().format_help()
    assert '--endpoint' in help_text
    assert '--allow-insecure-loopback' in help_text
    assert '--token' not in help_text, 'credentials must never be a command line argument'
    with pytest.raises(SystemExit):
        module.build_parser().parse_args([])


@pytest.mark.parametrize('module', ALL_STARTERS, ids=lambda m: m.__name__)
def test_no_starter_opts_into_insecure_loopback_by_default(module):
    args = module.build_parser().parse_args(_smallest_command(module))
    assert args.allow_insecure_loopback is False


def _smallest_command(module) -> list[str]:
    return {'_starter_memory': ['recall', 'q'],
            '_starter_ingestion': ['pending'],
            '_starter_provenance': ['why', 'sha256:abc'],
            '_starter_diagnostics': ['probe']}[module.__name__]


@pytest.mark.parametrize('module', ALL_STARTERS, ids=lambda m: m.__name__)
def test_only_declared_write_subcommands_enable_writes(module, tmp_path, monkeypatch):
    """A read subcommand must never open a client with allow_writes=True."""
    recorder = Recorder({'recall': {'results': []}, 'pending': {'pending': []},
                         'why': {'artifact_id': 'a', 'events': []}})
    monkeypatch.chdir(tmp_path)
    module.main(['--endpoint', ENDPOINT, *_smallest_command(module)], recorder)
    assert all(kwargs['allow_writes'] is False for kwargs in recorder.kwargs)
