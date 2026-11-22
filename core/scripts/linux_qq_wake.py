"""A source checkout consumes the configured OneBot service."""
def intent(core):
    return False


def wake(core, owner_fd, *, selected=None):
    return "请在 QQ 插件中连接已配置的 OneBot 服务。"
