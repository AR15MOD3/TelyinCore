"""Default private child teardown; cooperative plugin contract, not offline proof."""
import asyncio
from dataclasses import dataclass
from weakref import WeakValueDictionary
from bot_model_work import work_pending

_ingress_custody = {}  # Strong original refs, including assembly-failure paths.


@dataclass(frozen=True, eq=False)
class _RuntimeCompletion:
    """Identity receipt only; not permission to replay release or reopen work."""
    lifecycle: object
    ingress: object
    handle: object
    scope: object


# The lifecycle owns its receipt; this index does not pin retired lifecycles.
_runtime_completions = WeakValueDictionary()


def _runtime_completion(lifecycle):
    receipt = lifecycle.get('_runtime_completion')
    known = _runtime_completions.get(id(lifecycle))
    if receipt is None and known is None:
        return None
    if (type(receipt) is not _RuntimeCompletion or known is not receipt
            or receipt.lifecycle is not lifecycle
            or lifecycle.get('ingress_shutdown') is not receipt.ingress
            or lifecycle.get('handle') is not receipt.handle
            or lifecycle.get('scope') is not receipt.scope):
        raise RuntimeError('retired runtime identity changed')
    return receipt


def _cleanup_note(primary, label, error):
    # Formatting user-defined exceptions must not replace the primary failure.
    try:
        detail = str(error)
    except BaseException:
        detail = '<exception text unavailable>'
    BaseException.add_note(primary, label + ': ' + detail)


def bind_runtime_ingress(lifecycle, ingress):
    from telyincore.ingress_shutdown import IngressShutdown
    if (type(lifecycle) is not dict or id(lifecycle) in _ingress_custody
            or _runtime_completion(lifecycle) is not None):
        raise RuntimeError('ingress lifecycle already bound')
    if len(_ingress_custody) >= 16:
        raise RuntimeError('ingress custody capacity reached')
    owner = IngressShutdown(ingress)
    _ingress_custody[id(lifecycle)] = (lifecycle, owner)
    lifecycle['ingress_shutdown'] = owner
    return owner


def runtime_ingress(lifecycle):
    row = _ingress_custody.get(id(lifecycle))
    if row is None:
        if lifecycle.get('_runtime_completion') is not None:
            return _runtime_completion(lifecycle).ingress
        if lifecycle.get('ingress_shutdown') is not None:
            raise RuntimeError('foreign ingress owner')
        return None  # Assembly did not construct ingress.
    if row[0] is not lifecycle or lifecycle.get('ingress_shutdown') is not row[1]:
        raise RuntimeError('original ingress owner changed')
    return row[1]


def guard_ingress_release(lifecycle, *, primary=None):
    try:
        owner = runtime_ingress(lifecycle)
        if owner is not None:
            owner.begin()  # Seal only; private release never invents a wait budget.
            owner.assert_releasable()
        return True
    except BaseException as error:
        if primary is None:
            raise
        primary._ingress_release_error = error
        primary.add_note('ingress release denied; original resources retained')
        return False


def correlation_pending(owner):
    if owner is None:
        return False
    from telyincore_correlation_v2.custody import ResponsibilityOwner
    if type(owner) is not ResponsibilityOwner:
        raise ValueError('exact scoped correlation owner required')
    # Local tasks can be done while UNKNOWN native responsibility is retained.
    return owner.snapshot()['retained_records'] != 0


def assert_correlation_releasable(owner, *, primary=None):
    """Private pre-release barrier; never native admission or a release grant."""
    correlation_pending(owner)  # exact class check before touching supplied object
    if owner is not None:
        owner.finish_sync(primary=primary)


def root_owner(root, owner):
    if root is None:
        return owner
    from telyincore_correlation_v2.root import RootBinding
    if type(root) is not RootBinding or (owner is not None and root.owner is not owner):
        raise ValueError('exact matching root binding required')
    return root.owner


