"""Opt-in local release rehearsal. The operator pins a catalogue on this installation."""
import hashlib
import http.client
import json
from pathlib import Path
import re


class LocalRelease:
    def __init__(self, root: Path):
        self.config = root / "runtime" / "local-release-preview.json"

    def read(self, name, limit):
        settings = json.loads(self.config.read_bytes())
        port = settings["port"]
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("本机验收源的端口无效")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            raise ValueError("验收附件名无效")
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            connection.request("GET", "/" + name, headers={"Accept-Encoding": "identity"})
            response = connection.getresponse()
            if response.status != 200:
                raise ValueError("本机验收源没有此附件")
            data = response.read(limit + 1)
            if len(data) > limit:
                raise ValueError("验收附件过大")
            if name == "catalog.json" and hashlib.sha256(data).hexdigest() != settings["sha256"]:
                raise ValueError("本机验收目录校验失败")
            return data
        finally:
            connection.close()

    def catalog(self, platform):
        from .package_updates import version_key
        row = json.loads(self.read("catalog.json", 65536))
        version_key(row["latest"])
        if row["platform"] != platform or not isinstance(row["files"], dict):
            raise ValueError("本机验收目录的平台或附件无效")
        return row

    def latest(self, repository, platform):
        row = self.catalog(platform)
        names = list(row["files"])
        return dict(latest=row["latest"], assets=names, native_assets=names, localPreview=True,
                    minimum=row.get("minimum"))

    def metadata(self, platform, version, name, maximum):
        row = self.catalog(platform)
        entry = row["files"].get(name, {})
        size, digest = entry.get("size"), entry.get("sha256")
        if (row["latest"] != version or type(size) is not int or not 0 < size <= maximum
                or not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest)):
            raise ValueError("本机验收附件的版本、长度或摘要无效")
        return size, digest
