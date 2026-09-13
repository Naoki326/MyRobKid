"""在线设备注册表

记录当前通过 WebSocket 在线的设备（device_id -> ConnectionHandler），
供 HTTP 管理接口（如 /xiaozhi/ota/reboot 远程重启设备）查找连接。
HTTP 服务与 WebSocket 服务运行在同一个 asyncio 事件循环中，
因此可以安全地对查到的连接调用 websocket.send()。
"""

import threading

_lock = threading.Lock()
_connections = {}  # device_id -> ConnectionHandler
# device_id -> {"model": str, "version": str}
# 设备**自报**的型号与固件版本。数据源只有一处：设备开机自检打 OTA 接口时
# 带的 body（``board.type`` / ``application.version``，见 ota_handler）。
# WebSocket 握手头里**没有**这两项（固件只发 Protocol-Version / Device-Id /
# Client-Id / Authorization），hello 报文里的 ``version`` 是**协议版本**也不是
# 固件版本——所以不能从连接上读，只能把 OTA 那次上报记下来。
_reported = {}  # device_id -> {"model": str, "version": str}


def register(device_id, handler):
    """设备连接建立时注册（同一设备重连时覆盖旧记录）"""
    if not device_id:
        return
    with _lock:
        _connections[device_id] = handler


def record_device_info(device_id, model=None, version=None):
    """记下设备自报的型号 / 固件版本（OTA 自检时调用）。

    空值不覆盖已有的非空值：设备的 OTA 请求偶尔只有头没有 body（或反之），
    那一次缺的字段不该把上一次报到的值擦掉。
    """
    if not device_id:
        return
    with _lock:
        info = dict(_reported.get(device_id) or {})
        if model:
            info["model"] = str(model)
        if version:
            info["version"] = str(version)
        if info:
            _reported[device_id] = info


def get_device_info(device_id):
    """返回该设备自报的 {"model", "version"}（取不到就是空串）。"""
    with _lock:
        info = dict(_reported.get(device_id) or {})
    return {
        "model": info.get("model", "") or "",
        "version": info.get("version", "") or "",
    }


def unregister(device_id, handler):
    """设备断开时注销（仅当记录仍是同一个 handler 时才删除）。

    **不清** ``_reported``：型号/版本是设备的静态属性（MAC 不变则不变），而设备
    为重启/升级会频繁断连重连。清了就得等下一次开机 OTA 自检才补回来，中间那段
    窗口里页面拿到的设备行会是空的 model/version，版本对比退回「库中无更新」。
    代价是 ``_reported`` 只增不减；家用部署 MAC 数量有限，不计较。

    注：``_reported`` 只在 ``get_online()`` 的循环里被读，所以它的收益是
    「**重连后**、下次 OTA 自检前仍能拿到型号/版本」，不是「离线设备也判得准」
    ——离线设备不在 ``api/devices`` 载荷里。
    """
    with _lock:
        if _connections.get(device_id) is handler:
            del _connections[device_id]


def get_online(device_id=None):
    """返回指定设备的 handler，或全部在线设备 {device_id: handler} 的副本"""
    with _lock:
        if device_id:
            return _connections.get(device_id)
        return dict(_connections)
