"""Restore passive conversation data under the target Bot's runtime lock."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from bot_lifecycle import tree, write
from runtime_identity import MARKER, _object, bindings, safe_path

FILES = ('session-main.jsonl', 'record-history', 'handoff-note.md')


def restore_imported_history(base, bots, name, runtime):
    folder = safe_path(bots, bots / name)
    before = safe_path(bots, folder / (MARKER + '.bak'))
    if not before.is_file():
        return
    original = _object(before)
    token = original.get('token')
    local = bindings(base).get(name)
    if (original.get('name') != name or not local or token == local
            or not isinstance(token, str) or len(token) != 32
            or any(c not in '0123456789abcdef' for c in token)):
        return
    imported = safe_path(bots, folder / '.imported-runtime' / token)
    if not imported.is_dir():
        return
    receipt = safe_path(bots, imported / ('history-' + local + '.json'))
    state = _object(receipt) if receipt.exists() else None
    if state == {'version': 1, 'state': 'complete'}:
        return

    backups = safe_path(bots, folder / '.previous-installations')
    pointer = safe_path(bots, folder / '.runtime-export.json')
    if pointer.exists():
        info = _object(pointer)
        selected = info.get('backup')
        if set(info) != {'version', 'backup'} or type(info['version']) is not int or info['version'] != 1:
            raise ValueError('迁入档案的运行历史清单无效')
        if selected is None:
            return
        if (not isinstance(selected, str) or len(selected) != 32
                or any(c not in '0123456789abcdef' for c in selected)):
            raise ValueError('迁入档案的运行历史清单无效')
        backup = safe_path(bots, backups / selected)
    else:
        if not backups.is_dir():
            return
        candidates = list(backups.iterdir())
        if not candidates:
            return
        if len(candidates) != 1:
            raise ValueError('迁入档案有多份旧运行历史，无法确认应接续哪一份')
        backup = safe_path(bots, candidates[0])
    if not backup.is_dir():
        raise ValueError('迁入档案缺少清单指定的运行历史备份')
    sources = [safe_path(bots, backup / kind) for kind in ('runtime', 'baseline-runtime')]
    sources = [path for path in sources if path.is_dir()]
    if not sources:
        return
    if len(sources) != 1:
        raise ValueError('迁入档案有多份运行历史来源')
    source = sources[0]
    planned = {}
    for leaf in FILES:
        original_path = safe_path(bots, source / leaf)
        if original_path.exists():
            directory = leaf == 'record-history'
            if not (original_path.is_dir() if directory else original_path.is_file()):
                raise ValueError(f'迁入档案的历史文件类型错误：{leaf}')
            planned[leaf] = tree(original_path)
    if not planned:
        return
    runtime = safe_path(base, Path(runtime))
    digest = hashlib.sha256(json.dumps(planned, sort_keys=True).encode()).hexdigest()
    pending = {'version': 1, 'state': 'pending', 'backup': backup.name, 'digest': digest}
    runtime.mkdir(parents=True, exist_ok=True)
    if state is None:
        for leaf in FILES:
            if safe_path(base, runtime / leaf).exists():
                raise ValueError(f'目标 Bot 已有历史或笔记（{leaf}）；原数据和迁入备份均已保留')
        write(receipt, pending)
    elif state != pending:
        raise ValueError('迁入档案的历史内容与上次恢复时不一致')
    for leaf in FILES:
        dest = safe_path(base, runtime / leaf)
        if dest.exists() and (leaf not in planned or tree(dest) != planned[leaf]):
            raise ValueError(f'目标 Bot 已有不同的历史或笔记（{leaf}）；未覆盖')
    for leaf, expected in planned.items():
        dest = safe_path(base, runtime / leaf)
        if dest.exists():
            if tree(dest) != expected:
                raise ValueError(f'目标 Bot 已有不同的历史或笔记（{leaf}）；未覆盖')
            continue
        with tempfile.TemporaryDirectory(prefix='.history-import-', dir=runtime) as stage:
            copy = Path(stage) / leaf
            origin = source / leaf
            if origin.is_dir():
                shutil.copytree(origin, copy)
            else:
                shutil.copy2(origin, copy)
            if tree(copy) != expected:
                raise ValueError(f'迁入档案的历史文件校验失败（{leaf}）')
            if copy.is_dir():
                if dest.exists():
                    raise ValueError(f'恢复期间目标出现新历史（{leaf}）；未覆盖')
                copy.rename(dest)
            else:
                # Create only, including on platforms where rename replaces files.
                os.link(copy, dest)
    write(receipt, {'version': 1, 'state': 'complete'})
