"""The knowledge client: what an agent may do, and never more."""
import base64
import io
import json
import zipfile

import pytest
from conftest import Recorder, rpc_ok  # noqa: F401

from doublegate_sdk.errors import GateError
from doublegate_sdk.inventory import InventoryPage
from doublegate_sdk.knowledge import KnowledgeClient, Learning, Proposal
from doublegate_sdk.operations import CURATION, KNOWLEDGE, in_scope
from doublegate_sdk.transport import UnixSocketTransport


def page(offset=0, more=False, complete=True):
    return {'page': [{'artifact_id': 'record-a', 'state': 'FUTURE_STATE'}],
            'total': 2 if more else 1, 'limit': 1, 'offset': offset,
            'has_more': more, 'next_offset': offset + 1 if more else None,
            'coverage': {'complete': complete, 'errors': [] if complete else ['active unavailable']},
            'scope': {'gate_local': True}, 'counts': {}}


def test_the_client_is_lazy_and_status_preserves_the_gates_shape():
    t = Recorder({'state': 'L1B_FLAGGED'})
    client = KnowledgeClient(t)
    assert not t.calls
    assert client.status('abc')['state'] == 'L1B_FLAGGED'
    assert t.calls == [('dg.status', {'artifact_id': 'abc'})]


def test_a_curation_scoped_transport_is_refused_by_the_agent_client(tmp_path):
    with pytest.raises(ValueError, match='never decides'):
        KnowledgeClient(UnixSocketTransport(tmp_path / 'x', scope=CURATION))
    KnowledgeClient(UnixSocketTransport(tmp_path / 'x', scope=KNOWLEDGE))


def test_every_verb_the_agent_client_emits_is_inside_the_knowledge_scope():
    """The client's own methods, driven against a recorder: no deciding verb ever leaves."""
    t = Recorder({'artifact_id': 'a', 'state': 'L1_SCANNED', 'hits': [], 'pong': True, 'verbs': [],
                  'page': [], 'total': 0, 'limit': 1, 'offset': 0, 'has_more': False, 'next_offset': None,
                  'coverage': {'complete': True, 'errors': []}, 'scope': {}, 'counts': {}})
    c = KnowledgeClient(t)
    c.present(); c.ping(); c.describe(); c.status(); c.why('a'); c.tip('a'); c.relations('a'); c.approval('a')
    c.node('a'); c.comments('a'); c.pending(); c.inventory(limit=1); c.recall('q')
    c.remember('x', content_type='memory', source_uri='s'); c.learn('x', source_uri='s', evidence=['e'])
    c.propose_skill('x', source_uri='s'); c.propose_prompt_template('x', source_uri='s')
    c.annotate('a', 'b'); c.raise_objection('a', 'b'); c.resolve('a', 'b', reply_to='c'); c.retract_comment('a', 'c')
    c.defer('a'); c.rank('a', audience='ops', weight=2.0); c.hide('a', reason='r'); c.hide('a', lift=True)
    emitted = {m for m, _ in t.calls}
    assert emitted and emitted <= in_scope(KNOWLEDGE)
    assert not emitted & {'dg.sign', 'dg.promote', 'dg.demote', 'dg.reject', 'dg.relate', 'dg.ban', 'dg.operator_proof'}


def test_present_never_raises_and_a_probe_transport_is_used_for_it():
    probe = Recorder(GateError('unavailable'))
    assert KnowledgeClient(Recorder({'pong': True}), probe=probe).present() is False
    assert KnowledgeClient(Recorder({'pong': True})).present() is True
    assert probe.calls == [('dg.ping', {})]


def test_describe_is_parsed_once_and_content_types_come_from_the_enum():
    t = Recorder({'role': 'client', 'version': '1', 'verbs': [
        {'name': 'dg.ingest', 'params': [{'name': 'content_type', 'enum': ['memory', 'fetch']}]},
        {'name': 'dg.ingest_bundle', 'dispatched': False, 'params': []}]})
    c = KnowledgeClient(t)
    caps = c.describe(); c.describe()
    assert len(t.calls) == 1
    assert caps.accepts_content_type('fetch') and not caps.accepts_content_type('skill')
    assert not caps.dispatches('dg.ingest_bundle') and caps.dispatches('dg.ingest')
    assert caps.params_of('dg.ingest') == ('content_type',)


