# -*- coding: utf-8 -*-
"""
mqtt_broker.py — 本地 MQTT broker（模拟华为云 IoTDA 平台，课程模块④）

用 Python 标准库实现 MQTT 3.1.1 的最小可用子集，作为"平台"承接：
  - 设备接入鉴权：username=device_id，password=IoTDA 风格 HMAC，clientId 内嵌时间戳
  - 发布/订阅路由：支持 '+' 与 '#' 通配符
  - 命令下发（APP→平台→设备）与属性上报（设备→平台）
  - WebSocket 桥（:8899）：把属性上报翻译成简单 state JSON 推给浏览器俯视图

仅使用标准库，无第三方依赖。真实对接时把本 broker 换成 IoTDA 实例即可（端点/端口/TSL/鉴权见 README）。

用法：
  python mqtt_broker.py                # MQTT:1883  + 俯视图桥:8899
  python mqtt_broker.py --port 1883 --ws-port 8899
"""

import argparse
import base64
import hashlib
import json
import os
import socket
import struct
import threading
import time

import mqtt_protocol as proto


# ===================== MQTT 3.1.1 编解码工具 =====================
def encode_remaining_length(length: int) -> bytes:
    enc = b""
    while True:
        digit = length % 128
        length //= 128
        if length > 0:
            digit |= 0x80
        enc += bytes([digit])
        if length == 0:
            break
    return enc


def decode_remaining_length(buf: bytes, pos: int):
    multiplier = 1
    value = 0
    while pos < len(buf):
        b = buf[pos]
        pos += 1
        value += (b & 0x7F) * multiplier
        if (b & 0x80) == 0:
            break
        multiplier *= 128
        if multiplier > 128 ** 4:
            raise ValueError("malformed remaining length")
    return value, pos


def read_string(buf: bytes, pos: int):
    if pos + 2 > len(buf):
        raise ValueError("truncated string")
    length = (buf[pos] << 8) | buf[pos + 1]
    pos += 2
    s = buf[pos:pos + length].decode("utf-8")
    pos += length
    return s, pos


def write_string(s: str) -> bytes:
    b = s.encode("utf-8")
    return bytes([len(b) >> 8, len(b) & 0xFF]) + b


# ===================== 数据包构造 =====================
def build_connack(code: int) -> bytes:
    # 0x20=CONNACK, remaining=2, [ack flags=0, return code]
    return bytes([0x20, 0x02, 0x00, code & 0xFF])


def build_suback(pkt_id: int, codes: list) -> bytes:
    body = bytes([pkt_id >> 8, pkt_id & 0xFF]) + bytes(codes)
    return bytes([0x90, len(body)]) + body


def build_puback(pkt_id: int) -> bytes:
    return bytes([0x40, 0x02, pkt_id >> 8, pkt_id & 0xFF])


def build_pingresp() -> bytes:
    return bytes([0xD0, 0x00])


def build_publish(topic: str, payload: bytes) -> bytes:
    body = write_string(topic) + payload
    return bytes([0x30]) + encode_remaining_length(len(body)) + body


def topic_matches(filt: str, topic: str) -> bool:
    """MQTT topic 过滤器匹配（支持 + 单级、# 多级通配）"""
    f = filt.split("/")
    t = topic.split("/")
    for i, level in enumerate(f):
        if level == "#":
            return True
        if i >= len(t):
            return False
        if level != "+" and level != t[i]:
            return False
    return len(f) == len(t)


# ===================== 连接对象 =====================
class Conn:
    def __init__(self, sock, addr):
        self.sock = sock
        self.addr = addr
        self.client_id = None
        self.device_id = None
        self.subscriptions = set()
        self.last_active = time.time()
        self.alive = True

    def send(self, data: bytes):
        try:
            self.sock.sendall(data)
        except OSError:
            self.alive = False


CONNECTIONS = []
CONN_LOCK = threading.Lock()


# ===================== 发布路由 =====================
def route_publish(topic: str, payload: bytes, sender: Conn):
    """把一条 PUBLISH 投递给所有订阅匹配的活跃连接（不含发布者自身）"""
    with CONN_LOCK:
        targets = [c for c in CONNECTIONS if c is not sender and c.alive]
    for c in targets:
        if any(topic_matches(f, topic) for f in c.subscriptions):
            c.send(build_publish(topic, payload))


# ===================== 浏览器俯视图 WebSocket 桥 =====================
WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
WS_CLIENTS = set()
WS_LOCK = threading.Lock()


def ws_send_frame(conn, payload: bytes, opcode: int = 0x1):
    length = len(payload)
    if length < 126:
        header = struct.pack("!BB", 0x80 | opcode, length)
    elif length < 65536:
        header = struct.pack("!BBH", 0x80 | opcode, 126, length)
    else:
        header = struct.pack("!BBQ", 0x80 | opcode, 127, length)
    conn.sendall(header + payload)


