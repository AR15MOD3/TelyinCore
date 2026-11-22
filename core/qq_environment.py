"""Read-only QQ plugin declaration for the local settings UI; never a live-context claim."""
from pathlib import Path
import json
import re

LIMIT = 16 * 1024


def read_qq_environment(root):
    result = {"status": "unconfigured", "text": "", "readOnly": True,
              "source": "已安装 QQ 插件 · channel/qq.md",
              "message": "插件尚未声明环境文本；不会回退读取 Core 或其他插件。"}
    try:
        root = Path(root).resolve(strict=True)
        plugin = root / "extensions" / "core-infinity-qq"
        manifest = plugin / "package.json"
        if not manifest.exists():
            return result
        if plugin.resolve(strict=True) != plugin or manifest.resolve(strict=True) != manifest:
            raise ValueError("redirected plugin")
        with manifest.open("rb") as stream:
            raw = stream.read(64 * 1024 + 1)
        if len(raw) > 64 * 1024:
            raise ValueError("manifest too large")
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("name") != "core-infinity-qq":
            raise ValueError("wrong plugin")
        contract = data.get("telyincore", {})
        if not isinstance(contract, dict):
            raise ValueError("invalid contract")
        channels = contract.get("channels", [])
        if not isinstance(channels, list) or any(not isinstance(c, dict) for c in channels):
            raise ValueError("invalid channels")
        declared = [c for c in channels if c.get("source") == "qq"]
        if not declared:
            return result
        if len(declared) != 1 or declared[0].get("context") != "channel/qq.md":
            raise ValueError("invalid QQ resource")
        path = plugin / "channel" / "qq.md"
        if path.resolve(strict=True) != path:
            raise ValueError("redirected resource")
        with path.open("rb") as stream:
            raw = stream.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise ValueError("resource too large")
        text = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"<!--.*?-->", "", text, flags=re.S).strip()
        if not text:
            raise ValueError("empty declaration")
        result.update(status="available", text=text,
                      message="只读显示磁盘声明，不代表当前进程已加载；更新后需重新加载插件。")
    except (OSError, ValueError, TypeError, UnicodeError):
        result.update(status="unavailable", text="",
                      message="环境声明不可读取或不符合约束；未使用替代内容。")
    return result
