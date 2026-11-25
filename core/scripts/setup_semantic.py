"""Prepare CPU embedding dependencies and a pinned local model on first deployment.

Runtime inference never downloads. Failed downloads stay in staging and are retried;
only a fully verified model directory is published. No Bot memory is touched.
"""
from pathlib import Path
import argparse
import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

CORE = Path(__file__).resolve().parents[1]
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))
from telyincore.embeddings import DEFAULT_MODEL
from telyincore.source_guard import lease

from telyincore.download_sources import HF, TORCH, TRANSFER_ERRORS, open_public, pip_install, download_routes, settings_root


DEPENDENCIES = {'torch': '2.15.0+cpu', 'sentence-transformers': '5.2.3', 'transformers': '4.57.6'}


def model_spec():
    return json.loads((Path(__file__).with_name('semantic-model.json')).read_text(encoding='utf-8'))


def valid_file(path, spec):
    if not path.is_file() or path.is_symlink() or path.stat().st_size != spec['size']:
        return False
    digest = (hashlib.sha256() if spec['algorithm'] == 'sha256' else
              hashlib.sha1(b'blob ' + str(spec['size']).encode() + b'\0'))
    with path.open('rb') as stream:
        while data := stream.read(1024 * 1024):
            digest.update(data)
    return digest.hexdigest() == spec['hash']


def signature(model):
    versions = {name: importlib.metadata.version(name) for name in DEPENDENCIES}
    if versions != DEPENDENCIES:
        raise ValueError('语义依赖版本未准备好')
    stats = {}
    for name, expected in model_spec()['files'].items():
        path = model / name
        st = path.stat()
        if path.is_symlink() or st.st_size != expected['size']:
            raise ValueError('语义模型文件不完整')
        stats[name] = [st.st_size, st.st_mtime_ns, st.st_ctime_ns]
    return dict(schema=1, python=sys.executable, prefix=sys.prefix, dependencies=versions,
                files=stats, model=str(model.resolve()),
                installer=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                spec=model_spec())


def marker_path(model):
    # Per-interpreter probe; the model may be shared by multiple deployments.
    key = hashlib.sha256(str(Path(sys.prefix).resolve()).encode()).hexdigest()[:16]
    return model.parent / (model.name + '.ready-' + key + '.json')


def ready(model=Path(DEFAULT_MODEL)):
    model = Path(model)
    try:
        return json.loads(marker_path(model).read_text(encoding='utf-8')) == signature(model)
    except (OSError, ValueError, importlib.metadata.PackageNotFoundError):
        return False


def download(url, partial, expected_size, proxy=None):
    """Resume interrupted immutable downloads, with full hash validation after."""
    for attempt in range(3):
        try:
            offset = partial.stat().st_size if partial.exists() else 0
            if offset >= expected_size:
                partial.unlink(missing_ok=True)
                offset = 0
            headers = {'User-Agent': 'TelyinCore-model-download', 'Accept-Encoding': 'identity'}
            if offset:
                headers['Range'] = f'bytes={offset}-'
            request = urllib.request.Request(url, headers=headers)
            with open_public(request, timeout=25, proxy=proxy) as response:
                resume = offset and response.status == 206
                if resume and not response.headers.get('Content-Range', '').startswith(f'bytes {offset}-'):
                    raise ValueError('模型下载续传范围不符')
                with partial.open('ab' if resume else 'wb') as output:
                    count = offset if resume else 0
                    while block := response.read(1024 * 1024):
                        count += len(block)
                        if count > expected_size:
                            raise ValueError('模型文件过大')
                        output.write(block)
            if partial.stat().st_size != expected_size:
                raise OSError('模型下载未完成')
            return
        except OSError as exc:
            if isinstance(exc, urllib.error.HTTPError) and 400 <= exc.code < 500 and exc.code not in (408, 429):
                raise
            if attempt == 2:
                raise
            print('模型下载中断，正在续传重试…', flush=True)


