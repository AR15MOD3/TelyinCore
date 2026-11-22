# telyincore/plugins.py
'插件声明、启停与同实例责任管理。\n\n目录清单、enabled 配置意图和 loaded 实例状态分别报告；启用不等于加载完成。\nstart/gathered/stop 与实际宿主装配相接，API 版本及关键字由常量和契约检查决定，\n不能再以早期“尚无加载器、enabled 恒 False”的注释判断当前状态。\n坏声明单独留痕；失败或未退出的异步工作仍属于原实例，不能仅凭 done 释放。\n'

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from threading import Event, RLock, Thread, local
from typing import Any

#: **2026-10-10：一个读口不许有“倒全库”这种缺省** ——
#: 它是那天卡死的根因 （ 实测：一次空手调会倒出 **209 条 / 14.5 万字** ）。
#: 给它一个底 ； 而调用方**显式**给了 `limit` 就照它给 。
READ_EVENTS_LIMIT = 50

# 插件安装目录是部署状态，不纳入源码版本控制。
PLUGINS_DIR = "extensions"
#: **内置那一批住在哪儿**（2026-10-05 作者："下载这个项目内置的插件"）——
#: ⚠️ 与 `extensions/` **分家**，而这是承重的：`extensions/` 是**部署状态**（不进 git ），
#: 而内置那一批是**随这个项目一起发的** ⇒ 它必须在**进 git** 的目录里 。
BUILTIN_DIR = "builtin"

#: 那一份声明（`package.json`）的名字。
MANIFEST = "package.json"

# 启用开关随插件目录保存在 state.json 中，属于部署状态。
STATE = "state.json"

# 插件入口由 package.json 的 main 指定，默认 index.py；元数据使用 telyincore 字段。
# 入口 create(ctx) 返回实例，生命周期调用 start/stop。
# 仅加载启用的插件；单个插件失败应报告原因，不影响其他插件。
# loaded 表示当前持有实例，健康状态及清理责任还要看 lifecycle。

# package.json 未声明 main 时使用的入口文件。
ENTRY = "index.py"

# 入口模块提供的实例工厂函数名。
MAKE = "create"

#: **已经装起来的那几个**（`{包名: 交回来的那一份}`）—— 进程级，一处机器看得见的地方 。
#: ⚠️ **它只在开着的时候有**（关掉 ⇒ 从这里拿掉 ＋ 叫一声 `stop()` ）。
_loaded: dict[str, Any] = {}
_channel_leases: dict[str, tuple[Any, Any]] = {}
_package_leases: dict[tuple[str, str], Any] = {}

# One process-wide transaction protects check/create/start/register/stop AND the
# state.json read-modify-write. Reentrant hook calls are rejected by phase below.
# Read-only status does not take this lock: stop() may join a status-reading worker.
_lifecycle_lock = RLock()
_lifecycle_confined = Event()  # process-lifetime denial; no reset/allow API
_lifecycle_states: dict[str, tuple[Any, str, str]] = {}
_lifecycle_threads: dict[str, tuple[Any, tuple[Thread, ...]]] = {}
# Provenance belongs to an exact live object, never a name inferred from disk.
_lifecycle_roots: dict[str, tuple[Any, str]] = {}
_closing_roots: set[str] = set()  # sticky for this process; no reopen API
# Per-thread dynamic drain pass; reentrant public stops share its budget.
# Strong exact-instance rows expire on outer return, including exceptions.
_root_drain_context = local()


# Exact-instance setting provenance, separate from lifecycle root ownership.
# This does NOT grant authority or alter root fences/closing semantics.
# Third cell pins host rules to the exact instance, including failed drain.
_instance_setting_scopes: dict[str, tuple[Any, Any, Any]] = {}


def _setting_scope_key(scope):
    if scope is None:
        return None
    from .plugin_scope_store import PluginScopeStore
    if type(scope) is not PluginScopeStore:
        raise ValueError('explicit PluginScopeStore required')
    return (_root_key(scope.bots), scope.bot)


def _same_setting_scope(name, instance, scope):
    row = _instance_setting_scopes.get(name)
    actual = row[1] if row is not None and row[0] is instance else None
    return actual == _setting_scope_key(scope)


def _same_runtime_policy(name, instance, policy):
    row = _instance_setting_scopes.get(name)
    return (row is not None and row[0] is instance and len(row) == 3 and row[2] == policy)


def _retained_dependents(name, instance):
    row = _instance_setting_scopes.get(name)
    if row is None or row[0] is not instance or len(row) != 3 or row[2] is None:
        return ()
    owner = _owned_root(name, instance)
    if owner is None:
        return ()
    matches = []
    for rule in row[2].dependencies:
        child = _loaded.get(rule.child)
        child_row = _instance_setting_scopes.get(rule.child)
        if (rule.parent == name and rule.child in _loaded and child_row is not None
                and child_row[0] is child and child_row[1] == row[1]
                and _owned_root(rule.child, child) == owner):
            matches.append((rule, child))
    return tuple(matches)


@contextmanager
def _root_drain_pass():
    nested = getattr(_root_drain_context, "attempts", None) is not None
    if not nested:
        _root_drain_context.attempts = []
    try:
        yield
    finally:
        if not nested:
            del _root_drain_context.attempts


def _root_stop_order(rows):
    # Dependencies first, stop each captured generation at most once per invocation.
    captured = dict(rows)
    seen, ordered = set(), []
    def visit(name):
        if name in seen:
            return
        seen.add(name)
        instance = captured[name]
        for rule, child in _retained_dependents(name, instance):
            if rule.child in captured and captured[rule.child] is child:
                visit(rule.child)
        ordered.append((name, instance))
    for name in captured:
        visit(name)
    return ordered


def _scope_runtime_policy(root_key, scope):
    if scope is None:
        return None  # Explicit unscoped legacy API; never a scoped fallback.
    from .plugin_runtime_policy import PluginRuntimePolicy
    key = _setting_scope_key(scope)
    policy = scope.runtime_policy
    if (type(policy) is not PluginRuntimePolicy or policy.code_root != root_key
            or (policy.settings_root, policy.bot_id) != key):
        raise ValueError("explicit same-root/Bot runtime policy required for scoped lifecycle")
    for name, instance in tuple(_loaded.items()):
        if (_same_setting_scope(name, instance, scope)
                and not _same_runtime_policy(name, instance, policy)):
            raise ValueError("live plugin runtime policy changed; drain original generation first")
    return policy


def _scope_switches(root, scope):
    _setting_scope_key(scope)
    return scope.read_state()['enabled'] if scope is not None else _the_switch(the_place(root))


def _save_scope_switch(root, scope, name, on):
    _setting_scope_key(scope)
    if scope is None:
        _set_the_switch(the_place(root), name, on)
    elif on is None:
        scope.forget(name)
    else:
        scope.update({name: bool(on)})


def _scope_context(host, scope, name):
    if scope is None:
        return host
    _setting_scope_key(scope)
    if not isinstance(host, dict) or host.get('bot_id') != scope.bot:
        raise ValueError('plugin settings scope does not match host Bot identity')
    return dict(host, pluginConfigPath=str(scope.config_path(name)),
                pluginDataDir=str(scope.root / name / 'runtime'),
                pluginSettingsBot=scope.bot)


def _root_key(root: Any) -> str:
    return os.path.normcase(str(Path(root).resolve()))


def _require_open_root(root: Any) -> str:
    key = _root_key(root)
    if key in _closing_roots:
        raise ValueError('plugin root is closing; new lifecycle work denied')
    return key


def _owned_root(name: str, instance: Any) -> str | None:
    row = _lifecycle_roots.get(name)
    return row[1] if row is not None and row[0] is instance else None



def confine_lifecycle() -> None:
    """Irreversible denial for this cooperative Python loader, not a sandbox.

    Never adopts/stops/forgets existing instances. Refuse mixed hosts, including
    starting/failed rows. Empty loader state is NOT evidence of an empty OS host.
    Trusted same-process Python can bypass this module; no security isolation or
    production completeness is claimed. Participating built-ins consult this latch;
    other direct create() entrypoints remain unmediated.
    """
    if not _lifecycle_lock.acquire(blocking=False):
        raise ValueError('experimental plugin loader busy')
    try:
        if _loaded or _channel_leases or _lifecycle_threads or _lifecycle_states:
            raise ValueError('experimental lifecycle requires a fresh plugin loader')
        _lifecycle_confined.set()
    finally:
        _lifecycle_lock.release()


def _require_unconfined_lifecycle() -> None:
    if _lifecycle_confined.is_set():
        raise ValueError('experimental lifecycle forbids unmediated plugin lifecycle')


def lifecycle_transaction() -> Any:
    """Share lifecycle serialization with the host's tool-table publication."""
    return _lifecycle_lock


@contextmanager
def preserve_enabled_intent(root: Any, name: str, *, scope=None) -> Any:
    """A host close/open reload must not change the user's persistent intent."""
    with _lifecycle_lock:
        name = _plugin_name(root, name)
        place = the_place(root)
        original = _scope_switches(root, scope).get(name)
        primary = None
        try:
            yield
        except Exception as exc:
            primary = exc
            raise
        finally:
            try:
                if _scope_switches(root, scope).get(name) != original:
                    _save_scope_switch(root, scope, name, original)
            except Exception as exc:
                if primary is None:
                    raise
                primary.add_note(f"恢复插件启用意图失败：{type(exc).__name__}: {exc}")


_async_calls_lock = RLock()  # Never held over user code, await, I/O or lifecycle calls.
_async_calls: dict[object, Any] = {}


def _has_async_calls(instance: Any) -> bool:
    with _async_calls_lock:
        return any(owner is instance for owner in _async_calls.values())