def ws_broadcast_state(state_json: str):
    """把简单 state JSON 推给所有浏览器（俯视图只看不控）"""
    payload = state_json.encode("utf-8")
    dead = []
    with WS_LOCK:
        targets = list(WS_CLIENTS)
    for c in targets:
        try:
            ws_send_frame(c, payload)
        except OSError:
            dead.append(c)
    for c in dead:
        with WS_LOCK:
            WS_CLIENTS.discard(c)


def ws_bridge_worker(host, port):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Windows 的 SO_REUSEADDR 允许多个进程同时 bind 同一端口，后启动者会静默
    # 劫持浏览器连接（实测 8899 被本地 broker 与 iotda_monitor 同时 LISTENING，
    # 浏览器连到 1.5Hz 的真平台影子数据，画面表现为「小车很卡」）。
    # 故 Windows 改用 SO_EXCLUSIVEADDRUSE：端口被占直接失败，逼调用方换端口。
    if os.name == "nt":
        srv.setsockopt(socket.SOL_SOCKET,
                       getattr(socket, "SO_EXCLUSIVEADDRUSE", -5), 1)
    else:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(8)
    print(f"[BOOT] 俯视图桥(WS)已启动：http://localhost:{port}  （IoT 模式）")
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=ws_bridge_conn, args=(conn,), daemon=True).start()


