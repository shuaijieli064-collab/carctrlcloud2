# -*- coding: utf-8 -*-
"""
mqtt_protocol.py — 华为云 IoTDA 风格的协议映射（课程模块④：通信协议解析）

本模块定义"本地 MQTT broker（模拟 IoTDA 平台）"与"车端设备 / APP"之间的
命令与属性信封，是 TCP 直连协议与 IoT 平台协议之间的转换层。

## 华为云 IoTDA MQTT 约定（本地模拟版，字段与真实平台一致）
- 设备接入：MQTT 3.1.1，TLS 8883（本地模拟用明文 1883）
- 鉴权：   username = device_id
          password = Base64( HMAC-SHA256(secret, "HMAC-SHA256 {device_id} {timestamp}") )
          clientId = "{device_id}_{timestamp}"   （时间戳用于服务端校验防重放）
- 命令下发（平台 → 设备）：
    订阅： $oc/devices/{device_id}/sys/commands/#
    下发： $oc/devices/{device_id}/sys/commands/request_id={rid}/command_name={name}
    载荷： {"command_name":"move","paras":{"lx":0.5,"ly":-1.0}}
- 属性上报（设备 → 平台）：
    发布： $oc/devices/{device_id}/sys/properties/report
    载荷： {"services":[{"service_id":"Car",
            "properties":{"speed":1.0,"battery":99.0,"heading":90.0,"x":1.2,"y":1.0}}]}

## 与 TCP 直连协议的对照（同一套 lx/ly 语义）
  TCP  指令： {"type":"move","lx":0.5,"ly":-1.0,"seq":N}   →  IoT 命令： 上面的命令载荷
  TCP  状态： {"type":"state",...}                          →  IoT 属性： 上面的属性载荷
"""

import base64
import hashlib
import hmac
import json
import time

# ---------------- 主体注册表（本地模拟用；真实平台在 IoTDA 控制台创建） ----------------
# 设备与"平台应用（APP）"都用同一套 IoTDA 风格 HMAC 向平台鉴权。
# 真平台切换：在 IoTDA 控制台"产品→设备"中创建同名设备，把 secret 替换为平台分配的设备密钥。
DEVICE_REGISTRY = {
    "car_001": "secret_car_001",   # 模拟寻检车（设备侧）
}
APP_REGISTRY = {
    "app_controller": "secret_app",  # 模拟控制 APP（应用侧）
}

DEFAULT_DEVICE_ID = "car_001"
DEFAULT_SECRET = DEVICE_REGISTRY[DEFAULT_DEVICE_ID]
DEFAULT_APP_ID = "app_controller"
DEFAULT_APP_SECRET = APP_REGISTRY[DEFAULT_APP_ID]

# 服务与命令定义（对应 IoTDA 的"物模型"）
SERVICE_ID = "Car"
COMMAND_MOVE = "move"
COMMAND_STOP = "stop"
COMMAND_PING = "ping"
PROPERTY_KEYS = ("speed", "battery", "heading", "x", "y")


