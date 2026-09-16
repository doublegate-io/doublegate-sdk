"""The organization gate's REST doors: each status lands in the bucket the outbox acts on."""
import json

import pytest

from doublegate_sdk.errors import GateError
from doublegate_sdk.org_rest import classify_submission_status, pull_outcomes, put_submission


@pytest.mark.parametrize('code,retry_after,result,delay', [
    (202, None, 'accepted', None), (200, None, 'accepted', None),
    (503, '7', 'retry', 7.0), (429, None, 'retry', None), (503, 'soon', 'retry', None),
    (401, None, 'stopped', None), (403, None, 'stopped', None),
    (400, None, 'refused', None), (413, None, 'refused', None), (422, None, 'refused', None),
    (500, None, 'retry', None), (302, None, 'retry', None),
])
def test_each_door_status_lands_in_the_bucket_the_outbox_used(code, retry_after, result, delay):
    out = classify_submission_status(code, retry_after, b'{"error":"why"}')
    assert (out.result, out.status_code, out.retry_after_s) == (result, code, delay)
    if result == 'refused':
        assert 'why' in out.reason


def test_put_submission_sends_the_bearer_and_classifies_the_answer(http_server):
    seen = []

    def handler(method, path, headers, body):
        seen.append((method, path, headers.get('Authorization'), body))
        return 202, {'Location': path}, b'{}'
    base = http_server(handler)
    out = put_submission(base + '/org', 'abc', b'{"x":1}', 'dgk_1', timeout_s=2)
    assert out.result == 'accepted' and seen == [('PUT', '/org/submissions/abc', 'Bearer dgk_1', b'{"x":1}')]
    with pytest.raises(GateError) as err:
        put_submission('http://127.0.0.1:9', 'abc', b'{}', 'dgk_1', timeout_s=0.5)
    assert err.value.kind == 'unavailable' and err.value.outcome_unknown is True
    with pytest.raises(ValueError):
        put_submission(base, 'abc', b'{}', 'has space')


def test_pull_outcomes_pages_by_cursor_and_names_a_key_problem(http_server):
    def handler(method, path, headers, body):
        if headers.get('Authorization') != 'Bearer good':
            return 401, {}, b'{"error":"unauthorized"}'
        assert path == '/outcomes?since=5&limit=2'
        return 200, {}, json.dumps({'events': [{'event_id': 'e1'}, {'event_id': 'e2'}], 'next': 7}).encode()
    base = http_server(handler)
    page = pull_outcomes(base, 'good', since=5, limit=2)
    assert [e['event_id'] for e in page.events] == ['e1', 'e2'] and page.next == 7
    with pytest.raises(GateError, match='auth'):
        pull_outcomes(base, 'bad', since=5, limit=2)
    base = http_server(lambda m, p, h, b: (200, {}, b'not json'))
    with pytest.raises(GateError, match='invalid_response'):
        pull_outcomes(base, 'good', since=0)
