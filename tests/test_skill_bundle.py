import io
import zipfile
import importlib
import hashlib


def zip_body(entries):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for path, body in entries:
            z.writestr(path, body)
    return out.getvalue()


def test_inert_manifest_preserves_paths_and_duplicate_bytes():
    import doublegate_sdk
    assert importlib.util.find_spec('doublegate_sdk.skill_bundle') is not None
    from doublegate_sdk.skill_bundle import inspect_zip
    data = zip_body([('SKILL.md', b'# Help'), ('scripts/a.py', b'pass'), ('assets/a', b'pass')])
    package = inspect_zip(data)
    assert package.manifest == {
        'bundle_version': 1, 'archive_hash': hashlib.sha256(data).hexdigest(),
        'archive_size': len(data), 'members': [
            {'path': p, 'content_hash': hashlib.sha256(b).hexdigest(), 'size_bytes': len(b), 'classification': c}
            for p, b, c in [('SKILL.md', b'# Help', 'skill'), ('assets/a', b'pass', 'asset'), ('scripts/a.py', b'pass', 'script')]
        ]
    }
    assert package.members['assets/a'] == package.members['scripts/a.py'] == b'pass'
