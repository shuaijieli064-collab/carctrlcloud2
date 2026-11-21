# -*- coding: utf-8 -*-
"""
mqtt_client.py — 极简 MQTT 3.1.1 客户端（标准库实现，零依赖）

供以下角色复用：
  - car_mqtt_device.py  （车端以 IoT 设备身份接入 broker）
  - app_mqtt_client.py  （APP 替身，站在 APP 位置发命令 / 收属性）

支持：CONNECT/CONNACK、SUBSCRIBE/SUBACK、PUBLISH(PUBACK)、PINGREQ/PINGRESP、
DISCONNECT，以及后台读包线程 + 心跳。TLS 暂未实现（本地明文 1883）；
真平台对接时客户端需改用 TLSSocket（见 README）。
"""

import socket
import ssl
import threading
import time

from mqtt_broker import (
    encode_remaining_length,
    decode_remaining_length,
    read_string,
    write_string,
)


class MiniMqttClient:
    def __init__(self, host, port, client_id, username=None, password=None, keepalive=30,
                 tls=False, ca_file=None):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.username = username
        self.password = password
        self.keepalive = keepalive
        self.tls = tls          # True 时启用 TLS（MQTTS，如华为云 8883）
        self.ca_file = ca_file  # CA 证书 PEM 路径；缺省走系统信任库
        self.sock = None
        self._pkt_id = 1
        self._lock = threading.Lock()
        self._reader_thread = None
        self._alive = False
        self.on_message = None   # callback(topic:str, payload:str)

    # ---------- 底层发包 ----------
    def _send(self, ptype: int, flags: int, body: bytes):
        packet = bytes([(ptype << 4) | flags]) + encode_remaining_length(len(body)) + body
        with self._lock:
            if self.sock:
                self.sock.sendall(packet)

    def _next_id(self) -> int:
        self._pkt_id = (self._pkt_id % 65535) + 1
        return self._pkt_id

    # ---------- 连接 ----------
    def connect(self) -> bool:
        try:
            raw = socket.create_connection((self.host, self.port), timeout=5)
            if self.tls:
                ctx = ssl.create_default_context(cafile=self.ca_file)
                self.sock = ctx.wrap_socket(raw, server_hostname=self.host)
            else:
                self.sock = raw
        except ConnectionRefusedError:
            print(f"[MQTT] 目标计算机积极拒绝连接 {self.host}:{self.port} (WinError 10061)")
            print("       含义：该地址的这个端口上没有服务在监听，并非账号/密钥错误。")
            print("       local 模式：请先启动 mqtt_broker.py（1883）。")
            print("       real  模式：确认 iotda_config.json 为 platform=real / 域名 / 8883 / tls=true。")
            return False
        except (OSError, ssl.SSLError) as exc:
            print(f"[MQTT] 连接 {self.host}:{self.port} 失败：{type(exc).__name__}: {exc}")
            return False
        # 可变头 + 负载
        cflags = 0x02  # clean session
        if self.username is not None:
            cflags |= 0x80
        if self.password is not None:
            cflags |= 0x40
        body = write_string("MQTT") + bytes([4, cflags]) + bytes([self.keepalive >> 8, self.keepalive & 0xFF])
        body += write_string(self.client_id)
        if self.username is not None:
            body += write_string(self.username)
        if self.password is not None:
            body += write_string(self.password)
        self._send(1, 0, body)
        # 读 CONNACK
        self.sock.settimeout(5)
        data = self.sock.recv(4)
        if len(data) < 4 or data[0] != 0x20:
            return False
        code = data[3]
        if code != 0:
            print(f"[MQTT] CONNACK 拒绝，code={code}")
            return False
        self._alive = True
        self.sock.settimeout(0.5)
        self._reader_thread = threading.Thread(target=self._reader, daemon=True)
        self._reader_thread.start()
        return True

    # ---------- 订阅 / 发布 ----------
    def subscribe(self, topic: str, qos: int = 0):
        pid = self._next_id()
        body = bytes([pid >> 8, pid & 0xFF]) + write_string(topic) + bytes([qos])
        self._send(8, 0x02, body)   # SUBSCRIBE 固定头 flags=0x02

    def publish(self, topic: str, payload: str, qos: int = 0):
        body = write_string(topic)
        if qos > 0:
            pid = self._next_id()
            body += bytes([pid >> 8, pid & 0xFF])
        body += payload.encode("utf-8")
        self._send(3, (qos << 1), body)

    def ping(self):
        self._send(12, 0, b"")

    def is_alive(self) -> bool:
        """读包线程是否存活（False 表示会话已死，需重连重订阅）。"""
        return self._alive

    def disconnect(self):
        self._send(14, 0, b"")
        self._alive = False
        try:
            self.sock.close()
        except OSError:
            pass

    # ---------- 后台读包 ----------
    def _reader(self):
        buf = b""
        last_ping = time.time()
        try:
            while self._alive:
                try:
                    chunk = self.sock.recv(4096)
                except socket.timeout:
                    # 心跳：每 keepalive/2 发一次 PINGREQ（MQTT 规范要求，平台据此维持会话）
                    if time.time() - last_ping >= max(5, self.keepalive // 2):
                        self.ping()
                        last_ping = time.time()
                    continue
                except OSError:
                    break
                if not chunk:
                    break
                last_ping = time.time()  # 收到任何数据也视作链路活动
                buf += chunk
                while True:
                    if len(buf) < 2:
                        break
                    ptype = buf[0] >> 4
                    flags = buf[0] & 0x0F
                    try:
                        length, pos = decode_remaining_length(buf, 1)
                    except ValueError:
                        return
                    if len(buf) < pos + length:
                        break
                    body = buf[pos:pos + length]
                    buf = buf[pos + length:]
                    try:
                        self._dispatch(ptype, flags, body)
                    except Exception as e:
                        # 单包分发失败（如回执发送瞬时错误）不杀线程：
                        # 读包线程一旦退出，命令将堆积在 TCP 缓冲区无人处理，
                        # 平台侧表现为命令回执超时（IOTDA.014111）。
                        print(f"[MQTT] 报文分发异常（{type(e).__name__}: {e}），跳过该包")
        finally:
            self._alive = False

    def _dispatch(self, ptype, flags, body):
        if ptype == 3:    # PUBLISH
            pos = 0
            topic, pos = read_string(body, pos)
            qos = (flags >> 1) & 0x03
            if qos > 0:
                pid = (body[pos] << 8) | body[pos + 1]
                pos += 2
                # 回 PUBACK
                self._send(4, 0, bytes([pid >> 8, pid & 0xFF]))
            payload = body[pos:].decode("utf-8", "ignore")
            if self.on_message:
                self.on_message(topic, payload)
        elif ptype == 9:  # SUBACK：打印订阅结果（0x00/0x01=成功，0x80=失败）
            codes = list(body[2:])
            print(f"[MQTT] SUBACK codes={codes}")
        # PUBACK(4)/PINGRESP(13) 无需特别处理
