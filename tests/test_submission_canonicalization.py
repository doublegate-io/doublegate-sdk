"""P1-A/01 §2 canonicalization, content_digest and the ADR-0068 artifact_id.

Specification read at design main 72754b68 (ADR-0068 + its 2026-09-11 derivation
clarification) and P1-A/01 §1-§2. Every rule below cites the clause it enforces.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from doublegate_sdk.submission import (
    ENCODING_INVALID,
    ARTIFACT_ID_MISMATCH,
    CanonicalizationRefused,
    RouteIdMismatch,
    canonical_bytes,
    content_digest,
    submission_artifact_id,
    verify_route_artifact_id,
)


# --- §2.1/§2.2 encoding and member order ---------------------------------

def test_members_sort_by_utf16_code_unit_not_code_point():
    # U+FFFD (BMP) sorts AFTER U+10000 by code point and BEFORE it by UTF-16
    # code unit, because U+10000 leads with the surrogate 0xD800. JCS says
    # UTF-16. A code-point sort would emit these in the other order.
    doc = {"\uFFFD": 1, "\U00010000": 2}
    assert canonical_bytes(doc) == '{"\U00010000":2,"\uFFFD":1}'.encode("utf-8")


def test_no_insignificant_whitespace_and_utf8_no_bom():
    b = canonical_bytes({"b": 1, "a": [1, 2]})
    assert b == b'{"a":[1,2],"b":1}'
    assert not b.startswith(b"\xef\xbb\xbf")


def test_non_ascii_is_emitted_as_utf8_not_escaped():
    assert canonical_bytes({"k": "\u00e9"}) == b'{"k":\xc3\xa9}'.replace(b"\xc3\xa9", '"\u00e9"'.encode("utf-8"))


# --- §2.3 strings: NFC refused-not-normalized, minimal escaping -----------

def test_non_nfc_string_value_is_refused_not_normalized():
    with pytest.raises(CanonicalizationRefused) as e:
        canonical_bytes({"k": "e\u0301"})       # e + COMBINING ACUTE, NFD
    assert e.value.code == ENCODING_INVALID
    assert "NFC" in str(e.value)


def test_non_nfc_member_name_is_refused():
    with pytest.raises(CanonicalizationRefused):
        canonical_bytes({"e\u0301": 1})


def test_escapes_are_minimal_with_short_forms_and_lowercase_hex():
    out = canonical_bytes({"k": '\b\f\n\r\t"\\\u0001\u001f'}).decode("utf-8")
    assert out == '{"k":"\\b\\f\\n\\r\\t\\"\\\\\\u0001\\u001f"}'


def test_solidus_and_other_chars_are_never_escaped():
    assert canonical_bytes({"k": "a/b"}) == b'{"k":"a/b"}'


def test_lone_surrogate_is_refused():
    with pytest.raises(CanonicalizationRefused):
        canonical_bytes({"k": "\ud800"})


# --- §2.4 numbers ---------------------------------------------------------

def test_float_is_refused_outright_not_rounded():
    with pytest.raises(CanonicalizationRefused) as e:
        canonical_bytes({"k": 1.0})
    assert e.value.code == ENCODING_INVALID


def test_integer_above_2_53_minus_1_is_refused():
    with pytest.raises(CanonicalizationRefused):
        canonical_bytes({"k": 2 ** 53})


def test_max_safe_integer_is_accepted():
    assert canonical_bytes({"k": 2 ** 53 - 1}) == b'{"k":9007199254740991}'


def test_negative_integer_is_refused_no_sign_allowed():
    with pytest.raises(CanonicalizationRefused):
        canonical_bytes({"k": -1})


def test_bool_is_a_boolean_not_an_integer():
    assert canonical_bytes({"k": True, "j": False}) == b'{"j":false,"k":true}'


# --- §2.5 null is a value, not an absence --------------------------------

def test_null_is_serialized_and_differs_from_an_absent_member():
    assert canonical_bytes({"a": None}) == b'{"a":null}'
    assert canonical_bytes({"a": None}) != canonical_bytes({})


# --- §2.7 arrays ----------------------------------------------------------

def test_array_order_is_preserved_exactly():
    assert canonical_bytes({"a": [3, 1, 2]}) == b'{"a":[3,1,2]}'


def test_unsupported_type_is_refused():
    with pytest.raises(CanonicalizationRefused):
        canonical_bytes({"a": {1, 2}})


# --- ADR-0068 D2: content_digest is the CONTENT identity -----------------

def test_content_digest_is_sha256_of_the_document_bytes():
    blob = b"hello world"
    assert content_digest(blob) == "sha256:" + hashlib.sha256(blob).hexdigest()


def test_same_bytes_always_share_one_content_digest():
    # ADR-0068 D2: many submissions may share one content_digest; that is the
    # relation duplicate detection observes.
    assert content_digest(b"x") == content_digest(b"x")


# --- ADR-0068 clarification: artifact_id is the SUBMISSION EVENT ---------

def _doc(**over):
    d = {
        "contract": "doublegate.submission/1",
        "envelope": {"envelope_version": 1, "content_hash": "a" * 64, "content_type": "text/plain",
                     "trust_class": "T-1", "source_uri": "agent://x", "writer_identity": "uid:1",
                     "deployment_id": "d1", "ingest_ts": 1, "size_bytes": 1},
        "content_digest": "sha256:" + "a" * 64,
        "evidence_manifest_digest": "sha256:" + "b" * 64,
        "tenant_id": "t1",
        "team_id": "team1",
        "principal": {"kind": "person", "id": "p1", "sponsor": None},
        "membership": {"issuer": "https://i.example", "revision": 7,
                       "assertion_digest": "sha256:" + "c" * 64},
        "submitted_at": "2026-09-11T00:00:00.000Z",
        "review_refs": [],
    }
    d.update(over)
    return d


def test_artifact_id_is_sha256_of_the_submissions_own_canonical_bytes():
    doc = _doc()
    assert submission_artifact_id(doc) == "sha256:" + hashlib.sha256(canonical_bytes(doc)).hexdigest()


def test_artifact_id_is_not_a_member_of_the_signed_document():
    # step 1 of "what an implementer must do": artifact_id absent.
    with pytest.raises(CanonicalizationRefused):
        submission_artifact_id(_doc(artifact_id="sha256:" + "0" * 64))


def test_the_same_document_from_two_principals_yields_two_artifact_ids():
    # The consequence ADR-0068 D1 states so nobody discovers it later.
    a = submission_artifact_id(_doc())
    b = submission_artifact_id(_doc(principal={"kind": "person", "id": "p2", "sponsor": None}))
    assert a != b


def test_two_submissions_of_the_same_content_share_a_content_digest():
    a, b = _doc(), _doc(principal={"kind": "person", "id": "p2", "sponsor": None})
    assert a["content_digest"] == b["content_digest"]
    assert submission_artifact_id(a) != submission_artifact_id(b)


def test_a_member_outside_any_tuple_still_moves_the_identity():
    # Exactly the hazard shape (b) was rejected on: differing only in
    # review_refs must NOT collide.
    a = submission_artifact_id(_doc())
    b = submission_artifact_id(_doc(review_refs=[{"stage": "local", "record_digest": "sha256:" + "d" * 64}]))
    assert a != b


def test_identical_retry_computes_the_identical_id():
    assert submission_artifact_id(_doc()) == submission_artifact_id(json.loads(json.dumps(_doc())))


# --- the verifier: recompute from the received bytes, refuse on mismatch --

def test_verifier_accepts_a_route_id_matching_the_received_bytes():
    doc = _doc()
    aid = submission_artifact_id(doc)
    assert verify_route_artifact_id(aid, doc) == aid


def test_verifier_refuses_a_tampered_route_segment():
    with pytest.raises(RouteIdMismatch) as e:
        verify_route_artifact_id("sha256:" + "0" * 64, _doc())
    assert e.value.code == ARTIFACT_ID_MISMATCH


def test_verifier_refuses_a_tampered_body():
    doc = _doc()
    aid = submission_artifact_id(doc)
    with pytest.raises(RouteIdMismatch):
        verify_route_artifact_id(aid, _doc(tenant_id="t2"))


def test_verifier_accepts_raw_bytes_as_received():
    doc = _doc()
    raw = canonical_bytes(doc)
    assert verify_route_artifact_id(submission_artifact_id(doc), json.loads(raw.decode("utf-8"))) is not None


def test_route_id_must_be_lowercase_hex_with_the_sha256_prefix():
    doc = _doc()
    aid = submission_artifact_id(doc)
    with pytest.raises(RouteIdMismatch):
        verify_route_artifact_id(aid.upper(), doc)
    with pytest.raises(RouteIdMismatch):
        verify_route_artifact_id(aid.removeprefix("sha256:"), doc)
