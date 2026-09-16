"""The curation client: the operator's verbs, each under its own proof."""
import pytest
from conftest import Recorder

from doublegate_sdk.curation import CurationClient
from doublegate_sdk.errors import GateError
from doublegate_sdk.operations import CURATION, KNOWLEDGE
from doublegate_sdk.proof import ServerMinted, SignerProof
from doublegate_sdk.transport import UnixSocketTransport


def signer(nonce_bytes):
    return b'\x01' * 64


def test_a_knowledge_scoped_transport_is_refused_by_the_curation_client(tmp_path):
    with pytest.raises(ValueError, match='curation-scoped'):
        CurationClient(UnixSocketTransport(tmp_path / 'x', scope=KNOWLEDGE))
    CurationClient(UnixSocketTransport(tmp_path / 'x', scope=CURATION))


def test_an_operator_verb_without_a_provider_is_refused_before_io():
    t = Recorder(scope=CURATION)
    with pytest.raises(GateError, match='proof_required'):
        CurationClient(t).approve('a')
    assert not t.calls


def test_approve_fetches_one_challenge_and_sends_sign_with_promote_under_that_proof():
    t = Recorder(results=[{'nonce': 'n1', 'ttl_ms': 60000}, {'artifact_id': 'a', 'state': 'ACTIVE', 'promote': {'state': 'ACTIVE'}}], scope=CURATION)
    out = CurationClient(t, SignerProof(signer)).approve('a', note='looks right')
    assert out['state'] == 'ACTIVE'
    assert t.calls[0] == ('dg.challenge', {})
    method, params = t.calls[1]
    assert method == 'dg.sign' and params['decision'] == 'promote' and params['promote'] is True and params['note'] == 'looks right'
    assert params['operator_proof'] == {'nonce': 'n1', 'sig': '01' * 64}


def test_every_deciding_verb_carries_a_fresh_proof_and_veto_is_a_demote():
    nonces = iter(['n1', 'n2', 'n3', 'n4', 'n5'])
    results = []
    for _ in range(5):
        results += [{'nonce': next(nonces)}, {'ok': True}]
    t = Recorder(results=results, scope=CURATION)
    c = CurationClient(t, SignerProof(signer))
    c.reject('a', 'wrong', in_favour_of='b'); c.veto('a', 'no'); c.supersede('new', 'old'); c.ban('writer', 'uid:1', reason='spam'); c.back()
    verbs = [m for m, _ in t.calls if m != 'dg.challenge']
    assert verbs == ['dg.reject', 'dg.demote', 'dg.relate', 'dg.ban', 'dg.back']
    proofs = [p['operator_proof']['nonce'] for m, p in t.calls if m != 'dg.challenge']
    assert proofs == ['n1', 'n2', 'n3', 'n4', 'n5']
    assert t.calls[1][1]['in_favour_of'] == 'b'
    assert t.calls[5][1] == {'artifact_id': 'new', 'kind': 'supersedes', 'parent': 'old', 'operator_proof': {'nonce': 'n3', 'sig': '01' * 64}}


def test_server_minted_proof_asks_the_client_daemon_and_a_refusal_says_proof_required():
    t = Recorder(results=[{'nonce': 'n', 'sig': 'ab'}, {'artifact_id': 'a', 'state': 'ACTIVE'}], scope=CURATION)
    CurationClient(t, ServerMinted()).approve('a')
    assert t.calls[0] == ('dg.operator_proof', {}) and t.calls[1][1]['operator_proof'] == {'nonce': 'n', 'sig': 'ab'}
    t = Recorder(results=[GateError('invalid_params', -32602, detail='the operator key is not readable')], scope=CURATION)
    with pytest.raises(GateError, match='proof_required'):
        CurationClient(t, ServerMinted()).approve('a')


def test_optional_proof_verbs_attach_one_only_when_a_provider_is_present():
    t = Recorder({'ok': True}, scope=CURATION)
    CurationClient(t).rank('a', audience='ops', weight=2.0)
    assert 'operator_proof' not in t.calls[0][1]
    t = Recorder(results=[{'nonce': 'n'}, {'ok': True}], scope=CURATION)
    CurationClient(t, SignerProof(signer)).hide('a', reason='stale')
    assert t.calls[1][1]['operator_proof']['nonce'] == 'n'


def test_keys_list_needs_no_proof_but_issue_and_revoke_do():
    t = Recorder({'keys': []}, scope=CURATION)
    assert CurationClient(t).keys() == {'keys': []}
    with pytest.raises(GateError, match='proof_required'):
        CurationClient(t).keys('issue', label='x')
    t = Recorder(results=[{'nonce': 'n'}, {'key': 'raw', 'key_id': 'k'}], scope=CURATION)
    assert CurationClient(t, SignerProof(signer)).keys('issue', label='x', scopes=['admin'])['key_id'] == 'k'
    assert t.calls[1][1]['operator_proof']['nonce'] == 'n' and t.calls[1][1]['action'] == 'issue'


def test_the_operator_reads_through_the_same_door():
    t = Recorder({'state': 'ACTIVE'}, scope=CURATION)
    assert CurationClient(t).knowledge.status('a')['state'] == 'ACTIVE'


def test_inputs_are_validated_before_any_proof_is_minted():
    t = Recorder(scope=CURATION)
    c = CurationClient(t, SignerProof(signer))
    with pytest.raises(ValueError):
        c.sign('a', 'approve')
    with pytest.raises(ValueError):
        c.relate('a', 'replaces', 'b')
    with pytest.raises(ValueError):
        c.ban('space', 'x', reason='r')
    with pytest.raises(ValueError):
        c.promote('a', importance=2)
    assert not t.calls


def test_the_lifecycle_passes_need_presence_not_a_proof():
    t = Recorder({'ok': True}, scope=CURATION)
    c = CurationClient(t)
    c.scan('a'); c.grade('a'); c.grade_pending(limit=5); c.settle_pending(); c.semantic_review('a', settle=True)
    assert [m for m, _ in t.calls] == ['dg.scan', 'dg.grade', 'dg.grade_pending', 'dg.settle_pending', 'dg.semantic_review']
    assert all('operator_proof' not in p for _, p in t.calls)
