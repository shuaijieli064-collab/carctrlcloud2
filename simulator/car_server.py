# -*- coding: utf-8 -*-
"""
car_server.py — 模拟小车服务端（代替真车，运行在 PC 上）

职责（课程阶段4 + 阶段6）：
  1. TCP :8888 —— 监听 0.0.0.0，接受鸿蒙 APP / test_client.py 的长连接；
  2. 按 '\\n' 分帧解析 JSON 指令：move / stop / ping，每条指令回送 ack 回执；
  3. 每 100ms 向所有客户端广播模拟状态流 state（速度/电量/朝向/位置）；
  4. 模拟小车运动学：速度逼近目标、朝向随 lx 转动、位置积分、电量缓降；
  5. 网关 :8889 —— 浏览器俯视图（只看不控）：
     - HTTP GET      → 返回 webview.html 页面；
     - WebSocket 升级 → 加入广播组，接收 100ms 一次的 state JSON 帧。

用法：
  python car_server.py                  # TCP:8888 + 网关:8889
  python car_server.py --port 9000 --ws-port 9001

仅使用 Python 标准库，无需安装第三方依赖。
"""

import argparse
import base64
import hashlib
import json
import math
import os
import socket
import socketserver
import struct
import threading
import time

# ---------------- 小车模拟参数 ----------------
MAX_SPEED = 1.0        # 最大线速度 m/s（ly=-1 时达到）
MAX_TURN = 90.0        # 最大角速度 °/s（lx=±1 时达到）
SPEED_RAMP = 3.0       # 加速度：速度逼近目标的速率（单位/秒）
BATTERY_DRAIN = 0.01   # 每 tick 电量消耗（%/tick，100ms 一个 tick）
STATE_INTERVAL = 0.1   # 状态广播周期（秒）
STEER_DEADZONE = 0.05  # 转向死区：|lx| 小于该值视为回正，抑制残余自转（与 APP 侧死区互补）


class CarState:
    """小车模拟状态（全局共享，加锁访问）"""

    def __init__(self):
        self.lock = threading.Lock()
        self.speed = 0.0          # 当前线速度 m/s（正值前进）
        self.target_speed = 0.0   # 目标线速度
        self.turn_rate = 0.0      # 当前角速度 °/s
        self.heading = 0.0        # 朝向（0=北，顺时针增大）
        self.x = 0.0              # 位置 x（米，东向）
        self.y = 0.0              # 位置 y（米，北向）
        self.battery = 100.0      # 电量 %
        self.seq_seen = 0         # 调试：最近一次收到的 seq

    def apply_move(self, lx, ly):
        """lx∈[-1,1] 左右转向；ly∈[-1,1] 前后（ly 负值=前进，与 APP 屏幕坐标一致）"""
        # 转向死区：微小 lx（手指抖动/未重建 APP 时的残余输出）不再驱动转向，避免神秘自转
        if abs(lx) < STEER_DEADZONE:
            lx = 0.0
        with self.lock:
            self.target_speed = -ly * MAX_SPEED
            self.turn_rate = lx * MAX_TURN

    def apply_stop(self):
        with self.lock:
            self.target_speed = 0.0
            self.turn_rate = 0.0

    def tick(self, dt):
        """状态推进：速度逼近目标 → 朝向转动 → 位置积分 → 电量缓降"""
        with self.lock:
            # 速度向目标逼近
            diff = self.target_speed - self.speed
            step = SPEED_RAMP * dt
            if abs(diff) <= step:
                self.speed = self.target_speed
            else:
                self.speed += step if diff > 0 else -step
            # 朝向与位置
            rad = math.radians(self.heading)
            self.heading = (self.heading + self.turn_rate * dt) % 360.0
            self.x += math.sin(rad) * self.speed * dt
            self.y += math.cos(rad) * self.speed * dt
            # 电量：运动时耗电快一些
            drain = BATTERY_DRAIN * (1.0 + abs(self.speed))
            self.battery = max(0.0, self.battery - drain)

    def snapshot(self):
        with self.lock:
            return {
                "type": "state",
                "speed": round(self.speed, 3) + 0.0,
                "battery": round(self.battery, 1) + 0.0,
                "heading": round(self.heading, 1) + 0.0,
                "x": round(self.x, 3) + 0.0,
                "y": round(self.y, 3) + 0.0,
            }


CAR = CarState()


