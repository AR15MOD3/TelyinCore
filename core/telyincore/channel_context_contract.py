'Channel context declaration contract used by the opt-in production host.\n\nlaunch builds ChannelContextHost when configured; successfully loaded providers\nregister separately from display markers. This registry supplies presentation\ncontext, never identity, history, tool or outbound authorization. Only trusted\nenvelopes are eligible; configured does not mean every runtime enables it.\n'
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
from types import MappingProxyType
from typing import Mapping


MAX_BYTES = 16 * 1024
_SOURCE = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z")
_RESERVED = frozenset({"console", "internal", "mixed", "unknown"})
_BUILTINS = MappingProxyType({
    "console": "本批消息来自本机终端；这说明入口，不单凭此证明操作者身份或物理在场。",
    "internal": "本次是内部唤醒，不代表有人刚到场或正在等待回复。",
    "mixed": "本批存在多个外部来源；逐条保留来源，不将不同场合、身份或接收目标混同。",
    "unknown": "当前外部来源尚无通道环境说明；不从通道名称或消息正文推断在场方式与身份。",
})


class ContextError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ContextDeclaration:
    source: str
    relative_path: str


@dataclass(frozen=True)
class ContextSelection:
    status: str
    source: str
    provider: str
    text: str


def declarations(manifest) -> tuple[ContextDeclaration, ...]:
    """Old display-only manifests remain valid; partial new declarations fail."""
    if not isinstance(manifest, Mapping):
        raise ContextError("invalid_manifest")
    box = manifest.get("telyincore", {})
    if not isinstance(box, Mapping):
        raise ContextError("invalid_manifest")
    entries = box.get("channels", [])
    if not isinstance(entries, list):
        raise ContextError("invalid_channels")
    result = []
    sources = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ContextError("invalid_channel_entry")
        if "context" not in entry and "source" not in entry:
            continue
        source, path = entry.get("source"), entry.get("context")
        if not isinstance(source, str) or not _SOURCE.fullmatch(source):
            raise ContextError("invalid_source")
        if source in _RESERVED:
            raise ContextError("reserved_source")
        if source in sources:
            raise ContextError("duplicate_source")
        if not isinstance(path, str) or not path or "\\" in path or "\x00" in path:
            raise ContextError("invalid_context_path")
        parts = PurePosixPath(path)
        if (parts.is_absolute() or PureWindowsPath(path).drive
                or any(p in ("", ".", "..") for p in path.split("/"))
                or parts.suffix.lower() != ".md"):
            raise ContextError("invalid_context_path")
        result.append(ContextDeclaration(source, path))
        sources.add(source)
    return tuple(result)


class ContextRegistry:
    """Per-host registry. Membership is not evidence of message provenance.

    The later ingress adapter must bind a message to its actual loaded provider
    BEFORE calling this resolver. A source value in user text is never enough.
    No plugin code is imported or executed by this module.
    """

    def __init__(self):
        self._providers = {}

    def register(self, provider, plugin_root, manifest, *, enabled, loaded):
        if not enabled or not loaded:
            return
        if not isinstance(provider, str) or not provider:
            raise ContextError("invalid_provider")
        parsed = declarations(manifest)
        if any(d.source in self._providers for d in parsed):
            raise ContextError("source_conflict")
        try:
            root = Path(plugin_root).resolve(strict=True)
        except (OSError, RuntimeError):
            raise ContextError("plugin_root_missing") from None
        if not root.is_dir():
            raise ContextError("plugin_root_missing")
        # Registration is transactional: do not partially admit a bad plugin.
        staged = {}
        for declaration in parsed:
            text = self._read(root, declaration.relative_path)
            staged[declaration.source] = (provider, text)
        self._providers.update(staged)

    @staticmethod
    def _read(root, relative_path):
        try:
            path = (root / relative_path).resolve(strict=True)
            if not path.is_relative_to(root):
                raise ContextError("context_path_escape")
            with path.open("rb") as stream:
                raw = stream.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ContextError("context_too_large")
            text = raw.decode("utf-8-sig")
        except (OSError, UnicodeError, RuntimeError):
            raise ContextError("context_unreadable") from None
        text = re.sub(r"<!--.*?-->", "", text, flags=re.S).strip()
        if not text:
            raise ContextError("context_empty")
        return text

    def unregister(self, provider):
        self._providers = {key: value for key, value in self._providers.items()
                           if value[0] != provider}

    def select(self, trusted_batch) -> ContextSelection:
        external = [item for item in trusted_batch
                    if getattr(item, "origin", None) == "external"]
        if not external:
            return ContextSelection("builtin", "internal", "host", _BUILTINS["internal"])
        given_sources = [getattr(item, "source", None) for item in external]
        if any(not isinstance(source, str) or not source for source in given_sources):
            raise ContextError("invalid_envelope_source")
        sources = set(given_sources)
        if sources == {"console"}:
            return ContextSelection("builtin", "console", "host", _BUILTINS["console"])
        if len(sources) > 1:
            return ContextSelection("mixed", "mixed", "host", _BUILTINS["mixed"])
        source = next(iter(sources))
        if source not in self._providers:
            return ContextSelection("unconfigured", source, "host", _BUILTINS["unknown"])
        provider, text = self._providers[source]
        return ContextSelection("plugin", source, provider, text)
