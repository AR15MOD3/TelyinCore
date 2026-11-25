import pytest
from bot_instance_publisher import InstancePublisher
from bot_lifecycle_channel import LifecycleClient
from infinity_control import QQControl
from qq_host_runtime import QQPanel


def test_reopen_after_startup_lease_uses_original_verified_publisher(tmp_path, monkeypatch):
    core = tmp_path/'core'
    core.mkdir()
    scope = tmp_path/'bots/IRIS/infinity'
    client = object.__new__(LifecycleClient)
    client.closed = False
    ops = []
    def request(op, **kwargs):
        ops.append(op)
        if op == 'describe' and len(ops) == 1:
            return dict(instance_only=True, core=str(core), bot_scope=str(scope), bot='IRIS', companion_supported=True)
        if op == 'account-check':
            return {'ok': True}
        raise AssertionError('unexpected request after startup: ' + op)
    monkeypatch.setattr(client, 'request', request)
    publisher = InstancePublisher(core, scope, 'IRIS', client)
    publisher.bind(core,'IRIS')
    panel = QQPanel(lambda: QQControl(core, companion_lifecycle=publisher, bot_scope=scope))
    for _ in range(2):
        assert panel.get()._companion_lifecycle is publisher
        panel.disable()
    assert publisher.check_account_scope() == {'ok': True}
    assert ops == ['describe', 'account-check']
    with pytest.raises(ValueError, match='scope'):
        publisher.check_companion_binding(core, None)
    client.closed = True
    with pytest.raises(ValueError, match='QQ 控制通道已关闭'):
        panel.get()
