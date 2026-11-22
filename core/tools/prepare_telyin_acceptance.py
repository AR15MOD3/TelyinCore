"""Create a fresh Bot acceptance copy from an explicit content allowlist."""
from pathlib import Path
import hashlib,json,os,shutil,tempfile

TREES=('persona','环境','memory/知识','memory/做法','memory/自我','memory/作息与习惯')
# Old observations contain conversation-specific material, excluded from acceptance.
EXCLUDE={'memory/自我/观察与更正.md'}


def prepare(source: Path, target: Path, *, display_name: str) -> dict:
    source=source.resolve(strict=True);target=target.absolute()
    if target.exists() or target.is_symlink():raise ValueError('验收目录已存在，不覆盖已有验收记录')
    if source==target or source in target.parents:raise ValueError('验收副本不能建在原档案内')
    files={}
    for tree in TREES:
        root=source/tree
        if not root.is_dir() or root.is_symlink():raise ValueError('源资料缺失或是链接：'+tree)
        for p in root.rglob('*'):
            if p.is_symlink():raise ValueError('源资料不允许链接')
            if p.is_file() and str(p.relative_to(source)) not in EXCLUDE:
                files[str(p.relative_to(source))]=p.read_bytes()
    avatar=source/'avatar.png'
    if avatar.is_file() and not avatar.is_symlink():files['avatar.png']=avatar.read_bytes()
    target.parent.mkdir(parents=True,exist_ok=True)
    temp=Path(tempfile.mkdtemp(prefix='.bot-init-',dir=target.parent))
    try:
        for name,blob in files.items():
            p=temp/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(blob)
        (temp/'memory/自我/观察与更正.md').write_text('',encoding='utf-8')
        (temp/'bot.json').write_text(json.dumps({'displayName':display_name},ensure_ascii=False),encoding='utf-8')
        for name in ('README.md','memory/README.md'):
            p=source/name
            if p.is_file():shutil.copy2(p,temp/name)
        (temp/'README.md').write_text('# Telyin 验收副本\n\n源自 Linux 的 Telyin 新版档案。\n保留人设、自述、习惯、知识与做法；未复制旧画像、聊天、账号绑定、插件配置或运行归属。\n模型配置由本机页面设置。不要把本目录当作原档案的运行数据。\n',encoding='utf-8')
        assert all((source/n).read_bytes()==b for n,b in files.items()),'source changed during export'
        temp.rename(target)
    finally:
        if temp.exists():shutil.rmtree(temp)
    return {'source':str(source),'bot':target.name,'display_name':display_name,
            'source_files':{n:hashlib.sha256(b).hexdigest() for n,b in sorted(files.items())},
            'excluded':['旧画像','旧聊天经历','旧观察记录','身份绑定','QQ插件配置','模型密钥','运行归属标记']}
