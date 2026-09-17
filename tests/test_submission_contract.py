"""Wire contract only: validation is not authentication or admission."""
import hashlib

import pytest

from doublegate_sdk.envelope import Envelope, EnvelopeError, make_envelope
from doublegate_sdk.submission import SubmissionTooLarge, decode_submission, encode_submission
from submission_fixtures import fixture, document, sidecars


def envelope(blob=b"f"):
    return make_envelope(blob=blob, content_type="imported_document", trust_class="T-5",
                         source_uri="test://inert", writer_identity="test",
                         deployment_id="test", ingest_ts=1)


def body(blob=b"f"):
    return fixture(blob)


@pytest.mark.parametrize("field", ["content_hash", "derives_from"])
def test_envelope_rejects_hash_with_trailing_newline(field):
    mapping = envelope().to_dict()
    malformed = "a" * 64 + "\n"
    mapping[field] = [malformed] if field == "derives_from" else malformed
    with pytest.raises(EnvelopeError, match=field):
        Envelope.from_mapping(mapping)


@pytest.mark.parametrize("blob", [b"", b"\x00\xff\x80", bytes(range(256)), b"PK\x03\x04\x00\xff", "é😀".encode()])
def test_exact_bytes_round_trip(blob):
    env = envelope(blob)
    encoded = encode_submission(blob, document(env.to_dict()), max_content_bytes=len(blob), **sidecars(env.to_dict()))
    assert encoded == body(blob)
    assert decode_submission(encoded, encoded["artifact_id"], max_content_bytes=len(blob)) == blob
    assert hashlib.sha256(blob).hexdigest() == encoded["document"]["envelope"]["content_hash"]


@pytest.mark.parametrize("encoded", ["Zg", "Zg=", "Zg===", "Zg==\n", " Zg==", "Zh==", "Zm9=", "_w==", "é", "!!!!"])
def test_noncanonical_base64_is_rejected(encoded):
    wire = body()
    wire["content"] = encoded
    with pytest.raises(ValueError, match="base64"):
        decode_submission(wire, max_content_bytes=256)


@pytest.mark.parametrize("encoding", [None, "utf-8", "BASE64", "", 1])
def test_only_explicit_base64_encoding_supported(encoding):
    wire = body()
    wire["encoding"] = encoding
    with pytest.raises(ValueError, match="encoding"):
        decode_submission(wire, max_content_bytes=256)


def test_omitted_encoding_is_strict_utf8_not_base64_or_replacement():
    text = "é😀 Zg=="
    wire = body(text.encode())
    wire.pop("encoding")
    wire["content"] = text
    assert decode_submission(wire, max_content_bytes=len(text.encode())) == text.encode()
    wire["content"] = "\ud800"
    with pytest.raises(ValueError, match="UTF-8"):
        decode_submission(wire, max_content_bytes=256)


@pytest.mark.parametrize("field,value,message", [("content_hash", "0" * 64, "content_hash"), ("size_bytes", 0, "size_bytes"), ("size_bytes", 2, "size_bytes")])
def test_decoded_hash_and_length_must_match(field, value, message):
    wire = body()
    wire["document"]["envelope"][field] = value
    with pytest.raises(ValueError, match=message):
        decode_submission(wire, max_content_bytes=256)
    with pytest.raises(ValueError, match=message):
        encode_submission(b"f", wire["document"], max_content_bytes=256,
                          **sidecars(envelope().to_dict()))


@pytest.mark.parametrize("field,value", [("artifact_id", "0" * 64), ("state", "admitted"), ("size_bytes", True), ("size_bytes", -1), ("ingest_ts", None), ("envelope_version", 2), ("trust_class", "T-9"), ("derives_from", [1]), ("content_hash", "A" * 64), ("content_hash", "a" * 63), ("content_hash", "a" * 65)])
def test_invalid_envelope_refused(field, value):
    wire = body()
    wire["document"]["envelope"][field] = value
    with pytest.raises(EnvelopeError):
        decode_submission(wire, max_content_bytes=256)


