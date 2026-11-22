"""Detached, read-only JSON containers with dict/list wire compatibility."""

def _immutable(*args, **kwargs):
    raise TypeError('event JSON is immutable')


class FrozenDict(dict):
    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _immutable

    def __deepcopy__(self, memo):
        return self


class FrozenList(list):
    __setitem__ = __delitem__ = append = clear = extend = insert = pop = remove = reverse = sort = __iadd__ = __imul__ = _immutable

    def __deepcopy__(self, memo):
        return self


def freeze(value, active=None):
    if not isinstance(value, (dict, list, tuple)):
        return value
    active = set() if active is None else active
    identity = id(value)
    if identity in active:
        raise ValueError('circular event JSON')
    active.add(identity)
    try:
        if isinstance(value, dict):
            return FrozenDict((key, freeze(item, active)) for key, item in value.items())
        if isinstance(value, list):
            return FrozenList(freeze(item, active) for item in value)
        return tuple(freeze(item, active) for item in value)
    finally:
        active.remove(identity)


def thaw(value):
    """Return ordinary detached containers for the public observation snapshot."""
    if isinstance(value, dict):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, list):
        return [thaw(item) for item in value]
    if isinstance(value, tuple):
        return tuple(thaw(item) for item in value)
    return value