def test_remember_preserves_bytes_and_never_supplies_an_identity():
    t = Recorder({'artifact_id': 'a', 'state': 'L1B_FLAGGED', 'findings_count': 1})
    result = KnowledgeClient(t).remember(b'raw\x00\xff', content_type='imported_document', trust_class='T-4',
                                         source_uri='fixture://bytes', derives_from=['p1'])
    method, params = t.calls[0]
    assert method == 'dg.ingest'
    assert base64.b64decode(params['content']) == b'raw\x00\xff' and params['encoding'] == 'base64'
    assert params['derives_from'] == ['p1']
    assert 'writer_identity' not in params and 'deployment_id' not in params
    assert isinstance(result, Proposal) and result.state == 'L1B_FLAGGED' and result.findings_count == 1
    assert Proposal.proposals_are_admission is False


def test_a_receipt_without_an_id_is_an_unknown_outcome():
    with pytest.raises(GateError) as err:
        KnowledgeClient(Recorder({'ok': True})).remember('x', content_type='memory', source_uri='s')
    assert err.value.kind == 'invalid_response' and err.value.outcome_unknown is True


def test_learn_lands_a_derived_fact_with_its_evidence_and_the_replaced_claim_as_provenance():
    t = Recorder(results=[{'artifact_id': 'new', 'state': 'L1_SCANNED'}, {'artifact_id': 'new', 'event_id': 'c1', 'counts': {}}])
    learning = KnowledgeClient(t).learn('The lab labels basalt by date.', source_uri='agent://s', evidence=['ev1', 'ev2'],
                                        replaces='old')
    ingest, comment = t.calls
    assert ingest[0] == 'dg.ingest' and ingest[1]['content_type'] == 'derived_fact' and ingest[1]['trust_class'] == 'T-5'
    assert ingest[1]['derives_from'] == ['ev1', 'ev2', 'old']
    assert comment == ('dg.comment', {'artifact_id': 'new', 'body': 'proposes: supersedes old', 'kind': 'note'})
    assert isinstance(learning, Learning) and learning.proposed_supersedes == 'old' and learning.derives_from == ('ev1', 'ev2', 'old')
    assert 'dg.relate' not in {m for m, _ in t.calls}


def test_learn_refuses_a_kind_that_is_not_a_belief_and_a_duplicate_posts_no_note():
    with pytest.raises(ValueError, match='kind'):
        KnowledgeClient(Recorder()).learn('x', source_uri='s', kind='skill')
    t = Recorder({'artifact_id': 'a', 'state': 'L1_SCANNED', 'duplicate': True})
    KnowledgeClient(t).learn('x', source_uri='s', replaces='old')
    assert [m for m, _ in t.calls] == ['dg.ingest']


def test_propose_bundle_inspects_the_archive_before_the_wire_and_needs_the_verb():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('SKILL.md', '# skill\n')
    archive = buf.getvalue()
    with pytest.raises(ValueError, match='archive'):
        KnowledgeClient(Recorder()).propose_bundle(b'not a zip', fetch_artifact_id='f', source_uri='s')
    t = Recorder(results=[{'role': 'client', 'verbs': [{'name': 'dg.ingest_bundle', 'dispatched': False}]}])
    with pytest.raises(GateError, match='unsupported_operation'):
        KnowledgeClient(t).propose_bundle(archive, fetch_artifact_id='f', source_uri='s')
    t = Recorder(results=[{'role': 'client', 'verbs': [{'name': 'dg.ingest_bundle'}]}, {'state': 'quarantined', 'archive_artifact_id': 'z'}])
    out = KnowledgeClient(t).propose_bundle(archive, fetch_artifact_id='f', source_uri='s', manifest={'v': 1})
    assert out['archive_artifact_id'] == 'z'
    assert t.calls[1][0] == 'dg.ingest_bundle' and base64.b64decode(t.calls[1][1]['content']) == archive
    assert t.calls[1][1]['manifest'] == {'v': 1} and 'trust_class' not in t.calls[1][1]


