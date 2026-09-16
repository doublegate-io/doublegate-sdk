"""One error vocabulary; the server's text never reaches ``str``."""
import pytest

from doublegate_sdk.errors import CODE_KINDS, HTTP_KINDS, KINDS, GateError, kind_of_code, kind_of_status


def test_every_catalogued_code_maps_to_exactly_one_known_kind():
    for code, kind in CODE_KINDS.items():
        assert kind in KINDS and kind_of_code(code) == kind
    for status, kind in HTTP_KINDS.items():
        assert kind in KINDS and kind_of_status(status) == kind
    assert kind_of_code(-32603) == 'remote_error' and kind_of_status(500) == 'remote_error'


def test_the_servers_message_rides_detail_capped_and_never_str():
    err = GateError('refused', -32014, detail='secret-text ' * 100, retry_after_ms=None)
    assert str(err) == 'refused (-32014)'
    assert 'secret-text' not in str(err)
    assert err.detail is not None and len(err.detail) == 512 and err.detail.startswith('secret-text')


def test_an_unknown_kind_is_a_programming_error():
    with pytest.raises(ValueError):
        GateError('made-up')