def prepare_model(model, source=None):
    spec = model_spec()
    if model.exists():
        if all(valid_file(model / name, item) for name, item in spec['files'].items()):
            return
        raise RuntimeError('现有模型不完整或版本不符，请保留后移走再重试：' + str(model))
    # Reuse the verified model from deployments that kept it next to core.
    legacy = CORE.parent / 'models' / 'bge-m3'
    if source is None and model == CORE / 'models' / 'bge-m3' and (legacy.exists() or legacy.is_symlink()):
        if legacy.is_symlink() or not all(valid_file(legacy / name, item) for name, item in spec['files'].items()):
            raise RuntimeError('旧版 BGE-M3 校验失败，请检查模型目录：' + str(legacy))
        model.parent.mkdir(parents=True, exist_ok=True)
        legacy.rename(model)
        print('已迁移本地 BGE-M3：' + str(model), flush=True)
        return
    stage = model.with_name(model.name + '.download')
    stage.mkdir(parents=True, exist_ok=True)
    for name, expected in spec['files'].items():
        target = stage / name
        if valid_file(target, expected):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + '.part')
        local = Path(source) / name if source else None
        print('准备语义模型：' + name, flush=True)
        if local is not None and valid_file(local, expected):
            shutil.copyfile(local, partial)
        else:
            routes = download_routes(settings_root(CORE))
            for index, (choice, proxy) in enumerate(routes):
                endpoint = HF[choice]
                url = f"{endpoint}/{spec['repo']}/resolve/{spec['revision']}/{name}"
                try:
                    print('下载源：' + endpoint + (' · 代理' if proxy else ' · 直连'), flush=True)
                    had_prefix = partial.is_file()
                    for attempt in range(2):
                        if proxy:
                            download(url, partial, expected['size'], proxy)
                        else:
                            download(url, partial, expected['size'])
                        if valid_file(partial, expected):
                            break
                        partial.unlink(missing_ok=True)
                        if index != len(routes) - 1 or not had_prefix or attempt:
                            raise OSError('模型文件校验失败')
                    break
                except TRANSFER_ERRORS:
                    if index == len(routes) - 1:
                        raise
                    print('当前下载路径未成功，尝试下一条已配置路径。', flush=True)
        if not valid_file(partial, expected):
            raise RuntimeError('模型校验失败：' + name)
        os.replace(partial, target)
    os.rename(stage, model)


def install_dependencies():
    def installed(name):
        try:
            return importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            return None
    def run(argv, env):
        return subprocess.run(argv, env=env).returncode

    command = [sys.executable, '-m', 'pip', 'install', '--only-binary=:all:']
    root = settings_root(CORE)
    if installed('torch') != DEPENDENCIES['torch']:
        indexes = {key: url + '/cpu' for key, url in TORCH.items()}
        if pip_install([*command, 'torch==' + DEPENDENCIES['torch']], run, root, indexes=indexes):
            raise RuntimeError('语义模型的 CPU 推理环境安装失败')
    if any(installed(name) != value for name, value in DEPENDENCIES.items()):
        if pip_install([*command, 'sentence-transformers==' + DEPENDENCIES['sentence-transformers'],
                        'transformers==' + DEPENDENCIES['transformers']], run, root):
            raise RuntimeError('语义模型依赖安装失败')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--source', type=Path, help='Import an existing, hash-verified BGE-M3 model')
    parser.add_argument('--model', type=Path, default=Path(DEFAULT_MODEL))
    args = parser.parse_args(argv)
    if ready(args.model):
        print('语义检索模型与依赖检查通过。', flush=True)
        return 0
    if args.check:
        print('语义检索尚未完整准备，请正常启动一次；已有旧版模型会自动校验迁移，缺少模型时才会下载。', flush=True)
        return 1
    try:
        with lease(args.model.parent / (args.model.name + '.setup'), timeout=5):
            install_dependencies()
            prepare_model(args.model, args.source)
            # A package import alone cannot demonstrate that model weights load.
            from telyincore.embeddings import embed
            vector = embed(['语义模型安装验收'], model_path=str(args.model))[0]
            if len(vector) != 1024 or abs(sum(v*v for v in vector) - 1) > .002:
                raise RuntimeError('模型推理验收失败')
            marker = marker_path(args.model)
            tmp = marker.with_suffix('.tmp')
            tmp.write_text(json.dumps(signature(args.model), ensure_ascii=False), encoding='utf-8')
            os.replace(tmp, marker)
        print('本地语义模型已就绪；记忆向量将在运行后自动增量建立。', flush=True)
        return 0
    except Exception as error:
        print('语义检索准备未完成：' + str(error) + '\n请检查网络或模型目录后重新启动；已有记忆未改动。', file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
