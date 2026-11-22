"""Offline Windows shared-runtime setup. Never start services or touch Bot state."""
from pathlib import Path, PurePosixPath
import argparse, hashlib, json, os, shutil, stat, subprocess, sys, tempfile, zipfile
CORE=Path(__file__).resolve().parents[1]
if str(CORE) not in sys.path:sys.path.insert(0,str(CORE))
from runtime_identity import safe_path
from service_layout import snowluma_directory, node_executable


def sha(raw):return hashlib.sha256(raw).hexdigest()
def specification(core):return json.loads((Path(core)/'scripts/components/windows-qq.json').read_text(encoding='utf-8'))

def clean_name(name):
    p=PurePosixPath(name)
    if not name or p.is_absolute() or '..' in p.parts or '\\' in name or ':' in name or any(x.endswith(('.', ' ')) for x in p.parts):
        raise ValueError('组件包含不安全的路径')
    return p


def payload(core, item):
    path=safe_path(core,Path(core)/'scripts/components'/item['archive'])
    if sha(path.read_bytes())!=item['sha256']:raise ValueError('随包组件校验失败：'+item['archive'])
    with zipfile.ZipFile(path) as z:
        actual=[i for i in z.infolist() if not i.is_dir()]
        if len(actual)!=len(item['files']) or len({i.filename.casefold() for i in actual})!=len(actual):raise ValueError('组件文件清单不符')
        result={}
        for entry in actual:
            name=entry.filename;clean_name(name)
            if name not in item['files'] or stat.S_ISLNK(entry.external_attr>>16) or entry.file_size>128*1024*1024:raise ValueError('组件文件无效')
            blob=z.read(entry)
            if sha(blob)!=item['files'][name]:raise ValueError('组件内容校验失败：'+name)
            result[name]=blob
        return result


def run_node(node,*args):
    done=subprocess.run([str(node),*map(str,args)],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=20,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if done.returncode:raise ValueError('Node 或原生组件加载检查失败：'+(done.stderr or done.stdout)[-1000:])
    return done.stdout.strip()


def probe(core):
    root=snowluma_directory(Path(core));node=node_executable(core)
    if not node:raise ValueError('Node.js 尚未安装')
    version=run_node(node,'--version')
    if tuple(map(int,version.removeprefix('v').split('.')))<(22,0,0):raise ValueError('Node.js 版本不兼容')
    run_node(node,'--check',root/'index.mjs')
    modules=[root/'native'/n for n in ('snowluma-win32-x64.node','websocket-win32-x64.node','ffmpeg/ffmpegAddon.win32.x64.node')]
    run_node(node,'-e','for(const p of JSON.parse(process.argv[1])) require(p);',json.dumps([str(p.resolve()) for p in modules]))
    return version


def marker(core):return Path(core).parent/'runtime/windows-qq-ready.json'
def snapshot(core):
    spec=specification(core);root=snowluma_directory(Path(core));paths=[Path(core)/'scripts/components/windows-qq.json',Path(__file__),Path(node_executable(core) or '')]
    paths += [root/n for n in spec['snowluma']['files']]
    return {str(p):[p.stat().st_size,p.stat().st_mtime_ns,p.stat().st_ctime_ns] for p in paths if p.is_file()} if len(paths)==len(set(paths)) and all(p.is_file() and not p.is_symlink() for p in paths) else None

def ready(core=CORE):
    try:
        value=snapshot(core)
        return value is not None and json.loads(marker(core).read_text(encoding='utf-8'))==value
    except (OSError,ValueError,KeyError):return False


def install_tree(target, files, old_hashes, *, validate):
    """Publish after a staged probe, preserve all unlisted state, rollback on failure."""
    target=Path(target);target.parent.mkdir(parents=True,exist_ok=True)
    before={}
    for n,raw in files.items():
        p=safe_path(target.parent,target/n)
        if p.exists():
            old=p.read_bytes()
            if old!=raw and sha(old) not in old_hashes.get(n,[]):raise ValueError('已有组件有未知修改，已保留：'+n)
            before[n]=old
        else:before[n]=None
    with tempfile.TemporaryDirectory(prefix='.qq-runtime-',dir=target.parent) as folder:
        stage=Path(folder)/'program';stage.mkdir()
        for n,raw in files.items():
            p=stage/clean_name(n);p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
        validate(stage)
        if not target.exists():stage.rename(target);return
        applied=[]
        try:
            for n,raw in files.items():
                if before[n]==raw:continue
                p=target/n;p.parent.mkdir(parents=True,exist_ok=True)
                pending=p.with_name(p.name+'.core-install-tmp')
                created=False
                try:
                    with pending.open('xb') as stream:
                        created=True; stream.write(raw)
                    os.replace(pending,p);applied.append(n)
                finally:
                    if created: pending.unlink(missing_ok=True)
        except BaseException:
            for n in reversed(applied):
                p=target/n
                if before[n] is None:p.unlink()
                else:p.write_bytes(before[n])
            raise


def prepare(core=CORE):
    core=Path(core)
    if ready(core):print('QQ 运行组件自检通过；账号与连接请在 QQ 插件页设置。',flush=True);return
    from bot_global_admission import HostAdmissionLock
    lock=HostAdmissionLock(core,exclusive=True).acquire()
    try:
        spec=specification(core)
        node=node_executable(core)
        try:usable=node and tuple(map(int,run_node(node,'--version').removeprefix('v').split('.'))) >= (22,0,0)
        except (OSError,ValueError,subprocess.TimeoutExpired):usable=False
        if not usable:
            print('安装随包 Node.js 运行环境…',flush=True)
            install_tree(core.parent/'services/node',payload(core,spec['node']),{},validate=lambda p:run_node(p/'node.exe','--version'))
        node=node_executable(core)
        print('检查并安装随包 SnowLuma Windows 组件…',flush=True)
        def check(stage):
            run_node(node,'--check',stage/'index.mjs')
            run_node(node,'-e','for(const p of JSON.parse(process.argv[1])) require(p);',json.dumps([str((stage/n).resolve()) for n in spec['snowluma']['files'] if n.endswith('.node')]))
        install_tree(snowluma_directory(core),payload(core,spec['snowluma']),spec['snowluma'].get('replaces',{}),validate=check)
        version=probe(core)
        value=snapshot(core)
        if value is None:raise ValueError('组件安装后校验未完成')
        path=marker(core);path.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,delete=False) as stream:
            tmp=Path(stream.name);json.dump(value,stream,ensure_ascii=False)
        os.replace(tmp,path)
        print('Node '+version+' 与 SnowLuma 程序检查通过。QQ 登录与收发尚未验证，请到 QQ 插件页继续。',flush=True)
    finally:lock.close()


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');p.add_argument('--core',type=Path,default=CORE);a=p.parse_args(argv)
    if os.name!='nt':print('此组件为 Windows x64 专用；未修改 Linux 服务。');return 2
    try:
        if a.check:
            probe(a.core)
            # Full check verifies packaged content against installed files too.
            spec=specification(a.core);root=snowluma_directory(a.core)
            if any(sha((root/n).read_bytes())!=h for n,h in spec['snowluma']['files'].items()):raise ValueError('SnowLuma 程序版本需更新，请正常启动完成安装')
            print('QQ 程序组件检查通过；连接状态另行验证。');return 0
        prepare(a.core);return 0
    except Exception as error:
        print('QQ 运行组件准备未完成：'+str(error)+'\n请退出此部署的全部 Bot 后重试；账号配置与缓存未修改。',file=sys.stderr,flush=True);return 1

if __name__=='__main__':raise SystemExit(main())