async def close_runtime(root, plugins, control, tick, *, drain_plugins, primary=None, tool_work=None, model_work=None, local_model=None, correlation_owner=None, correlation_root=None, ingress_owner=None):
    """Attempt every cleanup; never hide the original runtime failure.

    Default private instance children drain only loader-proven root instances.
    Explicit/internal assembly keeps its separate caller-owned lifecycle. Plugin
    stop is synchronous and may block: no bounded/full-descendant guarantee.
    """
    errors = []
    plugins_ok = not drain_plugins
    ingress_ok = ingress_owner is None
    predrain_ok = True
    original_resources = (root, plugins, control, correlation_root,
                          correlation_owner, model_work, tool_work, local_model)

    def predrain_failed(error):
        nonlocal predrain_ok
        predrain_ok = False
        errors.append(error)
        if ingress_owner is not None:
            for retain in (lambda: ingress_owner.retain_failure(error),
                           lambda: ingress_owner.retain(control),
                           lambda: ingress_owner.retain(original_resources)):
                try:
                    retain()
                except BaseException as secondary:
                    errors.append(secondary)

    if ingress_owner is not None:
        try:
            ingress_owner.begin()
        except BaseException as exc:
            predrain_failed(exc)
    try:
        correlation_owner = root_owner(correlation_root, correlation_owner)
        correlation_pending(correlation_owner)
    except BaseException as exc:
        predrain_failed(exc)
        # Do not invoke an unverified supplied root/owner as a cleanup consumer.
        correlation_owner = None
        correlation_root = None
    if correlation_root is not None:
        try:
            correlation_root.seal()
        except BaseException as exc:
            predrain_failed(exc)
    if local_model is not None:
        try:
            local_model.seal()
        except BaseException as exc:
            predrain_failed(exc)
    scopes = tuple(scope for scope in (model_work, tool_work, correlation_owner) if scope is not None)
    # Seal both before awaiting either: no tool/model admission during other drain.
    for scope in scopes:
        try:
            scope.seal()
        except BaseException as exc:
            predrain_failed(exc)
    for scope in scopes:
        try:
            await scope.close()
        except BaseException as exc:
            errors.append(exc)
    if ingress_owner is not None:
        try:
            ingress_owner.assert_releasable()
            ingress_ok = predrain_ok
        except BaseException as exc:
            errors.append(exc)
    if drain_plugins:
        try:
            if not predrain_ok or not ingress_ok or correlation_root is not None or correlation_pending(correlation_owner) or any(work_pending(scope) for scope in (model_work, tool_work)):
                plugins.fence_root(root)
                raise RuntimeError('plugin stop deferred: owned model/tool work still pending')
            result = plugins.shutdown_root(root)
            if result.get('ok') is not True:
                raise RuntimeError(f"plugin root shutdown unresolved: {result}")
            plugins_ok = True
        except BaseException as exc:
            errors.append(exc)
    if tick is not None:
        try:
            tick.cancel()
            await tick
        except asyncio.CancelledError:
            pass  # only this retained tick task, not a host-wide drain
        except BaseException as exc:
            errors.append(exc)
    try:
        if not predrain_ok or not ingress_ok or not plugins_ok:
            if ingress_owner is not None:
                ingress_owner.retain(control)
            raise RuntimeError('QQ control release deferred: ingress/plugin responsibility unresolved')
        control.close()
    except BaseException as exc:
        errors.append(exc)
    if local_model is not None:
        try:
            pending = not predrain_ok or not ingress_ok or correlation_root is not None or correlation_pending(correlation_owner) or any(work_pending(scope) for scope in scopes)
            await local_model.close(allow_stop=plugins_ok and not pending)
        except BaseException as exc:
            errors.append(exc)
    if errors:
        # Private receiver intentionally consumes its own cancellation as 130;
        # cleanup failure must instead escape, with cancellation retained as cause.
        cancelled = isinstance(primary, asyncio.CancelledError)
        chosen = primary if primary is not None and not cancelled else errors[0]
        if ingress_ok and predrain_ok:
            try:
                assert_correlation_releasable(correlation_owner, primary=chosen)
                if correlation_root is not None:
                    correlation_root.finish_sync(primary=chosen)
            except BaseException as error:
                errors.append(error)
                if ingress_owner is not None:
                    try:
                        ingress_owner.retain_failure(error)
                    except BaseException as secondary:
                        errors.append(secondary)
        chosen._runtime_cleanup_errors = tuple(errors)
        for error in errors:
            if error is not chosen:
                _cleanup_note(chosen, 'runtime cleanup failed', error)
        if cancelled:
            raise chosen from primary
        if primary is None:
            raise chosen
    elif correlation_root is not None:
        correlation_root.finish_sync(primary=primary)


