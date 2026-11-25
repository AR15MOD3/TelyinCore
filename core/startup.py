"""Choose a Bot before launch; one fixed Bot per process and data lock."""
from __future__ import annotations

import argparse
from contextlib import nullcontext
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from startup_terminal import header, paint, running, ChildJob, ChildJobFailure, watch
from launcher_presentation import notice
from bot_birth_process import BirthPopen
from bot_launcher_host import LauncherHost
from console_output import ConsoleOutput
from bot_launcher_child import LauncherShutdown, child_environment
from bot_launcher_lifecycle import ParentLauncherChannel

from bot_registry import BotRegistry, bot_id
from bot_bootstrap import ensure_bot_support
from bot_startup_probe import (refuse_existing_instance,
    owned_ready_port as controlled_ready_port, OwnedLauncherControl)


def _finish_control_shutdown(console_control, shutdown, parent_channel, launcher_host,
                             *, primary, channel_error, unwinding, cleanup_state):
    """Collect failures, attempt both retirement barriers, then preserve the primary."""
    from bot_console_control import collect_control_close_failure
    failure = collect_control_close_failure(console_control, shutdown, primary=primary)
    cleanup_state['control_close_failed'] = failure is not None
    barriers = []
    if parent_channel is not None:
        try:
            waiter = getattr(parent_channel, 'wait_companion', None)
            if callable(waiter):
                waiter()
        except BaseException as error:
            barriers.append(error)
        try:
            if launcher_host is not None and getattr(parent_channel, '_terminal_host', None) is not None:
                launcher_host.assert_lifecycle_retired()
        except BaseException as error:
            barriers.append(error)
    cleanup_state['retirement_failed'] = bool(barriers)
    control_primary = next((error for _, _, error in failure.failures
                            if error is not None and not isinstance(error, Exception)), None) if failure else None
    selected = (primary if primary is not None else unwinding if unwinding is not None
                else control_primary if control_primary is not None else channel_error
                if channel_error is not None else barriers[0] if barriers else failure)
    if selected is not None:
        if failure is not None:
            selected._control_close_failure = failure
        selected._retirement_errors = tuple(barriers)
        if channel_error is not None and channel_error is not selected:
            selected._channel_cleanup_error = channel_error
        if unwinding is not None and unwinding is not selected:
            selected._unwinding_cleanup_error = unwinding
        raise selected


def runtime_root(base: Path, name: str, bots: Path | None = None, *, scoped=False) -> Path:
    """Guarded launch path; two-argument form only resolves the legacy layout."""
    from runtime_identity import identity_lock, bind_runtime
    from bot_migration_gate import assert_identity_allowed
    assert_identity_allowed(base, name)
    with identity_lock(base):
        assert_identity_allowed(base, name)
        if bots is not None:
            bind_runtime(base, bots, bot_id(name), scoped=scoped)
        return _runtime_root_unchecked(base, name)


def _runtime_root_unchecked(base: Path, name: str) -> Path:
    """Keep the legacy owner's data in place; other Bots get separate runtimes."""
    from bot_migration_gate import assert_identity_allowed
    assert_identity_allowed(base, name)
    name = bot_id(name)
    base.mkdir(parents=True, exist_ok=True)
    owner = base / 'bot-runtime-owner.json'
    if not owner.exists():
        legacy = base / 'active-bot.json'
        baseline = name
        if legacy.exists():
            baseline = bot_id(json.loads(legacy.read_text(encoding='utf-8'))['baseline'])
        try:
            with owner.open('x', encoding='utf-8') as stream:
                json.dump({'baseline': baseline}, stream)
        except FileExistsError:
            pass
    baseline = bot_id(json.loads(owner.read_text(encoding='utf-8'))['baseline'])
    return base if name == baseline else base / 'bot-runtimes' / name


def instance_path(base: Path, name: str) -> Path:
    return base / 'instances' / (bot_id(name) + '.json')


def register_instance(base: Path, name: str, port: int) -> None:
    from bot_migration_gate import assert_identity_allowed
    from runtime_identity import identity_lock, safe_path
    assert_identity_allowed(base, name)
    with identity_lock(base):
        assert_identity_allowed(base, name)
        path = safe_path(base, instance_path(base, name))
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = safe_path(base, path.with_suffix('.tmp'))
        temp.write_text(json.dumps({'port': port, 'pid': os.getpid()}), encoding='utf-8')
        temp.replace(path)