def test_recall_excludes_own_pending_and_provisional_unless_asked_and_keeps_flags():
    t = Recorder({'hits': [{'artifact_id': 'a', 'provisional': True}], 'paths': {}})
    c = KnowledgeClient(t)
    c.recall('knowledge')
    assert t.calls[0] == ('dg.recall', {'query': 'knowledge', 'k': 10, 'include_own_pending': False, 'include_provisional': False})
    out = c.recall('knowledge', limit=2, spaces=['team'], include_provisional=True, include_superseded=True, audience='ops')
    assert out['hits'][0]['provisional'] is True
    assert t.calls[1][1]['spaces'] == ['team'] and t.calls[1][1]['include_superseded'] is True and t.calls[1][1]['audience'] == 'ops'
    with pytest.raises(GateError, match='invalid_response'):
        KnowledgeClient(Recorder({'hits': [{}, {}], 'paths': {}})).recall('q', limit=1)


@pytest.mark.parametrize('query,limit', [('', 1), ('q', True), ('q', 0)])
def test_an_invalid_recall_never_reaches_the_transport(query, limit):
    t = Recorder({'hits': []})
    with pytest.raises(ValueError):
        KnowledgeClient(t).recall(query, limit=limit)
    assert not t.calls


def test_annotations_are_typed_and_a_resolution_must_reply():
    t = Recorder({'artifact_id': 'a', 'event_id': 'e', 'counts': {}})
    c = KnowledgeClient(t)
    c.raise_objection('a', 'this contradicts the manual')
    assert t.calls[-1][1]['kind'] == 'objection'
    with pytest.raises(ValueError, match='reply_to'):
        c.annotate('a', 'done', kind='resolution')
    with pytest.raises(ValueError, match='kind'):
        c.annotate('a', 'x', kind='verdict')


def test_rank_and_hide_validate_before_the_wire():
    c = KnowledgeClient(Recorder({}))
    with pytest.raises(ValueError):
        c.rank('a', audience='ops', weight=0)
    with pytest.raises(ValueError):
        c.hide('a')


def test_inventory_keeps_unknown_states_partial_coverage_and_bounded_paging():
    t = Recorder(page(complete=False))
    result = KnowledgeClient(t).inventory(limit=1, state=['FUTURE_STATE'])
    assert isinstance(result, InventoryPage) and result.records[0]['state'] == 'FUTURE_STATE' and result.complete is False
    with pytest.raises(ValueError):
        KnowledgeClient(t).inventory(principal='admin')
    pages = KnowledgeClient(Recorder(page(more=True))).inventory_pages(limit=1, max_pages=1)
    next(pages)
    with pytest.raises(GateError, match='page_limit'):
        next(pages)
    pages = KnowledgeClient(Recorder(page(more=True))).inventory_pages(limit=1, max_pages=3)
    next(pages)
    with pytest.raises(GateError, match='invalid_response'):
        next(pages)


@pytest.mark.parametrize('changes', [{'total': 2}, {'total': 1, 'has_more': True, 'next_offset': 1},
                                     {'coverage': {'complete': True, 'errors': ['unavailable']}}])
def test_a_contradictory_inventory_page_is_refused(changes):
    data = page(); data.update(changes)
    with pytest.raises(GateError, match='invalid_response'):
        KnowledgeClient(Recorder(data)).inventory(limit=1)


def test_wait_for_returns_the_last_row_and_says_whether_the_state_was_reached():
    t = Recorder(results=[{'state': 'L1_SCANNED'}, {'state': 'ACTIVE'}])
    row = KnowledgeClient(t).wait_for('a', ['ACTIVE'], timeout=5, interval=0.01)
    assert row['reached'] is True and row['state'] == 'ACTIVE'
    row = KnowledgeClient(Recorder({'state': 'L1_SCANNED'})).wait_for('a', ['ACTIVE'], timeout=0, interval=0.01)
    assert row['reached'] is False


def test_the_real_socket_path_carries_a_proposal_end_to_end(unix_server):
    path = unix_server(rpc_ok({'artifact_id': 'a', 'state': 'L1_SCANNED'}))
    out = KnowledgeClient(UnixSocketTransport(path, timeout=2, scope=KNOWLEDGE)).remember('fact', content_type='memory', source_uri='s')
    assert out.artifact_id == 'a'
