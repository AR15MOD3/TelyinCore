"""Separate admission revocation from verified resource close, without replay.

Exceptions and owners stay in memory; inspection must expose only booleans,
not raw exception text or resource contents. No service/process operations.
"""

def close_once(owner, close):
    owner.closed = True
    if getattr(owner, '_physical_close_attempted', False):
        error = getattr(owner, 'close_error', None)
        if error is not None:
            raise error
        if not getattr(owner, 'close_complete', False):
            raise RuntimeError('physical close still in progress')
        return
    owner._physical_close_attempted = True
    owner.close_complete = False
    owner.close_error = None
    try:
        result = close()
        if result is not None:
            owner._physical_close_result = result
            raise TypeError('synchronous resource close must return None')
    except BaseException as error:
        owner.close_error = error
        BaseException.__setattr__(error, 'cleanup_owner', owner)
        raise
    owner.close_complete = True


def close_after_failure(owner, primary):
    """Never replace a primary error/cancellation with a cleanup failure."""
    try:
        owner.close()
    except BaseException as secondary:
        try:errors = BaseException.__getattribute__(primary, 'cleanup_errors')
        except AttributeError:errors = ()
        if not isinstance(errors, tuple):errors = ()
        if secondary is not primary and not any(e is secondary for e in errors):
            BaseException.__setattr__(primary, 'cleanup_errors', errors + (secondary,))
        BaseException.__setattr__(primary, 'cleanup_owner', owner)
