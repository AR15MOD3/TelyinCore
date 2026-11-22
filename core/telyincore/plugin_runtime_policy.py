"""Typed host lifecycle rules, not manifest authority or a Python sandbox."""
from dataclasses import dataclass
import os
from pathlib import Path

from .plugin_scope_store import identifier, plugin_name


def _name(value):
    if type(value) is not str:
        raise ValueError("runtime policy names must be strings")
    return plugin_name(value)


def _message(value):
    if (type(value) is not str or not value or len(value) > 1024
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError("invalid runtime policy message")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("runtime policy message must be UTF-8") from None


def root_key(root):
    path = Path(root)
    if not path.is_absolute():
        raise ValueError("absolute runtime policy root required")
    return os.path.normcase(str(path.resolve()))


@dataclass(frozen=True, slots=True)
class PluginDependency:
    child: str
    parent: str
    load_denied: str
    disable_denied: str

    def __post_init__(self):
        if _name(self.child) == _name(self.parent):
            raise ValueError("self dependency denied")
        _message(self.load_denied)
        _message(self.disable_denied)


@dataclass(frozen=True, slots=True)
class PluginRuntimePolicy:
    code_root: str
    settings_root: str
    bot_id: str
    scope_required: tuple[str, ...] = ()
    dependencies: tuple[PluginDependency, ...] = ()

    def __post_init__(self):
        for value in (self.code_root, self.settings_root):
            if type(value) is not str or root_key(value) != value:
                raise ValueError("canonical runtime policy root required")
        if type(self.bot_id) is not str:
            raise ValueError("stable runtime policy Bot required")
        identifier(self.bot_id)
        if type(self.scope_required) is not tuple or type(self.dependencies) is not tuple:
            raise ValueError("immutable runtime policy tuples required")
        names = tuple(_name(name) for name in self.scope_required)
        if len(set(names)) != len(names):
            raise ValueError("duplicate scoped declaration rule")
        if any(type(rule) is not PluginDependency for rule in self.dependencies):
            raise ValueError("typed runtime dependencies required")
        edges = tuple((rule.child, rule.parent) for rule in self.dependencies)
        if len(set(edges)) != len(edges):
            raise ValueError("duplicate runtime dependency")
        graph = {}
        for child, parent in edges:
            graph.setdefault(child, set()).add(parent)
        def visit(node, path):
            if node in path:
                raise ValueError("cyclic runtime dependency")
            for parent in graph.get(node, ()):
                visit(parent, path | {node})
        for node in graph:
            visit(node, set())

    @classmethod
    def bind(cls, code_root, settings_root, bot_id, *, scope_required=(), dependencies=()):
        return cls(root_key(code_root), root_key(settings_root), bot_id,
                   scope_required, dependencies)
