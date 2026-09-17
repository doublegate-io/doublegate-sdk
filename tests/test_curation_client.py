"""The curation client: the decisions, authorized by the caller's role at the gate."""
import pytest
from conftest import Recorder

from doublegate_sdk.curation import CurationClient
from doublegate_sdk.operations import CURATION, KNOWLEDGE, OPERATIONS
from doublegate_sdk.transport import UnixSocketTransport


def test_a_knowledge_scoped_transport_is_refused_by_the_curation_client(tmp_path):
    with pytest.raises(ValueError, match='curation-scoped'):
        CurationClient(UnixSocketTransport(tmp_path / 'x', scope=KNOWLEDGE))
    CurationClient(UnixSocketTransport(tmp_path / 'x', scope=CURATION))


def test_no_curation_call_carries_an_operator_proof_or_fetches_a_challenge():
    t = Recorder({'ok': True}, scope=CURATION)
    c = CurationClient(t)
    c.approve('a', note='looks right'); c.reject('a', 'wrong'); c.veto('a', 'no')
    c.supersede('new', 'old'); c.ban('writer', 'uid:1', reason='spam'); c.back()
    c.rank('a', audience='ops', weight=2.0); c.hide('a', reason='stale')
    c.keys('issue', label='x', role='agent')
    emitted = {m for m, _ in t.calls}
    assert 'dg.challenge' not in emitted and 'dg.operator_proof' not in emitted
    assert all('operator_proof' not in params for _, params in t.calls)


def test_approve_is_one_call_that_signs_promote_and_promotes():
    t = Recorder({'artifact_id': 'a', 'state': 'ACTIVE', 'promote': {'state': 'ACTIVE'}}, scope=CURATION)
    out = CurationClient(t).approve('a', note='looks right')
    assert out['state'] == 'ACTIVE'
    method, params = t.calls[0]
    assert len(t.calls) == 1 and method == 'dg.sign'
    assert params == {'artifact_id': 'a', 'decision': 'promote', 'note': 'looks right', 'promote': True}


def test_every_deciding_verb_goes_straight_to_the_gate_and_veto_is_a_demote():
    t = Recorder({'ok': True}, scope=CURATION)
    c = CurationClient(t)
    c.reject('a', 'wrong', in_favour_of='b'); c.veto('a', 'no'); c.supersede('new', 'old')
    c.ban('writer', 'uid:1', reason='spam'); c.back()
    assert [m for m, _ in t.calls] == ['dg.reject', 'dg.demote', 'dg.relate', 'dg.ban', 'dg.back']
    assert t.calls[0][1]['in_favour_of'] == 'b'
    assert t.calls[2][1] == {'artifact_id': 'new', 'kind': 'supersedes', 'parent': 'old'}


def test_every_verb_this_client_emits_is_a_reviewers_or_an_admins():
    # AUTH-4: an agent never holds a curation role, so nothing on the curation
    # scope may sit on the agent's row or below it.
    reviewing = {rpc for rpc, op in OPERATIONS.items()
                 if op.scope == CURATION and op.role in ('reviewer', 'admin')}
    assert reviewing == {rpc for rpc, op in OPERATIONS.items() if op.scope == CURATION} - {
        'dg.submit', 'dg.submit_drain'}


def test_keys_is_one_call_whatever_the_action():
    t = Recorder({'keys': []}, scope=CURATION)
    assert CurationClient(t).keys() == {'keys': []}
    assert t.calls == [('dg.keys', {'action': 'list'})]
    t = Recorder({'key': 'raw', 'key_id': 'k'}, scope=CURATION)
    assert CurationClient(t).keys('issue', label='x', role='agent')['key_id'] == 'k'
    assert t.calls[0][1] == {'action': 'issue', 'label': 'x', 'role': 'agent'}
    with pytest.raises(ValueError):
        CurationClient(Recorder(scope=CURATION)).keys('mint')


def test_a_reviewer_reads_through_the_same_door():
    t = Recorder({'state': 'ACTIVE'}, scope=CURATION)
    assert CurationClient(t).knowledge.status('a')['state'] == 'ACTIVE'


def test_inputs_are_validated_before_anything_reaches_the_transport():
    t = Recorder(scope=CURATION)
    c = CurationClient(t)
    with pytest.raises(ValueError):
        c.sign('a', 'approve')
    with pytest.raises(ValueError):
        c.relate('a', 'replaces', 'b')
    with pytest.raises(ValueError):
        c.ban('space', 'x', reason='r')
    with pytest.raises(ValueError):
        c.promote('a', importance=2)
    assert not t.calls


def test_the_lifecycle_passes_are_plain_calls():
    t = Recorder({'ok': True}, scope=CURATION)
    c = CurationClient(t)
    c.scan('a'); c.grade('a'); c.grade_pending(limit=5); c.settle_pending(); c.semantic_review('a', settle=True)
    assert [m for m, _ in t.calls] == ['dg.scan', 'dg.grade', 'dg.grade_pending', 'dg.settle_pending', 'dg.semantic_review']
