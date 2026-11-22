"""Linux profile acceptance, deliberately separate from formal Bot lifecycle."""
from __future__ import annotations
import argparse,json,sys,os,socket,threading
from pathlib import Path
CORE=Path(__file__).resolve().parents[1]
if str(CORE) not in sys.path:sys.path.insert(0,str(CORE))
from scripts.linux_dev import check_runtime,clean_model_environment,_plain_path,_open_when_ready
from tools.prepare_telyin_acceptance import TREES, prepare

BOT='TelyinLinux';BASELINE='AcceptanceBaseline'


def prepare_workspace(core=CORE, *, configure_qq=False):
    core=Path(core).resolve(strict=True);workspace=core/'.linux-acceptance'
    marker=workspace/'acceptance.json'
    expected={'version':1,'mode':'linux-profile-acceptance','source_core':str(core),'bot':BOT}
    _plain_path(core,marker)
    if workspace.exists():
        from bot_registry import bot_id
        actual = json.loads(marker.read_text()) if marker.is_file() else {}
        if isinstance(actual.get('bot'), str): expected['bot'] = bot_id(actual['bot'])
        if not marker.is_file() or actual!=expected:
            raise ValueError('Bot 档案目录归属不匹配，无法启动')
    else:
        workspace.mkdir(mode=0o700);marker.write_text(json.dumps(expected))
    from bot_migration_gate import assert_no_migration
    assert_no_migration(workspace/'core/data')
    from bot_baseline_archive import LINUX_RECEIPT
    from bot_migration_gate import retired_names
    if (workspace/'core/data'/LINUX_RECEIPT).exists() or expected['bot'] in retired_names(workspace/'core/data'):
        raise ValueError('当前 Bot 已归档，请从「档案 → 恢复已归档的档案」恢复后再唤醒')
    for folder,dirs,files in os.walk(workspace,followlinks=False):
        for name in dirs+files:_plain_path(core,Path(folder)/name)
    data=workspace/'core/data';data.mkdir(parents=True,exist_ok=True)
    bot=workspace/'bots'/expected['bot']
    if not bot.exists():
        if expected['bot'] != BOT: raise ValueError('已改名的档案目录缺失，不自动新建替代档案')
        source=core.parent/'bots/Telyin'
        if source.is_dir():
            report=prepare(source,bot,display_name='特琳')
        else:
            import shutil,tempfile
            with tempfile.TemporaryDirectory(prefix='linux-bot-default-') as temporary:
                seed=Path(temporary)/'Mybot'
                shutil.copytree(core/'templates/bot-default',seed)
                for tree in TREES: (seed/tree).mkdir(parents=True,exist_ok=True)
                report=prepare(seed,bot,display_name='Mybot')
        (workspace/'source-manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    if ((bot/'infinity').exists() and not configure_qq and not (workspace/'linux-qq-enabled').is_file()
            or (data/'extensions').exists()):
        raise ValueError('档案的插件配置与当前启动模式不匹配，请检查 QQ 接入设置')
    owner=data/'bot-runtime-owner.json'
    if owner.exists():
        if json.loads(owner.read_text())!={'baseline':BASELINE}:raise ValueError('Bot 运行数据归属不匹配')
    else:owner.write_text(json.dumps({'baseline':BASELINE}))
    engine=data/'engine.json'
    if not engine.exists():
        from telyincore.config import load_config
        output=load_config(env={}).max_tokens
        catalog=core/'data/engine.json'
        if catalog.is_file():
            original=json.loads(catalog.read_text()).get('profiles',{}).get('DeepSeek',{}).get('max_tokens')
            if type(original) is int and original>0:output=original
        engine.write_text(json.dumps({'current':'DeepSeek','profiles':{'DeepSeek':{
            'name':'DeepSeek','kind':'chat','base_url':'https://api.deepseek.com/v1',
            'model':'deepseek-chat','thinking':False,'max_tokens':output}}}))
    return data


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if "--manage-bots" in arguments:
        data=CORE/'.linux-acceptance/core/data'
        if not (data.parent.parent/'acceptance.json').is_file():
            print('请先完成 Linux 档案首次准备。');return 2
        from scripts.manage_bots import main as manage
        args=arguments[arguments.index('--manage-bots')+1:]
        if args and args[0] not in ('list','rename','resume-rename','rollback','archive','export','restore'):
            print('此命令不适用于 Linux 单档案部署。');return 2
        return manage(args,core=data.parent)
    p=argparse.ArgumentParser(description='启动 TelyinCore Linux 服务')
    p.add_argument('--port',type=int,default=7810);p.add_argument('--no-browser',action='store_true')
    p.add_argument('--check',action='store_true');p.add_argument('--check-documents',action='store_true')
    args=p.parse_args(argv);check_runtime()
    if args.check:print('Core 运行环境就绪。');return 0
    if not 1024<=args.port<=65535:p.error('端口范围1024–65535')
    data=prepare_workspace(configure_qq=args.check_documents);clean_model_environment()
    if args.check_documents:
        from scripts.check_document_storage import check_documents
        report=check_documents(CORE)
        output=data/'acceptance/documents.json';output.parent.mkdir(exist_ok=True);output.write_text(json.dumps(report,ensure_ascii=False,indent=2))
        for row in report['checks']:print(('PASS ' if row['ok'] else 'FAIL ')+row['name'])
        print('Report:',output);return 0 if report['ok'] else 1
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);probe.bind(('127.0.0.1',args.port))
    stopped=threading.Event()
    if not args.no_browser:threading.Thread(target=_open_when_ready,args=(args.port,stopped),daemon=True).start()
    from launch import main as launch_main
    try:return launch_main(['--acceptance-isolated','--profile-acceptance','--acceptance-root',str(data),'--acceptance-bot',json.loads((data.parent.parent/'acceptance.json').read_text())['bot'],'--port',str(args.port),'--start-paused'])
    finally:stopped.set()


if __name__=='__main__':raise SystemExit(main())
