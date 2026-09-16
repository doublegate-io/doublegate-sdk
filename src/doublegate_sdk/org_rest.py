"""The organization gate's two REST doors a fleet client speaks (ADR-0074 d6):
``PUT /submissions/{artifact_id}`` (ADR-0030) and ``GET /outcomes`` (ADR-0041).
The body a submission carries is ``doublegate_sdk.submission``'s; this module
only moves it and classifies what the door said. Policy — what to do about a
retry, where to keep a cursor — is the caller's.
"""
from __future__ import annotations

import http.client
import json
import urllib.parse
from dataclasses import dataclass
from typing import Any

from doublegate_sdk.errors import GateError

ACCEPTED, RETRY, STOPPED, REFUSED = 'accepted', 'retry', 'stopped', 'refused'


@dataclass(frozen=True)
class SubmissionOutcome:
    """What the door said about one PUT, in the four words the outbox acts on:
    ``accepted`` (202/200), ``retry`` (503/429 with the door's delay, or any
    unexpected status), ``stopped`` (401/403: a key problem does not become
    correct by waiting), ``refused`` (400/413/422: this body, not the key)."""
    result: str
    status_code: int | None
    retry_after_s: float | None
    reason: str


@dataclass(frozen=True)
class OutcomesPage:
    events: tuple[dict[str, Any], ...]
    next: int | None


def _connection(endpoint: str, timeout_s: float) -> tuple[http.client.HTTPConnection, str]:
    u = urllib.parse.urlsplit(endpoint)
    if u.scheme not in ('http', 'https') or not u.hostname:
        raise ValueError(f'bad endpoint {endpoint!r}')
    cls = http.client.HTTPSConnection if u.scheme == 'https' else http.client.HTTPConnection
    conn = cls(u.hostname, u.port or (443 if u.scheme == 'https' else 80), timeout=timeout_s)
    return conn, (u.path.rstrip('/') or '')


def reason_of(body: bytes) -> str:
    """The door's ``error`` field, or a short prefix of the body."""
    try:
        data = json.loads(body)
        if isinstance(data, dict) and isinstance(data.get('error'), str):
            return data['error'][:200]
    except ValueError:
        pass
    return body[:200].decode('utf-8', 'replace')


def classify_submission_status(code: int, retry_after: str | None, body: bytes) -> SubmissionOutcome:
    if code in (202, 200):
        return SubmissionOutcome(ACCEPTED, code, None, '')
    if code in (503, 429):
        delay: float | None
        try:
            delay = float(retry_after) if retry_after is not None else None
        except ValueError:
            delay = None
        return SubmissionOutcome(RETRY, code, delay, 'backpressure')
    if code in (401, 403):
        return SubmissionOutcome(STOPPED, code, None, f'authz: HTTP {code} — check the token')
    if code in (400, 413, 422):
        return SubmissionOutcome(REFUSED, code, None, f'door: HTTP {code} — {reason_of(body)}')
    return SubmissionOutcome(RETRY, code, None, f'unexpected HTTP {code}')


def put_submission(endpoint: str, artifact_id: str, body: bytes, token: str, *,
                   timeout_s: float = 10.0) -> SubmissionOutcome:
    """One PUT. A transport failure raises ``GateError('unavailable')``; the
    caller decides that it is retryable, because that is a policy question."""
    if not isinstance(token, str) or not token or any(c.isspace() for c in token):
        raise ValueError('token must be nonempty text without whitespace')
    conn, base = _connection(endpoint, timeout_s)
    try:
        conn.request('PUT', f'{base}/submissions/{artifact_id}', body=body,
                     headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {token}'})
        resp = conn.getresponse()
        return classify_submission_status(resp.status, resp.getheader('Retry-After'), resp.read())
    except (OSError, http.client.HTTPException) as exc:
        raise GateError('unavailable', outcome_unknown=True, detail=f'transport: {exc}') from None
    finally:
        conn.close()


def pull_outcomes(endpoint: str, token: str, *, since: int, limit: int = 500,
                  timeout_s: float = 10.0) -> OutcomesPage:
    """``GET /outcomes?since=<cursor>&limit=<n>``. ``auth``/``scope`` on 401/403,
    ``remote_error`` on any other non-200, ``invalid_response`` on a non-JSON body."""
    if type(since) is not int or since < 0 or type(limit) is not int or limit < 1:
        raise ValueError('since must be a nonnegative and limit a positive integer')
    conn, base = _connection(endpoint, timeout_s)
    try:
        conn.request('GET', f'{base}/outcomes?since={since}&limit={limit}',
                     headers={'Accept': 'application/json', 'Authorization': f'Bearer {token}'})
        resp = conn.getresponse()
        code, data = resp.status, resp.read()
    except (OSError, http.client.HTTPException) as exc:
        raise GateError('unavailable', detail=f'transport: {exc}') from None
    finally:
        conn.close()
    if code in (401, 403):
        raise GateError('auth' if code == 401 else 'scope', detail=reason_of(data))
    if code != 200:
        raise GateError('remote_error', detail=f'HTTP {code}')
    try:
        body = json.loads(data)
    except ValueError:
        raise GateError('invalid_response') from None
    events = body.get('events') if isinstance(body, dict) else None
    if not isinstance(events, list) or not all(isinstance(e, dict) for e in events):
        raise GateError('invalid_response')
    nxt = body.get('next')
    return OutcomesPage(tuple(events), nxt if type(nxt) is int else None)
