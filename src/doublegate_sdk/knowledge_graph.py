"""Gate-local, admin-only diagnostic snapshot of the legacy artifact graph.

This is an adapter to OrgDaemon, not a new store or a remote API. A space
refinement is not tenant/team authorization. No UUID claim association, trust
assessment, evidence manifest, or assertion withdrawal is invented here.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, ClassVar, Mapping


@dataclass(frozen=True)
class GraphQuery:
    """Outgoing one-hop neighborhood of already served legacy artifact roots."""

    roots: tuple[str, ...]

    def __post_init__(self):
        if not isinstance(self.roots, tuple):
            raise TypeError("roots must be a tuple of artifact IDs")
        if any(not isinstance(root, str) or not root for root in self.roots):
            raise ValueError("roots must contain nonempty artifact IDs")


@dataclass(frozen=True)
class ArtifactNode:
    """Legacy identity from /memories, NOT a submission ID or claim UUID."""

    artifact_id: str
    space: str
    content_type: str
    body: str
    provenance: Mapping[str, Any]
    identity_domain: ClassVar[str] = 'legacy_artifact'


@dataclass(frozen=True)
class RelationEdge:
    """Original gate direction and attribution; event_id can be empty."""

    source: str
    kind: str
    target: str
    event_id: str
    identity: str
    ts: int


@dataclass(frozen=True)
class GraphResult:
    connection_id: str
    nodes: tuple[ArtifactNode, ...]
    edges: tuple[RelationEdge, ...]


class OrgGraphDiagnosticReader:
    """Organization-wide diagnostic reader, NOT an authorization wrapper.

    Requires a current admin API key on every read. Never mount behind a
    tenant/member/reader route. Reads the gate export and global hide projection
    under its lock, with no cache. Demoted, superseded, held, globally hidden
    and dangling endpoints are excluded. Audience-specific hides are NOT a
    scope boundary here. The legacy export does not guarantee review-authority
    withdrawal/revocation eligibility; results must not authorize content use.

    Traversal is outgoing, one hop only. Export work and fan-out are unbounded:
    the underlying gate API has no pagination or node/edge budget.
    """

    def __init__(self, daemon: Any):
        self._daemon = daemon

    def read(self, query: GraphQuery, *, api_key: str) -> GraphResult:
        daemon = self._daemon
        with daemon._lock:
            status, _ = daemon.apikeys.authorize(api_key, 'admin')
            if status != 200:
                raise PermissionError(f'graph read requires current admin authorization ({status})')
            rows = daemon._memories_for_api(None)
            hidden = daemon.serving.hidden_ids((row['artifact_id'] for row in rows), None)
            allowed = {row['artifact_id']: row for row in rows if row['artifact_id'] not in hidden}
            # Do not expose held metadata, pins, bans or dangling endpoints.
            edges = [RelationEdge(e['from'], e['kind'], e['to'], e['event_id'],
                                  e['identity'], e['ts'])
                     for e in daemon.graph_payload()['edges']
                     if e['from'] in allowed and e['to'] in allowed]
            selected = set(query.roots) & allowed.keys()
            selected.update(e.target for e in edges if e.source in query.roots)
            nodes = tuple(ArtifactNode(row['artifact_id'], row['space'],
                          row['content_type'], row['body'], MappingProxyType(dict(row['provenance'])))
                          for aid, row in sorted(allowed.items()) if aid in selected)
            return GraphResult(daemon.key.deployment_id, nodes,
                               tuple(e for e in edges if e.source in query.roots and e.target in selected))
