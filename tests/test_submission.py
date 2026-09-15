"""Pure submission contract, independent of gates and their authority."""
import importlib.util

from submission_fixtures import document, sidecars


def test_sdk_owns_byte_contract():
    assert importlib.util.find_spec("doublegate_sdk.submission") is not None
    from doublegate_sdk.envelope import make_envelope
    from doublegate_sdk.submission import decode_submission, encode_submission
    blob = bytes(range(256))
    env = make_envelope(blob=blob, content_type="imported_document", trust_class="T-5",
                        source_uri="test://inert", writer_identity="test", deployment_id="test", ingest_ts=1)
    body = encode_submission(blob, document(env.to_dict()), max_content_bytes=256, **sidecars(env.to_dict()))
    assert body["document"]["envelope"] == env.to_dict()
    assert body["encoding"] == "base64"
    assert decode_submission(body, body["artifact_id"], max_content_bytes=256) == blob
