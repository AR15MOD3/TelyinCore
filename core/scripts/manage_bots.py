"""Offline Bot archive, recoverable delete and missing-folder retirement."""
import argparse
import json
import sys
from pathlib import Path
CORE = Path(__file__).resolve().parents[1]
if str(CORE) not in sys.path: sys.path.insert(0, str(CORE))
from bot_lifecycle import BotLifecycle
from launcher_presentation import startup_header
from startup_terminal import menu_notice as notice
from startup_terminal import menu_header

ACTIONS = {"rename":"修改Bot档案名称", "export":"迁出Bot档案", "issues":"处理档案异常", "resume-rename":"继续未完成的改名", "archive":"将Bot档案归档", "delete":"删除 Bot 档案（可恢复）",
           "register-copy":"将副本识别为新档案", "retire-missing":"确认缺失档案已移出", "restore":"恢复已归档的档案",
           "rollback":"回退未完成的档案操作"}



def show_issues(core, choose):
    from bot_migration_gate import GATE
    from runtime_identity import MARKER, _object, bindings, profile_issues, safe_path
    from bot_offline_rename import KIND

    service = BotLifecycle(core)
    try:
        view = service.inventory()
        labels, commands = [], []
        gate = service.base / GATE
        if gate.exists():
            if _object(gate).get('kind') == KIND:
                labels.append(ACTIONS['resume-rename'])
                commands.append(['resume-rename'])
            labels.append(ACTIONS['rollback'])
            commands.append(['rollback'])
        issues = profile_issues(service.base, service.bots)
        registered_missing = {row['name'] for row in view['bots'] if row['missing'] and row['bound']}
        for name, issue in issues.items():
            if name in registered_missing:
                labels.append(name + ' · 档案缺失')
                if service._baseline(name):
                    commands.append(name + ' 是本机早期安装的主档案。请先放回原目录，再使用归档或迁出。')
                else:
                    commands.append(['retire-missing', name])
            else:
                copied = {}
                try:
                    marker = safe_path(service.bots, service.bots / name / MARKER)
                    if marker.is_file():
                        copied = _object(marker)
                except (OSError, ValueError):
                    pass
                original = copied.get('name')
                rows = bindings(service.base)
                if (isinstance(original, str) and original != name and original in rows
                        and original not in issues and copied.get('token') == rows[original]):
                    labels.append(name + ' · 将副本识别为新档案')
                    commands.append(['register-copy', name])
                else:
                    labels.append(name + ' · 检查档案')
                    commands.append(issue)
        if not labels:
            menu_header(ACTIONS['issues'], hero=True)
            notice('archives', '当前没有档案异常。')
        else:
            index = choose(ACTIONS['issues'], labels + ['返回档案菜单'])
            if index is None or index == len(commands):
                return
            command = commands[index]
            if isinstance(command, str):
                notice('archives', command)
            else:
                if command[0] == 'retire-missing':
                    notice('archives', '如果是误移或改名，请先把档案放回原路径。确认已移出后，才清理本机登记。')
                if main(command, core=core) == 2:
                    return
    except (OSError, ValueError) as error:
        notice('archives', '无法读取档案异常：' + str(error), level='ERROR')
    input('  按回车返回档案菜单。')

