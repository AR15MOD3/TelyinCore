"""Bounded, private Linux lifecycle frames over an inherited Unix socket pair.

The caller owns both endpoints at creation and must inherit only the intended
child endpoint. There is no named socket, listener, or process authority here.
"""
import math
import select
import socket
import struct
import sys
import time

from bot_channel_transport import MAX_FRAME
from physical_close import close_after_failure, close_once


class LinuxLifecycleSocket:
    def __init__(self, endpoint: socket.socket):
        if (sys.platform != "linux" or endpoint.family != socket.AF_UNIX
                or endpoint.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_SEQPACKET
                or endpoint.getsockname() != "" or endpoint.getpeername() != ""):
            raise ValueError("anonymous Linux sequence-packet socket required")
        endpoint.set_inheritable(False)
        endpoint.setblocking(False)
        self.endpoint = endpoint
        self.closed = False

    @classmethod
    def from_fd(cls, fd: int):
        if type(fd) is not int or fd < 3:
            raise ValueError("private inherited socket descriptor required")
        endpoint = socket.socket(fileno=fd)
        try:
            return cls(endpoint)
        except BaseException:
            endpoint.close()
            raise

    def fileno(self) -> int:
        return self.endpoint.fileno()

    def _remaining(self, deadline: float) -> float:
        if self.closed:
            raise ValueError("lifecycle socket closed")
        if type(deadline) not in (int, float) or not math.isfinite(deadline):
            raise ValueError("invalid lifecycle deadline")
        remaining = deadline - time.monotonic()
        if not 0 < remaining <= 5.001:
            raise TimeoutError("lifecycle socket deadline expired or invalid")
        return remaining

    def ready(self) -> bool:
        if self.closed:
            raise ValueError("lifecycle socket closed")
        with _select_epoll(self.endpoint, select.EPOLLIN) as poller:
            return bool(poller.poll(0))

    def _exchange(self, deadline, *, packet=None):
        reading = packet is None
        events = select.EPOLLIN if reading else select.EPOLLOUT
        with _select_epoll(self.endpoint, events) as poller:
            while True:
                if not poller.poll(self._remaining(deadline), 1):
                    raise TimeoutError("lifecycle socket IO deadline")
                try:
                    if reading:
                        result = self.endpoint.recvmsg(MAX_FRAME + 4)
                    else:
                        result = self.endpoint.send(packet)
                except BlockingIOError:
                    continue
                self._remaining(deadline)
                return result

    def send_frame(self, raw: bytes, deadline: float) -> None:
        try:
            if type(raw) is not bytes or not 0 < len(raw) <= MAX_FRAME:
                raise ValueError("invalid lifecycle frame")
            packet = struct.pack("!I", len(raw)) + raw
            if self._exchange(deadline, packet=packet) != len(packet):
                raise OSError("partial lifecycle packet write")
        except BaseException as primary:
            close_after_failure(self, primary)
            raise

    def recv_frame(self, deadline: float) -> bytes:
        try:
            packet, _, flags, _ = self._exchange(deadline)
            if flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC) or len(packet) < 4:
                raise ValueError("closed, truncated, or oversized lifecycle packet")
            size, = struct.unpack("!I", packet[:4])
            if not 0 < size <= MAX_FRAME or len(packet) != size + 4:
                raise ValueError("invalid lifecycle packet length")
            return packet[4:]
        except BaseException as primary:
            close_after_failure(self, primary)
            raise

    def close(self) -> None:
        close_once(self, self.endpoint.close)


def _select_epoll(endpoint, events):
    # epoll avoids select()'s FD_SETSIZE limit on busy desktop processes.
    poller = select.epoll()
    try:
        poller.register(endpoint, events)
    except BaseException:
        poller.close()
        raise
    return poller


def linux_lifecycle_pair():
    if sys.platform != "linux":
        raise ValueError("Linux lifecycle transport unavailable")
    parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    try:
        return LinuxLifecycleSocket(parent), LinuxLifecycleSocket(child)
    except BaseException:
        parent.close()
        child.close()
        raise