# ---------------- 鉴权：IoTDA 风格 HMAC 密码 ----------------
def make_iot_password(device_id: str, secret: str, timestamp: int | None = None) -> str:
    """生成 IoTDA 风格的接入密码（HMAC-SHA256 + Base64）。

    真实平台校验时亦用相同 secret 与设备携带的时间戳重算，做防重放比对。
    """
    ts = timestamp if timestamp is not None else int(time.time())
    content = f"HMAC-SHA256 {device_id} {ts}"
    digest = hmac.new(secret.encode("utf-8"), content.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def make_client_id(device_id: str, timestamp: int | None = None) -> str:
    """clientId 内嵌时间戳，供 broker 校验密码时还原 timestamp。"""
    ts = timestamp if timestamp is not None else int(time.time())
    return f"{device_id}_{ts}"


def parse_client_id(client_id: str) -> tuple[str, int | None]:
    """从 clientId 解析出 device_id 与 timestamp。"""
    if "_" in client_id:
        dev, _, ts = client_id.rpartition("_")
        try:
            return dev, int(ts)
        except ValueError:
            return client_id, None
    return client_id, None


def verify_password(principal_id: str, password: str, timestamp: int | None,
                    skew: int = 600) -> bool:
    """校验主体密码（HMAC 比对 + 时间窗防重放）。principal 可以是设备或应用。"""
    secret = DEVICE_REGISTRY.get(principal_id) or APP_REGISTRY.get(principal_id)
    if secret is None or timestamp is None:
        return False
    now = int(time.time())
    if abs(now - timestamp) > skew:
        return False
    expected = make_iot_password(principal_id, secret, timestamp)
    # 常量时间比较，避免时序侧信道
    return hmac.compare_digest(expected, password or "")


# ---------------- Topic 模板 ----------------
def command_sub_topic(device_id: str) -> str:
    """设备订阅：平台下发的命令（通配 request_id 与 command_name）。"""
    return f"$oc/devices/{device_id}/sys/commands/#"


def command_pub_topic(device_id: str, command_name: str, request_id: str | None = None) -> str:
    """APP/平台发布命令的目标 topic。request_id 唯一即可（用时间戳+随机数）。"""
    rid = request_id or f"req_{int(time.time() * 1000)}"
    return f"$oc/devices/{device_id}/sys/commands/request_id={rid}/command_name={command_name}"


def property_report_topic(device_id: str) -> str:
    """设备上报属性。"""
    return f"$oc/devices/{device_id}/sys/properties/report"


# ---------------- 命令 / 属性 信封编解码 ----------------
def encode_command(command_name: str, paras: dict | None = None) -> str:
    """构造 IoT 命令载荷（设备侧解析用同一结构）。"""
    payload = {"command_name": command_name, "paras": paras or {}}
    return json.dumps(payload, ensure_ascii=False)


def parse_command(topic: str, payload: str) -> dict | None:
    """解析一条到达的 IoT 命令。返回 {'name':..., 'paras':..., 'request_id':...} 或 None。

    topic 兼容两种形态（request_id 与 command_name 可能合在一段，也可能各占一段）：
      本地模拟： $oc/devices/{dev}/sys/commands/request_id={rid}/command_name={name}
      真平台：   .../commands/request_id={rid}/command_name={name}（分段或不分段均有）
    """
    try:
        seg = topic.split("/")
        if len(seg) < 6 or seg[1] != "devices" or seg[4] != "commands":
            return None
        request_id = None
        name = None
        for part in seg[5:]:
            if part.startswith("request_id="):
                request_id = part[len("request_id="):]
            elif part.startswith("command_name="):
                name = part[len("command_name="):]
        body = json.loads(payload)
        name = body.get("command_name", name)
        paras = body.get("paras", {})
        if name is None:
            return None
        return {"name": name, "paras": paras or {}, "request_id": request_id}
    except (ValueError, KeyError, IndexError):
        return None


def encode_property_report(snapshot: dict) -> str:
    """把内部 state 快照包装成 IoTDA 属性上报信封。"""
    props = {k: snapshot.get(k) for k in PROPERTY_KEYS if k in snapshot}
    envelope = {"services": [{"service_id": SERVICE_ID, "properties": props}]}
    return json.dumps(envelope, ensure_ascii=False)


def decode_property_report(payload: str) -> dict | None:
    """解析 IoTDA 属性上报信封 → 简单的 state 字典（供 APP / 俯视图使用）。"""
    try:
        envelope = json.loads(payload)
        for svc in envelope.get("services", []):
            if svc.get("service_id") == SERVICE_ID:
                return svc.get("properties", {})
        return None
    except ValueError:
        return None


# ---------------- 命令 → 内部动作 的转换（设备侧调用） ----------------
def apply_iot_command(car, command: dict) -> str:
    """把解析出的 IoT 命令施加到 CarState 模型，返回简短日志串。

    命令名按大小写不敏感匹配（真平台可能将命令名归一化为大写）。"""
    name = str(command["name"])
    key = name.lower()
    paras = command.get("paras", {})
    if key == COMMAND_MOVE:
        lx = paras.get("lx")
        ly = paras.get("ly")
        if not isinstance(lx, (int, float)) or not isinstance(ly, (int, float)):
            return "REJECT move 缺少数值型 lx/ly"
        car.apply_move(float(lx), float(ly))
        return f"move lx={lx:+.2f} ly={ly:+.2f}"
    elif key == COMMAND_STOP:
        car.apply_stop()
        return "stop"
    elif key == COMMAND_PING:
        return "ping"
    return f"UNKNOWN {name}"