def main(argv=None, core=CORE):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments and sys.stdin.isatty():
        from scripts.core_menu import choose
        while True:
            actions = ('archive', 'export', 'restore', 'rename', 'issues')
            choice = choose('档案', [*(ACTIONS[action] for action in actions), '返回主菜单'])
            if choice is None or choice == len(actions): return 0
            action = actions[choice]
            result = 0
            if action == 'issues':
                show_issues(core, choose)
                continue
            if action in ('rollback','resume-rename'):
                result = main([action], core=core)
            else:
                try:
                    view = BotLifecycle(core).inventory()
                    if action == 'restore':
                        rows = [r for r in view['archives'] if r['action'] in ('archive','delete') and r['state']=='committed' and r['name'] in view['retired']]
                        labels = [r['name'] + ' · ' + r['id'][:8] for r in rows]
                        targets = [r['id'] for r in rows]
                    else:
                        rows = [r for r in view['bots'] if r['missing'] == (action == 'retire-missing')]
                        labels = [r['name'] for r in rows]; targets = labels
                    if not rows:
                        menu_header(ACTIONS[action], hero=True)
                        notice('archives','当前没有符合此操作的档案。')
                    else:
                        selected = choose(ACTIONS[action] + ' / 选择档案', labels + ['返回档案菜单'])
                        if selected is None or selected == len(rows): continue
                        result = main([action, targets[selected]], core=core)
                except (OSError,ValueError) as exc: notice('archives',str(exc),level='ERROR')
            if result == 2: continue
            input('  按回车返回档案菜单。')
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', nargs='?', choices=['list','rename','resume-rename','archive','delete','retire-missing','restore','export','register-copy','rollback'], default='list')
    p.add_argument('target', nargs='?')
    p.add_argument('--new-name', help='新的档案名称（目录、内部身份及显示名）')
    p.add_argument('--confirm', help='完整 Bot 文件夹名；不提供时交互输入')
    p.add_argument('--destination', help='迁出到哪个现有文件夹')
    args = p.parse_args(arguments)
    service = BotLifecycle(core)
    try:
        if args.action == 'list':
            view = service.inventory()
            startup_header('Bot 档案 · 离线维护')
            for row in view['bots']:
                print(f"  {row['name']} · {'目录缺失' if row['missing'] else '档案在库'}")
            for row in view['archives']:
                print(f"  {row['id']} · {row['name']} · {row['action']} · {row['state']}")
            if view['interrupted']: print('有中断的事务；请使用 rollback 核对并回退，勿手动删除门禁。')
            print('\n操作：rename 改名（--new-name 新名称） / archive 归档 / delete 删除（保留副本） / retire-missing 退役误删身份 / restore 恢复归档')
            print('用法：--manage-bots archive Bot文件夹名；恢复时填写上面的归档编号。')
            return 0
        menu_header(ACTIONS[args.action], hero=True)
        if args.action in ('rename','resume-rename'):
            from bot_offline_rename import OfflineRename
            rename = OfflineRename(core)
            if args.action == 'resume-rename':
                from scripts.archive_progress import ArchiveProgress
                with ArchiveProgress() as progress:
                    rename.progress = progress
                    progress('读取未完成的改名记录')
                    result = rename.recover(rollback=False)
            else:
                if not args.target: p.error('请提供原档案名称')
                from scripts.maintenance_prepare import preflight_rename
                preflight_rename(core)
                new = args.new_name
                if new is None:
                    new = input('  新档案名称（回车取消）：').strip()
                if not new: return 2
                notice('archives', args.target + ' → ' + new)
                # Validate identity inputs before asking to stop any service.
                from bot_registry import bot_id
                from runtime_identity import safe_path
                bot_id(args.target); bot_id(new)
                if args.target.casefold() == new.casefold():
                    raise ValueError('新名称必须与原名称不同')
                if not safe_path(service.root, service.bots / args.target).is_dir():
                    raise ValueError('原档案目录不存在')
                if any(p.name.casefold() == new.casefold() for p in service.bots.iterdir()):
                    raise ValueError('目标档案名称已存在')
                services = []
                if args.confirm is None:
                    from scripts.maintenance_prepare import running_services
                    services = running_services(core)
                notice('archives','同步修改目录、内部名称和显示名；保留历史正文。')
                if services:
                    notice('archives','仍在运行的服务：' + '、'.join(services))
                    notice('archives','确认后先正常休止以上服务，等待全部收尾，再修改档案名称；不会强制结束进程。')
                else:
                    notice('archives','改名需要全部服务退出；网页暂停不等于休止。仍有启动或收尾时会停止操作。')
                confirm = args.confirm
                if confirm is None:
                    confirm = input('  请输入原档案名称确认（回车取消）：')
                if not confirm:return 2
                if confirm != args.target:
                    raise ValueError('请输入原档案名称确认')
                from scripts.archive_progress import ArchiveProgress
                with ArchiveProgress() as progress:
                    rename.progress = progress
                    if args.confirm is None:
                        from scripts.maintenance_prepare import wait_for_maintenance
                        progress('等待服务正常休止与收尾')
                        wait_for_maintenance(core, services)
                    result = rename.rename(args.target,new,confirm)
            notice('archives','改名完成：' + result['name'])
            return 0
        if args.action == 'rollback':
            if args.confirm != 'ROLLBACK' and input('  回退未完成的档案操作，请输入 ROLLBACK（回车取消）：') != 'ROLLBACK': return 2
            from bot_migration_gate import GATE
            from runtime_identity import _object
            from bot_offline_rename import OfflineRename, KIND
            gate = service.base/GATE
            result = (OfflineRename(core).recover() if gate.exists() and _object(gate).get('kind') == KIND
                      else service.rollback())
        else:
            if not args.target: p.error('请提供 Bot 文件夹名或归档编号')
            confirm = args.confirm
            if confirm is None:
                target_name = service._journal(args.target)[1]['name'] if args.action == 'restore' else args.target
                notice('archives', '目标档案：' + target_name)
                descriptions = {
                    'register-copy': '给副本建立独立身份，保留人设和记忆。首次唤醒后需要重新接入 QQ。',
                    'export': '复制到指定文件夹并核对内容，成功后将本机档案归档。',
                    'archive': '档案及独立运行资料将移入归档库，并从可唤醒列表中移除；可恢复。',
                    'delete': '档案将从可唤醒列表中删除，恢复副本仍保留在归档库；不会永久清空。',
                    'retire-missing': '保留本机剩余的运行资料，将此 Bot 从本机登记中移除。',
                    'restore': '恢复原档案及身份；不会覆盖现有同名档案。',
                }
                notice('archives', descriptions[args.action])
                if args.action in ('archive','delete') and service._baseline(target_name):
                    notice('archives','此档案使用旧共享运行根：私有会话与索引会一并归档；共享模型配置和其他 Bot 数据保留。')
                notice('archives', '请先休止所有 Bot。启动器会确认它们都已退出，再处理本机登记。')
                while True:
                    confirm = input(f'  请输入 {target_name} 确认（回车取消）：')
                    if not confirm:
                        return 2
                    if confirm == target_name:
                        break
                    notice('archives', f'名称不匹配，请输入 {target_name}。', level='WARN')
            if args.action == 'register-copy':
                result = service.register_copy(args.target, confirm)
            elif args.action == 'export':
                destination = args.destination
                if destination is None:
                    destination = input('  迁出到哪个文件夹（回车取消）：').strip().strip('"')
                if not destination:
                    return 2
                from scripts.archive_progress import ArchiveProgress
                with ArchiveProgress() as progress:
                    progress('正在复制并核对档案，完成后会归档本机副本')
                    result = service.remove(args.target, 'archive', confirm, destination=destination)
                notice('archives', '迁出目录：' + result['destination'])
            elif args.action == 'restore':
                result = service.restore(args.target, confirm)
            else:
                result = service.remove(args.target, args.action, confirm)
        notice('archives', ACTIONS[args.action] + '已完成：' + result['name'])
        if result.get('id'): notice('archives','归档记录：bots/.archives/' + result['id'])
        return 0
    except (OSError, ValueError) as exc:
        message = str(exc)
        if isinstance(exc, BlockingIOError) or message == 'another startup/maintenance admission owns this core':
            from scripts.maintenance_prepare import BUSY
            message = BUSY
        notice('archives', ACTIONS.get(args.action, '读取档案') + '未完成：' + message, level='ERROR')
        # Preserve the exact failing operation for remote Windows diagnosis.
        try:
            import traceback
            from datetime import datetime
            from runtime_identity import safe_path
            folder = safe_path(Path(core), Path(core)/'data/diagnostics')
            folder.mkdir(parents=True, exist_ok=True)
            report = safe_path(Path(core), folder/('archives-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.json'))
            report.write_text(json.dumps({'code':'BOT_ARCHIVE_' + args.action.upper().replace('-', '_') + '_FAILED',
                'action':args.action,'target':args.target,
                'error_type':type(exc).__name__, 'error':str(exc),'traceback':traceback.format_exc()},ensure_ascii=False,indent=2),encoding='utf-8')
            notice('report','完整诊断：' + str(report))
        except (OSError, ValueError): pass
        from bot_migration_gate import GATE
        if (Path(core)/'data'/GATE).exists():
            notice('archives','存在未完成事务；请到「档案 → 处理档案异常」继续或回退。',level='WARN')
        else: notice('archives','本次档案操作未完成；未绕过运行保护。核对记录与备份均保留。')
        return 1


if __name__ == '__main__': raise SystemExit(main())