def finish_default_root(root, plugins, *, primary=None, tool_work=None, model_work=None, correlation_owner=None, correlation_root=None, ingress_lifecycle=None):
    """Outer private-entry fallback, including synchronous assembly failures.

    Repeating a successful runtime drain is idempotent. Retained failed stops
    may be retried once here, but an original runtime failure is never cleared.
    Does not grant root/host authority or touch persistent enabled intent.
    """
    verified = False
    original_resources = (root, plugins, correlation_root, correlation_owner,
                          tool_work, model_work)
    try:
        if primary is not None and getattr(primary, '_runtime_cleanup_errors', ()):
            # The no-ingress internal path must not forget a previous failed seal.
            raise RuntimeError('prior runtime cleanup remains unresolved')
        if ingress_lifecycle is not None:
            if not guard_ingress_release(ingress_lifecycle):
                raise RuntimeError('ingress release denied')
        correlation_owner = root_owner(correlation_root, correlation_owner)
        correlation_pending(correlation_owner)
        verified = True
        if correlation_root is not None:
            correlation_root.seal()
        correlation_pending(correlation_owner)
        if correlation_owner is not None:
            correlation_owner.seal()
        if correlation_root is not None or correlation_pending(correlation_owner) or any(work_pending(scope) for scope in (model_work, tool_work)):
            plugins.fence_root(root)
            if correlation_root is not None:
                correlation_root.finish_sync()
            assert_correlation_releasable(correlation_owner)
            raise RuntimeError('default plugin stop deferred: owned model/tool work still pending')
        result = plugins.shutdown_root(root)
        if result.get('ok') is not True:
            raise RuntimeError(f"default plugin entry shutdown unresolved: {result}")
        return True
    except BaseException as error:
        # Persist responsibility BEFORE the no-primary/cancellation escape path.
        # Retention never invokes an unverified correlation root or owner.
        retention_errors = []
        if ingress_lifecycle is not None:
            row = _runtime_release_records.get(id(ingress_lifecycle))
            if row is not None and row['lifecycle'] is ingress_lifecycle:
                # Existing lock custody remains denied even if owner retention fails.
                row['faulted'], row['last_error'] = True, error
            try:
                original = runtime_ingress(ingress_lifecycle)
            except BaseException as secondary:
                retention_errors.append(secondary)
            else:
                if original is not None:
                    for retain in (lambda: original.retain_failure(error),
                                   lambda: original.retain(original_resources)):
                        try:
                            retain()
                        except BaseException as secondary:
                            retention_errors.append(secondary)
        carrier = error if primary is None else primary
        carrier._default_cleanup_error = error
        carrier._default_cleanup_retention_errors = tuple(retention_errors)
        if retention_errors:
            error._default_cleanup_retention_errors = tuple(retention_errors)
        if primary is None or isinstance(primary, asyncio.CancelledError):
            raise error from primary
        if ingress_lifecycle is not None:
            try:
                if not guard_ingress_release(ingress_lifecycle, primary=primary):
                    return False
            except BaseException as secondary:
                primary._default_cleanup_retention_error = secondary
                return False
        if not verified:
            # Keep evidence; never consume an unverified supplied root/owner.
            _cleanup_note(primary, 'default entry plugin cleanup failed', error)
            return False
        try:
            assert_correlation_releasable(correlation_owner, primary=primary)
            if correlation_root is not None:
                correlation_root.finish_sync(primary=primary)
        except BaseException as secondary:
            primary._default_cleanup_secondary = secondary
        _cleanup_note(primary, 'default entry plugin cleanup failed', error)
        return False


