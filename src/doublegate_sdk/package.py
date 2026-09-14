# Modified for standalone doublegate_sdk namespace; see NOTICE.
"""A package is one bounded JSON manifest, never an archive or executable.

POSIX descriptor-relative reads reject symlinks in every path component. Digest
identifies validated manifest data, not provenance, signatures or approval.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from doublegate_sdk.manifest import GatePackage, validate_manifest

MAX_MANIFEST_BYTES = 65536


from doublegate_sdk.errors import DoublegateError


class PackageError(DoublegateError, ValueError):
    """Fixed diagnostic without paths, keys or content."""
    @property
    def kind(self):
        return str(self.args[0]) if self.args else 'invalid_package'


def read_bounded(path: str | Path, limit: int) -> bytes:
    """Read a regular file once; no symlinks, special files or unbounded reads."""
    fd = None
    parent = None
    try:
        parts = Path(path).parts
        if not parts or '..' in parts:
            raise PackageError('unsafe_path')
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        parent = os.open('/' if Path(path).is_absolute() else '.', flags | os.O_DIRECTORY)
        names = parts[1:] if Path(path).is_absolute() else parts
        if not names:
            raise PackageError('not_regular_file')
        for name in names[:-1]:
            child = os.open(name, flags | os.O_DIRECTORY, dir_fd=parent)
            os.close(parent)
            parent = child
        fd = os.open(names[-1], flags, dir_fd=parent)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise PackageError('not_regular_file')
        if info.st_size > limit:
            raise PackageError('file_too_large')
        with os.fdopen(fd, 'rb') as stream:
            fd = None
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise PackageError('file_too_large')
        return data
    except (OSError, ValueError) as error:
        if isinstance(error, PackageError):
            raise
        raise PackageError('unreadable_file') from None
    finally:
        if fd is not None:
            os.close(fd)
        if parent is not None:
            os.close(parent)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PackageError('duplicate_json_key')
        result[key] = value
    return result


@dataclass(frozen=True, slots=True)
class LoadedPackage:
    package: GatePackage
    digest: str


def load_package(path: str | Path) -> LoadedPackage:
    """Hash sorted, compact, ASCII-escaped JSON after strict manifest validation."""
    data = read_bounded(path, MAX_MANIFEST_BYTES)
    try:
        raw = json.loads(data.decode('utf-8'), object_pairs_hook=_unique_object)
    except (ValueError, RecursionError):
        raise PackageError('invalid_json') from None
    package = validate_manifest(raw)
    canonical = json.dumps(raw, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    return LoadedPackage(package, hashlib.sha256(canonical.encode('ascii')).hexdigest())