def handle_frame(raw, conn):
    """解析单条 JSON 指令并回送回执。返回 True=正常，False=致命错误"""
    frame = raw.strip()
    if not frame:
        return True
    try:
        cmd = json.loads(frame)
    except (ValueError, UnicodeDecodeError):
        send_line(conn, {"type": "error", "msg": "invalid json"})
        print(f"[WARN] 非法报文：{frame[:80]!r}")
        return True

    if not isinstance(cmd, dict):
        send_line(conn, {"type": "error", "msg": "command must be json object"})
        return True

    ctype = cmd.get("type")
    seq = cmd.get("seq")

    if ctype == "move":
        lx = cmd.get("lx")
        ly = cmd.get("ly")
        if not isinstance(lx, (int, float)) or not isinstance(ly, (int, float)):
            send_line(conn, {"type": "error", "msg": "move requires numeric lx and ly", "seq": seq})
            print(f"[WARN] move 缺少数值型 lx/ly，已拒绝：{cmd}")
            return True
        CAR.apply_move(lx, ly)
        send_line(conn, {"type": "ack", "seq": seq, "ok": True})
        print(f"[CMD ] move lx={lx:+.2f} ly={ly:+.2f} seq={seq}")

    elif ctype == "stop":
        CAR.apply_stop()
        send_line(conn, {"type": "ack", "seq": seq, "ok": True})
        print(f"[CMD ] stop seq={seq}")

    elif ctype == "ping":
        send_line(conn, {"type": "pong", "seq": seq})
        print(f"[CMD ] ping seq={seq}")

    else:
        send_line(conn, {"type": "error", "msg": f"unknown type: {ctype}", "seq": seq})
        print(f"[WARN] 未知指令类型：{ctype}")
    return True