# SOURCE FRAGMENT ONLY: appended to the single existing bot_plugin_shutdown.py.
# Not a new Task/owner, native admission mechanism or independent cleanup engine.
_runtime_release_records = {}  # Exact original refs; no eviction/reset/automatic retry.
_RUNTIME_RELEASE_LIMIT = 16


def reserve_runtime_release(lifecycle, root, plugins, *, tool_work=None,
                            model_work=None, local_model=None, release):
    """Reserve bounded strong custody BEFORE acquiring a new instance lock."""
    from telyincore import plugins as actual_plugins
    from bot_tool_work import ToolWork
    from bot_model_work import ModelWork
    from bot_local_model_owner import LocalModelOwner
    if (type(lifecycle) is not dict or plugins is not actual_plugins
            or not callable(release)
            or (tool_work is not None and type(tool_work) is not ToolWork)
            or (model_work is not None and type(model_work) is not ModelWork)
            or (local_model is not None and type(local_model) is not LocalModelOwner)):
        raise ValueError('exact existing runtime references required for release custody')
    key = id(lifecycle)
    if _runtime_completion(lifecycle) is not None:
        raise RuntimeError('retired runtime lifecycle cannot be reused')
    if key in _runtime_release_records or len(_runtime_release_records) >= _RUNTIME_RELEASE_LIMIT:
        raise RuntimeError('runtime release custody unavailable before lock acquisition')
    _runtime_release_records[key] = dict(
        lifecycle=lifecycle, root=root, root_key=actual_plugins._root_key(root),
        plugins=plugins, tool_work=tool_work,
        model_work=model_work, local_model=local_model, release=release,
        scope=lifecycle.get('scope'),
        handle=None, acquire_entered=False, acquire_returned=False,
        release_attempted=False, released=False, faulted=False,
        finishing_scope=None, last_error=None)


def _runtime_release_record(lifecycle):
    row = _runtime_release_records.get(id(lifecycle))
    if row is None or row['lifecycle'] is not lifecycle:
        raise RuntimeError('original runtime release custody is missing')
    return row


def acquire_runtime_lock(lifecycle, acquire, *args):
    """Call the original acquisition once, keeping the original returned handle."""
    row = _runtime_release_record(lifecycle)
    if row['acquire_entered'] or not callable(acquire):
        raise RuntimeError('runtime lock acquisition is single use')
    row['acquire'], row['acquire_args'] = acquire, args
    row['acquire_entered'] = True
    try:
        row['handle'] = acquire(*args)
    except BaseException as error:
        row['faulted'], row['last_error'] = True, error
        raise
    row['acquire_returned'] = True
    lifecycle['handle'] = row['handle']
    if row['handle'] is None and row['scope'] is None:
        # No new handle and no sharing scope were acquired. This is NOT a drain
        # attestation or permission to stop/adopt anything in another instance.
        _runtime_release_records.pop(id(lifecycle), None)
    return row['handle']


def _check_local_model_release(owner):
    """Inspect existing custody only; never poll/stop a native process here.

    A direct child exit is insufficient; only retained birth-Job retirement
    can release native model responsibility. This is not host-offline proof.
    """
    if owner is None:
        return  # No LocalModelOwner was constructed; NOT a host-offline claim.
    from bot_local_model_owner import LocalModelOwner
    if type(owner) is not LocalModelOwner:
        raise RuntimeError('original local model owner identity is required')
    owner.seal()
    with owner._request_lock:
        requests = tuple(owner._requests)
    if (owner._fault or (owner._watch is not None and not owner._watch.done())
            or any(not request.done() for request in requests)):
        raise RuntimeError('local model/native responsibility is unconfirmed; release denied')
    if owner._process is not None or owner._history or owner._native_owners:
        # A direct child's exit remains insufficient. Windows native birth Jobs
        # supply separate, retained evidence for every owned subtree.
        owner.assert_native_retired()


