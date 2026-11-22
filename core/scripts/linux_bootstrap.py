"""Fast local startup check, invalidated by interpreter/dependency changes."""
from pathlib import Path
import sys,json,hashlib,importlib.metadata,tomllib
CORE=Path(__file__).resolve().parents[1]
if str(CORE) not in sys.path:sys.path.insert(0,str(CORE))
from scripts.ensure_runtime import ensure


def fingerprint(core):
    manifest=tomllib.loads((core/'pyproject.toml').read_text())
    dependencies=manifest['project']['dependencies']
    versions={}
    for requirement in dependencies:
        import re
        name=re.split(r'[<>=!~\[]',requirement,maxsplit=1)[0]
        try:versions[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:versions[name]=None
    return {'schema':1,'python':str(Path(sys.executable).absolute()),'prefix':sys.prefix,
        'version':sys.version,'dependencies':dependencies,'installed':versions,
        'probe':hashlib.sha256((core/'scripts/ensure_runtime.py').read_bytes()).hexdigest()}


def main(argv=None):
    args=list(sys.argv[1:] if argv is None else argv);core=CORE
    marker=core/'.linux-startup.json';now=fingerprint(core)
    if not args and marker.is_file():
        try:cached=json.loads(marker.read_text())
        except (OSError,ValueError):cached=None
        if cached==now and all(now['installed'].values()):
            print('快速自检通过。',flush=True);return 0
    result=ensure(core,install='--check' not in args)
    if result:return result
    from os import replace
    temporary=marker.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(fingerprint(core),ensure_ascii=False,indent=2)+'\n')
    replace(temporary,marker)
    print('环境检查通过。',flush=True);return 0


if __name__=='__main__':raise SystemExit(main())
