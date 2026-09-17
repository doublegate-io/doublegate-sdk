"""Describe this SDK's Python API offline: the two clients, their methods, the
operation table and the transports. It describes this SDK, not
what a running gate answers — that is ``KnowledgeClient.describe()``."""
from __future__ import annotations

import inspect
from typing import Any

from doublegate_sdk.curation import CurationClient
from doublegate_sdk.errors import CODE_KINDS, HTTP_KINDS, KINDS
from doublegate_sdk.knowledge import KnowledgeClient, _FILTERS
from doublegate_sdk.operations import OPERATIONS, ROLES, SCOPES, in_scope

CLIENTS: dict[str, type] = {'knowledge': KnowledgeClient, 'curation': CurationClient}


def _methods(cls: type) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, method in inspect.getmembers(cls, inspect.isfunction):
        if name.startswith('_'):
            continue
        signature = inspect.signature(method)
        parameters = []
        for parameter in signature.parameters.values():
            if parameter.name == 'self':
                continue
            variadic = parameter.kind in (inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL)
            value: dict[str, Any] = {'name': parameter.name, 'kind': parameter.kind.name,
                                     'annotation': str(parameter.annotation),
                                     'required': parameter.default is inspect.Parameter.empty and not variadic}
            if parameter.default is not inspect.Parameter.empty:
                value['default'] = parameter.default if isinstance(parameter.default, (str, int, float, bool, type(None))) else repr(parameter.default)
            parameters.append(value)
        out[name] = {'parameters': parameters, 'returns': str(signature.return_annotation),
                     'doc': inspect.getdoc(method) or ''}
    return out


def describe_client() -> dict[str, Any]:
    return {
        'scope': 'sdk-contract-not-server-capabilities',
        'format': 'python-call-description-not-json-schema',
        'transports': ['unix_socket', 'http', 'caller_supplied'],
        'roles': list(ROLES),
        'proposals_are_admission': False, 'automatic_retries': False,
        'scopes': {scope: sorted(in_scope(scope)) for scope in SCOPES},
        'operations': {rpc: {'mutates': op.mutates, 'role': op.role, 'service': op.service, 'scope': op.scope}
                       for rpc, op in sorted(OPERATIONS.items())},
        'error_kinds': sorted(KINDS),
        'code_kinds': {str(k): v for k, v in sorted(CODE_KINDS.items())},
        'http_kinds': {str(k): v for k, v in sorted(HTTP_KINDS.items())},
        'inventory_filters': sorted(_FILTERS),
        'clients': {name: _methods(cls) for name, cls in CLIENTS.items()},
    }
