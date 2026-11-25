"""Start the Linux Core profile independently of optional network adapters."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import threading

CORE = Path(__file__).resolve().parents[1]
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

PLUGIN = 'core-infinity-qq'

def save_json(path, data):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
        pending = Path(stream.name)
        try:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            pending.unlink(missing_ok=True)
            raise
    try:
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)


def prepare_qq(core, root, workspace, bot, args):
    seed = root / 'plugin-scope-bootstrap.json'
    if seed.exists() and not args.setup:
        return
    from scripts.linux_acceptance import BASELINE
    from runtime_identity import safe_path
    config_path = safe_path(workspace, workspace / 'bots' / bot / 'infinity/qq/config.json')
    marker = workspace / 'linux-qq-enabled'
    from telyincore.market import install_from
    if not (root / 'extensions' / PLUGIN).exists():
        result = install_from(root, core / 'builtin/infinity' / PLUGIN)
        if not result['ok']:
            raise ValueError(result['said'])
    if not config_path.exists():
        legacy = safe_path(workspace, workspace / 'plugin-host/extensions' / PLUGIN / 'config.json')
        config_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if legacy.is_file():
            save_json(config_path, json.loads(legacy.read_bytes()))
    if not seed.exists():
        save_json(seed, {'schema': 1, 'baseline_bot': BASELINE, 'bots': {bot: {PLUGIN: False}}})
    if args.setup:
        from scripts.linux_qq import configure
        configure(core, config_path)
        save_json(marker, {'enabled': True})
        return


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--setup', action='store_true')
    parser.add_argument('--port', type=int, default=7810)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error('端口范围 1024–65535')
    from scripts.linux_acceptance import prepare_workspace
    os.umask(0o077)
    data = prepare_workspace(CORE, configure_qq=True)
    root = data.parent
    workspace = root.parent
    bot = json.loads((workspace / 'acceptance.json').read_bytes())['bot']
    try:
        prepare_qq(CORE, root, workspace, bot, args)
    except (ValueError, OSError, RuntimeError, ImportError) as error:
        if args.setup:
            raise
        print(f'[QQ] 插件准备未完成：{error}。Core 继续启动。', file=sys.stderr)
    if args.setup:
        return 0
    from scripts.linux_dev import _open_when_ready, clean_model_environment
    clean_model_environment()
    stop = threading.Event()
    if not args.no_browser:
        threading.Thread(target=_open_when_ready, args=(args.port, stop), daemon=True).start()
    try:
        from telyincore import plugin_updates
        plugin_updates.plugin_updates = plugin_updates.PluginUpdates(root)
        from launch import main as launch_main
        return launch_main(['--acceptance-isolated', '--profile-acceptance', '--linux-qq-profile',
                            '--acceptance-root', str(data), '--acceptance-bot', bot,
                            '--port', str(args.port), '--start-paused'])
    finally:
        stop.set()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError, TimeoutError) as error:
        print('Linux Core：' + str(error), file=sys.stderr)
        raise SystemExit(1)