def send_line(conn, obj):
    """发送一行 JSON（含换行分帧符）"""
    try:
        conn.sendall((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
    except OSError:
        pass


def client_worker(conn, addr):
    """单个客户端连接的处理线程：收包分帧 + 分发"""
    print(f"[CONN] 客户端接入：{addr}")
    buffer = b""
    try:
        while True:
            data = conn.recv(4096)
            if not data:
                break
            buffer += data
            # 按换行分帧；处理粘包/拆包
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                try:
                    text = line.decode("utf-8")
                except UnicodeDecodeError:
                    send_line(conn, {"type": "error", "msg": "invalid utf-8"})
                    continue
                if not handle_frame(text, conn):
                    return
    except (ConnectionResetError, OSError):
        pass
    finally:
        print(f"[DISC] 客户端断开：{addr}")
        conn.close()


def state_broadcaster(clients):
    """周期广播状态流给所有在线客户端（100ms）：TCP 通道 + WebSocket 通道"""
    while True:
        CAR.tick(STATE_INTERVAL)
        snap_text = json.dumps(CAR.snapshot(), ensure_ascii=False)
        payload = (snap_text + "\n").encode("utf-8")
        dead = []
        for c in list(clients):
            try:
                c.sendall(payload)
            except OSError:
                dead.append(c)
        for c in dead:
            clients.discard(c)
            try:
                c.close()
            except OSError:
                pass
        ws_broadcast(snap_text)
        time.sleep(STATE_INTERVAL)


# ==================== 阶段6：浏览器俯视图网关（:8889） ====================
# 纯标准库实现 WebSocket 服务端：HTTP 升级握手 + 帧编解码（RFC 6455 最小子集）
WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
WS_CLIENTS = set()
WS_LOCK = threading.Lock()


def recv_exact(conn, n):
    """精确读取 n 字节，连接关闭返回 None"""
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def ws_send_frame(conn, payload: bytes, opcode: int = 0x1):
    """发送一个服务端→客户端的 WS 文本帧（服务端帧不掩码）"""
    length = len(payload)
    if length < 126:
        header = struct.pack("!BB", 0x80 | opcode, length)
    elif length < 65536:
        header = struct.pack("!BBH", 0x80 | opcode, 126, length)
    else:
        header = struct.pack("!BBQ", 0x80 | opcode, 127, length)
    conn.sendall(header + payload)


def ws_read_frame(conn):
    """读取一个客户端帧（浏览器帧必须掩码）。返回 (opcode, payload) 或 None"""
    hdr = recv_exact(conn, 2)
    if hdr is None:
        return None
    opcode = hdr[0] & 0x0F
    masked = (hdr[1] & 0x80) != 0
    length = hdr[1] & 0x7F
    if length == 126:
        ext = recv_exact(conn, 2)
        if ext is None:
            return None
        length = struct.unpack("!H", ext)[0]
    elif length == 127:
        ext = recv_exact(conn, 8)
        if ext is None:
            return None
        length = struct.unpack("!Q", ext)[0]
    mask = recv_exact(conn, 4) if masked else None
    if masked and mask is None:
        return None
    payload = recv_exact(conn, length) if length else b""
    if payload is None:
        return None
    if masked and payload:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return opcode, payload


def ws_broadcast(text: str):
    """把一条 state JSON 广播给所有浏览器（WS 通道，只看不控）"""
    payload = text.encode("utf-8")
    dead = []
    with WS_LOCK:
        targets = list(WS_CLIENTS)
    for c in targets:
        try:
            ws_send_frame(c, payload)
        except OSError:
            dead.append(c)
    if dead:
        with WS_LOCK:
            for c in dead:
                WS_CLIENTS.discard(c)
                try:
                    c.close()
                except OSError:
                    pass


def ws_worker(conn, addr):
    """8889 网关的单连接处理：HTTP GET 返回页面；WebSocket 升级加入广播组"""
    data = b""
    while b"\r\n\r\n" not in data and len(data) < 65536:
        chunk = conn.recv(4096)
        if not chunk:
            conn.close()
            return
        data += chunk
    head = data.split(b"\r\n\r\n", 1)[0].decode("latin-1")
    lines = head.split("\r\n")
    headers = {}
    for ln in lines[1:]:
        if ":" in ln:
            k, v = ln.split(":", 1)
            headers[k.strip().lower()] = v.strip()

    if headers.get("upgrade", "").lower() == "websocket":
        key = headers.get("sec-websocket-key", "")
        accept = base64.b64encode(
            hashlib.sha1((key + WS_MAGIC).encode("ascii")).digest()).decode()
        conn.sendall((
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
        ).encode("ascii"))
        with WS_LOCK:
            WS_CLIENTS.add(conn)
        print(f"[WS  ] 浏览器俯视图接入：{addr}")
        try:
            while True:
                frame = ws_read_frame(conn)
                if frame is None:
                    break
                opcode, payload = frame
                if opcode == 0x8:  # close
                    ws_send_frame(conn, b"", opcode=0x8)
                    break
                if opcode == 0x9:  # ping → pong
                    ws_send_frame(conn, payload, opcode=0xA)
                # 0x1 文本消息一律忽略：俯视图"只看不控"
        except OSError:
            pass
        finally:
            with WS_LOCK:
                WS_CLIENTS.discard(conn)
            conn.close()
            print(f"[WS  ] 浏览器断开：{addr}")
    else:
        # 普通 HTTP GET：返回俯视图页面
        html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "webview.html")
        try:
            with open(html_path, "rb") as f:
                body = f.read()
        except OSError:
            body = b"<h1>webview.html not found beside car_server.py</h1>"
        try:
            conn.sendall(b"HTTP/1.1 200 OK\r\n"
                         b"Content-Type: text/html; charset=utf-8\r\n"
                         b"Content-Length: " + str(len(body)).encode("ascii") +
                         b"\r\nConnection: close\r\n\r\n" + body)
        except OSError:
            pass
        finally:
            conn.close()


def ws_gateway(host, port):
    """俯视图网关：HTTP + WebSocket 同端口混合服务"""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(8)
    print(f"[BOOT] 俯视图网关已启动：浏览器打开 http://localhost:{port}")
    while True:
        conn, addr = srv.accept()
        threading.Thread(target=ws_worker, args=(conn, addr), daemon=True).start()


class CarRequestHandler(socketserver.BaseRequestHandler):
    def setup(self):
        CLIENTS.add(self.request)
        threading.Thread(target=client_worker, args=(self.request, self.client_address),
                         daemon=True).start()
        # setup 返回后 BaseServer 会继续 accept（handle 置空即可）

    def handle(self):
        while True:
            time.sleep(3600)  # 实际工作在 client_worker 线程


CLIENTS = set()


def main():
    parser = argparse.ArgumentParser(description="模拟小车服务端（TCP 控制 + 俯视图网关）")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址（默认 0.0.0.0）")
    parser.add_argument("--port", type=int, default=8888, help="TCP 控制端口（默认 8888）")
    parser.add_argument("--ws-port", type=int, default=8889, help="俯视图网关端口（默认 8889）")
    args = parser.parse_args()

    # 状态推进 + 广播线程
    threading.Thread(target=state_broadcaster, args=(CLIENTS,), daemon=True).start()
    # 俯视图网关（HTTP + WebSocket）
    threading.Thread(target=ws_gateway, args=(args.host, args.ws_port), daemon=True).start()

    # 允许快速重启复用端口
    socketserver.TCPServer.allow_reuse_address = True
    server = socketserver.ThreadingTCPServer((args.host, args.port), CarRequestHandler)
    print(f"[BOOT] 模拟小车服务端已启动：TCP {args.host}:{args.port}")
    print(f"[BOOT] 俯视图：http://localhost:{args.ws_port}  （只看不控）")
    print("[BOOT] 等待 APP / test_client.py 连接……（Ctrl+C 退出）")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[EXIT] 收到退出信号，关闭服务端")
        server.shutdown()


if __name__ == "__main__":
    main()
