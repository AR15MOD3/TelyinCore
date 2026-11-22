"""Bounded source-candidate ZIP inspection; NEVER extract, import or deploy.

A match only proves equivalence to an explicit static source closure NOW.
It does not prove complete distribution/runtime dependencies or host authority.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import stat
import struct
import zipfile

from runtime_identity import safe_path
from tools.bot_source_snapshot import (source_hashes, _relative, MAX_FILES,
                                       MAX_FILE_BYTES, MAX_TOTAL_BYTES)

MAX_ARCHIVE_BYTES = MAX_TOTAL_BYTES + 4 * 1024 * 1024


def _audit(core, archive, seeds):
    expected = source_hashes(core, seeds)  # no imports of target source
    if len({name.casefold() for name in expected}) != len(expected):
        raise ValueError('case-colliding source closure')
    archive = Path(archive).absolute()
    safe_path(Path(archive.anchor), archive)
    info = archive.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('regular archive required')
    with archive.open('rb') as stream:
        blob = stream.read(MAX_ARCHIVE_BYTES + 1)
    if len(blob) > MAX_ARCHIVE_BYTES or len(blob) < 22:
        raise ValueError('archive byte limit')
    # Strict classic single-disk ZIP, no comment/ZIP64/prefix/trailing records.
    # Bound the central directory BEFORE ZipFile constructs member objects.
    signature, disk, cd_disk, disk_count, count, cd_size, offset, comment = struct.unpack(
        '<4s4H2LH', blob[-22:])
    if (signature != b'PK\x05\x06' or disk or cd_disk or disk_count != count
            or not 0 < count <= MAX_FILES or comment
            or cd_size > MAX_FILES * (256 + 46)
            or offset + cd_size != len(blob) - 22 or not blob.startswith(b'PK\x03\x04')):
        raise ValueError('bounded classic source ZIP required')
    seen = set(); folded = set(); total = 0
    with zipfile.ZipFile(io.BytesIO(blob)) as package:
        members = package.infolist()
        if len(members) != count or count != len(expected):
            raise ValueError('source archive membership mismatch')
        for member in members:
            name = member.filename
            _relative(name)  # no absolute/dot/backslash/drive/unsupported data paths
            mode = member.external_attr >> 16
            if (len(name.encode('utf-8')) > 256 or name in seen or name.casefold() in folded
                    or name not in expected or member.is_dir() or member.flag_bits & 1
                    or member.extra or member.comment
                    or member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                    or stat.S_IFMT(mode) not in (0, stat.S_IFREG)
                    or member.external_attr & 0x400
                    or not 0 <= member.file_size <= MAX_FILE_BYTES):
                raise ValueError('unsafe source archive member')
            seen.add(name); folded.add(name.casefold()); total += member.file_size
            if total > MAX_TOTAL_BYTES:
                raise ValueError('source archive expanded byte limit')
            with package.open(member) as stream:
                raw = stream.read(MAX_FILE_BYTES + 1)
            if (len(raw) != member.file_size or len(raw) > MAX_FILE_BYTES
                    or hashlib.sha256(raw).hexdigest() != expected[name]):
                raise ValueError('source archive content mismatch')
    if seen != set(expected) or source_hashes(core, seeds) != expected:
        raise ValueError('source changed during archive audit')
    return dict(schema='bot-source-candidate-archive-v1', candidate_source_integrity=True,
                source_files=len(expected), expanded_bytes=total,
                archive_sha256=hashlib.sha256(blob).hexdigest(),
                scope='explicit static source closure only; not a deployable distribution',
                host_coverage_complete=False, production_ready=False,
                production_apply_supported=False)


def audit_archive(core, archive, seeds):
    try:
        return _audit(core, archive, seeds)
    except Exception:
        raise ValueError('candidate source archive rejected') from None


def run(core, archive, seeds, destination):
    destination = Path(destination).absolute()
    destination.mkdir(parents=True, exist_ok=False)
    try:
        report = audit_archive(core, archive, seeds)
    except ValueError:
        report = dict(schema='bot-source-candidate-archive-v1',
                      candidate_source_integrity=False, host_coverage_complete=False,
                      production_ready=False, production_apply_supported=False)
    with (destination/'archive-verification.json').open('x', encoding='utf-8') as f:
        json.dump(report, f, indent=2)
    return 2  # even a match is NOT production release authority


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    from tools.bot_lifecycle_acceptance import CORE, SOURCES
    raise SystemExit(run(CORE, options.archive, SOURCES, options.output))
