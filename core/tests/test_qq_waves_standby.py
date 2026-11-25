"""Exercise the native command route with the same operator gate as group chat."""
import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

from telyincore.qq_group_route import bind_waves

PACKAGE = Path(__file__).resolve().parents[1] / 'builtin/infinity/core-infinity-qq'
spec = importlib.util.spec_from_file_location('qq_waves_standby', PACKAGE / 'index.py')
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


def test_standby_buffers_live_commands_until_wake_and_respects_recall(tmp_path):
    config = tmp_path / 'qq.json'
    config.write_text(json.dumps({'groups': ['20001']}))
    paused = True
    receiver = Mock()
    qq = adapter.create({'bot_id': 'Fixture', 'root': str(tmp_path), 'packageDir': str(PACKAGE),
                         'qq_config_path': str(config), 'waves_commands_allowed': lambda: not paused})
    qq.groups = {'20001'}
    qq._unbind_waves_transport = lambda: None
    qq.connected = qq._started = True
    unbind = bind_waves('Fixture', receiver)

    def message(mid):
        return json.dumps({'post_type': 'message', 'message_type': 'group', 'self_id': 10001,
                           'group_id': 20001, 'user_id': 10002, 'message_id': mid,
                           'message': [{'type': 'text', 'data': {'text': 'ww帮助'}}]})

    try:
        for mid in (1, 2, 1, 3):
            assert qq.accept(message(mid))
        assert qq.accept(message(4), from_history=True)
        assert qq.accept(json.dumps({'post_type': 'notice', 'notice_type': 'group_recall',
                                     'group_id': 20001, 'self_id': 10001, 'message_id': 2}))
        qq._resume_waves()
        receiver.assert_not_called()
        try:
            qq._deliver_waves('10001', '20001', [], '')
        except RuntimeError as exc:
            assert '静候' in str(exc)
        else:
            raise AssertionError('unsolicited QQ send admitted while paused')
        paused = False
        qq._resume_waves()
        assert qq.accept(message(5))
        assert [c.args[0]['message_id'] for c in receiver.call_args_list] == [1, 3, 5]
        paused = True
        assert qq.accept(message(6))
        unbind()
        replacement = Mock()
        unbind = bind_waves('Fixture', replacement)
        paused = False
        qq._resume_waves()
        replacement.assert_not_called()
        assert not qq._pending_waves
    finally:
        unbind()


def test_wake_drains_on_the_live_receive_loop_without_a_second_qq_message(tmp_path, monkeypatch):
    import asyncio
    from contextlib import asynccontextmanager

    async def run():
        inbox = asyncio.Queue()
        delivered = asyncio.Event()
        paused = True
        config = tmp_path / 'qq.json'
        config.write_text(json.dumps({'groups': ['20001']}))
        qq = adapter.create({'bot_id': 'LoopFixture', 'root': str(tmp_path), 'packageDir': str(PACKAGE),
                             'qq_config_path': str(config), 'waves_commands_allowed': lambda: not paused})
        qq.groups = {'20001'}
        qq._unbind_waves_transport = lambda: None

        @asynccontextmanager
        async def connect(*args, **kwargs):
            yield type('Socket', (), {'recv': lambda self: inbox.get()})()

        monkeypatch.setattr(adapter, 'connect', connect)
        receiver = Mock(side_effect=lambda *args: delivered.set())
        unbind = bind_waves('LoopFixture', receiver)
        task = asyncio.create_task(qq._receive())
        try:
            await inbox.put(json.dumps({'post_type': 'message', 'message_type': 'group',
                'self_id': 10001, 'group_id': 20001, 'user_id': 10002, 'message_id': 6,
                'message': [{'type': 'text', 'data': {'text': 'ww练度群排行'}}]}))
            await asyncio.sleep(0)
            assert qq._pending_waves
            receiver.assert_not_called()
            paused = False
            await asyncio.wait_for(delivered.wait(), 2)
            receiver.assert_called_once()
        finally:
            unbind()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
