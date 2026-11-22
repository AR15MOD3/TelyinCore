"""Configuration-only plugin host for isolated acceptance; no plugin execution authority."""
from pathlib import Path
import shutil
from urllib.parse import unquote


class AcceptancePlugins:
    def __init__(self, core, workspace):
        from . import plugins
        from infinity_control import QQControl
        from plugin_config import PluginConfig
        self.root=Path(workspace)/'plugin-host'
        folder=self.root/'extensions/core-infinity-qq'
        if not folder.exists():
            folder.parent.mkdir(parents=True,exist_ok=True)
            shutil.copytree(Path(core)/'builtin/infinity/core-infinity-qq',folder)
        config=folder/'config.json'
        example=folder/'config.example.json'
        if not config.exists() and example.is_file():shutil.copy2(example,config)
        self.read=plugins.reader(self.root)
        self.qq=QQControl(self.root,plugin_running=lambda:False,protocol_health=False)
        self.config=PluginConfig(self.root,self.read,lambda name:None)

    def read_page(self, route):
        if route=='/infinity/qq':return self.qq.read()
        prefix='/infinity/plugin-config/'
        if route.startswith(prefix):return self.config.read(unquote(route[len(prefix):]))
        raise ValueError('隔离验收未提供此扩展页面')

    def act_page(self, route, values):
        if route=='/infinity/qq/settings':return self.qq.save(values)
        if route=='/infinity/qq/routes':return self.qq.save_routes(values)
        prefix='/infinity/plugin-config/'
        if route.startswith(prefix):return self.config.save(unquote(route[len(prefix):]),values)
        raise ValueError('隔离验收仅支持配置读写；尚未接入QQ登录、进程控制或消息收发')