def existing_port(core: Path, name: str) -> int | None:
    from bot_migration_gate import assert_identity_allowed
    assert_identity_allowed(core / 'data', name)
    found = None
    try:
        data = json.loads(instance_path(core / 'data', name).read_text(encoding='utf-8'))
        port = int(data['port'])
        if not 1 <= port <= 65535:
            return None
        with urllib.request.urlopen(
                f'http://127.0.0.1:{port}/infinity/bots', timeout=1) as response:
            live = json.load(response)
        if (live.get('active') == name and live.get('pid') == data['pid']
                and Path(live.get('coreRoot', '')).resolve() == core.resolve()):
            found = port
    except (OSError, ValueError, KeyError, TypeError):
        pass
    # A fence installed during the probe must not be swallowed as a stale instance.
    assert_identity_allowed(core / 'data', name)
    return found


def available_port(start: int) -> int:
    if not 1 <= start <= 65535:
        raise ValueError('端口应在1–65535之间')
    for port in range(start, 65536):
        with socket.socket() as probe:
            if os.name == 'nt':
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                probe.bind(('127.0.0.1', port))
                return port
            except OSError:
                continue
    raise ValueError('没有可用端口')


def ordered_bots(book: BotRegistry) -> list[dict]:
    """Default Mybot first; others oldest first, stable across display renames."""
    def key(row: dict) -> tuple:
        folder = book.folder(row['id'])
        created = None
        try:
            meta = json.loads((folder / 'bot.json').read_text(encoding='utf-8'))
            value = meta.get('createdAtNs')
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                created = value
        except (OSError, ValueError, AttributeError):
            pass
        if created is None:
            stat = folder.stat()
            created = getattr(stat, 'st_birthtime_ns', stat.st_ctime_ns)
        return (row['id'].casefold() != 'mybot', created, row['id'].casefold())
    from runtime_identity import bindings, profile_issues
    issues = profile_issues(book.runtime_base, book.parent)
    registered = bindings(book.runtime_base)
    return sorted((dict(row, pending=row['id'] not in registered)
                   for row in book.read()['bots'] if row['id'] not in issues), key=key)


def pick_bot(book: BotRegistry) -> str | None:
    return _pick_bot(book)


def _pick_bot(book: BotRegistry) -> str | None:
    """Use the same bounded temporary-screen navigation as the main menu."""
    from scripts.core_menu import choose
    while True:
        rows = ordered_bots(book)
        labels = [(row['id'], '首次唤醒时建立本机运行登记')
                  if row.get('pending') else row['id'] for row in rows]
        index = choose('唤醒 / 选择 Bot 档案', labels + ['+ 新建 Bot 档案', '返回主菜单'], hero=False)
        if index is None or index == len(rows) + 1: return None
        if index < len(rows): return rows[index]['id']
        try:
            name = input('  新档案文件夹名（回车返回）：').strip()
            if name:
                book.create({'id': name})
                return name
        except (EOFError, KeyboardInterrupt): return None
        except (ValueError, OSError) as error:
            notice('archives', str(error), level='ERROR')
            input('  按回车返回档案选择。')


def instance_registry(core,name):
    """Unsealed observation only. Does not attest empty OS host or touch account files."""
    from bot_host_registry import HostEvidenceRegistry
    from runtime_identity import identity_lock,safe_path
    from bot_migration_gate import assert_identity_allowed
    from bot_startup_probe import _json
    base=core/'data'
    with identity_lock(base):
        assert_identity_allowed(base,name)
        path=safe_path(base,base/'bot-runtime-owner.json')
        if path.exists():
            with path.open('rb') as f:raw=f.read(4097)
            if len(raw)>4096:raise ValueError('bounded launcher owner metadata required')
            baseline=bot_id(_json(raw).get('baseline'))
        else:baseline=name  # observation label only, no owner manifest/bootstrap creation
    return HostEvidenceRegistry(core,sorted({baseline,name}),baseline)