def ws_bridge_conn(conn):
    data = b""
    while b"\r\n\r\n" not in data and len(data) < 65536:
        chunk = conn.recv(4096)
        if not chunk:
            conn.close()
            return
        data += chunk
    head = data.split(b"\r\n\r\n", 1)[0].decode("latin-1")
    headers = {}
    for ln in head.split("\r\n")[1:]:
        if ":" in ln:
            k, v = ln.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    if headers.get("upgrade", "").lower() != "websocket":
        # 普通 HTTP GET：返回俯视图页面（与 car_server 的 8889 网关一致）
        html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webview.html")
        try:
            with open(html_path, "rb") as f:
                body = f.read()
        except OSError:
            body = b"<h1>webview.html not found</h1>"
        try:
            conn.sendall(b"HTTP/1.1 200 OK\r\n"
                         b"Content-Type: text/html; charset=utf-8\r\n"
                         b"Content-Length: " + str(len(body)).encode("ascii") +
                         b"\r\nConnection: close\r\n\r\n" + body)
        except OSError:
            pass
        finally:
            conn.close()
        return
    key = headers.get("sec-websocket-key", "")
    accept = base64.b64encode(
        hashlib.sha1((key + WS_MAGIC).encode("ascii")).digest()).decode()
    conn.sendall(
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n\r\n".encode("ascii"))
    with WS_LOCK:
        WS_CLIENTS.add(conn)
    print(f"[WS  ] 俯视图(IoT)接入")
    try:
        while True:
            hdr = conn.recv(2)
            if len(hdr) < 2:
                break
            opcode = hdr[0] & 0x0F
            length = hdr[1] & 0x7F
            if length == 126:
                length = struct.unpack("!H", conn.recv(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", conn.recv(8))[0]
            mask = conn.recv(4) if (hdr[1] & 0x80) else b""
            payload = conn.recv(length) if length else b""
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:  # close
                ws_send_frame(conn, b"", opcode=0x8)
                break
            # 浏览器发来的消息一律忽略（只看不控）
    except OSError:
        pass
    finally:
        with WS_LOCK:
            WS_CLIENTS.discard(conn)
        conn.close()


# ===================== 连接处理 =====================
def handle_connect(conn: Conn, body: bytes) -> bool:
    """解析 CONNECT，校验设备身份，返回是否接受"""
    pos = 0
    try:
        proto_name, pos = read_string(body, pos)
        if proto_name != "MQTT":
            conn.send(build_connack(0x01))
            return False
        level = body[pos]
        pos += 1
        flags = body[pos]
        pos += 1
        keepalive = (body[pos] << 8) | body[pos + 1]
        pos += 2
        client_id, pos = read_string(body, pos)
        # Will（若有）跳过
        if flags & 0x04:
            _, pos = read_string(body, pos)        # will topic
            _, pos = read_string(body, pos)        # will message
        username = None
        password = None
        if flags & 0x80:
            username, pos = read_string(body, pos)
        if flags & 0x40:
            password, pos = read_string(body, pos)
    except (ValueError, IndexError):
        conn.send(build_connack(0x01))
        return False

    device_id = username or client_id
    _, ts = proto.parse_client_id(client_id)
    if not proto.verify_password(device_id, password, ts):
        print(f"[AUTH] 拒绝接入 device={device_id}（鉴权失败）")
        conn.send(build_connack(0x04))  # not authorized
        return False

    conn.client_id = client_id
    conn.device_id = device_id
    conn.send(build_connack(0x00))
    print(f"[MQTT] 设备接入 device={device_id} keepalive={keepalive}s")
    return True


def handle_subscribe(conn: Conn, body: bytes, flags: int):
    pkt_id = (body[0] << 8) | body[1]
    pos = 2
    codes = []
    try:
        while pos < len(body):
            topic, pos = read_string(body, pos)
            qos = body[pos]
            pos += 1
            conn.subscriptions.add(topic)
            codes.append(0x00 if qos <= 1 else 0x80)
            print(f"[SUB ] device={conn.device_id} <- {topic}")
    except (ValueError, IndexError):
        codes.append(0x80)
    conn.send(build_suback(pkt_id, codes))


def handle_publish(conn: Conn, body: bytes, flags: int):
    pos = 0
    topic, pos = read_string(body, pos)
    qos = (flags >> 1) & 0x03
    if qos > 0:
        pkt_id = (body[pos] << 8) | body[pos + 1]
        pos += 2
        conn.send(build_puback(pkt_id))
    payload = body[pos:]
    conn.last_active = time.time()
    print(f"[PUB ] device={conn.device_id} -> {topic} ({len(payload)}B)")

    # 属性上报 → 同时推给浏览器俯视图（翻译为简单 state JSON，带 type=state 以匹配前端契约）
    if topic.endswith("properties/report"):
        props = proto.decode_property_report(payload.decode("utf-8", "ignore"))
        if props:
            props["type"] = "state"
            ws_broadcast_state(json.dumps(props, ensure_ascii=False))

    route_publish(topic, payload, conn)


def client_loop(conn: Conn):
    buffer = b""
    try:
        while True:
            data = conn.sock.recv(4096)
            if not data:
                break
            buffer += data
            # 解析所有完整包
            while True:
                if len(buffer) < 2:
                    break
                b0 = buffer[0]
                ptype = b0 >> 4
                flags = b0 & 0x0F
                try:
                    length, pos = decode_remaining_length(buffer, 1)
                except ValueError:
                    return
                if len(buffer) < pos + length:
                    break  # 不完整，等待更多数据
                pkt_body = buffer[pos:pos + length]
                buffer = buffer[pos + length:]
                conn.last_active = time.time()

                if ptype == 1:    # CONNECT
                    if not handle_connect(conn, pkt_body):
                        return
                elif ptype == 8:  # SUBSCRIBE
                    if not conn.alive:
                        return
                    handle_subscribe(conn, pkt_body, flags)
                elif ptype == 3:  # PUBLISH
                    handle_publish(conn, pkt_body, flags)
                elif ptype == 12:  # PINGREQ
                    conn.send(build_pingresp())
                elif ptype == 14:  # DISCONNECT
                    return
                # 其他类型忽略
    except (ConnectionResetError, OSError):
        pass
    finally:
        print(f"[DISC] device={conn.device_id} 断开")
        conn.alive = False
        try:
            conn.sock.close()
        except OSError:
            pass
        with CONN_LOCK:
            if conn in CONNECTIONS:
                CONNECTIONS.remove(conn)


def keepalive_watchdog():
    while True:
        time.sleep(5)
        now = time.time()
        with CONN_LOCK:
            snapshot = list(CONNECTIONS)
        for c in snapshot:
            if c.alive and now - c.last_active > 120:
                print(f"[WATCH] device={c.device_id} 心跳超时，断开")
                c.alive = False
                try:
                    c.sock.close()
                except OSError:
                    pass
                with CONN_LOCK:
                    if c in CONNECTIONS:
                        CONNECTIONS.remove(c)


def mqtt_listen(host, port):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(16)
    print(f"[BOOT] MQTT broker(模拟IoTDA)已启动：{host}:{port}")
    while True:
        sock, addr = srv.accept()
        conn = Conn(sock, addr)
        with CONN_LOCK:
            CONNECTIONS.append(conn)
        threading.Thread(target=client_loop, args=(conn,), daemon=True).start()


def main():
    parser = argparse.ArgumentParser(description="本地 MQTT broker（模拟华为云 IoTDA）")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=1883, help="MQTT 端口（默认 1883）")
    parser.add_argument("--ws-port", type=int, default=8899, help="俯视图桥端口（默认 8899）")
    args = parser.parse_args()

    threading.Thread(target=ws_bridge_worker, args=(args.host, args.ws_port), daemon=True).start()
    threading.Thread(target=keepalive_watchdog, daemon=True).start()
    try:
        mqtt_listen(args.host, args.port)
    except KeyboardInterrupt:
        print("\n[EXIT] 关闭 broker")


if __name__ == "__main__":
    main()
