"""The knowledge client: what an agent may do with a gate (ADR-0068 d2, d4).

Propose, learn, ask, annotate, weight for an audience. Never decide: no
method here signs, promotes, demotes, rejects or relates, and the transport
refuses those verbs before I/O. Identity is the connection's — nothing here
takes or sends a writer identity (I2). A successful proposal is a receipt,
not an admission; ``proposals_are_admission`` is False on every result.
"""
from __future__ import annotations

import base64
import json
import time
from collections.abc import Collection, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar

from doublegate_sdk.capabilities import Capabilities
from doublegate_sdk.errors import GateError
from doublegate_sdk.inventory import InventoryPage
from doublegate_sdk.operations import CURATION
from doublegate_sdk.transport import GateTransport

LEARNING_KINDS: frozenset[str] = frozenset({'derived_fact', 'summary', 'memory'})
COMMENT_KINDS: frozenset[str] = frozenset({'note', 'question', 'objection', 'resolution'})
_FILTERS = frozenset({'state', 'space', 'content_type', 'trust_class', 'retrievability', 'source', 'q', 'sort', 'descending'})


def _integer(value: Any, minimum: int) -> bool:
    return type(value) is int and value >= minimum


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f'{name} must be a nonempty string')
    return value


def _ids(value: Any, name: str) -> list[str]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise ValueError(f'{name} must be a sequence of artifact ids')
    out = [_text(v, name) for v in value]
    if len(set(out)) != len(out):
        raise ValueError(f'{name} repeats an artifact id')
    return out


@dataclass(frozen=True)
class Proposal:
    """The gate's receipt for one proposal. ``state`` is the gate's word, not ours."""
    artifact_id: str
    state: str
    duplicate: bool = False
    findings_count: int | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)
    proposals_are_admission: ClassVar[bool] = False

    @classmethod
    def from_response(cls, result: Any) -> Proposal:
        if (not isinstance(result, dict) or not isinstance(result.get('artifact_id'), str) or not result['artifact_id']
                or not isinstance(result.get('state'), str) or not result['state']):
            raise GateError('invalid_response', outcome_unknown=True)
        fc = result.get('findings_count')
        return cls(result['artifact_id'], result['state'], bool(result.get('duplicate', False)),
                   fc if type(fc) is int else None, dict(result))


@dataclass(frozen=True)
class Learning(Proposal):
    """A learning: content plus the evidence it derives from. ``proposed_supersedes``
    names the claim it would replace; only a gate decision replaces belief (ADR-0049 d5)."""
    kind: str = 'derived_fact'
    derives_from: tuple[str, ...] = ()
    proposed_supersedes: str | None = None