@pytest.mark.parametrize("wire", [None, [], {},
    {**body(), "document": {**body()["document"], "envelope": []}},
    {**body(), "document": {**body()["document"], "envelope": {}}},
    {key: value for key, value in body().items() if key != "content"},
    {**body(), "content": b"f"}])
def test_missing_or_invalid_submission_refused(wire):
    with pytest.raises(ValueError):
        decode_submission(wire, max_content_bytes=256)


@pytest.mark.parametrize("limit", [-1, True, False, 1.0, "1", None])
def test_caller_limit_requires_nonnegative_integer(limit):
    with pytest.raises(ValueError, match="max_content_bytes"):
        decode_submission(body(), max_content_bytes=limit)
    with pytest.raises(ValueError, match="max_content_bytes"):
        encode_submission(b"f", document(envelope().to_dict()), max_content_bytes=limit, **sidecars(envelope().to_dict()))


def test_limit_is_required_not_sdk_policy():
    with pytest.raises(TypeError, match="max_content_bytes"):
        decode_submission(body())
    with pytest.raises(TypeError, match="max_content_bytes"):
        encode_submission(b"f", envelope().to_dict())
    blob = b"x" * (1024 * 1024 + 1)
    assert decode_submission(encode_submission(blob, document(envelope(blob).to_dict()), max_content_bytes=len(blob), **sidecars(envelope(blob).to_dict())), max_content_bytes=len(blob)) == blob


def test_limits_cover_declared_encoded_and_decoded_sizes():
    with pytest.raises(SubmissionTooLarge):
        encode_submission(b"f", document(envelope().to_dict()), max_content_bytes=0, **sidecars(envelope().to_dict()))
    with pytest.raises(SubmissionTooLarge):
        decode_submission(body(), max_content_bytes=0)
    wire = body(b"")
    wire["content"] = "Zg=="
    with pytest.raises(SubmissionTooLarge, match="encoded"):
        decode_submission(wire, max_content_bytes=0)
    wire["content"] = "YWJj"
    with pytest.raises(SubmissionTooLarge, match="decoded"):
        decode_submission(wire, max_content_bytes=1)
    wire.pop("encoding")
    wire["content"] = "é"
    with pytest.raises(SubmissionTooLarge, match="decoded"):
        decode_submission(wire, max_content_bytes=1)


def test_id_binding_is_derived_and_not_an_authority_check():
    wire = body()
    original_id = wire["artifact_id"]
    assert decode_submission(wire, original_id, max_content_bytes=1) == b"f"
    with pytest.raises(ValueError, match="different artifact"):
        decode_submission(wire, "0" * 64, max_content_bytes=1)
    wire["document"]["envelope"]["writer_identity"] = "unverified assertion"
    # The event is hashed over its own bytes, so editing the envelope inside it
    # moves the id the body and the route both name.
    with pytest.raises(ValueError, match="different artifact"):
        decode_submission(wire, original_id, max_content_bytes=1)
    with pytest.raises(ValueError, match="different artifact"):
        decode_submission(wire, max_content_bytes=1)
    # Fresh test signatures restore consistency, not gate authority.
    wire["document"] = document(wire["document"]["envelope"])
    wire.update(sidecars(wire["document"]["envelope"]))
    from doublegate_sdk.submission import SubmissionEvent
    wire["artifact_id"] = SubmissionEvent.from_mapping(wire["document"]).artifact_id
    assert decode_submission(wire, max_content_bytes=1) == b"f"


def test_encoding_does_not_mutate_envelope_or_add_authority():
    mapping = envelope().to_dict()
    before = mapping.copy()
    result = encode_submission(b"f", document(mapping), max_content_bytes=1, **sidecars(mapping))
    assert mapping == before
    assert set(result) == {"document", "artifact_id", "signature", "content", "encoding",
                           "space", "local_verdicts", "local_promotion",
                           "production_evidence_manifest"}
    assert result["document"]["envelope"] is not mapping


@pytest.mark.parametrize("content", ["f", bytearray(b"f"), None])
def test_encoder_requires_bytes(content):
    with pytest.raises(ValueError, match="content must be bytes"):
        encode_submission(content, document(envelope().to_dict()), max_content_bytes=1, **sidecars(envelope().to_dict()))
