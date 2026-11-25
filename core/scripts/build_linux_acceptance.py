"""Public Linux acceptance distribution. Never include the source project's runtime or Bots."""
from pathlib import Path
import argparse,hashlib,json,stat,zipfile,sys
CORE=Path(__file__).resolve().parents[1]
if str(CORE) not in sys.path:sys.path.insert(0,str(CORE))
from scripts.build_windows_acceptance import source_files
from telyincore.distribution_version import validate_release_version


def build(core, destination, version='beta.0.3.1'):
    core=Path(core).resolve(strict=True);destination=Path(destination).resolve()
    validate_release_version(version)
    if destination.is_relative_to(core):raise ValueError('release directory must be outside source')
    sources={n:b for n,b in source_files(core).items() if not n.startswith("scripts/components/")}
    if (core/'start.sh').is_symlink():raise ValueError('linked launcher refused')
    sources['start.sh']=(core/'start.sh').read_bytes()
    sources['scripts/linux_dev.py'] = (core / 'scripts/linux_dev.py').read_bytes()
    from scripts.linux_snowluma import ARCHIVE, SHA256
    for name in (ARCHIVE, 'snowluma-files.json', 'LICENSE.SnowLuma', 'python-3.11.12-linux-x64.tar.gz'):
        component = core / 'linux-components' / name
        if component.is_symlink() or component.parent.is_symlink():
            raise ValueError('linked Linux component refused')
        payload = component.read_bytes()
        if name == ARCHIVE and hashlib.sha256(payload).hexdigest() != SHA256:
            raise ValueError('Linux SnowLuma component digest mismatch')
        sources['linux-components/' + name] = payload
    files={'core/'+name:blob for name,blob in sources.items()}
    files['启动 Telyincore.sh']=b'#!/usr/bin/env bash\nset -euo pipefail\ncd -- "$(dirname -- "$0")/core"\nexec bash ./start.sh --acceptance "$@"\n'
    metadata={'schema':'linux-core-acceptance-v1','platform':'linux','version':version,
        'runtime_mode':'isolated-profile-acceptance','includes_private_runtime_data':False,
        'source_fingerprint_sha256':hashlib.sha256(json.dumps({n:hashlib.sha256(b).hexdigest() for n,b in sorted(sources.items())},sort_keys=True).encode()).hexdigest()}
    files['candidate.json']=(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n').encode()
    files['README-使用说明.md']=('''# Telyincore_linux

Linux 部署包。
启动：在终端运行 bash "启动 Telyincore.sh"，用方向键和回车选择「唤醒／档案／休止／退出」。点「唤醒」先自检环境、BGE-M3 和本机支持的 SnowLuma 组件，自检通过后选择 Bot、打开控制页面，B 可再次打开页面，Q 可正常结束本次服务。已运行时会复用本部署实例。
应用菜单入口：首次运行 bash "启动 Telyincore.sh" --install-launcher，以后在应用菜单选择「TelyinCore 启动」。移动部署目录后重新注册即可。
Linux 档案使用本部署的单一档案，支持归档、迁出、恢复、改名和事务回退。静候期间暂停后台索引补建，网页开始运行后自动续建。
首次启动及旧版更新后会自动准备 CPU 语义依赖和 BGE-M3（约 2.3 GB，优先国内 hf-mirror 镜像，失败回退官方源）；下载核验及真实推理通过才报告就绪。模型位于 core/models/bge-m3，更新保留，旧部署的模型会校验后自动迁移；新增和修改记忆在后台自动增量补建。
环境检查：bash "启动 Telyincore.sh" --check
文档检查：bash "启动 Telyincore.sh" --check-documents
页面：http://127.0.0.1:7810/ 。启动时暂停，模型配置完成后点击运行。
Bot、画像、会话、配置和索引保存在 core/.linux-acceptance；Python环境在 core/.venv-linux。
更新：先退出服务，将同版本程序更新包内 Telyincore_linux 的内容合并覆盖本目录，再启动和Ctrl+F5。
程序更新包不包含或覆盖 .linux-acceptance、.venv-linux 或已有账号/插件运行资料。
## Linux QQ（x86_64）
SnowLuma 由 SnowLumaDevs 开发，本包采用归档发行版 v2.4.2 的原始 linux-x64.tar.gz（github.com/XingYunStar/snowluma/releases/tag/v2.4.2）。组件许可证在 core/linux-components/LICENSE.SnowLuma，EULA.md 与 PRIVACY.md 随上游组件保留。
首次正常启动或更新后启动会从包内校验并安装 SnowLuma 2.4.2 及其 Node。仅检查已安装组件可运行 bash "启动 Telyincore.sh" --check-qq。
安装并登录桌面版 Linux QQ。从启动器唤醒 Bot 会同时启动 SL，已有同部署 SL 时复用。自动启动的 SL 随 Core 退出，手动启动的独立 SL 由原终端管理。打开启动器显示的 SL 管理地址（默认 http://127.0.0.1:5099），首次凭据见本机 runtime/linux-snowluma/launcher.log，确认 SL 协议并完成账号接入。QQ 插件启用时，在桌面环境点「唤醒」会复用可接入的 QQ 或打开已安装的 QQ。已有 QQ 需要兼容启动时，按提示从托盘退出一次，Core 会用原档案自动重开。无需单独启动 QQ。系统库在 Btrfs 上出现映射标识差异时，启动器自动将所需库复制到私有 tmpfs；需要附加权限时使用当前用户的 user namespace。发行包支持 Linux x86_64 原生桌面 QQ 或已注册桌面入口的 QQ AppImage。系统禁止普通用户所需的命名空间、缺少桌面 QQ 或适用的临时文件系统时，请查看启动器提示和 runtime/linux-snowluma/qq-desktop.log。仍可通过 --start-qq 手动启动，--install-launcher 可注册桌面快捷入口。
首次唤醒即可在 Core 的 QQ 插件面板查看初始管理密码。在 SL 管理页完成首次登录、协议确认与改密。Core 会识别已验证在线的本机账号，可直接同步其协议配置。需要从 Core 执行管理恢复时，请在账号和管理凭据中保存当前管理密码。
在 SL 管理页配置 OneBot HTTP 与正向 Universal WebSocket，选本机回环地址、两个不同端口、array 消息格式和令牌。在 Core 中导入当前账号的连接配置、保存允许的私聊或群号，再检测接入。也可在休止 Core 后使用 --qq-setup 手动配置。
先在名单中的私聊发一条测试消息，核对收信、回复所在会话及协议回执。SL WebUI 就绪只表示管理服务可用。
''').encode()
    files['MANIFEST.sha256']=''.join(hashlib.sha256(b).hexdigest()+'  '+n+'\n' for n,b in sorted(files.items())).encode()
    destination.mkdir(parents=True,exist_ok=True)
    target=destination/f'Telyincore_linux_{version}.zip'
    if target.exists():raise FileExistsError(target)
    with zipfile.ZipFile(target,'x',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for n,b in sorted(files.items()):
            info=zipfile.ZipInfo('Telyincore_linux/'+n)
            info.compress_type=zipfile.ZIP_DEFLATED
            info.external_attr=(stat.S_IFREG | (0o755 if n.endswith('.sh') else 0o644))<<16
            z.writestr(info,b)
    with zipfile.ZipFile(target) as z:
        if z.testzip() is not None:raise ValueError('corrupt Linux archive')
    target.with_suffix('.sha256').write_text(hashlib.sha256(target.read_bytes()).hexdigest()+'  '+target.name+'\n')
    return target


if __name__=='__main__':
    import sys
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--version',default='beta.0.3.1');args=parser.parse_args()
    print(build(Path(__file__).resolve().parents[1],args.output,args.version))