def _guard_plugin_handler(name: str, instance: Any, handler: Any) -> Any:
    import asyncio
    import inspect
    from .async_tools import ManagedAwaitable

    owned = getattr(handler, '__owned_async_handler__', None)
    if owned is not None and (owned is handler
            or getattr(owned, '__owned_async_handler__', None) is not None
            or not (inspect.iscoroutinefunction(owned) or inspect.iscoroutinefunction(
                getattr(owned, '__call__', None)))):
        raise TypeError('owned async registration must be a distinct native async entry')

    def require_current():
        if (_loaded.get(name) is not instance
                or _owned_root(name, instance) in _closing_roots
                or _lifecycle_state(name)[0] != "running"):
            raise RuntimeError(f"「{name}」原实例已不可用，拒绝执行旧工具")

    async def before_await():
        # Another thread may hold the lifecycle lock during a long synchronous
        # start/stop. Never block the event-loop thread waiting for that lock.
        while not _lifecycle_lock.acquire(blocking=False):
            await asyncio.sleep(.01)
        try:
            require_current()
        finally:
            _lifecycle_lock.release()

    def invoke(args, kwargs):
        require_current()
        result = handler(*args, **kwargs)
        if not inspect.isawaitable(result):
            return result
        ticket = object()
        with _async_calls_lock:
            _async_calls[ticket] = instance

        def release():
            with _async_calls_lock:
                _async_calls.pop(ticket, None)

        return ManagedAwaitable(result, before_await, release)

    @wraps(handler)
    def run(*args: Any, **kwargs: Any) -> Any:
        # Legacy synchronous handlers retain their contract until individually
        # migrated. A returned awaitable reserves a lease before leaving the lock.
        with _lifecycle_lock:
            return invoke(args, kwargs)

    if (inspect.iscoroutinefunction(handler)
            or inspect.iscoroutinefunction(getattr(handler, "__call__", None))):
        @wraps(handler)
        async def run(*args: Any, **kwargs: Any) -> Any:
            while not _lifecycle_lock.acquire(blocking=False):
                await asyncio.sleep(.01)
            try:
                result = invoke(args, kwargs)
            finally:
                _lifecycle_lock.release()
            return await result

    try:
        run.__signature__ = inspect.signature(handler, follow_wrapped=False)
    except (TypeError, ValueError):
        pass  # Unknown call shape keeps conservative handler/error semantics.
    owned = getattr(handler, '__owned_async_handler__', None)
    if callable(owned):
        run.__owned_async_handler__ = _guard_plugin_handler(name, instance, owned)
    return run


def _serialized_lifecycle(fn: Any) -> Any:
    @wraps(fn)
    def act(*args: Any, **kwargs: Any) -> Any:
        with _lifecycle_lock:
            return fn(*args, **kwargs)
    return act


def _lifecycle_state(name: str) -> tuple[str, str]:
    missing = object()
    instance = _loaded.get(name, missing)
    saved = _lifecycle_states.get(name)
    if saved is not None and instance is not missing and saved[0] is instance:
        return saved[1], saved[2]
    return ("running", "") if instance is not missing else ("unloaded", "")


def _plugin_folder(root: Any, name: str) -> tuple[Path | None, dict[str, Any] | None]:
    place = the_place(root)
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None, None
    direct = place / name
    if direct.is_dir() and direct.resolve().parent == place.resolve():
        return direct, _read_manifest(direct / MANIFEST)
    for candidate in sorted(place.iterdir() if place.is_dir() else [], key=lambda p: p.name):
        if not candidate.is_dir() or candidate.resolve().parent != place.resolve():
            continue
        manifest = _read_manifest(candidate / MANIFEST)
        if manifest is not None and manifest.get("name") == name:
            return candidate, manifest
    return None, None


def _plugin_name(root: Any, name: str) -> str:
    _, manifest = _plugin_folder(root, name)
    named = manifest.get("name") if manifest is not None else None
    return named if isinstance(named, str) and named else name


def _owned_threads(instance: Any) -> tuple[Any, ...]:
    # Historical name: retain threads AND future handles before stop can clear
    # their containers. No properties, arbitrary iterators or object graphs run.
    import asyncio
    from concurrent.futures import Future
    stack = [getattr(instance, '__dict__', {})]
    seen, found = set(), []
    while stack:
        value = stack.pop()
        if id(value) in seen:
            continue
        seen.add(id(value))
        if len(seen) > 4096:
            raise RuntimeError('plugin background inventory exceeds safe bound')
        if isinstance(value, (Thread, asyncio.Future, Future)):
            found.append(value)
        elif type(value) is dict:
            stack.extend(tuple(value.values()))
        elif type(value) in (list, tuple, set, frozenset):
            stack.extend(tuple(value))
    return tuple(found)


class _LifecycleReturn:
    def __init__(self, phase, value):
        self.phase, self.value = phase, value


def _reject_lifecycle_return(name, instance, result, phase):
    import inspect
    if not inspect.isawaitable(result):
        return
    if inspect.iscoroutine(result):
        result.close()  # cold coroutine was never executed
    else:
        saved = _lifecycle_threads.get(name)
        previous = saved[1] if saved is not None and saved[0] is instance else ()
        marker = _LifecycleReturn(phase, result)
        _lifecycle_threads[name] = (instance, (*previous, marker))
        callback = getattr(result, 'add_done_callback', None)
        if callable(callback):
            def observed(done):
                try:done.result()
                except BaseException:pass # retained exact Future still carries the error
            callback(observed)
    raise RuntimeError(f'{phase}() 返回异步任务，尚未完成')


def _assert_background_retired(resources, *, name=None, instance=None):
    import asyncio
    for work in resources:
        returned=isinstance(work, _LifecycleReturn)
        if returned:
            work = work.value
        if isinstance(work, Thread):
            if work.is_alive():
                raise RuntimeError('stop() 未完成：插件线程仍然存活')
        else:
            if isinstance(work, asyncio.Future) and not isinstance(work, asyncio.Task):
                raise RuntimeError('plain asyncio Future cannot prove background retirement')
            if not callable(getattr(work, 'done', None)) or not work.done():
                raise RuntimeError('plugin background work still pending')
            if not returned and isinstance(work,asyncio.Task) and work.cancelled():
                from .plugin_ingress import native_cancellation_retired
                if native_cancellation_retired(work,name,instance):continue
            # Failed/cancelled work remains an explicit responsibility. Reading
            # the result never replays work or changes its completion state.
            work.result()


def _stop_instance(name: str, instance: Any) -> None:
    if _has_async_calls(instance):
        raise RuntimeError("plugin async tool work has not actually drained")
    binding = _channel_leases.get(name)
    if binding is not None and binding[0] is instance:
        binding[1].retire()
    threads = _owned_threads(instance)
    saved = _lifecycle_threads.get(name)
    returned_stop = False
    if saved is not None and saved[0] is instance:
        returned = tuple(work for work in saved[1] if isinstance(work, _LifecycleReturn))
        # A returned live start/stop is already accepted work. Observe its exact
        # terminal before another hook, and never execute a stop hook twice.
        _assert_background_retired(returned)
        returned_stop = any(work.phase == 'stop' for work in returned)
        threads = (*saved[1], *threads)
    threads = tuple(dict.fromkeys(threads))
    _lifecycle_threads[name] = (instance, threads)
    stop = getattr(instance, "stop", None)
    if callable(stop) and not returned_stop:
        try:
            result = stop()
        finally:
            threads = tuple(dict.fromkeys((*threads, *_owned_threads(instance))))
            _lifecycle_threads[name] = (instance, threads)
        if result is False or (isinstance(result, dict) and result.get("ok") is False):
            raise RuntimeError(f"stop() 未完成：{result}")
        # Async hooks are not completed by calling a synchronous lifecycle API.
        _reject_lifecycle_return(name, instance, result, 'stop')
    _assert_background_retired((*threads, *_owned_threads(instance)),name=name,instance=instance)
    from .plugin_ingress import retire_native_task_relations
    retire_native_task_relations(name,instance)
    _lifecycle_threads.pop(name, None)
    if _channel_leases.get(name) is binding:
        _channel_leases.pop(name, None)


def _active_items() -> tuple[tuple[str, Any], ...]:
    return tuple((name, instance) for name, instance in tuple(_loaded.items())
                 if _lifecycle_state(name)[0] == "running")

# 插件市场验证的发现关键字；搜索来源与包内关键字校验分别处理。
KEYWORD = "core-infinity"

# 各类插件的契约版本。不兼容接口变更递增对应版本。
# 插件版本小于框架版本时提示升级插件，大于框架版本时提示升级框架；版本须为正整数。
CONTRACTS: dict[str, int] = {"infinity": 1}


def _the_channels(given: Any) -> list[dict[str, Any]]:
    """ **插件交上来的那一份"外部渠道词表"** —— 收下能用的那几条，坏的丢掉。

    形状（`package.json` 的 `telyincore.channels`）：

    ```
    [{"label": "那一侧",
      "markers": [{"scope": "群", "pattern": "插件提供的标记正则"}]}]
    ```

    ⚠️ **为什么这份东西在插件里、而不是核心里**（2026-10-17 作者定的）：
      正文里那个渠道标记**是插件自己写进去的** ⇒
      怎么认它、认出来显示成什么词（`群` ／ `私` ／ 以后别人的 `直` ／ `微`），
      只有那个插件知道。核心只负责**按这份词表渲染**，一个外部渠道名都不认识。
    ⚠️ **形状不对就丢那一条**：这份数据来自插件（外来），
      一条坏的不许把整页带走 —— 与 `gathered` 那条"逐个插件地试"同一个方向。
    """
    if not isinstance(given, list):
        return []
    out: list[dict[str, Any]] = []
    for one in given:
        if not isinstance(one, dict):
            continue
        label = one.get("label")
        markers = one.get("markers")
        if not isinstance(label, str) or not label.strip() or not isinstance(markers, list):
            continue
        kept: list[dict[str, str]] = []
        for marker in markers:
            if not isinstance(marker, dict):
                continue
            scope = marker.get("scope")
            pattern = marker.get("pattern")
            if not isinstance(scope, str) or not scope.strip():
                continue
            if not isinstance(pattern, str) or not pattern.strip():
                continue
            one_marker = {"scope": scope, "pattern": pattern}
            # `filter="answered"` ⇒ 这一类来话**只收它参与过的**（经历那一页用）。
            # ⚠️ 别的值一律当"没声明"（不认它的过滤器 —— 那是**插件的**词，核心不认识）。
            if marker.get("filter") == "answered":
                one_marker["filter"] = "answered"
            kept.append(one_marker)
        if kept:
            word = one.get("speech")
            entry: dict[str, Any] = {"label": label, "markers": kept}
            routes = one.get("routes")
            if isinstance(routes, list):
                entry["routes"] = [{"scope": r["scope"], "pattern": r["pattern"]}
                                   for r in routes if isinstance(r, dict)
                                   and isinstance(r.get("scope"), str)
                                   and isinstance(r.get("pattern"), str)
                                   and len(r["pattern"]) <= 1000]
            # 它"发言"用哪几件工具、正文在哪个字段 —— 经历那一页用它把
            # "只留在工具调用里的那几句"补进轴上（`send_private` 那种）。
            if isinstance(word, dict):
                tools = word.get("tools")
                key = word.get("text")
                named = ([str(x) for x in tools if isinstance(x, str) and x]
                         if isinstance(tools, list) else [])
                if named and isinstance(key, str) and key:
                    entry["speech"] = {"tools": named, "text": key}
                    receipt = word.get("receipt")
                    if isinstance(receipt,str) and 0 < len(receipt) <= 1024:
                        entry["speech"]["receipt"] = receipt
                    destination = word.get("destination")
                    if isinstance(destination, str) and 0 < len(destination) <= 512:
                        entry["speech"]["destination"] = destination
            out.append(entry)
    return out


