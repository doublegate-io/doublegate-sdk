"""Real gate-local integration; install client/org wheels to run this file.

No importorskip: a missing consumer is a failed acceptance prerequisite.
"""
from dataclasses import FrozenInstanceError

import pytest

from doublegate import keys
from doublegate_org.daemon import OrgDaemon
import doublegate_sdk


@pytest.fixture
def gate(tmp_path):
    home = tmp_path / 'org'
    home.mkdir()
    (home / 'config.toml').write_text(
        '[deployment]\nrole="org"\n[auth]\nmode="key"\n'
        '[lifecycle]\nscan_delay_s=0\n[gating]\nauto_grade=false\n'
        '[quorum]\nprofile="shared"\n')
    keys.save_operator(keys.generate(), home)
    daemon = OrgDaemon(home)
    admin = daemon.apikeys.issue(daemon.ledger.append, label='graph test',
        scopes=['admin'], spaces=[], issued_by='test')
    yield daemon, admin
    daemon.close()


def admit(daemon, body, *, content_type='memory', space='main', derives_from=()):
    result = daemon.ingest(content=body.encode(), content_type=content_type,
        trust_class='T-1', source_uri='test://knowledge-graph',
        writer_identity='writer:producer', space=space, derives_from=list(derives_from))
    aid = result['artifact_id']
    daemon.sign(aid, 'promote', 'reviewer:a')
    daemon.sign(aid, 'promote', 'reviewer:b')
    assert daemon.promote(aid, 'integrator')['state'] == 'ACTIVE'
    return aid


def test_real_producer_gate_query_preserves_identity_and_provenance(gate):
    daemon, admin = gate
    parent = admit(daemon, 'The specification names a retry budget.')
    child = admit(daemon, 'The summary references the specification.',
                  content_type='summary', derives_from=[parent])
    # RED first: the package does not yet expose its knowledge graph module.
    assert hasattr(doublegate_sdk, 'knowledge_graph')
    kg = doublegate_sdk.knowledge_graph
    reader = kg.OrgGraphDiagnosticReader(daemon)
    before = daemon.ledger.tip_hash
    result = reader.read(kg.GraphQuery(roots=(child,)), api_key=admin['raw'])
    assert daemon.ledger.tip_hash == before
    assert {node.artifact_id for node in result.nodes} == {parent, child}
    node = next(n for n in result.nodes if n.artifact_id == child)
    assert node.identity_domain == 'legacy_artifact'
    assert node.content_type == 'summary'
    assert node.provenance['source_uri'] == 'test://knowledge-graph'
    assert node.provenance['promotion_event']
    assert node.body == 'The summary references the specification.'
    assert len(result.edges) == 1
    edge = result.edges[0]
    assert (edge.source, edge.kind, edge.target) == (child, 'derives_from', parent)
    assert edge.event_id == ''  # envelope provenance is not an assertion event
    assert edge.identity == 'writer:producer'
    assert result.connection_id == daemon.key.deployment_id


def test_one_hop_returns_only_edges_from_roots(gate):
    daemon, admin = gate
    parent = admit(daemon, 'parent')
    middle = admit(daemon, 'middle', derives_from=[parent])
    root = admit(daemon, 'root', derives_from=[parent, middle])
    kg = doublegate_sdk.knowledge_graph
    result = kg.OrgGraphDiagnosticReader(daemon).read(kg.GraphQuery((root,)), api_key=admin['raw'])
    assert {e.source for e in result.edges} == {root}


def test_hidden_endpoint_is_excluded_on_next_read(gate):
    daemon, admin = gate
    parent = admit(daemon, 'hide me')
    child = admit(daemon, 'visible child', derives_from=[parent])
    kg = doublegate_sdk.knowledge_graph
    reader = kg.OrgGraphDiagnosticReader(daemon)
    query = kg.GraphQuery((child,))
    assert len(reader.read(query, api_key=admin['raw']).nodes) == 2
    daemon.hide(parent, 'curator', reason='do not serve')
    # Baseline export alone still includes this globally hidden row.
    assert parent in {r['artifact_id'] for r in daemon._memories_for_api(None)}
    result = reader.read(query, api_key=admin['raw'])
    assert {n.artifact_id for n in result.nodes} == {child}
    assert not result.edges


def test_admin_key_is_rechecked_and_revocation_is_not_cached(gate):
    daemon, admin = gate
    aid = admit(daemon, 'authorization test')
    kg = doublegate_sdk.knowledge_graph
    reader = kg.OrgGraphDiagnosticReader(daemon)
    query = kg.GraphQuery((aid,))
    reader_key = daemon.apikeys.issue(daemon.ledger.append, label='reader',
        scopes=['read'], spaces=['main'], issued_by='test')
    for raw in ('invalid', reader_key['raw']):
        with pytest.raises(PermissionError):
            reader.read(query, api_key=raw)
    assert len(reader.read(query, api_key=admin['raw']).nodes) == 1
    daemon.apikeys.revoke(daemon.ledger.append, admin['record'].key_id)
    with pytest.raises(PermissionError):
        reader.read(query, api_key=admin['raw'])


def test_demoted_held_dangling_and_unknown_roots_are_not_exposed(gate):
    daemon, admin = gate
    parent = admit(daemon, 'demoted parent')
    held = daemon.ingest(content=b'held', content_type='memory', trust_class='T-1',
        source_uri='test://held', writer_identity='writer:producer')['artifact_id']
    child = admit(daemon, 'child', derives_from=[parent, held, '0' * 64])
    daemon.demote(parent, 'withdraw content', keys.operator_identity(daemon.operator_pubkey))
    kg = doublegate_sdk.knowledge_graph
    reader = kg.OrgGraphDiagnosticReader(daemon)
    result = reader.read(kg.GraphQuery((child,)), api_key=admin['raw'])
    assert {n.artifact_id for n in result.nodes} == {child}
    assert not result.edges
    result = reader.read(kg.GraphQuery((held, parent, 'missing')), api_key=admin['raw'])
    assert not result.nodes and not result.edges


def test_direction_depth_and_detached_export(gate):
    daemon, admin = gate
    grandparent = admit(daemon, 'grandparent')
    parent = admit(daemon, 'parent', derives_from=[grandparent])
    child = admit(daemon, 'child', derives_from=[parent])
    kg = doublegate_sdk.knowledge_graph
    reader = kg.OrgGraphDiagnosticReader(daemon)
    result = reader.read(kg.GraphQuery((child,)), api_key=admin['raw'])
    assert {n.artifact_id for n in result.nodes} == {parent, child}
    exported = {r['artifact_id']: r for r in daemon._memories_for_api(None)}
    for node in result.nodes:
        assert dict(node.provenance) == exported[node.artifact_id]['provenance']
        with pytest.raises(TypeError):
            node.provenance['source_uri'] = 'changed'
        with pytest.raises(FrozenInstanceError):
            node.body = 'changed'
    upward = reader.read(kg.GraphQuery((grandparent,)), api_key=admin['raw'])
    assert {n.artifact_id for n in upward.nodes} == {grandparent}
    assert not upward.edges
    assert not reader.read(kg.GraphQuery(()), api_key=admin['raw']).nodes


@pytest.mark.parametrize('roots', ['an-id', ['an-id'], (1,), ('',)])
def test_query_rejects_malformed_roots(roots):
    with pytest.raises((TypeError, ValueError)):
        doublegate_sdk.knowledge_graph.GraphQuery(roots)
