"""Validate a resolved management-API config without changing either service."""
import ctypes
import os
import socket

from typing import Any
from urllib.parse import urlsplit


def connection_settings(config: Any) -> dict[str, str]:
    """Only import an unambiguous pair of authenticated loopback listeners.

    The input must be GET /api/config/:uin's resolved config, not a raw overlay.
    Returned tokens are private deployment settings, never a frontend response.
    """
    networks = config.get("networks") if isinstance(config, dict) else None
    if not isinstance(networks, dict):
        raise ValueError("管理端未返回有效的 OneBot 网络配置")

    def listener(kind: str, scheme: str) -> tuple[str, str]:
        rows = networks.get(kind)
        if not isinstance(rows, list):
            raise ValueError("OneBot 网络节点列表无效")
        active = [row for row in rows if isinstance(row, dict)
                  and row.get("enabled", True) is not False]
        if len(active) != 1:
            raise ValueError("需要唯一的网络节点；请先在管理端明确配置")
        row = active[0]
        host, port = row.get("host"), row.get("port")
        if host != "127.0.0.1":
            raise ValueError("仅导入健康探测支持的 127.0.0.1 根路径监听；不改写上游地址")
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("OneBot 端口无效")
        path = row.get("path", "/")
        if path != "/":
            raise ValueError("仅支持根路径监听；不会剥离路径或改写配置")
        token = row.get("accessToken")
        if not isinstance(token, str) or not token.strip() or any(
                c.isspace() or ord(c) < 32 or ord(c) > 126 for c in token):
            raise ValueError("节点必须配置非空有效令牌；不会关闭鉴权")
        if row.get("messageFormat", "array") != "array":
            raise ValueError("消息格式需设为 array")
        if kind == "wsServers" and row.get("role", "Universal") not in ("Universal", "Event"):
            raise ValueError("WebSocket 节点必须支持事件推送")
        authority = f"[{host}]" if host == "::1" else host
        url = f"{scheme}://{authority}:{port}{path}"
        if urlsplit(url).hostname != host:
            raise ValueError("OneBot 地址无效")
        return url.rstrip("/"), token

    http, token = listener("httpServers", "http")
    ws, ws_token = listener("wsServers", "ws")
    if urlsplit(http).port == urlsplit(ws).port:
        raise ValueError("HTTP 与 WebSocket 必须使用不同端口")
    return {"httpUrl": http, "token": token, "wsUrl": ws, "wsToken": ws_token}


class _TcpOwnerRow(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in (
        "state", "local_addr", "local_port", "remote_addr", "remote_port", "pid")]


class _TcpOwnerTable(ctypes.Structure):
    _fields_ = [("count", ctypes.c_uint32), ("rows", _TcpOwnerRow * 1)]


def _management_listeners(raw: bytes, port: int = 5099) -> list[dict] | None:
    """Decode a bounded IPv4 PID-listener snapshot; malformed means UNKNOWN."""
    offset, stride = _TcpOwnerTable.rows.offset, ctypes.sizeof(_TcpOwnerRow)
    if len(raw) < offset or type(port) is not int or not 1 <= port <= 65535:
        return None
    count = ctypes.c_uint32.from_buffer_copy(raw[:4]).value
    if count > 65536 or offset + count * stride > len(raw):
        return None
    found = []
    for i in range(count):
        row = _TcpOwnerRow.from_buffer_copy(raw[offset + i * stride:offset + (i + 1) * stride])
        if row.state != 2 or row.local_port >> 16:
            return None
        if socket.ntohs(row.local_port) == port:
            if not row.pid:
                return None
            address = socket.inet_ntoa(bytes(ctypes.c_uint32(row.local_addr)))
            found.append({"address": address, "pid": row.pid})
    return found


def management_listener_owners(port: int = 5099, *, query=None) -> list[dict] | None:
    """Local OS snapshot only. Never TCP/HTTP-connect or infer from an API body.

    Production uses GetExtendedTcpTable(AF_INET, OWNER_PID_LISTENER). Query
    injection is exclusively for deterministic native-ABI tests. No shared
    service adoption, IPv6 fallback or wildcard-listener trust is implied.
    """
    try:
        if query is None:
            if os.name != "nt":
                return None
            query = ctypes.WinDLL("iphlpapi", use_last_error=True).GetExtendedTcpTable
            query.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32),
                              ctypes.c_int, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
            query.restype = ctypes.c_uint32
        size = ctypes.c_uint32()
        if query(None, ctypes.byref(size), False, socket.AF_INET, 3, 0) != 122:
            return None
        for _ in range(3):
            if not 4 <= size.value <= 4 * 1024 * 1024:
                return None
            capacity = size.value
            data = ctypes.create_string_buffer(capacity)
            rc = query(data, ctypes.byref(size), False, socket.AF_INET, 3, 0)
            if rc == 122:
                continue
            if rc != 0 or not 4 <= size.value <= capacity:
                return None
            return _management_listeners(data.raw[:size.value], port)
    except (OSError, ValueError, TypeError, OverflowError, ctypes.ArgumentError):
        return None
    return None
