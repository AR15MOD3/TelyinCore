"""One-time migration for installations without the in-app installer."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import zipfile
import tempfile

from bootstrap_install import apply_pending, atomic, folder, inspect_zip


def main():
    parser=argparse.ArgumentParser(description='首次迁移：正常退出 Core，再选择已有部署目录。')
    parser.add_argument('deployment',type=Path)
    root=parser.parse_args().deployment.resolve(strict=True)
    sys.path.insert(0,str(root/'core'))
    pkg=Path(sys.argv[0]).resolve()
    with zipfile.ZipFile(pkg) as z:
        meta=json.loads(z.read('UPDATE.json'))
        platform='win' if os.name=='nt' else 'linux'
        inspect_zip(root,z,platform,meta['from_version'],meta['to_version'])
    cache=folder(root);cache.mkdir(parents=True,exist_ok=True)
    with pkg.open('rb') as src, tempfile.NamedTemporaryFile(dir=cache,delete=False) as f:
        temp=Path(f.name)
        shutil.copyfileobj(src,f)
    try:
        with temp.open('rb') as f: key=hashlib.file_digest(f,'sha256').hexdigest()
        temp.replace(cache/(key+'.zip'))
        atomic(cache/'pending.json',json.dumps(dict(platform=platform,base=meta['from_version'],
               version=meta['to_version'],sha256=key)).encode(),0o600)
    finally:
        temp.unlink(missing_ok=True)
    apply_pending(root,platform)
    print('已完成迁移，请从原启动器打开 Core。')


if __name__=='__main__':
    main()