def _contract_said(kind: str, api: Any) -> str:
    """返回插件契约的诊断信息；本函数只报告，不执行装卸。

空字符串可能表示兼容或无法判断，调用方还须结合元数据状态。"""
    if not isinstance(api, int) or api < 1:
        return "它没写契约号（或者写的不是正整数）—— 那一格今天没得判。"
    want = CONTRACTS.get(kind)
    if want is None:
        return f"我们不认识「{kind}」这一类（今天只有 {sorted(CONTRACTS)}）—— 没得判。"
    if api < want:
        return (f"它按契约 v{api} 写，我们这边是 v{want}：**这个东西需要升级**。")
    if api > want:
        return (f"它要求契约 v{api}，我们这边只到 v{want}：**核心需要升级**。")
    return ""


#: 装进来的那些"从哪儿来"（与那个开关分开存 —— 它是**来源**，不是状态 ）。
ORIGINS = ".origins.json"


def _the_switch(place: Path) -> dict[str, bool]:
    """ **那个开关的状态**（`extensions/state.json` ⇒ `{包名: 开没开}` ）。

    ⚠️ **读不到 ⇒ 空那一张**（不抛 ）："还没开过任何一个"是一个答案 ——
       与 `installed()` 那条（"还没装过"是一个答案）同一条口径 。
    ⚠️ **它只认布尔**：值是别的东西 ⇒ **当没开**（不解释、也不猜 ）。
    """
    got = _read_manifest(place / STATE)
    if got is None:
        return {}
    return {str(k): v for k, v in got.items() if isinstance(v, bool)}


