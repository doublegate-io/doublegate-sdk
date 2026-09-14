"""Adversarial archives are generated data, never imported or executed."""
import io
import stat
import zipfile
from dataclasses import replace

import pytest
from doublegate_sdk import skill_bundle as bundle
from test_skill_bundle import zip_body


@pytest.mark.parametrize('path', ['../escape', '/root', 'a\\b', 'C:a', 'a//b', './a', 'a/../b', 'a\x00b', 'a\nb', 'CON', 'a.', 'a ', 'a/' * 40 + 'b'])
def test_rejects_unsafe_paths(path):
    data = zip_body([('SKILL.md', b'# Help'), (path.replace('\x00', '?'), b'x')])
    if '\x00' in path:
        data = data.replace(b'a?b', b'a\x00b')
    with pytest.raises(ValueError):
        bundle.inspect_zip(data)


@pytest.mark.parametrize('paths', [('a', 'A'), ('a', 'a/b'), ('a/b', 'a'), ('é', 'e\u0301'), ('a', 'a/')])
def test_rejects_collisions(paths):
    with pytest.raises(ValueError):
        bundle.inspect_zip(zip_body([('SKILL.md', b'# Help'), *[(p, b'') for p in paths]]))


def test_rejects_symlink():
    info = zipfile.ZipInfo('link')
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with pytest.raises(ValueError):
        bundle.inspect_zip(zip_body([('SKILL.md', b'# Help'), (info, b'/etc/passwd')]))


@pytest.mark.parametrize('field,value', [('max_archive_bytes', 10), ('max_entries', 1), ('max_member_bytes', 3), ('max_total_bytes', 5), ('max_ratio', 1), ('max_path_bytes', 4), ('max_depth', 1)])
def test_budget_dimensions(field, value):
    data = zip_body([('SKILL.md', b'# Help'), ('assets/a', b'a' * 1000)])
    assert hasattr(bundle, 'BundleLimits')
    with pytest.raises(ValueError):
        bundle.inspect_zip(data, limits=replace(bundle.BundleLimits(), **{field: value}))


def test_requires_exactly_one_root_skill():
    for entries in [[('README.md', b'x')], [('p/SKILL.md', b'x'), ('q/SKILL.md', b'x')]]:
        with pytest.raises(ValueError):
            bundle.inspect_zip(zip_body(entries))


def test_wrapper_directory_is_retained_not_stripped():
    result = bundle.inspect_zip(zip_body([('p/SKILL.md', b'# Help'), ('p/scripts/a.py', b'pass'), ('p/readme.txt', b'info')]))
    assert [r['classification'] for r in result.manifest['members']] == ['skill', 'document', 'script']


def test_manifest_tampering_missing_and_extra_members():
    data = zip_body([('SKILL.md', b'# Help'), ('assets/a', b'x')])
    original = bundle.inspect_zip(data).manifest
    assert hasattr(bundle, 'validate_manifest')
    bundle.validate_manifest(original, data)
    import copy
    for field, value in [('content_hash', '0' * 64), ('size_bytes', 500), ('classification', 'script'), ('path', '../bad')]:
        changed = copy.deepcopy(original)
        changed['members'][0][field] = value
        with pytest.raises(ValueError):
            bundle.validate_manifest(changed, data)
    changed = copy.deepcopy(original)
    changed['members'].pop()
    with pytest.raises(ValueError):
        bundle.validate_manifest(changed, data)
    with pytest.raises(ValueError):
        bundle.validate_manifest(original, zip_body([('SKILL.md', b'tamper')]))


def test_invalid_zip_and_crc_fail_closed():
    data = bytearray(zip_body([('SKILL.md', b'# Help')]))
    data[40] ^= 255
    for body in [b'not a zip', bytes(data)]:
        with pytest.raises(ValueError):
            bundle.inspect_zip(body)
