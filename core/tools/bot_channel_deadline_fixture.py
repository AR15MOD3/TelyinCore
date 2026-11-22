"""Hostile framing demo: NEW test pipe and self-created Python worker only."""
import json
import multiprocessing
import struct
import time

from bot_channel_transport import DeadlinePipe, MAX_FRAME


def worker(connection, ready, release, mode):
    frames = {'partial-header': b'\x00\x00',
              'partial-body': struct.pack('!I', 40) + b'x',
              'oversize': struct.pack('!I', MAX_FRAME + 1) + b'x' * (MAX_FRAME + 1)}
    try:
        if mode != 'idle':
            connection.send_bytes(frames[mode])
        ready.set()
        release.wait(timeout=10)
    finally:
        connection.close()


def demo(mode='partial-header'):
    if mode not in ('idle', 'partial-header', 'partial-body', 'oversize'):
        raise ValueError('unknown synthetic attack')
    context = multiprocessing.get_context('spawn')
    parent, child = context.Pipe(duplex=True)
    ready, release = context.Event(), context.Event()
    process = context.Process(target=worker, args=(child, ready, release, mode))
    transport = DeadlinePipe(parent)
    started = False
    try:
        process.start(); started = True; child.close()
        if not ready.wait(timeout=5):
            raise ValueError('test worker unavailable')
        before = time.monotonic()
        try:
            transport.recv_frame(before + 0.15)
        except (ValueError, OSError, TimeoutError):
            pass
        else:
            raise AssertionError('invalid/stalled frame accepted')
        elapsed = time.monotonic() - before
        assert elapsed < 0.75 and process.is_alive() and transport.closed
        release.set(); process.join(timeout=10)
        assert process.exitcode == 0
        return {'fixture_only': True, 'mode': mode, 'rejected': True,
                'io_seconds': elapsed, 'peer_alive_at_rejection': True,
                'transport_closed': True, 'production_connected': False}
    finally:
        transport.close(); child.close(); release.set()
        if started:
            process.join(timeout=10)
            if process.is_alive():
                process.terminate(); process.join(timeout=10)
            process.close()


if __name__ == '__main__':
    print(json.dumps([demo(mode) for mode in
        ('idle', 'partial-header', 'partial-body', 'oversize')]))
