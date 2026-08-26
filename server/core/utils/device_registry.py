"""在线设备注册表

记录当前通过 WebSocket 在线的设备（device_id -> ConnectionHandler），
供 HTTP 管理接口（如 /xiaozhi/ota/reboot 远程重启设备）查找连接。
HTTP 服务与 WebSocket 服务运行在同一个 asyncio 事件循环中，
因此可以安全地对查到的连接调用 websocket.send()。
"""

import threading

_lock = threading.Lock()
_connections = {}  # device_id -> ConnectionHandler


def register(device_id, handler):
    """设备连接建立时注册（同一设备重连时覆盖旧记录）"""
    if not device_id:
        return
    with _lock:
        _connections[device_id] = handler


def unregister(device_id, handler):
    """设备断开时注销（仅当记录仍是同一个 handler 时才删除）"""
    with _lock:
        if _connections.get(device_id) is handler:
            del _connections[device_id]


def get_online(device_id=None):
    """返回指定设备的 handler，或全部在线设备 {device_id: handler} 的副本"""
    with _lock:
        if device_id:
            return _connections.get(device_id)
        return dict(_connections)
