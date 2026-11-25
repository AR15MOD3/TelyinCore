"""Build a platform-bound native update from two tested distribution archives."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'core'))
from telyincore.distribution_version import validate_release_version
from telyincore.online_install import candidate_platform, program_path, safe_file
from telyincore.package_updates import RELEASE_REPOSITORY, overlay_name, version_key


def inventory(z):
    roots = {n.split('/')[0] for n in z.namelist()}
    if len(roots) != 1 or len(z.namelist()) != len(set(z.namelist())):
        raise ValueError('部署包根目录或清单无效')
    prefix = roots.pop() + '/'
    candidate = json.loads(z.read(prefix+'candidate.json'))
    platform = candidate_platform(candidate)
    if platform not in {'win','linux'} or prefix != f'Telyincore_{platform}/':
        raise ValueError('部署包的平台标签无效')
    result = {}
    for line in z.read(prefix+'MANIFEST.sha256').decode().splitlines():
        fingerprint, name = line.split('  ', 1)
        if name in result or not re.fullmatch('[0-9a-f]{64}',fingerprint):
            raise ValueError('部署包文件清单重复或无效')
        result[name] = fingerprint
    actual={n[len(prefix):] for n in z.namelist() if not n.endswith('/')}
    if set(result) != actual - {'MANIFEST.sha256'}:raise ValueError('部署包清单不完整')
    result['MANIFEST.sha256']=hashlib.sha256(z.read(prefix+'MANIFEST.sha256')).hexdigest()
    return prefix, candidate, result


def build(previous, current, out):
    with zipfile.ZipFile(previous) as before, zipfile.ZipFile(current) as after:
        p, old, a = inventory(before)
        prefix, new, b = inventory(after)
        platform, base, version = candidate_platform(new), old['version'], new['version']
        validate_release_version(version)
        if p!=prefix or version_key(version)<=version_key(base):raise ValueError('版本或平台不匹配')
        name=overlay_name(platform,base,version)
        rows=[]
        changed=sorted(n for n in a.keys()|b.keys() if a.get(n)!=b.get(n))
        for n in changed:
            safe_file(ROOT,n)
            if not program_path(n):
                raise ValueError(f'此差异无法作为程序增量发布：{n}')
            if n in a and hashlib.sha256(before.read(prefix+n)).hexdigest()!=a[n]:
                raise ValueError('基线校验失败：'+n)
            if n in b and hashlib.sha256(after.read(prefix+n)).hexdigest()!=b[n]:
                raise ValueError('发行文件校验失败：'+n)
            rows.append(dict(path=n,old_sha256=a.get(n),new_sha256=b.get(n)))
        out.mkdir(parents=True,exist_ok=True)
        target=out/name
        with zipfile.ZipFile(target,'x',zipfile.ZIP_DEFLATED) as package:
            package.write(ROOT/'release_tools/bootstrap.py','__main__.py')
            package.write(ROOT/'core/telyincore/online_install.py','bootstrap_install.py')
            for r in rows:
                n=r['path']
                if r['new_sha256']:package.writestr('payload/'+n,after.read(prefix+n))
            package.writestr('UPDATE.json',json.dumps(dict(schema='core-online-update-v1',platform=platform,
                            from_version=base,to_version=version,files=rows),ensure_ascii=False))
    checksum=target.with_suffix('.sha256')
    checksum.write_text(hashlib.sha256(target.read_bytes()).hexdigest()+'  '+name+'\n',encoding='ascii')
    return f'{platform}-{version}',target,checksum


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('previous',type=Path);parser.add_argument('current',type=Path)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--also-from', type=Path, action='append', default=[], help='另一个支持直升的完整基线包，可重复')
    parser.add_argument('--publish',action='store_true')
    parser.add_argument('--notes-file',type=Path)
    args=parser.parse_args()
    if args.publish and not args.notes_file:parser.error('--publish 需要 --notes-file')
    import tempfile
    args.out.mkdir(parents=True, exist_ok=True)
    assets = []
    with tempfile.TemporaryDirectory(dir=args.out) as temporary:
        staging = Path(temporary)
        for previous in [args.previous, *args.also_from]:
            tag, package, checksum = build(previous, args.current, staging)
            assets.extend((package.name, checksum.name))
        if any((args.out / name).exists() for name in assets):
            raise FileExistsError("输出目录已含同名更新包")
        for name in assets:
            (staging / name).rename(args.out / name)
    attachments = [str(args.out / name) + ("#core-online-update-v1" if name.endswith(".zip") else "") for name in assets]
    if args.publish:
        commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
        if subprocess.check_output(['git','status','--porcelain','--untracked-files=no'],cwd=ROOT):raise ValueError('请先提交源码')
        subprocess.run(['gh','release','create',tag,*attachments,
            '--repo',RELEASE_REPOSITORY,'--target',commit,'--prerelease','--title',tag,
            '--notes-file',str(args.notes_file)],check=True,cwd=ROOT)
    print("\n".join(str(args.out / name) for name in assets))


if __name__=='__main__':main()
