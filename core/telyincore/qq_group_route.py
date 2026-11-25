"""Bot-local rendezvous for native Waves commands and the active QQ transport."""
from threading import Lock

_lock = Lock()
_receivers = {}
_transports = {}


def _bind(table, owner, callback):
    with _lock:
        if not owner or owner in table:
            raise RuntimeError("QQ group route already bound or missing owner")
        table[owner] = callback

    def unbind():
        with _lock:
            if table.get(owner) is callback:
                del table[owner]
    return unbind


def bind_waves(owner, receive):
    return _bind(_receivers, owner, receive)


def bind_qq(owner, deliver):
    return _bind(_transports, owner, deliver)


def waves_receiver(owner):
    with _lock:
        return _receivers.get(owner)


def deliver_group(owner, account, group, segments, original_id=""):
    with _lock:
        deliver = _transports.get(owner)
    if deliver is None:
        raise RuntimeError("QQ group transport offline")
    return deliver(account, group, segments, original_id)
