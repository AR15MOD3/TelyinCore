"""Build a program-only overlay from two acceptance distributions."""
from pathlib import Path, PurePosixPath
import argparse
import hashlib
import json
import zipfile


def read_release(path):
    with zipfile.ZipFile(path) as archive:
        files = {}
        roots = set()
        for entry in archive.infolist():
            if entry.is_dir():
                continue
            parts = PurePosixPath(entry.filename).parts
            if len(parts) < 2 or '..' in parts or '\\' in entry.filename:
                raise ValueError('invalid archive member')
            roots.add(parts[0])
            relative = '/'.join(parts[1:])
            if relative in files:
                raise ValueError('duplicate archive member')
            files[relative] = archive.read(entry)
    if len(roots) != 1 or not roots <= {'Telyincore_win', 'Telyincore_linux'}:
        raise ValueError('unexpected distribution root')
    return roots.pop(), files, json.loads(files['candidate.json'])['version']


def digest(blob):
    return hashlib.sha256(blob).hexdigest()


def build(previous, current, destination, notes):
    old_root, old, old_version = read_release(previous)
    root, new, version = read_release(current)
    if old_root != root:
        raise ValueError('platforms differ')
    excluded = ('bots/', 'core/data/', 'core/extensions/', 'runtime/',
                'core/.venv/', 'core/.venv-linux/', 'core/.linux-acceptance/')
    changes = {name: blob for name, blob in new.items()
               if old.get(name) != blob and not name.startswith(excluded)}
    # Older incremental Windows installs may have skipped this new dependency.
    # Carry it even when it is unchanged in the previous full distribution.
    if root == 'Telyincore_win':
        changes['core/desktop_open.py'] = new['core/desktop_open.py']
        qq_entry = 'core/extensions/core-infinity-qq/index.py'
        if qq_entry in new and old.get(qq_entry) != new[qq_entry]:
            changes[qq_entry] = new[qq_entry]
    manifest = {'schema': 'telyincore-overlay-update-v1', 'from_version': old_version,
                'to_version': version, 'archive_root': root, 'requires_stopped_service': True,
                'runtime_data_included': False, 'removals': (['管理 Bot.bat'] if root == 'Telyincore_win' and '管理 Bot.bat' not in new else []),
                'files': [{'path': name, 'old_sha256': digest(old[name]) if name in old else None,
                           'new_sha256': digest(blob)} for name, blob in sorted(changes.items())]}
    target = Path(destination) / f'{root}_{version}_update.zip'
    with zipfile.ZipFile(target, 'x', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, blob in sorted(changes.items()):
            archive.writestr(f'{root}/{name}', blob)
        archive.writestr(f'{root}/UPDATE.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr(f'{root}/更新说明.md', notes)
    with zipfile.ZipFile(target) as archive:
        if archive.testzip() is not None:
            raise ValueError('corrupt update archive')
        for row in manifest['files']:
            if digest(archive.read(f"{root}/{row['path']}")) != row['new_sha256']:
                raise ValueError('update member checksum mismatch')
    target.with_suffix('.sha256').write_text(f'{digest(target.read_bytes())}  {target.name}\n')
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('previous', type=Path)
    parser.add_argument('current', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--notes', required=True, type=Path)
    args = parser.parse_args()
    print(build(args.previous, args.current, args.output, args.notes.read_text()))
