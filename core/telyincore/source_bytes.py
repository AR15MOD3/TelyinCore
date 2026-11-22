"""Finite, identity-checked original bytes shared by plain and archive readers.

Physical location is not an authorization. Callers retain their current scope
checks; archive handles additionally require a live grant callback on each read.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat
from .file_signature import signature, descriptor_signature


def safe_file(path):
    path = Path(path).absolute()
    for part in reversed((path, *path.parents)):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('原始资料路径不能包含链接')
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('原始资料必须是普通文件')
    return path


@contextmanager
def checked_open(path, *, expected=None):
    path = safe_file(path)
    before = signature(path)
    if expected is not None and before != expected:
        raise ValueError('原始资料身份已变化')
    with path.open('rb') as stream:
        if descriptor_signature(stream.fileno()) != before:
            raise ValueError('原始资料打开时变化')
        try:
            yield stream
        finally:
            safe_file(path)
            if signature(path) != before or descriptor_signature(stream.fileno()) != before:
                raise ValueError('原始资料读取期间变化')


def digest_file(path):
    digest = hashlib.sha256()
    with checked_open(path) as stream:
        initial = descriptor_signature(stream.fileno())
        remaining = initial[2]
        while remaining:
            block = stream.read(min(65536, remaining))
            if not block:
                raise ValueError('原始资料缩短')
            remaining -= len(block); digest.update(block)
    return initial, digest.hexdigest()


@dataclass(frozen=True)
class Generation:
    namespace: str
    logical_path: str
    sha256: str
    size: int

    def __post_init__(self):
        if not isinstance(self.namespace, str) or not 1 <= len(self.namespace) <= 128 or any(c in self.namespace for c in '/\\\0\r\n'):
            raise ValueError('invalid namespace')
        p = PurePosixPath(self.logical_path)
        if not self.logical_path or len(self.logical_path) > 4096 or p.is_absolute() or '..' in p.parts or str(p) != self.logical_path or any(c in self.logical_path for c in '\\:\0\r\n'):
            raise ValueError('invalid logical source')
        if not isinstance(self.sha256, str) or not re.fullmatch('[0-9a-f]{64}', self.sha256):
            raise ValueError('invalid original hash')
        if type(self.size) is not int or not 0 <= self.size <= 64 * 1024**3:
            raise ValueError('invalid original size')


def require_grant(grant, generation):
    if not callable(grant) or grant(generation) is not True:
        raise PermissionError('当前范围无权读取此原始资料')


def window(size, offset, length):
    if type(offset) is not int or type(length) is not int or offset < 0 or not 0 <= length <= 4 * 1024**2 or offset > size:
        raise ValueError('invalid bounded byte window')
    return min(length, size - offset)


class PlainReader:
    def __init__(self, path, generation, grant):
        self.path, self.generation, self.grant = Path(path), generation, grant
        require_grant(grant, generation)
        self.identity, digest = digest_file(self.path)
        if (self.identity[2], digest) != (generation.size, generation.sha256):
            raise ValueError('原始资料与代际身份不符')
        require_grant(grant, generation)

    def read(self, offset, length):
        require_grant(self.grant, self.generation)
        length = window(self.generation.size, offset, length)
        with checked_open(self.path, expected=self.identity) as stream:
            stream.seek(offset); raw = stream.read(length)
            if len(raw) != length:raise ValueError('原始资料读取不完整')
        require_grant(self.grant, self.generation)
        return raw

    def chunks(self):
        for offset in range(0, self.generation.size, 65536):
            yield self.read(offset, 65536)
        # Empty files and completed generators still recheck access and identity.
        self.read(self.generation.size, 0)
