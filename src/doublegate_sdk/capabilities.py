"""What a running gate says it answers (``dg.describe``), parsed once.

The catalog is the schema (ADR-0044): a client learns a verb's presence,
its parameters and whether this build dispatches it from the gate itself,
never from prose. ``content_types`` comes from the structured ``enum`` on
``dg.ingest``'s ``content_type`` parameter; a gate that does not publish one
yields an empty set, and a caller must not read that as "accepts anything".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class VerbInfo:
    name: str
    params: tuple[str, ...]
    dispatched: bool
    proof: str
    role: str


@dataclass(frozen=True)
class Capabilities:
    role: str
    version: str
    verbs: dict[str, VerbInfo]
    content_types: frozenset[str]

    @classmethod
    def from_describe(cls, data: dict[str, Any]) -> Capabilities:
        if not isinstance(data, dict) or not isinstance(data.get('verbs'), list):
            raise ValueError('not a dg.describe answer')
        verbs: dict[str, VerbInfo] = {}
        content_types: set[str] = set()
        for verb in data['verbs']:
            if not isinstance(verb, dict) or not isinstance(verb.get('name'), str):
                continue
            params = verb.get('params') if isinstance(verb.get('params'), list) else []
            names = tuple(p['name'] for p in params if isinstance(p, dict) and isinstance(p.get('name'), str))
            verbs[verb['name']] = VerbInfo(verb['name'], names, bool(verb.get('dispatched', True)),
                                           str(verb.get('proof', 'none')), str(verb.get('role', 'both')))
            if verb['name'] == 'dg.ingest':
                for p in params:
                    if isinstance(p, dict) and p.get('name') == 'content_type' and isinstance(p.get('enum'), list):
                        content_types.update(t for t in p['enum'] if isinstance(t, str))
        return cls(str(data.get('role', 'any')), str(data.get('version', '')), verbs, frozenset(content_types))

    def dispatches(self, verb: str) -> bool:
        info = self.verbs.get(verb)
        return info is not None and info.dispatched

    def accepts_content_type(self, content_type: str) -> bool:
        return content_type in self.content_types

    def params_of(self, verb: str) -> tuple[str, ...]:
        info = self.verbs.get(verb)
        return info.params if info else ()
