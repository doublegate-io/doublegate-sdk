"""Pure, inert skill ZIP inspection. No disk extraction, execution or authority.

Paths are retained verbatim; ambiguous portable paths are rejected, not repaired.
The manifest contains every file occurrence, including equal hashes at different
paths. It conveys integrity and classification, NEVER approval to materialize.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import unicodedata
import zipfile
from dataclasses import dataclass
from typing import Any


def canonical_json(obj: Any) -> bytes:
    """Canonical JSON per ADR-0022: sorted keys, no whitespace, UTF-8, no NaN.

    ``ensure_ascii=False`` so non-ASCII content hashes as its UTF-8 bytes, not
    as ``\\uXXXX`` escapes — the escaped form is not canonical across encoders.
    """
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def content_hash(blob: bytes) -> str:
    """sha256 of a content blob — the value that goes in ``content_hash``."""
    return hashlib.sha256(blob).hexdigest()


class BundleError(ValueError):
    """Fixed diagnostic, never archive content or an attacker supplied filename."""


@dataclass(frozen=True)
class BundleLimits:
    max_archive_bytes: int = 1_000_000
    max_entries: int = 1000
    max_member_bytes: int = 2_000_000
    max_total_bytes: int = 20_000_000
    max_ratio: int = 100
    max_path_bytes: int = 1024
    max_depth: int = 32

    def __post_init__(self):
        if any(type(v) is not int or v < 1 for v in vars(self).values()):
            raise BundleError('invalid limits')


@dataclass(frozen=True)
class InspectedBundle:
    manifest: dict[str, Any]
    members: dict[str, bytes]


_RESERVED = re.compile(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?')


def _path(info: zipfile.ZipInfo, limits: BundleLimits) -> str:
    name = info.orig_filename
    if (name != info.filename or '\\' in name or ':' in name or name.startswith('/')
            or len(name.encode('utf-8')) > limits.max_path_bytes
            or any(unicodedata.category(c).startswith('C') for c in name)):
        raise BundleError('unsafe path')
    parts = name.removesuffix('/').split('/')
    if len(parts) > limits.max_depth or any(
        p in ('', '.', '..') or p.endswith((' ', '.')) or _RESERVED.fullmatch(p) for p in parts
    ):
        raise BundleError('unsafe path component')
    return unicodedata.normalize('NFC', '/'.join(parts)).casefold()


def inspect_zip(body: bytes, *, limits: BundleLimits = BundleLimits()) -> InspectedBundle:
    """Bounded in-memory expansion; verify metadata, actual bytes and CRC first."""
    if not isinstance(body, bytes) or len(body) > limits.max_archive_bytes:
        raise BundleError('archive byte limit')
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            infos = archive.infolist()
            if len(infos) > limits.max_entries:
                raise BundleError('entry limit')
            seen: dict[str, bool] = {}
            total = 0
            for info in infos:
                key = _path(info, limits)
                if key in seen:
                    raise BundleError('path collision')
                directory = info.is_dir()
                kind = stat.S_IFMT(info.external_attr >> 16)
                if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
                    raise BundleError('special file')
                if (kind == stat.S_IFDIR and not directory) or (kind == stat.S_IFREG and directory):
                    raise BundleError('inconsistent file type')
                if info.flag_bits & (1 | 64):
                    raise BundleError('encrypted archive')
                if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    raise BundleError('unsupported compression')
                if directory and info.file_size:
                    raise BundleError('directory payload')
                seen[key] = directory
                total += info.file_size
                if info.file_size > limits.max_member_bytes or total > limits.max_total_bytes:
                    raise BundleError('expanded byte limit')
                if info.file_size > limits.max_ratio * max(1, info.compress_size):
                    raise BundleError('compression ratio limit')
            for key in seen:
                parts = key.split('/')
                if any(seen.get('/'.join(parts[:n])) is False for n in range(1, len(parts))):
                    raise BundleError('parent collision')
            if total > limits.max_ratio * max(1, len(body)):
                raise BundleError('aggregate compression ratio limit')
            members = {}
            actual = 0
            for info in infos:
                with archive.open(info) as stream:
                    data = stream.read(min(info.file_size, limits.max_member_bytes) + 1)
                    if len(data) != info.file_size or stream.read(1):
                        raise BundleError('expanded length mismatch')
                actual += len(data)
                if actual > limits.max_total_bytes:
                    raise BundleError('actual byte limit')
                if not info.is_dir():
                    members[info.filename] = data
    except BundleError:
        raise
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, ValueError, OSError, EOFError) as exc:
        raise BundleError('invalid archive') from exc
    skills = [p for p in members if p.split('/')[-1] == 'SKILL.md']
    if len(skills) != 1:
        raise BundleError('requires one SKILL.md')
    root = skills[0].removesuffix('SKILL.md')
    if any(not p.startswith(root) for p in members):
        raise BundleError('member outside skill root')
    rows = []
    for path, data in sorted(members.items()):
        relative = path[len(root):]
        classification = ('skill' if relative == 'SKILL.md' else 'script' if relative.startswith('scripts/')
                          else 'asset' if relative.startswith('assets/') else 'document')
        rows.append(dict(path=path, content_hash=content_hash(data), size_bytes=len(data), classification=classification))
    return InspectedBundle(dict(bundle_version=1, archive_hash=content_hash(body), archive_size=len(body), members=rows), members)


def validate_manifest(manifest: dict[str, Any], body: bytes, *, limits: BundleLimits = BundleLimits()) -> InspectedBundle:
    """Recompute the complete manifest; reject missing, extra, reordered or altered fields."""
    inspected = inspect_zip(body, limits=limits)
    try:
        matches = canonical_json(manifest) == canonical_json(inspected.manifest)
    except (TypeError, ValueError, RecursionError) as exc:
        raise BundleError('invalid manifest') from exc
    if not matches:
        raise BundleError('manifest mismatch')
    return inspected