def _check_runtime_release(row, scope):
    """Deny new work and inspect actual existing resources; never run cleanup."""
    from telyincore import plugins as actual_plugins
    from bot_tool_work import ToolWork
    from bot_model_work import ModelWork
    guard_ingress_release(row['lifecycle'])
    if (row['plugins'] is not actual_plugins or not row['acquire_returned']
            or actual_plugins._root_key(row['root']) != row['root_key']
            or row['faulted'] or row['lifecycle'].get('handle') is not row['handle']
            or row['lifecycle'].get('scope') is not row['scope']
            or scope is not row['scope']):
        row['faulted'] = True
        raise RuntimeError('original runtime release identity or acquisition is unresolved')
    for name, expected in [('tool_work', ToolWork), ('model_work', ModelWork)]:
        work = row[name]
        if work is not None:
            if type(work) is not expected:
                row['faulted'] = True
                raise RuntimeError('runtime work identity changed before release')
            work.seal()  # No fresh tool/model admission while checking release.
    _check_local_model_release(row['local_model'])
    actual_plugins.fence_root(row['root'])
    if any(work_pending(work) for work in (row['tool_work'], row['model_work'])):
        raise RuntimeError('actual tool/model/stream responsibility remains pending')
    # Provider checks all retained rows, NOT just running _active_items().
    actual_plugins.assert_root_releasable(row['root'])


def _retain_release_error(row, error, primary):
    row['faulted'] = True
    row['last_error'] = error  # row still strongly retains exact handles/scopes.
    if primary is not None and not isinstance(primary, asyncio.CancelledError):
        primary.add_note('runtime release blocked; original resources retained')
        return False
    raise error from primary


def _prepare_runtime_completion(row):
    # Single baseline for the whole finish operation, never refreshed by an
    # inner lock callback or after failed cleanup.
    if '_completion_candidate' in row or '_completion_custody' in row:
        raise RuntimeError('runtime completion baseline already fixed')
    lifecycle = row['lifecycle']
    row['_completion_candidate'] = _RuntimeCompletion(
        lifecycle, runtime_ingress(lifecycle), row['handle'], row['scope'])
    row['_completion_custody'] = _ingress_custody.get(id(lifecycle))
    _check_completion_identity(row)


def _check_completion_identity(row):
    completion = row['_completion_candidate']
    lifecycle = completion.lifecycle
    # Pure identity check, usable before and after callbacks. Not a thread lock.
    custody = row['_completion_custody']
    if (row['lifecycle'] is not lifecycle
            or _runtime_release_records.get(id(lifecycle)) is not row
            or row['faulted']
            or row['handle'] is not completion.handle
            or row['scope'] is not completion.scope
            or lifecycle.get('handle') is not completion.handle
            or lifecycle.get('scope') is not completion.scope
            or lifecycle.get('ingress_shutdown') is not completion.ingress
            or _ingress_custody.get(id(lifecycle)) is not custody
            or (custody is not None and (custody[0] is not lifecycle
                                        or custody[1] is not completion.ingress))
            or (custody is None and completion.ingress is not None)
            or lifecycle.get('_runtime_completion') is not None
            or _runtime_completions.get(id(lifecycle)) is not None):
        raise RuntimeError('runtime identity changed before completion publication')
    return completion


def _publish_runtime_completion(row):
    completion = _check_completion_identity(row)
    if not row['released'] or not row['release_attempted']:
        raise RuntimeError('runtime release not confirmed before completion')
    lifecycle = completion.lifecycle
    _runtime_completions[id(lifecycle)] = completion
    lifecycle['_runtime_completion'] = completion
    _runtime_release_records.pop(id(lifecycle), None)
    _ingress_custody.pop(id(lifecycle), None)