def main(argv=None, *, host_registry=None, launch_options=None,
         reuse_start_paused=False) -> int:
    # The lifetime lease starts before selection, writes or native birth, and
    # remains held through the original finally/output/native retirement.
    from bot_global_admission import StartupAdmission
    from launcher_exit_log import launcher_exit_log
    core = Path(__file__).resolve().parent
    # A restart gets a fresh admission/registry only after the old finally block
    # has retired the exact child, job, output and endpoint. Never recurse or
    # apply a staged Core update as a side effect of a plugin operation.
    exact_port = False
    while True:
        update_handoff, plugin_handoff = [], []
        with launcher_exit_log(core), StartupAdmission(core) as admission:
            code = _main_owned(argv, host_registry=host_registry, launch_options=launch_options,
                               reuse_start_paused=reuse_start_paused, update_handoff=update_handoff,
                               plugin_handoff=plugin_handoff, exact_port=exact_port)
            admission.finish(code)
        if code == 0 and plugin_handoff:
            name, port = plugin_handoff[0]
            argv = ['--bot', name, '--port', str(port), '--no-browser']
            exact_port = True
            continue
        if code == 0 and update_handoff:
            name, port = update_handoff[0]
            return subprocess.call([sys.executable, str(core/'scripts/online_update.py'), '--relaunch',
                                    '--bot', name, '--port', str(port)], cwd=core)
        return code