class KnowledgeClient:
    """An agent's view of a gate through an explicit transport of scope ``read`` or ``knowledge``."""

    def __init__(self, transport: GateTransport, *, probe: GateTransport | None = None):
        scope = getattr(transport, 'scope', None)
        if scope == CURATION:
            raise ValueError('the knowledge client refuses a curation-scoped transport: an agent never decides')
        self._transport = transport
        self._probe = probe if probe is not None else transport
        self._capabilities: Capabilities | None = None

    # ---- liveness and the catalog ----

    def present(self) -> bool:
        """Is a gate answering? Never raises; a refusal or an outage is ``False``."""
        try:
            answer = self._probe.call('dg.ping', {})
        except GateError:
            return False
        return isinstance(answer, dict) and bool(answer.get('pong'))

    def ping(self) -> dict[str, Any]:
        return self._object(self._transport.call('dg.ping', {}))

    def describe(self, *, refresh: bool = False) -> Capabilities:
        if self._capabilities is None or refresh:
            self._capabilities = Capabilities.from_describe(self._object(self._transport.call('dg.describe', {})))
        return self._capabilities

    # ---- reads ----

    def status(self, artifact_id: str | None = None) -> dict[str, Any]:
        params = {} if artifact_id is None else {'artifact_id': _text(artifact_id, 'artifact_id')}
        return self._object(self._transport.call('dg.status', params))

    def why(self, artifact_id: str) -> dict[str, Any]:
        return self._one('dg.why', artifact_id)

    def tip(self, artifact_id: str) -> dict[str, Any]:
        return self._one('dg.tip', artifact_id)

    def relations(self, artifact_id: str) -> dict[str, Any]:
        return self._one('dg.relations', artifact_id)

    def approval(self, artifact_id: str) -> dict[str, Any]:
        return self._one('dg.approval', artifact_id)

    def node(self, artifact_id: str) -> dict[str, Any]:
        return self._one('dg.node', artifact_id)

    def comments(self, artifact_id: str, *, include_retracted: bool = False) -> dict[str, Any]:
        if type(include_retracted) is not bool:
            raise ValueError('include_retracted must be boolean')
        return self._object(self._transport.call('dg.comments', {'artifact_id': _text(artifact_id, 'artifact_id'),
                                                                 'include_retracted': include_retracted}))

    def pending(self, *, limit: int = 100) -> dict[str, Any]:
        if not _integer(limit, 1):
            raise ValueError('limit must be a positive integer')
        return self._object(self._transport.call('dg.pending', {'limit': limit}))

    def inventory(self, *, limit: int = 100, offset: int = 0, **filters: Any) -> InventoryPage:
        if not _integer(limit, 1) or not _integer(offset, 0):
            raise ValueError('limit must be positive and offset nonnegative integers')
        if set(filters) - _FILTERS:
            raise ValueError('unsupported inventory parameter')
        result = self._transport.call('dg.inventory', {'limit': limit, 'offset': offset, **filters})
        return InventoryPage.from_response(self._object(result), requested_offset=offset)

    def inventory_pages(self, *, limit: int = 100, max_pages: int = 10, **filters: Any) -> Iterator[InventoryPage]:
        if not _integer(max_pages, 1):
            raise ValueError('max_pages must be a positive integer')
        offset = 0
        for _ in range(max_pages):
            page = self.inventory(limit=limit, offset=offset, **filters)
            yield page
            if not page.has_more:
                return
            if page.next_offset is None:
                raise GateError('invalid_response')
            offset = page.next_offset
        raise GateError('page_limit')

    def recall(self, query: str, *, limit: int = 10, spaces: list[str] | None = None,
               include_provisional: bool = False, include_superseded: bool = False,
               include_hidden: bool = False, audience: str | None = None,
               mode: str | None = None) -> dict[str, Any]:
        """Served knowledge. The caller's own unreviewed rows are always excluded;
        provisional, superseded and hidden rows only on explicit request, and
        each hit keeps its flags — no score is relabelled as trust."""
        if not isinstance(query, str) or not query.strip() or not _integer(limit, 1):
            raise ValueError('nonempty query and positive integer limit required')
        for name, flag in (('include_provisional', include_provisional), ('include_superseded', include_superseded),
                           ('include_hidden', include_hidden)):
            if type(flag) is not bool:
                raise ValueError(f'{name} must be boolean')
        if spaces is not None and (not isinstance(spaces, list) or not spaces
                                   or any(not isinstance(s, str) or not s for s in spaces)):
            raise ValueError('spaces must be a nonempty list of strings')
        params: dict[str, Any] = {'query': query, 'k': limit, 'include_own_pending': False,
                                  'include_provisional': include_provisional}
        if include_superseded:
            params['include_superseded'] = True
        if include_hidden:
            params['include_hidden'] = True
        if spaces is not None:
            params['spaces'] = spaces
        if audience is not None:
            params['audience'] = _text(audience, 'audience')
        if mode is not None:
            params['mode'] = _text(mode, 'mode')
        result = self._object(self._transport.call('dg.recall', params))
        hits = result.get('hits')
        if not isinstance(hits, list) or not all(isinstance(h, dict) for h in hits) or len(hits) > limit:
            raise GateError('invalid_response')
        return result

    # ---- proposals ----

    def remember(self, content: str | bytes, *, content_type: str, source_uri: str,
                 trust_class: str = 'T-1', space: str = 'main',
                 derives_from: Sequence[str] = ()) -> Proposal:
        """Propose one artifact. Bytes travel base64 and unchanged; the gate
        scans before it answers and holds the row until a reviewer that did
        not write it promotes it. Never retried; never carries an identity."""
        if not isinstance(content, (str, bytes)):
            raise ValueError('content must be text or bytes')
        for name, value in (('content_type', content_type), ('trust_class', trust_class),
                            ('source_uri', source_uri), ('space', space)):
            _text(value, name)
        try:
            raw = content.encode('utf-8') if isinstance(content, str) else content
        except UnicodeEncodeError:
            raise ValueError('invalid_utf8') from None
        params: dict[str, Any] = {'content': base64.b64encode(raw).decode('ascii'), 'encoding': 'base64',
                                  'content_type': content_type, 'trust_class': trust_class,
                                  'source_uri': source_uri, 'space': space}
        parents = _ids(derives_from, 'derives_from')
        if parents:
            params['derives_from'] = parents
        return Proposal.from_response(self._transport.call('dg.ingest', params))

    def learn(self, text: str, *, source_uri: str, kind: str = 'derived_fact',
              evidence: Sequence[str] = (), space: str = 'main', trust_class: str = 'T-5',
              replaces: str | None = None) -> Learning:
        """A learning: a claim distilled from evidence, proposed with its provenance.

        ``evidence`` becomes ``derives_from`` (never hides a parent, ADR-0035 d1).
        ``replaces`` names the claim this one should supersede: it joins the
        provenance and a note is left on the new row saying so, because
        only a gate decision replaces belief and ``supersedes`` is the
        operator's relation (ADR-0049 d5; GAPS 20). Default ``T-5``: a
        sentence a model wrote is a proposal about the world, not a fact.
        """
        if kind not in LEARNING_KINDS:
            raise ValueError(f'kind must be one of {sorted(LEARNING_KINDS)}')
        if not isinstance(text, str) or not text.strip():
            raise ValueError('a learning needs text')
        parents = _ids(evidence, 'evidence')
        if replaces is not None:
            _text(replaces, 'replaces')
            if replaces not in parents:
                parents.append(replaces)
        proposal = self.remember(text, content_type=kind, source_uri=source_uri, trust_class=trust_class,
                                 space=space, derives_from=parents)
        if replaces is not None and not proposal.duplicate:
            try:
                self.annotate(proposal.artifact_id, f'proposes: supersedes {replaces}', kind='note')
            except GateError as exc:
                if exc.kind not in ('forbidden_operation', 'writes_disabled', 'unsupported_operation'):
                    raise
        return Learning(proposal.artifact_id, proposal.state, proposal.duplicate, proposal.findings_count,
                        proposal.raw, kind, tuple(parents), replaces)

    def propose_skill(self, text: str, *, source_uri: str, space: str = 'main',
                      trust_class: str = 'T-5', derives_from: Sequence[str] = ()) -> Proposal:
        return self.remember(text, content_type='skill', source_uri=source_uri, trust_class=trust_class,
                             space=space, derives_from=derives_from)

    def propose_prompt_template(self, text: str, *, source_uri: str, space: str = 'main',
                                trust_class: str = 'T-5', derives_from: Sequence[str] = ()) -> Proposal:
        return self.remember(text, content_type='prompt_template', source_uri=source_uri,
                             trust_class=trust_class, space=space, derives_from=derives_from)

    def propose_bundle(self, archive: bytes, *, fetch_artifact_id: str, source_uri: str,
                       manifest: dict[str, Any] | None = None, space: str = 'main') -> dict[str, Any]:
        """A skill bundle: the archive is inspected here first (``skill_bundle``),
        then handed to ``dg.ingest_bundle`` beside the fetch record it derives
        from. The gate decides the class; no trust class travels."""
        from doublegate_sdk.skill_bundle import BundleError, inspect_zip
        if not isinstance(archive, bytes) or not archive:
            raise ValueError('archive must be nonempty bytes')
        _text(fetch_artifact_id, 'fetch_artifact_id'); _text(source_uri, 'source_uri'); _text(space, 'space')
        try:
            inspect_zip(archive)
        except BundleError as exc:
            raise ValueError(f'archive refused before the wire: {exc}') from None
        if not self.describe().dispatches('dg.ingest_bundle'):
            raise GateError('unsupported_operation', -32601, detail='this gate does not dispatch dg.ingest_bundle')
        params: dict[str, Any] = {'content': base64.b64encode(archive).decode('ascii'), 'source_uri': source_uri,
                                  'fetch_artifact_id': fetch_artifact_id, 'space': space}
        if manifest is not None:
            if not isinstance(manifest, dict):
                raise ValueError('manifest must be an object')
            params['manifest'] = manifest
        return self._object(self._transport.call('dg.ingest_bundle', params))

    # ---- annotations (ADR-0066): remarks, never decisions ----

    def annotate(self, artifact_id: str, body: str, *, kind: str = 'note',
                 reply_to: str | None = None) -> dict[str, Any]:
        if kind not in COMMENT_KINDS:
            raise ValueError(f'kind must be one of {sorted(COMMENT_KINDS)}')
        if kind == 'resolution' and not reply_to:
            raise ValueError('a resolution replies to a comment: reply_to is required')
        params: dict[str, Any] = {'artifact_id': _text(artifact_id, 'artifact_id'), 'body': _text(body, 'body'), 'kind': kind}
        if reply_to is not None:
            params['reply_to'] = _text(reply_to, 'reply_to')
        return self._object(self._transport.call('dg.comment', params))

    def raise_objection(self, artifact_id: str, body: str) -> dict[str, Any]:
        """An objection on the record. Only a human's objection holds promotion
        (ADR-0066); an agent's is a remark the reviewer will see (GAPS 21)."""
        return self.annotate(artifact_id, body, kind='objection')

    def resolve(self, artifact_id: str, body: str, *, reply_to: str) -> dict[str, Any]:
        return self.annotate(artifact_id, body, kind='resolution', reply_to=reply_to)

    def retract_comment(self, artifact_id: str, event_id: str, *, reason: str = '') -> dict[str, Any]:
        params: dict[str, Any] = {'artifact_id': _text(artifact_id, 'artifact_id'), 'event_id': _text(event_id, 'event_id')}
        if reason:
            params['reason'] = reason
        return self._object(self._transport.call('dg.comment_retract', params))

    def defer(self, artifact_id: str) -> dict[str, Any]:
        return self._one('dg.defer', artifact_id)

    # ---- serving for an audience (ADR-0052 d5: any identity but the writer's) ----

    def rank(self, artifact_id: str, *, audience: str, weight: float, reason: str = '') -> dict[str, Any]:
        if type(weight) not in (int, float) or not 0 < weight <= 10.0:
            raise ValueError('weight must be a number in (0, 10]')
        params: dict[str, Any] = {'artifact_id': _text(artifact_id, 'artifact_id'),
                                  'audience': _text(audience, 'audience'), 'weight': float(weight)}
        if reason:
            params['reason'] = reason
        return self._object(self._transport.call('dg.rank', params))

    def hide(self, artifact_id: str, *, reason: str = '', audience: str = 'all', lift: bool = False) -> dict[str, Any]:
        if type(lift) is not bool:
            raise ValueError('lift must be boolean')
        if not lift and not reason:
            raise ValueError('a hide needs a reason; lift=True reverses one')
        params: dict[str, Any] = {'artifact_id': _text(artifact_id, 'artifact_id'), 'audience': _text(audience, 'audience')}
        if lift:
            params['lift'] = True
        else:
            params['reason'] = reason
        return self._object(self._transport.call('dg.hide', params))

    # ---- waiting on the gate ----

    def wait_for(self, artifact_id: str, states: Collection[str], *, timeout: float = 30.0,
                 interval: float = 0.5) -> dict[str, Any]:
        """Poll ``status`` until the row is in one of ``states`` or the deadline
        passes; returns the last row either way, with ``reached`` saying which."""
        wanted = frozenset(_text(s, 'states') for s in states)
        if not wanted:
            raise ValueError('states must name at least one state')
        if type(timeout) not in (int, float) or timeout < 0 or type(interval) not in (int, float) or interval <= 0:
            raise ValueError('timeout must be nonnegative and interval positive')
        deadline = time.monotonic() + timeout
        while True:
            row = self.status(artifact_id)
            if row.get('state') in wanted:
                return {**row, 'reached': True}
            if time.monotonic() >= deadline:
                return {**row, 'reached': False}
            time.sleep(min(interval, max(0.0, deadline - time.monotonic())))

    # ---- helpers ----

    def _one(self, method: str, artifact_id: str) -> dict[str, Any]:
        return self._object(self._transport.call(method, {'artifact_id': _text(artifact_id, 'artifact_id')}))

    @staticmethod
    def _object(result: Any) -> dict[str, Any]:
        if not isinstance(result, dict):
            raise GateError('invalid_response')
        return result


def as_json(value: Any) -> str:
    """Canonical text for a structured learning body: sorted keys, no NaN, unicode kept."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