def release_runtime_lock(lifecycle, handle, *, primary=None, _scope=None):
    """Pre-unlock barrier. Does not cancel/join/stop, mint an owner or retry."""
    row = _runtime_release_record(lifecycle)
    try:
        if handle is not row['handle']:
            row['faulted'] = True
            raise RuntimeError('runtime lock handle changed before release')
        if row['release_attempted']:
            raise RuntimeError('runtime lock release result is not retryable')
        if _scope is not None and row['finishing_scope'] is not _scope:
            row['faulted'] = True
            raise RuntimeError('sharing release lacks its original finish consumer')
        if _scope is None:
            _prepare_runtime_completion(row)
        else:
            # Shared baseline belongs to the outer finish consumer. Missing or
            # changed identity is a failure, never an opportunity to recapture.
            _check_completion_identity(row)
        _check_runtime_release(row, _scope)
        _check_completion_identity(row)
        row['release_attempted'] = True  # Before raw callback, never retry UNKNOWN.
        if handle is not None:
            row['release'](handle)
        row['released'] = True
        if _scope is None:
            _publish_runtime_completion(row)
        return True  # Only this lock callback; NOT native or model drain proof.
    except BaseException as error:
        return _retain_release_error(row, error, primary)


def finish_runtime_release(lifecycle, *, primary=None):
    """Sole non-correlation outer release consumer, after every outer cleanup."""
    row = _runtime_release_records.get(id(lifecycle))
    try:
        if row is None:
            if _runtime_completion(lifecycle) is not None:
                guard_ingress_release(lifecycle)
                return True  # Observe prior completion; never invoke raw release.
            if any(lifecycle.get(key) is not None
                   for key in ('handle', 'scope', 'ingress_shutdown')):
                raise RuntimeError('runtime custody missing at final release')
            return True  # No acquisition/owner existed; not a drain attestation.
        if row['lifecycle'] is not lifecycle:
            raise RuntimeError('foreign runtime final release')
        if row['scope'] is not None:
            return finish_runtime_scope(lifecycle, row['scope'], primary=primary)
        return release_runtime_lock(lifecycle, row['handle'], primary=primary)
    except BaseException as error:
        if row is not None:
            return _retain_release_error(row, error, primary)
        if primary is not None and not isinstance(primary, asyncio.CancelledError):
            primary._runtime_final_release_error = error
            _cleanup_note(primary, 'runtime final release unresolved', error)
            return False
        raise error from primary


def finish_runtime_scope(lifecycle, scope, *, primary=None):
    """Gate the existing HostAssembly.finish before factory/lock release."""
    from functools import partial
    from telyincore.sharing_bridge.assembly import HostAssembly
    row = _runtime_release_record(lifecycle)
    try:
        if row['release_attempted'] or row.get('_finish_attempted', False):
            raise RuntimeError('sharing release result is not retryable')
        if type(scope) is not HostAssembly or row['scope'] is not scope:
            row['faulted'] = True
            raise RuntimeError('exact existing sharing scope required at finish')
        _prepare_runtime_completion(row)
        _check_runtime_release(row, scope)  # BEFORE mount/factory close too.
        _check_completion_identity(row)
        row['finishing_scope'] = scope
        try:
            row['_finish_attempted'] = True  # Before finish, including no handle.
            scope.finish(handle=row['handle'],
                release=partial(release_runtime_lock, lifecycle, _scope=scope),
                primary=primary)
            _check_completion_identity(row)
            if scope.token in scope.retained:
                raise RuntimeError('sharing cleanup remains unresolved; release retained')
            if row['handle'] is None and not row['released']:
                # HostAssembly intentionally does not call release(None).
                release_runtime_lock(lifecycle, None, _scope=scope)
            if not row['released']:
                raise RuntimeError('original lock release was not observed')
            _publish_runtime_completion(row)
            return True
        finally:
            row['finishing_scope'] = None
    except BaseException as error:
        return _retain_release_error(row, error, primary)