def _set_the_switch(place: Path, name: str, on: bool | None) -> None:
    """ **把那个开关写下去**（ 原子：先写临时那一个、再 `os.replace`
    —— 照 `console._save_usage_totals` 那一条"写一半就断电"的规矩 ）。

    ⚠️ **它只碰 state.json 一个文件**：插件那些目录**一个字节都不动**
       （那是**别人的东西**，这一层没资格改 ）。

     **`on=None` ＝ 把那一格忘掉**（2026-10-05 加）—— 删掉一个插件之后，
       那一格名字**不该留在这一份里** （那会是一条**指向不存在的东西**的记录 ，
       而本仓不许"接不上的东西静默留着"）。
    """
    import os
    import tempfile

    place.mkdir(parents=True, exist_ok=True)
    state = _the_switch(place)
    if on is None:
        state.pop(name, None)
    else:
        state[name] = on
    target = place / STATE
    handle, tmp = tempfile.mkstemp(dir=str(place), prefix=".state-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as out:
            json.dump(state, out, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def the_place(root: Any) -> Path:
    """ **插件装在哪儿** —— `<root>/extensions/`（`root` 是"当下那一层"，与会话那些同一条）。"""
    return Path(root) / PLUGINS_DIR


def the_builtin(root: Any) -> Path:
    """ **内置那一批住在哪儿** —— `<root>/builtin/`（ 随这个项目发的，不是部署状态 ）。"""
    return Path(root) / BUILTIN_DIR


def _the_origins(place: Path) -> dict[str, dict[str, str]]:
    """ **装进来的那些是从哪儿来的**（`extensions/.origins.json` ）。

    ⚠️ **为什么要有这一份**（2026-10-05 作者："作者不应该显示 github 的用户名吗？为什么是没写"）：
       · **市场那一段**的作者，是从 GitHub 那一条**现查来的**（仓库的 owner ）；
       · 而**已安装那一段**原先只读插件自己的 `package.json` —— 他那一份**没写 `author`**
         ⇒ 于是显示"（没写）" 。
    ⇒ 所以装的那一刻**把来源记下来**（哪条链接、谁写的） —— 那是**我们真看到过的东西**，
       不是猜的 （ 而且卸载时**顺手把这一格去掉** ）。
    """
    got = _read_manifest(place / ORIGINS)
    if got is None:
        return {}
    return {str(k): v for k, v in got.items() if isinstance(v, dict)}


def _set_an_origin(place: Path, name: str, box: dict[str, str] | None) -> None:
    """ 记一格来源（原子：先写临时那一个、再 `os.replace` —— 与开关同一条规矩 ）。

    `box=None` ⇒ **把那一格去掉**（卸载那一条走这里 ）。
    """
    import os
    import tempfile

    place.mkdir(parents=True, exist_ok=True)
    state = _the_origins(place)
    if box is None:
        state.pop(name, None)
    else:
        state[name] = {str(k): str(v) for k, v in box.items()}
    target = place / ORIGINS
    handle, tmp = tempfile.mkstemp(dir=str(place), prefix=".origins-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as out:
            json.dump(state, out, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def note_origin(root: Any, name: str, *, source: str = "", url: str = "",
                by: str = "") -> None:
    """ **入口在装完之后叫这一句**（把"从哪儿来"记下来 ）。"""
    _set_an_origin(the_place(root), str(name), {"source": source, "url": url, "by": by})


def _the_entry(one: Path, manifest: dict[str, Any]) -> Path:
    """解析 package.json 的 main，缺省 index.py；仅允许包内相对路径，拒绝越界入口。"""
    said = manifest.get("main")
    name = str(said) if isinstance(said, str) and said.strip() else ENTRY
    target = (one / name).resolve()
    if not str(target).startswith(str(one.resolve()) + os.sep) and target != one.resolve():
        raise ValueError(f"入口写出了包外面：{name}")
    return target


def _a_place_to_stand(root: Any, name: str, one: Path, host: Any = None) -> dict[str, Any]:
    """提供给 create(ctx) 的路径与宿主能力。

只暴露已接入的能力；缺少 host 时仅提供路径信息，不创建空壳接口。"""
    box: dict[str, Any] = {
        "name": name,
        "packageDir": str(one),
        "root": str(Path(root)),
        # botDir 与 dataDir 均指向当前实例的 root。
        "botDir": str(Path(root)),
        "dataDir": str(Path(root)),
    }
    if host is not None:
        box.update(host)
        if name != "core-infinity-qq":
            box.pop("owner_qq_route", None)
        box.pop("_channel_context_host", None)
        ingress=box.pop('_native_task_ingress',None)
        if ingress is not None:
            def start_native(thread,*,stop):
                original=_loaded.get(name)
                if original is None:raise RuntimeError('native plugin birth has no exact loader owner')
                return ingress.start_background_thread(thread,stop=stop,owner=(name,original))
            box['start_background_thread']=start_native
            box['bind_native_task']=ingress.bind_native_task
    return box


def a_host(*, store: Any = None, config: Any = None, name: str = "") -> dict[str, Any]:
    """ **造一个宿主**（"你能干什么"那几样）—— `launch.py` 造它、递给 `carry(host=…)` 。

    ⚠️ **每一样都可能缺席，而缺席就说缺席**（不给一个假的东西 ）：
       · `store`（事件库）没递 ⇒ **`push_event` ／ `read_events` 都不给**；
       · `config` 没递 ⇒ **`model_facts` 不给**； 而 `log` **永远给**（那不需要任何后端 ）。
    ⚠️ **`push_event` 落的是事件库**（`EventStore.append` ）—— 那是**插件报外部变化**的
       正路（照那边那条："被动变化通过事件报告" ）。 而它**不替插件编内容**：
       塞什么由插件说了算（`text` ／ `type` ／ `source` 都是它给的 ／ 那边那句话：
       "`text` 由生产方提供，**Core 不添加语义内容**" ）。
    """
    host: dict[str, Any] = {}
    if name:
        # `log` —— 一行一行地记（ 而它**住哪儿**：日志要一个落点 ⇒
        # 今天先**只保证"叫得动"**：交给调用方自己写的那个 `log` ／ 没给就打到标准输出 ）。
        def log(line: Any) -> None:
            import sys
            print(f"[plugin {name}] {line}", file=sys.stderr, flush=True)
        host["log"] = log
    if store is not None:
        background=getattr(store,'start_background',None)
        if callable(background):host['start_background_work']=background
        spawn=getattr(store,'spawn_background',None)
        if callable(spawn):host['spawn_background_work']=spawn
        submit=getattr(store,'submit_background',None)
        if callable(submit):host['submit_background_work']=submit
        native=getattr(store,'start_background_thread',None)
        if callable(native):host['start_background_thread']=native
        from .plugin_ingress import PluginIngress
        if type(store) is PluginIngress:host['_native_task_ingress']=store
        evidence=getattr(store,'background_evidence_reference',None)
        if callable(evidence):host['background_evidence_reference']=evidence
        def push_event(draft: Any) -> Any:
            """ **报一件事**（走事件库的 `append` ）—— 而返回的是它真落下的那一条 。

            ⚠️ **`ts` 由这一层补**（`append` 少它当场抛 —— 真跑一遍抓到的 ）：
                那是**记账**，不是**内容** —— "`text` 由生产方提供、Core 不添加语义内容"
               那条（`worlds.md:45` ）管的是**内容**；而"这件事发生在几点"是**库要的字段**，
               插件**多半不知道**（它没有钟 ）⇒ 缺就补上 。
            ⚠️ **有就用它给的**（插件自己知道得更准的时候，它说了算 ）。
            """
            import time
            box = dict(draft or {})
            if not box.get("ts"):
                box["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
            return store.append(box)
        host["push_event"] = push_event

        def read_events(**q: Any) -> Any:
            """ **读事件库**（只读 ）—— 不传条件 ⇒ 全库、升序（`RangeQuery` 的缺省 ）。

            ⚠ **2026-10-10 加了一把上限**（ 作者：「做甲」）—— 理由在下面那三行 ：
               那天**它卡在打转里出不来** ， 而量出来的根就在这一句 ：
               `read_events '{}'` ⇒ 缺省 `RangeQuery()` ⇒ **整本库**
              （ 实测：**209 条 ／ 约 14.5 万字** ）⇒ 那一轮**装不下、写不下**
              ⇒ 结果没进会话 ⇒ 输入不变 ⇒ 引擎**缓存命中**（ 0.02 秒回同一份 ）
              ⇒ 同一份又叫它再调一次 ⇒ **无限** 。
            ⚠ **为什么上限放在这一层**（ 而不是让它自己去填 `limit` ）：
               那张参数表（`tools.A_SINCE_GREP` ）**没有 `required`**
              ⇒ 模型**以为可以不填** ⇒ 于是“缺省”这一头**必须自己有个底** 。
            ⚠ **要更多的走 `limit`** ： 调用方**显式**给了就照它给 （ 这一层不替它压 ）。
            """
            from .events import RangeQuery
            box = dict(q)
            if not box.get("limit"):
                box["limit"] = READ_EVENTS_LIMIT
            return list(store.range(RangeQuery(**box)))
        host["read_events"] = read_events

        def latest_cursor() -> int:
            """ 现在的游标（插件常要它来记"我读到哪儿了" ）。"""
            return int(store.latest_cursor())
        host["latest_cursor"] = latest_cursor
    if config is not None:
        def model_facts() -> dict[str, Any]:
            """ **当前那一台机器**（模型名 ／ 窗口 —— 照那边 `modelFacts` ）。"""
            return {
                "model": str(getattr(config, "model", "") or ""),
                "contextWindow": getattr(config, "context_window", None),
                "baseUrl": str(getattr(config, "base_url", "") or ""),
            }
        host["model_facts"] = model_facts
    return host


class PluginSetupRequired(ValueError):
    """Expected setup state raised before starting plugin work."""


@_serialized_lifecycle
def carry(root: Any, name: str, *, host: Any = None, scope=None) -> dict[str, Any]:
    """ **把一份插件真的跑起来**（A 那一半：import 它 ＋ 叫 `create(ctx)`）。

    交回四样：`ok`（成没成）／ `said`（一句人话）／ `what`（它交回来的那一份）／
    `got`（`create` 交回来的对象，成的时候才有 ）。

    ⚠️⚠️ **它必须"崩了也不带走"**（这一整页的前提是"插件可有可无" ）：
       import 抛 ／ 没有那个入口 ／ 没有 `create` ／ `create` 自己抛 —— **每一种都兜住**
       ⇒ 交回 `ok=False` ＋ 一句人话，**而 core 照跑** 。
    ⚠️ **它写 `sys.modules`**（用包名做键 ）：不写的话，插件里那些**相对 import**
       会在装载那一刻断 （那是 Python 那边的实情 ）。
    """
    _require_unconfined_lifecycle()
    root_key = _require_open_root(root)
    scope_key = _setting_scope_key(scope)
    policy = _scope_runtime_policy(root_key, scope)
    import importlib.util
    import sys

    # ⚠️⚠️ **名字有两种，而这一层两种都要认**（ 真跑一遍抓到的 bug）：
    #   · **目录名**（`hello-world`）—— 文件系统上那一格；
    # · **包名**（`core-infinity-hello`，`package.json` 的 `name` ）—— **页上／那个开关
    # 用的就是它** ⇒ 只认目录名的话，`switch(on=True)` 那一趟会**找不到它** 。
    # ⇒ 先按目录名找；不是再**扫一遍**、按包名比 （ 目录只有几十个，那一点开销不算事 ）。
    one, manifest = _plugin_folder(root, name)
    if one is None:
        return {"ok": False, "said": f"没装过叫「{name}」的插件。",
                "what": None, "got": None}
    if manifest is None:
        return {"ok": False, "said": f"「{name}」那份 package.json 读不出来。",
                "what": None, "got": None}
    # These bundled adapters have private resources, not just switches. A
    # mixed rollout must never silently load an old shared-data implementation.
    # This is a compatibility declaration, NOT a lifecycle capability/guard.
    if (policy is not None and manifest.get('name') in policy.scope_required
            and (not isinstance(manifest.get('telyincore'), dict)
                 or type(manifest['telyincore'].get('botScope')) is not int
                 or manifest['telyincore']['botScope'] != 1)):
        return {'ok':False,
            'said':(
                'installed adapter lacks botScope=1; coordinate shared package upgrade '
                'before scoped '
                'startup'
            ),
                'what':None,'got':None}
    named = manifest.get("name")
    box = str(named) if isinstance(named, str) and named else name
    host = _scope_context(host, scope, box)
    # A normal Core process owns one Bot. Reject mixed live scopes rather than
    # reusing another Bot's instance or changing lifecycle ownership keys.
    for other_name, other_instance in tuple(_loaded.items()):
        if not _same_setting_scope(other_name, other_instance, scope):
            return {'ok': False, 'said': 'live plugin belongs to another Bot settings scope',
                'what': None, 'got': None}
    for dependency in policy.dependencies if policy is not None else ():
        if dependency.child != box:
            continue
        parent = _loaded.get(dependency.parent)
        if (not _scope_switches(root, scope).get(dependency.parent, False)
                or parent is None or not _same_setting_scope(dependency.parent, parent, scope)
                or _owned_root(dependency.parent, parent) != root_key
                or not _same_runtime_policy(dependency.parent, parent, policy)
                or _lifecycle_state(dependency.parent)[0] != 'running'):
            return {'ok': False, 'said': dependency.load_denied, 'what': None, 'got': None}
    if box in _loaded:
        if _owned_root(box, _loaded[box]) != root_key:
            return {"ok": False, "said": "plugin instance root is foreign or unproven",
                    "what": None, "got": None}
        state, error = _lifecycle_state(box)
        if state == "running":
            return {"ok": True, "said": f"「{box}」已经起来了。",
                    "what": _loaded[box], "got": _loaded[box]}
        return {"ok": False, "said": error or f"「{box}」正在 {state}，不能重复装载。",
                "what": None, "got": None}
    module = None
    created = False
    module_key = f"core_infinity_{box}"
    previous_module = sys.modules.get(module_key)
    lease = None
    reservation = object()
    _loaded[box] = reservation
    _instance_setting_scopes[box] = (reservation, scope_key, policy)
    _lifecycle_roots[box] = (reservation, root_key)
    _lifecycle_states[box] = (reservation, "starting", "")
    try:
        from .plugin_package_lock import PackageLock
        package_lease = PackageLock(root, box).__enter__()
        _package_leases[(root_key, box)] = package_lease
        # The package could have been removed before our lease was acquired.
        current_folder, current_manifest = _plugin_folder(root, name)
        if current_folder != one or current_manifest != manifest:
            raise ValueError('插件安装包已变化，请刷新后重试')
        entry = _the_entry(one, manifest)
        if not entry.is_file():
            raise FileNotFoundError(f"「{box}」没有入口那一份（{entry.name}）。")
        spec = importlib.util.spec_from_file_location(f"core_infinity_{box}", entry)
        if spec is None or spec.loader is None:
            raise ImportError(f"「{box}」那一份加载不起来。")
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"core_infinity_{box}"] = module
        spec.loader.exec_module(module)
        _require_open_root(root)
        make = getattr(module, MAKE, None)
        if not callable(make):
            raise TypeError(f"「{box}」里没有 {MAKE}(ctx) 那个入口函数。")
        context_host = (host or {}).get("_channel_context_host")
        if context_host is not None:
            lease = context_host.prepare(box, one, manifest)
        place = _a_place_to_stand(root, box, one, host)
        if context_host is not None and callable(place.get("push_event")):
            original_push = place["push_event"]
            if lease is not None:
                place["push_event"] = lambda draft: lease.push(original_push, draft)
            else:
                place["push_event"] = lambda draft: context_host.push_unconfigured(
                    original_push, draft)
        got = make(place)
        created = True
        # **交回来的那一份记在这儿**（`loaded` 查的就是这张表 ）——
        # ⚠️ **键用包名**（页上／那个开关用的都是它 ）； 少了这一句，
        # `carry` 成功了而 `loaded` 照旧说 `False` —— 两处打架（真跑一遍抓到的 ）。
        _loaded[box] = got
        _instance_setting_scopes[box] = (got, scope_key, policy)
        _lifecycle_roots[box] = (got, root_key)
        _require_open_root(root)
        _lifecycle_states[box] = (got, "starting", "")
        if lease is not None:
            _channel_leases[box] = (got, lease)
        start = getattr(got, "start", None)
        if callable(start):
            result = start()
            _reject_lifecycle_return(box, got, result, 'start')
            if result is False or (isinstance(result, dict) and result.get("ok") is False):
                raise RuntimeError(f"start() 未完成：{result}")
        _require_open_root(root)
        if lease is not None:
            lease.activate()
    except Exception as exc: # noqa: BLE001 —— **崩了也不带走**（见上面那一段）
        if lease is not None:
            lease.abort()
        why = f"「{box}」装载的时候出错了：{type(exc).__name__}: {exc}"
        cleanup_target = got if created else module
        if cleanup_target is not None:
            try:
                _stop_instance(box, cleanup_target)
            except Exception as cleanup:
                why += f"；清理失败，实例仍被追踪，须先重试关闭：{type(cleanup).__name__}: {cleanup}"
                _loaded[box] = cleanup_target
                _instance_setting_scopes[box] = (cleanup_target, scope_key, policy)
                _lifecycle_roots[box] = (cleanup_target, root_key)
                _lifecycle_states[box] = (cleanup_target, "cleanup_failed", why)
                return {"ok": False, "said": why, "what": None, "got": None}
        _loaded.pop(box, None)
        package_lease = _package_leases.pop((root_key, box), None)
        if package_lease is not None: package_lease.close()
        _instance_setting_scopes.pop(box, None)
        _lifecycle_roots.pop(box, None)
        _lifecycle_states.pop(box, None)
        if module is not None and sys.modules.get(module_key) is module:
            if previous_module is None:
                sys.modules.pop(module_key, None)
            else:
                sys.modules[module_key] = previous_module
        if isinstance(exc, PluginSetupRequired):
            return {"ok": True, "setup_required": True, "said": str(exc),
                    "what": None, "got": None}
        return {"ok": False, "said": why,
                "what": None, "got": None}
    _lifecycle_states.pop(box, None)
    return {"ok": True, "said": f"「{box}」起来了。", "what": got, "got": got}


@_serialized_lifecycle
def put_out(root: Any, name: str, *, scope=None) -> dict[str, Any]:
    """ **把它停下来**（关掉的时候叫这一声 ）—— 有 `stop()` 就叫，没有就只是忘掉 。

    ⚠️ **`stop()` 自己抛 ⇒ 兜住**（"停不下来"不该把开关那一格带崩 —— 与装载同一条口径 ）。
    """
    name = _plugin_name(root, name)
    if name not in _loaded:
        return {"ok": True, "said": f"「{name}」已经停了。"}
    got = _loaded[name]
    if not _same_setting_scope(name, got, scope):
        return {"ok": False, "said": "plugin stop belongs to another Bot settings scope"}
    owner = _owned_root(name, got)
    if owner is not None and owner != _root_key(root):
        return {"ok": False, "said": "plugin instance belongs to another root"}
    return _put_out_owned_name(name, got)


def _put_out_owned_name(name: str, got: Any) -> dict[str, Any]:
    if name not in _loaded or _loaded[name] is not got:
        return {"ok": False, "said": "plugin generation changed; original drain owner retained"}
    dependents = _retained_dependents(name, got)
    if dependents:
        return {"ok": False, "said": dependents[0][0].disable_denied}
    # Caller holds lifecycle lock. No filesystem lookup: manifests may be gone.
    state, _ = _lifecycle_state(name)
    if state in {"starting", "stopping"}:
        return {"ok": False, "said": f"「{name}」正在 {state}，不能重入停止。"}
    attempts = getattr(_root_drain_context, "attempts", None)
    if attempts is not None:
        owner = _owned_root(name, got)
        if any(row[0] == name and row[1] is got and row[2] == owner for row in attempts):
            return {"ok": False, "said": "plugin generation already attempted in this root drain; "
                    "retained for explicit retry"}
        # Consume before calling the hook, so nested public drains cannot retry.
        attempts.append((name, got, owner))
    _lifecycle_states[name] = (got, "stopping", "")
    try:
        _stop_instance(name, got)
    except BaseException as exc:                # preserve cancellation/interrupt ownership too
        why = f"「{name}」停的时候出错了，实例仍被追踪：{type(exc).__name__}: {exc}"
        _lifecycle_states[name] = (got, "stop_failed", why)
        if not isinstance(exc,Exception):raise
        return {"ok": False, "said": why}
    package_root = _owned_root(name, got)
    _loaded.pop(name, None)
    package_lease = _package_leases.pop((package_root, name), None)
    if package_lease is not None: package_lease.close()
    _instance_setting_scopes.pop(name, None)
    _lifecycle_roots.pop(name, None)
    _lifecycle_states.pop(name, None)
    return {"ok": True, "said": f"「{name}」停了。"}


@_serialized_lifecycle
def fence_root(root: Any) -> None:
    """Deny new work without stopping resources still used by pending tools."""
    _closing_roots.add(_root_key(root))


@_serialized_lifecycle
def shutdown_root(root: Any) -> dict[str, Any]:
    """Fence and drain only instances proven created for this root by carry().

    Retry retained failures without reopening the root or changing enabled intent.
    Unknown rows are neither adopted nor forgotten. This cooperative synchronous
    stop contract is NOT a descendant census, bounded stop or offline authority.
    """
    key = _root_key(root)
    _closing_roots.add(key)
    stopped, failed, unscoped = [], {}, []
    with _root_drain_pass():
        for name, instance in _root_stop_order(tuple(_loaded.items())):
            if name not in _loaded or _loaded[name] is not instance:
                continue  # Already drained by an earlier callback, never stop it twice.
            owner = _owned_root(name, instance)
            if owner is None:
                unscoped.append(name)
            elif owner == key:
                result = _put_out_owned_name(name, instance)
                if result['ok']:
                    stopped.append(name)
                else:
                    failed[name] = result['said']
    return {'ok': not failed and not unscoped, 'stopped': stopped,
            'failed': failed, 'unscoped': unscoped, 'complete': False}


def _read_manifest(path: Path) -> dict[str, Any] | None:
    """读一份 `package.json` —— **读不出来就交回 `None`**（不抛、也不编一个空壳 ）。

    ⚠️ **两类读不到都算"没有"**：文件不在（`OSError`）与不是 JSON（`JSONDecodeError`）——
       与 `prefix.assemble` 那条"读不到就是这一行没有正文"同一条口径 。
      ⚠️ **它不判声明合不合格**（归安装入口及激活时的清单校验）：
       这一层只**如实**把能读到的报出来 。
    """
    try:
        got = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return got if isinstance(got, dict) else None


def _one_plugin(name: str, manifest: dict[str, Any], *, on: bool = False,
                place: Path | None = None,
                origin: dict[str, str] | None = None,
                builtin: bool = False,
                gone: bool = False) -> dict[str, Any]:
    """将声明转换为插件列表项。

installed 表示目录存在，enabled 表示配置意图，loaded 表示进程持有实例。
运行、失败及清理责任由 lifecycle 报告；displayName 缺省使用包名。
优先读取 telyincore 元数据，同时识别旧的第三方兼容字段。"""
    mine = manifest.get("telyincore")
    theirs = manifest.get("cortico")
    box = mine if isinstance(mine, dict) else (theirs if isinstance(theirs, dict) else {})
    keywords = manifest.get("keywords")
    display = box.get("displayName")
    kind = str(box.get("kind") or "")
    api = box.get("api") if isinstance(box.get("api"), int) else None
    named = str(manifest.get("name") or name)
    # **作者**（2026-10-05 作者问过的那一条）：先读包里的 `author`，而**包里没写**时
    # 退回**我们记下来的来源**（装的那一刻的 GitHub owner ）—— 两样都读不到 ⇒ 空串
    # （ 页上把空串说成"没写"，而那与"（没写）"是同一件事 ）。
    told = manifest.get("author")
    if isinstance(told, dict):
        told = told.get("name")
    by = str(told).strip() if isinstance(told, str) and str(told).strip() else ""
    if not by and origin:
        by = str(origin.get("by") or "")
    # 体积 ／ 文件数 ／ 那一份入口（详情面板要显示的那几格 —— 都是**量出来的** ）
    size = 0
    files = 0
    if place is not None and place.is_dir():
        for item in sorted(place.rglob("*")):
            if item.is_symlink() or not item.is_file():
                continue
            try:
                size += item.stat().st_size
            except OSError:
                continue
            files += 1
    return {
        "name": named,
        "displayName": str(display) if isinstance(display, str) and display else named,
        "icon": box.get("icon") if isinstance(box.get("icon"), str) else "",
        "version": str(manifest.get("version") or ""),
        "kind": kind,
        "author": by,
        "license": str(manifest.get("license") or ""),
        "description": str(manifest.get("description") or ""),
        "entry": str(manifest.get("main") or "index.py"),
        "bytes": size,
        "files": files,
        # **这是一份"内置的"吗**（随这个项目发的 ）—— 而页上靠它**置顶 ＋ 灰着显示**
        "builtin": bool(builtin),
        # **装了没有**（2026-10-05：内置那一批**卸了也不消失** —— 那时这一格是 `False`，
        # 而页上把那一张卡**整块灰掉**，要再用就得**重新装** ）。
        "installed": not bool(gone),
        # 开了没有（那个开关 ）
        "enabled": bool(on),
        # 跑起来没有 —— **它说的是实话**：`_loaded` 里有它 ⇒ **真的 import 过、
        # `create(ctx)` 也回来了** ；而不是"我读过它的 package.json 了" 。
        "loaded": _lifecycle_state(named)[0] == "running",
        "tracked": named in _loaded,
        "lifecycle": _lifecycle_state(named)[0],
        "lifecycle_error": _lifecycle_state(named)[1],
        "api": api,
        "keywords": [str(one) for one in keywords] if isinstance(keywords, list) else [],
        # **它自己的"外部渠道词表"**（2026-10-17）：`telyincore.channels` ——
        # 经历那一页靠它把"哪一路来的"画成 `[那一侧·群]` 那种前缀。
        # ⚠️ **核心不认识任何外部渠道名**：这一格是**插件交上来的数据**
        #    （`label` ／ `scope` ／ 标记正则全在插件自己的 package.json 里）
        #    ⇒ 加一个外部渠道（微 ／ B…）＝ 那个插件多写一条，页面一个字不用改。
        # ⚠️ 形状不对的那几条**丢掉**（一个坏插件不许把整页带走）。
        "channels": _the_channels(box.get("channels")),
        # **契约那一句**（空那一串 ＝ 对得上 ／ 有字 ＝ 为什么对不上 ）
        "contract": _contract_said(kind, api),
        # 装进来的那一份在哪儿（详情面板要显示"安装位置"那一行 ）
        "where": str(place) if place is not None else "",
        # **从哪儿来**（装的那一刻记下来的那条链接 ／ 谁写的 —— 空那一份 ＝ 不知道 ）
        "origin": dict(origin or {}),
    }


def installed(root: Any, *, scope=None) -> dict[str, Any]:
    """返回插件位置、按目录名排序的列表、损坏声明及 wired 状态。

目录不存在时列表为空，wired 仍为 True；未安装与读取失败分别报告。"""
    place = the_place(root)
    plugins: list[dict[str, Any]] = []
    broken: list[str] = []
    if not place.is_dir():
        # ⚠️ **连"一个都没装"也要把内置那一批列出来**（ 它们本就该在名单里 ）
        return {"where": str(place), "plugins": _the_builtin_rows(root, set()),
                "broken": broken, "wired": True, "shared_uninstall": 1}
    # 那个开关（`state.json`）—— 读一次，给下面每一项用
    switch = _scope_switches(root, scope)
    # **"从哪儿来"那一份**（2026-10-05）—— 也是读一次给每一项用
    origins = _the_origins(place)
    for one in sorted(place.iterdir(), key=lambda p: p.name):
        if not one.is_dir() or one.name.startswith("."):
            continue
        manifest = _read_manifest(one / MANIFEST)
        if manifest is None:
            broken.append(one.name)
            continue
        box = manifest.get("name")
        named = str(box) if isinstance(box, str) and box else one.name
        plugins.append(_one_plugin(one.name, manifest, on=switch.get(named, False),
                                   place=one, origin=origins.get(named)))
    # **内置那一批**（2026-10-05 作者那两句）——
    # · 装了的 ⇒ 上面那一趟已经在名单里了 ⇒ 只把 `builtin` 那一格标上 ；
    # · **没装的 ⇒ 也要列**（ 那是"卸了也不消失"那一条的落点 ）。
    have = {one["name"] for one in plugins}
    for one in _the_builtin_rows(root, have):
        plugins.append(one)
    # ⚠️⚠️ **装着的那些内置也要标上**（2026-10-05 真驱动浏览器抓到的真 bug）：
    # 我第一版的合并**只把"没装的"那些列出来** ⇒ 而**装着的**内置那一格照旧是 `False`
    # ⇒ 它**不会被置顶**（"核心插件要置顶"那一条当场落空 ）。
    # ⚠️ 为什么那个临时目录的验没抓住： 那一趟里内置是"**没装**"的（它走的是另一条路 ）
    # —— 这正是"替身绿而真的红"那一类 ⇒ 修完要**用"装着的"再验一遍** 。
    builtin_names = {one["name"] for one in _the_builtin_rows(root, set())}
    for one in plugins:
        one["can_remove"] = True
        if not _same_setting_scope(one["name"], _loaded.get(one["name"]), scope):
            one["loaded"] = False
        if one["name"] in builtin_names:
            one["builtin"] = True
    # **内置置顶**（作者："新的插件应该排在核心插件的后面，也就是核心插件要置顶"）——
    # 同一个次序里按名字排（ 一个确定的次序，不靠文件系统那一套 ）。
    plugins.sort(key=lambda one: (0 if one.get("builtin") else 1, str(one["name"])))
    return {"where": str(place), "plugins": plugins, "broken": broken, "wired": True, "shared_uninstall": 1}


def _the_builtin_rows(root: Any, have: set[str]) -> list[dict[str, Any]]:
    """ **内置那一批交回来的那几行**（`have` 里有的 ⇒ 标个 `builtin` 就完了 ）。

    ⚠️ 读不出来（没 manifest）的**跳过** —— 内置那一批是**跟这个项目一起发的**，
       写坏一个是**我们自己的事**，不该让整份名单崩 （ 而 `broken` 那一格说的是
       `extensions/` 里别人的东西 ）。
    """
    built = the_builtin(root)
    rows: list[dict[str, Any]] = []
    if not built.is_dir():
        return rows
    # `builtin/infinity/<package>/` 是内置 infinity 插件的源码位置；
    # 也继续识别旧的 `builtin/<package>/`，以免已有本地源突然从列表消失。
    manifests = sorted((*built.glob(f"*/{MANIFEST}"),
                        *built.glob(f"*/*/{MANIFEST}")), key=lambda p: str(p))
    for path in manifests:
        one = path.parent
        if path.is_symlink() or one.is_symlink() or one.parent.is_symlink():
            continue
        manifest = _read_manifest(path)
        if manifest is None:
            continue
        named = str(manifest.get("name") or one.name)
        if named in have:
            continue # 装了的，上面那一趟已经列了
        rows.append(_one_plugin(one.name, manifest, on=False, place=one,
                                builtin=True, gone=True))
    return rows


@_serialized_lifecycle
def switch(root: Any, name: str, *, on: bool, host: Any = None, scope=None) -> dict[str, Any]:
    """修改插件启用意图并执行相应生命周期操作。

安装、启用与运行状态分别报告；名字不存在时抛出 KeyError。"""
    _require_unconfined_lifecycle()
    root_key = _require_open_root(root)
    policy = _scope_runtime_policy(root_key, scope)
    place = the_place(root)
    name = _plugin_name(root, name)
    # ⚠️ **只认"真装着的"**（2026-10-05）：内置那一批**卸了也还在名单里**（灰着 ）⇒
    # 拿它来比就必须**滤掉没装的**，不然"开一个不存在的插件"会被放过去
    # （而那是一条**永远对不上的状态** ）。
    have = {one["name"] for one in installed(root, scope=scope)["plugins"] if one.get("installed")}
    if name not in have:
        raise KeyError(f"没装过叫「{name}」的插件（现在装着的是 {sorted(have)}）")
    # 启用配置表达用户意图；loaded 与 lifecycle 反映实际实例状态及失败原因。
    if scope is not None:
        _scope_context(host, scope, name)
        _scope_switches(root, scope)  # reject corrupt state before any lifecycle mutation
        for dependency in policy.dependencies:
            parent = _loaded.get(name)
            retained = name in _loaded and bool(_retained_dependents(name, parent))
            if (not on and name == dependency.parent
                    and (_scope_switches(root, scope).get(dependency.child, False) or retained)):
                return {**installed(root, scope=scope), 'ok': False,
                        'said': dependency.disable_denied}
        # Publish persistent intent before starting/stopping anything. A failed
        # durable write must never create an unrecorded running instance.
        _save_scope_switch(root, scope, name, bool(on))
    if on:
        outcome = carry(root, name, host=host, scope=scope)
    else:
        outcome = put_out(root, name, scope=scope)
    if scope is None:
        _save_scope_switch(root, scope, name, bool(on))
    return {**installed(root, scope=scope), "ok": outcome["ok"], "said": outcome["said"],
            **({"setup_required": True} if outcome.get("setup_required") else {})}


def wake(root: Any, *, host: Any = None, scope=None) -> dict[str, Any]:
    """启动时仅加载已启用插件，分别返回成功列表和带原因的失败列表。"""
    _require_unconfined_lifecycle()
    _require_open_root(root)
    up: list[str] = []
    down: list[tuple[str, str]] = []
    waiting: list[tuple[str, str]] = []
    for one in installed(root, scope=scope)["plugins"]:
        if not one.get("enabled"):
            continue
        got = carry(root, one["name"], host=host, scope=scope)
        if got.get("setup_required"):
            waiting.append((one["name"], str(got["said"])))
        elif got["ok"]:
            up.append(one["name"])
        else:
            down.append((one["name"], str(got["said"])))
    return {"up": up, "down": down, **({"waiting": waiting} if waiting else {})}


def event_requires_isolation(event: Any) -> bool:
    """An active adapter may reserve a single accepted event before identity binding.

    This creates no event, tool invocation or account permission. Classification
    is synchronous and local; the later normal dispatcher/identity guards apply.
    """
    import inspect
    for name, instance in _active_items():
        predicate = getattr(instance, 'event_requires_isolation', None)
        if not callable(predicate):
            continue
        try:
            if inspect.iscoroutinefunction(predicate):
                raise TypeError('batch predicate must be synchronous')
            answer = _guard_plugin_handler(name, instance, predicate)(event)
            if type(answer) is not bool:
                close = getattr(answer, 'close', None)
                if callable(close):
                    close()
                raise TypeError('batch predicate must return bool')
            if answer:
                return True
        except Exception as exc:
            import sys
            print('[plugin] batch isolation predicate failed: '+type(exc).__name__, file=sys.stderr)
            # A classifier failure must not combine account commands with others.
            return True
    return False


def event_is_current(event: Any) -> bool:
    """Let the owning adapter invalidate a queued event without deleting history."""
    for _, instance in _active_items():
        check = getattr(instance, "event_is_current", None)
        if callable(check):
            try:
                if not check(event):
                    return False
            except Exception as exc:
                import sys
                print(f"[plugin] event filter failed: {type(exc).__name__}", file=sys.stderr)
                return False
    return True


_system_calls: dict[object, Any] = {}


def _system_entry(instance, operation):
    from .system_calls import SystemHook
    import inspect
    claim = getattr(instance, 'system_hooks', None)
    if claim is None:
        return None  # Explicit legacy plugin contract, never blindly offloaded.
    if not callable(claim):
        raise TypeError('invalid system hook declaration')
    hooks = claim()
    if type(hooks) is not dict or set(hooks) - {'output_tap', 'proactive_send', 'resolve_media', 'waves_login_result'}:
        raise TypeError('invalid system hook table')
    for hook in hooks.values():
        if (type(hook) is not SystemHook or not callable(hook.prepare)
                or inspect.iscoroutinefunction(hook.prepare)
                or inspect.iscoroutinefunction(getattr(hook.prepare, '__call__', None))):
            raise TypeError('system hooks require an explicit typed prepare stage')
    return hooks.get(operation)


def _prepare_system(name, instance, entry, *args):
    import inspect
    from .system_calls import PreparedSystemCall
    if not _lifecycle_lock.acquire(blocking=False):
        raise RuntimeError('system hook lifecycle busy; no I/O started')
    try:
        root = _owned_root(name, instance)
        if (not root or root in _closing_roots or _loaded.get(name) is not instance
                or _lifecycle_state(name)[0] != 'running'):
            raise RuntimeError('system hook instance/root is no longer current')
        prepared = _guard_plugin_handler(name, instance, entry.prepare)(*args)
        if prepared is None:
            return None  # This source batch does not belong to this adapter.
        if (type(prepared) is not PreparedSystemCall or not prepared.bot or not prepared.scope
                or not inspect.iscoroutinefunction(prepared.body)):
            from .async_tools import ManagedAwaitable
            if isinstance(prepared, ManagedAwaitable):
                prepared.close()  # cold closes; started Futures retain their lease
            raise TypeError('system prepare must return a frozen native call')
        return root, prepared
    finally:
        _lifecycle_lock.release()


def _validate_system_call(ticket, owner):
    import asyncio
    from .async_tools import WorkOwner
    from .system_calls import SystemInvocation, current_system_call
    with _async_calls_lock:
        registered = _system_calls.get(ticket) if type(ticket) is SystemInvocation else None
    if (registered != (asyncio.get_running_loop(), asyncio.current_task())
            or current_system_call.get() is not ticket
            or not isinstance(owner, WorkOwner)
            or (owner.bot, owner.scope, owner.call_id, owner.tool)
               != (ticket.bot, ticket.scope, ticket.call_id, ticket.operation)
            or _loaded.get(ticket.plugin) is not ticket.instance
            or _owned_root(ticket.plugin, ticket.instance) != ticket.root
            or ticket.root in _closing_roots
            or _lifecycle_state(ticket.plugin)[0] != 'running'):
        raise ValueError('blocking I/O requires an active exact host system call')


async def _run_system_prepared(name, instance, operation, captured):
    import asyncio
    import uuid
    from .system_calls import SystemInvocation, current_system_call
    root, prepared = captured
    if asyncio.current_task().cancelling():
        raise asyncio.CancelledError
    ticket = SystemInvocation(name, instance, root, operation, uuid.uuid4().hex,
                              prepared.bot, prepared.scope)
    guarded = _guard_plugin_handler(name, instance, prepared.body)
    with _async_calls_lock:
        _system_calls[ticket] = (asyncio.get_running_loop(), asyncio.current_task())
    token = current_system_call.set(ticket)
    try:
        return await guarded()
    finally:
        current_system_call.reset(token)
        with _async_calls_lock:
            _system_calls.pop(ticket, None)


async def output_taps(batch: Any, text: str) -> None:
    """Capture registered system calls before any await, then consume serially.

    Legacy plugins are not implicitly migrated. Registered prepare failures
    cannot fall back to synchronous I/O. Optional delivery is never retried.
    """
    import inspect
    import sys
    from .batch_snapshot import freeze_batch
    import json
    frozen = freeze_batch(lambda: batch or ())
    pending = []
    for name, instance in _active_items():
        try:
            entry = _system_entry(instance, 'output_tap')
            if entry is not None:
                captured = _prepare_system(name, instance, entry, tuple(json.loads(frozen)), text)
                if captured is not None:
                    pending.append((name, instance, captured, None))
            else:
                tap = getattr(instance, 'output_tap', None)
                if callable(tap):
                    pending.append((name, instance, None,
                                    _guard_plugin_handler(name, instance, tap)))
        except Exception as exc:
            print(f'[plugin {name}] system prepare failed: {type(exc).__name__}',
                  file=sys.stderr, flush=True)
    for name, instance, captured, legacy in pending:
        try:
            if captured is not None:
                await _run_system_prepared(name, instance, 'output_tap', captured)
            else:
                result = legacy(batch, text)
                if inspect.isawaitable(result):
                    await result
        except Exception as exc:
            print(f'[plugin {name}] output tap failed: {type(exc).__name__}',
                  file=sys.stderr, flush=True)


def resolve_media(reference: str) -> str:
    """Resolve a small persisted media handle only when a model needs its bytes."""
    for _, instance in _active_items():
        resolve = getattr(instance, "resolve_media", None)
        if not callable(resolve):
            continue
        result = resolve(reference)
        if result is not None:
            if not isinstance(result, str) or not result.startswith("data:image/"):
                raise ValueError("插件没有返回有效的图片数据")
            return result
    raise ValueError("没有插件能解析这张图片")


def prepare_media_resolutions(references, *, root_key=None, consumer=None):
    """Capture every native media handler before first await; resolve serially.

    Legacy resolvers are not implicitly executed on the owner loop or blindly
    offloaded. Bad native registration never falls back to the sync API.
    """
    from types import MappingProxyType
    if not isinstance(root_key, str) or not root_key:
        raise ValueError('native media requires its assembled host root')
    import inspect
    if consumer is not None and not inspect.iscoroutinefunction(consumer):
        raise TypeError('media consumer must be an explicit native coroutine function')
    refs = tuple(dict.fromkeys(references))
    if any(not isinstance(ref, str) or not ref.startswith('plugin-media:') for ref in refs):
        raise ValueError('native media requires exact projected request handles')
    items = tuple((name, instance) for name, instance in _active_items()
                  if _owned_root(name, instance) == root_key)
    pending, bots = {}, set()
    for reference in refs:
        matches = []
        for name, instance in items:
            try:
                entry = _system_entry(instance, 'resolve_media')
                if entry is None:
                    continue
                captured = _prepare_system(name, instance, entry, reference)
                if captured is not None:
                    matches.append((name, instance, captured))
            except Exception:
                # This is an unavailable picture, never permission to sync-read.
                continue
        if len(matches) == 1:
            pending[reference] = matches[0]
            bots.add(matches[0][2][1].bot)
    if len(bots) > 1:
        pending.clear()  # A request cannot borrow multiple live Bot generations.
    consumed = False

    async def resolve():
        nonlocal consumed
        if consumed:
            raise RuntimeError('prepared media request is single use')
        consumed = True
        result = dict.fromkeys(refs)
        for reference, (name, instance, captured) in pending.items():
            try:
                value = await _run_system_prepared(name, instance, 'resolve_media', captured)
                if not isinstance(value, str) or not value.startswith('data:image/'):
                    raise ValueError('invalid native image result')
                result[reference] = value
            except Exception:
                pass  # No retry; optional image remains unavailable.
        return MappingProxyType(result)

    if consumer is None:
        return resolve

    async def consume():
        return await consumer(await resolve())

    # Hold each real selected media instance through the actual downstream
    # consumer, not just the file read. No fabricated model/system invocation.
    guarded = consume
    instances = {(name, id(instance)): instance
                 for name, instance, _ in pending.values()}
    for (name, _), instance in reversed(tuple(instances.items())):
        guarded = _guard_plugin_handler(name, instance, guarded)

    async def owned_consumer():
        return await guarded()

    return owned_consumer


def _retain_plugin_dependency(instance, value):
    """Retain unexpected work derived from an already active instance call.

    Does not admit new work or mint a model/system invocation. Works while a
    root is fenced: existing work must not be forgotten because stop was asked.
    """
    import inspect
    from .async_tools import ManagedAwaitable
    if not inspect.isawaitable(value):
        raise TypeError('only asynchronous dependencies need retained ownership')
    ticket = object()
    with _async_calls_lock:
        if not any(owner is instance for owner in _async_calls.values()):
            raise RuntimeError('dependency has no active parent instance lease')
        _async_calls[ticket] = instance

    async def before():
        pass  # Observation of existing work, not fresh admission after a fence.

    def release():
        with _async_calls_lock:
            _async_calls.pop(ticket, None)

    return ManagedAwaitable(value, before, release)


def _proactive_instances(provider: str | None = None) -> list[Any]:
    ready: list[Any] = []
    for name, instance in _active_items():
        if provider is not None and name != provider:
            continue
        available = getattr(instance, "proactive_available", None)
        sender = getattr(instance, "proactive_send", None)
        if not callable(available) or not callable(sender):
            continue
        try:
            if available():
                ready.append(instance)
        except Exception:  # an optional plugin cannot spoil the status view
            continue
    return ready


def proactive_available(*, provider: str | None = None) -> bool:
    """Whether there is exactly one configured destination for proactive speech."""
    return len(_proactive_instances(provider)) == 1


def deliver_proactive(text: str, *, provider: str | None = None) -> str | None:
    """Let the one configured infinity deliver speech without naming its channel."""
    ready = _proactive_instances(provider)
    if not ready:
        return None
    if len(ready) != 1:
        raise RuntimeError("多个主动消息出口同时可用，无法确定收件人")
    return ready[0].proactive_send(text)


def prepare_proactive(text: str, *, bot: str, provider: str | None = None):
    """Freeze one native destination before outbox append/any await.

    Missing/malformed native registration is not permission to call sync I/O.
    The returned cold closure starts no work until actually awaited once.
    """
    ready = _proactive_instances(provider)
    if len(ready) > 1:
        raise RuntimeError('multiple proactive destinations; no delivery attempted')
    captured = None
    if ready:
        instance = ready[0]
        name = next(name for name, value in _active_items() if value is instance)
        entry = _system_entry(instance, 'proactive_send')
        if entry is None:
            raise RuntimeError('proactive destination lacks native owned registration')
        captured = _prepare_system(name, instance, entry, text)
        if captured is None or captured[1].bot != bot:
            raise RuntimeError('proactive destination Bot owner mismatch')
    consumed = False

    async def deliver():
        nonlocal consumed
        if consumed:
            raise RuntimeError('prepared proactive delivery is single use')
        consumed = True
        if captured is None:
            return None
        return await _run_system_prepared(name, instance, 'proactive_send', captured)

    return deliver


@_serialized_lifecycle
def gathered(root: Any, *, taken: Any = (), reserved: Any = None, scope=None) -> dict[str, Any]:
    """收集插件 ToolDef 及逐插件的拒绝原因。

重名、保留名或无效 effect 使该插件整组工具被拒绝，不影响其他插件。
复制绑定并添加实例有效性保护，不改插件原声明；旧快照不能调用已失效实例。"""
    from .tools import ToolDef
    from dataclasses import replace

    specs: list[Any] = []
    said: list[tuple[str, str]] = []
    mine = {str(one) for one in taken}
    for one in installed(root, scope=scope)["plugins"]:
        if not one.get("enabled"):
            continue
        got = _loaded.get(one["name"])
        if not _same_setting_scope(one["name"], got, scope):
            continue
        if got is None or _lifecycle_state(one["name"])[0] != "running":
            # ⚠️ **开了而没起来** ⇒ 它当然没有工具
            # （那与"起来了、而一个工具都没声明"是两件事 ）
            continue
        claim = getattr(got, "tools", None)
        if not callable(claim):
            continue # 没声明工具 ⇒ 正常
        try:
            wants = list(claim() or [])
        except Exception as exc: # noqa: BLE001 —— 崩了也不带走
            said.append((one["name"], f"它的 tools() 出错了：{type(exc).__name__}: {exc}"))
            continue
        here: list[Any] = []
        why = ""
        for want in wants:
            if not isinstance(want, ToolDef):
                why = f"它交上来的那个 {type(want).__name__} 不是 ToolDef"
                break
            from .tools import EFFECTS
            if want.effect not in EFFECTS:
                why = f"「{want.name}」的 effect/sidecar 声明无效"
                break
            if not callable(getattr(want, "handler", None)):
                why = f"「{want.name}」没有能调的 handler"
                break
            if str(want.name) in mine:
                # **撞上已经收下的那一个（别的插件 ／ 核心自己那张表）** ⇒ 拒它一个
                why = f"「{want.name}」与已经有的那一个撞名了"
                break
            if reserved is not None and reserved(str(want.name)):
                why = f"「{want.name}」是框架自己占着的名字（保留名）"
                break
            try:
                guarded = _guard_plugin_handler(one["name"], got, want.handler)
            except Exception as error:
                why = "工具执行登记失败：" + type(error).__name__
                break
            here.append(replace(want, handler=guarded))
            mine.add(str(want.name))
        if why:
            # ⚠️ **整串也不要**（那一串是一份声明 ⇒ 收一半比不收更坏：插件以为它在 ）
            for one2 in here:
                mine.discard(str(one2.name))
            said.append((one["name"], why))
            continue
        specs.extend(here)
    return {"specs": specs, "said": said}


@_serialized_lifecycle
def remove(root: Any, name: str, *, host: Any = None, scope=None) -> dict[str, Any]:
    """ **删掉一个装进来的插件**（2026-10-05 作者："参考它的排版" ⇒ 照参考那张卡底下那枚
    「删除扩展」）。

     **它不是一个"删目录"的动作**（照参考那句正本：「**删除扩展会移除共享安装包**」）——
       要把这三件做全，**缺一件都会留下一个坏状态** ：
         ① **先关掉它**（ 一个跑着的插件被抽掉之后，它下一次被叫就会崩
            —— 而"关掉"那一趟同时把它的工具从那张表里拿掉 ）；
         ② **把那格目录真删掉**（ 而**只删那一格**：一个字节都不许跑到
            `the_place()` 外面去 —— 照本仓那条"不许越界" ）；
         ③ **把 `state.json` 里那一格也忘掉**（ 不然会留一条**指向不存在的东西**的记录 ）。

    ⚠️ **名字要先过一遍"认不认得"**：`../..` 那一类**当场拒** ，而且
       **只认 `installed()` 里有的名字**（ 不许把这一格变成"删盘上任意一个目录"的口子 ）。
    ⚠️ **两边装法不同，这一点照实说**：参考那句说"移除共享安装包"（它是 pnpm 那套，
       共享一份）； 而我们装的时候是**把文件抄进来**的 ⇒ 这边删掉就是**全删了**
       （ 而底下的回执也会这么说 ）。
    """
    _require_unconfined_lifecycle()
    if scope is not None:
        _require_unconfined_lifecycle()
        _require_open_root(root)
        _scope_context(host, scope, name)
        return {'ok': False, 'name': name, 'where': '',
                'said': (
                    '此插件的安装包由多个档案共享，当前档案不能单独卸载。'
                    '如需停止使用，请在此档案中停用；共享文件会保留，其他档案不受影响。'
                )}
    import shutil

    said = str(name or "").strip()
    if not said or "/" in said or "\\" in said or said in (".", "..") or said.startswith("."):
        return {"ok": False, "name": said, "where": "",
                "said": "那个名字不像一个插件名（一个字节都没动）。"}
    base = the_place(root).resolve()
    have = [one["name"] for one in installed(root)["plugins"]]
    if said not in have:
        return {"ok": False, "name": said, "where": "",
                "said": f"没有叫「{said}」的插件（没动 —— 现在装着的是 {have}）。"}
    # ① **先关掉**（ 而它同时把工具从那张表里拿掉 ）
    stopped = switch(root, said, on=False, host=host)
    if not stopped.get("ok"):
        return {"ok": False, "name": said, "where": "", "said": stopped["said"]}
    # ② **删那一格**（ 先确认那一格**真在** `extensions/` 里面 —— 不许越界 ）
    place = the_place(root) / said
    if place.resolve().parent != base:
        return {"ok": False, "name": said, "where": str(place),
                "said": "那一格不在插件目录里（没动）。"}
    if place.is_dir():
        try:
            from .plugin_package_lock import PackageLock
            with PackageLock(root, said, exclusive=True):
                shutil.rmtree(place)
        except (OSError, ValueError) as exc:
            # ⚠️ **删不掉要说清楚**（2026-10-05）：Windows 上**文件被占用**就删不掉 ——
            # 而那是真会发生的一件事（插件自己打开着它目录里的一个日志／数据库 ）。
            # 而**这不是"要先关 core"** —— 插件跑在 core**这个进程里**，
            # "关掉插件"那一步上面已经做了（ 本进程已经不再读它了 ）；
            # 删不掉的原因是**那个文件句柄还开着** —— 而句柄归那一份插件的代码 。
            return {"ok": False, "name": said, "where": str(place),
                    "said": (f"「{said}」已经关掉了，而那一格目录**删不掉**："
                             f"{type(exc).__name__}: {exc} —— "
                             f"（ 通常是它自己还开着一个文件。core 不必停；"
                             f"把那一份文件放掉，再按一次即可。）")}
    # ③ **那一格名字也忘掉**（`on=None` ⇒ 从 state.json 里去掉 ），
    # 而"它从哪儿来"那一格**也一起去掉**（ 留一条指向不存在的名字，那是假账 ）
    _set_the_switch(the_place(root), said, None)
    _set_an_origin(the_place(root), said, None)
    return {"ok": True, "name": said, "where": str(place),
            "said": f"「{said}」删掉了（ 那一格目录没了 —— 要再装回来，去市场那一段 ）。"}


def recalc(root: Any, *, taken: Any = (), reserved: Any = None, scope=None) -> dict[str, Any]:
    """按当前插件状态重新收集工具及诊断，供派发表和请求声明同步刷新，无需重启。"""
    return gathered(root, taken=taken, reserved=reserved, scope=scope)


def switcher(root: Any, *, host: Any = None, scope=None) -> Any:
    """ 造一个"开/关"（`launch.py` 递给 `serve(switch_plugin=…)` 的那一个 ）。"""
    def act(name: str, on: bool) -> dict[str, Any]:
        return switch(root, name, on=on, host=host, scope=scope)
    return act


def reader(root: Any, *, scope=None) -> Any:
    """ **造一个读法**（`launch.py` 递给 `serve(read_plugins=…)` 的那一个 ）。

    ⚠️ **为什么要有这一层**：`web.py` **零同胞 import**（AST 钉着 ⇒ 它够不到本模块 ）
       ⇒ 与 `read_memory` ／ `read_usage` 同一条路：**由入口递** 。
    """
    def read() -> dict[str, Any]:
        return installed(root, scope=scope)
    return read


# ── 市场那一半（2026-10-05 作者："先做插件市场吧"）──────────────────────────
#
# ⚠️⚠️ **这是这一层第一次碰外网**（明写，不装作它一直在）：上面那些全是读本机 。
# 而**成法照本仓已经有的那一处抄**（`web.py::_catalog_sees` —— 它拉一份公开目录 ）：
# · `urllib` ＋ **设 `User-Agent`**（ 不设就是 `Python-urllib/3.x` —— 那一句是
# 2026-10-05 **量出来**的，写在 `web.py:440` ）；
# · `build_opener(ProxyHandler({}))`（ 绕开环境里那些代理 ）；
# · **今天这几样都归 `github.py` 自己**（作者 2026-10-05："删掉 npm 那一条路" ）——
# 它那一格 `ttl` 就是「刷页不该每次真打」那一条 。


#: **市场那一份缓存的落点**（`extensions/.market-cache.json` —— 与 `.origins.json`
#: 同一个地方：那是**这一层的账** ，而不是插件自己的东西 ）。
MARKET_CACHE = ".market-cache.json"


def market_cache(root: Any) -> Path:
    """ 市场那份缓存放哪儿（`<root>/extensions/.market-cache.json` ）。"""
    return the_place(root) / MARKET_CACHE


def warm_market(root: Any, *, proxy_root=None) -> None:
    """ **起来就先把那一份问一次**（2026-10-05 作者："重启之后插件市场那会先空着，
    有没有办法提前刷新好"）—— 它**不挡启动**（后台 daemon 线程 ），
    而页一打开时缓存已经是热的 。"""
    from . import github

    from .model_proxy import auxiliary_proxy
    proxy = auxiliary_proxy(proxy_root, 'market') if proxy_root is not None else None
    github.warm(market_cache(root), proxy) if proxy else github.warm(market_cache(root))


def every_market() -> dict[str, Any]:
    """ **市场那一段真正读的那一份**（作者 2026-10-05："删掉 npm 那一条路，不用管了"）。

     **今天只有一个来源：GitHub** —— 作者那句「直接放弃 npm 好了，转用 github」
       而**它要的那格话题**（`github.TOPIC` ＝ `core-infinity-plugin` ）**任何人都能打**
       ⇒ 那才是"**别人也能发、别人也能搜到**"那一半真正的落点 。

    ⚠️ **每一条标明它从哪儿来**（`source` ）—— 因为"装"那一条对来源**不一样**：
        今天只有 GitHub ⇒ `{"url": 归档链接}` ； 而"包名"那一路（`{"name": …}`）
       **属于 npm** ⇒ 它跟着那一条一起没了 。

    ⚠️ **那一格门槛照旧在**：装完由 `market` 那一层查包里有没有 `KEYWORD`
       —— 那一条与"发到哪儿"**无关**（它是**包里的**声明 ）。
    """
    from . import github

    return github.found(cache=_MARKET_CACHE_HERE[0])


#: ⚠️ 那一格是"这一次跑的时候，缓存落在哪儿"（ 由入口递一次 —— `web.py` 零同胞 import
#: 那条规矩的下游：市场这一层**够不到** `root` ⇒ 由 `every_market_reader(root)` 落 ）。
_MARKET_CACHE_HERE: list[Any] = [None]


def every_market_reader(root: Any = None, *, proxy_root=None) -> Any:
    """ 造一个读法（`launch.py` 递给 `serve(read_market=…)` 的那一个 ）。

     递了 `root` ⇒ **盘上那一份也接上**（ 重启之后立刻有东西可显示 ）。
    """
    if root is not None:
        _MARKET_CACHE_HERE[0] = market_cache(root)
    def read() -> dict[str, Any]:
        if proxy_root is None:
            return every_market()
        from . import github
        from .model_proxy import auxiliary_proxy, ProxySettingsError
        try:
            proxy = auxiliary_proxy(proxy_root, 'market')
        except ProxySettingsError as exc:
            return {"found": [], "wired": False, "said": str(exc)}
        return github.found(cache=market_cache(root), proxy=proxy)
    return read




# SOURCE FRAGMENT ONLY: appended to the existing canonical telyincore.plugins.
@_serialized_lifecycle
def assert_root_releasable(root: Any) -> None:
    """Cooperative registry gate, NOT native descendant or remote-drain proof.

    This checks retained/stopping/error rows, not only running active instances.
    No stop/retry/network/PID query is performed. Missing provenance is HOLD.
    """
    key = _root_key(root)
    _closing_roots.add(key)  # Sticky denial of new work, never a grant.
    def belongs_or_unknown(instance):
        owners = {row[1] for row in _lifecycle_roots.values() if row[0] is instance}
        return not owners or key in owners
    if any(belongs_or_unknown(instance) for instance in _loaded.values()):
        raise RuntimeError('registered plugin instances remain; lock release denied')
    with _async_calls_lock:
        if any(belongs_or_unknown(instance) for instance in _async_calls.values()):
            raise RuntimeError('plugin awaitable or dependency remains; lock release denied')
    if any(belongs_or_unknown(row[0]) for row in _lifecycle_threads.values()):
        raise RuntimeError('retained plugin stop/thread result unresolved; lock release denied')
    if any(belongs_or_unknown(row[0]) for row in _channel_leases.values()):
        raise RuntimeError('plugin channel lease remains; lock release denied')


def prepare_bound_system(name, operation, *args, expected_bot):
    """Host-only captured call; no legacy fallback or rediscovery after await."""
    items=[(n,i) for n,i in _active_items() if n==name]
    if len(items)!=1:raise RuntimeError('notification adapter unavailable')
    name,instance=items[0];entry=_system_entry(instance,operation)
    if entry is None:raise RuntimeError('notification system hook missing')
    captured=_prepare_system(name,instance,entry,*args)
    if captured is None or captured[1].bot!=expected_bot:
        raise RuntimeError('notification Bot/target mismatch')
    async def invoke():
        return await _run_system_prepared(name,instance,operation,captured)
    return invoke


def prepare_bound_qq_notice(batch, expected_bot):
    # Pin exact original QQ instance now; prepare text only after composition.
    import json
    from .batch_snapshot import freeze_batch
    frozen=tuple(json.loads(freeze_batch(lambda:batch)))
    items=[(n,i) for n,i in _active_items() if n=='core-infinity-qq']
    if len(items)!=1:raise RuntimeError('QQ notification adapter unavailable')
    name,instance=items[0];entry=_system_entry(instance,'output_tap')
    if entry is None:raise RuntimeError('QQ typed output required')
    async def deliver(text):
        if _loaded.get(name) is not instance:raise RuntimeError('QQ generation changed')
        captured=_prepare_system(name,instance,entry,frozen,text)
        if captured is None or captured[1].bot!=expected_bot:
            raise RuntimeError('QQ notice target no longer admitted')
        # Propagate failures. Generic output_taps intentionally hides optional
        # failures, which cannot be used as a notification delivery receipt.
        await _run_system_prepared(name,instance,'output_tap',captured)
    return deliver
