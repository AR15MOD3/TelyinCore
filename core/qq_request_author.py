"""Bind a plugin operation to the author of a currently admitted QQ message batch."""
import re


def verified_request(console, channels, event_is_current):
    if console is None or channels is None:
        return None
    lease = console.current_batch_identity()
    if lease is None:
        return None
    events = console.original_batch()
    if not events:
        return None
    with channels.lock:
        generation = channels.active.get('qq')
    if not generation:
        return None
    identities = set()
    for event in events:
        if event.origin != 'external' or event.source != 'qq' or event.type != 'message':
            return None
        context = channels.select((event,))
        if context.provider != 'core-infinity-qq' or context.status != 'plugin':
            return None
        meta = event.meta or {}
        receipt = meta.get('_host_channel_context_v1') or {}
        if receipt.get('lease') != generation:
            return None
        author = meta.get('user_id')
        room = meta.get('conversation', '')
        if (not isinstance(author, str) or not re.fullmatch(r'[1-9][0-9]*', author)
                or event.sender_key != 'qq:' + author or not meta.get('message_id')
                or not isinstance(room, str)):
            return None
        group = meta.get('group_id')
        private = room == 'private:' + author and group is None
        grouped = isinstance(group, str) and re.fullmatch(r'[1-9][0-9]*', group) and room == 'group:' + group
        if not (private or grouped):
            return None
        identities.add((author, room))
    if len(identities) != 1:
        return None

    def current():
        with channels.lock:
            active = channels.active.get('qq') == generation
        return active and console.current_batch_identity() is lease and all(event_is_current(event) for event in events)

    if current():
        author, _ = identities.pop()
        return author, current
    return None
