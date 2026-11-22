"""Host persistence calls whose caller retains responsibility until thread exit.

Only thread-safe host file operations belong here, not arbitrary plugin callbacks.
Cancellation does not cancel the physical write, start it twice, or detach it.
The owning task remains pending so its public bounded drain can report incomplete.
"""
from __future__ import annotations
import asyncio
import contextvars
import threading


async def file_call(operation, /, *args, _cleanup=False, **kwargs):
    task = asyncio.current_task()
    if task is not None and task.cancelling() and not _cleanup:
        raise asyncio.CancelledError()
    loop = asyncio.get_running_loop()
    arrived = loop.create_future()
    box = {}
    context = contextvars.copy_context()

    def notify():
        if not arrived.done():
            arrived.set_result(None)

    def work():
        try:
            box['value'] = context.run(operation, *args, **kwargs)
        except BaseException as error:
            box['error'] = error
        finally:
            loop.call_soon_threadsafe(notify)

    worker = threading.Thread(target=work, name='core-retained-persistence', daemon=False)
    worker.start()
    primary = None
    while not arrived.done() or worker.is_alive():
        try:
            await asyncio.shield(arrived)
            if worker.is_alive():
                await asyncio.sleep(0)
        except asyncio.CancelledError as error:
            if primary is None:
                primary = error
    if primary is not None:
        if 'error' in box:
            primary.persistence_error = box['error']
            primary.add_note('cancelled persistence operation also failed; original error retained')
        raise primary
    if 'error' in box:
        raise box['error']
    return box.get('value')