def _main_owned(argv=None, *, host_registry=None, launch_options=None,
                reuse_start_paused=False, update_handoff=None, plugin_handoff=None, exact_port=False) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=7799)
    parser.add_argument('--bot', help='跳过交互，启动指定Bot')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--console', action='store_true')
    parser.add_argument('--runtime-evidence', action='store_true',
                        help='Enable finite local evidence through authenticated launch options')
    args = parser.parse_args(argv)
    from bot_launcher_options import normalize_options,options_argv
    launch_options=normalize_options(launch_options)
    requires_fresh_instance = (launch_options.get('retrieval_at_tail')
                               or (launch_options.get('start_paused') and not reuse_start_paused))
    if args.runtime_evidence:launch_options['runtime_evidence']=True
    if launch_options.get('console') and not args.console:raise ValueError('console mode must be explicit')
    if args.console:launch_options['console']=True
    if host_registry is not None and launch_options:raise ValueError('web options require default observation parent')
    core = Path(__file__).resolve().parent
    instance_only = host_registry is None
    # Programmatic opt-in only: no flag/env bootstrap, adoption or global registry.
    # The caller must provide real exclusive-host coverage; this hook covers only
    # this launcher's immediate child, not launch.py's plugins or other starters.
    if host_registry is not None:
        from bot_host_registry import HostEvidenceRegistry
        if (type(host_registry) is not HostEvidenceRegistry
                or host_registry.core.resolve() != core.resolve()):
            raise ValueError('host registry scope mismatch')
    book = BotRegistry(core.parent / 'bots', core / 'templates/bot-default', '')
    from runtime_identity import check_layout
    check_layout(core / 'data', core.parent / 'bots', discovery=True)
    if not book.read()['bots'] and not args.bot:
        parent = core.parent / 'bots'
        if parent.is_dir() and any(p.is_dir() and not p.name.startswith('.')
                                  for p in parent.iterdir()):
            raise ValueError('已有目录但无有效 Bot；请修复档案，不自动新建 Mybot')
        from bot_baseline_archive import archived_baseline
        from runtime_identity import bindings
        if not bindings(core / 'data') and archived_baseline(core / 'data') is None:
            book.create({'id': 'Mybot'})
    name = bot_id(args.bot) if args.bot else pick_bot(book)
    if name is None:
        return 0
    if host_registry is not None and name not in host_registry.bots:
        raise ValueError('Bot outside host registry scope')
    from bot_migration_gate import assert_identity_allowed
    assert_identity_allowed(core / 'data', name)
    check_layout(core / 'data', core.parent / 'bots', selected=name)
    folder = book.folder(name)
    if not book.valid(folder):
        raise ValueError(f'档案 {name} 缺少可读取的人设文件；请到「档案 → 处理档案异常」检查')
    # No support-file writes before identity preflight; child performs initialization.
    if host_registry is not None:
        refuse_existing_instance(core, name, host_registry)
    old = existing_port(core, name) if host_registry is None and not args.console else None
    if old is not None:
        if host_registry is not None:
            raise ValueError('controlled launcher cannot adopt an existing service')
        if requires_fresh_instance:
            raise ValueError('start-paused or retrieval-tail experiment requires stopping this Bot first; existing process was not changed')
        running(name, old, Path(), reused=True)
        if not args.no_browser:
            webbrowser.open(f'http://127.0.0.1:{old}/#/live')
        return 0
    # Serialize port allocation until the child has bound its listener.
    from launch import _acquire_instance_lock, _release_instance_lock
    allocation = core / 'data' / 'startup-port-allocation'
    lock = None
    for _ in range(150):
        lock = _acquire_instance_lock(allocation)
        if lock is not None:
            break
        time.sleep(0.2)
    if lock is None:
        raise ValueError('其他实例仍在启动，请稍后重试')
    process = None
    registered_pid = None
    job = None
    output = None
    shutdown = None
    parent_channel = None
    console_control = None
    launcher_host = None
    endpoint_recovered = False
    restart_capability = ""
    try:
        try:
            if host_registry is not None:
                refuse_existing_instance(core, name, host_registry)
            old = existing_port(core, name) if host_registry is None and not args.console else None
            if old is not None:
                if host_registry is not None:
                    raise ValueError('controlled launcher cannot adopt an existing service')
                if requires_fresh_instance:
                    raise ValueError('start-paused or retrieval-tail experiment requires stopping this Bot first; existing process was not changed')
                running(name, old, reused=True)
                if not args.no_browser:
                    webbrowser.open(f'http://127.0.0.1:{old}/#/live')
                return 0
            if host_registry is None:
                host_registry = instance_registry(core,name)
                # Endpoint recovery runs under the port allocation lock.
                from bot_instance_recovery import recover_stale_endpoint
                endpoint_recovered = recover_stale_endpoint(core,name)
                refuse_existing_instance(core,name,host_registry)
            if args.console and not 0<args.port<=65535:raise ValueError('bounded console port placeholder required')
            port = args.port if args.console else available_port(args.port)
            if exact_port and port != args.port:
                raise ValueError('重启所需的原页面端口被占用，未改用其他端口或启动重复 Bot。')
            companion_preflight = None
            companion_available = True
            if instance_only:
                try:
                    from bot_companion_custody import check_previous_companion
                    from snowluma_instance import initialize_instance
                    initialize_instance(core, name, baseline=name == host_registry.baseline, exclude_ports=(port,))
                    if os.name == 'nt':
                        companion_preflight = check_previous_companion(core, name)
                except (ImportError, OSError, ValueError, RuntimeError) as error:
                    companion_available = False
                    companion_preflight = f'本次启动已关闭 QQ 托管权限，Core 继续运行：{error}'
            header('正在启动…')
            print('  Bot    ' + paint(name, '97'), flush=True)
            if endpoint_recovered:
                ConsoleOutput(None).line('[startup] 已归档上次运行遗留的启动记录，继续启动当前 Bot。\n')
            if companion_preflight:
                ConsoleOutput(None).line('[startup] ' + companion_preflight + '\n')
            flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
            with (host_registry.observe_window(name) if instance_only else
                  host_registry.start_window(name)) as birth:
                label,options = None,{}
                if host_registry is not None:
                    parent_channel=(ParentLauncherChannel(host_registry,name,instance_only=True,companion_available=companion_available,**(dict(launch_options=launch_options) if launch_options else {})) if instance_only else
                                    ParentLauncherChannel(host_registry,name))
                    label,options=parent_channel.spawn_options()
                command=[sys.executable,'-u',str(core/'bot_launcher_child.py')]
                if label is not None:command.append('--parent-lifecycle-handle='+label)
                if args.console:
                    from bot_console_control import ConsoleControlPipe
                    console_control=ConsoleControlPipe()
                    command.append('--parent-control-handle='+console_control.inherit(options))
                command += ['--root',str(core/'data'),'--bot',name,'--port',str(port)]
                command += options_argv(launch_options)
                from native_python import python_command
                command,spawn_environment = python_command(command[1:],
                    env=(dict(os.environ,PYTHONIOENCODING="utf-8") if instance_only
                         else child_environment()))
                if instance_only and not args.console and os.name == 'nt':
                    from telyincore.plugin_service_restart import ENV, offer
                    restart_capability = offer(os.getpid())
                    spawn_environment = dict(spawn_environment, **{ENV: restart_capability})
                if instance_only:
                    launcher_host = LauncherHost('startup.console' if args.console else 'startup.web',
                        command,core,name,launch_options=launch_options)
                    launcher_host.bind_parent(parent_channel)
                try:
                    from bot_global_admission import before_birth, register_birth
                    before_birth()
                    process = BirthPopen(command,cwd=core,
                        stdin=None if args.console else subprocess.PIPE,bufsize=0,stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,creationflags=flags,
                        env=spawn_environment,**options)
                    job = getattr(process, 'birth_job', None)  # retain before endpoint/birth callbacks
                    register_birth(process)
                    if launcher_host is not None:launcher_host.bind(process)
                except BaseException as error:
                    if isinstance(error, ChildJobFailure):
                        job = error.job
                        process = job.process
                    try:
                        if parent_channel is not None:parent_channel.after_spawn()
                    except BaseException:error.add_note('launcher inherited endpoint cleanup failed')
                    raise
                else:
                    if parent_channel is not None:parent_channel.after_spawn()
                if console_control is not None:console_control.after_spawn()
                if birth is not None:
                    # Descendant/plugin starters do not share this registry yet.
                    # Never expose a complete snapshot between birth and revocation.
                    birth.commit(process.pid, untracked_descendants=True)
                    registered_pid = process.pid
            # The child cannot enter application code before its private frame.
            # Establish/verify the native job BEFORE releasing that admission gate.
            try:
                job = getattr(process, 'birth_job', None) or ChildJob(process)
            except ChildJobFailure as error:
                job = error.job  # retain partially constructed native resources
                raise
            if parent_channel is not None:
                if console_control is None:parent_channel.handoff(process)
                else:parent_channel.handoff(process,control_stream=console_control.writer)
            shutdown = (LauncherShutdown(process) if console_control is None else
                        LauncherShutdown(process,control_stream=console_control.writer))
            reader = ConsoleOutput(process)
            if launcher_host is not None:launcher_host.bind_output(reader)
            output = reader.start()
            if args.console:
                live=None  # no listener, HTTP readiness or instance-marker claim
            else:
                for _ in range(200):
                    if parent_channel is not None:parent_channel.tick()
                    if process.poll() is not None:
                        notice('startup', f'{name} 在页面就绪前退出（退出码 {process.returncode}）。'
                               '请查看上方最后一条错误。 [BOT_EXIT_BEFORE_READY]', level='ERROR')
                        return process.returncode
                    live = (existing_port(core, name) if host_registry is None else
                            controlled_ready_port(core, name, process, host_registry))
                    if live is not None:
                        output.ready(name, live)
                        if not args.no_browser:
                            webbrowser.open(f'http://127.0.0.1:{live}/#/live')
                        break
                    time.sleep(0.15)
                else:
                    notice('startup', f'等待 {name} 的页面就绪超时（端口 {port}）。'
                           '请查看上方启动日志。 [BOT_PAGE_READY_TIMEOUT]', level='ERROR')
                    return 1
        finally:
            _release_instance_lock(lock)
        if args.console:
            from bot_console_control import watch_console
            code=watch_console(process,shutdown=shutdown,pump=parent_channel.tick)
        elif host_registry is None:
            code = watch(process, live, shutdown=shutdown)
        else:
            # live is the verified endpoint, not the initially allocated port.
            controlled = OwnedLauncherControl(core,name,process,host_registry,live)
            code = watch(process, live, controlled=controlled, shutdown=shutdown,
                         pump=getattr(parent_channel,'watch_tick',parent_channel.tick))
        from launcher_exit_log import exit_event
        if restart_capability:
            from telyincore.plugin_service_restart import consume
            if consume(core, process.pid, name, port, restart_capability, exit_code=code):
                if plugin_handoff is not None:
                    plugin_handoff.append((name, port))
        if os.name == 'nt' and code == 0 and update_handoff is not None:
            from telyincore.online_install import requested_windows_restart
            if requested_windows_restart(core.parent, process.pid, name, port):
                update_handoff.append((name, port))
        exit_event('controller_exited', exit_code=code)
        notice('shutdown', 'Bot 进程已退出，正在检查页面和后台服务是否已关闭。')
        return code
    finally:
        local_registration_retired = False
        cleanup_state = {}
        try:
            channel_error=None
            primary=sys.exception()
            if parent_channel is not None:
                try:channel_closed=parent_channel.close()
                except BaseException as error:
                    channel_closed=False
                    if primary is not None:primary.add_note('launcher parent endpoint cleanup failed')
                    else:channel_error=error
                if not channel_closed:
                    print(paint('  启动器与 Bot 的控制连接关闭失败，后台服务是否退出仍需核对。', '33'),flush=True)
            try:
                if job is not None:
                    try:
                        if launcher_host is not None and launcher_host.process is process:
                            launcher_host.close()
                        else:job.close()
                    except BaseException as error:
                        # Still drain our exact child; never mask the original error.
                        if primary is not None:
                            primary._launcher_host = launcher_host  # retain unresolved native resources
                            primary.add_note('native job close failed; handle retained')
                        elif channel_error is None:channel_error=error
                        else:
                            channel_error._launcher_host = launcher_host
                            channel_error.add_note('native job close failed; handle retained')
                if process is not None and process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
            finally:
                try:
                    if registered_pid is not None:
                        # Native absence, not Popen.wait alone, authorizes retirement.
                        # Failure leaves the row present and offline checks blocked.
                        if parent_channel is not None:
                            try:
                                retired = parent_channel.retire_instance()
                                if not retired and instance_path(core/'data',name).exists():
                                    raise ValueError('原启动器未持有实例登记回执；原件已保留')
                                if retired:
                                    notice('shutdown', '本次页面端点登记已移除。')
                            except BaseException as error:
                                print('  无法清理本次启动记录，已保留原记录：'
                                      + type(error).__name__ + '：' + str(error)[:160],
                                      file=sys.stderr,flush=True)
                                if primary is not None:
                                    primary.add_note('owned endpoint retirement failed: '+type(error).__name__)
                                raise
                        host_registry.retire(registered_pid)
                        local_registration_retired = True
                finally:
                    try:
                        if output is not None:
                            if output.finish() is False:
                                error = TimeoutError('original console output still pending after 3 seconds')
                                error._console_output = output
                                if primary is not None:
                                    primary._console_output = output
                                    primary.add_note('console output drain incomplete')
                                elif channel_error is None:
                                    channel_error = error
                                else:
                                    channel_error._console_output = output
                                    channel_error.add_note('console output drain incomplete')
                            if launcher_host is not None and launcher_host.state=='retired':
                                try:launcher_host.assert_local_retired()
                                except BaseException as error:
                                    if primary is not None:
                                        primary._launcher_host=launcher_host
                                        primary.add_note('original launcher output retirement unconfirmed')
                                    elif channel_error is None:channel_error=error
                                    else:
                                        channel_error._launcher_host=launcher_host
                                        channel_error.add_note('original launcher output retirement unconfirmed')
                    finally:
                        _finish_control_shutdown(console_control, shutdown, parent_channel,
                            launcher_host, primary=primary, channel_error=channel_error,
                            unwinding=sys.exception(), cleanup_state=cleanup_state)
        except Exception as error:
            from launcher_exit_log import exit_event
            exit_event('launcher_cleanup_failed', error=error)
            error._launcher_failure_phase='shutdown'
            raise
        finally:
            # Observation only, after existing release/retirement gates. Never
            # turn a diagnostic failure into a different primary exception.
            from launcher_exit_log import exit_event
            exit_event('launcher_cleanup_finished', local_registration_retired=local_registration_retired,
                       cleanup_failed=sys.exception() is not None or any(cleanup_state.values()))
            reporter = getattr(parent_channel, 'report_watch_closeout', None)
            if callable(reporter):
                try:
                    reporter(exit_code=None if process is None else process.returncode,
                             local_retired=local_registration_retired,
                             cleanup_failed=(sys.exception() is not None
                                 or any(cleanup_state.values())))
                except Exception:
                    print('  [lifecycle] 无法保存退出诊断，本次退出还未完全确认。', flush=True)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError) as error:
        label='退出收尾失败' if getattr(error,'_launcher_failure_phase',None)=='shutdown' else '启动失败'
        print(f'{label}：{error}', file=sys.stderr)
        sys.exit(1)
